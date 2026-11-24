"""Shared pieces of the one-time-code flows of migration 0007: the email, the errors, and
sending a code without ever claiming a delivery that did not happen."""

import html
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.interfaces.i_email_service import (
    EmailDeliveryError,
    EmailMessage,
    IEmailSender,
)
from src.domain.exceptions.domain_errors import (
    Conflict,
    DomainError,
    Gone,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    VerificationFailed,
)
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    IssueResult,
    VerifyResult,
)
from src.domain.repositories.i_unit_of_work import ITransaction

# A real Argon2id hash of a random value, checked when there is no account, so that answer
# takes as long as a real password check.
TIMING_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$LgmK9ajp4hVuEECnmlELqA$3Ow5Y0vPv42OYedhueQ7MCCh1C2JdRJn13qG/0HadeE"


@dataclass(frozen=True, slots=True)
class CodeSent:
    subject_id: UUID
    masked_email: str
    delivery_channel: str
    code_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime


def code_email(to_email: str, code: str, expires_in_seconds: int, *, subject: str, purpose: str) -> EmailMessage:
    """Fixed wording plus the code. ``purpose`` is fixed text chosen by the server, never user input."""
    minutes = max(1, expires_in_seconds // 60)
    expiry = f"{minutes} minute{'s' if minutes != 1 else ''}"
    text = (
        f"Your verification code is: {code}\n\n"
        f"{purpose} It expires in {expiry} and can be used only once.\n\n"
        "If you did not ask for this, you can ignore this email. Never share this code.\n"
    )
    body = (
        f"<p>Your verification code is: <strong>{html.escape(code)}</strong></p>"
        f"<p>{html.escape(purpose)} It expires in {expiry} and can be used only once.</p>"
        "<p>If you did not ask for this, you can ignore this email. Never share this code.</p>"
    )
    return EmailMessage(to_email=to_email, subject=subject, text_body=text, html_body=body)


def email_not_configured() -> ServiceUnavailable:
    return ServiceUnavailable(
        "Email delivery is not configured on this server, so no verification code can be sent.",
        code="EMAIL_DELIVERY_NOT_CONFIGURED",
    )


async def send_or_roll_back(sender: IEmailSender, message: EmailMessage, transaction: ITransaction) -> None:
    try:
        await sender.send(message)
    except EmailDeliveryError:
        await transaction.rollback()  # the code was never delivered: keep nothing
        raise UpstreamServiceFailed(
            "The verification email could not be sent. Please try again in a few minutes.",
            code="EMAIL_DELIVERY_FAILED",
        ) from None


def issue_refused(
    result: IssueResult, *, not_found: DomainError, expired: DomainError, done: DomainError
) -> DomainError:
    if result.outcome is FlowOutcome.COOLDOWN:
        return RateLimited(
            "Please wait before requesting another code.",
            code="OTP_RESEND_COOLDOWN",
            retry_after=result.retry_after_seconds,
        )
    if result.outcome is FlowOutcome.HOURLY_LIMIT:
        return RateLimited(
            "Too many codes have been requested in the last hour. Please try again later.",
            code="OTP_HOURLY_LIMIT",
            retry_after=result.retry_after_seconds,
        )
    if result.outcome is FlowOutcome.EXPIRED:
        return expired
    if result.outcome in (FlowOutcome.ALREADY_VERIFIED, FlowOutcome.ALREADY_COMPLETED):
        return done
    return not_found


def counts_attempt(result: VerifyResult) -> bool:
    """Wrong codes are counted, so they must be committed even though the request fails."""
    return result.outcome in (FlowOutcome.INVALID_CODE, FlowOutcome.ATTEMPTS_EXCEEDED)


def code_not_verified(
    result: VerifyResult, *, not_found: DomainError, expired: DomainError, done: DomainError
) -> DomainError:
    if result.outcome is FlowOutcome.INVALID_CODE:
        left = result.attempts_remaining or 0
        return VerificationFailed(
            f"The code is incorrect. {left} attempt{'s' if left != 1 else ''} left.", code="OTP_INVALID"
        )
    if result.outcome is FlowOutcome.ATTEMPTS_EXCEEDED:
        return RateLimited("Too many incorrect codes. Request a new code.", code="OTP_ATTEMPTS_EXCEEDED")
    if result.outcome is FlowOutcome.CODE_EXPIRED:
        return VerificationFailed("This code has expired. Request a new code.", code="OTP_EXPIRED")
    if result.outcome is FlowOutcome.NO_ACTIVE_CODE:
        return VerificationFailed("No code is active. Request a new code.", code="OTP_NOT_ISSUED")
    if result.outcome is FlowOutcome.EXPIRED:
        return expired
    if result.outcome in (FlowOutcome.ALREADY_VERIFIED, FlowOutcome.ALREADY_COMPLETED):
        return done
    return not_found


def gone(message: str, code: str) -> Gone:
    return Gone(message, code=code)


def missing(message: str, code: str) -> NotFound:
    return NotFound(message, code=code)


def already(message: str, code: str) -> Conflict:
    return Conflict(message, code=code)
