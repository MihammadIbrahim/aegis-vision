from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.domain.entities.branch_request import CampusRegistrationRequest
from src.domain.exceptions.domain_errors import InvalidStateTransition
from src.domain.value_objects.enums import CampusRequestStatus

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def make_request(*, verified: bool = True) -> CampusRegistrationRequest:
    return CampusRegistrationRequest(
        id=uuid4(),
        organization_id=uuid4(),
        proposed_campus_name="North Campus",
        applicant_name="Hamza Raza",
        applicant_email="hamza@example.test",
        email_verified=verified,
        status=CampusRequestStatus.PENDING,
        submitted_at=NOW,
    )


def test_approve_records_reviewer_and_campus() -> None:
    reviewer, campus = uuid4(), uuid4()
    approved = make_request().approve(reviewer, campus, NOW)
    assert approved.status is CampusRequestStatus.APPROVED
    assert approved.reviewed_by == reviewer
    assert approved.approved_campus_id == campus


def test_cannot_approve_unverified_email() -> None:
    with pytest.raises(InvalidStateTransition):
        make_request(verified=False).approve(uuid4(), uuid4(), NOW)


def test_decided_request_cannot_be_decided_again() -> None:
    approved = make_request().approve(uuid4(), uuid4(), NOW)
    with pytest.raises(InvalidStateTransition):
        approved.approve(uuid4(), uuid4(), NOW)
    with pytest.raises(InvalidStateTransition):
        approved.reject(uuid4(), NOW)


def test_reject_does_not_need_verified_email() -> None:
    rejected = make_request(verified=False).reject(uuid4(), NOW)
    assert rejected.status is CampusRequestStatus.REJECTED
