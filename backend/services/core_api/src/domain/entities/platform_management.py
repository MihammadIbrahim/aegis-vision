"""What the Platform Super Admin may see about organizations (decision D-019): metadata, contact details
and counts only. Never footage, stream addresses, alerts, chat, reports or personnel."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID


class OrganizationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


@dataclass(frozen=True, slots=True)
class PlatformCounts:
    organizations_total: int
    organizations_active: int
    organizations_inactive: int
    organizations_suspended: int
    campuses_total: int
    campuses_active: int
    cameras_total: int
    admins_active: int
    supervisors_active: int
    guards_active: int
    deleted_organizations_total: int


@dataclass(frozen=True, slots=True)
class MainAdminContact:
    full_name: str
    username: str | None
    email: str
    phone: str | None


@dataclass(frozen=True, slots=True)
class OrganizationSummary:
    organization_code: str
    name: str
    official_email: str | None
    official_phone: str | None
    status: OrganizationStatus
    created_at: datetime
    main_admin: MainAdminContact | None
    campus_count: int
    camera_count: int


@dataclass(frozen=True, slots=True)
class CampusBreakdown:
    campus_name: str
    status: str
    created_at: datetime
    building_count: int
    perimeter_count: int
    floor_count: int
    area_count: int
    camera_count: int
    admin_count: int
    supervisor_count: int
    guard_count: int


@dataclass(frozen=True, slots=True)
class OrganizationListQuery:
    q: str | None = None
    status: OrganizationStatus | None = None
    sort: str = "created_at"  # name | created_at | camera_count
    descending: bool = True
    limit: int = 25
    offset: int = 0


# --- Platform AI settings -------------------------------------------------------------------------

AI_MODULES: tuple[str, ...] = (
    "human_detection_tracking_enabled",
    "person_reid_enabled",
    "weapon_detection_enabled",
    "fight_violence_detection_enabled",
    "restricted_area_face_recognition_enabled",
    "rag_assistant_enabled",
    "agentic_investigation_enabled",
)
AI_PARAMETERS: tuple[str, ...] = (
    "default_confidence_threshold",
    "frame_sample_fps",
    "detection_cooldown_seconds",
    "max_task_retries",
    "task_timeout_seconds",
)
# Which modules exist in code today (honest status shown next to each switch).
AI_MODULES_BUILT: frozenset[str] = frozenset({"human_detection_tracking_enabled"})


@dataclass(frozen=True, slots=True)
class AiSettings:
    human_detection_tracking_enabled: bool
    person_reid_enabled: bool
    weapon_detection_enabled: bool
    fight_violence_detection_enabled: bool
    restricted_area_face_recognition_enabled: bool
    rag_assistant_enabled: bool
    agentic_investigation_enabled: bool
    default_confidence_threshold: Decimal
    frame_sample_fps: int
    detection_cooldown_seconds: int
    max_task_retries: int
    task_timeout_seconds: int
    updated_at: datetime
    updated_by_username: str | None

    def values(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (*AI_MODULES, *AI_PARAMETERS)}


@dataclass(frozen=True, slots=True)
class OrganizationAiPermission:
    """One vision module for one organization (decision D-028): allowed, blocked for this organization by the
    Super Admin, or disabled for the whole platform (``platform_ai_settings``)."""

    module_key: str  # human_detection, person_reid, weapon_detection, fight_detection, face_recognition
    feature: str  # human_detection_tracking, … (the platform setting is "<feature>_enabled")
    label: str
    core: bool  # one of the four modules the Super Admin blocks per organization
    blocked: bool
    platform_enabled: bool

    @property
    def status(self) -> str:
        if not self.platform_enabled:
            return "DISABLED_GLOBALLY"
        return "BLOCKED" if self.blocked else "ALLOWED"


# --- Platform audit log ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditLogQuery:
    organization: str | None = None  # Organization ID (code), exact, or part of the organization name
    action: str | None = None
    outcome: str | None = None
    date_from: datetime | None = None  # inclusive
    date_to: datetime | None = None  # exclusive
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AuditLogEntry:
    id: UUID
    occurred_at: datetime
    actor_type: str  # SUPER_ADMIN | SYSTEM
    actor_username: str | None
    action: str
    outcome: str
    organization_code: str | None
    organization_name: str | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any] = field(default_factory=dict)
