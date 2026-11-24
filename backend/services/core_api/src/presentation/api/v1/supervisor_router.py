"""Supervisor creation by the signed-in Organization Admin (only Admins: ``OrganizationAdminDep``).

1. POST /organization-admin/supervisors/authorization                 current password; emails the Admin a code
   POST /organization-admin/supervisors/authorization/{id}/resend-otp
   POST /organization-admin/supervisors/authorization/{id}/verify      code; the form may now be submitted once
2. POST /organization-admin/supervisors                               the form; emails the Supervisor a code
   POST /organization-admin/supervisors/creations/{id}/resend-otp
3. POST /organization-admin/supervisors/creations/{id}/verify         code; creates the Supervisor
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from src.application.dtos.supervisor_dto import (
    AuthorizationVerifiedResponse,
    CodeSentResponse,
    CreateSupervisorRequest,
    OtpRequest,
    SupervisorAuthorizationRequest,
    SupervisorCreatedResponse,
)
from src.application.use_cases.supervisor_use_cases import (
    ResendSupervisorAuthorizationCode,
    ResendSupervisorCode,
    StartSupervisorAuthorization,
    SubmitSupervisor,
    VerifySupervisor,
    VerifySupervisorAuthorization,
)
from src.config.settings import Settings
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.infrastructure.database.repositories.staff_account_repository import (
    SqlStaffAccountRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import OrganizationAdminDep, SessionDep

router = APIRouter(prefix="/organization-admin/supervisors", tags=["Supervisor creation"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


@router.post("/authorization", response_model=CodeSentResponse)
async def start_authorization(
    body: SupervisorAuthorizationRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    """Re-confirm the Admin's current password, then email the Admin a code."""
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
    )
    return CodeSentResponse.from_result(await use_case.execute(body.current_password))


@router.post("/authorization/{authorization_id}/resend-otp", response_model=CodeSentResponse)
async def resend_authorization_code(
    authorization_id: UUID, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendSupervisorAuthorizationCode(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return CodeSentResponse.from_result(await use_case.execute(authorization_id))


@router.post("/authorization/{authorization_id}/verify", response_model=AuthorizationVerifiedResponse)
async def verify_authorization(
    authorization_id: UUID, body: OtpRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> AuthorizationVerifiedResponse:
    use_case = VerifySupervisorAuthorization(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        form_ttl_seconds=_settings(request).supervisor_form_expire_minutes * 60,
    )
    expires_at = await use_case.execute(authorization_id, body.otp)
    return AuthorizationVerifiedResponse(authorization_id=authorization_id, form_expires_at=expires_at)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CodeSentResponse)
async def submit_supervisor(
    body: CreateSupervisorRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    """Submit the Supervisor form (uses up the confirmation) and email the Supervisor a code."""
    settings = _settings(request)
    use_case = SubmitSupervisor(
        SqlStaffAccountRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_creation_expire_minutes * 60,
    )
    sent = await use_case.execute(
        authorization_id=body.authorization_id,
        full_name=body.full_name,
        username=body.username,
        email=body.email,
        phone=body.phone_number,
        password=body.password,
    )
    return CodeSentResponse.from_result(sent)


@router.post("/creations/{creation_id}/resend-otp", response_model=CodeSentResponse)
async def resend_supervisor_code(
    creation_id: UUID, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendSupervisorCode(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return CodeSentResponse.from_result(await use_case.execute(creation_id))


@router.post(
    "/creations/{creation_id}/verify", status_code=status.HTTP_201_CREATED, response_model=SupervisorCreatedResponse
)
async def verify_supervisor(
    creation_id: UUID, body: OtpRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> SupervisorCreatedResponse:
    """Check the Supervisor's code; creates the Supervisor and returns the new Supervisor ID."""
    use_case = VerifySupervisor(
        SqlStaffAccountRepository(session), request.app.state.otp_codec, SqlTransaction(session)
    )
    return SupervisorCreatedResponse.from_entity(await use_case.execute(creation_id, body.otp))
