from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from src.domain.exceptions.domain_errors import InvalidStateTransition
from src.domain.value_objects.enums import CampusRequestStatus

# A verified request must be decided within this time of email verification (decision I).
DECISION_WINDOW = timedelta(hours=72)


@dataclass(frozen=True, slots=True)
class CampusRegistrationRequest:
    """A request to register a new campus Admin under an existing organization (Section 8).

    A rejected request keeps no applicant details (decision I; migration 0024): the proposed campus
    name, name, email, username and phone are erased and ``details_erased_at`` says when, so the
    request history shows only that a request was rejected, and when."""

    id: UUID
    organization_id: UUID
    proposed_campus_name: str | None
    applicant_name: str | None
    applicant_email: str | None
    email_verified: bool
    status: CampusRequestStatus
    submitted_at: datetime
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    approved_campus_id: UUID | None = None
    applicant_username: str | None = None
    applicant_phone: str | None = None
    email_verified_at: datetime | None = None
    details_erased_at: datetime | None = None
    reviewed_by_name: str | None = None

    @property
    def decision_deadline(self) -> datetime | None:
        """When an undecided verified request expires (None for requests from before migration 0021)."""
        return None if self.email_verified_at is None else self.email_verified_at + DECISION_WINDOW

    def approve(self, reviewer_id: UUID, campus_id: UUID, at: datetime) -> "CampusRegistrationRequest":
        self._ensure_pending()
        if not self.email_verified:
            raise InvalidStateTransition("The applicant's official email has not been verified.")
        return replace(
            self,
            status=CampusRequestStatus.APPROVED,
            reviewed_by=reviewer_id,
            reviewed_at=at,
            approved_campus_id=campus_id,
        )

    def reject(self, reviewer_id: UUID, at: datetime) -> "CampusRegistrationRequest":
        """Rejected, with the applicant's details erased (decision I)."""
        self._ensure_pending()
        return replace(
            self,
            status=CampusRequestStatus.REJECTED,
            reviewed_by=reviewer_id,
            reviewed_at=at,
            proposed_campus_name=None,
            applicant_name=None,
            applicant_email=None,
            applicant_username=None,
            applicant_phone=None,
            details_erased_at=at,
        )

    def is_expired(self, now: datetime) -> bool:
        """Still pending past its 72-hour decision window: it can never be approved (decision I)."""
        deadline = self.decision_deadline
        return self.status is CampusRequestStatus.PENDING and deadline is not None and deadline <= now

    def _ensure_pending(self) -> None:
        if self.status is not CampusRequestStatus.PENDING:
            raise InvalidStateTransition(f"This request has already been {self.status.lower()}.")
