"""Forgot password for Organization Admins and Supervisors (public; limited per client IP).

1. POST /auth/password-reset                       Organization ID + email; same answer whether or not they match
   POST /auth/password-reset/{reset_id}/resend-otp
2. POST /auth/password-reset/{reset_id}/verify      code -> one-time reset token
3. POST /auth/password-reset/{reset_id}/complete    reset token + new password; earlier sessions stop working
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from src.application.dtos.supervisor_dto import (
    CompletePasswordResetRequest,
    OtpRequest,
    PasswordChangedResponse,
    PasswordResetRequest,
    PasswordResetStartedResponse,
    ResetTokenResponse,
)
from src.application.interfaces.i_rate_limiter import IRateLimiter
from src.application.use_cases.password_reset_use_cases import (
    CompletePasswordReset,
    RequestPasswordReset,
    ResendPasswordResetCode,
    VerifyPasswordResetCode,
)
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import RateLimited
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.infrastructure.database.repositories.audit_log_repository import SqlAuthEventLog
from src.infrastructure.database.repositories.staff_account_repository import (
    SqlStaffAccountRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import SessionDep


async def _limit_per_client_ip(request: Request) -> None:
    limiter: IRateLimiter = request.app.state.password_reset_rate_limiter
    retry_after = limiter.hit(request.client.host if request.client else "unknown")
    if retry_after is not None:
        raise RateLimited(
            "Too many password-reset requests from this network. Please try again later.",
            code="TOO_MANY_REQUESTS",
            retry_after=retry_after,
        )


router = APIRouter(prefix="/auth/password-reset", tags=["Password reset"], dependencies=[Depends(_limit_per_client_ip)])


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PasswordResetStartedResponse)
async def request_reset(
    body: PasswordResetRequest, request: Request, session: SessionDep
) -> PasswordResetStartedResponse:
    settings: Settings = request.app.state.settings
    use_case = RequestPasswordReset(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.password_reset_expire_minutes * 60,
    )
    return PasswordResetStartedResponse.from_result(
        await use_case.execute(
            body.organization_id,
            body.email,
            supervisor_id=body.supervisor_id,
            guard_id=body.guard_id,
            operational_mode=body.operational_mode,
        )
    )


@router.post(
    "/{reset_id}/resend-otp", status_code=status.HTTP_202_ACCEPTED, response_model=PasswordResetStartedResponse
)
async def resend_reset_code(reset_id: UUID, request: Request, session: SessionDep) -> PasswordResetStartedResponse:
    use_case = ResendPasswordResetCode(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(request.app.state.settings),
    )
    return PasswordResetStartedResponse.from_result(await use_case.execute(reset_id))


@router.post("/{reset_id}/verify", response_model=ResetTokenResponse)
async def verify_reset_code(
    reset_id: UUID, body: OtpRequest, request: Request, session: SessionDep
) -> ResetTokenResponse:
    settings: Settings = request.app.state.settings
    use_case = VerifyPasswordResetCode(
        SqlStaffAccountRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        token_ttl_seconds=settings.password_reset_token_minutes * 60,
    )
    token = await use_case.execute(reset_id, body.otp)
    return ResetTokenResponse(reset_token=token.token, expires_in=token.expires_in_seconds)


@router.post("/{reset_id}/complete", response_model=PasswordChangedResponse)
async def complete_reset(
    reset_id: UUID, body: CompletePasswordResetRequest, request: Request, session: SessionDep
) -> PasswordChangedResponse:
    use_case = CompletePasswordReset(
        SqlStaffAccountRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        SqlTransaction(session),
        SqlAuthEventLog(session),
    )
    await use_case.execute(reset_id, body.reset_token, body.new_password, body.confirm_new_password)
    return PasswordChangedResponse()
