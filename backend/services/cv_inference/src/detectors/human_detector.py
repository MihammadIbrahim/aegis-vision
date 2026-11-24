"""Human (person) detection with a pretrained YOLOX model in ONNX format (decision D-015).

Model: YOLOX-Tiny, official ONNX export from Megvii-BaseDetection/YOLOX release 0.1.1rc0 (code
Apache-2.0; weights COCO-trained, no separate weight licence stated: see ``weights/README.md``). The
model and its expected layout are described by a metadata file (``weights/yolox_tiny.model.json``);
the detector refuses to start if the file, its checksum or the model's real input/output shapes do
not match that description. It never falls back to another detector.

Pre- and post-processing follow the publisher's own code (``yolox/data/data_augment.py::preproc``,
``yolox/utils/demo_utils.py``, ``demo/ONNXRuntime/onnx_inference.py``):

* **Input:** letterbox into the input size, keep the aspect ratio, resized image in the top-left
  corner, padding value 114; BGR channel order kept; values 0–255 as float32, no mean/std; layout
  1×3×H×W.
* **Output** ``[1, N, 5 + num_classes]``: per anchor point ``(x, y, w, h, objectness, class scores…)``,
  with objectness and class scores already sigmoid-activated. Boxes are decoded on the stride-8/16/32
  grids: centre = (raw + grid) × stride, size = exp(raw) × stride. Score = objectness × class score.
* Only the COCO "person" class is kept in this phase; candidates below the score threshold are
  dropped, overlapping boxes are removed by NMS, and boxes are mapped back to the frame and clipped.

The output is a person detection only: persons never create alerts (decision D-013, item 8).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.detectors.base_detector import DetectorError
from src.model_loader import Device, ModelAdapter, ModelLoadError, OnnxRuntimeAdapter
from src.schemas import BoundingBox, Detection, Frame, ModelInfo

PERSON = "person"


class YoloxModelSpec(BaseModel):
    """The metadata file next to the ONNX model. Every field is checked before the model is used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    format: Literal["yolox-grid-v1"]
    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_name: str = Field(min_length=1)
    input_height: int = Field(gt=0, multiple_of=32)
    input_width: int = Field(gt=0, multiple_of=32)
    strides: tuple[int, ...] = (8, 16, 32)
    num_classes: int = Field(gt=0)
    person_class_index: int = Field(ge=0)
    pad_value: int = Field(default=114, ge=0, le=255)
    source: str
    code_license: str
    weights_license: str
    reference_metrics: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> YoloxModelSpec:
        if self.person_class_index >= self.num_classes:
            raise ValueError("person_class_index must be below num_classes")
        if not self.strides or any(s <= 0 or self.input_height % s or self.input_width % s for s in self.strides):
            raise ValueError("every stride must divide the input size")
        return self

    @property
    def anchor_count(self) -> int:
        return sum((self.input_height // s) * (self.input_width // s) for s in self.strides)

    @classmethod
    def load(cls, path: Path | str) -> YoloxModelSpec:
        try:
            return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        except OSError as error:
            raise ModelLoadError(f"Model metadata not found or unreadable: {Path(path).name}") from error
        except (ValueError, ValidationError) as error:
            raise ModelLoadError(f"Invalid model metadata {Path(path).name}: {error}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def letterbox(image: np.ndarray, height: int, width: int, pad_value: int) -> tuple[np.ndarray, float]:
    """The publisher's ``preproc``: returns the 1×3×H×W float32 tensor and the scale ratio."""
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8 or image.shape[0] == 0 or image.shape[1] == 0:
        raise DetectorError("The frame must be a non-empty uint8 image with 3 channels.")
    ratio = min(height / image.shape[0], width / image.shape[1])
    new_h, new_w = max(1, int(image.shape[0] * ratio)), max(1, int(image.shape[1] * ratio))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    padded = np.full((height, width, 3), pad_value, dtype=np.uint8)
    padded[:new_h, :new_w] = resized
    tensor = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
    return tensor, ratio


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Single-class non-maximum suppression (the publisher's ``nms``). Returns kept indices,
    highest score first."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        overlap = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(overlap <= iou_threshold)[0] + 1]
    return keep


class YoloxDecoder:
    """Turns a raw YOLOX output into person detections in frame coordinates."""

    def __init__(self, spec: YoloxModelSpec, *, score_threshold: float, nms_iou: float, max_detections: int) -> None:
        if not 0.0 < score_threshold < 1.0 or not 0.0 < nms_iou <= 1.0 or max_detections < 1:
            raise ValueError("score_threshold in (0, 1), nms_iou in (0, 1], max_detections >= 1")
        self._spec = spec
        self._score = score_threshold
        self._nms = nms_iou
        self._max = max_detections
        grids, strides = [], []
        for stride in spec.strides:
            h, w = spec.input_height // stride, spec.input_width // stride
            xv, yv = np.meshgrid(np.arange(w), np.arange(h))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((h * w, 1), stride))
        self._grid = np.concatenate(grids).astype(np.float32)
        self._stride = np.concatenate(strides).astype(np.float32)

    def decode(self, raw: np.ndarray, ratio: float, frame_width: int, frame_height: int) -> list[Detection]:
        spec = self._spec
        expected = (1, spec.anchor_count, 5 + spec.num_classes)
        if raw.shape != expected:
            raise DetectorError(f"Unexpected model output shape {tuple(raw.shape)}; expected {expected}.")
        pred = raw[0]
        if not np.isfinite(pred).all():
            raise DetectorError("The model output contains non-finite values.")
        scores = pred[:, 4] * pred[:, 5 + spec.person_class_index]
        candidates = np.where(scores > self._score)[0]
        if candidates.size == 0:
            return []
        p = pred[candidates]
        centres = (p[:, :2] + self._grid[candidates]) * self._stride[candidates]
        sizes = np.exp(np.clip(p[:, 2:4], -20, 20)) * self._stride[candidates]
        boxes = np.concatenate([centres - sizes / 2, centres + sizes / 2], axis=1) / ratio
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, frame_width)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, frame_height)
        valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (boxes[:, 3] - boxes[:, 1] >= 1)
        boxes, kept_scores = boxes[valid], scores[candidates][valid]
        if boxes.shape[0] == 0:
            return []
        keep = nms(boxes, kept_scores, self._nms)[: self._max]
        return [
            Detection(label=PERSON, confidence=float(min(1.0, kept_scores[i])), box=BoundingBox(*map(float, boxes[i])))
            for i in keep
        ]


class YoloxHumanDetector:
    """``Detector`` for persons. ``adapter`` can be injected (tests); otherwise the ONNX file named in
    the metadata is loaded through ``OnnxRuntimeAdapter`` after its checksum and shapes are checked."""

    def __init__(
        self,
        spec_path: Path | str,
        *,
        score_threshold: float = 0.35,
        nms_iou: float = 0.45,
        max_detections: int = 100,
        device: Device = "cpu",
        verify_checksum: bool = True,
        adapter: ModelAdapter | None = None,
    ) -> None:
        spec_file = Path(spec_path)
        self.spec = YoloxModelSpec.load(spec_file)
        self.info = ModelInfo(
            name=self.spec.name,
            version=self.spec.version,
            labels=(PERSON,),
            synthetic=False,
            license=f"code: {self.spec.code_license}; weights: {self.spec.weights_license}",
            source=self.spec.source,
        )
        if adapter is None:
            model_file = spec_file.parent / self.spec.file
            if not model_file.is_file():
                raise ModelLoadError(
                    f"Model file {self.spec.file} not found next to {spec_file.name}. See weights/README.md."
                )
            if verify_checksum and _sha256(model_file) != self.spec.sha256:
                raise ModelLoadError(f"Checksum mismatch for {self.spec.file}: not the expected model.")
            adapter = OnnxRuntimeAdapter(model_file, self.info, device=device)
            self._check_signature(adapter)
        self.adapter = adapter
        self._decoder = YoloxDecoder(
            self.spec, score_threshold=score_threshold, nms_iou=nms_iou, max_detections=max_detections
        )

    def _check_signature(self, adapter: OnnxRuntimeAdapter) -> None:
        inputs = adapter.input_details()
        outputs = adapter.output_details()
        expected_in = [1, 3, self.spec.input_height, self.spec.input_width]
        expected_out = [1, self.spec.anchor_count, 5 + self.spec.num_classes]
        if [name for name, _ in inputs] != [self.spec.input_name] or list(inputs[0][1]) != expected_in:
            raise ModelLoadError(
                f"Model input {inputs} does not match the metadata ({self.spec.input_name} {expected_in})."
            )
        if len(outputs) != 1 or list(outputs[0][1]) != expected_out:
            raise ModelLoadError(f"Model output {outputs} does not match the metadata ({expected_out}).")

    def detect(self, frame: Frame) -> list[Detection]:
        tensor, ratio = letterbox(frame.image, self.spec.input_height, self.spec.input_width, self.spec.pad_value)
        try:
            outputs = self.adapter.run({self.spec.input_name: tensor})
        except Exception as error:
            raise DetectorError(f"Person detection failed: {type(error).__name__}") from error
        if not outputs:
            raise DetectorError("The model returned no output.")
        return self._decoder.decode(np.asarray(outputs[0]), ratio, frame.width, frame.height)

    def close(self) -> None:
        self.adapter.close()
