"""Read-only queries for the signed-in Organization Admin.

Runs on the tenant session with the caller bound for Row-Level Security, and every query
also filters by the caller's own organization and campus (taken from the database, never
from the request). Both checks would have to fail for data to cross organizations.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_organization_repo import AdminDashboardFacts
from src.infrastructure.database.models import (
    CameraModel,
    CampusModel,
    CampusRegistrationRequestModel,
    OrganizationModel,
    UserModel,
)
from src.infrastructure.database.repositories.branch_repository import not_expired


class SqlOrganizationAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def organization_status(self, organization_id: UUID) -> str | None:
        status: str | None = await self._session.scalar(
            select(OrganizationModel.status).where(OrganizationModel.id == organization_id)
        )
        return status

    async def dashboard_facts(
        self, organization_id: UUID, campus_id: UUID, user_id: UUID, *, include_campus_requests: bool
    ) -> AdminDashboardFacts | None:
        organization = await self._session.scalar(
            select(OrganizationModel).where(OrganizationModel.id == organization_id)
        )
        campus = await self._session.scalar(
            select(CampusModel).where(CampusModel.id == campus_id, CampusModel.organization_id == organization_id)
        )
        admin = await self._session.scalar(
            select(UserModel).where(
                UserModel.id == user_id,
                UserModel.organization_id == organization_id,
                UserModel.campus_id == campus_id,
            )
        )
        if organization is None or campus is None or admin is None:
            return None

        role_counts = await self._session.execute(
            select(UserModel.role, func.count())
            .where(
                UserModel.organization_id == organization_id,
                UserModel.campus_id == campus_id,
                UserModel.status == "ACTIVE",
            )
            .group_by(UserModel.role)
        )
        counts: dict[str, int] = {role: number for role, number in role_counts.all()}
        cameras = await self._session.scalar(
            select(func.count())
            .select_from(CameraModel)
            .where(CameraModel.organization_id == organization_id, CameraModel.campus_id == campus_id)
        )
        pending_requests = None
        if include_campus_requests:
            pending_requests = await self._session.scalar(
                select(func.count())
                .select_from(CampusRegistrationRequestModel)
                .where(
                    CampusRegistrationRequestModel.organization_id == organization_id,
                    CampusRegistrationRequestModel.status == "PENDING",
                    not_expired(datetime.now(UTC)),
                )
            )
        return AdminDashboardFacts(
            organization_code=organization.organization_code,
            organization_name=organization.name,
            organization_status=organization.status,
            official_email=organization.official_email,
            official_phone=organization.official_phone,
            campus_name=campus.name,
            campus_status=campus.status,
            admin_display_id=admin.display_id,
            admin_full_name=admin.full_name,
            admin_username=admin.username,
            admin_email=admin.email,
            admin_phone=admin.phone,
            is_main_admin=admin.is_first_admin,
            active_admins=counts.get("ADMIN", 0),
            active_supervisors=counts.get("SUPERVISOR", 0),
            active_guards=counts.get("GUARD", 0),
            cameras=cameras or 0,
            pending_campus_requests=pending_requests,
        )
