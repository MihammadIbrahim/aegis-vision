from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.branch import Campus
from src.domain.entities.branch_request import CampusRegistrationRequest
from src.domain.value_objects.enums import CampusRequestStatus


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """``outcome`` is ``APPROVED`` or the reason nothing was created (see migration 0021)."""

    outcome: str
    reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CampusRequestFilter:
    """Request history (Org Admin page 15, items 10–11): by status, and a search over the applicant's
    name, email and the proposed campus name (rejected requests have none left to search, decision I)."""

    status: CampusRequestStatus | None = None
    text: str | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class CampusRequestPage:
    items: list[CampusRegistrationRequest]
    total: int
    counts: dict[str, int] = field(default_factory=dict)  # per status, ignoring the status filter


class ICampusRepository(Protocol):
    async def add_campus(self, campus: Campus) -> None: ...

    async def list_pending_requests(self, organization_id: UUID) -> list[CampusRegistrationRequest]: ...

    async def get_request(self, request_id: UUID, organization_id: UUID) -> CampusRegistrationRequest | None: ...

    async def save_request_decision(self, request: CampusRegistrationRequest) -> None: ...

    async def search_requests(self, organization_id: UUID, where: CampusRequestFilter) -> CampusRequestPage: ...

    async def approve_request(
        self, request_id: UUID, *, campus_id: UUID, user_id: UUID, display_id: str
    ) -> ApprovalResult: ...
