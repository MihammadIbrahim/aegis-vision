"""Organization Admin sign-in (public: no token needed to call these).

1. POST /auth/organization-admin/login                         credentials; a code is emailed
2. POST /auth/organization-admin/login/{login_id}/resend-otp   email a new code
3. POST /auth/organization-admin/login/{login_id}/verify       check the code; returns a token

The token is a normal organization (``tenant`` audience) access token. It is refused by
every Super Admin route, and Super Admin tokens are refused here and by every
organization route. These routes use the tenant database role with no signed-in user and
reach credentials only through the narrow functions of migration 0006.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from src.application.dtos.auth_dto import (
    AdminTokenResponse,
    LoginCodeSentResponse,
    OrganizationAdminLoginRequest,
    VerifyLoginRequest,
)
from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_rate_limiter import IRateLimiter
from src.application.use_cases.auth_use_cases import (
    AdminLoginForm,
    ResendOrganizationAdminLoginCode,
    StartOrganizationAdminLogin,
    VerifyOrganizationAdminLogin,
)
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import RateLimited
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.infrastructure.database.repositories.admin_login_repository import (
    SqlAdminLoginRepository,
)
from src.infrastructure.database.repositories.audit_log_repository import SqlAuthEventLog
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.jwt import TENANT_AUDIENCE, create_access_token
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import SessionDep


async def _limit_per_client_ip(request: Request) -> None:
    limiter: IRateLimiter = request.app.state.login_rate_limiter
    retry_after = limiter.hit(request.client.host if request.client else "unknown")
    if retry_after is not None:
        raise RateLimited(
            "Too many sign-in requests from this network. Please try again later.",
            code="TOO_MANY_REQUESTS",
            retry_after=retry_after,
        )


router = APIRouter(
    prefix="/auth/organization-admin",
    tags=["Organization Admin sign-in"],
    dependencies=[Depends(_limit_per_client_ip)],
)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _email(request: Request) -> IEmailSender:
    return request.app.state.email_sender


def _otp(request: Request) -> IOtpCodec:
    return request.app.state.otp_codec


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


@router.post("/login", response_model=LoginCodeSentResponse)
async def login(body: OrganizationAdminLoginRequest, request: Request, session: SessionDep) -> LoginCodeSentResponse:
    """Check the credentials and email a 6-digit code. No token yet. Any wrong value gives
    the same 401 ``INVALID_CREDENTIALS``."""
    settings = _settings(request)
    use_case = StartOrganizationAdminLogin(
        SqlAdminLoginRepository(session),
        Argon2PasswordHasher(),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(settings),
        login_ttl_seconds=settings.org_login_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
        auth_events=SqlAuthEventLog(session),
    )
    sent = await use_case.execute(
        AdminLoginForm(
            organization_name=body.organization_name,
            organization_id=body.organization_id,
            email=body.email,
            password=body.password,
        )
    )
    return LoginCodeSentResponse.from_result(sent)


@router.post("/login/{login_id}/resend-otp", response_model=LoginCodeSentResponse)
async def resend_code(login_id: UUID, request: Request, session: SessionDep) -> LoginCodeSentResponse:
    """Email a new code; the previous one stops working."""
    use_case = ResendOrganizationAdminLoginCode(
        SqlAdminLoginRepository(session),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return LoginCodeSentResponse.from_result(await use_case.execute(login_id))


@router.post("/login/{login_id}/verify", response_model=AdminTokenResponse)
async def verify_login(
    login_id: UUID, body: VerifyLoginRequest, request: Request, session: SessionDep
) -> AdminTokenResponse:
    """Check the code. Only now is an access token issued."""
    settings = _settings(request)
    user_id = await VerifyOrganizationAdminLogin(
        SqlAdminLoginRepository(session), _otp(request), SqlTransaction(session), SqlAuthEventLog(session)
    ).execute(login_id, body.otp)
    token = create_access_token(
        user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_access_token_expire_minutes,
        audience=TENANT_AUDIENCE,
    )
    return AdminTokenResponse(access_token=token, expires_in=settings.jwt_access_token_expire_minutes * 60)
