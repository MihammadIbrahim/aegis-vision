"""How long a campus keeps routine recordings (requirements Section 17; decision D-023).

The Admin sets a value and a unit (Minutes, Hours or Days). Evidence in the vault is not covered by
any retention: it never expires (see ``evidence.py``).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.exceptions.domain_errors import ValidationFailed

MIN_RETENTION_SECONDS = 60
MAX_RETENTION_SECONDS = 365 * 86400


class RetentionUnit(StrEnum):
    MINUTES = "MINUTES"
    HOURS = "HOURS"
    DAYS = "DAYS"


_SECONDS = {RetentionUnit.MINUTES: 60, RetentionUnit.HOURS: 3600, RetentionUnit.DAYS: 86400}


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    value: int
    unit: RetentionUnit
    is_default: bool = False
    updated_at: datetime | None = None
    updated_by: UUID | None = None
    updated_by_name: str | None = None

    @property
    def seconds(self) -> int:
        return self.value * _SECONDS[self.unit]

    @classmethod
    def checked(cls, value: int, unit: RetentionUnit) -> "RetentionPolicy":
        if value < 1:
            raise ValidationFailed("The retention value must be at least 1.", code="INVALID_RETENTION")
        seconds = value * _SECONDS[unit]
        if seconds < MIN_RETENTION_SECONDS or seconds > MAX_RETENTION_SECONDS:
            raise ValidationFailed(
                "Routine recordings can be kept between 1 minute and 365 days.", code="INVALID_RETENTION"
            )
        return cls(value, unit)

    @classmethod
    def default_days(cls, days: int) -> "RetentionPolicy":
        return cls(days, RetentionUnit.DAYS, is_default=True)
