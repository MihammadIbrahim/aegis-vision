"""Live monitoring and routine recordings (requirements Sections 13 and 17; decision D-023).

* ``CameraStatus``: the last known state of a camera. ONLINE means the ingestion service received
  frames recently; OFFLINE means it could not (connection refused, timeout, decode failure); UNKNOWN
  means nothing has been reported yet. The error text is a short, generic reason written by the
  services; it never contains the stream address or credentials.
* ``RecordingSegment``: one Tier 1 file in the recordings tier. Routine recordings are volatile:
  the retention daemon removes them after the campus retention period.
* ``StorageStatus``: the storage node's last disk report.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class CameraState(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class StatusSource(StrEnum):
    INGESTION = "INGESTION"
    HEALTH_CHECK = "HEALTH_CHECK"


@dataclass(frozen=True, slots=True)
class CameraStatus:
    camera_id: UUID
    state: CameraState
    last_seen_at: datetime | None
    last_error: str | None
    source: StatusSource | None
    state_changed_at: datetime | None
    reported_at: datetime | None

    @classmethod
    def unknown(cls, camera_id: UUID) -> "CameraStatus":
        return cls(camera_id, CameraState.UNKNOWN, None, None, None, None, None)


@dataclass(frozen=True, slots=True)
class RecordingSegment:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    camera_id: UUID
    camera_name: str
    started_at: datetime
    ended_at: datetime
    relative_path: str
    size_bytes: int
    created_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class EventMarker:
    """An AI alert that falls inside a recording timeline (shown as a marker on the scrubber)."""

    alert_id: UUID
    camera_id: UUID
    alert_type: str
    priority: str
    status: str
    detected_at: datetime
    last_detected_at: datetime


@dataclass(frozen=True, slots=True)
class StorageStatus:
    node: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: Decimal
    level: str  # OK | WARNING | CRITICAL
    reported_at: datetime


@dataclass(frozen=True, slots=True)
class CampusUsage:
    """What one campus stores, by tier (from the indexes; the disk itself is shared by the node)."""

    recording_segments: int
    recording_bytes: int
    oldest_recording_at: datetime | None
    evidence_items: int
    evidence_bytes: int


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """One on-demand reachability test of a camera from the server (no credentials are used)."""

    reachable: bool
    checked_at: datetime
    latency_ms: int | None
    detail: str
