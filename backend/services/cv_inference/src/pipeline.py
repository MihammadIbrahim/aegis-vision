"""The processing pipeline: FrameSource → Detector → Tracker → [TrackObservers] → EventRules → DetectionSink.

Each stage is an interface, so a real model, an RTSP source or another sink can be swapped in
without touching the others. ``TrackObserver`` is where track-level consumers attach: the planned
cross-camera Re-ID component will take person crops from (frame, tracks) there. Nothing is attached
by default, and no cross-camera matching exists yet.

Failure policy:
* **Stops the run** (``report.stopped_reason`` is set): a video that cannot be read, a detector
  error, or a sink configuration error (wrong token, intake off, unknown camera).
* **Recorded and continues**: an event the backend rejects (422) or cannot take after all retries.
  These are listed in ``report.undelivered``, never dropped silently.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from src.detection_publisher import (
    DetectionSink,
    SinkConfigurationError,
    SinkRejectedError,
    SinkResult,
    SinkUnavailableError,
)
from src.detectors.base_detector import Detector
from src.rules import EventRules, RuleStats
from src.schemas import DetectionEvent, Frame, Track
from src.tracking.single_tracker import Tracker
from src.video_source import FrameSource, VideoSourceError

logger = logging.getLogger(__name__)


class TrackObserver(Protocol):
    """Receives each sampled frame with its camera-local tracks. Must not modify them."""

    def observe(self, frame: Frame, tracks: Sequence[Track]) -> None: ...


@dataclass(frozen=True, slots=True)
class Undelivered:
    event: DetectionEvent
    reason: str


@dataclass
class PipelineReport:
    model_name: str
    model_synthetic: bool
    frames: int = 0
    tracks_started: int = 0
    delivered: list[SinkResult] = field(default_factory=list)
    undelivered: list[Undelivered] = field(default_factory=list)
    stopped_reason: str | None = None
    rule_stats: RuleStats = field(default_factory=RuleStats)
    detector_ms_total: float = 0.0
    wall_seconds: float = 0.0

    @property
    def outcomes(self) -> dict[str, int]:
        return dict(Counter(r.outcome for r in self.delivered))

    @property
    def detector_ms_mean(self) -> float:
        return self.detector_ms_total / self.frames if self.frames else 0.0

    @property
    def frames_per_second(self) -> float:
        return self.frames / self.wall_seconds if self.wall_seconds else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model_name,
            "model_is_synthetic": self.model_synthetic,
            "frames_processed": self.frames,
            "tracks_started": self.tracks_started,
            "events": self.rule_stats.events,
            "delivered": self.outcomes,
            "undelivered": [{"detection_id": u.event.detection_id, "reason": u.reason} for u in self.undelivered],
            "stopped_reason": self.stopped_reason,
            "rules": vars(self.rule_stats),
            "detector_ms_mean": round(self.detector_ms_mean, 3),
            "processing_fps": round(self.frames_per_second, 2),
        }


class Pipeline:
    def __init__(
        self,
        source: FrameSource,
        detector: Detector,
        tracker: Tracker,
        rules: EventRules,
        sink: DetectionSink,
        observers: Sequence[TrackObserver] = (),
    ) -> None:
        self._observers = list(observers)
        self._source = source
        self._detector = detector
        self._tracker = tracker
        self._rules = rules
        self._sink = sink

    def run(self) -> PipelineReport:
        report = PipelineReport(model_name=self._detector.info.name, model_synthetic=self._detector.info.synthetic)
        report.rule_stats = self._rules.stats
        if self._detector.info.synthetic:
            logger.warning("using the synthetic test detector %s: not a real model", self._detector.info.name)
        started = time.perf_counter()
        seen_tracks: set[int] = set()
        try:
            for frame in self._source.frames():
                report.frames += 1
                t0 = time.perf_counter()
                try:
                    detections = self._detector.detect(frame)
                except Exception as error:
                    report.stopped_reason = f"detector failed at frame {frame.index}: {type(error).__name__}"
                    logger.error(report.stopped_reason)
                    break
                report.detector_ms_total += (time.perf_counter() - t0) * 1000
                tracks = self._tracker.update(frame.camera_id, frame.index, detections)
                for track in tracks:
                    if track.confirmed and track.track_id not in seen_tracks:
                        seen_tracks.add(track.track_id)
                        report.tracks_started += 1
                for observer in self._observers:
                    observer.observe(frame, tracks)
                for event in self._rules.process(frame, tracks):
                    try:
                        report.delivered.append(self._sink.send(event))
                    except SinkConfigurationError as error:
                        report.undelivered.append(Undelivered(event, str(error)))
                        report.stopped_reason = str(error)
                        logger.error("stopping: %s", error)
                        return report
                    except (SinkRejectedError, SinkUnavailableError) as error:
                        report.undelivered.append(Undelivered(event, str(error)))
                        logger.error("event %s not delivered: %s", event.detection_id, error)
                if report.stopped_reason:
                    break
        except VideoSourceError as error:
            report.stopped_reason = str(error)
            logger.error("stopping: %s", error)
        finally:
            self._source.close()
            report.wall_seconds = time.perf_counter() - started
        return report
