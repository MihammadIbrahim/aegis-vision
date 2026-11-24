"""Connections between a running vision worker and the Core API (decision D-024).

* ``AiConfigWatcher``: asks ``GET /internal/cameras/{id}/ai-config`` which modules this camera may run now
  (platform AND campus AND camera, set on the AI Configuration page), every ``poll_seconds``, and reports
  what it runs with ``POST /internal/cameras/{id}/ai-status`` (modules, frames per second, last error).
  When the Core API cannot be reached the last known answer stays; before any answer nothing is allowed.
* ``GatedDetector`` (decision D-028): asks the camera's configuration **before** each model runs and skips the
  models of modules the camera may not run, so a disabled module costs no inference: the weapon detector runs only
  with weapon detection, the fight classifier (and its clip buffer, cleared when switched off) only with
  fight/violence detection, the person detector only with human detection or Re-ID. With Re-ID on and human
  detection off, persons are still detected, internally, for the Re-ID crops only: they are kept for tracking but
  never drawn on the Live overlay. Detections of modules that are off are still dropped afterwards (a single model
  may serve several modules).
* ``OverlayWriter`` (``TrackObserver``): writes the newest detections of each frame to
  ``<storage root>/live/<organization>/<campus>/<camera>/overlay.json`` for the Live page (boxes as
  fractions of the frame, labels, confidences, track IDs, capture time). Written atomically.
* ``HttpObservationSink``: sends each Re-ID observation (512-number vector + person crop) to
  ``POST /internal/reid/observations``; refused cameras (Re-ID off) are counted, not retried.
All calls use the service token (``CV_SERVICE_TOKEN``), never a user token.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from src.detectors.base_detector import Detector
from src.schemas import Detection, Frame, Track

logger = logging.getLogger(__name__)

# Which module a detection label belongs to.
PERSON_LABELS = frozenset({"person"})
FIGHT_LABELS = frozenset({"fight", "violence", "fight_violence"})


def module_of(label: str) -> str:
    name = label.lower()
    if name in PERSON_LABELS:
        return "human_detection"
    if name in FIGHT_LABELS:
        return "fight_detection"
    return "weapon_detection"


def modules_served(detector: Detector) -> frozenset[str]:
    """The modules a model's detections serve. Person detections feed both human detection and Re-ID crops."""
    served: set[str] = set()
    for label in detector.info.labels:
        module = module_of(label)
        served.add(module)
        if module == "human_detection":
            served.add("person_reid")
    return frozenset(served)


@dataclass
class CameraConfig:
    organization_id: UUID
    campus_id: UUID
    modules: frozenset[str]
    confidence_threshold: float = 0.5


@dataclass
class AiConfigWatcher:
    camera_id: UUID
    client: httpx.Client
    token: str
    poll_seconds: float = 30.0
    worker: str = "cv_inference"
    clock: Callable[[], float] = time.monotonic
    config: CameraConfig | None = None
    last_error: str | None = None
    _checked_at: float | None = field(default=None, repr=False)
    _frames: int = field(default=0, repr=False)
    _window_started: float | None = field(default=None, repr=False)

    def _headers(self) -> dict[str, str]:
        return {"X-Service-Token": self.token}

    def refresh(self, *, force: bool = False) -> CameraConfig | None:
        now = self.clock()
        if not force and self._checked_at is not None and now - self._checked_at < self.poll_seconds:
            return self.config
        self._checked_at = now
        try:
            response = self.client.get(f"/api/v1/internal/cameras/{self.camera_id}/ai-config", headers=self._headers())
            if response.status_code == 200:
                body = response.json()
                self.config = CameraConfig(
                    UUID(body["organization_id"]),
                    UUID(body["campus_id"]),
                    frozenset(body["modules"]),
                    float(body.get("confidence_threshold", 0.5)),
                )
                self.last_error = None
            else:
                self.last_error = f"AI configuration refused ({response.status_code})"
                logger.warning("%s for camera %s", self.last_error, self.camera_id)
        except httpx.HTTPError as error:
            self.last_error = f"Core API unreachable: {type(error).__name__}"
            logger.warning("%s; keeping the last configuration", self.last_error)
        self.report()
        return self.config

    def allows(self, module: str) -> bool:
        return self.config is not None and module in self.config.modules

    def count_frame(self) -> None:
        if self._window_started is None:
            self._window_started = self.clock()
        self._frames += 1

    def report(self, running: Sequence[str] | None = None) -> None:
        elapsed = (self.clock() - self._window_started) if self._window_started is not None else 0.0
        fps = round(self._frames / elapsed, 2) if elapsed > 0 else None
        modules = sorted(running if running is not None else (self.config.modules if self.config else []))
        try:
            self.client.post(
                f"/api/v1/internal/cameras/{self.camera_id}/ai-status",
                json={"modules": modules, "frames_per_second": fps, "error": self.last_error, "worker": self.worker},
                headers=self._headers(),
            )
        except httpx.HTTPError as error:
            logger.warning("could not report the AI status: %s", type(error).__name__)
        self._frames, self._window_started = 0, self.clock()


class GatedDetector:
    """Wraps a detector (or each model of a ``CompositeDetector``); runs only the models of modules the camera may
    run now and keeps only their allowed detections (see the module docstring)."""

    def __init__(self, inner: Detector, watcher: AiConfigWatcher) -> None:
        self._inner = inner
        self._watcher = watcher
        self.info = inner.info
        parts = getattr(inner, "detectors", None)
        self._parts: list[Detector] = list(parts) if parts else [inner]
        self._served = [modules_served(part) for part in self._parts]
        self._idle = [False] * len(self._parts)
        self.skipped = [0] * len(self._parts)  # frames each model did not run (tests, diagnostics)

    def _keeps(self, label: str) -> bool:
        module = module_of(label)
        if self._watcher.allows(module):
            return True
        # Re-ID without human detection: persons are needed for the Re-ID crops (hidden from the overlay).
        return module == "human_detection" and self._watcher.allows("person_reid")

    def detect(self, frame: Frame) -> list[Detection]:
        self._watcher.refresh()
        self._watcher.count_frame()
        detections: list[Detection] = []
        for index, part in enumerate(self._parts):
            if not any(self._watcher.allows(module) for module in self._served[index]):
                self.skipped[index] += 1
                if not self._idle[index]:
                    self._idle[index] = True
                    reset = getattr(part, "reset", None)
                    if callable(reset):
                        reset()  # e.g. the fight classifier's clip buffer: no stale frames when switched back on
                continue
            self._idle[index] = False
            detections += [d for d in part.detect(frame) if self._keeps(d.label)]
        return detections

    def close(self) -> None:
        self._inner.close()


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".overlay-")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class OverlayWriter:
    """``TrackObserver``: the newest detections for the Live page's overlay."""

    def __init__(self, storage_root: Path, watcher: AiConfigWatcher) -> None:
        self._root = storage_root
        self._watcher = watcher
        self.written = 0

    def _path(self, camera_id: UUID) -> Path | None:
        config = self._watcher.config
        if config is None:
            return None
        return self._root / "live" / str(config.organization_id) / str(config.campus_id) / str(camera_id) / "overlay.json"

    def observe(self, frame: Frame, tracks: Sequence[Track]) -> None:
        path = self._path(frame.camera_id)
        if path is None:
            return
        height, width = frame.image.shape[:2]
        detections: list[dict[str, Any]] = []
        for track in tracks:
            if not self._watcher.allows(module_of(track.detection.label)):
                continue  # e.g. persons detected only for Re-ID crops while human detection is off
            box = track.detection.box
            detections.append(
                {
                    "label": track.detection.label,
                    "confidence": round(track.detection.confidence, 4),
                    "box": [
                        round(max(0.0, box.x1 / width), 4),
                        round(max(0.0, box.y1 / height), 4),
                        round(min(1.0, box.x2 / width), 4),
                        round(min(1.0, box.y2 / height), 4),
                    ],
                    "track_id": track.track_id if track.confirmed else None,
                    "module": module_of(track.detection.label),
                }
            )
        body = {
            "camera_id": str(frame.camera_id),
            "captured_at": frame.timestamp.isoformat(),
            "frame_width": int(width),
            "frame_height": int(height),
            "modules": sorted(self._watcher.config.modules) if self._watcher.config else [],
            "detections": detections,
        }
        _atomic_write(path, json.dumps(body).encode("utf-8"))
        self.written += 1


class HttpObservationSink:
    """Re-ID observations to the Core API. Needs observations that carry their vector and crop."""

    wants_media = True

    def __init__(self, client: httpx.Client, token: str, model_version: str) -> None:
        self._client = client
        self._token = token
        self._model = model_version
        self.sent = 0
        self.refused = 0
        self.failed = 0

    def record(self, observation: Any) -> None:
        if observation.embedding is None:
            return
        body = {
            "camera_id": str(observation.camera_id),
            "track_id": observation.track_id,
            "observed_at": observation.observed_at.isoformat(),
            "embedding": [round(float(v), 7) for v in observation.embedding],
            "model_version": self._model,
            "quality": observation.quality,
            "crop_jpeg_base64": base64.b64encode(observation.crop_jpeg).decode() if observation.crop_jpeg else None,
        }
        try:
            response = self._client.post(
                "/api/v1/internal/reid/observations", json=body, headers={"X-Service-Token": self._token}
            )
        except httpx.HTTPError as error:
            self.failed += 1
            logger.warning("Re-ID observation not delivered: %s", type(error).__name__)
            return
        if response.status_code == 201:
            self.sent += 1
        elif response.status_code == 409:
            self.refused += 1  # Re-ID is switched off for this camera
        else:
            self.failed += 1
            logger.warning("Re-ID observation refused: %s", response.status_code)

    def close(self) -> None:
        return None
