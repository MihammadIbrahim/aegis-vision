"""Restricted-Area Access Management for the signed-in user's own campus (decision D-016).

Organization Admins change everything; Supervisors read current authorizations and run access
checks; Guards: 403.

GET    /restricted-access/areas                         restricted areas (all areas and access_note: Admins)
PUT    /restricted-access/areas/{area_id}               mark an area restricted / change its note (Admins)
DELETE /restricted-access/areas/{area_id}               clear the restricted mark (no active access) (Admins)
GET    /restricted-access/areas/{area_id}/grants        who may enter (?include_revoked=true: Admins)
GET    /restricted-access/personnel/{id}/grants         where a person may go (?include_revoked=true: Admins)
POST   /restricted-access/grants                        grant one person access to one or more areas (Admins)
PUT    /restricted-access/grants/{grant_id}             change validity, schedule, note (Admins)
POST   /restricted-access/grants/{grant_id}/revoke      revoke (kept as history) (Admins)
GET    /restricted-access/grants/{grant_id}/history     the grant's audit history (Admins)
GET    /restricted-access/check                         ?personnel_id= &area_id= &at= : is this person allowed?
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import AwareDatetime

from src.application.dtos.personnel_dto import (
    AccessCheckResponse,
    AuditEntryResponse,
    GrantHistoryResponse,
    GrantRequest,
    GrantResponse,
    GrantTermsRequest,
    MarkRestrictedRequest,
    RestrictedAreaResponse,
    RevokeRequest,
    SupervisorRestrictedAreaResponse,
)
from src.application.use_cases.area_access_use_cases import (
    ManageAccessGrants,
    ManageRestrictedAreas,
    ViewRestrictedAccess,
)
from src.domain.entities.user import User
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.repositories.area_access_repository import (
    SqlAreaAccessRepository,
)
from src.infrastructure.database.repositories.audit_log_repository import (
    SqlOrganizationAuditLog,
)
from src.infrastructure.database.repositories.personnel_repository import (
    SqlPersonnelRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import OrganizationAdminDep, SessionDep, StaffDep

router = APIRouter(prefix="/restricted-access", tags=["Restricted-area access"])


def _access(session, actor: User) -> SqlAreaAccessRepository:  # type: ignore[no-untyped-def]
    return SqlAreaAccessRepository(session, actor.organization_id, actor.campus_id)


def _personnel(session, actor: User) -> SqlPersonnelRepository:  # type: ignore[no-untyped-def]
    return SqlPersonnelRepository(session, actor.organization_id, actor.campus_id)


def _view(session, actor: User) -> ViewRestrictedAccess:  # type: ignore[no-untyped-def]
    return ViewRestrictedAccess(_access(session, actor), _personnel(session, actor))


def _grants(session, actor: User) -> ManageAccessGrants:  # type: ignore[no-untyped-def]
    return ManageAccessGrants(
        _access(session, actor), _personnel(session, actor), SqlOrganizationAuditLog(session), SqlTransaction(session)
    )


@router.get("/areas", response_model=list[RestrictedAreaResponse] | list[SupervisorRestrictedAreaResponse])
async def areas(
    actor: StaffDep, session: SessionDep, include_unrestricted: bool = False
) -> list[RestrictedAreaResponse] | list[SupervisorRestrictedAreaResponse]:
    """Admins get each area's ``access_note``; Supervisors get the same areas without that field
    (the note is an Admin note, D-016 item 16)."""
    found = await _view(session, actor).areas(actor, include_unrestricted=include_unrestricted)
    if actor.role is UserRole.ADMIN:
        return [RestrictedAreaResponse.of(a) for a in found]
    return [SupervisorRestrictedAreaResponse.of(a) for a in found]


@router.put("/areas/{area_id}", response_model=RestrictedAreaResponse)
async def mark_restricted(
    area_id: UUID, actor: OrganizationAdminDep, session: SessionDep, body: MarkRestrictedRequest | None = None
) -> RestrictedAreaResponse:
    use_case = ManageRestrictedAreas(_access(session, actor), SqlOrganizationAuditLog(session), SqlTransaction(session))
    area = await use_case.mark(actor, area_id, None if body is None else body.access_note)
    return RestrictedAreaResponse.of(area)


@router.delete("/areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_restricted(area_id: UUID, actor: OrganizationAdminDep, session: SessionDep) -> None:
    use_case = ManageRestrictedAreas(_access(session, actor), SqlOrganizationAuditLog(session), SqlTransaction(session))
    await use_case.clear(actor, area_id)


@router.get("/areas/{area_id}/grants", response_model=list[GrantResponse])
async def area_grants(
    area_id: UUID, actor: StaffDep, session: SessionDep, include_revoked: bool = False
) -> list[GrantResponse]:
    found = await _view(session, actor).area_grants(actor, area_id, include_revoked=include_revoked)
    return [GrantResponse.of(g) for g in found]


@router.get("/personnel/{personnel_id}/grants", response_model=list[GrantResponse])
async def personnel_grants(
    personnel_id: UUID, actor: StaffDep, session: SessionDep, include_revoked: bool = False
) -> list[GrantResponse]:
    found = await _view(session, actor).personnel_grants(actor, personnel_id, include_revoked=include_revoked)
    return [GrantResponse.of(g) for g in found]


@router.post("/grants", status_code=status.HTTP_201_CREATED, response_model=list[GrantResponse])
async def grant(body: GrantRequest, actor: OrganizationAdminDep, session: SessionDep) -> list[GrantResponse]:
    created = await _grants(session, actor).grant(actor, body.personnel_id, body.area_ids, body.to_terms())
    return [GrantResponse.of(g) for g in created]


@router.put("/grants/{grant_id}", response_model=GrantResponse)
async def update_grant(
    grant_id: UUID, body: GrantTermsRequest, actor: OrganizationAdminDep, session: SessionDep
) -> GrantResponse:
    return GrantResponse.of(await _grants(session, actor).update(actor, grant_id, body.to_terms()))


@router.post("/grants/{grant_id}/revoke", response_model=GrantResponse)
async def revoke_grant(
    grant_id: UUID, actor: OrganizationAdminDep, session: SessionDep, body: RevokeRequest | None = None
) -> GrantResponse:
    return GrantResponse.of(
        await _grants(session, actor).revoke(actor, grant_id, None if body is None else body.reason)
    )


@router.get("/grants/{grant_id}/history", response_model=GrantHistoryResponse)
async def grant_history(grant_id: UUID, actor: OrganizationAdminDep, session: SessionDep) -> GrantHistoryResponse:
    found, entries = await _view(session, actor).grant_history(actor, grant_id)
    return GrantHistoryResponse(grant=GrantResponse.of(found), history=[AuditEntryResponse.of(e) for e in entries])


@router.get("/check", response_model=AccessCheckResponse)
async def check(
    actor: StaffDep,
    session: SessionDep,
    personnel_id: Annotated[UUID, Query()],
    area_id: Annotated[UUID, Query()],
    at: Annotated[AwareDatetime | None, Query(description="Default: now")] = None,
) -> AccessCheckResponse:
    when = at or datetime.now(UTC)
    result = await _view(session, actor).check(actor, personnel_id, area_id, when)
    return AccessCheckResponse(
        personnel_id=personnel_id,
        area_id=area_id,
        at=when,
        decision=result.decision.value,
        authorized=result.authorized,
        grant_id=result.grant_id,
    )
