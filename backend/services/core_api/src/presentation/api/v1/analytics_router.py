"""Reports & Analytics and the Dashboard (Org Admin pages 1 and 22, Supervisor pages 1 and 18; decision D-024).
Admins and Supervisors of the campus; Guards and Super Admin tokens 403.

GET /analytics              ?period=TODAY|LAST_7_DAYS|LAST_30_DAYS|CUSTOM&since=&until=&building_id=&floor_id=&area_id=
GET /analytics/export       the same, ?format=csv|pdf (audited)
GET /analytics/dashboard    counters, urgent and recent alerts, recent cases, Guards, trends, recent activity
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.analytics_use_cases import Analytics, Period, resolve_period
from src.domain.entities.user import User
from src.infrastructure.database.repositories.analytics_repository import Scope, SqlAnalyticsRepository
from src.infrastructure.database.repositories.audit_log_repository import SqlAuditLogReader, SqlOrganizationAuditLog
from src.infrastructure.database.repositories.organization_settings_repository import (
    SqlOrganizationSettingsRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.exporters.pdf_exporter import report_csv, report_pdf
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/analytics", tags=["Reports & Analytics"])

Preset = Literal["TODAY", "LAST_7_DAYS", "LAST_30_DAYS", "CUSTOM"]


def _analytics(actor: User, session: AsyncSession) -> Analytics:
    return Analytics(
        SqlAnalyticsRepository(session, actor),
        SqlOrganizationAuditLog(session),
        SqlAuditLogReader(session),
        SqlTransaction(session),
    )


async def _scope(
    actor: User,
    session: AsyncSession,
    preset: str,
    since: datetime | None,
    until: datetime | None,
    building_id: UUID | None,
    floor_id: UUID | None,
    area_id: UUID | None,
) -> tuple[Period, Scope, str]:
    settings = await SqlOrganizationSettingsRepository(session).load(actor.organization_id)
    time_zone = settings.time_zone if settings else "UTC"
    period = resolve_period(preset, since, until, time_zone, datetime.now(UTC))
    scope = Scope(period.since, period.until, time_zone, building_id, floor_id, area_id)
    return period, scope, settings.name if settings else ""


@router.get("")
async def analytics(
    actor: StaffDep,
    session: SessionDep,
    period: Preset = "LAST_7_DAYS",
    since: datetime | None = None,
    until: datetime | None = None,
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
) -> dict[str, Any]:
    resolved, scope, _ = await _scope(actor, session, period, since, until, building_id, floor_id, area_id)
    return await _analytics(actor, session).report(actor, resolved, scope)


@router.get("/export")
async def export(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    format: Literal["csv", "pdf"] = Query(default="csv"),  # noqa: A002  (the query parameter's name)
    period: Preset = "LAST_7_DAYS",
    since: datetime | None = None,
    until: datetime | None = None,
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
) -> Response:
    resolved, scope, organization = await _scope(actor, session, period, since, until, building_id, floor_id, area_id)
    use_case = _analytics(actor, session)
    report = await use_case.report(actor, resolved, scope)
    if format == "pdf":
        content = report_pdf(report, organization=organization, generated_by=f"{actor.full_name} ({actor.role.value})")
        media, suffix = "application/pdf", "pdf"
    else:
        content, media, suffix = report_csv(report), "text/csv; charset=utf-8", "csv"
    await use_case.record_export(actor, resolved, format)
    stamp = resolved.until.strftime("%Y%m%d")
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="security-report-{stamp}.{suffix}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/dashboard")
async def dashboard(actor: StaffDep, session: SessionDep) -> dict[str, Any]:
    _, scope, _ = await _scope(actor, session, "LAST_7_DAYS", None, None, None, None, None)
    return await _analytics(actor, session).dashboard(actor, scope)
