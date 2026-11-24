"""First Organization Admin registration (public: no sign-in).

1. POST /auth/organization-registrations                         submit the form; a code is emailed
2. POST /auth/organization-registrations/{id}/resend-otp         email a new code
3. POST /auth/organization-registrations/{id}/verify             check the code; creates the organization

These routes use the tenant database role with no signed-in user, so Row-Level
Security shows them nothing. They reach the registration tables only through the
narrow database functions of migration 0004. No route here issues a token: login is
separate.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from src.application.dtos.organization_registration_dto import (
    OrganizationRegistrationRequest,
    RegistrationCompletedResponse,
    VerificationCodeSentResponse,
    VerifyRegistrationRequest,
)
from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_rate_limiter import IRateLimiter
from src.application.use_cases.organization_registration_use_cases import (
    RegistrationForm,
    ResendRegistrationCode,
    SubmitOrganizationRegistration,
    VerifyOrganizationRegistration,
)
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import RateLimited
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.infrastructure.database.repositories.organization_registration_repository import (
    SqlOrganizationRegistrationRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import SessionDep


async def _limit_per_client_ip(request: Request) -> None:
    limiter: IRateLimiter = request.app.state.registration_rate_limiter
    retry_after = limiter.hit(request.client.host if request.client else "unknown")
    if retry_after is not None:
        raise RateLimited(
            "Too many registration requests from this network. Please try again later.",
            code="TOO_MANY_REQUESTS",
            retry_after=retry_after,
        )


router = APIRouter(
    prefix="/auth/organization-registrations",
    tags=["Organization registration"],
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


@router.post("", status_code=status.HTTP_201_CREATED, response_model=VerificationCodeSentResponse)
async def submit_registration(
    body: OrganizationRegistrationRequest, request: Request, session: SessionDep
) -> VerificationCodeSentResponse:
    """Submit the first Admin's registration. Creates only a pending registration and emails
    a 6-digit code. No organization or account exists until the code is verified."""
    settings = _settings(request)
    use_case = SubmitOrganizationRegistration(
        SqlOrganizationRegistrationRepository(session),
        Argon2PasswordHasher(),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(settings),
        registration_ttl_seconds=settings.org_registration_expire_minutes * 60,
    )
    sent = await use_case.execute(
        RegistrationForm(
            organization_name=body.organization_name,
            campus_name=body.campus_name,
            admin_full_name=body.admin_full_name,
            username=body.username,
            email=body.email,
            phone=body.phone_number,
            password=body.password,
        )
    )
    return VerificationCodeSentResponse.from_result(sent)


@router.post("/{registration_id}/resend-otp", response_model=VerificationCodeSentResponse)
async def resend_code(registration_id: UUID, request: Request, session: SessionDep) -> VerificationCodeSentResponse:
    """Email a new code. The previous code stops working. Limited by a cooldown and by a
    maximum number of codes per email address per hour."""
    use_case = ResendRegistrationCode(
        SqlOrganizationRegistrationRepository(session),
        _otp(request),
        _email(request),
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return VerificationCodeSentResponse.from_result(await use_case.execute(registration_id))


@router.post("/{registration_id}/verify", response_model=RegistrationCompletedResponse)
async def verify_registration(
    registration_id: UUID, body: VerifyRegistrationRequest, request: Request, session: SessionDep
) -> RegistrationCompletedResponse:
    """Check the code. If it is right, create the organization (with its permanent
    Organization ID), its first campus (the name given in the form) and the Main Admin, all
    together, and return them, including the Organization ID and the Main Admin ID."""
    use_case = VerifyOrganizationRegistration(
        SqlOrganizationRegistrationRepository(session), _otp(request), SqlTransaction(session)
    )
    return RegistrationCompletedResponse.from_entity(await use_case.execute(registration_id, body.otp))
