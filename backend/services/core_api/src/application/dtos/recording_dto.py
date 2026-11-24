"""Bodies for live monitoring, recordings and the internal service reports (decision D-023).
No stream URL with credentials, no file-system path and no service token is ever returned."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.camera_dto import CameraResponse
from src.application.use_cases.recording_use_cases import (
    DiskReport,
    MonitoredCamera,
    SegmentReport,
    StatusReport,
    Timeline,
)
from src.domain.entities.recording import (
    CameraState,
    CameraStatus,
    CampusUsage,
    EventMarker,
    HealthCheckResult,
    RecordingSegment,
    StorageStatus,
)
from src.domain.repositories.i_recording_repo import ExpiredSegment, ServiceCamera


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CameraStatusResponse(_Frozen):
    state: CameraState
    last_seen_at: datetime | None
    last_error: str | None = Field(description="A short, generic reason; never the address or credentials")
    source: Literal["INGESTION", "HEALTH_CHECK"] | None
    state_changed_at: datetime | None
    reported_at: datetime | None

    @classmethod
    def of(cls, s: CameraStatus) -> "CameraStatusResponse":
        return cls(
            state=s.state,
            last_seen_at=s.last_seen_at,
            last_error=s.last_error,
            source=None if s.source is None else s.source.value,  # type: ignore[arg-type]
            state_changed_at=s.state_changed_at,
            reported_at=s.reported_at,
        )


class MonitoredCameraResponse(_Frozen):
    camera: CameraResponse
    status: CameraStatusResponse

    @classmethod
    def of(cls, m: MonitoredCamera) -> "MonitoredCameraResponse":
        return cls(camera=CameraResponse.from_entity(m.camera), status=CameraStatusResponse.of(m.status))


class LiveSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_ids: list[UUID] = Field(default_factory=list, max_length=64, description="Empty: every camera of the campus")


class HealthCheckResponse(_Frozen):
    reachable: bool
    checked_at: datetime
    latency_ms: int | None
    detail: str
    camera: MonitoredCameraResponse

    @classmethod
    def of(cls, r: HealthCheckResult, m: MonitoredCamera) -> "HealthCheckResponse":
        return cls(
            reachable=r.reachable,
            checked_at=r.checked_at,
            latency_ms=r.latency_ms,
            detail=r.detail,
            camera=MonitoredCameraResponse.of(m),
        )


class RecordingSegmentResponse(_Frozen):
    id: UUID
    camera_id: UUID
    camera_name: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    size_bytes: int
    expires_at: datetime | None = Field(
        default=None, description="When the retention daemon may delete it (end + campus retention); timeline only"
    )

    @classmethod
    def of(cls, s: RecordingSegment, retention_seconds: int | None = None) -> "RecordingSegmentResponse":
        return cls(
            id=s.id,
            camera_id=s.camera_id,
            camera_name=s.camera_name,
            started_at=s.started_at,
            ended_at=s.ended_at,
            duration_seconds=round(s.duration_seconds, 3),
            size_bytes=s.size_bytes,
            expires_at=None if retention_seconds is None else s.ended_at + timedelta(seconds=retention_seconds),
        )


class EventMarkerResponse(_Frozen):
    alert_id: UUID
    camera_id: UUID
    alert_type: str
    priority: str
    status: str
    detected_at: datetime
    last_detected_at: datetime

    @classmethod
    def of(cls, m: EventMarker) -> "EventMarkerResponse":
        return cls(
            alert_id=m.alert_id,
            camera_id=m.camera_id,
            alert_type=m.alert_type,
            priority=m.priority,
            status=m.status,
            detected_at=m.detected_at,
            last_detected_at=m.last_detected_at,
        )


class TimelineResponse(_Frozen):
    since: datetime
    until: datetime
    retention_seconds: int = Field(description="The campus retention in force for routine recordings")
    cameras: list[MonitoredCameraResponse]
    segments: list[RecordingSegmentResponse] = Field(description="Newest first")
    markers: list[EventMarkerResponse] = Field(description="AI alerts inside the period, oldest first")

    @classmethod
    def of(cls, t: Timeline) -> "TimelineResponse":
        return cls(
            since=t.since,
            until=t.until,
            retention_seconds=t.retention_seconds,
            cameras=[MonitoredCameraResponse.of(c) for c in t.cameras],
            segments=[RecordingSegmentResponse.of(s, t.retention_seconds) for s in t.segments],
            markers=[EventMarkerResponse.of(m) for m in t.markers],
        )


class CampusUsageResponse(_Frozen):
    recording_segments: int
    recording_bytes: int
    oldest_recording_at: datetime | None
    evidence_items: int
    evidence_bytes: int

    @classmethod
    def of(cls, u: CampusUsage) -> "CampusUsageResponse":
        return cls(
            recording_segments=u.recording_segments,
            recording_bytes=u.recording_bytes,
            oldest_recording_at=u.oldest_recording_at,
            evidence_items=u.evidence_items,
            evidence_bytes=u.evidence_bytes,
        )


class StorageStatusResponse(_Frozen):
    node: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float
    level: Literal["OK", "WARNING", "CRITICAL"]
    reported_at: datetime

    @classmethod
    def of(cls, s: StorageStatus) -> "StorageStatusResponse":
        return cls(
            node=s.node,
            total_bytes=s.total_bytes,
            used_bytes=s.used_bytes,
            free_bytes=s.free_bytes,
            percent_used=float(s.percent_used),
            level=s.level,  # type: ignore[arg-type]
            reported_at=s.reported_at,
        )


# --- Internal service endpoints (X-Service-Token) ------------------------------------------------


class ServiceCameraResponse(_Frozen):
    camera_id: UUID
    organization_id: UUID
    campus_id: UUID
    display_name: str
    connection_type: str
    stream_url: str | None

    @classmethod
    def of(cls, c: ServiceCamera) -> "ServiceCameraResponse":
        return cls(
            camera_id=c.camera_id,
            organization_id=c.organization_id,
            campus_id=c.campus_id,
            display_name=c.display_name,
            connection_type=c.connection_type,
            stream_url=c.stream_url,
        )


class CameraStatusReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: CameraState
    error: str | None = Field(default=None, max_length=300, description="Generic reason; must not contain the URL")
    seen_at: datetime | None = None

    def to_report(self, camera_id: UUID) -> StatusReport:
        return StatusReport(camera_id, self.state, self.error, self.seen_at)


class CameraStatusReportResponse(_Frozen):
    outcome: Literal["CHANGED", "UNCHANGED"]
    previous_state: CameraState | None


class SegmentReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_id: UUID
    started_at: datetime
    ended_at: datetime
    relative_path: str = Field(max_length=512, description="Inside the recordings tier: <org>/<campus>/<camera>/...")
    size_bytes: int = Field(ge=0)

    def to_report(self) -> SegmentReport:
        return SegmentReport(self.camera_id, self.started_at, self.ended_at, self.relative_path, self.size_bytes)


class SegmentReportResponse(_Frozen):
    segment_id: UUID
    outcome: Literal["REGISTERED", "DUPLICATE"]


class ExpiredSegmentResponse(_Frozen):
    segment_id: UUID
    organization_id: UUID
    campus_id: UUID
    camera_id: UUID
    relative_path: str
    started_at: datetime
    retention_seconds: int

    @classmethod
    def of(cls, e: ExpiredSegment) -> "ExpiredSegmentResponse":
        return cls(
            segment_id=e.segment_id,
            organization_id=e.organization_id,
            campus_id=e.campus_id,
            camera_id=e.camera_id,
            relative_path=e.relative_path,
            started_at=e.started_at,
            retention_seconds=e.retention_seconds,
        )


class PurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_ids: list[UUID] = Field(min_length=1, max_length=5000)


class PurgeResponse(_Frozen):
    purged: int


class DiskReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)

    def to_report(self) -> DiskReport:
        return DiskReport(self.total_bytes, self.used_bytes, self.free_bytes)


class DiskReportResponse(_Frozen):
    level: Literal["OK", "WARNING", "CRITICAL"]
    notified: bool
