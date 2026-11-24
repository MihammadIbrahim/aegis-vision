"""Bodies for Building, Floor & Area Management. IDs are the stable location IDs that cameras,
Guard assignments and later modules (alerts, recordings, incidents) refer to."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.location_use_cases import (
    AreaNode,
    BuildingNode,
    FloorNode,
)
from src.domain.entities.location import (
    Area,
    BoundaryDirection,
    BoundarySide,
    Building,
    BuildingKind,
    Floor,
    LocationSelection,
)
from src.domain.repositories.i_location_repo import LinkedCamera, LinkedGuard
from src.domain.value_objects.location_path import LocationPath


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Requests -----------------------------------------------------------------------------------


class BuildingRequest(_Strict):
    name: str = Field(max_length=200)
    kind: BuildingKind = Field(description="INDOOR or OUTDOOR_PERIMETER")


class FloorRequest(_Strict):
    name: str = Field(max_length=200)
    level: int | None = Field(default=None, description="Optional number for ordering, e.g. -1, 0, 1")


class AreaRequest(_Strict):
    name: str = Field(max_length=200)
    floor_id: UUID | None = Field(default=None, description="A floor of the same building, or none")
    boundary_direction: BoundaryDirection | None = None
    boundary_name: str | None = Field(default=None, max_length=200, description="Only with CUSTOM")
    boundary_side: BoundarySide | None = None


class LocationSelectionRequest(_Strict):
    """The dropdowns: building, then optionally floor, then optionally area."""

    building_id: UUID
    floor_id: UUID | None = None
    area_id: UUID | None = None

    def to_selection(self) -> LocationSelection:
        return LocationSelection(self.building_id, self.floor_id, self.area_id)


# --- Responses ----------------------------------------------------------------------------------


class BuildingResponse(_Frozen):
    id: UUID
    name: str
    kind: BuildingKind

    @classmethod
    def of(cls, b: Building) -> "BuildingResponse":
        return cls(id=b.id, name=b.name, kind=b.kind)


class FloorResponse(_Frozen):
    id: UUID
    building_id: UUID
    name: str
    level: int | None

    @classmethod
    def of(cls, f: Floor) -> "FloorResponse":
        return cls(id=f.id, building_id=f.building_id, name=f.name, level=f.level)


class AreaResponse(_Frozen):
    id: UUID
    building_id: UUID
    floor_id: UUID | None
    name: str
    boundary_direction: BoundaryDirection | None
    boundary_name: str | None
    boundary_side: BoundarySide | None

    @classmethod
    def of(cls, a: Area) -> "AreaResponse":
        return cls(
            id=a.id,
            building_id=a.building_id,
            floor_id=a.floor_id,
            name=a.name,
            boundary_direction=a.boundary.direction,
            boundary_name=a.boundary.custom_name,
            boundary_side=a.boundary.side,
        )


class AreaTreeResponse(AreaResponse):
    camera_count: int
    guard_count: int

    @classmethod
    def node(cls, n: AreaNode) -> "AreaTreeResponse":
        return cls(**AreaResponse.of(n.area).model_dump(), camera_count=n.cameras, guard_count=n.guards)


class FloorTreeResponse(FloorResponse):
    camera_count: int
    guard_count: int
    areas: list[AreaTreeResponse]

    @classmethod
    def node(cls, n: FloorNode) -> "FloorTreeResponse":
        return cls(
            **FloorResponse.of(n.floor).model_dump(),
            camera_count=n.cameras,
            guard_count=n.guards,
            areas=[AreaTreeResponse.node(a) for a in n.areas],
        )


class BuildingTreeResponse(BuildingResponse):
    camera_count: int = Field(description="Cameras at or below this building")
    guard_count: int = Field(description="Guards assigned at or below this building")
    floors: list[FloorTreeResponse]
    areas: list[AreaTreeResponse] = Field(description="Areas not on a floor (all areas of a perimeter)")

    @classmethod
    def node(cls, n: BuildingNode) -> "BuildingTreeResponse":
        return cls(
            **BuildingResponse.of(n.building).model_dump(),
            camera_count=n.cameras,
            guard_count=n.guards,
            floors=[FloorTreeResponse.node(f) for f in n.floors],
            areas=[AreaTreeResponse.node(a) for a in n.areas],
        )


class LocationPathResponse(_Frozen):
    level: Literal["BUILDING", "FLOOR", "AREA"]
    building_id: UUID
    building_name: str
    building_kind: BuildingKind
    floor_id: UUID | None
    floor_name: str | None
    area_id: UUID | None
    area_name: str | None

    @classmethod
    def of(cls, p: LocationPath) -> "LocationPathResponse":
        level: Literal["BUILDING", "FLOOR", "AREA"] = "AREA" if p.area_id else "FLOOR" if p.floor_id else "BUILDING"
        return cls(
            level=level,
            building_id=p.building_id,
            building_name=p.building_name,
            building_kind=BuildingKind(p.building_kind),
            floor_id=p.floor_id,
            floor_name=p.floor_name,
            area_id=p.area_id,
            area_name=p.area_name,
        )


class GuardLocationResponse(_Frozen):
    guard_id: str
    full_name: str
    location: LocationPathResponse

    @classmethod
    def of(cls, g: LinkedGuard) -> "GuardLocationResponse":
        return cls(guard_id=g.guard_id, full_name=g.full_name, location=LocationPathResponse.of(g.path))


class CameraLocationResponse(_Frozen):
    camera_id: UUID
    display_name: str
    location: LocationPathResponse

    @classmethod
    def of(cls, c: LinkedCamera) -> "CameraLocationResponse":
        return cls(camera_id=c.camera_id, display_name=c.display_name, location=LocationPathResponse.of(c.path))


class LinkedToLocationResponse(_Frozen):
    cameras: list[CameraLocationResponse]
    guards: list[GuardLocationResponse]


class OwnGuardLocationResponse(_Frozen):
    """The Guard's own assignment. Read-only: only an Admin or Supervisor can change it."""

    assigned: bool
    building_name: str | None = None
    building_kind: BuildingKind | None = None
    floor_name: str | None = None
    floor_level: int | None = None
    area_name: str | None = None
    boundary_direction: BoundaryDirection | None = None
    boundary_name: str | None = None
    boundary_side: BoundarySide | None = None
