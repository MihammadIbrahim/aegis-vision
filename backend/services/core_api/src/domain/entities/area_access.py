"""Restricted areas and personnel access permissions (decision D-016).

* A **restricted area** is an area of the campus location hierarchy (0013) that the Admin has
  marked as restricted (server room, laboratory, ...).
* An **access grant** lets one person enter one restricted area: a validity period, an optional
  weekly schedule (days and a same-day time window in a named time zone), and revocation. Revoked
  grants are kept as history. A person can hold grants for many areas; an area can have many
  authorized people; there is at most one active grant per person and area.
"""

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.domain.exceptions.domain_errors import ValidationFailed


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class AccessSchedule:
    """Allowed ISO weekdays (1 = Monday ... 7 = Sunday) and a same-day window [start, end)."""

    days: tuple[int, ...]
    start: time
    end: time
    timezone: str

    def __post_init__(self) -> None:
        if not self.days or len(set(self.days)) != len(self.days) or any(d not in range(1, 8) for d in self.days):
            raise ValidationFailed("Schedule days must be distinct weekdays 1 (Monday) to 7.", code="INVALID_SCHEDULE")
        if self.start >= self.end:
            raise ValidationFailed("The schedule must start before it ends on the same day.", code="INVALID_SCHEDULE")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValidationFailed("Unknown time zone for the schedule.", code="INVALID_TIMEZONE") from error

    def allows(self, at: datetime) -> bool:
        local = at.astimezone(ZoneInfo(self.timezone))
        return local.isoweekday() in self.days and self.start <= local.time().replace(tzinfo=None) < self.end


@dataclass(frozen=True, slots=True)
class RestrictedArea:
    area_id: UUID
    area_name: str
    building_id: UUID
    building_name: str
    floor_id: UUID | None
    floor_name: str | None
    is_restricted: bool
    access_note: str | None = None
    marked_at: datetime | None = None
    active_grants: int = 0


@dataclass(frozen=True, slots=True)
class AccessGrant:
    id: UUID
    personnel_id: UUID
    personnel_code: str
    personnel_name: str
    personnel_status: str
    category_label: str
    department: str | None
    area_id: UUID
    area_name: str
    floor_name: str | None
    building_name: str
    status: GrantStatus
    valid_from: datetime
    valid_until: datetime | None
    schedule: AccessSchedule | None
    note: str | None
    granted_at: datetime
    granted_by_name: str | None
    updated_at: datetime | None
    revoked_at: datetime | None = None
    revoked_by_name: str | None = None
    revoke_reason: str | None = None


@dataclass(frozen=True, slots=True)
class GrantTerms:
    """What the Admin sets when granting or updating access."""

    valid_from: datetime | None = None  # None: from now
    valid_until: datetime | None = None  # None: until revoked
    schedule: AccessSchedule | None = None
    note: str | None = None
