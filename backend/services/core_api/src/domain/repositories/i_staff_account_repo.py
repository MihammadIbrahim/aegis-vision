"""Storage for the Admin step-up check, Supervisor creation, and password reset
(migration 0007). Each method calls one narrow database function."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from src.domain.repositories.i_organization_registration_repo import OtpLimits


class FlowOutcome(StrEnum):
    """Every answer the step-up, Supervisor-creation and password-reset functions can give."""

    ISSUED = "ISSUED"
    VERIFIED = "VERIFIED"
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    APPLIED = "APPLIED"
    OK = "OK"
    COOLDOWN = "COOLDOWN"
    HOURLY_LIMIT = "HOURLY_LIMIT"
    INVALID_CODE = "INVALID_CODE"
    ATTEMPTS_EXCEEDED = "ATTEMPTS_EXCEEDED"
    CODE_EXPIRED = "CODE_EXPIRED"
    NO_ACTIVE_CODE = "NO_ACTIVE_CODE"
    NOT_FOUND = "NOT_FOUND"
    EXPIRED = "EXPIRED"
    ALREADY_VERIFIED = "ALREADY_VERIFIED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    DISPLAY_ID_TAKEN = "DISPLAY_ID_TAKEN"
    INVALID_TOKEN = "INVALID_TOKEN"
    CONFLICT = "CONFLICT"
    # Ownership transfer and account deletion (migration 0010)
    NOT_MAIN_ADMIN = "NOT_MAIN_ADMIN"
    RECIPIENT_NOT_ELIGIBLE = "RECIPIENT_NOT_ELIGIBLE"
    OWNERSHIP_TRANSFER_PENDING = "OWNERSHIP_TRANSFER_PENDING"
    OWNERSHIP_TRANSFER_REQUIRED = "OWNERSHIP_TRANSFER_REQUIRED"
    OWNERSHIP_SUCCESSOR_REQUIRED = "OWNERSHIP_SUCCESSOR_REQUIRED"
    CANCELLED = "CANCELLED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    DELETED = "DELETED"
    EXPORTED = "EXPORTED"
    # Campus requests from applicants (migration 0021)
    CAMPUS_NAME_TAKEN = "CAMPUS_NAME_TAKEN"
    EMAIL_ALREADY_PENDING = "EMAIL_ALREADY_PENDING"


@dataclass(frozen=True, slots=True)
class IssueResult:
    outcome: FlowOutcome
    retry_after_seconds: int | None = None
    email: str | None = None  # None for a password reset that matched no account
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VerifyResult:
    outcome: FlowOutcome
    attempts_remaining: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StepUpAccount:
    user_id: UUID
    password_hash: str = field(repr=False)
    locked_until: datetime | None


@dataclass(frozen=True, slots=True)
class SupervisorForm:
    creation_id: UUID
    authorization_id: UUID
    full_name: str
    username: str
    email: str
    phone: str
    password_hash: str = field(repr=False)
    recipient_digest: str = field(repr=False)
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class CreatedSupervisor:
    supervisor_id: str
    full_name: str
    username: str
    email: str
    phone: str
    campus_name: str
    organization_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CompleteSupervisorResult:
    outcome: FlowOutcome
    supervisor: CreatedSupervisor | None = None


@dataclass(frozen=True, slots=True)
class ResetIdentity:
    outcome: FlowOutcome
    username: str | None = None
    email: str | None = None


class IStaffAccountRepository(Protocol):
    # Admin step-up (acts for the signed-in caller only)
    async def step_up_account(self) -> StepUpAccount | None: ...
    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None: ...
    async def start_step_up(self, grant_id: UUID, *, ttl_seconds: int) -> None: ...
    async def issue_step_up_otp(
        self, grant_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def verify_step_up_otp(self, grant_id: UUID, code_hash: str, *, form_ttl_seconds: int) -> VerifyResult: ...

    # Supervisor creation (for the signed-in Admin's own organization and campus)
    async def submit_supervisor(self, form: SupervisorForm) -> FlowOutcome: ...
    async def issue_supervisor_otp(
        self, creation_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def verify_supervisor_otp(self, creation_id: UUID, code_hash: str) -> VerifyResult: ...
    async def complete_supervisor(
        self, creation_id: UUID, *, user_id: UUID, display_id: str
    ) -> CompleteSupervisorResult: ...

    # Password reset (public)
    async def request_password_reset(
        self, request_id: UUID, organization_code: str, email: str, subject_digest: str, *, ttl_seconds: int
    ) -> None: ...
    async def request_password_reset_by_guard_id(
        self, request_id: UUID, guard_id: str, email: str, subject_digest: str, *, ttl_seconds: int, stationed: bool
    ) -> None: ...
    async def request_password_reset_by_supervisor_id(
        self, request_id: UUID, supervisor_id: str, email: str, subject_digest: str, *, ttl_seconds: int
    ) -> None: ...
    async def issue_reset_otp(
        self, request_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def verify_reset_otp(
        self, request_id: UUID, code_hash: str, token_hash: str, *, token_ttl_seconds: int
    ) -> VerifyResult: ...
    async def reset_identity(self, request_id: UUID, token_hash: str) -> ResetIdentity: ...
    async def complete_reset(self, request_id: UUID, token_hash: str, password_hash: str) -> FlowOutcome: ...


# --- Supervisor profile (migration 0008) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorProfile:
    organization_name: str
    organization_id: str
    supervisor_id: str
    full_name: str
    username: str | None
    email: str
    phone: str | None


@dataclass(frozen=True, slots=True)
class ProfileAccount:
    user_id: UUID
    password_hash: str = field(repr=False)
    locked_until: datetime | None


class ISupervisorProfileRepository(Protocol):
    """Acts only for the signed-in Supervisor."""

    async def profile(self, user_id: UUID, organization_id: UUID, campus_id: UUID) -> SupervisorProfile | None: ...
    async def account(self) -> ProfileAccount | None: ...
    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None: ...
    async def submit(
        self,
        change_id: UUID,
        *,
        full_name: str | None,
        username: str | None,
        email: str | None,
        password_hash: str | None,
        new_email_digest: str | None,
        ttl_seconds: int,
    ) -> FlowOutcome: ...
    async def issue(
        self, change_id: UUID, *, new_email: bool, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def verify(self, change_id: UUID, *, new_email: bool, code_hash: str) -> VerifyResult: ...
    async def apply(self, change_id: UUID) -> tuple[FlowOutcome, bool]: ...
    async def cancel(self, change_id: UUID) -> bool: ...


class IGuardProfileRepository(ISupervisorProfileRepository, Protocol):
    """A Guard's own profile: only email, phone number and password can change (migration 0009)."""

    async def submit_guard_change(
        self,
        change_id: UUID,
        *,
        email: str | None,
        phone: str | None,
        password_hash: str | None,
        new_email_digest: str | None,
        ttl_seconds: int,
    ) -> FlowOutcome: ...


# --- Ownership transfer and account deletion (migrations 0010 and 0011) ----------------------------------


@dataclass(frozen=True, slots=True)
class DeletionOverview:
    is_main_admin: bool
    blocker: FlowOutcome | None  # why deletion cannot start yet, if it cannot
    campus_will_be_deleted: bool
    organization_will_be_deleted: bool
    transfer_id: UUID | None
    transfer_status: str | None
    transfer_to_name: str | None
    transfer_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class OwnershipCandidate:
    admin_id: str
    full_name: str


@dataclass(frozen=True, slots=True)
class IncomingTransfer:
    transfer_id: UUID
    from_name: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OwnershipHistoryEntry:
    """An accepted transfer to the current Main Admin, with the previous owner as they were then."""

    transfer_id: UUID
    previous_owner_name: str
    previous_owner_username: str | None
    previous_owner_email: str
    status: str
    offered_at: datetime
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class DeletionResult:
    outcome: FlowOutcome
    attempts_remaining: int | None = None
    campus_deleted: bool | None = None
    organization_deleted: bool | None = None


class IAccountLifecycleRepository(Protocol):
    """Acts only for the signed-in Admin (migration 0010)."""

    async def overview(self) -> DeletionOverview | None: ...
    async def candidates(self) -> list[OwnershipCandidate]: ...
    async def start_transfer(
        self, transfer_id: UUID, recipient_admin_id: str, *, verify_ttl_seconds: int
    ) -> FlowOutcome: ...
    async def issue_transfer_otp(
        self, transfer_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def verify_transfer_otp(
        self, transfer_id: UUID, code_hash: str, *, accept_ttl_seconds: int
    ) -> VerifyResult: ...
    async def cancel_transfer(self, transfer_id: UUID) -> bool: ...
    async def incoming(self) -> list[IncomingTransfer]: ...
    async def history(self) -> list[OwnershipHistoryEntry]: ...
    async def decide(self, transfer_id: UUID, *, accept: bool) -> FlowOutcome: ...
    async def start_deletion(self, request_id: UUID, *, ttl_seconds: int) -> FlowOutcome: ...
    async def issue_deletion_otp(
        self, request_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...
    async def complete_deletion(self, request_id: UUID, code_hash: str) -> DeletionResult: ...
    async def export_campus(self, request_id: UUID) -> tuple[FlowOutcome, dict[str, Any]]: ...
