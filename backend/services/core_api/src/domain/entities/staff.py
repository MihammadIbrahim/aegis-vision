"""A Supervisor or Guard account as Staff Management sees it (web Phase 8; decision D-017).

A Guard's operational category follows from its location assignment: **STANDBY** (no building,
floor or area; campus-wide dispatch and escalations) or **STATIONED** (stationed at one location; it
receives the alerts routed there). Supervisors have no category.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.value_objects.enums import AccountStatus, UserRole
from src.domain.value_objects.location_path import LocationPath


class GuardCategory(StrEnum):
    STANDBY = "STANDBY"
    STATIONED = "STATIONED"


@dataclass(frozen=True, slots=True)
class StaffMember:
    user_id: UUID
    display_id: str
    role: UserRole
    status: AccountStatus
    full_name: str
    username: str | None
    email: str
    phone: str | None
    created_at: datetime
    location: LocationPath | None = None

    @property
    def category(self) -> GuardCategory | None:
        if self.role is not UserRole.GUARD:
            return None
        return GuardCategory.STANDBY if self.location is None else GuardCategory.STATIONED
