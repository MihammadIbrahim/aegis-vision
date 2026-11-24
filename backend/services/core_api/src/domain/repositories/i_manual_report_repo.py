from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.manual_report import ManualReport


@dataclass(frozen=True, slots=True)
class ReportSubmission:
    report_id: UUID
    client_report_id: UUID
    report_type: str
    description: str
    building_id: UUID
    floor_id: UUID | None
    area_id: UUID | None
    photo_path: str | None
    photo_content_type: str | None
    occurred_at: datetime | None


class IManualReportRepository(Protocol):
    async def submit(self, report: ReportSubmission) -> tuple[UUID | None, str]: ...
    async def get(self, report_id: UUID) -> ManualReport | None: ...
    async def photo(self, report_id: UUID) -> tuple[str, str] | None: ...
    async def list_reports(self, *, limit: int, offset: int) -> list[ManualReport]: ...
    async def review(self, report_id: UUID, reviewer_id: UUID, note: str | None) -> bool: ...
    async def guard_locations(self) -> list[dict[str, object]]: ...
