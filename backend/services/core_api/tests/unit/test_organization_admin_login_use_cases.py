"""Sign-in logic with fake parts: what is checked, counted, committed or rolled back."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from src.application.use_cases.auth_use_cases import (
    INVALID_CREDENTIALS,
    AdminLoginForm,
    StartOrganizationAdminLogin,
    VerifyOrganizationAdminLogin,
    login_code_email,
)
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    Conflict,
    PermissionDenied,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_admin_login_repo import (
    LoginAccount,
    LoginIssueOutcome,
    LoginIssueResult,
    LoginVerifyOutcome,
    LoginVerifyResult,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from tests.fakes import FakeEmailSender

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
USER = UUID(int=42)
LIMITS = OtpLimits(ttl_seconds=300, max_attempts=5, resend_cooldown_seconds=60, hourly_limit=5)
FORM = AdminLoginForm(
    organization_name="Lakeside Institute",
    organization_id="ABCDEF12345678901",
    email="maryam@example.com",
    password="Lakeside-pass-2026",
)


class FakeTransaction:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class FakeHasher:
    def __init__(self, *, matches: bool) -> None:
        self.matches = matches
        self.checked: list[str] = []

    def hash(self, password: str) -> str:
        return "$argon2id$fake"

    def verify(self, password_hash: str, password: str) -> bool:
        self.checked.append(password_hash)
        return self.matches and password_hash == "$argon2id$real"


class FakeOtp:
    def new_code(self) -> str:
        return "042137"

    def hash_login_code(self, login_id: UUID, code: str) -> str:
        return f"login:{login_id}:{code}"


class FakeRepository:
    def __init__(
        self,
        account: LoginAccount | None = None,
        *,
        lock_on_failure: bool = False,
        issue: LoginIssueResult | None = None,
        verify: LoginVerifyResult | None = None,
        pending_hash: str | None = None,
    ) -> None:
        self.account = account
        self.pending_hash = pending_hash
        self.lock_on_failure = lock_on_failure
        self.issue = issue or LoginIssueResult(
            LoginIssueOutcome.ISSUED, email="maryam@example.com", login_expires_at=NOW + timedelta(minutes=10)
        )
        self.verify_result = verify or LoginVerifyResult(LoginVerifyOutcome.VERIFIED, user_id=USER)
        self.calls: list[str] = []

    async def find_account(self, organization_name: str, organization_code: str, email: str):
        self.calls.append("find")
        return self.account

    async def find_pending_campus_request(self, organization_name: str, organization_code: str, email: str):
        self.calls.append("pending")
        return self.pending_hash

    async def record_failure(self, user_id: UUID, *, max_attempts: int, window_seconds: int):
        self.calls.append("failure")
        return NOW + timedelta(minutes=15) if self.lock_on_failure else None

    async def start(self, user_id: UUID, login_id: UUID, *, ttl_seconds: int) -> None:
        self.calls.append("start")

    async def issue_otp(self, login_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits):
        self.calls.append("issue")
        return self.issue

    async def verify_otp(self, login_id: UUID, code_hash: str):
        self.calls.append(f"verify:{code_hash}")
        return self.verify_result


def account(*, eligible: bool = True, locked_until: datetime | None = None) -> LoginAccount:
    return LoginAccount(user_id=USER, password_hash="$argon2id$real", eligible=eligible, locked_until=locked_until)


def _start(repo, hasher, email=None, tx=None):  # type: ignore[no-untyped-def]
    return StartOrganizationAdminLogin(
        repo,
        hasher,
        FakeOtp(),
        email or FakeEmailSender(),
        tx or FakeTransaction(),
        limits=LIMITS,
        login_ttl_seconds=600,
        max_failed_attempts=5,
        lockout_seconds=900,
        clock=lambda: NOW,
    )


async def test_no_email_provider_means_nothing_is_checked() -> None:
    repo = FakeRepository(account())
    with pytest.raises(ServiceUnavailable):
        await _start(repo, FakeHasher(matches=True), email=FakeEmailSender(configured=False)).execute(FORM)
    assert repo.calls == []


async def test_unknown_account_still_costs_a_password_check() -> None:
    hasher = FakeHasher(matches=True)
    with pytest.raises(AuthenticationFailed) as error:
        await _start(FakeRepository(None), hasher).execute(FORM)
    assert str(error.value) == INVALID_CREDENTIALS and error.value.code == "INVALID_CREDENTIALS"
    assert len(hasher.checked) == 1 and hasher.checked[0] != "$argon2id$real"


async def test_verified_campus_applicant_is_told_approval_is_pending_after_a_correct_password() -> None:
    # Decision L: no session and no code, but a clear answer, once the password is right.
    tx = FakeTransaction()
    with pytest.raises(PermissionDenied) as error:
        await _start(FakeRepository(None, pending_hash="$argon2id$real"), FakeHasher(matches=True), tx=tx).execute(
            FORM
        )
    assert error.value.code == "ADMIN_APPROVAL_PENDING"
    assert "commit" not in tx.events

    with pytest.raises(AuthenticationFailed):  # wrong password: the usual answer, nothing revealed
        await _start(FakeRepository(None, pending_hash="$argon2id$real"), FakeHasher(matches=False)).execute(FORM)


async def test_locked_account_is_refused_without_checking_the_password() -> None:
    hasher = FakeHasher(matches=True)
    with pytest.raises(AccountLocked) as error:
        await _start(FakeRepository(account(locked_until=NOW + timedelta(seconds=90))), hasher).execute(FORM)
    assert error.value.retry_after == 90 and hasher.checked == []


async def test_wrong_password_is_counted_and_committed() -> None:
    repo, tx = FakeRepository(account()), FakeTransaction()
    with pytest.raises(AuthenticationFailed):
        await _start(repo, FakeHasher(matches=False), tx=tx).execute(FORM)
    assert repo.calls == ["find", "failure"] and tx.events == ["commit"]


async def test_the_failure_that_reaches_the_limit_locks_the_account() -> None:
    with pytest.raises(AccountLocked) as error:
        await _start(FakeRepository(account(), lock_on_failure=True), FakeHasher(matches=False)).execute(FORM)
    assert error.value.code == "ACCOUNT_LOCKED" and error.value.retry_after == 900


async def test_inactive_account_with_the_right_password_gets_the_generic_answer() -> None:
    repo, tx = FakeRepository(account(eligible=False)), FakeTransaction()
    with pytest.raises(AuthenticationFailed) as error:
        await _start(repo, FakeHasher(matches=True), tx=tx).execute(FORM)
    assert str(error.value) == INVALID_CREDENTIALS
    assert repo.calls == ["find"] and tx.events == ["rollback"]


async def test_right_credentials_start_a_sign_in_and_email_a_code() -> None:
    repo, tx, email = FakeRepository(account()), FakeTransaction(), FakeEmailSender()
    sent = await _start(repo, FakeHasher(matches=True), email=email, tx=tx).execute(FORM)
    assert repo.calls == ["find", "start", "issue"] and tx.events == ["commit"]
    assert email.last_code() == "042137" and sent.masked_email == "ma***@example.com"


async def test_failed_email_keeps_nothing() -> None:
    tx = FakeTransaction()
    with pytest.raises(UpstreamServiceFailed):
        await _start(FakeRepository(account()), FakeHasher(matches=True), FakeEmailSender(fail=True), tx).execute(FORM)
    assert tx.events == ["rollback"]


async def test_refused_code_issue_keeps_nothing() -> None:
    repo = FakeRepository(account(), issue=LoginIssueResult(LoginIssueOutcome.HOURLY_LIMIT, retry_after_seconds=60))
    tx = FakeTransaction()
    with pytest.raises(RateLimited) as error:
        await _start(repo, FakeHasher(matches=True), tx=tx).execute(FORM)
    assert error.value.code == "OTP_HOURLY_LIMIT" and tx.events == ["rollback"]


async def test_right_code_returns_the_account_and_commits() -> None:
    repo, tx = FakeRepository(), FakeTransaction()
    assert await VerifyOrganizationAdminLogin(repo, FakeOtp(), tx).execute(UUID(int=7), "042137") == USER
    assert repo.calls == [f"verify:login:{UUID(int=7)}:042137"] and tx.events == ["commit"]


@pytest.mark.parametrize(
    ("outcome", "error_type", "code", "events"),
    [
        (LoginVerifyOutcome.INVALID_CODE, VerificationFailed, "OTP_INVALID", ["commit"]),
        (LoginVerifyOutcome.ALREADY_COMPLETED, Conflict, "LOGIN_ALREADY_COMPLETED", ["rollback"]),
        (LoginVerifyOutcome.NOT_ELIGIBLE, AuthenticationFailed, "INVALID_CREDENTIALS", ["rollback"]),
        (LoginVerifyOutcome.CODE_EXPIRED, VerificationFailed, "OTP_EXPIRED", ["rollback"]),
    ],
)
async def test_codes_that_do_not_verify_give_no_account(outcome, error_type, code, events) -> None:  # type: ignore[no-untyped-def]
    tx = FakeTransaction()
    repo = FakeRepository(verify=LoginVerifyResult(outcome, attempts_remaining=3))
    with pytest.raises(error_type) as error:
        await VerifyOrganizationAdminLogin(repo, FakeOtp(), tx).execute(UUID(int=7), "042137")
    assert error.value.code == code and tx.events == events


async def test_malformed_code_makes_no_database_call() -> None:
    repo = FakeRepository()
    with pytest.raises(ValidationFailed):
        await VerifyOrganizationAdminLogin(repo, FakeOtp(), FakeTransaction()).execute(UUID(int=7), "12345")
    assert repo.calls == []


def test_sign_in_email_has_only_fixed_text_and_the_code() -> None:
    message = login_code_email("maryam@example.com", "042137", 300)
    assert message.subject == "Your sign-in code" and "Your verification code is: 042137" in message.text_body
    assert "Lakeside" not in message.text_body and "042137" not in repr(message)


def _settings(**extra: object) -> Settings:
    return Settings(
        database_url="x",
        platform_database_url="x",
        migration_database_url="x",
        jwt_secret_key="j" * 40,
        _env_file=None,  # type: ignore[call-arg]
        **extra,  # type: ignore[arg-type]
    )


def test_sign_in_settings_have_safe_defaults_and_must_outlive_the_code() -> None:
    settings = _settings()
    assert (settings.org_login_max_attempts, settings.org_login_lockout_minutes) == (5, 15)
    assert (settings.org_login_expire_minutes, settings.org_login_ip_limit_per_hour) == (10, 60)
    with pytest.raises(ValueError, match="ORG_LOGIN_EXPIRE_MINUTES must be longer"):
        _settings(org_login_expire_minutes=5, otp_expire_seconds=300)
