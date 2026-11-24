"""An in-app notification (requirements 18). Delivery is by the API only: no push or WebSocket yet."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Notification:
    id: UUID
    kind: str
    title: str
    alert_id: UUID | None
    manual_report_id: UUID | None
    created_at: datetime
    read_at: datetime | None
    # The Guard a staff notification is about (migration 0018); None for alert and report notifications.
    subject_user_id: UUID | None = None
    # The camera a CAMERA_OFFLINE / CAMERA_ONLINE notification is about (migration 0023); None otherwise.
    camera_id: UUID | None = None
    # The case an INCIDENT_* notification is about (migration 0025); None otherwise.
    incident_id: UUID | None = None
