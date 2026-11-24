"""Audit-log viewer bodies (decision D-024). Records never contain passwords, codes or tokens."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.entities.audit_log import AuditCategory, AuditOutcome, AuditPage, AuditRecord


class AuditRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    occurred_at: datetime
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    actor_user_id: UUID | None
    actor_display_id: str | None = Field(description="The acting user's displayed ID; null for system actions")
    actor_name: str | None
    actor_role: str
    target_type: str
    target_id: UUID | None
    target_label: str | None = Field(description="A readable name for user and camera targets")
    details: dict[str, Any]

    @classmethod
    def of(cls, r: AuditRecord) -> "AuditRecordResponse":
        return cls(
            id=r.id,
            occurred_at=r.occurred_at,
            category=r.category,
            action=r.action,
            outcome=r.outcome,
            actor_user_id=r.actor_user_id,
            actor_display_id=r.actor_display_id,
            actor_name=r.actor_name,
            actor_role=r.actor_role,
            target_type=r.target_type,
            target_id=r.target_id,
            target_label=r.target_label,
            details=r.metadata,
        )


class AuditPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[AuditRecordResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, page: AuditPage, limit: int, offset: int) -> "AuditPageResponse":
        return cls(items=[AuditRecordResponse.of(r) for r in page.items], total=page.total, limit=limit, offset=offset)


class AuditCategoriesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: list[AuditCategory] = Field(description="The categories the caller may read")
