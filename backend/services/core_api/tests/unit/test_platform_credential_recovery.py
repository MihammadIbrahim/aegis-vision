"""RecoverPlatformCredentials with in-memory fakes (no database)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.use_cases.platform_admin_use_cases import (
    LOGIN_NORMAL,
    LOGIN_RECOVERY,
    RecoverPlatformCredentials,
)
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    CurrentPasswordIncorrect,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.value_objects.recovery_credentials import RecoveryCredentials
from tests.unit.test_platform_recovery_login import (
    FakeAdmins,
    FakeAudit,
    FakeHasher,
    primary,
)

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
RECOVERY = RecoveryCredentials(username="emergency", password="Emergency-pass-2026")


def recover(admins: FakeAdmins, audit: FakeAudit, recovery: RecoveryCredentials | None = RECOVERY):
    return RecoverPlatformCredentials(
        admins, FakeHasher(), recovery, max_attempts=5, lockout=timedelta(minutes=15), audit=audit, clock=lambda: NOW
    )


async def run(use_case, admin_id, method=LOGIN_RECOVERY, **overrides):
    args = {
        "recovery_username": "emergency",
        "recovery_password": "Emergency-pass-2026",
        "new_username": "renewed.admin",
        "new_password": "Restored-pass-2027",
        "confirm_new_password": "Restored-pass-2027",
    } | overrides
    return await use_case.execute(admin_id, method, **args)


async def test_success_changes_both_with_one_version_step() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    updated = await run(recover(admins, audit), admins.admin.id)
    assert (updated.username, updated.password_hash) == ("renewed.admin", "hashed:Restored-pass-2027")
    assert updated.token_version == 3  # was 2
    assert audit.events[-1].action == "CREDENTIALS_RECOVERED" and audit.events[-1].outcome == "SUCCESS"


async def test_needs_a_recovery_session_and_recovery_enabled() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    with pytest.raises(PermissionDenied):
        await run(recover(admins, audit), admins.admin.id, method=LOGIN_NORMAL)
    with pytest.raises(PermissionDenied):
        await run(recover(admins, audit, recovery=None), admins.admin.id)


async def test_only_the_primary_account() -> None:
    admins, audit = FakeAdmins(primary(is_primary=False)), FakeAudit()
    with pytest.raises(PermissionDenied):
        await run(recover(admins, audit), admins.admin.id)


async def test_new_password_cannot_equal_the_recovery_password() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    with pytest.raises(ValidationFailed, match="emergency recovery password"):
        await run(
            recover(admins, audit),
            admins.admin.id,
            new_password="Emergency-pass-2026",
            confirm_new_password="Emergency-pass-2026",
        )


async def test_wrong_reentry_counts_and_eventually_locks() -> None:
    admins, audit = FakeAdmins(primary()), FakeAudit()
    for _ in range(5):
        with pytest.raises(CurrentPasswordIncorrect):
            await run(recover(admins, audit), admins.admin.id, recovery_password="wrong")
    with pytest.raises(AccountLocked):
        await run(recover(admins, audit), admins.admin.id)
    assert admins.admin.username == "boss"


async def test_inputs_are_checked_before_counting_failures() -> None:
    admins, audit = FakeAdmins(replace(primary(), failed_login_attempts=0)), FakeAudit()
    with pytest.raises(ValidationFailed):
        await run(recover(admins, audit), admins.admin.id, recovery_password="wrong", confirm_new_password="x")
    assert admins.admin.failed_login_attempts == 0 and uuid4() != admins.admin.id
