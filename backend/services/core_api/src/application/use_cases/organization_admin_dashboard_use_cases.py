"""The signed-in Organization Admin's dashboard summary.

The organization and campus are always the caller's own, read from the database. The
request carries no IDs, so there is nothing to change to reach another organization.
"""

from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound, PermissionDenied
from src.domain.repositories.i_organization_repo import (
    AdminDashboardFacts,
    IOrganizationAdminRepository,
)
from src.domain.services.permission_policy import ensure_admin
from src.domain.value_objects.enums import UserRole

# Shown so a client can render an honest empty state instead of invented numbers.
MODULES_NOT_AVAILABLE_YET = ("alerts", "incidents", "recordings", "evidence", "ai_detection", "guard_reports")


class GetOrganizationAdminDashboard:
    def __init__(self, repository: IOrganizationAdminRepository) -> None:
        self._repository = repository

    async def execute(self, actor: User) -> AdminDashboardFacts:
        ensure_admin(actor)
        facts = await self._repository.dashboard_facts(
            actor.organization_id, actor.campus_id, actor.id, include_campus_requests=actor.is_first_admin
        )
        if facts is None:
            raise NotFound("Your organization or campus could not be found.")
        return facts


class GetSupervisorDashboard:
    """The signed-in Supervisor's organization, campus, profile and campus counts."""

    def __init__(self, repository: IOrganizationAdminRepository) -> None:
        self._repository = repository

    async def execute(self, actor: User) -> AdminDashboardFacts:
        if actor.role is not UserRole.SUPERVISOR:
            raise PermissionDenied("Only Supervisors can use this page.")
        facts = await self._repository.dashboard_facts(
            actor.organization_id, actor.campus_id, actor.id, include_campus_requests=False
        )
        if facts is None:
            raise NotFound("Your organization or campus could not be found.")
        return facts


class GetGuardDashboard:
    """The signed-in Guard's organization, campus and own profile."""

    def __init__(self, repository: IOrganizationAdminRepository) -> None:
        self._repository = repository

    async def execute(self, actor: User) -> AdminDashboardFacts:
        if actor.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards can use the Guard app.")
        facts = await self._repository.dashboard_facts(
            actor.organization_id, actor.campus_id, actor.id, include_campus_requests=False
        )
        if facts is None:
            raise NotFound("Your organization or campus could not be found.")
        return facts
