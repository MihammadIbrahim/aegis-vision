"""Live CCTV monitoring and the recordings archive (requirements Sections 13 and 17; decision D-023).

Admins and Supervisors of a campus see their cameras' online/offline status, the newest live frame
of each camera, test a camera's reachability, and browse and play routine recordings with the AI
alerts that fall inside them as timeline markers. Guards and Super Admins have no access to any of
it (the platform role has no grants on these tables, and the storage tiers are never exposed).

The internal reports (``ServiceReports``) are what the ingestion service and the retention daemon
send; they are authenticated with the service token in the router, not with a user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.application.use_cases.camera_use_cases import CameraFilter
from src.domain.entities.camera import Camera
from src.domain.entities.recording import (
    CameraState,
    CameraStatus,
    EventMarker,
    HealthCheckResult,
    RecordingSegment,
    StatusSource,
    StorageStatus,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound, ValidationFailed
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_camera_repo import ICameraRepository
from src.domain.repositories.i_recording_repo import (
    ExpiredSegment,
    IRecordingRepository,
    SegmentFilter,
    ServiceCamera,
)
from src.domain.repositories.i_storage_settings_repo import IStorageSettingsRepository
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import ensure_camera_viewer

LIVE_FRAME_NAME = "latest.jpg"
_SAFE_RELATIVE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*")
MAX_TIMELINE_DAYS = 31

# Audit actions (requirements Section 18).
LIVE_VIEW_STARTED = "LIVE_VIEW_STARTED"
CAMERA_HEALTH_CHECKED = "CAMERA_HEALTH_CHECKED"
RECORDING_PLAYED = "RECORDING_PLAYED"


def _missing_camera() -> NotFound:
    return NotFound("Camera not found.")


@dataclass(frozen=True, slots=True)
class MonitoredCamera:
    camera: Camera
    status: CameraStatus


@dataclass(frozen=True, slots=True)
class LiveFrame:
    data: bytes
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Timeline:
    cameras: list[MonitoredCamera]
    segments: list[RecordingSegment]
    markers: list[EventMarker]
    since: datetime
    until: datetime
    # The campus retention in force: each segment is purged ``retention_seconds`` after it ends.
    retention_seconds: int = 0


def recording_file(storage: ICampusMediaStorage, segment: RecordingSegment) -> Path:
    """Where a segment lives: ``recordings/<org>/<campus>/<camera>/...`` under the storage root."""
    prefix = f"{segment.organization_id}/{segment.campus_id}/"
    if not segment.relative_path.startswith(prefix):
        raise NotFound("Recording not found.", code="RECORDING_FILE_MISSING")
    return storage.tier_path(
        "recordings", segment.organization_id, segment.campus_id, segment.relative_path[len(prefix) :]
    )


class CameraMonitoring:
    def __init__(
        self,
        cameras: ICameraRepository,
        recordings: IRecordingRepository,
        storage: ICampusMediaStorage,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        live_stale_seconds: int = 15,
    ) -> None:
        self._cameras = cameras
        self._recordings = recordings
        self._storage = storage
        self._audit = audit
        self._transaction = transaction
        self._stale = live_stale_seconds

    async def overview(self, actor: User, where: CameraFilter = CameraFilter()) -> list[MonitoredCamera]:
        ensure_camera_viewer(actor)
        cameras = await self._cameras.list_cameras(actor.organization_id, actor.campus_id)
        statuses = await self._recordings.camera_statuses(actor.organization_id, actor.campus_id)
        return [
            MonitoredCamera(c, statuses.get(c.id) or CameraStatus.unknown(c.id)) for c in cameras if where.matches(c)
        ]

    async def one(self, actor: User, camera_id: UUID) -> MonitoredCamera:
        ensure_camera_viewer(actor)
        camera = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if camera is None:
            raise _missing_camera()
        statuses = await self._recordings.camera_statuses(actor.organization_id, actor.campus_id)
        return MonitoredCamera(camera, statuses.get(camera.id) or CameraStatus.unknown(camera.id))

    async def start_live_view(self, actor: User, camera_ids: list[UUID]) -> list[MonitoredCamera]:
        """Called once when a live page (or fullscreen view) opens: the audit record names the
        cameras; the frames themselves are not logged one by one."""
        ensure_camera_viewer(actor)
        wanted = set(camera_ids)
        shown = [m for m in await self.overview(actor) if not wanted or m.camera.id in wanted]
        if wanted and len(shown) != len(wanted):
            raise _missing_camera()
        await self._audit.record(
            actor, LIVE_VIEW_STARTED, "CAMERA", None, {"camera_ids": [str(m.camera.id) for m in shown]}
        )
        await self._transaction.commit()
        return shown

    async def live_frame(self, actor: User, camera_id: UUID) -> LiveFrame:
        ensure_camera_viewer(actor)
        camera = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if camera is None:
            raise _missing_camera()
        path = self._storage.tier_path("live", actor.organization_id, actor.campus_id, f"{camera.id}/{LIVE_FRAME_NAME}")
        try:
            stat = path.stat()
        except OSError:
            raise NotFound("No live frame is available for this camera.", code="LIVE_FRAME_UNAVAILABLE") from None
        captured_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if datetime.now(UTC) - captured_at > timedelta(seconds=self._stale):
            raise NotFound("The live frame is stale; the camera may be offline.", code="LIVE_FRAME_STALE")
        return LiveFrame(path.read_bytes(), captured_at)

    async def health_check(self, actor: User, camera_id: UUID, result: HealthCheckResult) -> MonitoredCamera:
        """Records the probe's result (made by the router, outside the database transaction). An
        unreachable camera becomes OFFLINE at once; a reachable one is marked ONLINE only while no
        ingestion report exists, because reachability alone does not prove that frames arrive."""
        ensure_camera_viewer(actor)
        camera = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if camera is None:
            raise _missing_camera()
        statuses = await self._recordings.camera_statuses(actor.organization_id, actor.campus_id)
        current = statuses.get(camera.id)
        if not result.reachable:
            await self._recordings.report_status(
                camera.id, CameraState.OFFLINE, result.detail, None, StatusSource.HEALTH_CHECK
            )
        elif current is None or current.source is StatusSource.HEALTH_CHECK or current.state is CameraState.UNKNOWN:
            await self._recordings.report_status(
                camera.id, CameraState.ONLINE, None, result.checked_at, StatusSource.HEALTH_CHECK
            )
        await self._audit.record(
            actor,
            CAMERA_HEALTH_CHECKED,
            "CAMERA",
            camera.id,
            {"reachable": result.reachable, "latency_ms": result.latency_ms, "detail": result.detail},
        )
        updated = await self.one(actor, camera.id)  # read back before commit: the RLS actor is per transaction
        await self._transaction.commit()
        return updated


class RecordingsArchive:
    def __init__(
        self,
        cameras: ICameraRepository,
        recordings: IRecordingRepository,
        storage: ICampusMediaStorage,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        retention: IStorageSettingsRepository | None = None,
        default_retention_seconds: int = 7 * 86400,
    ) -> None:
        self._cameras = cameras
        self._recordings = recordings
        self._storage = storage
        self._audit = audit
        self._transaction = transaction
        self._retention = retention
        self._default_retention = default_retention_seconds

    async def _retention_seconds(self, actor: User) -> int:
        policy = (
            None
            if self._retention is None
            else await self._retention.get_retention(actor.organization_id, actor.campus_id)
        )
        return self._default_retention if policy is None else policy.seconds

    async def timeline(
        self,
        actor: User,
        *,
        camera_id: UUID | None,
        where: CameraFilter,
        since: datetime | None,
        until: datetime | None,
        limit: int,
        offset: int,
    ) -> Timeline:
        ensure_camera_viewer(actor)
        until = until or datetime.now(UTC)
        since = since or until - timedelta(days=1)
        if since >= until:
            raise ValidationFailed("The start of the period must be before its end.", code="INVALID_PERIOD")
        if until - since > timedelta(days=MAX_TIMELINE_DAYS):
            raise ValidationFailed(f"A timeline covers at most {MAX_TIMELINE_DAYS} days.", code="PERIOD_TOO_LONG")
        cameras = await self._cameras.list_cameras(actor.organization_id, actor.campus_id)
        statuses = await self._recordings.camera_statuses(actor.organization_id, actor.campus_id)
        chosen = [c for c in cameras if where.matches(c) and (camera_id is None or c.id == camera_id)]
        if camera_id is not None and not chosen:
            raise _missing_camera()
        ids = tuple(c.id for c in chosen)
        retention_seconds = await self._retention_seconds(actor)
        if not ids:
            return Timeline([], [], [], since, until, retention_seconds)
        segments = await self._recordings.list_segments(
            actor.organization_id, actor.campus_id, SegmentFilter(ids, since, until, limit, offset)
        )
        markers = await self._recordings.markers(actor.organization_id, actor.campus_id, ids, since, until)
        monitored = [MonitoredCamera(c, statuses.get(c.id) or CameraStatus.unknown(c.id)) for c in chosen]
        return Timeline(monitored, segments, markers, since, until, retention_seconds)

    async def segment(self, actor: User, segment_id: UUID) -> RecordingSegment:
        ensure_camera_viewer(actor)
        segment = await self._recordings.get_segment(segment_id, actor.organization_id, actor.campus_id)
        if segment is None:
            raise NotFound("Recording not found.")
        return segment

    async def media(self, actor: User, segment_id: UUID) -> tuple[RecordingSegment, Path]:
        """The file to play. Each playback is audited (requirements Section 18)."""
        segment = await self.segment(actor, segment_id)
        path = recording_file(self._storage, segment)
        if not path.is_file():
            raise NotFound("The recording file is no longer on disk.", code="RECORDING_FILE_MISSING")
        await self._audit.record(
            actor,
            RECORDING_PLAYED,
            "RECORDING",
            segment.id,
            {"camera_id": str(segment.camera_id), "started_at": segment.started_at.isoformat()},
        )
        await self._transaction.commit()
        return segment, path

    async def storage(self, actor: User) -> StorageStatus | None:
        ensure_camera_viewer(actor)
        return await self._recordings.storage_status()


@dataclass(frozen=True, slots=True)
class StatusReport:
    camera_id: UUID
    state: CameraState
    error: str | None
    seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class SegmentReport:
    camera_id: UUID
    started_at: datetime
    ended_at: datetime
    relative_path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DiskReport:
    total_bytes: int
    used_bytes: int
    free_bytes: int


class ServiceReports:
    """What the ingestion service and the retention daemon tell the API (service token)."""

    def __init__(
        self,
        recordings: IRecordingRepository,
        transaction: ITransaction,
        *,
        default_retention_seconds: int,
        warning_percent: int,
        critical_percent: int,
        node: str,
    ) -> None:
        self._recordings = recordings
        self._transaction = transaction
        self._default_retention = default_retention_seconds
        self._warning = warning_percent
        self._critical = critical_percent
        self._node = node

    async def cameras(self) -> list[ServiceCamera]:
        return await self._recordings.service_cameras()

    async def camera_status(self, report: StatusReport) -> tuple[str, str | None]:
        outcome, previous = await self._recordings.report_status(
            report.camera_id, report.state, report.error, report.seen_at, StatusSource.INGESTION
        )
        if outcome == "CAMERA_NOT_FOUND":
            await self._transaction.rollback()
            raise NotFound("Camera not found or its organization is not active.", code="CAMERA_NOT_FOUND")
        if outcome == "INVALID":
            await self._transaction.rollback()
            raise ValidationFailed("Unknown camera state.", code="INVALID_STATE")
        await self._transaction.commit()
        return outcome, previous

    async def register_segment(self, report: SegmentReport) -> tuple[UUID, str]:
        if report.ended_at <= report.started_at:
            raise ValidationFailed("The segment must end after it starts.", code="INVALID_SPAN")
        if not _SAFE_RELATIVE_PATH.fullmatch(report.relative_path) or ".." in report.relative_path.split("/"):
            raise ValidationFailed("The recording path must be a plain relative path.", code="INVALID_PATH")
        segment_id, outcome = await self._recordings.register_segment(
            report.camera_id, report.started_at, report.ended_at, report.relative_path, report.size_bytes
        )
        if segment_id is None:
            await self._transaction.rollback()
            if outcome == "CAMERA_NOT_FOUND":
                raise NotFound("Camera not found or its organization is not active.", code="CAMERA_NOT_FOUND")
            raise ValidationFailed("The recording path is outside the camera's folder.", code="PATH_OUTSIDE_CAMERA")
        await self._transaction.commit()
        return segment_id, outcome

    async def expired(self, limit: int) -> list[ExpiredSegment]:
        return await self._recordings.expired_segments(self._default_retention, limit)

    async def purge(self, segment_ids: list[UUID]) -> int:
        count = await self._recordings.purge_segments(segment_ids)
        await self._transaction.commit()
        return count

    async def disk(self, report: DiskReport) -> tuple[str, bool]:
        level, notified = await self._recordings.report_storage(
            self._node, report.total_bytes, report.used_bytes, report.free_bytes, self._warning, self._critical
        )
        await self._transaction.commit()
        return level, notified
