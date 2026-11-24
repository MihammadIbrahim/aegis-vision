"""Camera status, the recordings index and the storage report (migration 0023; decision D-023).

Staff methods take the actor's organization and campus and are repeated by Row-Level Security.
The ``service_*`` methods back the internal endpoints used by the ingestion service and the
retention daemon; no actor is bound there, so they go through SECURITY DEFINER functions that
check the camera's organization and campus are active, exactly like detection intake.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.recording import (
    CameraState,
    CameraStatus,
    CampusUsage,
    EventMarker,
    RecordingSegment,
    StatusSource,
    StorageStatus,
)


@dataclass(frozen=True, slots=True)
class ServiceCamera:
    """What the ingestion service needs to open a camera (the stream URL never has credentials)."""

    camera_id: UUID
    organization_id: UUID
    campus_id: UUID
    display_name: str
    connection_type: str
    stream_url: str | None


@dataclass(frozen=True, slots=True)
class ExpiredSegment:
    segment_id: UUID
    organization_id: UUID
    campus_id: UUID
    camera_id: UUID
    relative_path: str
    started_at: datetime
    retention_seconds: int


@dataclass(frozen=True, slots=True)
class SegmentFilter:
    camera_ids: tuple[UUID, ...] | None = None  # None: every camera of the campus
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 500
    offset: int = 0


class IRecordingRepository(Protocol):
    # --- Signed-in staff (own campus) -----------------------------------------------------------
    async def camera_statuses(self, organization_id: UUID, campus_id: UUID) -> dict[UUID, CameraStatus]: ...

    async def list_segments(
        self, organization_id: UUID, campus_id: UUID, where: SegmentFilter
    ) -> list[RecordingSegment]: ...

    async def get_segment(
        self, segment_id: UUID, organization_id: UUID, campus_id: UUID
    ) -> RecordingSegment | None: ...

    async def markers(
        self,
        organization_id: UUID,
        campus_id: UUID,
        camera_ids: tuple[UUID, ...] | None,
        since: datetime,
        until: datetime,
    ) -> list[EventMarker]: ...

    async def storage_status(self) -> StorageStatus | None: ...

    async def campus_usage(self, organization_id: UUID, campus_id: UUID) -> CampusUsage: ...

    # --- Internal services and server-side checks -----------------------------------------------
    async def service_cameras(self) -> list[ServiceCamera]: ...

    async def report_status(
        self, camera_id: UUID, state: CameraState, error: str | None, seen_at: datetime | None, source: StatusSource
    ) -> tuple[str, str | None]:
        """(outcome, previous state): CHANGED, UNCHANGED, CAMERA_NOT_FOUND or INVALID."""
        ...

    async def register_segment(
        self, camera_id: UUID, started_at: datetime, ended_at: datetime, relative_path: str, size_bytes: int
    ) -> tuple[UUID | None, str]:
        """(segment id, outcome): REGISTERED, DUPLICATE, CAMERA_NOT_FOUND or PATH_OUTSIDE_CAMERA."""
        ...

    async def expired_segments(self, default_seconds: int, limit: int) -> list[ExpiredSegment]: ...

    async def purge_segments(self, segment_ids: list[UUID]) -> int: ...

    async def report_storage(
        self, node: str, total: int, used: int, free: int, warning_percent: int, critical_percent: int
    ) -> tuple[str, bool]:
        """(level, notified)."""
        ...
