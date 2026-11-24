"""Alert bodies (decision D-013). Detections carry no images: ``snapshot_ref`` is only a reference
for the future evidence module and is never served by these routes."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.location_dto import LocationSelectionRequest
from src.domain.entities.alert import (
    Alert,
    AlertEvent,
    AlertMessage,
    AlertPriority,
    AlertStatus,
    AlertType,
    EscalationScope,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DetectionRequest(_Strict):
    """Sent by an AI worker. Unknown cameras and inactive organizations are refused."""

    camera_id: UUID
    detection_type: AlertType
    confidence: Decimal | None = Field(default=None, ge=0, le=1, max_digits=5, decimal_places=4)
    detected_at: datetime
    detection_id: str | None = Field(
        default=None, min_length=1, max_length=128, description="The worker's own ID; the same ID twice is ignored"
    )
    snapshot_ref: str | None = Field(default=None, max_length=1024)


class DetectionResponse(_Frozen):
    alert_id: UUID
    outcome: Literal["CREATED", "REPEATED", "DUPLICATE"]


class ClientAction(_Strict):
    client_action_id: UUID | None = Field(default=None, description="Set by an offline queue; resending is safe")


class AssessmentRequest(ClientAction):
    assessment: Literal["UNDER_CONTROL", "ADDITIONAL_GUARDS_REQUIRED"]


class ResolveRequest(ClientAction):
    note: str | None = Field(default=None, max_length=1000)


class DismissRequest(ClientAction):
    reason: str = Field(max_length=1000)


class EscalateRequest(ClientAction):
    scope: EscalationScope = Field(description="CAMPUS, LOCATION (building → floor → area) or GUARDS (Guard IDs)")
    location: LocationSelectionRequest | None = None
    guard_ids: list[str] = Field(default_factory=list, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class MessageRequest(_Strict):
    body: str = Field(max_length=2000)
    client_message_id: UUID | None = None


class RecipientResponse(_Frozen):
    guard_id: str
    full_name: str
    reason: str
    state: str
    assessment: str | None
    acknowledged_at: datetime | None
    responding_at: datetime | None


class AlertLocation(_Frozen):
    building_id: UUID | None
    building_name: str | None
    building_kind: str | None
    floor_id: UUID | None
    floor_name: str | None
    area_id: UUID | None
    area_name: str | None


class AlertResponse(_Frozen):
    id: UUID
    alert_type: AlertType
    priority: AlertPriority
    status: AlertStatus
    camera_id: UUID
    camera_name: str
    location: AlertLocation = Field(description="Where the camera was when the alert was raised")
    confidence: Decimal | None
    detected_at: datetime
    last_detected_at: datetime
    detection_count: int
    close_note: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    recipients: list[RecipientResponse] = Field(
        description="Staff: every Guard the alert went to. A Guard: only their own entry."
    )
    # Decision D-024.
    handled_by: UUID | None = Field(default=None, description="The responsible Admin or Supervisor")
    handled_by_name: str | None = None
    handled_at: datetime | None = None
    recipient_count: int = Field(default=0, description="Guards the alert went to")
    acknowledged_count: int = 0
    responding_count: int = 0
    has_snapshot: bool = Field(default=False, description="A detection snapshot is kept for the alert")

    @classmethod
    def of(cls, a: Alert) -> "AlertResponse":
        return cls(
            id=a.id,
            alert_type=a.alert_type,
            priority=a.priority,
            status=a.status,
            camera_id=a.camera_id,
            camera_name=a.camera_name,
            location=AlertLocation(
                building_id=a.building_id,
                building_name=a.building_name,
                building_kind=a.building_kind,
                floor_id=a.floor_id,
                floor_name=a.floor_name,
                area_id=a.area_id,
                area_name=a.area_name,
            ),
            confidence=a.confidence,
            detected_at=a.detected_at,
            last_detected_at=a.last_detected_at,
            detection_count=a.detection_count,
            close_note=a.close_note,
            closed_at=a.closed_at,
            created_at=a.created_at,
            updated_at=a.updated_at,
            recipients=[
                RecipientResponse(
                    guard_id=r.guard_id,
                    full_name=r.full_name,
                    reason=r.reason,
                    state=r.state,
                    assessment=r.assessment,
                    acknowledged_at=r.acknowledged_at,
                    responding_at=r.responding_at,
                )
                for r in a.recipients
            ],
            handled_by=a.handled_by,
            handled_by_name=a.handled_by_name,
            handled_at=a.handled_at,
            recipient_count=a.recipient_count,
            acknowledged_count=a.acknowledged_count,
            responding_count=a.responding_count,
            has_snapshot=a.has_snapshot,
        )


class HandlerRequest(_Strict):
    user_id: UUID | None = Field(description="An Admin or Supervisor of the campus; null clears it")


class AlertEscalationResponse(_Frozen):
    id: UUID
    scope: str = Field(description="CAMPUS, LOCATION, GUARDS, AUTOMATIC or ASSISTANCE")
    escalated_by_name: str | None = Field(description="null for automatic escalation")
    escalated_by_role: str | None
    note: str | None
    added_count: int
    created_at: datetime
    guards: list[str]


class AlertEventResponse(_Frozen):
    event_type: str
    actor_role: str
    actor_name: str | None
    from_status: str | None
    to_status: str | None
    details: dict[str, object]
    created_at: datetime

    @classmethod
    def of(cls, e: AlertEvent) -> "AlertEventResponse":
        return cls(
            event_type=e.event_type,
            actor_role=e.actor_role,
            actor_name=e.actor_name,
            from_status=e.from_status,
            to_status=e.to_status,
            details=e.details,
            created_at=e.created_at,
        )


class ActionResponse(_Frozen):
    alert_id: UUID
    outcome: Literal["DONE", "UNCHANGED", "REPLAYED"]
    status: AlertStatus


class EscalationResponse(_Frozen):
    alert: AlertResponse
    added_guards: int


class MessageResponse(_Frozen):
    id: UUID
    sender_role: str
    sender_name: str
    body: str
    created_at: datetime

    @classmethod
    def of(cls, m: AlertMessage) -> "MessageResponse":
        return cls(id=m.id, sender_role=m.sender_role, sender_name=m.sender_name, body=m.body, created_at=m.created_at)
