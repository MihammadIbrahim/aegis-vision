"""The campus location hierarchy (requirements Section 3):

    Campus → Building or Outdoor Perimeter → optional Floor → Area / Zone → Camera

Floors exist only in indoor buildings. An area belongs to a building and, optionally, to one of
its floors. Guards and cameras are linked to exactly one building, floor or area."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from src.domain.exceptions.domain_errors import ValidationFailed


class BuildingKind(StrEnum):
    INDOOR = "INDOOR"
    OUTDOOR_PERIMETER = "OUTDOOR_PERIMETER"


class BoundaryDirection(StrEnum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"
    CUSTOM = "CUSTOM"


class BoundarySide(StrEnum):
    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"


class LocationLevel(StrEnum):
    BUILDING = "BUILDING"
    FLOOR = "FLOOR"
    AREA = "AREA"


MAX_NAME_LENGTH = 120
MIN_FLOOR_LEVEL, MAX_FLOOR_LEVEL = -20, 300


def clean_name(value: str, what: str) -> str:
    """Trimmed, 1–120 characters, no control characters. Stored as typed otherwise."""
    name = value.strip()
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(c) < 32 for c in name):
        raise ValidationFailed(f"{what} must be 1 to {MAX_NAME_LENGTH} characters.", code="INVALID_NAME")
    return name


@dataclass(frozen=True, slots=True)
class Boundary:
    """Optional on an area: a direction (or a custom name) and an inside/outside label."""

    direction: BoundaryDirection | None = None
    custom_name: str | None = None
    side: BoundarySide | None = None

    @classmethod
    def build(
        cls, direction: BoundaryDirection | None, custom_name: str | None, side: BoundarySide | None
    ) -> "Boundary":
        if direction is BoundaryDirection.CUSTOM:
            if custom_name is None:
                raise ValidationFailed("A custom boundary needs a name.", code="INVALID_BOUNDARY")
            return cls(direction, clean_name(custom_name, "The boundary name"), side)
        if custom_name is not None:
            raise ValidationFailed("A boundary name is used only with the CUSTOM direction.", code="INVALID_BOUNDARY")
        return cls(direction, None, side)


@dataclass(frozen=True, slots=True)
class Building:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    kind: BuildingKind
    name: str


@dataclass(frozen=True, slots=True)
class Floor:
    id: UUID
    building_id: UUID
    name: str
    level: int | None


@dataclass(frozen=True, slots=True)
class Area:
    id: UUID
    building_id: UUID
    floor_id: UUID | None
    name: str
    boundary: Boundary


@dataclass(frozen=True, slots=True)
class LocationSelection:
    """What the dropdowns send: a building, optionally one of its floors, optionally an area."""

    building_id: UUID
    floor_id: UUID | None = None
    area_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class LocationTarget:
    """The one place a Guard or camera is linked to (the most specific level chosen)."""

    level: LocationLevel
    id: UUID
