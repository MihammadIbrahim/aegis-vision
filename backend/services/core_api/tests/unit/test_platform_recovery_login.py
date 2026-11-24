"""Emergency recovery login and credential changes, with in-memory fakes (no database)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.application.use_cases.platform_admin_use_cases import (
    INVALID_CREDENTIALS,
    LOGIN_NORMAL,
    LOGIN_RECOVERY,
    ChangePlatformPassword,
    LoginPlatformAdmin,
)
from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.entities.platform_audit_event import PlatformAuditEvent
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    CurrentPasswordIncorrect,
)
from src.domain.value_objects.recovery_credentials import RecoveryCredentials

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=15)
RECOVERY = RecoveryCredentials(username="admin", password="admin@123")


class FakeHasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeAdmins:
    def __init__(self, admin: PlatformAdmin) -> None:
        self.admin = admin

    async def get_by_username(self, username: str, *, for_update: bool = False) -> PlatformAdmin | None:
        return self.admin if self.admin.username.lower() == username.lower() else None

    async def get_by_id(self, admin_id: UUID, *, for_update: bool = False) -> PlatformAdmin | None:
        return self.admin if self.admin.id == admin_id else None

    async def get_primary(self, *, for_update: bool = False) -> PlatformAdmin | None:
        return self.admin if self.admin.is_primary else None

    async def save_login_state(self, admin: PlatformAdmin) -> None:
        self.admin = admin

    async def save_credentials(self, admin: PlatformAdmin) -> None:
        self.admin = admin


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[PlatformAuditEvent] = []

    async def record(self, event: PlatformAuditEvent) -> None:
        self.events.append(event)


def primary(**changes: object) -> PlatformAdmin:
    base = PlatformAdmin(
        id=uuid4(),
        username="boss",
        password_hash="hashed:New-password-2026",
        is_active=True,
        failed_login_attempts=0,
        last_failed_login_at=None,
        locked_until=None,
        last_login_at=None,
        created_at=NOW - timedelta(days=1),
        is_primary=True,
        token_version=2,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def login(admins: FakeAdmins, audit: FakeAudit) -> LoginPlatformAdmin:
    return LoginPlatformAdmin(
        admins,
        FakeHasher(),
        max_attempts=5,
        lockout=WINDOW,
        clock=lambda: NOW,
        recovery=RECOVERY,
        audit=audit,
        token_factory=lambda admin: f"token:{admin.id}:v{admin.token_version}",
    )


async def test_recovery_logs_in_to_the_primary_admin_after_a_username_change() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    result = await login(admins, audit).execute("admin", "admin@123")
    assert result.login_method == LOGIN_RECOVERY and result.admin_id == admins.admin.id
    assert audit.events[-1].metadata == {"login_method": LOGIN_RECOVERY}


async def test_normal_login_still_wins_when_both_would_match() -> None:
    admins, audit = FakeAdmins(primary(username="admin", password_hash="hashed:admin@123")), FakeAudit()
    assert (await login(admins, audit).execute("admin", "admin@123")).login_method == LOGIN_NORMAL


async def test_wrong_recovery_password_counts_on_the_primary_account_and_locks_it() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    for _ in range(5):
        with pytest.raises(AuthenticationFailed, match=INVALID_CREDENTIALS):
            await login(admins, audit).execute("admin", "wrong")
    assert admins.admin.is_locked(NOW)
    with pytest.raises(AccountLocked):  # both methods are blocked by the same lock
        await login(admins, audit).execute("boss", "New-password-2026")
    with pytest.raises(AccountLocked):
        await login(admins, audit).execute("admin", "admin@123")


async def test_recovery_password_with_the_normal_username_is_refused() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    with pytest.raises(AuthenticationFailed):
        await login(admins, audit).execute("boss", "admin@123")


async def test_inactive_primary_cannot_use_recovery() -> None:
    admins, audit = FakeAdmins(primary(is_active=False)), FakeAudit()
    with pytest.raises(AuthenticationFailed):
        await login(admins, audit).execute("admin", "admin@123")


async def test_password_change_needs_the_database_password_and_bumps_the_version() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    change = ChangePlatformPassword(
        admins, FakeHasher(), max_attempts=5, lockout=WINDOW, audit=audit, clock=lambda: NOW
    )
    with pytest.raises(CurrentPasswordIncorrect):
        await change.execute(admins.admin.id, "admin@123", "Newer-password-2027", "Newer-password-2027")
    assert admins.admin.failed_login_attempts == 1
    updated = await change.execute(admins.admin.id, "New-password-2026", "Newer-password-2027", "Newer-password-2027")
    assert updated.token_version == 3 and updated.password_hash == "hashed:Newer-password-2027"
    assert [e.action for e in audit.events] == ["PASSWORD_CHANGED", "PASSWORD_CHANGED"]
    assert [e.outcome for e in audit.events] == ["FAILURE", "SUCCESS"]
