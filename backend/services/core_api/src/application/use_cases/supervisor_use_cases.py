"""Supervisor creation by an Organization Admin (and, with ``GUARD_KIND``, Guard creation by an
Admin; Supervisors could too until migration 0018, decision D-017) (requirements Section 9.2, as clarified on
2026-10-27).

1. Step-up: the Admin re-enters their current password, then the code emailed to the
   Admin. That gives a short-lived, single-use permission to submit one Supervisor form.
2. Form: Name, Username, Email, Phone Number, Password. The username is checked now; a
   code is emailed to the Supervisor's address.
3. The Supervisor's code is verified. Then the Supervisor account is created, all at once,
   in the Admin's own organization and campus, with a new 27-character Supervisor ID.

The database functions act only for the signed-in caller, so an Admin cannot use another
Admin's permission or form, or create a Supervisor in another organization or campus.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.organization_registration_use_cases import mask_email
from src.application.use_cases.otp_flow import (
    TIMING_DUMMY_HASH,
    CodeSent,
    already,
    code_email,
    code_not_verified,
    counts_attempt,
    email_not_configured,
    gone,
    issue_refused,
    missing,
    send_or_roll_back,
)
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    Conflict,
    CurrentPasswordIncorrect,
    DomainError,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    CreatedSupervisor,
    FlowOutcome,
    IssueResult,
    IStaffAccountRepository,
    SupervisorForm,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import (
    check_email,
    check_full_name,
    check_password,
    check_phone,
    check_username,
)
from src.domain.value_objects.system_id import generate_supervisor_id

STEP_UP = "ADMIN_STEP_UP"
SUPERVISOR_EMAIL = "SUPERVISOR_EMAIL"


@dataclass(frozen=True, slots=True)
class AccountKind:
    """What is being created. The same flow creates Supervisors and Guards (both by Admins only,
    decision D-017); only wording, error codes and the code-hash labels differ."""

    label: str  # "Supervisor"
    code: str  # prefix of error codes, e.g. "SUPERVISOR"
    creator: str  # who creates it, in messages
    step_up_purpose: str
    email_purpose: str
    step_up_reason: str | None = None  # the email's "before ..." wording, when not "adding a <label>"


SUPERVISOR_KIND = AccountKind("Supervisor", "SUPERVISOR", "Admin", STEP_UP, SUPERVISOR_EMAIL)
GUARD_KIND = AccountKind("Guard", "GUARD", "Admin", "GUARD_STEP_UP", "GUARD_EMAIL")
_UNIQUE_ID_ATTEMPTS = 5


def _step_up_errors() -> dict[str, DomainError]:
    return {
        "not_found": missing("Confirmation not found. Please confirm your password again.", "AUTHORIZATION_NOT_FOUND"),
        "expired": gone("This confirmation has expired. Please confirm your password again.", "AUTHORIZATION_EXPIRED"),
        "done": already("This confirmation is already complete.", "AUTHORIZATION_ALREADY_VERIFIED"),
    }


def _creation_errors(kind: AccountKind = SUPERVISOR_KIND) -> dict[str, DomainError]:
    return {
        "not_found": missing(f"{kind.label} form not found.", f"{kind.code}_CREATION_NOT_FOUND"),
        "expired": gone(f"This {kind.label} form has expired. Please start again.", f"{kind.code}_CREATION_EXPIRED"),
        "done": already(f"This {kind.label} has already been created.", f"{kind.code}_ALREADY_CREATED"),
    }


def _sent(subject_id: UUID, result: IssueResult, sender: IEmailSender, limits: OtpLimits) -> CodeSent:
    assert result.email is not None and result.expires_at is not None
    return CodeSent(
        subject_id=subject_id,
        masked_email=mask_email(result.email),
        delivery_channel=sender.delivery_channel,
        code_expires_in_seconds=limits.ttl_seconds,
        resend_available_in_seconds=limits.resend_cooldown_seconds,
        expires_at=result.expires_at,
    )


class _Codes:
    """Issue and email a code for the step-up or for the Supervisor's email."""

    def __init__(
        self,
        repository: IStaffAccountRepository,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        limits: OtpLimits,
        kind: AccountKind = SUPERVISOR_KIND,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._limits = limits
        self._kind = kind

    async def step_up(self, grant_id: UUID) -> CodeSent:
        reason = self._kind.step_up_reason or f"before adding a {self._kind.label}"
        code = self._otp.new_code()
        result = await self._repository.issue_step_up_otp(
            grant_id, uuid4(), self._otp.hash_purpose_code(self._kind.step_up_purpose, grant_id, code), self._limits
        )
        return await self._deliver(
            grant_id,
            code,
            result,
            _step_up_errors(),
            subject="Confirm it's you",
            purpose=f"Enter this code to confirm it's you {reason}.",
        )

    async def supervisor(self, creation_id: UUID) -> CodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_supervisor_otp(
            creation_id, uuid4(), self._otp.hash_purpose_code(self._kind.email_purpose, creation_id, code), self._limits
        )
        return await self._deliver(
            creation_id,
            code,
            result,
            _creation_errors(self._kind),
            subject=f"Verify your {self._kind.label} email address",
            purpose=f"Your organization's {self._kind.creator} is creating a {self._kind.label} account for this "
            f"email address. Give this code to the {self._kind.creator} to confirm the address.",
        )

    async def _deliver(
        self,
        subject_id: UUID,
        code: str,
        result: IssueResult,
        errors: dict[str, DomainError],
        *,
        subject: str,
        purpose: str,
    ) -> CodeSent:
        if result.outcome is not FlowOutcome.ISSUED or result.email is None:
            await self._transaction.rollback()
            raise issue_refused(result, **errors)
        message = code_email(result.email, code, self._limits.ttl_seconds, subject=subject, purpose=purpose)
        await send_or_roll_back(self._sender, message, self._transaction)
        return _sent(subject_id, result, self._sender, self._limits)


class StartSupervisorAuthorization:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        ttl_seconds: int,
        max_failed_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        kind: AccountKind = SUPERVISOR_KIND,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._sender = sender
        self._transaction = transaction
        self._ttl = ttl_seconds
        self._max_failed = max_failed_attempts
        self._lockout = lockout_seconds
        self._clock = clock
        self._codes = _Codes(repository, otp, sender, transaction, limits, kind)

    async def execute(self, current_password: str) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        account = await self._repository.step_up_account()
        if account is None:  # e.g. an Admin account that has no password
            self._hasher.verify(TIMING_DUMMY_HASH, current_password)
            raise _wrong_password()
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, current_password):
            locked_until = await self._repository.record_password_failure(
                account.user_id, max_attempts=self._max_failed, window_seconds=self._lockout
            )
            await self._transaction.commit()  # wrong passwords count toward the same lock as sign-in
            raise _locked(self._lockout) if locked_until is not None else _wrong_password()
        grant_id = uuid4()
        await self._repository.start_step_up(grant_id, ttl_seconds=self._ttl)
        sent = await self._codes.step_up(grant_id)
        await self._transaction.commit()
        return sent


def _wrong_password() -> CurrentPasswordIncorrect:
    return CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many incorrect passwords. Please try again later.", code="ACCOUNT_LOCKED", retry_after=max(1, retry_after)
    )


class ResendSupervisorAuthorizationCode:
    def __init__(
        self, repository, otp, sender, transaction, *, limits: OtpLimits, kind: AccountKind = SUPERVISOR_KIND
    ) -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._codes = _Codes(repository, otp, sender, transaction, limits, kind)

    async def execute(self, grant_id: UUID) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        sent = await self._codes.step_up(grant_id)
        await self._transaction.commit()
        return sent


class VerifySupervisorAuthorization:
    """Returns until when the Supervisor form may be submitted."""

    def __init__(
        self,
        repository: IStaffAccountRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        *,
        form_ttl_seconds: int,
        kind: AccountKind = SUPERVISOR_KIND,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._form_ttl = form_ttl_seconds
        self._kind = kind

    async def execute(self, grant_id: UUID, code: str) -> datetime:
        _ensure_code_format(code)
        result = await self._repository.verify_step_up_otp(
            grant_id,
            self._otp.hash_purpose_code(self._kind.step_up_purpose, grant_id, code),
            form_ttl_seconds=self._form_ttl,
        )
        if result.outcome is FlowOutcome.VERIFIED and result.expires_at is not None:
            await self._transaction.commit()
            return result.expires_at
        await (self._transaction.commit() if counts_attempt(result) else self._transaction.rollback())
        raise code_not_verified(result, **_step_up_errors())


class SubmitSupervisor:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        ttl_seconds: int,
        kind: AccountKind = SUPERVISOR_KIND,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._ttl = ttl_seconds
        self._kind = kind
        self._codes = _Codes(repository, otp, sender, transaction, limits, kind)

    async def execute(
        self, *, authorization_id: UUID, full_name: str, username: str, email: str, phone: str, password: str
    ) -> CodeSent:
        name = check_full_name(full_name)
        checked_username = check_username(username)
        checked_email = check_email(email)
        checked_phone = check_phone(phone)
        check_password(password, username=checked_username, email=checked_email)
        if not self._sender.is_configured:
            raise email_not_configured()

        creation_id = uuid4()
        outcome = await self._repository.submit_supervisor(
            SupervisorForm(
                creation_id=creation_id,
                authorization_id=authorization_id,
                full_name=name,
                username=checked_username,
                email=checked_email,
                phone=checked_phone,
                password_hash=self._hasher.hash(password),
                recipient_digest=self._otp.recipient_digest(checked_email),
                ttl_seconds=self._ttl,
            )
        )
        if outcome is FlowOutcome.USERNAME_TAKEN:
            raise Conflict("This username is already taken.", code="USERNAME_TAKEN")
        if outcome is FlowOutcome.AUTHORIZATION_REQUIRED:
            raise PermissionDenied(
                f"Confirm your password and the emailed code before adding a {self._kind.label}.",
                code="REVERIFICATION_REQUIRED",
            )
        if outcome is not FlowOutcome.CREATED:
            raise PermissionDenied(f"Only an active {self._kind.creator} can add {self._kind.label}s.")
        sent = await self._codes.supervisor(creation_id)
        await self._transaction.commit()
        return sent


class ResendSupervisorCode:
    def __init__(
        self, repository, otp, sender, transaction, *, limits: OtpLimits, kind: AccountKind = SUPERVISOR_KIND
    ) -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._codes = _Codes(repository, otp, sender, transaction, limits, kind)

    async def execute(self, creation_id: UUID) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        sent = await self._codes.supervisor(creation_id)
        await self._transaction.commit()
        return sent


class VerifySupervisor:
    def __init__(
        self,
        repository: IStaffAccountRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        *,
        display_id_factory: Callable[[], str] = generate_supervisor_id,
        kind: AccountKind = SUPERVISOR_KIND,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._new_id = display_id_factory
        self._kind = kind

    async def execute(self, creation_id: UUID, code: str) -> CreatedSupervisor:
        _ensure_code_format(code)
        verification = await self._repository.verify_supervisor_otp(
            creation_id, self._otp.hash_purpose_code(self._kind.email_purpose, creation_id, code)
        )
        if verification.outcome is not FlowOutcome.VERIFIED:
            await (self._transaction.commit() if counts_attempt(verification) else self._transaction.rollback())
            raise code_not_verified(verification, **_creation_errors(self._kind))

        user_id = uuid4()
        for _ in range(_UNIQUE_ID_ATTEMPTS):
            result = await self._repository.complete_supervisor(creation_id, user_id=user_id, display_id=self._new_id())
            if result.outcome is not FlowOutcome.DISPLAY_ID_TAKEN:
                break
        if result.outcome is FlowOutcome.COMPLETED and result.supervisor is not None:
            await self._transaction.commit()
            return result.supervisor
        await self._transaction.rollback()  # nothing is created, and the code stays unused
        if result.outcome is FlowOutcome.EMAIL_ALREADY_REGISTERED:
            raise Conflict(
                "This email address already belongs to an account. Nothing was created.",
                code="EMAIL_ALREADY_REGISTERED",
            )
        if result.outcome is FlowOutcome.USERNAME_TAKEN:
            raise Conflict(
                "This username was taken by another account first. Nothing was created.", code="USERNAME_TAKEN"
            )
        errors = _creation_errors(self._kind)
        if result.outcome is FlowOutcome.EXPIRED:
            raise errors["expired"]
        if result.outcome is FlowOutcome.ALREADY_COMPLETED:
            raise errors["done"]
        if result.outcome is FlowOutcome.NOT_FOUND:
            raise errors["not_found"]
        raise Conflict(
            f"The {self._kind.label} could not be created. Nothing was created.", code=f"{self._kind.code}_CONFLICT"
        )


def _ensure_code_format(code: str) -> None:
    if not (len(code) == 6 and code.isascii() and code.isdigit()):
        raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
