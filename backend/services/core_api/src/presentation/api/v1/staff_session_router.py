"""After sign-in: which dashboard to open, and the Supervisor dashboard.

Admins and Supervisors use the same sign-in endpoint; ``GET /auth/me`` tells the client
which dashboard belongs to the signed-in role. Each dashboard checks the role itself.
"""

from fastapi import APIRouter

from src.application.dtos.supervisor_dto import (
    SessionResponse,
    SupervisorDashboardResponse,
)
from src.application.use_cases.organization_admin_dashboard_use_cases import (
    GetSupervisorDashboard,
)
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.repositories.organization_admin_repository import (
    SqlOrganizationAdminRepository,
)
from src.presentation.dependencies import ActorDep, SessionDep, SupervisorDep

router = APIRouter(tags=["Session"])

_DASHBOARDS = {
    UserRole.ADMIN: "/api/v1/organization-admin/dashboard",
    UserRole.SUPERVISOR: "/api/v1/supervisor/dashboard",
    UserRole.GUARD: "/api/v1/guard/dashboard",
}


@router.get("/auth/me", response_model=SessionResponse)
async def me(actor: ActorDep) -> SessionResponse:
    return SessionResponse(
        user_id=actor.display_id,
        account_id=actor.id,
        full_name=actor.full_name,
        role=actor.role.value,
        dashboard=_DASHBOARDS.get(actor.role),
        is_main_admin=actor.is_first_admin,
    )


@router.get("/supervisor/dashboard", response_model=SupervisorDashboardResponse)
async def supervisor_dashboard(supervisor: SupervisorDep, session: SessionDep) -> SupervisorDashboardResponse:
    facts = await GetSupervisorDashboard(SqlOrganizationAdminRepository(session)).execute(supervisor)
    return SupervisorDashboardResponse.from_facts(facts)
