from typing import Protocol
from uuid import UUID

from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.entities.platform_audit_event import PlatformAuditEvent


class IPlatformAdminRepository(Protocol):
    async def get_by_username(self, username: str, *, for_update: bool = False) -> PlatformAdmin | None: ...

    async def get_by_id(self, admin_id: UUID, *, for_update: bool = False) -> PlatformAdmin | None: ...

    async def get_primary(self, *, for_update: bool = False) -> PlatformAdmin | None: ...

    async def save_login_state(self, admin: PlatformAdmin) -> None: ...

    async def save_credentials(self, admin: PlatformAdmin) -> None:
        """Persist username, password hash and token version. Raises Conflict if the username is taken."""
        ...


class IPlatformAuditLog(Protocol):
    async def record(self, event: PlatformAuditEvent) -> None: ...
