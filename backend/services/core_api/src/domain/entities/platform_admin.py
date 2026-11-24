from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PlatformAdmin:
    """A Platform Super Admin. Separate from organization users: no organization,
    no campus, and no access to any organization's operational data."""

    id: UUID
    username: str
    password_hash: str
    is_active: bool
    failed_login_attempts: int
    last_failed_login_at: datetime | None
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    # The account the seed command created; emergency recovery logs in to it.
    is_primary: bool = False
    # Copied into every token; increased on a credential change to sign out older tokens.
    token_version: int = 1

    def is_locked(self, now: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > now

    def after_failed_login(self, now: datetime, max_attempts: int, window: timedelta) -> "PlatformAdmin":
        """Count a failure. Failures older than the window start a fresh count;
        reaching max_attempts locks the account for one window."""
        recent = self.last_failed_login_at is not None and self.last_failed_login_at > now - window
        attempts = self.failed_login_attempts + 1 if recent else 1
        if attempts >= max_attempts:
            return replace(self, failed_login_attempts=0, last_failed_login_at=now, locked_until=now + window)
        return replace(self, failed_login_attempts=attempts, last_failed_login_at=now)

    def after_successful_login(self, now: datetime) -> "PlatformAdmin":
        return replace(self, failed_login_attempts=0, last_failed_login_at=None, locked_until=None, last_login_at=now)

    def with_new_username(self, username: str) -> "PlatformAdmin":
        return replace(self, username=username, token_version=self.token_version + 1)

    def with_new_password_hash(self, password_hash: str) -> "PlatformAdmin":
        return replace(self, password_hash=password_hash, token_version=self.token_version + 1)

    def with_new_credentials(self, username: str, password_hash: str) -> "PlatformAdmin":
        """Both changed together: counts as one change (one token-version step)."""
        return replace(self, username=username, password_hash=password_hash, token_version=self.token_version + 1)
