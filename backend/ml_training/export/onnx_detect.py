"""Runs an exported detector ONNX file with NumPy pre/post-processing only (no ultralytics, no
torch). Used to check that the ONNX model plus this decoder reproduce the PyTorch model's results
(``evaluate_weapons.py --weights model.onnx``), and mirrored by ``cv_inference``'s weapon detector.

Supports the two ultralytics export layouts:
* ``ultralytics-detect-v1`` (YOLO11/YOLOv8): ``[B, 4 + nc, A]``, boxes ``xywh`` in letterboxed input
  pixels, class scores already sigmoid-activated; needs NMS.
* ``ultralytics-rtdetr-v1`` (RT-DETR): ``[B, Q, 4 + nc]``, boxes ``xywh`` normalised to 0–1; no NMS.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from export.manifest import ModelManifest


@dataclass(frozen=True)
class DetectedBox:
    label: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float


def letterbox(image_bgr: np.ndarray, height: int, width: int, pad_value: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    """ultralytics ``LetterBox(auto=False, center=True)``: keep the aspect ratio, pad both sides.
    Returns the padded image and ``(scale, (pad_x, pad_y))``."""
    h, w = image_bgr.shape[:2]
    scale = min(height / h, width / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR) if (new_w, new_h) != (w, h) else image_bgr
    pad_x, pad_y = (width - new_w) / 2, (height - new_h) / 2
    top, bottom = int(round(pad_y - 0.1)), int(round(pad_y + 0.1))
    left, right = int(round(pad_x - 0.1)), int(round(pad_x + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad_value,) * 3)
    return padded, scale, (left, top)


def nms_per_class(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, iou_threshold: float, max_detections: int) -> list[int]:
    """Class-aware NMS by offsetting boxes of different classes so they never overlap."""
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


def decode_ultralytics(
    raw: np.ndarray,
    *,
    manifest: ModelManifest,
    scale: float,
    pad: tuple[float, float],
    frame_size: tuple[int, int],
    input_size: tuple[int, int],
    score_threshold: float,
    nms_iou: float,
    max_detections: int = 300,
) -> list[DetectedBox]:
    labels = manifest.class_labels
    fmt = manifest.output.format
    if fmt == "ultralytics-detect-v1":
        pred = raw.T if raw.ndim == 2 else raw[0].T  # [A, 4+nc]
        xywh, class_scores = pred[:, :4], pred[:, 4:]
    elif fmt == "ultralytics-rtdetr-v1":
        pred = raw if raw.ndim == 2 else raw[0]  # [Q, 4+nc]
        xywh, class_scores = pred[:, :4].copy(), pred[:, 4:]
        xywh[:, [0, 2]] *= input_size[1]
        xywh[:, [1, 3]] *= input_size[0]
    else:
        raise ValueError(f"not a detector output format: {fmt}")
    if class_scores.shape[1] != len(labels):
        raise ValueError(f"model has {class_scores.shape[1]} classes, manifest lists {len(labels)}")
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(class_ids)), class_ids]
    keep = scores >= score_threshold
    if not keep.any():
        return []
    xywh, scores, class_ids = xywh[keep], scores[keep], class_ids[keep]
    boxes = np.stack([xywh[:, 0] - xywh[:, 2] / 2, xywh[:, 1] - xywh[:, 3] / 2, xywh[:, 0] + xywh[:, 2] / 2, xywh[:, 1] + xywh[:, 3] / 2], axis=1)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad[0]) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad[1]) / scale
    frame_h, frame_w = frame_size
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, frame_w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, frame_h)
    valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (boxes[:, 3] - boxes[:, 1] >= 1)
    boxes, scores, class_ids = boxes[valid], scores[valid], class_ids[valid]
    if fmt == "ultralytics-detect-v1":
        kept = nms_per_class(boxes, scores, class_ids, nms_iou, max_detections)
    else:
        kept = list(np.argsort(-scores)[:max_detections])
    return [DetectedBox(labels[int(class_ids[i])], float(min(1.0, scores[i])), *map(float, boxes[i])) for i in kept]


class OnnxDetector:
    def __init__(self, manifest_path: Path, *, score_threshold: float = 0.25, nms_iou: float = 0.6, providers: list[str] | None = None) -> None:
        import onnxruntime as ort

        self.manifest = ModelManifest.load(manifest_path)
        model_path = manifest_path.parent / self.manifest.file
        self.session = ort.InferenceSession(str(model_path), providers=providers or ["CPUExecutionProvider"])
        shape = self.manifest.input.shape
        self.height, self.width = int(shape[2]), int(shape[3])
        self.score_threshold, self.nms_iou = score_threshold, nms_iou

    def preprocess(self, image_bgr: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        spec = self.manifest.input
        padded, scale, pad = letterbox(image_bgr, self.height, self.width, spec.letterbox_pad_value or 114)
        image = padded[:, :, ::-1] if spec.color_order == "RGB" else padded
        array = image.astype(np.float32) * spec.scale
        array = (array - np.asarray(spec.mean, np.float32)) / np.asarray(spec.std, np.float32)
        return np.ascontiguousarray(array.transpose(2, 0, 1)[None]), scale, pad

    def detect(self, image_bgr: np.ndarray) -> list[DetectedBox]:
        tensor, scale, pad = self.preprocess(image_bgr)
        raw = self.session.run(None, {self.manifest.input.name: tensor})[0]
        return decode_ultralytics(
            raw,
            manifest=self.manifest,
            scale=scale,
            pad=pad,
            frame_size=image_bgr.shape[:2],
            input_size=(self.height, self.width),
            score_threshold=self.score_threshold,
            nms_iou=self.nms_iou,
        )
