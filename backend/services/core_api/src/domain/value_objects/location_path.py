"""A location written out from the building down, for display (names and IDs)."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LocationPath:
    building_id: UUID
    building_name: str
    building_kind: str
    floor_id: UUID | None = None
    floor_name: str | None = None
    area_id: UUID | None = None
    area_name: str | None = None

    @property
    def label(self) -> str:
        """Building › Floor › Area, as people read it (audit records, exports)."""
        return " › ".join(p for p in (self.building_name, self.floor_name, self.area_name) if p)

    def is_within(self, level: str, location_id: UUID) -> bool:
        """At or below the given building, floor or area."""
        return {"BUILDING": self.building_id, "FLOOR": self.floor_id, "AREA": self.area_id}[level] == location_id
