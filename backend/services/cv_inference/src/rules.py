"""Turning per-frame detections into confirmed events.

**What the pipeline decides** (here):
* which model labels matter, and as which alert type (``class_map``; other labels, such as ``person``,
  are tracked but never sent: human detection does not create alerts, decision D-013);
* per-label confidence thresholds;
* confirmation: a label must pass its threshold on ``min_consecutive_frames`` sampled frames in a
  row before an event fires (one noisy frame is not an alert);
* throttling: while the same situation continues, at most one event per ``cooldown_seconds`` of
  video time, so the backend keeps the alert's "last detected" time current without a request per
  frame;
* a stable ``detection_id`` per event.

**What the backend decides** (Core API, migration 0015): alert identity. It ignores a
``detection_id`` it has seen (DUPLICATE) and merges a repeat into an open alert of the same camera and
type within ``ALERT_DEDUP_SECONDS`` (REPEATED). ``cooldown_seconds`` must therefore stay below that
window (checked in ``config.py``), otherwise a continuing threat could open a second alert.

An "episode" is a run of positive frames for one camera and alert type. It ends after
``reset_after_missing_frames`` sampled frames without a qualifying detection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.schemas import AlertType, DetectionEvent, Frame, Track


@dataclass(frozen=True, slots=True)
class RuleConfig:
    class_map: dict[str, AlertType]
    thresholds: dict[str, float] = field(default_factory=dict)
    default_threshold: float = 0.5
    min_consecutive_frames: int = 3
    cooldown_seconds: float = 10.0
    reset_after_missing_frames: int = 2

    def __post_init__(self) -> None:
        for label, value in {**self.thresholds, "<default>": self.default_threshold}.items():
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"threshold for {label} must be between 0 and 1")
        if self.min_consecutive_frames < 1 or self.reset_after_missing_frames < 0 or self.cooldown_seconds < 0:
            raise ValueError("min_consecutive_frames >= 1, reset_after_missing_frames >= 0, cooldown_seconds >= 0")

    def threshold(self, label: str) -> float:
        return self.thresholds.get(label, self.default_threshold)


@dataclass
class RuleStats:
    frames: int = 0
    detections: int = 0
    unmapped: int = 0  # labels without an alert type (e.g. person): tracked, never sent
    below_threshold: int = 0
    unconfirmed_episodes: int = 0  # ended before reaching min_consecutive_frames
    suppressed_by_cooldown: int = 0
    events: int = 0


@dataclass
class _Episode:
    start_index: int
    streak: int = 0
    missing: int = 0
    emitted: int = 0
    last_emitted_at: datetime | None = None
    best_confidence: float = 0.0
    best_label: str = ""
    best_track: int | None = None


class EventRules:
    def __init__(self, config: RuleConfig, *, model_name: str) -> None:
        self._config = config
        self._model = model_name
        self._episodes: dict[tuple[UUID, AlertType], _Episode] = {}
        self.stats = RuleStats()

    def process(self, frame: Frame, tracks: list[Track]) -> list[DetectionEvent]:
        """Feed one sampled frame's tracked detections; returns the events to send now."""
        self.stats.frames += 1
        best: dict[AlertType, Track] = {}
        for track in tracks:
            self.stats.detections += 1
            detection = track.detection
            alert_type = self._config.class_map.get(detection.label)
            if alert_type is None:
                self.stats.unmapped += 1
                continue
            if detection.confidence < self._config.threshold(detection.label):
                self.stats.below_threshold += 1
                continue
            current = best.get(alert_type)
            if current is None or detection.confidence > current.detection.confidence:
                best[alert_type] = track

        events: list[DetectionEvent] = []
        for alert_type, track in best.items():
            key = (frame.camera_id, alert_type)
            episode = self._episodes.get(key) or _Episode(start_index=frame.index)
            self._episodes[key] = episode
            episode.streak += 1
            episode.missing = 0
            if track.detection.confidence >= episode.best_confidence:
                episode.best_confidence = track.detection.confidence
                episode.best_label = track.detection.label
                episode.best_track = track.track_id
            if episode.streak < self._config.min_consecutive_frames:
                continue
            due = (
                episode.last_emitted_at is None
                or (frame.timestamp - episode.last_emitted_at).total_seconds() >= self._config.cooldown_seconds
            )
            if not due:
                self.stats.suppressed_by_cooldown += 1
                continue
            events.append(self._event(frame, alert_type, episode))
            episode.emitted += 1
            episode.last_emitted_at = frame.timestamp
            episode.best_confidence = 0.0  # the next event reports the best confidence since this one

        # Episodes of this camera without a qualifying detection in this frame.
        for (camera_id, alert_type), episode in list(self._episodes.items()):
            if camera_id != frame.camera_id or alert_type in best:
                continue
            episode.missing += 1
            if episode.missing > self._config.reset_after_missing_frames:
                if episode.emitted == 0:
                    self.stats.unconfirmed_episodes += 1
                del self._episodes[(camera_id, alert_type)]
        self.stats.events += len(events)
        return events

    def _event(self, frame: Frame, alert_type: AlertType, episode: _Episode) -> DetectionEvent:
        # Same video, camera, type, episode and event number → same ID, on every run and every retry.
        material = f"{frame.source_id}|{frame.camera_id}|{alert_type.value}|{episode.start_index}|{episode.emitted}"
        digest = hashlib.sha256(material.encode()).hexdigest()[:32]
        return DetectionEvent(
            detection_id=f"{self._model}-{digest}"[:128],
            camera_id=frame.camera_id,
            alert_type=alert_type,
            confidence=round(episode.best_confidence, 4),
            detected_at=frame.timestamp,
            label=episode.best_label,
            frame_index=frame.index,
            track_id=episode.best_track,
        )
