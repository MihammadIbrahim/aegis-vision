from dataclasses import dataclass
from uuid import UUID

from src.domain.value_objects.enums import CampusStatus


@dataclass(frozen=True, slots=True)
class Campus:
    """An approved campus / branch / workspace. Each campus has isolated operational data."""

    id: UUID
    organization_id: UUID
    name: str
    status: CampusStatus
