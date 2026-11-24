"""Storage for pending registrations and their one-time codes.

The SQL implementation calls one narrow database function per step. The function
does the checks and the locking, so every rule here holds even under concurrent
requests.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from src.domain.entities.organization import CompletedRegistration, PendingRegistration


class SubmitOutcome(StrEnum):
    CREATED = "CREATED"
    ORGANIZATION_NAME_TAKEN = "ORGANIZATION_NAME_TAKEN"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    ORGANIZATION_CODE_TAKEN = "ORGANIZATION_CODE_TAKEN"  # pick another code and try again


class IssueOutcome(StrEnum):
    ISSUED = "ISSUED"
    COOLDOWN = "COOLDOWN"
    HOURLY_LIMIT = "HOURLY_LIMIT"
    REGISTRATION_NOT_FOUND = "REGISTRATION_NOT_FOUND"
    REGISTRATION_EXPIRED = "REGISTRATION_EXPIRED"  # its details were erased; commit that
    ALREADY_COMPLETED = "ALREADY_COMPLETED"


class VerifyOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    INVALID_CODE = "INVALID_CODE"  # an attempt was counted; commit that
    ATTEMPTS_EXCEEDED = "ATTEMPTS_EXCEEDED"  # may also have counted the last attempt; commit
    CODE_EXPIRED = "CODE_EXPIRED"
    NO_ACTIVE_CODE = "NO_ACTIVE_CODE"
    REGISTRATION_NOT_FOUND = "REGISTRATION_NOT_FOUND"
    REGISTRATION_EXPIRED = "REGISTRATION_EXPIRED"  # its details were erased; commit that
    ALREADY_COMPLETED = "ALREADY_COMPLETED"


class CompleteOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    ORGANIZATION_NAME_TAKEN = "ORGANIZATION_NAME_TAKEN"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    DISPLAY_ID_TAKEN = "DISPLAY_ID_TAKEN"  # pick another display ID and try again
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    REGISTRATION_NOT_FOUND = "REGISTRATION_NOT_FOUND"
    REGISTRATION_EXPIRED = "REGISTRATION_EXPIRED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class OtpLimits:
    ttl_seconds: int
    max_attempts: int
    resend_cooldown_seconds: int
    hourly_limit: int  # codes per email address per hour, across all registrations


@dataclass(frozen=True, slots=True)
class IssueResult:
    outcome: IssueOutcome
    retry_after_seconds: int | None = None
    email: str | None = None  # where to send the code (only when ISSUED)
    registration_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VerifyResult:
    outcome: VerifyOutcome
    attempts_remaining: int | None = None


@dataclass(frozen=True, slots=True)
class CompleteResult:
    outcome: CompleteOutcome
    registration: CompletedRegistration | None = None


class IOrganizationRegistrationRepository(Protocol):
    async def submit(self, registration: PendingRegistration) -> SubmitOutcome: ...

    async def issue_otp(
        self, registration_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...

    async def verify_otp(self, registration_id: UUID, code_hash: str) -> VerifyResult: ...

    async def complete(
        self, registration_id: UUID, *, organization_id: UUID, campus_id: UUID, user_id: UUID, display_id: str
    ) -> CompleteResult: ...
