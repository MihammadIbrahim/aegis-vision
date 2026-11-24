"""Manual report bodies. The photo travels as base64 (JPEG or PNG), so a queued offline report is
one self-contained request."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.manual_report_use_cases import ReportDraft
from src.domain.entities.manual_report import ManualReport


class ManualReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_report_id: UUID = Field(description="Created by the app; resending the same report is safe")
    report_type: str = Field(max_length=60, description="For example Suspicious Person or Property Damage")
    description: str = Field(max_length=4000)
    building_id: UUID
    floor_id: UUID | None = None
    area_id: UUID | None = None
    occurred_at: datetime | None = None
    photo_base64: str | None = Field(default=None, max_length=28_000_000)

    def to_draft(self) -> ReportDraft:
        return ReportDraft(
            client_report_id=self.client_report_id,
            report_type=self.report_type,
            description=self.description,
            building_id=self.building_id,
            floor_id=self.floor_id,
            area_id=self.area_id,
            occurred_at=self.occurred_at,
            photo_base64=self.photo_base64,
        )


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class ManualReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    guard_id: str
    guard_name: str
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
    status: Literal["SUBMITTED", "REVIEWED"]
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, r: ManualReport) -> "ManualReportResponse":
        return cls(
            id=r.id,
            guard_id=r.reporter_guard_id,
            guard_name=r.reporter_name,
            report_type=r.report_type,
            description=r.description,
            building_id=r.building_id,
            building_name=r.building_name,
            floor_id=r.floor_id,
            floor_name=r.floor_name,
            area_id=r.area_id,
            area_name=r.area_name,
            has_photo=r.has_photo,
            occurred_at=r.occurred_at,
            status=r.status,  # type: ignore[arg-type]
            review_note=r.review_note,
            reviewed_at=r.reviewed_at,
            created_at=r.created_at,
        )


class SubmittedReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report: ManualReportResponse
    created: bool = Field(description="False when this client_report_id had already been received")


class GuardLocationOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    building_id: UUID
    building_name: str
    building_kind: str
    floor_id: UUID | None
    floor_name: str | None
    floor_level: int | None
    area_id: UUID | None
    area_name: str | None
