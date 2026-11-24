"""Building, Floor & Area Management, for the signed-in Admin or Supervisor's own campus.

GET    /locations                               the campus tree (buildings → floors → areas) with counts
POST   /locations/buildings                     Admin only: add a building or outdoor perimeter
PUT    /locations/buildings/{id}                Admin only: rename or change type
DELETE /locations/buildings/{id}                Admin only: remove an empty building or perimeter
POST   /locations/buildings/{id}/floors         add a floor (indoor buildings only)
PUT    /locations/floors/{id}                   rename / renumber
DELETE /locations/floors/{id}                   remove an empty floor
POST   /locations/buildings/{id}/areas          add an area / zone (optionally on a floor, with a boundary)
PUT    /locations/areas/{id}                    edit an area
DELETE /locations/areas/{id}                    remove an area nothing is linked to
GET    /locations/{buildings|floors|areas}/{id}/linked   cameras and Guards at or below it
GET    /locations/guard-assignments             Guards with a location
PUT    /locations/guard-assignments/{guard_id}  set a Guard's location (Guard ID)
DELETE /locations/guard-assignments/{guard_id}  remove a Guard's location
GET    /locations/camera-links                  cameras with a location
PUT    /locations/camera-links/{camera_id}      set a camera's location
DELETE /locations/camera-links/{camera_id}      remove a camera's location

Another campus's IDs answer 404, the same as IDs that do not exist.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.location_dto import (
    AreaRequest,
    AreaResponse,
    BuildingRequest,
    BuildingResponse,
    BuildingTreeResponse,
    CameraLocationResponse,
    FloorRequest,
    FloorResponse,
    GuardLocationResponse,
    LinkedToLocationResponse,
    LocationSelectionRequest,
)
from src.application.use_cases.location_use_cases import (
    AreaDetails,
    ManageAreas,
    ManageBuildings,
    ManageCameraLocations,
    ManageFloors,
    ManageGuardLocations,
    ViewLocations,
)
from src.application.use_cases.staff_use_cases import GuardLocationActivity
from src.domain.entities.location import LocationLevel
from src.domain.entities.user import User
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.repositories.staff_repository import SqlStaffRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import LocationStaffDep, SessionDep

router = APIRouter(prefix="/locations", tags=["Building, Floor & Area Management"])


def _deps(actor: User, session) -> tuple[SqlLocationRepository, SqlTransaction]:  # type: ignore[no-untyped-def]
    return SqlLocationRepository(session, actor.organization_id, actor.campus_id), SqlTransaction(session)


def _audited(
    actor: User, session: AsyncSession
) -> tuple[SqlLocationRepository, SqlTransaction, SqlOrganizationAuditLog]:
    """Location changes are written to the organization audit log (decision D-024)."""
    return (*_deps(actor, session), SqlOrganizationAuditLog(session))


def _guard_locations(actor: User, session) -> ManageGuardLocations:  # type: ignore[no-untyped-def]
    """Setting or removing a Guard's location is audited; Supervisors' changes notify the Admins (D-017)."""
    activity = GuardLocationActivity(SqlStaffRepository(session, actor.organization_id, actor.campus_id))
    return ManageGuardLocations(*_deps(actor, session), activity=activity)


def _details(body: AreaRequest) -> AreaDetails:
    return AreaDetails(body.name, body.floor_id, body.boundary_direction, body.boundary_name, body.boundary_side)


@router.get("", response_model=list[BuildingTreeResponse])
async def location_tree(actor: LocationStaffDep, session: SessionDep) -> list[BuildingTreeResponse]:
    return [BuildingTreeResponse.node(n) for n in await ViewLocations(*_deps(actor, session)).tree(actor)]


# --- Buildings ------------------------------------------------------------------------------------


@router.post("/buildings", status_code=status.HTTP_201_CREATED, response_model=BuildingResponse)
async def add_building(body: BuildingRequest, actor: LocationStaffDep, session: SessionDep) -> BuildingResponse:
    return BuildingResponse.of(await ManageBuildings(*_audited(actor, session)).create(actor, body.name, body.kind))


@router.put("/buildings/{building_id}", response_model=BuildingResponse)
async def edit_building(
    building_id: UUID, body: BuildingRequest, actor: LocationStaffDep, session: SessionDep
) -> BuildingResponse:
    building = await ManageBuildings(*_audited(actor, session)).update(actor, building_id, body.name, body.kind)
    return BuildingResponse.of(building)


@router.delete("/buildings/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_building(building_id: UUID, actor: LocationStaffDep, session: SessionDep) -> None:
    await ManageBuildings(*_audited(actor, session)).delete(actor, building_id)


# --- Floors -----------------------------------------------------------------------------------------


@router.post("/buildings/{building_id}/floors", status_code=status.HTTP_201_CREATED, response_model=FloorResponse)
async def add_floor(
    building_id: UUID, body: FloorRequest, actor: LocationStaffDep, session: SessionDep
) -> FloorResponse:
    return FloorResponse.of(
        await ManageFloors(*_audited(actor, session)).create(actor, building_id, body.name, body.level)
    )


@router.put("/floors/{floor_id}", response_model=FloorResponse)
async def edit_floor(floor_id: UUID, body: FloorRequest, actor: LocationStaffDep, session: SessionDep) -> FloorResponse:
    floor = await ManageFloors(*_audited(actor, session)).update(actor, floor_id, body.name, body.level)
    return FloorResponse.of(floor)


@router.delete("/floors/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_floor(floor_id: UUID, actor: LocationStaffDep, session: SessionDep) -> None:
    await ManageFloors(*_audited(actor, session)).delete(actor, floor_id)


# --- Areas --------------------------------------------------------------------------------------------


@router.post("/buildings/{building_id}/areas", status_code=status.HTTP_201_CREATED, response_model=AreaResponse)
async def add_area(building_id: UUID, body: AreaRequest, actor: LocationStaffDep, session: SessionDep) -> AreaResponse:
    return AreaResponse.of(await ManageAreas(*_audited(actor, session)).create(actor, building_id, _details(body)))


@router.put("/areas/{area_id}", response_model=AreaResponse)
async def edit_area(area_id: UUID, body: AreaRequest, actor: LocationStaffDep, session: SessionDep) -> AreaResponse:
    return AreaResponse.of(await ManageAreas(*_audited(actor, session)).update(actor, area_id, _details(body)))


@router.delete("/areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_area(area_id: UUID, actor: LocationStaffDep, session: SessionDep) -> None:
    await ManageAreas(*_audited(actor, session)).delete(actor, area_id)


_LEVELS: dict[str, LocationLevel] = {
    "buildings": LocationLevel.BUILDING,
    "floors": LocationLevel.FLOOR,
    "areas": LocationLevel.AREA,
}


@router.get("/{level}/{location_id}/linked", response_model=LinkedToLocationResponse)
async def linked_to_location(
    level: Literal["buildings", "floors", "areas"], location_id: UUID, actor: LocationStaffDep, session: SessionDep
) -> LinkedToLocationResponse:
    cameras, guards = await ViewLocations(*_deps(actor, session)).linked(actor, _LEVELS[level], location_id)
    return LinkedToLocationResponse(
        cameras=[CameraLocationResponse.of(c) for c in cameras], guards=[GuardLocationResponse.of(g) for g in guards]
    )


# --- Guard locations (optional; never changed by the Guard) --------------------------------------------


@router.get("/guard-assignments", response_model=list[GuardLocationResponse])
async def guard_locations(actor: LocationStaffDep, session: SessionDep) -> list[GuardLocationResponse]:
    return [GuardLocationResponse.of(g) for g in await ManageGuardLocations(*_deps(actor, session)).list(actor)]


@router.put("/guard-assignments/{guard_id}", response_model=GuardLocationResponse)
async def assign_guard(
    guard_id: str, body: LocationSelectionRequest, actor: LocationStaffDep, session: SessionDep
) -> GuardLocationResponse:
    assigned = await _guard_locations(actor, session).assign(actor, guard_id, body.to_selection())
    return GuardLocationResponse.of(assigned)


@router.delete("/guard-assignments/{guard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_guard(guard_id: str, actor: LocationStaffDep, session: SessionDep) -> None:
    await _guard_locations(actor, session).clear(actor, guard_id)


# --- Camera locations -------------------------------------------------------------------------------------


@router.get("/camera-links", response_model=list[CameraLocationResponse])
async def camera_locations(actor: LocationStaffDep, session: SessionDep) -> list[CameraLocationResponse]:
    return [CameraLocationResponse.of(c) for c in await ManageCameraLocations(*_audited(actor, session)).list(actor)]


@router.put("/camera-links/{camera_id}", response_model=CameraLocationResponse)
async def link_camera(
    camera_id: UUID, body: LocationSelectionRequest, actor: LocationStaffDep, session: SessionDep
) -> CameraLocationResponse:
    linked = await ManageCameraLocations(*_audited(actor, session)).link(actor, camera_id, body.to_selection())
    return CameraLocationResponse.of(linked)


@router.delete("/camera-links/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_camera(camera_id: UUID, actor: LocationStaffDep, session: SessionDep) -> None:
    await ManageCameraLocations(*_audited(actor, session)).unlink(actor, camera_id)
