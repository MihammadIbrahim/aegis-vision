from typing import Protocol
from uuid import UUID

from src.domain.entities.notification import Notification


class INotificationRepository(Protocol):
    async def list_notifications(self, *, unread_only: bool, limit: int) -> list[Notification]: ...
    async def mark_read(self, notification_id: UUID) -> bool: ...
    async def mark_all_read(self) -> int: ...
