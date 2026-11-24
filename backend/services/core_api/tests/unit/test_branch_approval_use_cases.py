"""Use-case rules tested with in-memory fakes (no database)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.application.use_cases.branch_approval_use_cases import (
    ApproveCampusRequest,
    RejectCampusRequest,
)
from src.domain.entities.branch import Campus
from src.domain.entities.branch_request import CampusRegistrationRequest
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    CurrentPasswordIncorrect,
    Gone,
    NotFound,
    PermissionDenied,
)
from src.domain.repositories.i_branch_repo import ApprovalResult, CampusRequestFilter, CampusRequestPage
from src.domain.value_objects.enums import AccountStatus, CampusRequestStatus, UserRole

NOW = datetime(2026, 10, 26, tzinfo=UTC)


class FakeCampuses:
    def __init__(self, requests: list[CampusRegistrationRequest], outcomes: list[str] | None = None) -> None:
        self.requests = {r.id: r for r in requests}
        self.added: list[Campus] = []
        self.saved: list[CampusRegistrationRequest] = []
        self.approvals: list[dict] = []
        self.outcomes = outcomes or ["APPROVED"]

    async def approve_request(
        self, request_id: UUID, *, campus_id: UUID, user_id: UUID, display_id: str
    ) -> ApprovalResult:
        self.approvals.append({"request_id": request_id, "campus_id": campus_id, "user_id": user_id,
                               "display_id": display_id})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return ApprovalResult(outcome, NOW if outcome == "APPROVED" else None)

    async def add_campus(self, campus: Campus) -> None:
        self.added.append(campus)

    async def list_pending_requests(self, organization_id: UUID) -> list[CampusRegistrationRequest]:
        return [r for r in self.requests.values() if r.organization_id == organization_id]

    async def get_request(self, request_id: UUID, organization_id: UUID) -> CampusRegistrationRequest | None:
        request = self.requests.get(request_id)
        return request if request and request.organization_id == organization_id else None

    async def save_request_decision(self, request: CampusRegistrationRequest) -> None:
        self.saved.append(request)

    async def search_requests(self, organization_id: UUID, where: CampusRequestFilter) -> CampusRequestPage:
        items = [r for r in self.requests.values() if r.organization_id == organization_id]
        return CampusRequestPage(items=items, total=len(items))


class FakeGrants:
    """The Main Admin's password + code confirmation (decision H)."""

    def __init__(self, outcome: str = "CONSUMED") -> None:
        self.outcome = outcome
        self.consumed: list[UUID] = []

    async def consume_step_up(self, grant_id: UUID) -> str:
        self.consumed.append(grant_id)
        return self.outcome


class FakePassword:
    def __init__(self, correct: bool = True) -> None:
        self.correct = correct
        self.checked: list[str] = []

    async def confirm(self, current_password: str) -> None:
        self.checked.append(current_password)
        if not self.correct:
            raise CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, UUID | None, dict]] = []

    async def record(self, actor, action, target_type, target_id, metadata, *, outcome="SUCCESS") -> None:  # type: ignore[no-untyped-def]
        self.records.append((action, target_type, target_id, metadata))


def approve(campuses: FakeCampuses, grants: FakeGrants | None = None, **kwargs) -> ApproveCampusRequest:  # type: ignore[no-untyped-def]
    return ApproveCampusRequest(campuses, confirmations=grants or FakeGrants(), **kwargs)


def reject(campuses: FakeCampuses, password: FakePassword | None = None, **kwargs) -> RejectCampusRequest:  # type: ignore[no-untyped-def]
    return RejectCampusRequest(campuses, confirmation=password or FakePassword(), **kwargs)


def admin(org: UUID, *, first: bool) -> User:
    return User(
        id=uuid4(),
        organization_id=org,
        campus_id=uuid4(),
        role=UserRole.ADMIN,
        status=AccountStatus.ACTIVE,
        full_name="Admin",
        email="admin@example.test",
        display_id="ADM-00000000001",
        is_first_admin=first,
    )


def pending_request(org: UUID) -> CampusRegistrationRequest:
    return CampusRegistrationRequest(
        id=uuid4(),
        organization_id=org,
        proposed_campus_name="North Campus",
        applicant_name="Hamza Raza",
        applicant_email="hamza@example.test",
        email_verified=True,
        status=CampusRequestStatus.PENDING,
        submitted_at=NOW,
    )


async def test_first_admin_approval_creates_campus_and_non_first_admin() -> None:
    org = uuid4()
    request = pending_request(org)
    campuses = FakeCampuses([request])

    grants, audit = FakeGrants(), FakeAudit()
    authorization = uuid4()
    result = await approve(campuses, grants, clock=lambda: NOW, audit=audit).execute(
        admin(org, first=True), request.id, authorization
    )

    # One call creates the campus and the applicant's Admin account (migration 0021).
    assert result.status is CampusRequestStatus.APPROVED
    [call] = campuses.approvals
    assert call["request_id"] == request.id and result.approved_campus_id == call["campus_id"]
    assert call["display_id"].startswith("ADM-")
    assert grants.consumed == [authorization]  # decision H: the confirmation is used up
    [(action, target_type, target_id, details)] = audit.records
    assert (action, target_type, target_id) == ("CAMPUS_REQUEST_APPROVED", "CAMPUS_REQUEST", request.id)
    assert details["campus_name"] == "North Campus"


async def test_approval_retries_a_colliding_display_id_and_reports_conflicts() -> None:
    org = uuid4()
    request = pending_request(org)
    campuses = FakeCampuses([request], ["DISPLAY_ID_TAKEN", "APPROVED"])
    await approve(campuses).execute(admin(org, first=True), request.id, uuid4())
    assert len(campuses.approvals) == 2 and campuses.approvals[0]["display_id"] != ""

    main_admin = admin(org, first=True)
    with pytest.raises(Conflict):
        await approve(FakeCampuses([request], ["USERNAME_TAKEN"])).execute(main_admin, request.id, uuid4())
    with pytest.raises(Gone):
        await approve(FakeCampuses([request], ["EXPIRED"])).execute(main_admin, request.id, uuid4())


async def test_other_admin_cannot_approve_or_reject_and_nothing_is_written() -> None:
    org = uuid4()
    request = pending_request(org)
    campuses = FakeCampuses([request])

    grants, password = FakeGrants(), FakePassword()
    with pytest.raises(PermissionDenied):
        await approve(campuses, grants).execute(admin(org, first=False), request.id, uuid4())
    with pytest.raises(PermissionDenied):
        await reject(campuses, password).execute(admin(org, first=False), request.id, "pw")

    assert campuses.approvals == [] and campuses.saved == []
    assert grants.consumed == [] and password.checked == []


async def test_first_admin_cannot_review_another_organizations_request() -> None:
    request = pending_request(uuid4())
    with pytest.raises(NotFound):
        await approve(FakeCampuses([request])).execute(admin(uuid4(), first=True), request.id, uuid4())


@pytest.mark.parametrize(
    ("outcome", "error", "code"),
    [
        ("NOT_FOUND", NotFound, "AUTHORIZATION_NOT_FOUND"),
        ("NOT_VERIFIED", PermissionDenied, "AUTHORIZATION_NOT_VERIFIED"),
        ("ALREADY_USED", Conflict, "AUTHORIZATION_USED"),
        ("EXPIRED", Gone, "AUTHORIZATION_EXPIRED"),
    ],
)
async def test_approval_needs_a_usable_confirmation(outcome: str, error: type, code: str) -> None:
    org = uuid4()
    request = pending_request(org)
    campuses = FakeCampuses([request])
    with pytest.raises(error) as raised:
        await approve(campuses, FakeGrants(outcome)).execute(admin(org, first=True), request.id, uuid4())
    assert raised.value.code == code
    assert campuses.approvals == []  # nothing is created


async def test_expired_request_is_refused_before_the_confirmation_is_used() -> None:
    org = uuid4()
    request = replace(pending_request(org), email_verified_at=NOW - timedelta(hours=73))
    grants = FakeGrants()
    with pytest.raises(Gone) as raised:
        await approve(FakeCampuses([request]), grants, clock=lambda: NOW).execute(
            admin(org, first=True), request.id, uuid4()
        )
    assert raised.value.code == "CAMPUS_REQUEST_EXPIRED" and grants.consumed == []


async def test_rejection_needs_the_password_and_erases_the_applicant_details() -> None:
    org = uuid4()
    request = replace(pending_request(org), applicant_username="hamza.r", applicant_phone="+923001234567")
    campuses, audit = FakeCampuses([request]), FakeAudit()

    with pytest.raises(CurrentPasswordIncorrect):
        await reject(campuses, FakePassword(correct=False)).execute(admin(org, first=True), request.id, "wrong")
    assert campuses.saved == []

    rejected = await reject(campuses, audit=audit, clock=lambda: NOW).execute(
        admin(org, first=True), request.id, "right"
    )
    assert rejected.status is CampusRequestStatus.REJECTED and rejected.details_erased_at == NOW
    [saved] = campuses.saved
    assert (saved.proposed_campus_name, saved.applicant_name, saved.applicant_email) == (None, None, None)
    assert (saved.applicant_username, saved.applicant_phone) == (None, None)
    [(action, _, target_id, details)] = audit.records
    assert action == "CAMPUS_REQUEST_REJECTED" and target_id == request.id
    assert "Hamza" not in str(details) and "hamza" not in str(details)  # decision I: nothing kept
