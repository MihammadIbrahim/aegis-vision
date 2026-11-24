"""The signed-in user's own in-app notifications (Row-Level Security: own rows only)."""

from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.notification import Notification


class SqlNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_notifications(self, *, unread_only: bool, limit: int) -> list[Notification]:
        where = "WHERE read_at IS NULL" if unread_only else ""
        rows = await self._session.execute(
            text(
                "SELECT id, kind, title, alert_id, manual_report_id, created_at, read_at, subject_user_id, camera_id, "
                "incident_id FROM user_notifications "
                f"{where} ORDER BY created_at DESC, id LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [Notification(*r) for r in rows]

    async def mark_read(self, notification_id: UUID) -> bool:
        statement = text(
            "UPDATE user_notifications SET read_at = COALESCE(read_at, now()) WHERE id = :id RETURNING id"
        ).bindparams(bindparam("id", type_=Uuid))
        return (await self._session.execute(statement, {"id": notification_id})).first() is not None

    async def mark_all_read(self) -> int:
        rows = await self._session.execute(
            text("UPDATE user_notifications SET read_at = now() WHERE read_at IS NULL RETURNING id")
        )
        return len(rows.all())
