"""Super Admin login rules, tested with in-memory fakes (no database)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.application.use_cases.platform_admin_use_cases import (
    INVALID_CREDENTIALS,
    LoginPlatformAdmin,
)
from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.exceptions.domain_errors import AccountLocked, AuthenticationFailed

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=15)


class FakeHasher:
    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


class FakeAdmins:
    def __init__(self, admin: PlatformAdmin | None) -> None:
        self.admin = admin
        self.saved: list[PlatformAdmin] = []

    async def get_by_username(self, username: str, *, for_update: bool = False) -> PlatformAdmin | None:
        return self.admin if self.admin and self.admin.username.lower() == username.lower() else None

    async def get_by_id(self, admin_id: UUID) -> PlatformAdmin | None:
        return self.admin

    async def save_login_state(self, admin: PlatformAdmin) -> None:
        self.saved.append(admin)
        self.admin = admin


def make_admin(**changes: object) -> PlatformAdmin:
    base = PlatformAdmin(
        id=uuid4(),
        username="root",
        password_hash="hashed:secret-password",
        is_active=True,
        failed_login_attempts=0,
        last_failed_login_at=None,
        locked_until=None,
        last_login_at=None,
        created_at=NOW - timedelta(days=30),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def login(admins: FakeAdmins, clock_time: datetime = NOW) -> LoginPlatformAdmin:
    return LoginPlatformAdmin(
        admins,
        FakeHasher(),
        lambda admin_id: f"token-for-{admin_id}",
        max_attempts=5,
        lockout=WINDOW,
        clock=lambda: clock_time,
    )


async def test_correct_password_issues_token_and_clears_failures() -> None:
    admins = FakeAdmins(make_admin(failed_login_attempts=3, last_failed_login_at=NOW - timedelta(minutes=1)))
    result = await login(admins).execute("ROOT", "secret-password")  # username is case-insensitive
    assert result.access_token == f"token-for-{admins.admin.id}"  # type: ignore[union-attr]
    assert admins.admin.failed_login_attempts == 0 and admins.admin.last_login_at == NOW  # type: ignore[union-attr]


async def test_unknown_user_and_wrong_password_give_the_same_message() -> None:
    admins = FakeAdmins(make_admin())
    with pytest.raises(AuthenticationFailed) as unknown:
        await login(admins).execute("nobody", "secret-password")
    with pytest.raises(AuthenticationFailed) as wrong:
        await login(admins).execute("root", "wrong")
    assert str(unknown.value) == str(wrong.value) == INVALID_CREDENTIALS


async def test_five_failures_lock_the_account_even_for_the_right_password() -> None:
    admins = FakeAdmins(make_admin())
    for _ in range(5):
        with pytest.raises(AuthenticationFailed):
            await login(admins).execute("root", "wrong")
    assert admins.admin.locked_until == NOW + WINDOW  # type: ignore[union-attr]
    with pytest.raises(AccountLocked):
        await login(admins).execute("root", "secret-password")


async def test_lock_expires_after_the_window() -> None:
    admins = FakeAdmins(make_admin(locked_until=NOW - timedelta(seconds=1)))
    await login(admins).execute("root", "secret-password")
    assert admins.admin.locked_until is None  # type: ignore[union-attr]


async def test_old_failures_do_not_count_toward_a_lock() -> None:
    admins = FakeAdmins(make_admin(failed_login_attempts=4, last_failed_login_at=NOW - timedelta(hours=1)))
    with pytest.raises(AuthenticationFailed):
        await login(admins).execute("root", "wrong")
    assert admins.admin.failed_login_attempts == 1 and admins.admin.locked_until is None  # type: ignore[union-attr]


async def test_inactive_admin_cannot_log_in() -> None:
    admins = FakeAdmins(make_admin(is_active=False))
    with pytest.raises(AuthenticationFailed):
        await login(admins).execute("root", "secret-password")
