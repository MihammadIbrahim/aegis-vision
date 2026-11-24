"""Organization Settings (Org Admin page 25; decision D-024).

GET /organization/settings      Admins: profile, hierarchy scope, preferences, alert defaults, sign-in rules,
                                storage and AI summary, campus-request rules
PUT /organization/settings      the Main Admin only, with their password
GET /organization/preferences   any signed-in user: time zone and date/time formats
"""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.ai_config_use_cases import AiConfiguration
from src.application.use_cases.organization_settings_use_cases import (
    OrganizationSettingsService,
    SettingsChange,
    SettingsView,
    security_rules,
)
from src.domain.entities.user import User
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.repositories.ai_config_repository import SqlAiConfigRepository
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.organization_settings_repository import (
    SqlOrganizationSettingsRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.api.v1.storage_settings_router import password_confirmation
from src.presentation.dependencies import MemberDep, SessionDep

router = APIRouter(prefix="/organization", tags=["Organization settings"])


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreferencesResponse(_Out):
    time_zone: str
    date_format: str
    time_format: str


class OrganizationProfile(_Out):
    name: str
    organization_code: str
    official_email: str | None
    official_phone: str | None
    registered_at: datetime
    address_line: str | None
    city: str | None
    country: str | None


class AlertDefaults(_Out):
    auto_escalation_enabled: bool
    escalation_high_seconds: int
    escalation_standard_seconds: int
    assistance_broadcast_enabled: bool


class SecurityRulesResponse(_Out):
    sign_in_code_required: bool
    code_expires_seconds: int
    code_max_attempts: int
    lockout_after_failed_sign_ins: int
    lockout_minutes: int
    password_min_length: int
    password_max_length: int


class CampusSummaryResponse(_Out):
    campus_name: str
    retention_seconds: int
    retention_label: str
    retention_is_default: bool
    ai_modules: list[str]
    pending_campus_requests: int | None


class CampusRequestRules(_Out):
    decision_window_hours: int
    requires_exact_name_and_id: bool = True
    applicant_email_code: bool = True
    approval_needs_password_and_code: bool = True
    rejection_needs_password: bool = True
    rejection_erases_details: bool = True


class SettingsResponse(_Out):
    profile: OrganizationProfile
    preferences: PreferencesResponse
    alert_defaults: AlertDefaults
    is_main_admin: bool = Field(description="Main Admin (approves campuses, edits these settings) or campus Admin")
    can_edit: bool
    campus: CampusSummaryResponse
    security: SecurityRulesResponse
    campus_requests: CampusRequestRules
    updated_at: datetime | None
    updated_by_name: str | None

    @classmethod
    def of(cls, v: SettingsView) -> "SettingsResponse":
        s = v.settings
        return cls(
            profile=OrganizationProfile(
                name=s.name,
                organization_code=s.organization_code,
                official_email=s.official_email,
                official_phone=s.official_phone,
                registered_at=s.registered_at,
                address_line=s.address_line,
                city=s.city,
                country=s.country,
            ),
            preferences=PreferencesResponse(
                time_zone=s.time_zone, date_format=s.date_format, time_format=s.time_format
            ),
            alert_defaults=AlertDefaults(
                auto_escalation_enabled=s.auto_escalation_enabled,
                escalation_high_seconds=s.escalation_high_seconds,
                escalation_standard_seconds=s.escalation_standard_seconds,
                assistance_broadcast_enabled=s.assistance_broadcast_enabled,
            ),
            is_main_admin=v.is_main_admin,
            can_edit=v.is_main_admin,
            campus=CampusSummaryResponse(
                campus_name=v.campus.campus_name,
                retention_seconds=v.campus.retention_seconds,
                retention_label=v.campus.retention_label,
                retention_is_default=v.campus.retention_is_default,
                ai_modules=v.campus.ai_modules,
                pending_campus_requests=v.campus.pending_campus_requests,
            ),
            security=SecurityRulesResponse(**{f: getattr(v.security, f) for f in SecurityRulesResponse.model_fields}),
            campus_requests=CampusRequestRules(decision_window_hours=v.campus_request_window_hours),
            updated_at=s.updated_at,
            updated_by_name=s.updated_by_name,
        )


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    official_phone: str | None = Field(default=None, max_length=40)
    address_line: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    time_zone: str = Field(max_length=64)
    date_format: str = Field(max_length=10)
    time_format: str = Field(max_length=3)
    auto_escalation_enabled: bool
    escalation_high_seconds: int = Field(ge=30, le=3600)
    escalation_standard_seconds: int = Field(ge=30, le=86400)
    assistance_broadcast_enabled: bool
    current_password: str = Field(min_length=1, max_length=256)

    def to_change(self) -> SettingsChange:
        return SettingsChange(
            official_phone=self.official_phone,
            address_line=self.address_line,
            city=self.city,
            country=self.country,
            time_zone=self.time_zone,
            date_format=self.date_format,
            time_format=self.time_format,
            auto_escalation_enabled=self.auto_escalation_enabled,
            escalation_high_seconds=self.escalation_high_seconds,
            escalation_standard_seconds=self.escalation_standard_seconds,
            assistance_broadcast_enabled=self.assistance_broadcast_enabled,
        )


async def _service(actor: User, session: AsyncSession, request: Request) -> OrganizationSettingsService:
    settings = request.app.state.settings
    modules: list[str] = []
    if actor.role is UserRole.ADMIN:
        modules = await AiConfiguration(
            SqlAiConfigRepository(session), SqlOrganizationAuditLog(session), SqlTransaction(session)
        ).enabled_modules(actor)
    return OrganizationSettingsService(
        SqlOrganizationSettingsRepository(session, modules),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        security=security_rules(settings),
        default_retention_seconds=settings.recording_retention_default_days * 86400,
        confirmation=password_confirmation(actor, session, request),
    )


@router.get("/preferences", response_model=PreferencesResponse)
async def preferences(request: Request, actor: MemberDep, session: SessionDep) -> PreferencesResponse:
    s = await (await _service(actor, session, request)).preferences(actor)
    return PreferencesResponse(time_zone=s.time_zone, date_format=s.date_format, time_format=s.time_format)


@router.get("/settings", response_model=SettingsResponse)
async def view_settings(request: Request, actor: MemberDep, session: SessionDep) -> SettingsResponse:
    return SettingsResponse.of(await (await _service(actor, session, request)).view(actor))


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: SettingsRequest, request: Request, actor: MemberDep, session: SessionDep
) -> SettingsResponse:
    service = await _service(actor, session, request)
    return SettingsResponse.of(await service.update(actor, body.to_change(), body.current_password))
