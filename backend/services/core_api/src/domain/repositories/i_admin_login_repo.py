"""Storage for Organization Admin sign-ins: credential look-up, failed-attempt counting,
pending sign-ins and their one-time codes. The SQL implementation calls the narrow
database functions of migration 0006, which do the checks and the locking."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from src.domain.repositories.i_organization_registration_repo import OtpLimits


@dataclass(frozen=True, slots=True)
class LoginAccount:
    """The Admin account matching the exact organization name, Organization ID and email."""

    user_id: UUID
    password_hash: str = field(repr=False)
    eligible: bool  # account, campus and organization are all active
    locked_until: datetime | None


class LoginIssueOutcome(StrEnum):
    ISSUED = "ISSUED"
    COOLDOWN = "COOLDOWN"
    HOURLY_LIMIT = "HOURLY_LIMIT"
    LOGIN_NOT_FOUND = "LOGIN_NOT_FOUND"
    LOGIN_EXPIRED = "LOGIN_EXPIRED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class LoginVerifyOutcome(StrEnum):
    VERIFIED = "VERIFIED"
    INVALID_CODE = "INVALID_CODE"  # an attempt was counted; commit that
    ATTEMPTS_EXCEEDED = "ATTEMPTS_EXCEEDED"  # may also have counted the last attempt; commit
    CODE_EXPIRED = "CODE_EXPIRED"
    NO_ACTIVE_CODE = "NO_ACTIVE_CODE"
    LOGIN_NOT_FOUND = "LOGIN_NOT_FOUND"
    LOGIN_EXPIRED = "LOGIN_EXPIRED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class LoginIssueResult:
    outcome: LoginIssueOutcome
    retry_after_seconds: int | None = None
    email: str | None = None
    login_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LoginVerifyResult:
    outcome: LoginVerifyOutcome
    attempts_remaining: int | None = None
    user_id: UUID | None = None


class IAdminLoginRepository(Protocol):
    async def find_account(self, organization_name: str, organization_code: str, email: str) -> LoginAccount | None:
        """Also locks the account's credentials until the transaction ends."""
        ...

    async def find_pending_campus_request(
        self, organization_name: str, organization_code: str, email: str
    ) -> str | None:
        """The password hash of a verified campus request still waiting for the Main Admin (migration 0021)."""
        ...

    async def find_guard_account(self, guard_id: str, email: str) -> LoginAccount | None:
        """A Guard matching the exact Guard ID and email (locks the credentials too)."""
        ...

    async def guard_is_stationed(self, user_id: UUID) -> bool | None:
        """True/False for an active Guard with/without a location; None for anyone else."""
        ...

    async def record_failure(self, user_id: UUID, *, max_attempts: int, window_seconds: int) -> datetime | None:
        """Returns the lock time if this failure locked the account."""
        ...

    async def start(self, user_id: UUID, login_id: UUID, *, ttl_seconds: int) -> None: ...

    async def issue_otp(
        self, login_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> LoginIssueResult: ...

    async def verify_otp(self, login_id: UUID, code_hash: str) -> LoginVerifyResult: ...
