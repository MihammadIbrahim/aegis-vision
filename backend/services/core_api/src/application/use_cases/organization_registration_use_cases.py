"""First Organization Admin registration (requirements Section 8 and decisions A–F of
docs/requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).

1. Submit: validate the form, reserve an Organization ID, store a pending registration,
   and email a one-time code. Nothing else exists yet.
2. Resend: email a new code (the old one stops working), within the cooldown and the
   hourly limit.
3. Verify: check the code. If it is right, create the organization, its first campus (named
   by the applicant, decision D-021) and the Main Admin in the same transaction, then return the
   Organization ID and the Main Admin ID.

Registration never signs anyone in; login is a separate step.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import (
    EmailDeliveryError,
    EmailMessage,
    IEmailSender,
)
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.domain.entities.organization import CompletedRegistration, PendingRegistration
from src.domain.exceptions.domain_errors import (
    Conflict,
    DomainError,
    Gone,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_organization_registration_repo import (
    CompleteOutcome,
    IOrganizationRegistrationRepository,
    IssueOutcome,
    IssueResult,
    OtpLimits,
    SubmitOutcome,
    VerifyOutcome,
    VerifyResult,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import validate_registration
from src.domain.value_objects.organization_code import generate_organization_code
from src.domain.value_objects.system_id import generate_staff_id

# Random IDs collide almost never; a few retries make that "never" in practice.
_UNIQUE_ID_ATTEMPTS = 5
_OTP_FORMAT = re.compile(r"[0-9]{6}")


@dataclass(frozen=True, slots=True)
class RegistrationForm:
    organization_name: str
    campus_name: str
    admin_full_name: str
    username: str
    email: str
    phone: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CodeSent:
    registration_id: UUID
    masked_email: str
    delivery_channel: str
    code_expires_in_seconds: int
    resend_available_in_seconds: int
    registration_expires_at: datetime


def mask_email(email: str) -> str:
    """``ayesha@example.com`` -> ``ay***@example.com``."""
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def registration_code_email(to_email: str, code: str, expires_in_seconds: int) -> EmailMessage:
    """Fixed wording only: nothing typed into the form (such as the organization name)
    appears in the email, so the form cannot be used to send arbitrary text to anyone."""
    minutes = max(1, expires_in_seconds // 60)
    expiry = f"{minutes} minute{'s' if minutes != 1 else ''}"
    text = (
        f"Your verification code is: {code}\n\n"
        f"Enter this code to finish registering your organization. It expires in {expiry} "
        "and can be used only once.\n\n"
        "If you did not try to register an organization, you can ignore this email. "
        "Nothing is created without this code.\n"
    )
    body = (
        f"<p>Your verification code is: <strong>{html.escape(code)}</strong></p>"
        f"<p>Enter this code to finish registering your organization. It expires in {expiry} "
        "and can be used only once.</p>"
        "<p>If you did not try to register an organization, you can ignore this email. "
        "Nothing is created without this code.</p>"
    )
    return EmailMessage(
        to_email=to_email, subject="Your organization registration code", text_body=text, html_body=body
    )


# --- Errors shared by the steps -------------------------------------------------------


def _email_not_configured() -> ServiceUnavailable:
    return ServiceUnavailable(
        "Email delivery is not configured on this server, so no verification code can be sent.",
        code="EMAIL_DELIVERY_NOT_CONFIGURED",
    )


def _not_found() -> NotFound:
    return NotFound("Registration not found. Please start a new registration.", code="REGISTRATION_NOT_FOUND")


def _expired() -> Gone:
    return Gone("This registration has expired. Please start a new registration.", code="REGISTRATION_EXPIRED")


def _already_completed() -> Conflict:
    return Conflict(
        "This registration is already complete. Its code cannot be used again.",
        code="REGISTRATION_ALREADY_COMPLETED",
    )


def _name_taken() -> Conflict:
    return Conflict(
        "An organization with this name is already registered (names are compared ignoring capitalization).",
        code="ORGANIZATION_NAME_TAKEN",
    )


def _username_taken() -> Conflict:
    return Conflict("This username is already taken.", code="USERNAME_TAKEN")


class _CodeIssuer:
    """Issue a new code and email it. Used by both submit and resend."""

    def __init__(
        self,
        repository: IOrganizationRegistrationRepository,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        limits: OtpLimits,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._email = email
        self._transaction = transaction
        self._limits = limits

    async def issue_and_send(self, registration_id: UUID) -> CodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_otp(
            registration_id, uuid4(), self._otp.hash_code(registration_id, code), self._limits
        )
        if result.outcome is not IssueOutcome.ISSUED:
            raise await self._refused(result)
        if result.email is None or result.registration_expires_at is None:
            raise RuntimeError("issue_otp returned ISSUED without a recipient")
        try:
            await self._email.send(registration_code_email(result.email, code, self._limits.ttl_seconds))
        except EmailDeliveryError:
            # Nothing is kept: the code was never delivered, so it must not count or work.
            await self._transaction.rollback()
            raise UpstreamServiceFailed(
                "The verification email could not be sent. Please try again in a few minutes.",
                code="EMAIL_DELIVERY_FAILED",
            ) from None
        return CodeSent(
            registration_id=registration_id,
            masked_email=mask_email(result.email),
            delivery_channel=self._email.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            registration_expires_at=result.registration_expires_at,
        )

    async def _refused(self, result: IssueResult) -> DomainError:
        if result.outcome is IssueOutcome.REGISTRATION_EXPIRED:
            await self._transaction.commit()  # keep the erasure of the applicant's details
            return _expired()
        await self._transaction.rollback()
        if result.outcome is IssueOutcome.COOLDOWN:
            return RateLimited(
                "Please wait before requesting another code.",
                code="OTP_RESEND_COOLDOWN",
                retry_after=result.retry_after_seconds,
            )
        if result.outcome is IssueOutcome.HOURLY_LIMIT:
            return RateLimited(
                "Too many codes have been sent to this email address in the last hour. Please try again later.",
                code="OTP_HOURLY_LIMIT",
                retry_after=result.retry_after_seconds,
            )
        if result.outcome is IssueOutcome.ALREADY_COMPLETED:
            return _already_completed()
        return _not_found()


class SubmitOrganizationRegistration:
    def __init__(
        self,
        repository: IOrganizationRegistrationRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        registration_ttl_seconds: int,
        organization_code_factory: Callable[[], str] = generate_organization_code,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._email = email
        self._transaction = transaction
        self._ttl_seconds = registration_ttl_seconds
        self._new_code = organization_code_factory
        self._issuer = _CodeIssuer(repository, otp, email, transaction, limits)

    async def execute(self, form: RegistrationForm) -> CodeSent:
        applicant = validate_registration(
            organization_name=form.organization_name,
            campus_name=form.campus_name,
            admin_full_name=form.admin_full_name,
            username=form.username,
            email=form.email,
            phone=form.phone,
            password=form.password,
        )
        if not self._email.is_configured:
            raise _email_not_configured()

        registration_id = uuid4()
        password_hash = self._hasher.hash(applicant.password)
        recipient_digest = self._otp.recipient_digest(applicant.email)
        outcome = SubmitOutcome.ORGANIZATION_CODE_TAKEN
        for _ in range(_UNIQUE_ID_ATTEMPTS):
            outcome = await self._repository.submit(
                PendingRegistration(
                    id=registration_id,
                    organization_code=self._new_code(),
                    organization_name=applicant.organization_name,
                    campus_name=applicant.campus_name,
                    admin_full_name=applicant.admin_full_name,
                    username=applicant.username,
                    email=applicant.email,
                    phone=applicant.phone,
                    password_hash=password_hash,
                    recipient_digest=recipient_digest,
                    ttl_seconds=self._ttl_seconds,
                )
            )
            if outcome is not SubmitOutcome.ORGANIZATION_CODE_TAKEN:
                break
        if outcome is SubmitOutcome.ORGANIZATION_NAME_TAKEN:
            raise _name_taken()
        if outcome is SubmitOutcome.USERNAME_TAKEN:
            raise _username_taken()
        if outcome is not SubmitOutcome.CREATED:
            raise RuntimeError("Could not reserve a unique Organization ID.")

        sent = await self._issuer.issue_and_send(registration_id)
        await self._transaction.commit()
        return sent


class ResendRegistrationCode:
    def __init__(
        self,
        repository: IOrganizationRegistrationRepository,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
    ) -> None:
        self._email = email
        self._transaction = transaction
        self._issuer = _CodeIssuer(repository, otp, email, transaction, limits)

    async def execute(self, registration_id: UUID) -> CodeSent:
        if not self._email.is_configured:
            raise _email_not_configured()
        sent = await self._issuer.issue_and_send(registration_id)
        await self._transaction.commit()
        return sent


class VerifyOrganizationRegistration:
    def __init__(
        self,
        repository: IOrganizationRegistrationRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        *,
        display_id_factory: Callable[[], str] = generate_staff_id,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._new_display_id = display_id_factory

    async def execute(self, registration_id: UUID, code: str) -> CompletedRegistration:
        if not _OTP_FORMAT.fullmatch(code):
            raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
        verification = await self._repository.verify_otp(registration_id, self._otp.hash_code(registration_id, code))
        if verification.outcome is not VerifyOutcome.VERIFIED:
            raise await self._not_verified(verification)

        organization_id, campus_id, user_id = uuid4(), uuid4(), uuid4()
        for _ in range(_UNIQUE_ID_ATTEMPTS):
            completion = await self._repository.complete(
                registration_id,
                organization_id=organization_id,
                campus_id=campus_id,
                user_id=user_id,
                display_id=self._new_display_id(),
            )
            if completion.outcome is not CompleteOutcome.DISPLAY_ID_TAKEN:
                break
        if completion.outcome is CompleteOutcome.COMPLETED and completion.registration is not None:
            await self._transaction.commit()
            return completion.registration

        # Undo everything, including the use of the code: the registration stays as it was.
        await self._transaction.rollback()
        raise _completion_failed(completion.outcome)

    async def _not_verified(self, result: VerifyResult) -> DomainError:
        outcome = result.outcome
        if outcome in (VerifyOutcome.INVALID_CODE, VerifyOutcome.ATTEMPTS_EXCEEDED, VerifyOutcome.REGISTRATION_EXPIRED):
            await self._transaction.commit()  # keep the counted attempt, or the erasure
        else:
            await self._transaction.rollback()
        if outcome is VerifyOutcome.INVALID_CODE:
            left = result.attempts_remaining or 0
            return VerificationFailed(
                f"The code is incorrect. {left} attempt{'s' if left != 1 else ''} left.", code="OTP_INVALID"
            )
        if outcome is VerifyOutcome.ATTEMPTS_EXCEEDED:
            return RateLimited("Too many incorrect codes. Request a new code.", code="OTP_ATTEMPTS_EXCEEDED")
        if outcome is VerifyOutcome.CODE_EXPIRED:
            return VerificationFailed("This code has expired. Request a new code.", code="OTP_EXPIRED")
        if outcome is VerifyOutcome.NO_ACTIVE_CODE:
            return VerificationFailed(
                "No code is active for this registration. Request a new code.", code="OTP_NOT_ISSUED"
            )
        if outcome is VerifyOutcome.REGISTRATION_EXPIRED:
            return _expired()
        if outcome is VerifyOutcome.ALREADY_COMPLETED:
            return _already_completed()
        return _not_found()


def _completion_failed(outcome: CompleteOutcome) -> DomainError:
    if outcome is CompleteOutcome.ORGANIZATION_NAME_TAKEN:
        return Conflict(
            "Another organization registered this name first (names are compared ignoring capitalization). "
            "Nothing was created. Please start a new registration with a different name.",
            code="ORGANIZATION_NAME_TAKEN",
        )
    if outcome is CompleteOutcome.USERNAME_TAKEN:
        return Conflict(
            "This username was taken by another account first. Nothing was created. "
            "Please start a new registration with a different username.",
            code="USERNAME_TAKEN",
        )
    if outcome is CompleteOutcome.EMAIL_ALREADY_REGISTERED:
        return Conflict(
            "This email address already belongs to an account. Nothing was created. "
            "Please register with a different email address.",
            code="EMAIL_ALREADY_REGISTERED",
        )
    if outcome is CompleteOutcome.REGISTRATION_EXPIRED:
        return _expired()
    if outcome is CompleteOutcome.ALREADY_COMPLETED:
        return _already_completed()
    if outcome is CompleteOutcome.REGISTRATION_NOT_FOUND:
        return _not_found()
    return Conflict(
        "The registration could not be completed. Nothing was created. Please try again.",
        code="REGISTRATION_CONFLICT",
    )
