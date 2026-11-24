"""Organization audit records as people read them (requirements Section 18; Org Admin page 21,
Supervisor page 17; decision D-024).

Records are written by every module (insert-only, migration 0015) and are never edited or deleted.
Each has a category computed by the database from the action (``app_audit_category``, migration 0024).
Admins read every record of their campus; Supervisors read the operational categories and their own
records. Records never contain passwords, codes or tokens.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class AuditCategory(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    USER_MANAGEMENT = "USER_MANAGEMENT"
    CAMERA_LOCATION = "CAMERA_LOCATION"
    AI_SETTINGS = "AI_SETTINGS"
    ALERTS = "ALERTS"
    GUARD_RESPONSE = "GUARD_RESPONSE"
    INCIDENT_CHAT = "INCIDENT_CHAT"
    INCIDENTS = "INCIDENTS"
    EVIDENCE = "EVIDENCE"
    RETENTION = "RETENTION"
    VIDEO = "VIDEO"
    PERSON_SEARCH = "PERSON_SEARCH"
    MANUAL_REPORTS = "MANUAL_REPORTS"
    PERSONNEL = "PERSONNEL"
    ORGANIZATION = "ORGANIZATION"
    AI_ASSISTANT = "AI_ASSISTANT"  # chatbot questions and tool calls (decision D-027); not operational
    OTHER = "OTHER"


# What Supervisors may read (must match OPERATIONAL_CATEGORIES in migration 0024).
OPERATIONAL_CATEGORIES: frozenset[AuditCategory] = frozenset(
    {
        AuditCategory.ALERTS,
        AuditCategory.GUARD_RESPONSE,
        AuditCategory.CAMERA_LOCATION,
        AuditCategory.AI_SETTINGS,
        AuditCategory.EVIDENCE,
        AuditCategory.VIDEO,
        AuditCategory.PERSON_SEARCH,
        AuditCategory.MANUAL_REPORTS,
        AuditCategory.INCIDENTS,
        AuditCategory.INCIDENT_CHAT,
    }
)


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: UUID
    occurred_at: datetime
    category: AuditCategory
    action: str
    outcome: AuditOutcome
    actor_user_id: UUID | None
    actor_display_id: str | None
    actor_name: str | None
    actor_role: str
    target_type: str
    target_id: UUID | None
    target_label: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: list[AuditRecord]
    total: int
