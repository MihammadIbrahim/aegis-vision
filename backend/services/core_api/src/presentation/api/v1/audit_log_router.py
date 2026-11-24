"""Audit logs of the caller's campus (decision D-024). Admins and Supervisors (Supervisors: operational
records and their own); Guards 403; Super Admin tokens are refused (the platform role has no access).

GET /audit-logs              filters: category (repeatable), actor_role, actor (displayed ID), action, outcome,
                             q (keyword), since, until, limit, offset
GET /audit-logs/categories   the categories the caller may read
GET /audit-logs/export       the same filters, as CSV (audited)
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Response

from src.application.dtos.audit_log_dto import AuditCategoriesResponse, AuditPageResponse
from src.application.use_cases.audit_log_use_cases import ViewAuditLogs, visible_categories
from src.domain.entities.audit_log import AuditCategory
from src.domain.repositories.i_audit_log_repo import AuditFilter
from src.infrastructure.database.repositories.audit_log_repository import SqlAuditLogReader, SqlOrganizationAuditLog
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.exporters.csv_exporter import to_csv
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/audit-logs", tags=["Audit logs"])


def _use_case(session) -> ViewAuditLogs:  # type: ignore[no-untyped-def]
    return ViewAuditLogs(SqlAuditLogReader(session), SqlOrganizationAuditLog(session), SqlTransaction(session))


def _filter(
    category: list[AuditCategory] | None,
    actor_role: str | None,
    actor: str | None,
    action: str | None,
    outcome: str | None,
    q: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    offset: int,
) -> AuditFilter:
    return AuditFilter(
        categories=tuple(c.value for c in category) if category else None,
        actor_role=actor_role,
        actor=actor,
        action=action,
        outcome=outcome,
        text=q,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=AuditPageResponse)
async def list_audit_logs(
    actor: StaffDep,
    session: SessionDep,
    category: list[AuditCategory] | None = Query(default=None),
    actor_role: Literal["ADMIN", "SUPERVISOR", "GUARD", "SYSTEM"] | None = None,
    actor_id: str | None = Query(default=None, alias="actor", max_length=64),
    action: str | None = Query(default=None, max_length=64, pattern=r"^[A-Z_]{3,64}$"),
    outcome: Literal["SUCCESS", "FAILURE", "DENIED"] | None = None,
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditPageResponse:
    where = _filter(category, actor_role, actor_id, action, outcome, q, since, until, limit, offset)
    return AuditPageResponse.of(await _use_case(session).page(actor, where), limit, offset)


@router.get("/categories", response_model=AuditCategoriesResponse)
async def audit_categories(actor: StaffDep) -> AuditCategoriesResponse:
    return AuditCategoriesResponse(categories=visible_categories(actor))


@router.get("/export")
async def export_audit_logs(
    actor: StaffDep,
    session: SessionDep,
    category: list[AuditCategory] | None = Query(default=None),
    actor_role: Literal["ADMIN", "SUPERVISOR", "GUARD", "SYSTEM"] | None = None,
    actor_id: str | None = Query(default=None, alias="actor", max_length=64),
    action: str | None = Query(default=None, max_length=64, pattern=r"^[A-Z_]{3,64}$"),
    outcome: Literal["SUCCESS", "FAILURE", "DENIED"] | None = None,
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    where = _filter(category, actor_role, actor_id, action, outcome, q, since, until, 1, 0)
    rows = await _use_case(session).export(actor, where)
    body = to_csv(
        [
            "occurred_at",
            "category",
            "action",
            "outcome",
            "actor_id",
            "actor_name",
            "actor_role",
            "target_type",
            "target_id",
            "target",
            "details",
        ],
        (
            [
                r.occurred_at,
                r.category.value,
                r.action,
                r.outcome.value,
                r.actor_display_id,
                r.actor_name,
                r.actor_role,
                r.target_type,
                r.target_id,
                r.target_label,
                r.metadata,
            ]
            for r in rows
        ),
    )
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-logs.csv"', "Cache-Control": "no-store"},
    )
