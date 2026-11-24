"""Main Admin ownership transfer and Admin account deletion (migration 0010).

Delete Account:
- **Additional Admin, or a Main Admin with nobody to hand over to (single campus):**
  1. Admin ID + current password.
  2. A code to the registered email.
  3. Verify; the account, and the campus if no other active Admin remains on it, are deleted.
- **Main Admin while another eligible Admin exists:** deletion is refused until ownership has
  moved.
  1. Choose the recipient, re-enter the password and verify an emailed code. The request is sent.
  2. The recipient accepts. They are now the Main Admin, and the sender is an additional Admin.
  3. The sender deletes as above, with a fresh code. The transfer code cannot be reused: it has
     its own purpose and request.

Every database function acts only for the signed-in caller, so nobody can start, accept or
complete another Admin's transfer or deletion.
"""

import contextlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import (
    ICampusMediaStorage,
    MediaCleanupFailed,
    StagedMediaDeletion,
)
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
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    Conflict,
    CurrentPasswordIncorrect,
    DomainError,
    OperationFailed,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    DeletionOverview,
    FlowOutcome,
    IAccountLifecycleRepository,
    IncomingTransfer,
    IssueResult,
    IStaffAccountRepository,
    OwnershipCandidate,
    OwnershipHistoryEntry,
    VerifyResult,
)
from src.domain.repositories.i_unit_of_work import ITransaction

TRANSFER = "OWNERSHIP_TRANSFER"
DELETION = "ACCOUNT_DELETION"

_BLOCKERS: dict[FlowOutcome, str] = {
    FlowOutcome.OWNERSHIP_TRANSFER_REQUIRED: (
        "You are the Main Admin. Transfer ownership to another Admin before deleting your account."
    ),
    FlowOutcome.OWNERSHIP_TRANSFER_PENDING: (
        "Your ownership transfer is waiting for the other Admin to accept it. "
        "You can delete your account after it is accepted."
    ),
    FlowOutcome.OWNERSHIP_SUCCESSOR_REQUIRED: (
        "Your organization has other campuses but no other Admin who can take ownership, "
        "so your account cannot be deleted yet."
    ),
}


def _blocked(outcome: FlowOutcome) -> Conflict:
    return Conflict(_BLOCKERS[outcome], code=outcome.value)


def _transfer_errors() -> dict[str, DomainError]:
    return {
        "not_found": missing("Ownership transfer not found.", "TRANSFER_NOT_FOUND"),
        "expired": gone("This ownership transfer has expired. Please start again.", "TRANSFER_EXPIRED"),
        "done": already("This ownership transfer has already been confirmed.", "TRANSFER_ALREADY_VERIFIED"),
    }


def _deletion_errors() -> dict[str, DomainError]:
    return {
        "not_found": missing("Deletion request not found. Please start again.", "DELETION_NOT_FOUND"),
        "expired": gone("This deletion request has expired. Please start again.", "DELETION_EXPIRED"),
        "done": already("This deletion request is no longer open.", "DELETION_NOT_OPEN"),
    }


@dataclass(frozen=True, slots=True)
class DeletionStatus:
    overview: DeletionOverview
    candidates: list[OwnershipCandidate]


class _PasswordCheck:
    """The caller re-enters their current password; wrong ones count toward the sign-in lock."""

    def __init__(
        self,
        staff: IStaffAccountRepository,
        hasher: IPasswordHasher,
        transaction: ITransaction,
        *,
        max_failed_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._staff = staff
        self._hasher = hasher
        self._transaction = transaction
        self._max_failed = max_failed_attempts
        self._lockout = lockout_seconds
        self._clock = clock

    async def confirm(self, password: str) -> None:
        account = await self._staff.step_up_account()
        if account is None:
            self._hasher.verify(TIMING_DUMMY_HASH, password)
            raise _wrong_password()
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, password):
            locked_until = await self._staff.record_password_failure(
                account.user_id, max_attempts=self._max_failed, window_seconds=self._lockout
            )
            await self._transaction.commit()
            raise _locked(self._lockout) if locked_until is not None else _wrong_password()


def _wrong_password() -> CurrentPasswordIncorrect:
    return CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many incorrect passwords. Please try again later.", code="ACCOUNT_LOCKED", retry_after=max(1, retry_after)
    )


class _Codes:
    def __init__(
        self,
        repository: IAccountLifecycleRepository,
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

    async def transfer(self, transfer_id: UUID) -> CodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_transfer_otp(
            transfer_id, uuid4(), self._otp.hash_purpose_code(TRANSFER, transfer_id, code), self._limits
        )
        return await self._deliver(
            transfer_id,
            code,
            result,
            _transfer_errors(),
            subject="Confirm the ownership transfer",
            purpose="Enter this code to confirm handing over ownership of your organization to another Admin.",
        )

    async def deletion(self, request_id: UUID) -> CodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_deletion_otp(
            request_id, uuid4(), self._otp.hash_purpose_code(DELETION, request_id, code), self._limits
        )
        return await self._deliver(
            request_id,
            code,
            result,
            _deletion_errors(),
            subject="Confirm the deletion of your account",
            purpose="Enter this code to confirm the permanent deletion of your Admin account.",
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
        if result.outcome is not FlowOutcome.ISSUED or result.email is None or result.expires_at is None:
            await self._transaction.rollback()
            raise issue_refused(result, **errors)
        message = code_email(result.email, code, self._limits.ttl_seconds, subject=subject, purpose=purpose)
        await send_or_roll_back(self._sender, message, self._transaction)
        return CodeSent(
            subject_id=subject_id,
            masked_email=mask_email(result.email),
            delivery_channel=self._sender.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=result.expires_at,
        )


def _ensure_code_format(code: str) -> None:
    if not (len(code) == 6 and code.isascii() and code.isdigit()):
        raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")


class GetDeletionStatus:
    """What happens on Delete Account, and the transfer status page."""

    def __init__(self, repository: IAccountLifecycleRepository) -> None:
        self._repository = repository

    async def execute(self) -> DeletionStatus:
        overview = await self._repository.overview()
        if overview is None:
            raise PermissionDenied("Only an active Admin can do this.")
        candidates = await self._repository.candidates() if overview.is_main_admin else []
        return DeletionStatus(overview, candidates)


class StartOwnershipTransfer:
    def __init__(
        self,
        repository: IAccountLifecycleRepository,
        staff: IStaffAccountRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        verify_ttl_seconds: int,
        max_failed_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._transaction = transaction
        self._ttl = verify_ttl_seconds
        self._password = _PasswordCheck(
            staff,
            hasher,
            transaction,
            max_failed_attempts=max_failed_attempts,
            lockout_seconds=lockout_seconds,
            clock=clock,
        )
        self._codes = _Codes(repository, otp, sender, transaction, limits)

    async def execute(self, recipient_admin_id: str, current_password: str) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        await self._password.confirm(current_password)
        transfer_id = uuid4()
        outcome = await self._repository.start_transfer(
            transfer_id, recipient_admin_id.strip(), verify_ttl_seconds=self._ttl
        )
        if outcome is not FlowOutcome.CREATED:
            await self._transaction.rollback()
            if outcome is FlowOutcome.NOT_MAIN_ADMIN:
                raise PermissionDenied("Only the Main Admin can transfer ownership.", code="NOT_MAIN_ADMIN")
            if outcome is FlowOutcome.RECIPIENT_NOT_ELIGIBLE:
                raise ValidationFailed(
                    "Choose an active Admin of your organization from the list.", code="RECIPIENT_NOT_ELIGIBLE"
                )
            raise _blocked(FlowOutcome.OWNERSHIP_TRANSFER_PENDING)
        sent = await self._codes.transfer(transfer_id)
        await self._transaction.commit()
        return sent


class ResendOwnershipTransferCode:
    def __init__(self, repository, otp, sender, transaction, *, limits: OtpLimits) -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._codes = _Codes(repository, otp, sender, transaction, limits)

    async def execute(self, transfer_id: UUID) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        sent = await self._codes.transfer(transfer_id)
        await self._transaction.commit()
        return sent


class VerifyOwnershipTransfer:
    """A verified code sends the request to the recipient. Returns the acceptance deadline."""

    def __init__(
        self,
        repository: IAccountLifecycleRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        *,
        accept_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._accept_ttl = accept_ttl_seconds

    async def execute(self, transfer_id: UUID, code: str) -> datetime:
        _ensure_code_format(code)
        result = await self._repository.verify_transfer_otp(
            transfer_id, self._otp.hash_purpose_code(TRANSFER, transfer_id, code), accept_ttl_seconds=self._accept_ttl
        )
        if result.outcome is FlowOutcome.VERIFIED and result.expires_at is not None:
            await self._transaction.commit()
            return result.expires_at
        await (self._transaction.commit() if counts_attempt(result) else self._transaction.rollback())
        raise code_not_verified(result, **_transfer_errors())


class CancelOwnershipTransfer:
    def __init__(self, repository: IAccountLifecycleRepository, transaction: ITransaction) -> None:
        self._repository = repository
        self._transaction = transaction

    async def execute(self, transfer_id: UUID) -> None:
        if not await self._repository.cancel_transfer(transfer_id):
            await self._transaction.rollback()
            raise _transfer_errors()["not_found"]
        await self._transaction.commit()


class ListIncomingTransfers:
    def __init__(self, repository: IAccountLifecycleRepository, transaction: ITransaction) -> None:
        self._repository = repository
        self._transaction = transaction

    async def execute(self) -> list[IncomingTransfer]:
        incoming = await self._repository.incoming()
        await self._transaction.commit()  # keeps the expiry of out-of-date requests
        return incoming


class ListOwnershipHistory:
    """Accepted transfers to the caller, shown only while the caller is the Main Admin. The previous
    owner's name, username and email were copied at acceptance, so they stay after that account is
    deleted. The database function applies the same rule."""

    def __init__(self, repository: IAccountLifecycleRepository, transaction: ITransaction) -> None:
        self._repository = repository
        self._transaction = transaction

    async def execute(self, actor: User) -> list[OwnershipHistoryEntry]:
        if not actor.is_first_admin:
            raise PermissionDenied("Only the Main Admin can view the ownership history.", code="NOT_MAIN_ADMIN")
        entries = await self._repository.history()
        await self._transaction.rollback()  # read only
        return entries


class DecideOwnershipTransfer:
    """The recipient accepts (becoming the Main Admin) or rejects."""

    def __init__(self, repository: IAccountLifecycleRepository, transaction: ITransaction) -> None:
        self._repository = repository
        self._transaction = transaction

    async def execute(self, transfer_id: UUID, *, accept: bool) -> FlowOutcome:
        outcome = await self._repository.decide(transfer_id, accept=accept)
        if outcome in (FlowOutcome.ACCEPTED, FlowOutcome.REJECTED):
            await self._transaction.commit()
            return outcome
        if outcome in (FlowOutcome.EXPIRED, FlowOutcome.STALE):
            await self._transaction.commit()  # keep the request marked as closed
            raise gone(
                "This ownership transfer is no longer valid. Nothing was changed.",
                "TRANSFER_EXPIRED" if outcome is FlowOutcome.EXPIRED else "TRANSFER_STALE",
            )
        await self._transaction.rollback()
        if outcome is FlowOutcome.ALREADY_COMPLETED:
            raise already("This ownership transfer has already been accepted.", "TRANSFER_ALREADY_ACCEPTED")
        raise _transfer_errors()["not_found"]


class StartAccountDeletion:
    def __init__(
        self,
        repository: IAccountLifecycleRepository,
        staff: IStaffAccountRepository,
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
    ) -> None:
        self._repository = repository
        self._sender = sender
        self._transaction = transaction
        self._ttl = ttl_seconds
        self._password = _PasswordCheck(
            staff,
            hasher,
            transaction,
            max_failed_attempts=max_failed_attempts,
            lockout_seconds=lockout_seconds,
            clock=clock,
        )
        self._codes = _Codes(repository, otp, sender, transaction, limits)

    async def execute(self, actor: User, admin_id: str, current_password: str) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        if admin_id.strip() != actor.display_id:
            raise PermissionDenied("The Admin ID does not match your account.", code="ADMIN_ID_MISMATCH")
        await self._password.confirm(current_password)
        request_id = uuid4()
        outcome = await self._repository.start_deletion(request_id, ttl_seconds=self._ttl)
        if outcome is not FlowOutcome.CREATED:
            await self._transaction.rollback()
            if outcome in _BLOCKERS:
                raise _blocked(outcome)
            raise PermissionDenied("Only an active Admin can delete their account.")
        sent = await self._codes.deletion(request_id)
        await self._transaction.commit()
        return sent


class ResendAccountDeletionCode:
    def __init__(self, repository, otp, sender, transaction, *, limits: OtpLimits) -> None:  # type: ignore[no-untyped-def]
        self._sender = sender
        self._transaction = transaction
        self._codes = _Codes(repository, otp, sender, transaction, limits)

    async def execute(self, request_id: UUID) -> CodeSent:
        if not self._sender.is_configured:
            raise email_not_configured()
        sent = await self._codes.deletion(request_id)
        await self._transaction.commit()
        return sent


@dataclass(frozen=True, slots=True)
class AccountDeleted:
    campus_deleted: bool
    organization_deleted: bool


_NOTHING_DELETED = "Your account could not be deleted. Nothing was changed; please try again later."


class CompleteAccountDeletion:
    """Verifies the code and erases the account (and campus) in one database transaction.

    Campus files are moved out of reach before the commit and put back if the commit fails;
    they are erased after the commit. Success is reported only when both are done."""

    def __init__(
        self,
        repository: IAccountLifecycleRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        storage: ICampusMediaStorage,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._storage = storage

    async def execute(self, actor: User, request_id: UUID, code: str) -> AccountDeleted:
        _ensure_code_format(code)
        try:
            result = await self._repository.complete_deletion(
                request_id, self._otp.hash_purpose_code(DELETION, request_id, code)
            )
        except Exception as error:  # a database failure: the transaction is rolled back
            await self._transaction.rollback()
            raise OperationFailed(_NOTHING_DELETED, code="DELETION_FAILED") from error
        if result.outcome is FlowOutcome.DELETED:
            return await self._erase(actor, bool(result.campus_deleted), bool(result.organization_deleted))
        if result.outcome in _BLOCKERS:
            await self._transaction.rollback()  # nothing deleted, and the code stays unused
            raise _blocked(result.outcome)
        verification = VerifyResult(result.outcome, result.attempts_remaining)
        await (self._transaction.commit() if counts_attempt(verification) else self._transaction.rollback())
        raise code_not_verified(verification, **_deletion_errors())

    async def _erase(self, actor: User, campus_deleted: bool, organization_deleted: bool) -> AccountDeleted:
        staged = StagedMediaDeletion()
        with contextlib.suppress(MediaCleanupFailed):
            self._storage.sweep()  # leftovers of an earlier failed clean-up; already reported then
        try:
            if campus_deleted:
                staged = self._storage.stage(
                    actor.organization_id, actor.campus_id, whole_organization=organization_deleted
                )
        except (OSError, MediaCleanupFailed) as error:
            await self._transaction.rollback()
            self._storage.restore(staged)
            raise OperationFailed(_NOTHING_DELETED, code="DELETION_FAILED") from error
        try:
            await self._transaction.commit()
        except Exception as error:
            self._storage.restore(staged)
            raise OperationFailed(_NOTHING_DELETED, code="DELETION_FAILED") from error
        try:
            self._storage.finalize(staged)
        except MediaCleanupFailed as error:
            raise OperationFailed(
                "Your account and campus records were deleted, but some campus files could not be erased "
                "yet. They can no longer be opened and will be erased on the next clean-up. "
                "Contact the platform administrator if this continues.",
                code="MEDIA_CLEANUP_INCOMPLETE",
            ) from error
        return AccountDeleted(campus_deleted, organization_deleted)


# Data the application does not hold yet, so an export cannot contain it.
EXPORT_NOT_INCLUDED: tuple[tuple[str, str], ...] = (
    ("recordings", "Video recordings are not included in this export; they are deleted with the campus."),
    ("event_clips", "Event clips are not included in this export; they are deleted with the campus."),
    ("evidence_files", "Saved evidence files are not included in this export; download them before deleting."),
    ("incidents", "Incident cases, their history and chat are not included in this export."),
    ("reports", "Analytics reports are generated on demand and are not stored."),
    ("notifications", "In-app notifications are personal reminders and are not exported."),
    ("manual_report_photos", "Photos are not included; each manual report says whether it had one."),
    ("alert_chat", "Alert chat messages are not exported."),
)


class ExportCampusData:
    """Optional copy of the caller's own campus records, for an open deletion request. Built in
    memory and returned; nothing is written to the server. Skipping it changes nothing."""

    def __init__(
        self,
        repository: IAccountLifecycleRepository,
        transaction: ITransaction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._transaction = transaction
        self._clock = clock

    async def execute(self, request_id: UUID) -> dict[str, Any]:
        outcome, data = await self._repository.export_campus(request_id)
        await self._transaction.rollback()  # read only
        if outcome is FlowOutcome.EXPORTED:
            return {
                "format": "campus-export/1",
                "generated_at": self._clock().isoformat(),
                **data,
                "not_included": [{"category": c, "reason": r} for c, r in EXPORT_NOT_INCLUDED],
            }
        if outcome in _BLOCKERS:
            raise _blocked(outcome)
        errors = _deletion_errors()
        raise errors["expired"] if outcome is FlowOutcome.EXPIRED else errors["not_found"]
