"""Platform Super Admin management (decision D-019). Platform tokens and the platform database session
only; tenant tokens are refused (401). Tenant data comes only from the narrow ``platform_*`` functions.

GET  /platform/overview                                  counts, health summary, AI modules, recent activity
GET  /platform/organizations                             ?q= &status= &sort=name|created_at|camera_count
                                                         &order=asc|desc &limit=1–100 &offset=
GET  /platform/organizations/export                      CSV, same filters (up to 10 000 rows; audited)
GET  /platform/organizations/{organization_id}           profile, Main Admin, campuses with counts
PUT  /platform/organizations/{organization_id}/main-admin   {full_name, email, phone_number, current_password}
POST /platform/organizations/{organization_id}/status       {status, reason?, current_password}
GET  /platform/organizations/{organization_id}/ai-permissions   AI feature access (allowed / blocked / disabled)
PUT  /platform/organizations/{organization_id}/ai-permissions   {module_key, blocked, current_password} (D-028)
GET  /platform/ai-settings
PUT  /platform/ai-settings                               every switch and parameter + current_password
GET  /platform/audit-logs                                ?organization= &action= &outcome= &date_from= &date_to=
                                                         &limit=1–200 &offset=
GET  /platform/audit-logs/actions                        actions present, with counts
GET  /platform/audit-logs/export                         CSV, same filters (up to 10 000 rows; audited)
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from src.application.dtos.platform_management_dto import (
    AiModuleStatus,
    AiSettingsResponse,
    AuditActionResponse,
    AuditLogEntryResponse,
    AuditLogPageResponse,
    CampusResponse,
    ChangeOrganizationAiPermissionRequest,
    ChangeOrganizationStatusRequest,
    OrganizationAiPermissionResponse,
    OrganizationAiPermissionsResponse,
    OrganizationDetailResponse,
    OrganizationPageResponse,
    OrganizationResponse,
    OverviewResponse,
    PlatformCountsResponse,
    UpdateAiSettingsRequest,
    UpdateMainAdminRequest,
)
from src.application.use_cases.platform_admin_use_cases import RequestContext
from src.application.use_cases.platform_management_use_cases import (
    ChangeOrganizationAiPermission,
    ChangeOrganizationStatus,
    ExportAuditLogs,
    ExportOrganizations,
    GetAiSettings,
    GetOrganizationAiPermissions,
    GetOrganizationDetails,
    GetPlatformCounts,
    ListAuditActions,
    ListAuditLogs,
    ListOrganizations,
    UpdateAiSettings,
    UpdateMainAdminContact,
)
from src.domain.entities.platform_management import (
    AuditLogQuery,
    OrganizationListQuery,
    OrganizationStatus,
)
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    CurrentPasswordIncorrect,
    ValidationFailed,
)
from src.infrastructure.database.repositories.platform_admin_repository import (
    SqlPlatformAdminRepository,
    SqlPlatformAuditLog,
)
from src.infrastructure.database.repositories.platform_management_repository import (
    SqlPlatformManagementRepository,
)
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.platform_health_router import check_health
from src.presentation.dependencies import PlatformAdminDep, PlatformSessionDep

router = APIRouter(prefix="/platform", tags=["Platform Super Admin: management"])

SortKey = Literal["name", "created_at", "camera_count"]
Order = Literal["asc", "desc"]


def _context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent")
    )


def _sensitive(cls, request: Request, session):  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    return cls(
        SqlPlatformManagementRepository(session),
        SqlPlatformAdminRepository(session),
        Argon2PasswordHasher(),
        max_attempts=settings.platform_login_max_attempts,
        lockout=timedelta(minutes=settings.platform_login_lockout_minutes),
        audit=SqlPlatformAuditLog(session),
    )


async def _commit_on_password_failure(session, call):  # type: ignore[no-untyped-def]
    """Wrong current passwords are counted and audited, so that part is committed before the error."""
    try:
        return await call
    except (CurrentPasswordIncorrect, AccountLocked):
        await session.commit()
        raise


def _csv(body: str, filename: str) -> Response:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Response(
        content="﻿" + body,  # BOM: spreadsheets read UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-{stamp}.csv"', "Cache-Control": "no-store"},
    )


def _org_query(
    q: str | None, status: OrganizationStatus | None, sort: str, order: str, limit: int, offset: int
) -> OrganizationListQuery:
    return OrganizationListQuery((q or "").strip() or None, status, sort, order == "desc", limit, offset)


# --- Overview ---------------------------------------------------------------------------------------------


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request, admin: PlatformAdminDep, session: PlatformSessionDep) -> OverviewResponse:
    repository = SqlPlatformManagementRepository(session)
    counts = await GetPlatformCounts(repository).execute()
    report = await check_health(request, session)
    ai = AiSettingsResponse.of(await GetAiSettings(repository).execute())
    recent, _ = await ListAuditLogs(repository).execute(AuditLogQuery(limit=10))
    return OverviewResponse(
        counts=PlatformCountsResponse.of(counts),
        health={"overall_status": report.overall_status, "summary": report.summary},
        ai_modules=[AiModuleStatus(**m.model_dump()) for m in ai.modules],
        recent_activity=[AuditLogEntryResponse.of(e) for e in recent],
    )


# --- Organizations -----------------------------------------------------------------------------------------


@router.get("/organizations", response_model=OrganizationPageResponse)
async def list_organizations(
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
    q: str | None = Query(default=None, max_length=200, description="Name, official email or Organization ID"),
    status: OrganizationStatus | None = None,
    sort: SortKey = "created_at",
    order: Order = "desc",
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> OrganizationPageResponse:
    query = _org_query(q, status, sort, order, limit, offset)
    items, total = await ListOrganizations(SqlPlatformManagementRepository(session)).execute(query)
    return OrganizationPageResponse(
        items=[OrganizationResponse.of(o) for o in items], total=total, limit=limit, offset=offset
    )


@router.get("/organizations/export", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def export_organizations(
    request: Request,
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
    q: str | None = Query(default=None, max_length=200),
    status: OrganizationStatus | None = None,
    sort: SortKey = "created_at",
    order: Order = "desc",
) -> Response:
    body = await _sensitive(ExportOrganizations, request, session).run(
        admin.id, _org_query(q, status, sort, order, 1, 0), _context(request)
    )
    await session.commit()
    return _csv(body, "organizations")


@router.get("/organizations/{organization_id}", response_model=OrganizationDetailResponse)
async def organization_details(
    organization_id: str, admin: PlatformAdminDep, session: PlatformSessionDep
) -> OrganizationDetailResponse:
    repository = SqlPlatformManagementRepository(session)
    organization, campuses = await GetOrganizationDetails(repository).execute(organization_id)
    permissions = await GetOrganizationAiPermissions(repository).execute(organization.organization_code)
    return OrganizationDetailResponse(
        **OrganizationResponse.of(organization).model_dump(),
        campuses=[CampusResponse.of(c) for c in campuses],
        ai_permissions=[OrganizationAiPermissionResponse.of(p) for p in permissions],
    )


@router.get("/organizations/{organization_id}/ai-permissions", response_model=OrganizationAiPermissionsResponse)
async def organization_ai_permissions(
    organization_id: str, admin: PlatformAdminDep, session: PlatformSessionDep
) -> OrganizationAiPermissionsResponse:
    permissions = await GetOrganizationAiPermissions(SqlPlatformManagementRepository(session)).execute(
        organization_id
    )
    return OrganizationAiPermissionsResponse(
        organization_id=organization_id.strip(),
        ai_permissions=[OrganizationAiPermissionResponse.of(p) for p in permissions],
    )


@router.put("/organizations/{organization_id}/ai-permissions", response_model=OrganizationAiPermissionsResponse)
async def change_organization_ai_permission(
    organization_id: str,
    body: ChangeOrganizationAiPermissionRequest,
    request: Request,
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
) -> OrganizationAiPermissionsResponse:
    use_case = _sensitive(ChangeOrganizationAiPermission, request, session)
    permissions = await _commit_on_password_failure(
        session,
        use_case.run(
            admin.id,
            organization_id,
            module=body.module_key,
            blocked=body.blocked,
            current_password=body.current_password,
            context=_context(request),
        ),
    )
    await session.commit()
    return OrganizationAiPermissionsResponse(
        organization_id=organization_id.strip(),
        ai_permissions=[OrganizationAiPermissionResponse.of(p) for p in permissions],
    )


@router.put("/organizations/{organization_id}/main-admin", response_model=OrganizationResponse)
async def update_main_admin(
    organization_id: str,
    body: UpdateMainAdminRequest,
    request: Request,
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
) -> OrganizationResponse:
    use_case = _sensitive(UpdateMainAdminContact, request, session)
    updated = await _commit_on_password_failure(
        session,
        use_case.run(
            admin.id,
            organization_id,
            full_name=body.full_name,
            email=body.email,
            phone=body.phone_number,
            current_password=body.current_password,
            context=_context(request),
        ),
    )
    await session.commit()
    return OrganizationResponse.of(updated)


@router.post("/organizations/{organization_id}/status", response_model=OrganizationResponse)
async def change_status(
    organization_id: str,
    body: ChangeOrganizationStatusRequest,
    request: Request,
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
) -> OrganizationResponse:
    use_case = _sensitive(ChangeOrganizationStatus, request, session)
    updated = await _commit_on_password_failure(
        session,
        use_case.run(
            admin.id,
            organization_id,
            status=OrganizationStatus(body.status),
            reason=body.reason,
            current_password=body.current_password,
            context=_context(request),
        ),
    )
    await session.commit()
    return OrganizationResponse.of(updated)


# --- AI settings ---------------------------------------------------------------------------------------------


@router.get("/ai-settings", response_model=AiSettingsResponse)
async def ai_settings(admin: PlatformAdminDep, session: PlatformSessionDep) -> AiSettingsResponse:
    return AiSettingsResponse.of(await GetAiSettings(SqlPlatformManagementRepository(session)).execute())


@router.put("/ai-settings", response_model=AiSettingsResponse)
async def update_ai_settings(
    body: UpdateAiSettingsRequest, request: Request, admin: PlatformAdminDep, session: PlatformSessionDep
) -> AiSettingsResponse:
    use_case = _sensitive(UpdateAiSettings, request, session)
    updated = await _commit_on_password_failure(
        session,
        use_case.run(admin.id, body.values(), current_password=body.current_password, context=_context(request)),
    )
    await session.commit()
    return AiSettingsResponse.of(updated)


# --- Audit log ------------------------------------------------------------------------------------------------


def _audit_query(
    organization: str | None,
    action: str | None,
    outcome: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> AuditLogQuery:
    if date_from and date_to and date_to < date_from:
        raise ValidationFailed("The end date is before the start date.", code="INVALID_DATE_RANGE")
    start = None if date_from is None else datetime.combine(date_from, time.min, UTC)
    end = None if date_to is None else datetime.combine(date_to + timedelta(days=1), time.min, UTC)  # whole day
    return AuditLogQuery((organization or "").strip() or None, action, outcome, start, end, limit, offset)


_ACTION = Query(default=None, pattern=r"^[A-Z_]{3,64}$")
_DATE_HELP = "YYYY-MM-DD (UTC); date_to includes that whole day"


@router.get("/audit-logs", response_model=AuditLogPageResponse)
async def audit_logs(
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
    organization: str | None = Query(default=None, max_length=200, description="Organization ID or part of the name"),
    action: str | None = _ACTION,
    outcome: Literal["SUCCESS", "FAILURE"] | None = None,
    date_from: date | None = Query(default=None, description=_DATE_HELP),
    date_to: date | None = Query(default=None, description=_DATE_HELP),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditLogPageResponse:
    query = _audit_query(organization, action, outcome, date_from, date_to, limit, offset)
    entries, total = await ListAuditLogs(SqlPlatformManagementRepository(session)).execute(query)
    return AuditLogPageResponse(
        items=[AuditLogEntryResponse.of(e) for e in entries], total=total, limit=limit, offset=offset
    )


@router.get("/audit-logs/actions", response_model=list[AuditActionResponse])
async def audit_actions(admin: PlatformAdminDep, session: PlatformSessionDep) -> list[AuditActionResponse]:
    actions = await ListAuditActions(SqlPlatformManagementRepository(session)).execute()
    return [AuditActionResponse(action=a, count=n) for a, n in actions]


@router.get("/audit-logs/export", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def export_audit_logs(
    request: Request,
    admin: PlatformAdminDep,
    session: PlatformSessionDep,
    organization: str | None = Query(default=None, max_length=200),
    action: str | None = _ACTION,
    outcome: Literal["SUCCESS", "FAILURE"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    query = _audit_query(organization, action, outcome, date_from, date_to, 1, 0)
    body = await _sensitive(ExportAuditLogs, request, session).run(admin.id, query, _context(request))
    await session.commit()
    return _csv(body, "platform-audit-log")
