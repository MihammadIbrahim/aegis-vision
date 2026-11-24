"""Use-case logic with fake parts: what is committed, what is rolled back, what is retried."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from src.application.use_cases.organization_registration_use_cases import (
    RegistrationForm,
    ResendRegistrationCode,
    SubmitOrganizationRegistration,
    VerifyOrganizationRegistration,
    mask_email,
    registration_code_email,
)
from src.domain.entities.organization import CompletedRegistration, PendingRegistration
from src.domain.exceptions.domain_errors import (
    Conflict,
    Gone,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_organization_registration_repo import (
    CompleteOutcome,
    CompleteResult,
    IssueOutcome,
    IssueResult,
    OtpLimits,
    SubmitOutcome,
    VerifyOutcome,
    VerifyResult,
)
from tests.fakes import FakeEmailSender

LIMITS = OtpLimits(ttl_seconds=300, max_attempts=5, resend_cooldown_seconds=60, hourly_limit=5)
FORM = RegistrationForm(
    organization_name="Lakeside Institute",
    campus_name="Main Campus",
    admin_full_name="Maryam Siddiqui",
    username="maryam.s",
    email="maryam@lakeside.example.com",
    phone="+92 300 1234567",
    password="Lakeside-pass-2026",
)
EXPIRES = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


class FakeTransaction:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class FakeHasher:
    def hash(self, password: str) -> str:
        return "$argon2id$v=19$fake-hash"

    def verify(self, password_hash: str, password: str) -> bool:
        return False


class FakeOtp:
    def new_code(self) -> str:
        return "042137"

    def hash_code(self, registration_id: UUID, code: str) -> str:
        return f"hash:{registration_id}:{code}"

    def recipient_digest(self, email: str) -> str:
        return "d" * 64


class FakeRepository:
    def __init__(
        self,
        *,
        submit: list[SubmitOutcome] | None = None,
        issue: IssueResult | None = None,
        verify: VerifyResult | None = None,
        complete: list[CompleteOutcome] | None = None,
    ) -> None:
        self.submit_outcomes = submit or [SubmitOutcome.CREATED]
        self.issue_result = issue or IssueResult(
            IssueOutcome.ISSUED, email="maryam@lakeside.example.com", registration_expires_at=EXPIRES
        )
        self.verify_result = verify or VerifyResult(VerifyOutcome.VERIFIED)
        self.complete_outcomes = complete or [CompleteOutcome.COMPLETED]
        self.submitted: list[PendingRegistration] = []
        self.issued: list[tuple[UUID, str]] = []
        self.verified: list[tuple[UUID, str]] = []
        self.completions: list[dict[str, object]] = []

    async def submit(self, registration: PendingRegistration) -> SubmitOutcome:
        self.submitted.append(registration)
        return self.submit_outcomes.pop(0)

    async def issue_otp(self, registration_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits):
        self.issued.append((registration_id, code_hash))
        return self.issue_result

    async def verify_otp(self, registration_id: UUID, code_hash: str) -> VerifyResult:
        self.verified.append((registration_id, code_hash))
        return self.verify_result

    async def complete(self, registration_id: UUID, **ids: object) -> CompleteResult:
        self.completions.append(ids)
        outcome = self.complete_outcomes.pop(0)
        if outcome is not CompleteOutcome.COMPLETED:
            return CompleteResult(outcome)
        return CompleteResult(
            outcome,
            CompletedRegistration(
                organization_code="ABCDEF48271930561",
                organization_name="Lakeside Institute",
                official_email="maryam@lakeside.example.com",
                official_phone="+923001234567",
                campus_name="Main Campus",
                admin_display_id=str(ids["display_id"]),
                admin_full_name="Maryam Siddiqui",
                admin_username="maryam.s",
                admin_email="maryam@lakeside.example.com",
                admin_phone="+923001234567",
                completed_at=EXPIRES,
            ),
        )


def _submit(repo: FakeRepository, email: FakeEmailSender, tx: FakeTransaction, codes=None):  # type: ignore[no-untyped-def]
    codes = iter(codes or ["AAAAAA00000000001", "AAAAAA00000000002", "AAAAAA00000000003"])
    return SubmitOrganizationRegistration(
        repo,
        FakeHasher(),
        FakeOtp(),
        email,
        tx,
        limits=LIMITS,
        registration_ttl_seconds=3600,
        organization_code_factory=lambda: next(codes),
    )


async def test_submit_without_an_email_provider_touches_nothing() -> None:
    repo, tx = FakeRepository(), FakeTransaction()
    with pytest.raises(ServiceUnavailable) as error:
        await _submit(repo, FakeEmailSender(configured=False), tx).execute(FORM)
    assert error.value.code == "EMAIL_DELIVERY_NOT_CONFIGURED"
    assert repo.submitted == [] and tx.events == []


async def test_invalid_form_is_refused_before_anything_is_stored() -> None:
    repo, tx = FakeRepository(), FakeTransaction()
    with pytest.raises(ValidationFailed):
        await _submit(repo, FakeEmailSender(), tx).execute(replace(FORM, organization_name=" Lakeside"))
    assert repo.submitted == [] and tx.events == []


async def test_submit_stores_only_the_password_hash_and_emails_the_code() -> None:
    repo, tx, email = FakeRepository(), FakeTransaction(), FakeEmailSender()
    sent = await _submit(repo, email, tx).execute(FORM)
    [stored] = repo.submitted
    assert stored.password_hash == "$argon2id$v=19$fake-hash"
    assert FORM.password not in repr(stored) and FORM.password not in repr(FORM)
    assert stored.phone == "+923001234567" and stored.ttl_seconds == 3600
    assert email.last_code() == "042137" and email.messages[0].to_email == "maryam@lakeside.example.com"
    assert sent.masked_email == "ma***@lakeside.example.com" and sent.registration_id == stored.id
    assert tx.events == ["commit"]


async def test_organization_code_collision_is_retried_with_a_new_code_for_the_same_registration() -> None:
    repo = FakeRepository(submit=[SubmitOutcome.ORGANIZATION_CODE_TAKEN, SubmitOutcome.CREATED])
    await _submit(repo, FakeEmailSender(), FakeTransaction()).execute(FORM)
    assert [r.organization_code for r in repo.submitted] == ["AAAAAA00000000001", "AAAAAA00000000002"]
    assert len({r.id for r in repo.submitted}) == 1


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (SubmitOutcome.ORGANIZATION_NAME_TAKEN, "ORGANIZATION_NAME_TAKEN"),
        (SubmitOutcome.USERNAME_TAKEN, "USERNAME_TAKEN"),
    ],
)
async def test_taken_name_or_username_sends_no_email(outcome: SubmitOutcome, code: str) -> None:
    email = FakeEmailSender()
    with pytest.raises(Conflict) as error:
        await _submit(FakeRepository(submit=[outcome]), email, FakeTransaction()).execute(FORM)
    assert error.value.code == code and email.messages == []


async def test_failed_email_rolls_everything_back() -> None:
    tx = FakeTransaction()
    with pytest.raises(UpstreamServiceFailed) as error:
        await _submit(FakeRepository(), FakeEmailSender(fail=True), tx).execute(FORM)
    assert error.value.code == "EMAIL_DELIVERY_FAILED"
    assert tx.events == ["rollback"]


async def test_hourly_limit_rolls_back_the_new_registration_and_says_when_to_retry() -> None:
    repo = FakeRepository(issue=IssueResult(IssueOutcome.HOURLY_LIMIT, retry_after_seconds=120))
    tx, email = FakeTransaction(), FakeEmailSender()
    with pytest.raises(RateLimited) as error:
        await _submit(repo, email, tx).execute(FORM)
    assert (error.value.code, error.value.retry_after) == ("OTP_HOURLY_LIMIT", 120)
    assert tx.events == ["rollback"] and email.messages == []


async def test_resend_of_an_expired_registration_keeps_the_erasure() -> None:
    tx = FakeTransaction()
    use_case = ResendRegistrationCode(
        FakeRepository(issue=IssueResult(IssueOutcome.REGISTRATION_EXPIRED)),
        FakeOtp(),
        FakeEmailSender(),
        tx,
        limits=LIMITS,
    )
    with pytest.raises(Gone):
        await use_case.execute(UUID(int=1))
    assert tx.events == ["commit"]


def _verify(repo: FakeRepository, tx: FakeTransaction, display_ids=None):  # type: ignore[no-untyped-def]
    ids = iter(display_ids or ["KQMTZX48271930561", "ABCDEF12345678901"])
    return VerifyOrganizationRegistration(repo, FakeOtp(), tx, display_id_factory=lambda: next(ids))


async def test_wrong_code_keeps_the_counted_attempt() -> None:
    repo, tx = FakeRepository(verify=VerifyResult(VerifyOutcome.INVALID_CODE, attempts_remaining=2)), FakeTransaction()
    with pytest.raises(VerificationFailed) as error:
        await _verify(repo, tx).execute(UUID(int=1), "000000")
    assert error.value.code == "OTP_INVALID" and "2 attempts left" in str(error.value)
    assert tx.events == ["commit"] and repo.completions == []


async def test_code_is_checked_against_its_keyed_hash() -> None:
    repo = FakeRepository()
    await _verify(repo, FakeTransaction()).execute(UUID(int=7), "042137")
    assert repo.verified == [(UUID(int=7), f"hash:{UUID(int=7)}:042137")]


async def test_display_id_collision_is_retried_with_the_same_record_ids() -> None:
    repo, tx = FakeRepository(complete=[CompleteOutcome.DISPLAY_ID_TAKEN, CompleteOutcome.COMPLETED]), FakeTransaction()
    done = await _verify(repo, tx).execute(UUID(int=1), "042137")
    assert [c["display_id"] for c in repo.completions] == ["KQMTZX48271930561", "ABCDEF12345678901"]
    assert repo.completions[0]["organization_id"] == repo.completions[1]["organization_id"]
    assert done.admin_display_id == "ABCDEF12345678901" and tx.events == ["commit"]


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (CompleteOutcome.EMAIL_ALREADY_REGISTERED, "EMAIL_ALREADY_REGISTERED"),
        (CompleteOutcome.ORGANIZATION_NAME_TAKEN, "ORGANIZATION_NAME_TAKEN"),
        (CompleteOutcome.USERNAME_TAKEN, "USERNAME_TAKEN"),
    ],
)
async def test_failed_completion_rolls_back_everything_including_the_code(outcome: CompleteOutcome, code: str) -> None:
    tx = FakeTransaction()
    with pytest.raises(Conflict) as error:
        await _verify(FakeRepository(complete=[outcome]), tx).execute(UUID(int=1), "042137")
    assert error.value.code == code and tx.events == ["rollback"]


async def test_malformed_code_is_refused_without_a_database_call() -> None:
    repo, tx = FakeRepository(), FakeTransaction()
    with pytest.raises(ValidationFailed):
        await _verify(repo, tx).execute(UUID(int=1), "12345")
    assert repo.verified == [] and tx.events == []


def test_email_contains_only_fixed_text_and_the_code() -> None:
    message = registration_code_email("maryam@example.com", "042137", 300)
    assert message.subject == "Your organization registration code"
    assert "Your verification code is: 042137" in message.text_body and "5 minutes" in message.text_body
    assert "<strong>042137</strong>" in message.html_body
    assert "042137" not in repr(message)


@pytest.mark.parametrize(
    ("email", "masked"),
    [("maryam@example.com", "ma***@example.com"), ("ab@example.com", "a***@example.com"), ("a@x.org", "a***@x.org")],
)
def test_mask_email(email: str, masked: str) -> None:
    assert mask_email(email) == masked
