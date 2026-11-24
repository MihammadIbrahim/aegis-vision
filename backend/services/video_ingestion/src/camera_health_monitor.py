"""Online / offline decisions per camera (requirements 13.2; decision D-023).

A camera is ONLINE while frames keep arriving and OFFLINE once none arrived for
``offline_after_seconds`` or the stream process failed. Only changes are sent to the Core API
(which notifies the campus staff), plus a heartbeat for ONLINE cameras so "last seen" stays
current. Cameras with no usable source are reported UNKNOWN with the reason, once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

logger = logging.getLogger(__name__)

Reporter = Callable[[UUID, str, str | None, datetime | None], None]


@dataclass(slots=True)
class _Track:
    state: str = "UNKNOWN"
    last_frame: float | None = None
    last_reported_state: str | None = None
    last_report_at: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class StatusChange:
    camera_id: UUID
    state: str
    reason: str | None


@dataclass
class HealthMonitor:
    offline_after_seconds: float
    heartbeat_seconds: float
    reporter: Reporter
    _tracks: dict[UUID, _Track] = field(default_factory=dict)

    def frame_seen(self, camera_id: UUID, now: float) -> None:
        track = self._tracks.setdefault(camera_id, _Track())
        track.last_frame = now
        track.state = "ONLINE"
        track.reason = None

    def stream_failed(self, camera_id: UUID, reason: str) -> None:
        track = self._tracks.setdefault(camera_id, _Track())
        track.state = "OFFLINE"
        track.reason = reason

    def unavailable(self, camera_id: UUID, reason: str) -> None:
        track = self._tracks.setdefault(camera_id, _Track())
        track.state = "UNKNOWN"
        track.reason = reason

    def forget(self, camera_id: UUID) -> None:
        self._tracks.pop(camera_id, None)

    def evaluate(self, now: float) -> list[StatusChange]:
        """Apply the silence rule and send what changed (or is due a heartbeat)."""
        changes: list[StatusChange] = []
        for camera_id, track in self._tracks.items():
            if (
                track.state == "ONLINE"
                and track.last_frame is not None
                and now - track.last_frame > self.offline_after_seconds
            ):
                track.state = "OFFLINE"
                track.reason = "No frames received."
            due = track.last_reported_state != track.state or (
                track.state == "ONLINE"
                and (track.last_report_at is None or now - track.last_report_at >= self.heartbeat_seconds)
            )
            if not due:
                continue
            seen_at = None
            if track.state == "ONLINE" and track.last_frame is not None:
                seen_at = datetime.fromtimestamp(track.last_frame, tz=UTC)
            try:
                self.reporter(camera_id, track.state, track.reason, seen_at)
            except Exception as error:  # noqa: BLE001 - try again on the next evaluation
                logger.warning("camera %s: status could not be reported: %s", camera_id, error)
                continue
            if track.last_reported_state != track.state:
                changes.append(StatusChange(camera_id, track.state, track.reason))
            track.last_reported_state = track.state
            track.last_report_at = now
        return changes

    def state_of(self, camera_id: UUID) -> str:
        return self._tracks[camera_id].state if camera_id in self._tracks else "UNKNOWN"
