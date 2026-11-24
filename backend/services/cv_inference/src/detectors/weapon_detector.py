"""Weapon detection with a model trained in ``ml_training`` (YOLO11 or RT-DETR via ultralytics) and
exported to ONNX with a manifest. No ultralytics code runs here: pre-processing and decoding are
NumPy, mirrored from ``ml_training/export/onnx_detect.py``.

* **Input:** letterbox to the manifest's size (keep aspect ratio, pad both sides with grey 114),
  BGR→RGB, ``× 1/255``, ``1×3×H×W`` float32.
* **Output** ``ultralytics-detect-v1``: ``[1, 4 + nc, A]`` with ``xywh`` in input pixels and class scores
  already sigmoid-activated; class-aware NMS. ``ultralytics-rtdetr-v1``: ``[1, Q, 4 + nc]`` with
  normalised ``xywh``; no NMS.
* Detections carry the model's class name (``handgun``, ``knife`` …). ``EventRules.class_map`` maps
  those labels to the ``WEAPON`` alert type (HIGH priority in Core API). Per-class thresholds
  calibrated on the validation split travel in the manifest (``score_<label>``) and are used unless
  the settings override them.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.detectors.base_detector import DetectorError
from src.manifest import ModelManifest, load_model
from src.model_loader import Device, ModelAdapter
from src.schemas import BoundingBox, Detection, Frame, ModelInfo


def letterbox(image: np.ndarray, height: int, width: int, pad_value: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Returns the padded BGR image, the scale, and the (left, top) padding in pixels."""
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8 or image.shape[0] == 0 or image.shape[1] == 0:
        raise DetectorError("The frame must be a non-empty uint8 image with 3 channels.")
    h, w = image.shape[:2]
    scale = min(height / h, width / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR) if (new_w, new_h) != (w, h) else image
    pad_x, pad_y = (width - new_w) / 2, (height - new_h) / 2
    top, bottom = int(round(pad_y - 0.1)), int(round(pad_y + 0.1))
    left, right = int(round(pad_x - 0.1)), int(round(pad_x + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad_value,) * 3)
    return padded, scale, (left, top)


def nms_per_class(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, iou_threshold: float, max_detections: int) -> list[int]:
    if boxes.shape[0] == 0:
        return []
    offset = classes[:, None].astype(np.float32) * (boxes.max() + 1.0)
    shifted = boxes + offset
    x1, y1, x2, y2 = shifted.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size and len(keep) < max_detections:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / np.clip(areas[i] + areas[order[1:]] - inter, 1e-12, None)
        order = order[1:][iou <= iou_threshold]
    return keep


class UltralyticsDecoder:
    def __init__(self, manifest: ModelManifest, *, thresholds: dict[str, float], default_threshold: float, nms_iou: float, max_detections: int) -> None:
        if not 0.0 < default_threshold < 1.0 or not 0.0 < nms_iou <= 1.0 or max_detections < 1:
            raise ValueError("default_threshold in (0, 1), nms_iou in (0, 1], max_detections >= 1")
        if manifest.output.format not in ("ultralytics-detect-v1", "ultralytics-rtdetr-v1"):
            raise ValueError(f"not a detector output format: {manifest.output.format}")
        self._manifest = manifest
        self._labels = list(manifest.class_labels)
        self._per_class = np.array([float(thresholds.get(label, default_threshold)) for label in self._labels], dtype=np.float64)
        self._min = float(self._per_class.min())
        self._nms = nms_iou
        self._max = max_detections

    @property
    def class_thresholds(self) -> dict[str, float]:
        return {label: float(t) for label, t in zip(self._labels, self._per_class, strict=True)}

    def decode(self, raw: np.ndarray, *, scale: float, pad: tuple[int, int], frame_width: int, frame_height: int) -> list[Detection]:
        fmt = self._manifest.output.format
        raw = np.asarray(raw)
        if raw.ndim != 3 or raw.shape[0] != 1:
            raise DetectorError(f"Unexpected model output shape {tuple(raw.shape)}.")
        if not np.isfinite(raw).all():
            raise DetectorError("The model output contains non-finite values.")
        if fmt == "ultralytics-detect-v1":
            pred = raw[0].T
            xywh, class_scores = pred[:, :4], pred[:, 4:]
        else:
            pred = raw[0]
            xywh, class_scores = pred[:, :4].copy(), pred[:, 4:]
            xywh[:, [0, 2]] *= self._manifest.input.width
            xywh[:, [1, 3]] *= self._manifest.input.height
        if class_scores.shape[1] != len(self._labels):
            raise DetectorError(f"The model has {class_scores.shape[1]} classes; the manifest lists {len(self._labels)}.")
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(class_ids)), class_ids]
        keep = scores >= self._per_class[class_ids]
        if not keep.any():
            return []
        xywh, scores, class_ids = xywh[keep], scores[keep], class_ids[keep]
        boxes = np.stack([xywh[:, 0] - xywh[:, 2] / 2, xywh[:, 1] - xywh[:, 3] / 2, xywh[:, 0] + xywh[:, 2] / 2, xywh[:, 1] + xywh[:, 3] / 2], axis=1)
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad[0]) / scale
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad[1]) / scale
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, frame_width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, frame_height)
        valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (boxes[:, 3] - boxes[:, 1] >= 1)
        boxes, scores, class_ids = boxes[valid], scores[valid], class_ids[valid]
        if boxes.shape[0] == 0:
            return []
        kept = nms_per_class(boxes, scores, class_ids, self._nms, self._max) if fmt == "ultralytics-detect-v1" else list(np.argsort(-scores)[: self._max])
        return [Detection(label=self._labels[int(class_ids[i])], confidence=float(min(1.0, scores[i])), box=BoundingBox(*map(float, boxes[i]))) for i in kept]


class WeaponOnnxDetector:
    """``Detector`` for weapons. ``adapter`` can be injected (tests); otherwise the manifest's ONNX
    file is loaded after its checksum and input shape are checked."""

    def __init__(
        self,
        manifest_path: Path | str,
        *,
        default_threshold: float = 0.4,
        thresholds: dict[str, float] | None = None,
        nms_iou: float | None = None,
        max_detections: int = 100,
        device: Device = "cpu",
        verify_checksum: bool = True,
        adapter: ModelAdapter | None = None,
    ) -> None:
        if adapter is None:
            self.manifest, loaded = load_model(manifest_path, expected_task="weapon_detection", device=device, verify_checksum=verify_checksum)
            self.adapter: ModelAdapter = loaded
        else:
            self.manifest = ModelManifest.load(manifest_path)
            self.adapter = adapter
        self.info: ModelInfo = self.manifest.model_info()
        calibrated = {label: self.manifest.thresholds[f"score_{label}"] for label in self.manifest.class_labels if f"score_{label}" in self.manifest.thresholds}
        if "score" in self.manifest.thresholds and not calibrated:
            default_threshold = float(self.manifest.thresholds["score"])
        self._decoder = UltralyticsDecoder(
            self.manifest,
            thresholds={**calibrated, **(thresholds or {})},
            default_threshold=default_threshold,
            nms_iou=float(nms_iou if nms_iou is not None else self.manifest.thresholds.get("nms_iou", 0.6)),
            max_detections=max_detections,
        )
        spec = self.manifest.input
        self._mean = np.asarray(spec.mean, np.float32)
        self._std = np.asarray(spec.std, np.float32)

    @property
    def class_thresholds(self) -> dict[str, float]:
        return self._decoder.class_thresholds

    def preprocess(self, image: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        spec = self.manifest.input
        padded, scale, pad = letterbox(image, spec.height, spec.width, spec.letterbox_pad_value if spec.letterbox_pad_value is not None else 114)
        rgb = padded[:, :, ::-1] if spec.color_order == "RGB" else padded
        array = (rgb.astype(np.float32) * spec.scale - self._mean) / self._std
        return np.ascontiguousarray(array.transpose(2, 0, 1)[None]), scale, pad

    def detect(self, frame: Frame) -> list[Detection]:
        tensor, scale, pad = self.preprocess(frame.image)
        try:
            outputs = self.adapter.run({self.manifest.input.name: tensor})
        except Exception as error:
            raise DetectorError(f"Weapon detection failed: {type(error).__name__}") from error
        if not outputs:
            raise DetectorError("The model returned no output.")
        return self._decoder.decode(np.asarray(outputs[0]), scale=scale, pad=pad, frame_width=frame.width, frame_height=frame.height)

    def close(self) -> None:
        self.adapter.close()
