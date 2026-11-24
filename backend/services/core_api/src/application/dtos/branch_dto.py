from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.entities.branch_request import CampusRegistrationRequest
from src.domain.repositories.i_branch_repo import CampusRequestPage
from src.domain.value_objects.enums import CampusRequestStatus


class CampusRequestResponse(BaseModel):
    """Only the applicant and registration details needed to review the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    # Null once a rejection erased the applicant's details (decision I; see ``details_erased_at``).
    proposed_campus_name: str | None
    applicant_name: str | None
    applicant_email: str | None
    email_verified: bool
    status: CampusRequestStatus
    submitted_at: datetime
    reviewed_at: datetime | None
    applicant_username: str | None
    applicant_phone: str | None
    email_verified_at: datetime | None
    decision_deadline: datetime | None

    @classmethod
    def from_entity(cls, request: CampusRegistrationRequest) -> "CampusRequestResponse":
        return cls(
            id=request.id,
            proposed_campus_name=request.proposed_campus_name,
            applicant_name=request.applicant_name,
            applicant_email=request.applicant_email,
            email_verified=request.email_verified,
            status=request.status,
            submitted_at=request.submitted_at,
            reviewed_at=request.reviewed_at,
            applicant_username=request.applicant_username,
            applicant_phone=request.applicant_phone,
            email_verified_at=request.email_verified_at,
            decision_deadline=request.decision_deadline,
        )


class CampusRequestRecordResponse(CampusRequestResponse):
    """A request in the history (page 15, items 10–11): also who decided it and whether its details
    were erased by the rejection (decision I)."""

    details_erased_at: datetime | None
    reviewed_by_name: str | None = Field(description="The Main Admin who decided; null when not visible")

    @classmethod
    def from_entity(cls, request: CampusRegistrationRequest) -> "CampusRequestRecordResponse":
        base = CampusRequestResponse.from_entity(request).model_dump()
        return cls(**base, details_erased_at=request.details_erased_at, reviewed_by_name=request.reviewed_by_name)


class CampusRequestHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[CampusRequestRecordResponse]
    total: int
    limit: int
    offset: int
    counts: dict[str, int] = Field(description="Requests per status (PENDING, APPROVED, REJECTED) for the search")

    @classmethod
    def of(cls, page: CampusRequestPage, limit: int, offset: int) -> "CampusRequestHistoryResponse":
        return cls(
            items=[CampusRequestRecordResponse.from_entity(r) for r in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
            counts=page.counts,
        )


class ApproveCampusRequestBody(BaseModel):
    """The confirmation from ``POST /campus-requests/authorization/{id}/verify`` (decision H)."""

    model_config = ConfigDict(extra="forbid")

    authorization_id: UUID


class RejectCampusRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
