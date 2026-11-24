"""Buildings, floors, areas and location links (migration 0013).

Every query is filtered by the caller's organization and campus, and Row-Level Security applies
the same rule again. Only Admins can write buildings (also enforced by RLS). Database rule
violations become the API's usual errors."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.location import (
    Area,
    Boundary,
    BoundaryDirection,
    BoundarySide,
    Building,
    BuildingKind,
    Floor,
    LocationLevel,
    LocationTarget,
)
from src.domain.exceptions.domain_errors import Conflict
from src.domain.repositories.i_location_repo import (
    Dependents,
    GuardRecord,
    LinkedCamera,
    LinkedGuard,
)
from src.domain.value_objects.location_path import LocationPath


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_SCOPE = "organization_id = :org AND campus_id = :campus"
_PATH_COLUMNS = (
    "b.id AS building_id, b.name AS building_name, b.kind AS building_kind, "
    "f.id AS floor_id, f.name AS floor_name, a.id AS area_id, a.name AS area_name"
)
_PATH_JOINS = """LEFT JOIN areas a ON a.id = x.area_id
                 LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, a.floor_id)
                 JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, a.building_id)"""

# Database constraint → the error the caller sees.
_CONFLICTS: dict[str, tuple[str, str]] = {
    "uq_buildings_name": ("LOCATION_NAME_TAKEN", "A building or perimeter with this name already exists here."),
    "uq_floors_name": ("LOCATION_NAME_TAKEN", "This building already has a floor with this name."),
    "uq_floors_level": ("FLOOR_LEVEL_TAKEN", "This building already has a floor at this level."),
    "uq_areas_name": ("LOCATION_NAME_TAKEN", "An area with this name already exists on this floor."),
    "fk_floors_building": ("BUILDING_HAS_FLOORS", "Remove this building's floors before making it a perimeter."),
}
_IN_USE = ("LOCATION_IN_USE", "This location is still in use. Move or remove what is linked to it first.")


def _constraint(error: IntegrityError) -> str | None:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None) or getattr(
        getattr(error.orig, "diag", None), "constraint_name", None
    )


def _conflict(error: IntegrityError) -> Conflict:
    code, message = _CONFLICTS.get(_constraint(error) or "", _IN_USE)
    return Conflict(message, code=code)


def _path(row: Any) -> LocationPath:
    return LocationPath(
        row.building_id, row.building_name, row.building_kind, row.floor_id, row.floor_name, row.area_id, row.area_name
    )


def _area(row: Any) -> Area:
    boundary = Boundary(
        BoundaryDirection(row.boundary_direction) if row.boundary_direction else None,
        row.boundary_name,
        BoundarySide(row.boundary_side) if row.boundary_side else None,
    )
    return Area(row.id, row.building_id, row.floor_id, row.name, boundary)


def _link(target: LocationTarget | None) -> dict[str, UUID | None]:
    ids: dict[str, UUID | None] = {"building_id": None, "floor_id": None, "area_id": None}
    if target is not None:
        ids[f"{target.level.value.lower()}_id"] = target.id
    return ids


class SqlLocationRepository:
    def __init__(self, session: AsyncSession, organization_id: UUID, campus_id: UUID) -> None:
        self._session = session
        self._scope = {"org": organization_id, "campus": campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None, *uuids: str) -> Any:
        query = _sql(statement, "org", "campus", *uuids)
        try:
            return await self._session.execute(query, {**self._scope, **(params or {})})
        except IntegrityError as error:
            raise _conflict(error) from error

    # --- Reading ----------------------------------------------------------------------------

    async def buildings(self) -> list[Building]:
        rows = await self._run(f"SELECT id, kind, name FROM buildings WHERE {_SCOPE} ORDER BY lower(name)")
        return [Building(r.id, self._scope["org"], self._scope["campus"], BuildingKind(r.kind), r.name) for r in rows]

    async def floors(self) -> list[Floor]:
        rows = await self._run(
            f"SELECT id, building_id, name, level FROM floors WHERE {_SCOPE} ORDER BY level NULLS LAST, lower(name)"
        )
        return [Floor(r.id, r.building_id, r.name, r.level) for r in rows]

    async def areas(self) -> list[Area]:
        rows = await self._run(
            "SELECT id, building_id, floor_id, name, boundary_direction, boundary_name, boundary_side "
            f"FROM areas WHERE {_SCOPE} ORDER BY lower(name)"
        )
        return [_area(r) for r in rows]

    async def get_building(self, building_id: UUID) -> Building | None:
        row = (
            await self._run(
                f"SELECT id, kind, name FROM buildings WHERE {_SCOPE} AND id = :id", {"id": building_id}, "id"
            )
        ).one_or_none()
        return (
            None
            if row is None
            else Building(row.id, self._scope["org"], self._scope["campus"], BuildingKind(row.kind), row.name)
        )

    async def get_floor(self, floor_id: UUID) -> Floor | None:
        row = (
            await self._run(
                f"SELECT id, building_id, name, level FROM floors WHERE {_SCOPE} AND id = :id", {"id": floor_id}, "id"
            )
        ).one_or_none()
        return None if row is None else Floor(row.id, row.building_id, row.name, row.level)

    async def get_area(self, area_id: UUID) -> Area | None:
        row = (
            await self._run(
                "SELECT id, building_id, floor_id, name, boundary_direction, boundary_name, boundary_side "
                f"FROM areas WHERE {_SCOPE} AND id = :id",
                {"id": area_id},
                "id",
            )
        ).one_or_none()
        return None if row is None else _area(row)

    # --- Buildings (Admins only; RLS refuses Supervisors) ------------------------------------

    async def add_building(self, building: Building) -> None:
        await self._run(
            "INSERT INTO buildings (id, organization_id, campus_id, kind, name) "
            "VALUES (:id, :org, :campus, :kind, :name)",
            {"id": building.id, "kind": building.kind.value, "name": building.name},
            "id",
        )

    async def update_building(self, building: Building) -> None:
        await self._run(
            f"UPDATE buildings SET kind = :kind, name = :name WHERE {_SCOPE} AND id = :id",
            {"id": building.id, "kind": building.kind.value, "name": building.name},
            "id",
        )

    async def delete_building(self, building_id: UUID) -> None:
        await self._run(f"DELETE FROM buildings WHERE {_SCOPE} AND id = :id", {"id": building_id}, "id")

    # --- Floors and areas ---------------------------------------------------------------------

    async def add_floor(self, floor: Floor) -> None:
        await self._run(
            "INSERT INTO floors (id, organization_id, campus_id, building_id, name, level) "
            "VALUES (:id, :org, :campus, :building, :name, :level)",
            {"id": floor.id, "building": floor.building_id, "name": floor.name, "level": floor.level},
            "id",
            "building",
        )

    async def update_floor(self, floor: Floor) -> None:
        await self._run(
            f"UPDATE floors SET name = :name, level = :level WHERE {_SCOPE} AND id = :id",
            {"id": floor.id, "name": floor.name, "level": floor.level},
            "id",
        )

    async def delete_floor(self, floor_id: UUID) -> None:
        await self._run(f"DELETE FROM floors WHERE {_SCOPE} AND id = :id", {"id": floor_id}, "id")

    def _area_params(self, area: Area) -> dict[str, Any]:
        b = area.boundary
        return {
            "id": area.id,
            "building": area.building_id,
            "floor": area.floor_id,
            "name": area.name,
            "direction": b.direction.value if b.direction else None,
            "boundary_name": b.custom_name,
            "side": b.side.value if b.side else None,
        }

    async def add_area(self, area: Area) -> None:
        await self._run(
            "INSERT INTO areas (id, organization_id, campus_id, building_id, floor_id, name, "
            "boundary_direction, boundary_name, boundary_side) "
            "VALUES (:id, :org, :campus, :building, :floor, :name, :direction, :boundary_name, :side)",
            self._area_params(area),
            "id",
            "building",
            "floor",
        )

    async def update_area(self, area: Area) -> None:
        await self._run(
            "UPDATE areas SET floor_id = :floor, name = :name, boundary_direction = :direction, "
            f"boundary_name = :boundary_name, boundary_side = :side WHERE {_SCOPE} AND id = :id",
            {k: v for k, v in self._area_params(area).items() if k != "building"},
            "id",
            "floor",
        )

    async def delete_area(self, area_id: UUID) -> None:
        await self._run(f"DELETE FROM areas WHERE {_SCOPE} AND id = :id", {"id": area_id}, "id")

    async def dependents(self, target: LocationTarget) -> Dependents:
        column = {"BUILDING": "building_id", "FLOOR": "floor_id", "AREA": "area_id"}[target.level.value]
        counts = {
            "floors": f"SELECT count(*) FROM floors WHERE {_SCOPE} AND building_id = :id",
            "areas": f"SELECT count(*) FROM areas WHERE {_SCOPE} AND {column} = :id",
            "cameras": f"SELECT count(*) FROM cameras WHERE {_SCOPE} AND {column} = :id",
            "guards": f"SELECT count(*) FROM guard_location_assignments WHERE {_SCOPE} AND {column} = :id",
        }
        if target.level is not LocationLevel.BUILDING:
            counts.pop("floors")
        if target.level is LocationLevel.AREA:
            counts.pop("areas")
        values = {k: (await self._run(q, {"id": target.id}, "id")).scalar_one() for k, q in counts.items()}
        return Dependents(**values)

    # --- Links ------------------------------------------------------------------------------

    async def linked_cameras(self) -> list[LinkedCamera]:
        rows = await self._run(
            f"SELECT x.id AS camera_id, x.display_name, {_PATH_COLUMNS} FROM cameras x {_PATH_JOINS} "
            "WHERE x.organization_id = :org AND x.campus_id = :campus ORDER BY lower(x.display_name)"
        )
        return [LinkedCamera(r.camera_id, r.display_name, _path(r)) for r in rows]

    async def linked_guards(self) -> list[LinkedGuard]:
        rows = await self._run(
            f"SELECT u.id AS user_id, u.display_id, u.full_name, {_PATH_COLUMNS} "
            f"FROM guard_location_assignments x JOIN users u ON u.id = x.guard_user_id {_PATH_JOINS} "
            "WHERE x.organization_id = :org AND x.campus_id = :campus ORDER BY lower(u.full_name)"
        )
        return [LinkedGuard(r.user_id, r.display_id, r.full_name, _path(r)) for r in rows]

    async def find_guard(self, guard_id: str) -> GuardRecord | None:
        row = (
            await self._run(
                f"SELECT id, display_id, full_name FROM users WHERE {_SCOPE} AND display_id = :guard "
                "AND role = 'GUARD' AND status = 'ACTIVE'",
                {"guard": guard_id},
            )
        ).one_or_none()
        return None if row is None else GuardRecord(row.id, row.display_id, row.full_name)

    async def set_guard_location(self, guard: GuardRecord, target: LocationTarget) -> None:
        await self._run(
            "INSERT INTO guard_location_assignments (guard_user_id, organization_id, campus_id, "
            "building_id, floor_id, area_id) VALUES (:guard, :org, :campus, :building_id, :floor_id, :area_id) "
            "ON CONFLICT (guard_user_id) DO UPDATE SET building_id = EXCLUDED.building_id, "
            "floor_id = EXCLUDED.floor_id, area_id = EXCLUDED.area_id, assigned_at = now()",
            {"guard": guard.user_id, **_link(target)},
            "guard",
            "building_id",
            "floor_id",
            "area_id",
        )

    async def clear_guard_location(self, guard_user_id: UUID) -> bool:
        result = await self._run(
            f"DELETE FROM guard_location_assignments WHERE {_SCOPE} AND guard_user_id = :guard RETURNING guard_user_id",
            {"guard": guard_user_id},
            "guard",
        )
        return result.first() is not None

    async def camera_exists(self, camera_id: UUID) -> bool:
        row = (await self._run(f"SELECT 1 FROM cameras WHERE {_SCOPE} AND id = :id", {"id": camera_id}, "id")).first()
        return row is not None

    async def set_camera_location(self, camera_id: UUID, target: LocationTarget | None) -> None:
        await self._run(
            "UPDATE cameras SET building_id = :building_id, floor_id = :floor_id, area_id = :area_id "
            f"WHERE {_SCOPE} AND id = :id",
            {"id": camera_id, **_link(target)},
            "id",
            "building_id",
            "floor_id",
            "area_id",
        )

    async def own_guard_location(self) -> dict[str, object] | None:
        row = (await self._session.execute(text("SELECT * FROM app_guard_location()"))).mappings().one_or_none()
        return None if row is None else dict(row)
