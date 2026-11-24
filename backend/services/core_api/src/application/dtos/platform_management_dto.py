"""Platform Super Admin management bodies (decision D-019). ``organization_id`` is the organization's
public Organization ID (``ORG-`` + 11 digits), the same value tenant APIs call ``organization_id``.
Passwords are accepted for confirmation and never returned."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.platform_health_use_cases import HealthReport
from src.domain.entities.platform_management import (
    AI_MODULES,
    AI_MODULES_BUILT,
    AiSettings,
    AuditLogEntry,
    CampusBreakdown,
    OrganizationAiPermission,
    OrganizationSummary,
    PlatformCounts,
)

OrganizationStatusValue = Literal["ACTIVE", "INACTIVE", "SUSPENDED"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Organizations ------------------------------------------------------------------------------------


class MainAdminResponse(_Frozen):
    full_name: str
    username: str | None
    email: str
    phone_number: str | None


class OrganizationResponse(_Frozen):
    organization_id: str = Field(description="Organization ID (ORG- + 11 digits)")
    name: str
    official_email: str | None
    official_phone_number: str | None
    status: OrganizationStatusValue
    created_at: datetime = Field(description="Registration / onboarding date")
    main_admin: MainAdminResponse | None
    campus_count: int = Field(description="Approved campuses / branches")
    camera_count: int = Field(description="Registered cameras (no online/offline status exists)")

    @classmethod
    def of(cls, o: OrganizationSummary) -> "OrganizationResponse":
        admin = o.main_admin
        return cls(
            organization_id=o.organization_code,
            name=o.name,
            official_email=o.official_email,
            official_phone_number=o.official_phone,
            status=o.status.value,  # type: ignore[arg-type]
            created_at=o.created_at,
            main_admin=None
            if admin is None
            else MainAdminResponse(
                full_name=admin.full_name, username=admin.username, email=admin.email, phone_number=admin.phone
            ),
            campus_count=o.campus_count,
            camera_count=o.camera_count,
        )


class OrganizationPageResponse(_Frozen):
    items: list[OrganizationResponse]
    total: int
    limit: int
    offset: int


class CampusResponse(_Frozen):
    name: str
    status: str
    created_at: datetime
    building_count: int = Field(description="Indoor buildings")
    perimeter_count: int = Field(description="Outdoor perimeters")
    floor_count: int
    area_count: int
    camera_count: int
    admin_count: int = Field(description="Active Admins")
    supervisor_count: int = Field(description="Active Supervisors")
    guard_count: int = Field(description="Active Guards")

    @classmethod
    def of(cls, c: CampusBreakdown) -> "CampusResponse":
        return cls(
            name=c.campus_name,
            status=c.status,
            created_at=c.created_at,
            building_count=c.building_count,
            perimeter_count=c.perimeter_count,
            floor_count=c.floor_count,
            area_count=c.area_count,
            camera_count=c.camera_count,
            admin_count=c.admin_count,
            supervisor_count=c.supervisor_count,
            guard_count=c.guard_count,
        )


class OrganizationAiPermissionResponse(_Frozen):
    module_key: str = Field(description="human_detection, person_reid, weapon_detection, fight_detection, …")
    feature: str = Field(description="human_detection_tracking, person_reid, weapon_detection, …")
    label: str
    core: bool = Field(description="One of the four modules blocked per organization")
    blocked: bool = Field(description="Blocked for this organization by the Super Admin")
    platform_enabled: bool
    status: Literal["ALLOWED", "BLOCKED", "DISABLED_GLOBALLY"]

    @classmethod
    def of(cls, p: OrganizationAiPermission) -> "OrganizationAiPermissionResponse":
        return cls(
            module_key=p.module_key,
            feature=p.feature,
            label=p.label,
            core=p.core,
            blocked=p.blocked,
            platform_enabled=p.platform_enabled,
            status=p.status,  # type: ignore[arg-type]
        )


class OrganizationDetailResponse(OrganizationResponse):
    registered_address: None = Field(default=None, description="Not collected at registration (always null)")
    campuses: list[CampusResponse]
    ai_permissions: list[OrganizationAiPermissionResponse] = Field(
        default_factory=list, description="AI feature access for this organization (decision D-028)"
    )


class OrganizationAiPermissionsResponse(_Frozen):
    organization_id: str
    ai_permissions: list[OrganizationAiPermissionResponse]


class ChangeOrganizationAiPermissionRequest(_Strict):
    module_key: str = Field(min_length=1, max_length=64, description="A module key or its feature name")
    blocked: bool
    current_password: str = Field(min_length=1, max_length=256)


class UpdateMainAdminRequest(_Strict):
    full_name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    phone_number: str = Field(max_length=40)
    current_password: str = Field(min_length=1, max_length=1024, description="The Super Admin's own password")


class ChangeOrganizationStatusRequest(_Strict):
    status: OrganizationStatusValue
    reason: str | None = Field(default=None, max_length=500, description="Optional; kept in the audit log")
    current_password: str = Field(min_length=1, max_length=1024, description="The Super Admin's own password")


# --- AI settings ----------------------------------------------------------------------------------------


class _AiValues(BaseModel):
    human_detection_tracking_enabled: bool
    person_reid_enabled: bool
    weapon_detection_enabled: bool
    fight_violence_detection_enabled: bool
    restricted_area_face_recognition_enabled: bool
    rag_assistant_enabled: bool
    agentic_investigation_enabled: bool
    default_confidence_threshold: Decimal = Field(ge=Decimal("0.05"), le=Decimal("0.99"), decimal_places=2)
    frame_sample_fps: int = Field(ge=1, le=30)
    detection_cooldown_seconds: int = Field(ge=0, le=3600)
    max_task_retries: int = Field(ge=0, le=10)
    task_timeout_seconds: int = Field(ge=5, le=600)


class UpdateAiSettingsRequest(_AiValues):
    """Every switch and parameter (a full replacement), plus the Super Admin's own password."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)

    def values(self) -> dict[str, Any]:
        return self.model_dump(exclude={"current_password"})


class AiModuleStatus(_Frozen):
    key: str
    enabled: bool
    implemented: bool = Field(description="False: the module is not built yet, so enabling it has no effect")


class AiSettingsResponse(_AiValues):
    model_config = ConfigDict(extra="forbid", frozen=True)

    modules: list[AiModuleStatus]
    updated_at: datetime
    updated_by_username: str | None

    @classmethod
    def of(cls, s: AiSettings) -> "AiSettingsResponse":
        return cls(
            **s.values(),
            modules=[
                AiModuleStatus(key=k, enabled=getattr(s, k), implemented=k in AI_MODULES_BUILT) for k in AI_MODULES
            ],
            updated_at=s.updated_at,
            updated_by_username=s.updated_by_username,
        )


# --- Health ---------------------------------------------------------------------------------------------


class ServiceHealthResponse(_Frozen):
    key: str
    name: str
    status: Literal["OPERATIONAL", "DEGRADED", "DOWN", "NOT_DEPLOYED"]
    latency_ms: float | None
    detail: str
    metrics: dict[str, Any]


class StorageHealthResponse(_Frozen):
    path: str
    accessible: bool
    writable: bool
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    used_percent: float | None
    level: Literal["OK", "WARNING", "CRITICAL", "UNAVAILABLE"]
    warning_percent: int
    critical_percent: int


class HealthResponse(_Frozen):
    checked_at: datetime
    overall_status: Literal["OPERATIONAL", "DEGRADED", "DOWN"]
    summary: str
    services: list[ServiceHealthResponse]
    storage: StorageHealthResponse
    problems: list[str]

    @classmethod
    def of(cls, r: HealthReport) -> "HealthResponse":
        return cls(
            checked_at=r.checked_at,
            overall_status=r.overall_status,  # type: ignore[arg-type]
            summary=r.summary,
            services=[
                ServiceHealthResponse(
                    key=s.key,
                    name=s.name,
                    status=s.status,  # type: ignore[arg-type]
                    latency_ms=s.latency_ms,
                    detail=s.detail,
                    metrics=s.metrics,
                )  # fmt: skip
                for s in r.services
            ],
            storage=StorageHealthResponse(**{f: getattr(r.storage, f) for f in StorageHealthResponse.model_fields}),
            problems=r.problems,
        )


# --- Audit log ------------------------------------------------------------------------------------------


class AuditLogEntryResponse(_Frozen):
    id: str
    occurred_at: datetime
    actor_type: Literal["SUPER_ADMIN", "ORGANIZATION_ADMIN", "SYSTEM"]
    actor_username: str | None
    action: str
    outcome: Literal["SUCCESS", "FAILURE"]
    organization_id: str | None
    organization_name: str | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any] = Field(description="Event metadata; never passwords, codes or tokens")

    @classmethod
    def of(cls, e: AuditLogEntry) -> "AuditLogEntryResponse":
        return cls(
            id=str(e.id),
            occurred_at=e.occurred_at,
            actor_type=e.actor_type,  # type: ignore[arg-type]
            actor_username=e.actor_username,
            action=e.action,
            outcome=e.outcome,  # type: ignore[arg-type]
            organization_id=e.organization_code,
            organization_name=e.organization_name,
            ip_address=e.ip_address,
            user_agent=e.user_agent,
            details=e.details,
        )


class AuditLogPageResponse(_Frozen):
    items: list[AuditLogEntryResponse]
    total: int
    limit: int
    offset: int


class AuditActionResponse(_Frozen):
    action: str
    count: int


# --- Overview -------------------------------------------------------------------------------------------


class PlatformCountsResponse(_Frozen):
    organizations_total: int
    organizations_active: int
    organizations_inactive: int
    organizations_suspended: int
    deleted_organizations_total: int
    campuses_total: int
    campuses_active: int
    cameras_total: int = Field(description="Registered cameras across all organizations (no video access)")
    admins_active: int
    supervisors_active: int
    guards_active: int

    @classmethod
    def of(cls, c: PlatformCounts) -> "PlatformCountsResponse":
        return cls(**{f: getattr(c, f) for f in cls.model_fields})


class OverviewResponse(_Frozen):
    counts: PlatformCountsResponse
    health: dict[str, str] = Field(description="{overall_status, summary} of GET /platform/health")
    ai_modules: list[AiModuleStatus]
    recent_activity: list[AuditLogEntryResponse] = Field(description="The 10 most recent platform audit events")
