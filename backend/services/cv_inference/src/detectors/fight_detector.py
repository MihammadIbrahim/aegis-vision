"""Fight / violence detection from clips, with a model trained in ``ml_training`` (Video Swin, MViT
or R(2+1)D, exported to ONNX with a manifest).

Three parts:
* ``FrameRingBuffer``: the last ``clip_len`` sampled frames per camera.
* ``ViolenceOnnxClassifier`` (``ClipDetector``): resize/crop/normalise the frames the way the training
  loader did (manifest: ``resize_short``, ``crop``, Kinetics mean/std, RGB), run the clip through the
  network, return the violence probability.
* ``TemporalDetectorAdapter`` (``Detector``): lets the clip model run inside the unchanged frame
  pipeline. Each sampled frame goes into its camera's buffer; once the buffer is full, every
  ``infer_every`` frames the clip is classified, and the latest probability is reported as a
  whole-frame ``Detection`` labelled ``fight_violence`` on this and the following frames until the next
  classification. The existing ``EventRules`` then apply the threshold, the ``min_consecutive_frames``
  confirmation and the cooldown, and Core API makes it a HIGH-priority ``FIGHT_VIOLENCE`` alert.

Frame rate: the model was trained on clips sampled at ``target_fps`` (manifest). The pipeline's
``CV_SAMPLE_FPS`` should be close to it; a mismatch stretches or compresses motion. The adapter logs a
warning when they differ by more than 25 %.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np

from src.detectors.base_detector import ClipPrediction, DetectorError
from src.manifest import ModelManifest, load_model
from src.model_loader import Device, ModelAdapter
from src.schemas import BoundingBox, Detection, Frame, ModelInfo

logger = logging.getLogger(__name__)

VIOLENCE_LABEL = "fight_violence"
NORMAL_LABEL = "normal"


class FrameRingBuffer:
    """Keeps the most recent ``clip_len`` frames per camera, in arrival order."""

    def __init__(self, clip_len: int) -> None:
        if clip_len < 2:
            raise ValueError("clip_len must be at least 2")
        self.clip_len = clip_len
        self._buffers: dict[UUID, deque[Frame]] = {}
        self._pushed: dict[UUID, int] = {}

    def push(self, frame: Frame) -> None:
        buffer = self._buffers.setdefault(frame.camera_id, deque(maxlen=self.clip_len))
        buffer.append(frame)
        self._pushed[frame.camera_id] = self._pushed.get(frame.camera_id, 0) + 1

    def full(self, camera_id: UUID) -> bool:
        return len(self._buffers.get(camera_id, ())) == self.clip_len

    def frames(self, camera_id: UUID) -> list[Frame]:
        return list(self._buffers.get(camera_id, ()))

    def pushed(self, camera_id: UUID) -> int:
        return self._pushed.get(camera_id, 0)

    def reset(self, camera_id: UUID | None = None) -> None:
        if camera_id is None:
            self._buffers.clear()
            self._pushed.clear()
        else:
            self._buffers.pop(camera_id, None)
            self._pushed.pop(camera_id, None)


def _resize_short(image: np.ndarray, short: int) -> np.ndarray:
    h, w = image.shape[:2]
    scale = short / min(h, w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    if (new_w, new_h) == (w, h):
        return image
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def _center_crop(image: np.ndarray, size: int) -> np.ndarray:
    h, w = image.shape[:2]
    size = min(size, h, w)
    y, x = (h - size) // 2, (w - size) // 2
    return image[y : y + size, x : x + size]


class ViolenceOnnxClassifier:
    """``ClipDetector``: a sequence of ``clip_len`` frames → one ``ClipPrediction`` for ``fight_violence``."""

    def __init__(self, manifest_path: Path | str, *, device: Device = "cpu", verify_checksum: bool = True, adapter: ModelAdapter | None = None) -> None:
        if adapter is None:
            self.manifest, loaded = load_model(manifest_path, expected_task="violence_classification", device=device, verify_checksum=verify_checksum)
            self.adapter: ModelAdapter = loaded
        else:
            self.manifest = ModelManifest.load(manifest_path)
            self.adapter = adapter
        spec = self.manifest.input
        if spec.layout != "NCTHW" or self.manifest.output.format != "softmax-probabilities":
            raise DetectorError("The manifest does not describe a clip classifier (NCTHW input, softmax output).")
        if "violence" not in self.manifest.class_labels:
            raise DetectorError("The manifest's class labels must include 'violence'.")
        self.info: ModelInfo = ModelInfo(
            name=self.manifest.name,
            version=self.manifest.version,
            labels=(VIOLENCE_LABEL,),
            synthetic=False,
            license=self.manifest.license,
            source=self.manifest.model_info().source,
        )
        self.clip_length = int(spec.clip_len or spec.shape[2])
        self.target_fps = float(spec.target_fps or 8.0)
        self.threshold = float(self.manifest.thresholds.get("violence_probability", 0.5))
        self._size = int(spec.crop or spec.shape[-1])
        self._resize_short = int(spec.resize_short or self._size)
        self._mean = np.asarray(spec.mean, np.float32)
        self._std = np.asarray(spec.std, np.float32)
        self._violence_index = self.manifest.class_labels.index("violence")

    @property
    def clip_len(self) -> int:  # the ClipDetector protocol's name
        return self.clip_length

    def preprocess(self, frames: Sequence[Frame]) -> np.ndarray:
        if len(frames) != self.clip_length:
            raise DetectorError(f"A clip needs exactly {self.clip_length} frames, got {len(frames)}.")
        spec = self.manifest.input
        prepared = []
        for frame in frames:
            image = _center_crop(_resize_short(frame.image, self._resize_short), self._size)
            if image.shape[0] != self._size or image.shape[1] != self._size:
                image = cv2.resize(image, (self._size, self._size), interpolation=cv2.INTER_LINEAR)
            rgb = image[:, :, ::-1] if spec.color_order == "RGB" else image
            prepared.append((rgb.astype(np.float32) * spec.scale - self._mean) / self._std)
        clip = np.stack(prepared)  # T, H, W, 3
        return np.ascontiguousarray(clip.transpose(3, 0, 1, 2)[None])  # 1, 3, T, H, W

    def classify(self, frames: Sequence[Frame]) -> list[ClipPrediction]:
        tensor = self.preprocess(frames)
        try:
            outputs = self.adapter.run({self.manifest.input.name: tensor})
        except Exception as error:
            raise DetectorError(f"Violence classification failed: {type(error).__name__}") from error
        if not outputs:
            raise DetectorError("The model returned no output.")
        probabilities = np.asarray(outputs[0], dtype=np.float64)
        if probabilities.shape != (1, len(self.manifest.class_labels)) or not np.isfinite(probabilities).all():
            raise DetectorError(f"Unexpected classifier output {probabilities.shape}.")
        probability = float(np.clip(probabilities[0, self._violence_index], 0.0, 1.0))
        return [ClipPrediction(VIOLENCE_LABEL, probability, frames[0].index, frames[-1].index)]

    def close(self) -> None:
        self.adapter.close()


class TemporalDetectorAdapter:
    """Runs a ``ClipDetector`` inside the per-frame pipeline (see the module docstring)."""

    def __init__(self, classifier: ViolenceOnnxClassifier, *, infer_every: int = 4, min_report_probability: float = 0.05, sample_fps: float | None = None) -> None:
        if infer_every < 1 or not 0.0 <= min_report_probability <= 1.0:
            raise ValueError("infer_every >= 1 and 0 <= min_report_probability <= 1")
        self.classifier = classifier
        self.info = classifier.info
        self.buffer = FrameRingBuffer(classifier.clip_length)
        self.infer_every = infer_every
        self.min_report = min_report_probability
        self._last: dict[UUID, ClipPrediction] = {}
        self.clips_classified = 0
        if sample_fps is not None and abs(sample_fps - classifier.target_fps) / classifier.target_fps > 0.25:
            logger.warning(
                "fight model trained at %.1f fps but the pipeline samples %.1f fps: motion speed will differ from training",
                classifier.target_fps,
                sample_fps,
            )

    def detect(self, frame: Frame) -> list[Detection]:
        self.buffer.push(frame)
        camera = frame.camera_id
        if self.buffer.full(camera):
            since_full = self.buffer.pushed(camera) - self.buffer.clip_len
            if since_full % self.infer_every == 0:
                self._last[camera] = self.classifier.classify(self.buffer.frames(camera))[0]
                self.clips_classified += 1
        last = self._last.get(camera)
        if last is None or last.confidence < self.min_report:
            return []
        return [Detection(label=VIOLENCE_LABEL, confidence=last.confidence, box=BoundingBox(0.0, 0.0, float(frame.width), float(frame.height)))]

    def reset(self) -> None:
        self.buffer.reset()
        self._last.clear()

    def close(self) -> None:
        self.classifier.close()
