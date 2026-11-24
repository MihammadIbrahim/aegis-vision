"""Staff Management for the signed-in Admin or Supervisor's own campus (web Phase 8; decision D-017).

GET  /staff                          list: ?role=GUARD|SUPERVISOR (Supervisors: GUARD only), ?status=ACTIVE|LEFT,
                                     ?category=STANDBY|STATIONED (Guards), ?q= (name, ID, username, email),
                                     ?limit= (1–200, default 50), ?offset=
GET  /staff/{id}                     one Guard (Admins, Supervisors) or Supervisor (Admins)
PUT  /staff/guards/{id}              Guard full name, username, phone number (+ own current password)
PUT  /staff/guards/{id}/password     set a Guard's password (+ own current password); earlier Guard sessions end
POST /staff/{id}/deactivate          Admins: status LEFT (+ own current password)
POST /staff/{id}/reactivate          Admins: status ACTIVE (+ own current password)

Creating staff stays with /organization-admin/supervisors… and /guards… (Admins only). Guard
locations: /locations/guard-assignments… (Admins and Supervisors).
Another campus's IDs answer 404, the same as IDs that do not exist.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from src.application.dtos.staff_dto import (
    SetGuardPasswordRequest,
    StaffMemberResponse,
    StaffStatusRequest,
    UpdateGuardRequest,
)
from src.application.use_cases.staff_use_cases import (
    ChangeStaffStatus,
    GetStaffMember,
    ListStaff,
    PasswordPolicy,
    SetGuardPassword,
    UpdateGuardDetails,
)
from src.domain.entities.staff import GuardCategory
from src.domain.entities.user import User
from src.domain.repositories.i_staff_repo import StaffFilter
from src.domain.value_objects.enums import AccountStatus, UserRole
from src.infrastructure.database.repositories.staff_repository import SqlStaffRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import (
    OrganizationAdminDep,
    SessionDep,
    StaffManagerDep,
)

router = APIRouter(prefix="/staff", tags=["Staff Management"])


def _deps(actor: User, session, request: Request | None = None):  # type: ignore[no-untyped-def]
    repository = SqlStaffRepository(session, actor.organization_id, actor.campus_id)
    if request is None:
        return repository, SqlTransaction(session)
    settings = request.app.state.settings
    policy = PasswordPolicy(settings.org_login_max_attempts, settings.org_login_lockout_minutes * 60)
    return repository, SqlTransaction(session), Argon2PasswordHasher(), policy


@router.get("", response_model=list[StaffMemberResponse])
async def list_staff(
    actor: StaffManagerDep,
    session: SessionDep,
    role: Literal["GUARD", "SUPERVISOR"] = "GUARD",
    status_: Literal["ACTIVE", "LEFT"] | None = Query(default=None, alias="status"),
    category: Literal["STANDBY", "STATIONED"] | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StaffMemberResponse]:
    where = StaffFilter(
        UserRole(role),
        None if status_ is None else AccountStatus(status_),
        None if category is None else GuardCategory(category),
        (q or "").strip() or None,
        limit,
        offset,
    )
    return [StaffMemberResponse.of(m) for m in await ListStaff(*_deps(actor, session)).execute(actor, where)]


@router.get("/{user_id}", response_model=StaffMemberResponse)
async def get_staff_member(user_id: UUID, actor: StaffManagerDep, session: SessionDep) -> StaffMemberResponse:
    return StaffMemberResponse.of(await GetStaffMember(*_deps(actor, session)).execute(actor, user_id))


@router.put("/guards/{user_id}", response_model=StaffMemberResponse)
async def update_guard(
    user_id: UUID, body: UpdateGuardRequest, request: Request, actor: StaffManagerDep, session: SessionDep
) -> StaffMemberResponse:
    updated = await UpdateGuardDetails(*_deps(actor, session, request)).execute(
        actor,
        user_id,
        full_name=body.full_name,
        username=body.username,
        phone=body.phone_number,
        current_password=body.current_password,
    )
    return StaffMemberResponse.of(updated)


@router.put("/guards/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def set_guard_password(
    user_id: UUID, body: SetGuardPasswordRequest, request: Request, actor: StaffManagerDep, session: SessionDep
) -> None:
    await SetGuardPassword(*_deps(actor, session, request)).execute(
        actor, user_id, new_password=body.new_password, current_password=body.current_password
    )


@router.post("/{user_id}/deactivate", response_model=StaffMemberResponse)
async def deactivate(
    user_id: UUID, body: StaffStatusRequest, request: Request, actor: OrganizationAdminDep, session: SessionDep
) -> StaffMemberResponse:
    changed = await ChangeStaffStatus(*_deps(actor, session, request)).execute(
        actor, user_id, active=False, current_password=body.current_password
    )
    return StaffMemberResponse.of(changed)


@router.post("/{user_id}/reactivate", response_model=StaffMemberResponse)
async def reactivate(
    user_id: UUID, body: StaffStatusRequest, request: Request, actor: OrganizationAdminDep, session: SessionDep
) -> StaffMemberResponse:
    changed = await ChangeStaffStatus(*_deps(actor, session, request)).execute(
        actor, user_id, active=True, current_password=body.current_password
    )
    return StaffMemberResponse.of(changed)
