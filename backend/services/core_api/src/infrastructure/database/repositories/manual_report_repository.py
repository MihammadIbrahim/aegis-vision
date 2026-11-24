"""Manual reports through Row-Level Security and ``app_manual_report_submit`` (migration 0015)."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.manual_report import ManualReport
from src.domain.repositories.i_manual_report_repo import ReportSubmission

_COLUMNS = """id, reporter_guard_id, reporter_name, report_type, description, building_id, building_name,
    floor_id, floor_name, area_id, area_name, photo_path IS NOT NULL AS has_photo, occurred_at, status, review_note,
    reviewed_at, created_at"""


def _report(row: Any) -> ManualReport:
    return ManualReport(*row)


class SqlManualReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(self, report: ReportSubmission) -> tuple[UUID | None, str]:
        statement = text(
            "SELECT report_id, outcome FROM app_manual_report_submit(:id, :client, :type, :description, :building, "
            ":floor, :area, :photo, :photo_type, :occurred_at)"
        ).bindparams(*(bindparam(n, type_=Uuid) for n in ("id", "client", "building", "floor", "area")))
        row = (
            await self._session.execute(
                statement,
                {
                    "id": report.report_id,
                    "client": report.client_report_id,
                    "type": report.report_type,
                    "description": report.description,
                    "building": report.building_id,
                    "floor": report.floor_id,
                    "area": report.area_id,
                    "photo": report.photo_path,
                    "photo_type": report.photo_content_type,
                    "occurred_at": report.occurred_at,
                },
            )
        ).one()
        return row.report_id, row.outcome

    async def get(self, report_id: UUID) -> ManualReport | None:
        statement = text(f"SELECT {_COLUMNS} FROM manual_reports WHERE id = :id").bindparams(
            bindparam("id", type_=Uuid)
        )
        row = (await self._session.execute(statement, {"id": report_id})).one_or_none()
        return None if row is None else _report(row)

    async def photo(self, report_id: UUID) -> tuple[str, str] | None:
        statement = text(
            "SELECT photo_path, photo_content_type FROM manual_reports WHERE id = :id AND photo_path IS NOT NULL"
        ).bindparams(bindparam("id", type_=Uuid))
        row = (await self._session.execute(statement, {"id": report_id})).one_or_none()
        return None if row is None else (row.photo_path, row.photo_content_type)

    async def list_reports(self, *, limit: int, offset: int) -> list[ManualReport]:
        rows = await self._session.execute(
            text(f"SELECT {_COLUMNS} FROM manual_reports ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        )
        return [_report(r) for r in rows]

    async def review(self, report_id: UUID, reviewer_id: UUID, note: str | None) -> bool:
        statement = text(
            "UPDATE manual_reports SET status = 'REVIEWED', reviewed_by = :reviewer, reviewed_at = now(), "
            "review_note = :note WHERE id = :id AND status = 'SUBMITTED' RETURNING id"
        ).bindparams(bindparam("id", type_=Uuid), bindparam("reviewer", type_=Uuid))
        row = (await self._session.execute(statement, {"id": report_id, "reviewer": reviewer_id, "note": note})).first()
        return row is not None

    async def guard_locations(self) -> list[dict[str, object]]:
        rows = (await self._session.execute(text("SELECT * FROM app_guard_campus_locations()"))).mappings().all()
        return [dict(r) for r in rows]
