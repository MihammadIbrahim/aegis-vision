"""A Guard's manual security report: a separate record from AI alerts (requirements 7, 15.6)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ManualReport:
    id: UUID
    reporter_guard_id: str
    reporter_name: str
    report_type: str
    description: str
    building_id: UUID | None
    building_name: str
    floor_id: UUID | None
    floor_name: str | None
    area_id: UUID | None
    area_name: str | None
    has_photo: bool
    occurred_at: datetime | None
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime
