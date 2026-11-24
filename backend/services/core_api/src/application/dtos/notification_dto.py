from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.domain.entities.notification import Notification


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    kind: str
    title: str
    alert_id: UUID | None
    manual_report_id: UUID | None
    subject_user_id: UUID | None
    camera_id: UUID | None = None
    incident_id: UUID | None = None
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def of(cls, n: Notification) -> "NotificationResponse":
        return cls(
            id=n.id,
            kind=n.kind,
            title=n.title,
            alert_id=n.alert_id,
            manual_report_id=n.manual_report_id,
            subject_user_id=n.subject_user_id,
            camera_id=n.camera_id,
            incident_id=n.incident_id,
            created_at=n.created_at,
            read_at=n.read_at,
        )


class ReadAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marked_read: int
