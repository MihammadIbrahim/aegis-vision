"""Incidents & Investigations bodies (decision D-024)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.location_dto import LocationSelectionRequest
from src.application.use_cases.incident_use_cases import IncidentDetails
from src.domain.entities.incident import (
    FootageGap,
    Incident,
    IncidentDetail,
    IncidentEvent,
    IncidentMessage,
    IncidentPage,
    IncidentPriority,
    IncidentStatus,
    LinkedAlert,
    LinkedEvidence,
    LinkedReport,
    Participant,
    StaffMember,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IncidentRequest(_Strict):
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=5000)
    priority: IncidentPriority = IncidentPriority.STANDARD
    occurred_at: datetime | None = Field(default=None, description="When it happened; now when omitted")
    camera_id: UUID | None = None
    location: LocationSelectionRequest | None = Field(
        default=None, description="Building → floor → area; the camera's location when omitted"
    )

    def to_details(self) -> IncidentDetails:
        return IncidentDetails(
            title=self.title,
            description=self.description,
            priority=self.priority,
            occurred_at=self.occurred_at,
            camera_id=self.camera_id,
            location=self.location.to_selection() if self.location else None,
        )


class CreateIncidentRequest(IncidentRequest):
    assigned_to: UUID | None = Field(default=None, description="An Admin or Supervisor of the campus")
    alert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    report_ids: list[UUID] = Field(default_factory=list, max_length=50)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)


class StatusChangeRequest(_Strict):
    status: IncidentStatus
    note: str | None = Field(default=None, max_length=2000, description="Needed to resolve, dismiss or reopen")


class AssigneeRequest(_Strict):
    user_id: UUID | None = Field(description="null removes the assignment")


class NoteRequest(_Strict):
    note: str = Field(min_length=1, max_length=2000)


class MessageRequest(_Strict):
    body: str = Field(min_length=1, max_length=2000)


class IncidentResponse(_Out):
    id: UUID
    reference: str = Field(description="INC-000001, numbered per campus")
    title: str
    description: str
    status: IncidentStatus
    priority: IncidentPriority
    occurred_at: datetime
    camera_id: UUID | None
    camera_name: str | None
    building_id: UUID | None
    floor_id: UUID | None
    area_id: UUID | None
    location: str | None
    assigned_to: UUID | None
    assigned_name: str | None
    created_by: UUID | None
    created_by_name: str
    resolution_note: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    alert_count: int
    report_count: int
    evidence_count: int

    @classmethod
    def of(cls, i: Incident) -> "IncidentResponse":
        return cls(
            id=i.id,
            reference=i.reference,
            title=i.title,
            description=i.description,
            status=i.status,
            priority=i.priority,
            occurred_at=i.occurred_at,
            camera_id=i.camera_id,
            camera_name=i.camera_name,
            building_id=i.location.building_id,
            floor_id=i.location.floor_id,
            area_id=i.location.area_id,
            location=i.location.label,
            assigned_to=i.assigned_to,
            assigned_name=i.assigned_name,
            created_by=i.created_by,
            created_by_name=i.created_by_name,
            resolution_note=i.resolution_note,
            closed_at=i.closed_at,
            created_at=i.created_at,
            updated_at=i.updated_at,
            alert_count=i.alert_count,
            report_count=i.report_count,
            evidence_count=i.evidence_count,
        )


class IncidentPageResponse(_Out):
    items: list[IncidentResponse]
    total: int
    limit: int
    offset: int
    counts: dict[str, int] = Field(description="Cases per status for the same search")

    @classmethod
    def of(cls, page: IncidentPage, limit: int, offset: int) -> "IncidentPageResponse":
        return cls(
            items=[IncidentResponse.of(i) for i in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
            counts=page.counts,
        )


class LinkedAlertResponse(_Out):
    alert_id: UUID
    alert_type: str
    priority: str
    status: str
    confidence: float | None
    detected_at: datetime
    last_detected_at: datetime
    detection_count: int
    camera_id: UUID | None
    camera_name: str
    location: str | None
    has_snapshot: bool
    linked_by_name: str
    linked_at: datetime

    @classmethod
    def of(cls, a: LinkedAlert) -> "LinkedAlertResponse":
        return cls(
            alert_id=a.alert_id,
            alert_type=a.alert_type,
            priority=a.priority,
            status=a.status,
            confidence=a.confidence,
            detected_at=a.detected_at,
            last_detected_at=a.last_detected_at,
            detection_count=a.detection_count,
            camera_id=a.camera_id,
            camera_name=a.camera_name,
            location=a.location_label,
            has_snapshot=a.has_snapshot,
            linked_by_name=a.linked_by_name,
            linked_at=a.linked_at,
        )


class LinkedReportResponse(_Out):
    report_id: UUID
    report_type: str
    description: str
    reporter_name: str
    reporter_guard_id: str
    location: str | None
    occurred_at: datetime | None
    created_at: datetime
    status: str
    has_photo: bool

    @classmethod
    def of(cls, r: LinkedReport) -> "LinkedReportResponse":
        return cls(
            report_id=r.report_id,
            report_type=r.report_type,
            description=r.description,
            reporter_name=r.reporter_name,
            reporter_guard_id=r.reporter_guard_id,
            location=r.location_label,
            occurred_at=r.occurred_at,
            created_at=r.created_at,
            status=r.status,
            has_photo=r.has_photo,
        )


class LinkedEvidenceResponse(_Out):
    evidence_id: UUID
    kind: str
    title: str
    camera_name: str
    covers_from: datetime
    covers_to: datetime
    saved_by_name: str
    saved_at: datetime
    media_type: str

    @classmethod
    def of(cls, e: LinkedEvidence) -> "LinkedEvidenceResponse":
        return cls(
            evidence_id=e.evidence_id,
            kind=e.kind,
            title=e.title,
            camera_name=e.camera_name,
            covers_from=e.covers_from,
            covers_to=e.covers_to,
            saved_by_name=e.saved_by_name,
            saved_at=e.saved_at,
            media_type=e.media_type,
        )


class ParticipantResponse(_Out):
    user_id: UUID
    display_id: str
    full_name: str
    invited: bool = Field(description="false: takes part as a recipient of one of the case's alerts")

    @classmethod
    def of(cls, p: Participant) -> "ParticipantResponse":
        return cls(user_id=p.user_id, display_id=p.display_id, full_name=p.full_name, invited=p.invited)


class FootageGapResponse(_Out):
    camera_id: UUID | None
    camera_name: str
    window_from: datetime
    window_to: datetime
    issue: str = Field(description="NO_RECORDING, PARTIAL_RECORDING, CAMERA_OFFLINE or CAMERA_REMOVED")
    covered_seconds: float

    @classmethod
    def of(cls, g: FootageGap) -> "FootageGapResponse":
        return cls(
            camera_id=g.camera_id,
            camera_name=g.camera_name,
            window_from=g.window_from,
            window_to=g.window_to,
            issue=g.issue,
            covered_seconds=round(g.covered_seconds, 1),
        )


class IncidentEventResponse(_Out):
    id: UUID
    kind: str
    actor_name: str
    actor_role: str
    from_status: str | None
    to_status: str | None
    note: str | None
    details: dict[str, Any]
    occurred_at: datetime

    @classmethod
    def of(cls, e: IncidentEvent) -> "IncidentEventResponse":
        return cls(
            id=e.id,
            kind=e.kind,
            actor_name=e.actor_name,
            actor_role=e.actor_role,
            from_status=e.from_status,
            to_status=e.to_status,
            note=e.note,
            details=e.details,
            occurred_at=e.occurred_at,
        )


class IncidentDetailResponse(_Out):
    incident: IncidentResponse
    alerts: list[LinkedAlertResponse]
    reports: list[LinkedReportResponse]
    evidence: list[LinkedEvidenceResponse]
    participants: list[ParticipantResponse]
    footage_gaps: list[FootageGapResponse]
    history: list[IncidentEventResponse]
    person_search: list[dict[str, Any]] = Field(description="Person-search results linked to the case")
    ai_assistance_available: bool = Field(
        default=False, description="The AI investigation summary and recommendations (agent not deployed)"
    )

    @classmethod
    def of(cls, d: IncidentDetail) -> "IncidentDetailResponse":
        return cls(
            incident=IncidentResponse.of(d.incident),
            alerts=[LinkedAlertResponse.of(a) for a in d.alerts],
            reports=[LinkedReportResponse.of(r) for r in d.reports],
            evidence=[LinkedEvidenceResponse.of(e) for e in d.evidence],
            participants=[ParticipantResponse.of(p) for p in d.participants],
            footage_gaps=[FootageGapResponse.of(g) for g in d.gaps],
            history=[IncidentEventResponse.of(e) for e in d.history],
            person_search=d.person_search,
        )


class MessageResponse(_Out):
    id: UUID
    sender_user_id: UUID | None
    sender_name: str
    sender_role: str
    body: str
    created_at: datetime

    @classmethod
    def of(cls, m: IncidentMessage) -> "MessageResponse":
        return cls(
            id=m.id,
            sender_user_id=m.sender_user_id,
            sender_name=m.sender_name,
            sender_role=m.sender_role,
            body=m.body,
            created_at=m.created_at,
        )


class StaffMemberResponse(_Out):
    user_id: UUID
    display_id: str
    full_name: str
    role: str

    @classmethod
    def of(cls, s: StaffMember) -> "StaffMemberResponse":
        return cls(user_id=s.user_id, display_id=s.display_id, full_name=s.full_name, role=s.role)
