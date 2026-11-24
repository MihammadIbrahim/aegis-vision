"""Forgot password for Organization Admins and Supervisors (requirements Section 9.6).

1. Request: Organization ID + registered email. The answer is the same whether or not an
   account matches: a request is stored and a code issued either way, but it is emailed only
   to a real, active Admin or Supervisor.
2. Verify the code: returns a one-time reset token (stored only as a keyed hash).
3. Set the new password with that token. Every session issued before the change stops
   working, and pending sign-ins are cancelled.

No step needs Admin approval.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import EmailDeliveryError, IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.otp_flow import (
    already,
    code_email,
    code_not_verified,
    counts_attempt,
    email_not_configured,
    gone,
    issue_refused,
    missing,
)
from src.domain.exceptions.domain_errors import (
    DomainError,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_audit_log_repo import IAuthEventLog
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    IStaffAccountRepository,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import check_password

PURPOSE = "PASSWORD_RESET"
_MAX_FIELD = 320


@dataclass(frozen=True, slots=True)
class ResetStarted:
    request_id: UUID
    code_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ResetToken:
    token: str
    expires_in_seconds: int


def _errors() -> dict[str, DomainError]:
    return {
        "not_found": missing("Password reset not found. Please start again.", "RESET_NOT_FOUND"),
        "expired": gone("This password reset has expired. Please start again.", "RESET_EXPIRED"),
        "done": already("This code has already been used.", "RESET_ALREADY_VERIFIED"),
    }


class _ResetCodes:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        limits: OtpLimits,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._limits = limits

    async def issue(self, request_id: UUID) -> ResetStarted:
        code = self._otp.new_code()
        result = await self._repository.issue_reset_otp(
            request_id, uuid4(), self._otp.hash_purpose_code(PURPOSE, request_id, code), self._limits
        )
        if result.outcome is not FlowOutcome.ISSUED or result.expires_at is None:
            await self._transaction.rollback()
            raise issue_refused(result, **_errors())
        if result.email is not None:
            message = code_email(
                result.email,
                code,
                self._limits.ttl_seconds,
                subject="Reset your password",
                purpose="Enter this code to reset your password.",
            )
            try:
                await self._sender.send(message)
            except EmailDeliveryError:
                # Not reported to the caller: a different answer here would reveal that the account
                # exists. The code was not delivered, so the person simply asks for a new one.
                pass
        return ResetStarted(
            request_id=request_id,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=result.expires_at,
        )


class RequestPasswordReset:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._ttl = ttl_seconds
        self._codes = _ResetCodes(repository, otp, sender, transaction, limits)

    async def execute(
        self,
        organization_id: str | None,
        email: str,
        supervisor_id: str | None = None,
        guard_id: str | None = None,
        operational_mode: str | None = None,
    ) -> ResetStarted:
        """Exactly one identifier: the Organization ID (Admins and Supervisors), the Supervisor ID
        (Supervisors only) or the Guard ID (Guards only). Guards also choose their operational mode;
        a mode that does not match the assignment is treated like non-matching details: the same answer,
        no code (decision D-018), so the answer never reveals the account or its assignment."""
        if not self._sender.is_configured:
            raise email_not_configured()
        given = [value for value in (organization_id, supervisor_id, guard_id) if value is not None]
        if len(given) != 1:
            raise ValidationFailed("Enter exactly one ID with the email address.", code="VALIDATION_FAILED")
        identifier = given[0].strip()
        email = email.strip()
        if not identifier or not email or len(identifier) > _MAX_FIELD or len(email) > _MAX_FIELD:
            raise ValidationFailed("Enter the ID and the email address.", code="VALIDATION_FAILED")
        request_id = uuid4()
        if guard_id is not None:
            await self._repository.request_password_reset_by_guard_id(
                request_id,
                identifier,
                email,
                self._otp.recipient_digest(f"password-reset-guard:{identifier}:{email}"),
                ttl_seconds=self._ttl,
                stationed=operational_mode == "STATIONED",
            )
        elif supervisor_id is not None:
            await self._repository.request_password_reset_by_supervisor_id(
                request_id,
                identifier,
                email,
                # Limits apply to what was typed, so they behave the same for unknown accounts.
                self._otp.recipient_digest(f"password-reset-supervisor:{identifier}:{email}"),
                ttl_seconds=self._ttl,
            )
        else:
            await self._repository.request_password_reset(
                request_id,
                identifier,
                email,
                self._otp.recipient_digest(f"password-reset:{identifier}:{email}"),
                ttl_seconds=self._ttl,
            )
        started = await self._codes.issue(request_id)
        await self._transaction.commit()
        return started


class ResendPasswordResetCode:
    def __init__(self, repository, otp, sender, transaction, *, limits: OtpLimits) -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._codes = _ResetCodes(repository, otp, sender, transaction, limits)

    async def execute(self, request_id: UUID) -> ResetStarted:
        if not self._sender.is_configured:
            raise email_not_configured()
        started = await self._codes.issue(request_id)
        await self._transaction.commit()
        return started


class VerifyPasswordResetCode:
    def __init__(
        self, repository: IStaffAccountRepository, otp: IOtpCodec, transaction: ITransaction, *, token_ttl_seconds: int
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._token_ttl = token_ttl_seconds

    async def execute(self, request_id: UUID, code: str) -> ResetToken:
        if not (len(code) == 6 and code.isascii() and code.isdigit()):
            raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
        token = secrets.token_urlsafe(32)
        result = await self._repository.verify_reset_otp(
            request_id,
            self._otp.hash_purpose_code(PURPOSE, request_id, code),
            self._otp.hash_token(PURPOSE, token),
            token_ttl_seconds=self._token_ttl,
        )
        if result.outcome is FlowOutcome.VERIFIED:
            await self._transaction.commit()
            return ResetToken(token=token, expires_in_seconds=self._token_ttl)
        await (self._transaction.commit() if counts_attempt(result) else self._transaction.rollback())
        raise code_not_verified(result, **_errors())


class CompletePasswordReset:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        transaction: ITransaction,
        auth_events: IAuthEventLog | None = None,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._transaction = transaction
        self._events = auth_events

    async def execute(self, request_id: UUID, reset_token: str, new_password: str, confirm_new_password: str) -> None:
        if new_password != confirm_new_password:
            raise ValidationFailed("The new password and its confirmation do not match.", code="PASSWORD_MISMATCH")
        token_hash = self._otp.hash_token(PURPOSE, reset_token)
        identity = await self._repository.reset_identity(request_id, token_hash)
        if identity.outcome is not FlowOutcome.OK:
            await self._transaction.rollback()
            raise _invalid_token()
        # Accounts that can reset always have a username; the fallback can never match a password.
        check_password(new_password, username=identity.username or "\u0000", email=identity.email or "")
        outcome = await self._repository.complete_reset(request_id, token_hash, self._hasher.hash(new_password))
        if outcome is not FlowOutcome.COMPLETED:
            await self._transaction.rollback()
            raise _invalid_token()
        if self._events is not None:
            await self._events.reset_completed(request_id)  # AUTH_PASSWORD_RESET_COMPLETED (decision D-024)
        await self._transaction.commit()


def _invalid_token() -> VerificationFailed:
    return VerificationFailed(
        "This reset link is invalid or has expired. Please start again.", code="RESET_TOKEN_INVALID"
    )
