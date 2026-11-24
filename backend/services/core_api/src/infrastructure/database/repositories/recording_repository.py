"""Camera status, the recordings index, AI event markers and the storage report (migration 0023)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.recording import (
    CameraState,
    CameraStatus,
    CampusUsage,
    EventMarker,
    RecordingSegment,
    StatusSource,
    StorageStatus,
)
from src.domain.repositories.i_recording_repo import ExpiredSegment, SegmentFilter, ServiceCamera

_SEGMENT_COLUMNS = """s.id, s.organization_id, s.campus_id, s.camera_id, x.display_name AS camera_name,
       s.started_at, s.ended_at, s.relative_path, s.size_bytes, s.created_at"""
_SEGMENT_FROM = "FROM recording_segments s JOIN cameras x ON x.id = s.camera_id"
_SCOPE = "s.organization_id = :org AND s.campus_id = :campus"


def _sql(statement: str, *uuid_params: str, arrays: tuple[str, ...] = ()):  # type: ignore[no-untyped-def]
    params = [bindparam(n, type_=Uuid) for n in uuid_params]
    params += [bindparam(n, type_=ARRAY(Uuid)) for n in arrays]
    return text(statement).bindparams(*params)


def _segment(row: Any) -> RecordingSegment:
    return RecordingSegment(
        id=row.id,
        organization_id=row.organization_id,
        campus_id=row.campus_id,
        camera_id=row.camera_id,
        camera_name=row.camera_name,
        started_at=row.started_at,
        ended_at=row.ended_at,
        relative_path=row.relative_path,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


class SqlRecordingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Signed-in staff --------------------------------------------------------------------------

    async def camera_statuses(self, organization_id: UUID, campus_id: UUID) -> dict[UUID, CameraStatus]:
        rows = await self._session.execute(
            _sql(
                "SELECT camera_id, state, last_seen_at, last_error, source, state_changed_at, reported_at "
                "FROM camera_status WHERE organization_id = :org AND campus_id = :campus",
                "org",
                "campus",
            ),
            {"org": organization_id, "campus": campus_id},
        )
        return {
            r.camera_id: CameraStatus(
                camera_id=r.camera_id,
                state=CameraState(r.state),
                last_seen_at=r.last_seen_at,
                last_error=r.last_error,
                source=StatusSource(r.source),
                state_changed_at=r.state_changed_at,
                reported_at=r.reported_at,
            )
            for r in rows
        }

    async def list_segments(
        self, organization_id: UUID, campus_id: UUID, where: SegmentFilter
    ) -> list[RecordingSegment]:
        clauses = [_SCOPE]
        params: dict[str, Any] = {
            "org": organization_id,
            "campus": campus_id,
            "limit": where.limit,
            "offset": where.offset,
        }
        arrays: tuple[str, ...] = ()
        if where.camera_ids is not None:
            clauses.append("s.camera_id = ANY (:cameras)")
            params["cameras"] = list(where.camera_ids)
            arrays = ("cameras",)
        if where.since is not None:
            clauses.append("s.ended_at > :since")
            params["since"] = where.since
        if where.until is not None:
            clauses.append("s.started_at < :until")
            params["until"] = where.until
        rows = await self._session.execute(
            _sql(
                f"SELECT {_SEGMENT_COLUMNS} {_SEGMENT_FROM} WHERE {' AND '.join(clauses)} "
                "ORDER BY s.started_at DESC, s.id LIMIT :limit OFFSET :offset",
                "org",
                "campus",
                arrays=arrays,
            ),
            params,
        )
        return [_segment(r) for r in rows]

    async def get_segment(self, segment_id: UUID, organization_id: UUID, campus_id: UUID) -> RecordingSegment | None:
        row = (
            await self._session.execute(
                _sql(f"SELECT {_SEGMENT_COLUMNS} {_SEGMENT_FROM} WHERE {_SCOPE} AND s.id = :id", "id", "org", "campus"),
                {"id": segment_id, "org": organization_id, "campus": campus_id},
            )
        ).first()
        return None if row is None else _segment(row)

    async def markers(
        self,
        organization_id: UUID,
        campus_id: UUID,
        camera_ids: tuple[UUID, ...] | None,
        since: datetime,
        until: datetime,
    ) -> list[EventMarker]:
        clauses = [
            "a.organization_id = :org AND a.campus_id = :campus",
            "a.last_detected_at >= :since",
            "a.detected_at <= :until",
        ]
        params: dict[str, Any] = {"org": organization_id, "campus": campus_id, "since": since, "until": until}
        arrays: tuple[str, ...] = ()
        if camera_ids is not None:
            clauses.append("a.camera_id = ANY (:cameras)")
            params["cameras"] = list(camera_ids)
            arrays = ("cameras",)
        rows = await self._session.execute(
            _sql(
                "SELECT a.id, a.camera_id, a.alert_type, a.priority, a.status, a.detected_at, a.last_detected_at "
                f"FROM alerts a WHERE {' AND '.join(clauses)} ORDER BY a.detected_at LIMIT 2000",
                "org",
                "campus",
                arrays=arrays,
            ),
            params,
        )
        return [
            EventMarker(r.id, r.camera_id, r.alert_type, r.priority, r.status, r.detected_at, r.last_detected_at)
            for r in rows
        ]

    async def storage_status(self) -> StorageStatus | None:
        row = (await self._session.execute(text("SELECT * FROM app_storage_status()"))).first()
        if row is None:
            return None
        return StorageStatus(
            node=row.node,
            total_bytes=row.total_bytes,
            used_bytes=row.used_bytes,
            free_bytes=row.free_bytes,
            percent_used=row.percent_used,
            level=row.level,
            reported_at=row.reported_at,
        )

    async def campus_usage(self, organization_id: UUID, campus_id: UUID) -> CampusUsage:
        row = (
            await self._session.execute(
                _sql(
                    "SELECT (SELECT count(*) FROM recording_segments s WHERE {seg}) AS rc, "
                    "(SELECT COALESCE(sum(size_bytes), 0) FROM recording_segments s WHERE {seg}) AS rb, "
                    "(SELECT min(started_at) FROM recording_segments s WHERE {seg}) AS ro, "
                    "(SELECT count(*) FROM evidence_items e WHERE {ev}) AS ec, "
                    "(SELECT COALESCE(sum(size_bytes), 0) FROM evidence_items e WHERE {ev}) AS eb".format(
                        seg="s.organization_id = :org AND s.campus_id = :campus",
                        ev="e.organization_id = :org AND e.campus_id = :campus",
                    ),
                    "org",
                    "campus",
                ),
                {"org": organization_id, "campus": campus_id},
            )
        ).one()
        return CampusUsage(int(row.rc), int(row.rb), row.ro, int(row.ec), int(row.eb))

    # --- Internal services ------------------------------------------------------------------------

    async def service_cameras(self) -> list[ServiceCamera]:
        rows = await self._session.execute(text("SELECT * FROM app_service_cameras()"))
        return [
            ServiceCamera(r.camera_id, r.organization_id, r.campus_id, r.display_name, r.connection_type, r.stream_url)
            for r in rows
        ]

    async def report_status(
        self, camera_id: UUID, state: CameraState, error: str | None, seen_at: datetime | None, source: StatusSource
    ) -> tuple[str, str | None]:
        row = (
            await self._session.execute(
                _sql(
                    "SELECT outcome, previous_state "
                    "FROM app_camera_status_report(:camera, :state, :error, :seen, :source)",
                    "camera",
                ),
                {"camera": camera_id, "state": state.value, "error": error, "seen": seen_at, "source": source.value},
            )
        ).one()
        return row.outcome, row.previous_state

    async def register_segment(
        self, camera_id: UUID, started_at: datetime, ended_at: datetime, relative_path: str, size_bytes: int
    ) -> tuple[UUID | None, str]:
        row = (
            await self._session.execute(
                _sql(
                    "SELECT segment_id, outcome "
                    "FROM app_recording_segment_register(:camera, :started, :ended, :path, :size)",
                    "camera",
                ),
                {
                    "camera": camera_id,
                    "started": started_at,
                    "ended": ended_at,
                    "path": relative_path,
                    "size": size_bytes,
                },
            )
        ).one()
        return row.segment_id, row.outcome

    async def expired_segments(self, default_seconds: int, limit: int) -> list[ExpiredSegment]:
        rows = await self._session.execute(
            text("SELECT * FROM app_recording_segments_expired(:default_seconds, :limit)"),
            {"default_seconds": default_seconds, "limit": limit},
        )
        return [
            ExpiredSegment(
                r.segment_id,
                r.organization_id,
                r.campus_id,
                r.camera_id,
                r.relative_path,
                r.started_at,
                r.retention_seconds,
            )
            for r in rows
        ]

    async def purge_segments(self, segment_ids: list[UUID]) -> int:
        if not segment_ids:
            return 0
        count: int = (
            await self._session.execute(
                _sql("SELECT app_recording_segments_purge(:ids)", arrays=("ids",)), {"ids": segment_ids}
            )
        ).scalar_one()
        return count

    async def report_storage(
        self, node: str, total: int, used: int, free: int, warning_percent: int, critical_percent: int
    ) -> tuple[str, bool]:
        row = (
            await self._session.execute(
                text("SELECT level, notified FROM app_storage_report(:node, :total, :used, :free, :warn, :crit)"),
                {
                    "node": node,
                    "total": total,
                    "used": used,
                    "free": free,
                    "warn": warning_percent,
                    "crit": critical_percent,
                },
            )
        ).one()
        return row.level, row.notified
