"""Alert storage (migration 0015). Reads go through Row-Level Security: staff see their campus's
alerts, Guards only the alerts sent to them. Guard actions and detection intake are database
functions; staff actions are row writes that the database also limits to the campus's staff."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.domain.entities.alert import Alert, AlertEvent, AlertMessage, AlertStatus
from src.domain.entities.location import LocationTarget


@dataclass(frozen=True, slots=True)
class AlertFilter:
    status: str | None = None
    priority: str | None = None
    alert_type: str | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None
    limit: int = 100
    offset: int = 0
    # Decision D-024: search (alert ID, type, camera, location) and the detection time.
    text: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    handled_by: UUID | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    camera_id: UUID
    alert_type: str
    confidence: Decimal | None
    detected_at: datetime
    detection_id: str | None
    snapshot_ref: str | None


class IAlertRepository(Protocol):
    async def ingest(self, detection: Detection, *, dedup_seconds: int) -> tuple[UUID | None, str]: ...
    async def list_alerts(self, where: AlertFilter) -> list[Alert]: ...
    async def get(self, alert_id: UUID, *, lock: bool = False) -> Alert | None: ...
    async def history(self, alert_id: UUID) -> list[AlertEvent]: ...
    async def action_replayed(self, actor_id: UUID, client_action_id: UUID | None) -> bool: ...
    async def guard_action(
        self, alert_id: UUID, action: str, client_action_id: UUID | None
    ) -> tuple[str, str | None]: ...
    async def close(self, alert_id: UUID, status: AlertStatus, actor_id: UUID, note: str | None) -> None: ...
    async def set_status(self, alert_id: UUID, status: AlertStatus) -> None: ...
    async def add_event(
        self,
        alert: Alert,
        actor_id: UUID,
        actor_role: str,
        event_type: str,
        to_status: str,
        details: dict[str, object],
        client_action_id: UUID | None,
    ) -> None: ...
    async def eligible_guards(
        self, alert: Alert, *, location: LocationTarget | None, guard_ids: list[str] | None
    ) -> list[UUID]: ...
    async def add_escalation(
        self,
        alert: Alert,
        escalation_id: UUID,
        actor_id: UUID,
        scope: str,
        location: LocationTarget | None,
        note: str | None,
        guards: list[UUID],
    ) -> None: ...
    async def notify(self, alert: Alert, user_ids: list[UUID], kind: str, title: str) -> None: ...
    async def campus_staff(self, alert: Alert) -> list[UUID]: ...
    async def messages(self, alert_id: UUID) -> list[AlertMessage]: ...
    async def post_message(
        self,
        alert: Alert,
        message_id: UUID,
        sender_id: UUID,
        sender_role: str,
        sender_name: str,
        body: str,
        client_message_id: UUID | None,
    ) -> tuple[UUID, bool]: ...
