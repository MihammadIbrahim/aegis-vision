"""The signed-in user's in-app notifications. They are stored and listed by the API; nothing is
pushed to devices (no FCM or WebSocket integration exists)."""

from uuid import UUID

from src.domain.entities.notification import Notification
from src.domain.exceptions.domain_errors import NotFound
from src.domain.repositories.i_notification_repo import INotificationRepository
from src.domain.repositories.i_unit_of_work import ITransaction


class ManageNotifications:
    def __init__(self, notifications: INotificationRepository, transaction: ITransaction) -> None:
        self._notifications = notifications
        self._transaction = transaction

    async def list(self, *, unread_only: bool, limit: int) -> list[Notification]:
        return await self._notifications.list_notifications(unread_only=unread_only, limit=limit)

    async def read(self, notification_id: UUID) -> None:
        if not await self._notifications.mark_read(notification_id):
            raise NotFound("Notification not found.", code="NOTIFICATION_NOT_FOUND")
        await self._transaction.commit()

    async def read_all(self) -> int:
        count = await self._notifications.mark_all_read()
        await self._transaction.commit()
        return count
