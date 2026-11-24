"""Campus/Branch Approval Requests: the Main Admin's side (Org Admin page 15; decisions D-020, H, I, D-024).

GET  /campus-requests                                   pending requests (verified, inside the 72-hour window)
GET  /campus-requests/history                           all requests; ?status=, q=, limit=, offset=
POST /campus-requests/authorization                     current password; emails the Main Admin a code
POST /campus-requests/authorization/{id}/resend-otp
POST /campus-requests/authorization/{id}/verify         code; one approval may now be made with this confirmation
POST /campus-requests/{id}/approve                      {authorization_id}: creates the campus and its Admin
POST /campus-requests/{id}/reject                       {current_password}: erases the applicant's details

Main Admin only (other Admins, Supervisors and Guards 403; Super Admin tokens 403). Decisions are audited.
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request

from src.application.dtos.branch_dto import (
    ApproveCampusRequestBody,
    CampusRequestHistoryResponse,
    CampusRequestResponse,
    RejectCampusRequestBody,
)
from src.application.dtos.supervisor_dto import (
    AuthorizationVerifiedResponse,
    CodeSentResponse,
    OtpRequest,
    SupervisorAuthorizationRequest,
)
from src.application.use_cases.branch_approval_use_cases import (
    CAMPUS_APPROVAL_KIND,
    ApproveCampusRequest,
    ListPendingCampusRequests,
    RejectCampusRequest,
    SearchCampusRequests,
)
from src.application.use_cases.supervisor_use_cases import (
    ResendSupervisorAuthorizationCode,
    StartSupervisorAuthorization,
    VerifySupervisorAuthorization,
)
from src.config.settings import Settings
from src.domain.repositories.i_branch_repo import CampusRequestFilter
from src.domain.value_objects.enums import CampusRequestStatus
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.branch_repository import (
    SqlCampusRepository,
)
from src.infrastructure.database.repositories.staff_account_repository import SqlStaffAccountRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.storage_settings_router import password_confirmation
from src.presentation.api.v1.supervisor_router import _limits
from src.presentation.dependencies import MainAdminDep, SessionDep

router = APIRouter(prefix="/campus-requests", tags=["Campus Approval Requests"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("", response_model=list[CampusRequestResponse])
async def list_pending_requests(actor: MainAdminDep, session: SessionDep) -> list[CampusRequestResponse]:
    requests = await ListPendingCampusRequests(SqlCampusRepository(session)).execute(actor)
    return [CampusRequestResponse.from_entity(request) for request in requests]


@router.get("/history", response_model=CampusRequestHistoryResponse)
async def request_history(
    actor: MainAdminDep,
    session: SessionDep,
    status: CampusRequestStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CampusRequestHistoryResponse:
    where = CampusRequestFilter(status=status, text=q, limit=limit, offset=offset)
    page = await SearchCampusRequests(SqlCampusRepository(session)).execute(actor, where)
    return CampusRequestHistoryResponse.of(page, limit, offset)


# --- The Main Admin's confirmation before approving (password + emailed code, decision H) --------------


@router.post("/authorization", response_model=CodeSentResponse)
async def start_approval_authorization(
    body: SupervisorAuthorizationRequest, request: Request, actor: MainAdminDep, session: SessionDep
) -> CodeSentResponse:
    settings = _settings(request)
    use_case = StartSupervisorAuthorization(
        SqlStaffAccountRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_authorization_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
        kind=CAMPUS_APPROVAL_KIND,
    )
    return CodeSentResponse.from_result(await use_case.execute(body.current_password))


@router.post("/authorization/{authorization_id}/resend-otp", response_model=CodeSentResponse)
async def resend_approval_code(
    authorization_id: UUID, request: Request, actor: MainAdminDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendSupervisorAuthorizationCode(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
        kind=CAMPUS_APPROVAL_KIND,
    )
    return CodeSentResponse.from_result(await use_case.execute(authorization_id))


@router.post("/authorization/{authorization_id}/verify", response_model=AuthorizationVerifiedResponse)
async def verify_approval_authorization(
    authorization_id: UUID, body: OtpRequest, request: Request, actor: MainAdminDep, session: SessionDep
) -> AuthorizationVerifiedResponse:
    use_case = VerifySupervisorAuthorization(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        form_ttl_seconds=_settings(request).supervisor_form_expire_minutes * 60,
        kind=CAMPUS_APPROVAL_KIND,
    )
    expires_at = await use_case.execute(authorization_id, body.otp)
    return AuthorizationVerifiedResponse(authorization_id=authorization_id, form_expires_at=expires_at)


# --- Decisions ----------------------------------------------------------------------------------------


@router.post("/{request_id}/approve", response_model=CampusRequestResponse)
async def approve_request(
    request_id: UUID, body: ApproveCampusRequestBody, actor: MainAdminDep, session: SessionDep
) -> CampusRequestResponse:
    use_case = ApproveCampusRequest(
        SqlCampusRepository(session),
        confirmations=SqlStaffAccountRepository(session),
        audit=SqlOrganizationAuditLog(session),
    )
    approved = await use_case.execute(actor, request_id, body.authorization_id)
    await session.commit()
    return CampusRequestResponse.from_entity(approved)


@router.post("/{request_id}/reject", response_model=CampusRequestResponse)
async def reject_request(
    request_id: UUID, body: RejectCampusRequestBody, request: Request, actor: MainAdminDep, session: SessionDep
) -> CampusRequestResponse:
    use_case = RejectCampusRequest(
        SqlCampusRepository(session),
        confirmation=password_confirmation(actor, session, request),
        audit=SqlOrganizationAuditLog(session),
    )
    rejected = await use_case.execute(actor, request_id, body.current_password)
    await session.commit()
    return CampusRequestResponse.from_entity(rejected)
