"""Routes for the signed-in Organization Admin. Every route needs an organization access
token for an active Admin of an active organization (``OrganizationAdminDep``)."""

from fastapi import APIRouter

from src.application.dtos.auth_dto import AdminDashboardResponse
from src.application.use_cases.organization_admin_dashboard_use_cases import (
    GetOrganizationAdminDashboard,
)
from src.infrastructure.database.repositories.organization_admin_repository import (
    SqlOrganizationAdminRepository,
)
from src.presentation.dependencies import OrganizationAdminDep, SessionDep

router = APIRouter(prefix="/organization-admin", tags=["Organization Admin"])


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def dashboard(admin: OrganizationAdminDep, session: SessionDep) -> AdminDashboardResponse:
    """The Admin's organization, campus, profile and the counts that exist today. It takes no
    IDs: the organization and campus are always the caller's own."""
    facts = await GetOrganizationAdminDashboard(SqlOrganizationAdminRepository(session)).execute(admin)
    return AdminDashboardResponse.from_facts(facts)
