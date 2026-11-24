"""Building, Floor & Area Management (requirements Section 3; decisions D-010 and D-011).

Who may do what (inside their own campus only):
- **Buildings and outdoor perimeters:** Admins create, change and remove them; Supervisors view.
- **Floors and areas:** Admins and Supervisors.
- **Guard and camera locations:** Admins and Supervisors.
- **Guards** see only their own assignment (Guard app).

A location that is still in use (child locations, cameras or Guards) cannot be removed. Nothing
is ever deleted along with it. Each use case commits only after every check has passed. Every change
is written to the organization audit log (BUILDING_ / FLOOR_ / AREA_ CREATED, UPDATED, DELETED and
CAMERA_LOCATION_SET / _CLEARED; decision D-024).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from src.domain.entities.location import (
    MAX_FLOOR_LEVEL,
    MIN_FLOOR_LEVEL,
    Area,
    Boundary,
    BoundaryDirection,
    BoundarySide,
    Building,
    BuildingKind,
    Floor,
    LocationLevel,
    LocationSelection,
    LocationTarget,
    clean_name,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict, NotFound, ValidationFailed
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_location_repo import (
    Dependents,
    ILocationRepository,
    LinkedCamera,
    LinkedGuard,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import (
    ensure_building_manager,
    ensure_location_staff,
)
from src.domain.value_objects.location_path import LocationPath

_NOT_FOUND = {
    LocationLevel.BUILDING: "Building or perimeter not found.",
    LocationLevel.FLOOR: "Floor not found.",
    LocationLevel.AREA: "Area not found.",
}


def _missing(level: LocationLevel) -> NotFound:
    # The same answer whether the location does not exist or belongs to another campus.
    return NotFound(_NOT_FOUND[level], code="LOCATION_NOT_FOUND")


def _not_empty(dependents: Dependents) -> Conflict:
    parts = [
        f"{count} {name}"
        for name, count in (
            ("floor(s)", dependents.floors),
            ("area(s)", dependents.areas),
            ("camera(s)", dependents.cameras),
            ("Guard(s)", dependents.guards),
        )
        if count
    ]
    return Conflict(
        f"This location still has {', '.join(parts)}. Move or remove them first.", code="LOCATION_NOT_EMPTY"
    )


def _level(value: int | None) -> int | None:
    if value is not None and not MIN_FLOOR_LEVEL <= value <= MAX_FLOOR_LEVEL:
        raise ValidationFailed(
            f"Floor level must be between {MIN_FLOOR_LEVEL} and {MAX_FLOOR_LEVEL}.", code="INVALID_FLOOR_LEVEL"
        )
    return value


class _Base:
    def __init__(
        self,
        repository: ILocationRepository,
        transaction: ITransaction,
        audit: IOrganizationAuditLog | None = None,
    ) -> None:
        self._repo = repository
        self._transaction = transaction
        self._audit_log = audit

    async def _audit(
        self, actor: User, action: str, target_type: str, target_id: UUID, details: dict[str, Any]
    ) -> None:
        if self._audit_log is not None:
            await self._audit_log.record(actor, action, target_type, target_id, details)

    async def _building(self, building_id: UUID) -> Building:
        building = await self._repo.get_building(building_id)
        if building is None:
            raise _missing(LocationLevel.BUILDING)
        return building

    async def _floor(self, floor_id: UUID) -> Floor:
        floor = await self._repo.get_floor(floor_id)
        if floor is None:
            raise _missing(LocationLevel.FLOOR)
        return floor

    async def _area(self, area_id: UUID) -> Area:
        area = await self._repo.get_area(area_id)
        if area is None:
            raise _missing(LocationLevel.AREA)
        return area

    async def _ensure_empty(self, target: LocationTarget) -> None:
        dependents = await self._repo.dependents(target)
        if not dependents.empty:
            raise _not_empty(dependents)

    async def _floor_of(self, building: Building, floor_id: UUID | None) -> UUID | None:
        if floor_id is None:
            return None
        floor = await self._floor(floor_id)
        if floor.building_id != building.id:
            raise ValidationFailed("That floor belongs to another building.", code="INVALID_LOCATION")
        return floor.id

    async def resolve(self, selection: LocationSelection) -> LocationTarget:
        """Check a dropdown selection and return its most specific level."""
        building = await self._building(selection.building_id)
        floor_id = await self._floor_of(building, selection.floor_id)
        if selection.area_id is None:
            if floor_id is not None:
                return LocationTarget(LocationLevel.FLOOR, floor_id)
            return LocationTarget(LocationLevel.BUILDING, building.id)
        area = await self._area(selection.area_id)
        if area.building_id != building.id or area.floor_id != floor_id:
            raise ValidationFailed("That area is not on the selected building and floor.", code="INVALID_LOCATION")
        return LocationTarget(LocationLevel.AREA, area.id)


class LocationResolver(_Base):
    """Checks a building → floor → area selection for other modules (Camera Management)."""


# --- Viewing ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AreaNode:
    area: Area
    cameras: int
    guards: int


@dataclass(frozen=True, slots=True)
class FloorNode:
    floor: Floor
    areas: list[AreaNode]
    cameras: int
    guards: int


@dataclass(frozen=True, slots=True)
class BuildingNode:
    building: Building
    floors: list[FloorNode]
    areas: list[AreaNode] = field(default_factory=list)  # areas not on a floor
    cameras: int = 0
    guards: int = 0


def _count(paths: list[LocationPath], level: str, location_id: UUID) -> int:
    return sum(1 for p in paths if p.is_within(level, location_id))


class ViewLocations(_Base):
    async def tree(self, actor: User) -> list[BuildingNode]:
        """The campus hierarchy, with how many cameras and Guards are at or below each place."""
        ensure_location_staff(actor)
        cameras = [c.path for c in await self._repo.linked_cameras()]
        guards = [g.path for g in await self._repo.linked_guards()]
        floors, areas = await self._repo.floors(), await self._repo.areas()

        def area_node(a: Area) -> AreaNode:
            return AreaNode(a, _count(cameras, "AREA", a.id), _count(guards, "AREA", a.id))

        result = []
        for b in await self._repo.buildings():
            floor_nodes = [
                FloorNode(
                    f,
                    [area_node(a) for a in areas if a.floor_id == f.id],
                    _count(cameras, "FLOOR", f.id),
                    _count(guards, "FLOOR", f.id),
                )
                for f in floors
                if f.building_id == b.id
            ]
            loose = [area_node(a) for a in areas if a.building_id == b.id and a.floor_id is None]
            result.append(
                BuildingNode(b, floor_nodes, loose, _count(cameras, "BUILDING", b.id), _count(guards, "BUILDING", b.id))
            )
        return result

    async def linked(
        self, actor: User, level: LocationLevel, location_id: UUID
    ) -> tuple[list[LinkedCamera], list[LinkedGuard]]:
        """Cameras and Guards at or below one building, floor or area."""
        ensure_location_staff(actor)
        lookup = {
            LocationLevel.BUILDING: self._building,
            LocationLevel.FLOOR: self._floor,
            LocationLevel.AREA: self._area,
        }
        await lookup[level](location_id)  # 404 unless it is in the caller's campus
        cameras = [c for c in await self._repo.linked_cameras() if c.path.is_within(level.value, location_id)]
        guards = [g for g in await self._repo.linked_guards() if g.path.is_within(level.value, location_id)]
        return cameras, guards


# --- Buildings and outdoor perimeters (Admins only) ----------------------------------------------


class ManageBuildings(_Base):
    async def create(self, actor: User, name: str, kind: BuildingKind) -> Building:
        ensure_building_manager(actor)
        building = Building(uuid4(), actor.organization_id, actor.campus_id, kind, clean_name(name, "The name"))
        await self._repo.add_building(building)
        details = {"name": building.name, "kind": kind.value}
        await self._audit(actor, "BUILDING_CREATED", "BUILDING", building.id, details)
        await self._transaction.commit()
        return building

    async def update(self, actor: User, building_id: UUID, name: str, kind: BuildingKind) -> Building:
        ensure_building_manager(actor)
        current = await self._building(building_id)
        if kind is BuildingKind.OUTDOOR_PERIMETER and current.kind is BuildingKind.INDOOR:
            if (await self._repo.dependents(LocationTarget(LocationLevel.BUILDING, building_id))).floors:
                raise Conflict(
                    "Remove this building's floors before making it an outdoor perimeter.", code="BUILDING_HAS_FLOORS"
                )
        building = Building(current.id, current.organization_id, current.campus_id, kind, clean_name(name, "The name"))
        await self._repo.update_building(building)
        await self._audit(
            actor,
            "BUILDING_UPDATED",
            "BUILDING",
            building.id,
            {"name": building.name, "kind": kind.value, "previous": {"name": current.name, "kind": current.kind.value}},
        )
        await self._transaction.commit()
        return building

    async def delete(self, actor: User, building_id: UUID) -> None:
        ensure_building_manager(actor)
        current = await self._building(building_id)
        await self._ensure_empty(LocationTarget(LocationLevel.BUILDING, building_id))
        await self._repo.delete_building(building_id)
        await self._audit(
            actor, "BUILDING_DELETED", "BUILDING", building_id, {"name": current.name, "kind": current.kind.value}
        )
        await self._transaction.commit()


# --- Floors (indoor buildings only) and areas ----------------------------------------------------


class ManageFloors(_Base):
    async def create(self, actor: User, building_id: UUID, name: str, level: int | None) -> Floor:
        ensure_location_staff(actor)
        building = await self._building(building_id)
        if building.kind is not BuildingKind.INDOOR:
            raise ValidationFailed("Outdoor perimeters have no floors.", code="FLOORS_NOT_ALLOWED")
        floor = Floor(uuid4(), building.id, clean_name(name, "The floor name"), _level(level))
        await self._repo.add_floor(floor)
        details = {"name": floor.name, "level": floor.level, "building": building.name}
        await self._audit(actor, "FLOOR_CREATED", "FLOOR", floor.id, details)
        await self._transaction.commit()
        return floor

    async def update(self, actor: User, floor_id: UUID, name: str, level: int | None) -> Floor:
        ensure_location_staff(actor)
        current = await self._floor(floor_id)
        floor = Floor(current.id, current.building_id, clean_name(name, "The floor name"), _level(level))
        await self._repo.update_floor(floor)
        await self._audit(
            actor,
            "FLOOR_UPDATED",
            "FLOOR",
            floor.id,
            {"name": floor.name, "level": floor.level, "previous": {"name": current.name, "level": current.level}},
        )
        await self._transaction.commit()
        return floor

    async def delete(self, actor: User, floor_id: UUID) -> None:
        ensure_location_staff(actor)
        current = await self._floor(floor_id)
        await self._ensure_empty(LocationTarget(LocationLevel.FLOOR, floor_id))
        await self._repo.delete_floor(floor_id)
        await self._audit(actor, "FLOOR_DELETED", "FLOOR", floor_id, {"name": current.name, "level": current.level})
        await self._transaction.commit()


@dataclass(frozen=True, slots=True)
class AreaDetails:
    name: str
    floor_id: UUID | None = None
    boundary_direction: BoundaryDirection | None = None
    boundary_name: str | None = None
    boundary_side: BoundarySide | None = None

    def boundary(self) -> Boundary:
        return Boundary.build(self.boundary_direction, self.boundary_name, self.boundary_side)


class ManageAreas(_Base):
    async def create(self, actor: User, building_id: UUID, details: AreaDetails) -> Area:
        ensure_location_staff(actor)
        building = await self._building(building_id)
        floor_id = await self._floor_of(building, details.floor_id)
        area = Area(uuid4(), building.id, floor_id, clean_name(details.name, "The area name"), details.boundary())
        await self._repo.add_area(area)
        await self._audit(actor, "AREA_CREATED", "AREA", area.id, {"name": area.name, "building": building.name})
        await self._transaction.commit()
        return area

    async def update(self, actor: User, area_id: UUID, details: AreaDetails) -> Area:
        """Rename, change the boundary, or move to another floor of the same building."""
        ensure_location_staff(actor)
        current = await self._area(area_id)
        building = await self._building(current.building_id)
        floor_id = await self._floor_of(building, details.floor_id)
        area = Area(current.id, building.id, floor_id, clean_name(details.name, "The area name"), details.boundary())
        await self._repo.update_area(area)
        await self._audit(
            actor,
            "AREA_UPDATED",
            "AREA",
            area.id,
            {
                "name": area.name,
                "building": building.name,
                "moved_floor": current.floor_id != floor_id,
                "previous": {"name": current.name},
            },
        )
        await self._transaction.commit()
        return area

    async def delete(self, actor: User, area_id: UUID) -> None:
        ensure_location_staff(actor)
        current = await self._area(area_id)
        await self._ensure_empty(LocationTarget(LocationLevel.AREA, area_id))
        await self._repo.delete_area(area_id)
        await self._audit(actor, "AREA_DELETED", "AREA", area_id, {"name": current.name})
        await self._transaction.commit()


# --- Guard and camera locations --------------------------------------------------------------------


class GuardLocationRecorder(Protocol):
    async def record(self, actor: User, guard_user_id: UUID, level: str | None) -> None: ...


class ManageGuardLocations(_Base):
    """Optional, one per Guard; set by an Admin or Supervisor, never by the Guard. Without an
    assignment a Guard is a Standby Guard: reached by escalation, not by location routing (D-013,
    D-017). ``activity`` writes the audit entry and, for Supervisors, the Admin notification."""

    def __init__(
        self,
        repository: ILocationRepository,
        transaction: ITransaction,
        activity: GuardLocationRecorder | None = None,
    ) -> None:
        super().__init__(repository, transaction)  # audited by ``activity`` (GUARD_LOCATION_*)
        self._activity = activity

    async def list(self, actor: User) -> list[LinkedGuard]:
        ensure_location_staff(actor)
        return await self._repo.linked_guards()

    async def assign(self, actor: User, guard_id: str, selection: LocationSelection) -> LinkedGuard:
        ensure_location_staff(actor)
        guard = await self._repo.find_guard(guard_id.strip())
        if guard is None:
            raise NotFound("Guard not found.", code="GUARD_NOT_FOUND")
        target = await self.resolve(selection)
        await self._repo.set_guard_location(guard, target)
        assigned = next(g for g in await self._repo.linked_guards() if g.guard_user_id == guard.user_id)
        if self._activity is not None:
            await self._activity.record(actor, guard.user_id, target.level.value)
        await self._transaction.commit()
        return assigned

    async def clear(self, actor: User, guard_id: str) -> None:
        ensure_location_staff(actor)
        guard = await self._repo.find_guard(guard_id.strip())
        if guard is None or not await self._repo.clear_guard_location(guard.user_id):
            raise NotFound("This Guard has no location assignment.", code="GUARD_NOT_ASSIGNED")
        if self._activity is not None:
            await self._activity.record(actor, guard.user_id, None)
        await self._transaction.commit()


class ManageCameraLocations(_Base):
    async def list(self, actor: User) -> list[LinkedCamera]:
        ensure_location_staff(actor)
        return await self._repo.linked_cameras()

    async def link(self, actor: User, camera_id: UUID, selection: LocationSelection) -> LinkedCamera:
        ensure_location_staff(actor)
        if not await self._repo.camera_exists(camera_id):
            raise NotFound("Camera not found.")
        await self._repo.set_camera_location(camera_id, await self.resolve(selection))
        linked = next(c for c in await self._repo.linked_cameras() if c.camera_id == camera_id)
        details = {"name": linked.display_name, "location": linked.path.label}
        await self._audit(actor, "CAMERA_LOCATION_SET", "CAMERA", camera_id, details)
        await self._transaction.commit()
        return linked

    async def unlink(self, actor: User, camera_id: UUID) -> None:
        ensure_location_staff(actor)
        if not await self._repo.camera_exists(camera_id):
            raise NotFound("Camera not found.")
        await self._repo.set_camera_location(camera_id, None)
        await self._audit(actor, "CAMERA_LOCATION_CLEARED", "CAMERA", camera_id, {})
        await self._transaction.commit()


class GetOwnGuardLocation:
    def __init__(self, repository: ILocationRepository) -> None:
        self._repo = repository

    async def execute(self) -> dict[str, object] | None:
        return await self._repo.own_guard_location()


__all__ = [
    "AreaDetails",
    "BuildingNode",
    "GetOwnGuardLocation",
    "LocationResolver",
    "ManageAreas",
    "ManageBuildings",
    "ManageCameraLocations",
    "ManageFloors",
    "ManageGuardLocations",
    "ViewLocations",
]
