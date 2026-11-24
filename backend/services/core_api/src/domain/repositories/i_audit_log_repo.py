from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.audit_log import AuditPage, AuditRecord
from src.domain.entities.user import User


class IOrganizationAuditLog(Protocol):
    """Insert-only organization audit records (migration 0015). Never pass secrets or message text."""

    async def record(
        self,
        actor: User,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict[str, object],
        *,
        outcome: str = "SUCCESS",
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """Filters of the audit-log viewer (Org Admin page 21, Supervisor page 17). All optional."""

    categories: tuple[str, ...] | None = None
    actor_role: str | None = None
    actor: str | None = None  # the acting user's displayed ID (or internal ID)
    action: str | None = None
    outcome: str | None = None
    text: str | None = None  # keyword: action, target ID, actor, details
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    offset: int = 0


class IAuditLogReader(Protocol):
    """Reads organization audit records through Row-Level Security (migration 0024)."""

    async def page(self, where: AuditFilter) -> AuditPage: ...

    async def export_rows(self, where: AuditFilter, max_rows: int) -> list[AuditRecord]: ...


class IAuthEventLog(Protocol):
    """Sign-in and password-reset events of organization users (category AUTHENTICATION; Admins only)."""

    async def auth_event(self, user_id: UUID, action: str, outcome: str, metadata: dict[str, object]) -> None: ...

    async def reset_completed(self, request_id: UUID) -> None: ...
