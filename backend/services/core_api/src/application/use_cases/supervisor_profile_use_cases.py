"""A Supervisor's own profile.

Editable: Name, Username, Email, Password. Never editable: Organization Name, Organization
ID, Supervisor ID (the request cannot even name them; the database also refuses any change
of a Supervisor ID).

Save = current password, then a code to the registered email (and, for a new email, a code
to the new address too). The changes are applied, all at once, only after the code(s) are
verified. If verification fails, expires or is cancelled, nothing changes. A new password
signs out every earlier session.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.organization_registration_use_cases import mask_email
from src.application.use_cases.otp_flow import (
    TIMING_DUMMY_HASH,
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
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    Conflict,
    CurrentPasswordIncorrect,
    DomainError,
    NotFound,
    PermissionDenied,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    ISupervisorProfileRepository,
    SupervisorProfile,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import (
    check_email,
    check_full_name,
    check_password,
    check_phone,
    check_username,
)

CURRENT = "PROFILE_CHANGE"
NEW_EMAIL = "PROFILE_NEW_EMAIL"


@dataclass(frozen=True, slots=True)
class ProfileChangeForm:
    current_password: str = field(repr=False)
    full_name: str | None = None
    username: str | None = None
    email: str | None = None
    new_password: str | None = field(default=None, repr=False)
    confirm_new_password: str | None = field(default=None, repr=False)
    phone: str | None = None  # Admin profile only (decision D-024)


@dataclass(frozen=True, slots=True)
class ProfileChangeStarted:
    change_id: UUID
    masked_email: str
    masked_new_email: str | None
    delivery_channel: str
    code_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime


def _errors() -> dict[str, DomainError]:
    return {
        "not_found": missing("Profile change not found.", "PROFILE_CHANGE_NOT_FOUND"),
        "expired": gone("This change has expired. Nothing was changed; please save again.", "PROFILE_CHANGE_EXPIRED"),
        "done": already("These changes have already been saved.", "PROFILE_CHANGE_ALREADY_APPLIED"),
    }


class GetSupervisorProfile:
    def __init__(self, repository: ISupervisorProfileRepository) -> None:
        self._repository = repository

    async def execute(self, actor: User) -> SupervisorProfile:
        profile = await self._repository.profile(actor.id, actor.organization_id, actor.campus_id)
        if profile is None:
            raise NotFound("Profile not found.")
        return profile


class _Codes:
    def __init__(
        self,
        repository: ISupervisorProfileRepository,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        limits: OtpLimits,
        label: str = "Supervisor",
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._limits = limits
        self._label = label

    async def send(self, change_id: UUID, *, new_email: bool) -> tuple[str, datetime] | None:
        """Issue and email one code. Returns (recipient, change expiry), or None when the change
        has no new email (only for ``new_email=True``)."""
        code = self._otp.new_code()
        purpose = NEW_EMAIL if new_email else CURRENT
        result = await self._repository.issue(
            change_id,
            new_email=new_email,
            challenge_id=uuid4(),
            code_hash=self._otp.hash_purpose_code(purpose, change_id, code),
            limits=self._limits,
        )
        if new_email and result.outcome is FlowOutcome.NOT_FOUND:
            return None
        if result.outcome is not FlowOutcome.ISSUED or result.email is None or result.expires_at is None:
            await self._transaction.rollback()
            raise issue_refused(result, **_errors())
        message = code_email(
            result.email,
            code,
            self._limits.ttl_seconds,
            subject="Confirm your new email address" if new_email else "Confirm your profile changes",
            purpose=(
                f"Enter this code to confirm this address as the new email of your {self._label} account."
                if new_email
                else f"Enter this code to confirm the changes to your {self._label} profile."
            ),
        )
        await send_or_roll_back(self._sender, message, self._transaction)
        return result.email, result.expires_at


class StartProfileChange:
    def __init__(
        self,
        repository: ISupervisorProfileRepository,
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
        label: str = "Supervisor",
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._limits = limits
        self._ttl = ttl_seconds
        self._max_failed = max_failed_attempts
        self._lockout = lockout_seconds
        self._clock = clock
        self._label = label
        self._codes = _Codes(repository, otp, sender, transaction, limits, label)

    async def execute(self, actor: User, form: ProfileChangeForm) -> ProfileChangeStarted:
        current = await GetSupervisorProfile(self._repository).execute(actor)
        name = username = email = password = None
        if form.full_name is not None and check_full_name(form.full_name) != current.full_name:
            name = check_full_name(form.full_name)
        if form.username is not None and check_username(form.username) != current.username:
            username = check_username(form.username)
        if form.email is not None and check_email(form.email).lower() != current.email.lower():
            email = check_email(form.email)
        phone = None
        if form.phone is not None:
            if not getattr(self._repository, "SUPPORTS_PHONE", False):
                raise ValidationFailed("The phone number cannot be changed here.", code="PHONE_NOT_EDITABLE")
            if check_phone(form.phone) != current.phone:
                phone = check_phone(form.phone)
        if form.new_password is not None or form.confirm_new_password is not None:
            if form.new_password != form.confirm_new_password or form.new_password is None:
                raise ValidationFailed("The new password and its confirmation do not match.", code="PASSWORD_MISMATCH")
            check_password(
                form.new_password, username=username or current.username or "\u0000", email=email or current.email
            )
            password = form.new_password
        if not any(value is not None for value in (name, username, email, password, phone)):
            raise ValidationFailed("Nothing to save: no field differs from your current profile.", code="NO_CHANGES")
        if not self._sender.is_configured:
            raise email_not_configured()

        account = await self._repository.account()
        if account is None:
            self._hasher.verify(TIMING_DUMMY_HASH, form.current_password)
            raise _wrong_password()
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, form.current_password):
            locked_until = await self._repository.record_password_failure(
                account.user_id, max_attempts=self._max_failed, window_seconds=self._lockout
            )
            await self._transaction.commit()  # counts toward the same lock as sign-in
            raise _locked(self._lockout) if locked_until is not None else _wrong_password()

        change_id = uuid4()
        outcome = await self._repository.submit(
            change_id,
            full_name=name,
            username=username,
            email=email,
            password_hash=self._hasher.hash(password) if password is not None else None,
            new_email_digest=self._otp.recipient_digest(email) if email is not None else None,
            ttl_seconds=self._ttl,
            **({"phone": phone} if getattr(self._repository, "SUPPORTS_PHONE", False) else {}),
        )
        if outcome is FlowOutcome.USERNAME_TAKEN:
            await self._transaction.rollback()
            raise Conflict("This username is already taken.", code="USERNAME_TAKEN")
        if outcome is not FlowOutcome.CREATED:
            await self._transaction.rollback()
            raise PermissionDenied(f"Only an active {self._label} can change this profile.")
        sent_current = await self._codes.send(change_id, new_email=False)
        sent_new = await self._codes.send(change_id, new_email=True) if email is not None else None
        await self._transaction.commit()
        assert sent_current is not None
        return ProfileChangeStarted(
            change_id=change_id,
            masked_email=mask_email(sent_current[0]),
            masked_new_email=mask_email(sent_new[0]) if sent_new else None,
            delivery_channel=self._sender.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=sent_current[1],
        )


def _wrong_password() -> CurrentPasswordIncorrect:
    return CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many incorrect passwords. Please try again later.", code="ACCOUNT_LOCKED", retry_after=max(1, retry_after)
    )


class ResendProfileChangeCodes:
    def __init__(self, repository, otp, sender, transaction, *, limits: OtpLimits, label: str = "Supervisor") -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._limits = limits
        self._codes = _Codes(repository, otp, sender, transaction, limits, label)

    async def execute(self, change_id: UUID) -> ProfileChangeStarted:
        if not self._sender.is_configured:
            raise email_not_configured()
        sent_current = await self._codes.send(change_id, new_email=False)
        sent_new = await self._codes.send(change_id, new_email=True)
        await self._transaction.commit()
        assert sent_current is not None
        return ProfileChangeStarted(
            change_id=change_id,
            masked_email=mask_email(sent_current[0]),
            masked_new_email=mask_email(sent_new[0]) if sent_new else None,
            delivery_channel=self._sender.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=sent_current[1],
        )


class VerifyProfileChange:
    """Returns whether the password changed (then every earlier session is signed out)."""

    def __init__(self, repository: ISupervisorProfileRepository, otp: IOtpCodec, transaction: ITransaction) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction

    async def execute(self, change_id: UUID, otp: str, new_email_otp: str | None) -> bool:
        for code in (otp, new_email_otp):
            if code is not None and not (len(code) == 6 and code.isascii() and code.isdigit()):
                raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
        current = await self._repository.verify(
            change_id, new_email=False, code_hash=self._otp.hash_purpose_code(CURRENT, change_id, otp)
        )
        if current.outcome is not FlowOutcome.VERIFIED:
            await (self._transaction.commit() if counts_attempt(current) else self._transaction.rollback())
            raise code_not_verified(current, **_errors())
        if new_email_otp is not None:
            new = await self._repository.verify(
                change_id, new_email=True, code_hash=self._otp.hash_purpose_code(NEW_EMAIL, change_id, new_email_otp)
            )
            if new.outcome not in (FlowOutcome.VERIFIED, FlowOutcome.NOT_FOUND):  # NOT_FOUND: no email change
                await self._transaction.commit()  # keeps the counted attempt and the verified registered email
                raise code_not_verified(new, **_errors())

        outcome, password_changed = await self._repository.apply(change_id)
        if outcome is FlowOutcome.APPLIED:
            await self._transaction.commit()
            return password_changed
        if outcome is FlowOutcome.EMAIL_NOT_VERIFIED:
            await self._transaction.commit()  # keep the verified registered-email code
            raise VerificationFailed(
                "Enter the code sent to your new email address as well.", code="NEW_EMAIL_OTP_REQUIRED"
            )
        await self._transaction.rollback()  # nothing is applied
        if outcome is FlowOutcome.EMAIL_ALREADY_REGISTERED:
            raise Conflict(
                "This email address already belongs to an account. Nothing was changed.",
                code="EMAIL_ALREADY_REGISTERED",
            )
        if outcome is FlowOutcome.USERNAME_TAKEN:
            raise Conflict(
                "This username was taken by another account first. Nothing was changed.", code="USERNAME_TAKEN"
            )
        errors = _errors()
        raise (
            errors["expired"]
            if outcome is FlowOutcome.EXPIRED
            else errors["done" if outcome is FlowOutcome.ALREADY_COMPLETED else "not_found"]
        )


class CancelProfileChange:
    def __init__(self, repository: ISupervisorProfileRepository, transaction: ITransaction) -> None:
        self._repository = repository
        self._transaction = transaction

    async def execute(self, change_id: UUID) -> None:
        if not await self._repository.cancel(change_id):
            await self._transaction.rollback()
            raise _errors()["not_found"]
        await self._transaction.commit()
