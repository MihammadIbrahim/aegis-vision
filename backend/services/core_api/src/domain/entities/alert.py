"""AI alerts (requirements Section 15; decision D-013).

Lifecycle: NEW → ACKNOWLEDGED → RESPONDING → RESOLVED, plus ESCALATED (Admin/Supervisor widened the
recipients) and DISMISSED. RESOLVED and DISMISSED are final; the database refuses other changes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class AlertType(StrEnum):
    WEAPON = "WEAPON"
    FIGHT_VIOLENCE = "FIGHT_VIOLENCE"
    RESTRICTED_AREA = "RESTRICTED_AREA"


class AlertPriority(StrEnum):
    HIGH = "HIGH"
    STANDARD = "STANDARD"


class AlertStatus(StrEnum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESPONDING = "RESPONDING"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"

    @property
    def is_closed(self) -> bool:
        return self in (AlertStatus.RESOLVED, AlertStatus.DISMISSED)


class GuardAction(StrEnum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    RESPONDING = "RESPONDING"
    UNDER_CONTROL = "UNDER_CONTROL"
    ADDITIONAL_GUARDS_REQUIRED = "ADDITIONAL_GUARDS_REQUIRED"


class EscalationScope(StrEnum):
    CAMPUS = "CAMPUS"  # every active Guard of the alert's campus
    LOCATION = "LOCATION"  # Guards assigned at or below a building, floor or area
    GUARDS = "GUARDS"  # chosen Guards (Guard IDs)


HIGH_PRIORITY_TYPES = frozenset({AlertType.WEAPON, AlertType.FIGHT_VIOLENCE})


def priority_for(alert_type: AlertType) -> AlertPriority:
    """Weapon and fight/violence alerts are always high priority (requirements 14.3, 14.4, 15.1)."""
    return AlertPriority.HIGH if alert_type in HIGH_PRIORITY_TYPES else AlertPriority.STANDARD


@dataclass(frozen=True, slots=True)
class AlertRecipient:
    guard_user_id: UUID
    guard_id: str
    full_name: str
    reason: str
    state: str
    assessment: str | None
    added_at: datetime
    acknowledged_at: datetime | None
    responding_at: datetime | None


@dataclass(frozen=True, slots=True)
class Alert:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    camera_id: UUID
    camera_name: str
    alert_type: AlertType
    priority: AlertPriority
    status: AlertStatus
    confidence: Decimal | None
    detected_at: datetime
    last_detected_at: datetime
    detection_count: int
    building_id: UUID | None
    building_name: str | None
    building_kind: str | None
    floor_id: UUID | None
    floor_name: str | None
    area_id: UUID | None
    area_name: str | None
    closed_at: datetime | None
    close_note: str | None
    created_at: datetime
    updated_at: datetime
    recipients: list[AlertRecipient] = field(default_factory=list)
    # Decision D-024 (migration 0026): the responsible Admin or Supervisor, and counts for lists.
    handled_by: UUID | None = None
    handled_by_name: str | None = None
    handled_at: datetime | None = None
    recipient_count: int = 0
    acknowledged_count: int = 0
    responding_count: int = 0
    has_snapshot: bool = False


@dataclass(frozen=True, slots=True)
class AlertEvent:
    id: UUID
    event_type: str
    actor_role: str
    actor_name: str | None
    from_status: str | None
    to_status: str | None
    details: dict[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AlertMessage:
    id: UUID
    sender_role: str
    sender_name: str
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AlertEscalation:
    """A secondary broadcast of an alert (page 4 items 21, 25, 26): by staff, automatic, or a Guard's
    request for assistance; with the Guards it added."""

    id: UUID
    scope: str
    escalated_by_name: str | None
    escalated_by_role: str | None
    note: str | None
    added_count: int
    created_at: datetime
    guards: list[str] = field(default_factory=list)  # "Guard ID · name"
