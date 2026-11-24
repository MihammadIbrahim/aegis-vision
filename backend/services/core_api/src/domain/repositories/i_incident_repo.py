"""Incidents & Investigations storage (migration 0025). Every query runs for the signed-in user under
Row-Level Security: staff see their own campus's cases; Guards only the chat of cases they take part in."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.domain.entities.incident import (
    FootageGap,
    Incident,
    IncidentEvent,
    IncidentLocation,
    IncidentMessage,
    IncidentPage,
    LinkedAlert,
    LinkedEvidence,
    LinkedReport,
    Participant,
    StaffMember,
)


@dataclass(frozen=True, slots=True)
class IncidentFilter:
    statuses: tuple[str, ...] | None = None
    priority: str | None = None
    assigned_to: UUID | None = None
    unassigned: bool = False
    text: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class NewIncident:
    id: UUID
    title: str
    description: str
    priority: str
    occurred_at: datetime
    camera_id: UUID | None
    camera_name: str | None
    location: IncidentLocation
    assigned_to: UUID | None
    assigned_name: str | None
    created_by: UUID
    created_by_name: str


@dataclass(frozen=True, slots=True)
class CameraRef:
    camera_id: UUID
    camera_name: str
    location: IncidentLocation


class IIncidentRepository(Protocol):
    async def create(self, new: NewIncident) -> Incident: ...

    async def page(self, where: IncidentFilter) -> IncidentPage: ...

    async def get(self, incident_id: UUID, *, lock: bool = False) -> Incident | None: ...

    async def update_details(self, incident_id: UUID, changes: dict[str, Any]) -> None: ...

    async def set_status(self, incident_id: UUID, status: str, note: str | None, closed: bool) -> None: ...

    async def set_assignee(self, incident_id: UUID, user_id: UUID | None, name: str | None) -> None: ...

    async def touch(self, incident_id: UUID) -> None: ...

    async def add_event(
        self,
        incident_id: UUID,
        kind: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        note: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    async def history(self, incident_id: UUID) -> list[IncidentEvent]: ...

    async def camera(self, camera_id: UUID) -> CameraRef | None: ...

    async def staff_member(self, user_id: UUID) -> StaffMember | None: ...

    async def assignable_staff(self) -> list[StaffMember]: ...

    async def guard(self, user_id: UUID) -> StaffMember | None: ...

    # Links ------------------------------------------------------------------------------------------------
    async def alert_exists(self, alert_id: UUID) -> bool: ...

    async def link_alert(self, incident_id: UUID, alert_id: UUID, by_name: str) -> bool: ...

    async def unlink_alert(self, incident_id: UUID, alert_id: UUID) -> bool: ...

    async def alerts(self, incident_id: UUID) -> list[LinkedAlert]: ...

    async def report_incident(self, report_id: UUID) -> tuple[bool, UUID | None]: ...

    async def set_report_incident(self, report_id: UUID, incident_id: UUID | None) -> None: ...

    async def reports(self, incident_id: UUID) -> list[LinkedReport]: ...

    async def evidence_incident(self, evidence_id: UUID) -> tuple[bool, UUID | None]: ...

    async def set_evidence_incident(self, evidence_id: UUID, incident_id: UUID | None) -> None: ...

    async def evidence(self, incident_id: UUID) -> list[LinkedEvidence]: ...

    async def incidents_of_alert(self, alert_id: UUID) -> list[Incident]: ...

    # Chat and participants --------------------------------------------------------------------------------
    async def add_participant(self, incident_id: UUID, guard_user_id: UUID) -> bool: ...

    async def remove_participant(self, incident_id: UUID, guard_user_id: UUID) -> bool: ...

    async def participants(self, incident_id: UUID) -> list[Participant]: ...

    async def messages(self, incident_id: UUID, *, after: datetime | None, limit: int) -> list[IncidentMessage]: ...

    async def post_message(self, incident_id: UUID, sender_name: str, body: str) -> IncidentMessage: ...

    async def participant_incidents(self) -> list[Incident]: ...

    # Missing footage --------------------------------------------------------------------------------------
    async def footage_gaps(self, windows: list[tuple[UUID | None, str, datetime, datetime]]) -> list[FootageGap]: ...

    async def notify(self, user_id: UUID, kind: str, incident_id: UUID, title: str) -> None: ...

    async def person_search_links(self, incident_id: UUID) -> list[dict[str, Any]]: ...
