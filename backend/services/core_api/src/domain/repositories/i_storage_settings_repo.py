"""The campus's routine-recording retention (migration 0023; decision D-023). Admins and
Supervisors read it; only Admins change it (the policy requires their password)."""

from typing import Protocol
from uuid import UUID

from src.domain.entities.retention_policy import RetentionPolicy, RetentionUnit


class IStorageSettingsRepository(Protocol):
    async def get_retention(self, organization_id: UUID, campus_id: UUID) -> RetentionPolicy | None:
        """None when the campus has not set one (the server default applies)."""
        ...

    async def set_retention(
        self, organization_id: UUID, campus_id: UUID, value: int, unit: RetentionUnit, actor_id: UUID
    ) -> None: ...
