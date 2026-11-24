import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from src.application.use_cases.password_reset_use_cases import (
    CompletePasswordReset,
    RequestPasswordReset,
)
from src.application.use_cases.supervisor_use_cases import SubmitSupervisor
from src.domain.exceptions.domain_errors import UpstreamServiceFailed, ValidationFailed
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    IssueResult,
    ResetIdentity,
)
from src.domain.value_objects.system_id import (
    generate_supervisor_id,
    is_staff_id,
    is_supervisor_id,
)
from tests.fakes import FakeEmailSender

LIMITS = OtpLimits(ttl_seconds=300, max_attempts=5, resend_cooldown_seconds=60, hourly_limit=5)
LATER = datetime(2026, 10, 27, 12, 0, tzinfo=UTC) + timedelta(minutes=30)


def test_supervisor_ids_are_27_characters_and_random() -> None:
    ids = {generate_supervisor_id() for _ in range(3000)}
    assert len(ids) == 3000
    assert all(re.fullmatch(r"[A-Z]{6}[0-9]{21}", i) for i in ids)
    assert all(is_supervisor_id(i) and not is_staff_id(i) for i in ids)
    assert len({i[0] for i in ids}) == 26 and len({i[-1] for i in ids}) == 10
    assert not is_supervisor_id("ABCDEF12345678901") and not is_supervisor_id("abcdef123456789012345678901")


class Tx:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class Otp:
    def new_code(self) -> str:
        return "042137"

    def hash_purpose_code(self, purpose: str, subject_id: UUID, code: str) -> str:
        return f"{purpose}:{subject_id}:{code}"

    def hash_token(self, purpose: str, token: str) -> str:
        return f"{purpose}:{token}"

    def recipient_digest(self, email: str) -> str:
        return "d" * 64


class Repo:
    def __init__(self, *, email: str | None = "maryam@example.com", identity: ResetIdentity | None = None) -> None:
        self.email = email
        self.identity = identity or ResetIdentity(FlowOutcome.OK, "maryam.s", "maryam@example.com")
        self.calls: list[str] = []

    async def request_password_reset(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.calls.append("request")

    async def issue_reset_otp(self, *args) -> IssueResult:  # type: ignore[no-untyped-def]
        self.calls.append("issue")
        return IssueResult(FlowOutcome.ISSUED, email=self.email, expires_at=LATER)

    async def submit_supervisor(self, form) -> FlowOutcome:  # type: ignore[no-untyped-def]
        self.calls.append("submit")
        return FlowOutcome.CREATED

    async def issue_supervisor_otp(self, *args) -> IssueResult:  # type: ignore[no-untyped-def]
        return IssueResult(FlowOutcome.ISSUED, email="sara@example.com", expires_at=LATER)

    async def reset_identity(self, *args) -> ResetIdentity:  # type: ignore[no-untyped-def]
        return self.identity

    async def complete_reset(self, *args) -> FlowOutcome:  # type: ignore[no-untyped-def]
        self.calls.append("complete")
        return FlowOutcome.COMPLETED


def _reset(repo: Repo, sender: FakeEmailSender, tx: Tx) -> RequestPasswordReset:
    return RequestPasswordReset(repo, Otp(), sender, tx, limits=LIMITS, ttl_seconds=1800)  # type: ignore[arg-type]


@pytest.mark.parametrize("email", ["maryam@example.com", None])
async def test_reset_answer_does_not_depend_on_whether_the_account_exists(email: str | None) -> None:
    sender, tx = FakeEmailSender(), Tx()
    started = await _reset(Repo(email=email), sender, tx).execute("ABCDEF12345678901", "maryam@example.com")
    assert started.code_expires_in_seconds == 300 and tx.events == ["commit"]
    assert len(sender.messages) == (1 if email else 0)


async def test_failed_reset_email_is_not_revealed() -> None:
    tx = Tx()
    started = await _reset(Repo(), FakeEmailSender(fail=True), tx).execute("ABCDEF12345678901", "maryam@example.com")
    assert started.resend_available_in_seconds == 60 and tx.events == ["commit"]


async def test_new_password_follows_the_rules_and_must_be_confirmed() -> None:
    use_case = CompletePasswordReset(Repo(), _Hasher(), Otp(), Tx())  # type: ignore[arg-type]
    for new, confirm in (
        ("Short-1", "Short-1"),
        ("Good-pass-2027", "Other-pass-2027"),
        ("maryam.s-2027-abc", "maryam.s-2027-abc"),
    ):
        with pytest.raises(ValidationFailed):
            await use_case.execute(UUID(int=1), "token", new, confirm)


class _Hasher:
    def hash(self, password: str) -> str:
        return "$argon2id$fake"

    def verify(self, password_hash: str, password: str) -> bool:
        return False


async def test_supervisor_form_failure_to_email_keeps_nothing() -> None:
    tx = Tx()
    use_case = SubmitSupervisor(
        Repo(), _Hasher(), Otp(), FakeEmailSender(fail=True), tx, limits=LIMITS, ttl_seconds=1800
    )  # type: ignore[arg-type]
    with pytest.raises(UpstreamServiceFailed):
        await use_case.execute(
            authorization_id=UUID(int=1),
            full_name="Sara Khan",
            username="sara.khan",
            email="sara@example.com",
            phone="+923331112233",
            password="Supervisor-pass-2026",
        )
    assert tx.events == ["rollback"]
