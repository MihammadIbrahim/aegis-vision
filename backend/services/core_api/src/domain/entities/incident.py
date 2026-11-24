"""Incidents & Investigations (requirements Sections 15.5 and 16; Org Admin page 6, Supervisor page 6;
decision D-024; migration 0025).

A case belongs to one campus. Admins and Supervisors of that campus open and manage it; Guards take part
only in its chat (when invited, or when they received one of its alerts). Nothing about a case is ever
deleted; names are copied so the record survives removed accounts, cameras and locations.

Status flow (Supervisor page 6, item 7): Open → Under Investigation → Responding → Resolved, or Dismissed.
An active case may move to any other status; a Resolved or Dismissed case can only be reopened (to Under
Investigation). Resolving, dismissing and reopening need a note.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.domain.exceptions.domain_errors import InvalidStateTransition, ValidationFailed


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    RESPONDING = "RESPONDING"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"

    @property
    def is_closed(self) -> bool:
        return self in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED)


ACTIVE_STATUSES = (IncidentStatus.OPEN, IncidentStatus.UNDER_INVESTIGATION, IncidentStatus.RESPONDING)


class IncidentPriority(StrEnum):
    HIGH = "HIGH"
    STANDARD = "STANDARD"


MAX_TITLE = 200
MAX_DESCRIPTION = 5000
MAX_NOTE = 2000


def clean_title(value: str) -> str:
    title = " ".join(value.split())
    if not 3 <= len(title) <= MAX_TITLE:
        raise ValidationFailed(f"The title must be 3 to {MAX_TITLE} characters.", code="INVALID_TITLE")
    return title


def clean_description(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise ValidationFailed(f"The description must be at most {MAX_DESCRIPTION} characters.", code="INVALID_TEXT")
    return text


def clean_note(value: str | None, *, required: bool) -> str | None:
    text = (value or "").strip()
    if required and not text:
        raise ValidationFailed("Add a note explaining this change.", code="NOTE_REQUIRED")
    if len(text) > MAX_NOTE:
        raise ValidationFailed(f"The note must be at most {MAX_NOTE} characters.", code="INVALID_NOTE")
    return text or None


def check_transition(current: IncidentStatus, target: IncidentStatus) -> None:
    """Raises when ``current`` cannot become ``target`` (see the module docstring)."""
    if current is target:
        raise InvalidStateTransition("The case already has this status.", code="STATUS_UNCHANGED")
    if current.is_closed and target is not IncidentStatus.UNDER_INVESTIGATION:
        raise InvalidStateTransition(
            "A resolved or dismissed case can only be reopened for investigation.", code="INCIDENT_CLOSED"
        )


def note_required(current: IncidentStatus, target: IncidentStatus) -> bool:
    return target.is_closed or current.is_closed


def display_number(number: int) -> str:
    return f"INC-{number:06d}"


@dataclass(frozen=True, slots=True)
class IncidentLocation:
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None
    building_name: str | None = None
    floor_name: str | None = None
    area_name: str | None = None

    @property
    def label(self) -> str | None:
        parts = [p for p in (self.building_name, self.floor_name, self.area_name) if p]
        return " › ".join(parts) or None


@dataclass(frozen=True, slots=True)
class Incident:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    number: int
    title: str
    description: str
    status: IncidentStatus
    priority: IncidentPriority
    occurred_at: datetime
    camera_id: UUID | None
    camera_name: str | None
    location: IncidentLocation
    assigned_to: UUID | None
    assigned_name: str | None
    created_by: UUID | None
    created_by_name: str
    resolution_note: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Counts for lists (filled by the repository).
    alert_count: int = 0
    report_count: int = 0
    evidence_count: int = 0

    @property
    def reference(self) -> str:
        return display_number(self.number)


@dataclass(frozen=True, slots=True)
class IncidentEvent:
    id: UUID
    kind: str
    actor_user_id: UUID | None
    actor_name: str
    actor_role: str
    from_status: str | None
    to_status: str | None
    note: str | None
    details: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class LinkedAlert:
    """An AI alert of the case with its detection (the "related AI event", page 6 item 5)."""

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
    location_label: str | None
    has_snapshot: bool
    linked_by_name: str
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class LinkedReport:
    report_id: UUID
    report_type: str
    description: str
    reporter_name: str
    reporter_guard_id: str
    location_label: str | None
    occurred_at: datetime | None
    created_at: datetime
    status: str
    has_photo: bool


@dataclass(frozen=True, slots=True)
class LinkedEvidence:
    evidence_id: UUID
    kind: str
    title: str
    camera_name: str
    covers_from: datetime
    covers_to: datetime
    saved_by_name: str
    saved_at: datetime
    media_type: str


@dataclass(frozen=True, slots=True)
class Participant:
    user_id: UUID
    display_id: str
    full_name: str
    invited: bool  # False: takes part because they received one of the case's alerts


@dataclass(frozen=True, slots=True)
class FootageGap:
    """A limit of the evidence the system states openly (page 6 item 19): no or partial recording around a
    moment, a camera that is now offline, or a camera that was removed."""

    camera_id: UUID | None
    camera_name: str
    window_from: datetime
    window_to: datetime
    issue: str  # NO_RECORDING | PARTIAL_RECORDING | CAMERA_OFFLINE | CAMERA_REMOVED
    covered_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class IncidentMessage:
    id: UUID
    sender_user_id: UUID | None
    sender_name: str
    sender_role: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentDetail:
    incident: Incident
    alerts: list[LinkedAlert] = field(default_factory=list)
    reports: list[LinkedReport] = field(default_factory=list)
    evidence: list[LinkedEvidence] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    gaps: list[FootageGap] = field(default_factory=list)
    history: list[IncidentEvent] = field(default_factory=list)
    person_search: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IncidentPage:
    items: list[Incident]
    total: int
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class StaffMember:
    user_id: UUID
    display_id: str
    full_name: str
    role: str
