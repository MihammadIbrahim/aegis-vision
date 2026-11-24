from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PlatformAuditEvent:
    """One row of the platform audit log. Never holds a typed username, a password or a token."""

    action: str  # e.g. LOGIN_SUCCEEDED, PASSWORD_CHANGED
    outcome: str  # SUCCESS | FAILURE
    occurred_at: datetime
    actor_platform_admin_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # e.g. {"login_method": "RECOVERY"}
    ip_address: str | None = None
    user_agent: str | None = None
