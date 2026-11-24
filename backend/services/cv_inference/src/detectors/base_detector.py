"""Detector interfaces. Real models implement these through a ``ModelAdapter`` (see
``model_loader.py``): the adapter runs the network, the detector does the model-specific
pre-processing (resize, normalise) and post-processing (decode boxes, map class IDs to labels).

* ``Detector``: one frame → detections (human, weapon). No real detector exists yet.
* ``ClipDetector``: a sequence of frames → clip-level predictions (fight/violence needs temporal
  context, requirements 4 E). Interface only; no implementation yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.schemas import Detection, Frame, ModelInfo


class DetectorError(Exception):
    """A detector failed on a frame (model error, bad output). Stops the pipeline: results would be
    unreliable."""


class Detector(Protocol):
    info: ModelInfo

    def detect(self, frame: Frame) -> list[Detection]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ClipPrediction:
    label: str
    confidence: float
    first_frame_index: int
    last_frame_index: int


class ClipDetector(Protocol):
    """For fight/violence models. The pipeline will buffer ``clip_length`` sampled frames per camera
    and call ``classify``; that buffering is added together with the first real clip model."""

    info: ModelInfo
    clip_length: int

    def classify(self, frames: Sequence[Frame]) -> list[ClipPrediction]: ...

    def close(self) -> None: ...
