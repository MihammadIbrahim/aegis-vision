"""Data passed between the stages of the CV pipeline.

Frame → Detection (per frame) → Track (per camera, temporary IDs) → DetectionEvent (confirmed by the
rules, sent to Core API). These are plain, validated value objects; no model, video or HTTP code
lives here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

import numpy as np


class AlertType(StrEnum):
    """The detection types Core API accepts (``POST /api/v1/internal/detections``, migration 0015).
    WEAPON and FIGHT_VIOLENCE become HIGH-priority alerts there; RESTRICTED_AREA STANDARD."""

    WEAPON = "WEAPON"
    FIGHT_VIOLENCE = "FIGHT_VIOLENCE"
    RESTRICTED_AREA = "RESTRICTED_AREA"


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded video frame. ``image`` is BGR uint8, shape (height, width, 3)."""

    camera_id: UUID
    source_id: str  # stable identity of the video or stream the frame came from
    index: int  # position in the source, counting every decoded frame (not only sampled ones)
    timestamp: datetime  # capture time, timezone-aware
    image: np.ndarray = field(repr=False)

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("frame index must be non-negative")
        if self.timestamp.tzinfo is None:
            raise ValueError("frame timestamp must be timezone-aware")
        if self.image.dtype != np.uint8 or self.image.ndim != 3 or self.image.shape[2] != 3:
            raise ValueError("frame image must be uint8 with shape (height, width, 3)")

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Pixel coordinates, top-left (x1, y1) to bottom-right (x2, y2)."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        values = (self.x1, self.y1, self.x2, self.y2)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("bounding box coordinates must be finite")
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bounding box must have positive width and height")

    @property
    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    def iou(self, other: BoundingBox) -> float:
        """Intersection over union, 0.0 to 1.0."""
        width = min(self.x2, other.x2) - max(self.x1, other.x1)
        height = min(self.y2, other.y2) - max(self.y1, other.y1)
        if width <= 0 or height <= 0:
            return 0.0
        intersection = width * height
        return intersection / (self.area + other.area - intersection)


@dataclass(frozen=True, slots=True)
class Detection:
    """One object found in one frame. ``label`` is the model's class name (see the rules' class map)."""

    label: str
    confidence: float
    box: BoundingBox

    def __post_init__(self) -> None:
        if not self.label or not self.label.strip():
            raise ValueError("detection label must not be empty")
        if not (0.0 <= self.confidence <= 1.0) or not math.isfinite(self.confidence):
            raise ValueError("detection confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Track:
    """A detection followed across frames of ONE camera. The ID is temporary and local to that
    camera's tracker state; it is not a real-world identity (requirements 4 B), and the same number
    never links people across cameras. Cross-camera matching is Re-ID's job (not built yet)."""

    track_id: int
    detection: Detection
    hits: int  # frames this track was matched in
    first_frame_index: int
    camera_id: UUID | None = None  # set by the trackers; None only in hand-built test tracks
    confirmed: bool = True  # False while a new track has been seen in only one frame (ByteTracker)


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """What produced the detections: recorded with results so they stay traceable."""

    name: str
    version: str
    labels: tuple[str, ...]
    synthetic: bool = False  # True only for test detectors that are NOT real models
    license: str | None = None
    source: str | None = None  # where the weights came from (URL, registry entry)


@dataclass(frozen=True, slots=True)
class DetectionEvent:
    """A confirmed event, ready for Core API. ``detection_id`` is stable: the same video, camera and
    episode always give the same ID, so a resend is ignored by the backend (DUPLICATE)."""

    detection_id: str
    camera_id: UUID
    alert_type: AlertType
    confidence: float
    detected_at: datetime
    label: str
    frame_index: int
    track_id: int | None = None
