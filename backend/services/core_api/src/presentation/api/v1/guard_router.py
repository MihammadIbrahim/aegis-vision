"""Guard creation by a signed-in Admin (``GuardCreatorDep``; Supervisors could until decision D-017).

1. POST /guards/authorization                 current password; emails the acting user a code
   POST /guards/authorization/{id}/resend-otp
   POST /guards/authorization/{id}/verify      code; the Guard form may now be submitted once
2. POST /guards                               the form; emails the Guard a code
   POST /guards/creations/{id}/resend-otp
3. POST /guards/creations/{id}/verify         code; creates the Guard in the Admin's organization and campus
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from src.application.dtos.guard_dto import GuardCreatedResponse
from src.application.dtos.supervisor_dto import (
    AuthorizationVerifiedResponse,
    CodeSentResponse,
    CreateSupervisorRequest,
    OtpRequest,
    SupervisorAuthorizationRequest,
)
from src.application.use_cases.supervisor_use_cases import (
    GUARD_KIND,
    ResendSupervisorAuthorizationCode,
    ResendSupervisorCode,
    StartSupervisorAuthorization,
    SubmitSupervisor,
    VerifySupervisor,
    VerifySupervisorAuthorization,
)
from src.config.settings import Settings
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.value_objects.system_id import generate_guard_id
from src.infrastructure.database.repositories.staff_account_repository import (
    SqlGuardCreationRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import GuardCreatorDep, SessionDep

router = APIRouter(prefix="/guards", tags=["Guard creation"])


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
    body: SupervisorAuthorizationRequest, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> CodeSentResponse:
    settings = _settings(request)
    use_case = StartSupervisorAuthorization(
        SqlGuardCreationRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_authorization_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
        kind=GUARD_KIND,
    )
    return CodeSentResponse.from_result(await use_case.execute(body.current_password))


@router.post("/authorization/{authorization_id}/resend-otp", response_model=CodeSentResponse)
async def resend_authorization_code(
    authorization_id: UUID, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendSupervisorAuthorizationCode(
        SqlGuardCreationRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
        kind=GUARD_KIND,
    )
    return CodeSentResponse.from_result(await use_case.execute(authorization_id))


@router.post("/authorization/{authorization_id}/verify", response_model=AuthorizationVerifiedResponse)
async def verify_authorization(
    authorization_id: UUID, body: OtpRequest, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> AuthorizationVerifiedResponse:
    use_case = VerifySupervisorAuthorization(
        SqlGuardCreationRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        form_ttl_seconds=_settings(request).supervisor_form_expire_minutes * 60,
        kind=GUARD_KIND,
    )
    expires_at = await use_case.execute(authorization_id, body.otp)
    return AuthorizationVerifiedResponse(authorization_id=authorization_id, form_expires_at=expires_at)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CodeSentResponse)
async def submit_guard(
    body: CreateSupervisorRequest, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> CodeSentResponse:
    """Guard form: Name, Username, Email, Phone Number, Password (same fields as a Supervisor)."""
    settings = _settings(request)
    use_case = SubmitSupervisor(
        SqlGuardCreationRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_creation_expire_minutes * 60,
        kind=GUARD_KIND,
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
async def resend_guard_code(
    creation_id: UUID, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendSupervisorCode(
        SqlGuardCreationRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
        kind=GUARD_KIND,
    )
    return CodeSentResponse.from_result(await use_case.execute(creation_id))


@router.post(
    "/creations/{creation_id}/verify", status_code=status.HTTP_201_CREATED, response_model=GuardCreatedResponse
)
async def verify_guard(
    creation_id: UUID, body: OtpRequest, request: Request, actor: GuardCreatorDep, session: SessionDep
) -> GuardCreatedResponse:
    use_case = VerifySupervisor(
        SqlGuardCreationRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        display_id_factory=generate_guard_id,
        kind=GUARD_KIND,
    )
    return GuardCreatedResponse.from_entity(await use_case.execute(creation_id, body.otp))
