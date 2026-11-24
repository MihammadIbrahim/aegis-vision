from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AdminDashboardFacts:
    """Only facts that exist in the database today. No CCTV, alert or recording figures:
    those modules are not built yet."""

    organization_code: str
    organization_name: str
    organization_status: str
    official_email: str | None
    official_phone: str | None
    campus_name: str
    campus_status: str
    admin_display_id: str
    admin_full_name: str
    admin_username: str | None
    admin_email: str
    admin_phone: str | None
    is_main_admin: bool
    active_admins: int
    active_supervisors: int
    active_guards: int
    cameras: int
    pending_campus_requests: int | None  # Main Admin only


class IOrganizationAdminRepository(Protocol):
    async def organization_status(self, organization_id: UUID) -> str | None: ...

    async def dashboard_facts(
        self, organization_id: UUID, campus_id: UUID, user_id: UUID, *, include_campus_requests: bool
    ) -> AdminDashboardFacts | None:
        """Everything is read for the caller's own organization and campus only."""
        ...
