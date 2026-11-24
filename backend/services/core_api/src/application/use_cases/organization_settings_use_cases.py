"""Organization Settings (Org Admin page 25; decision D-024; migration 0026).

Every Admin sees the organization's profile, their own place in the hierarchy (Main Admin or campus
Admin), the sign-in rules, a storage and AI summary for their campus, and how campus requests work.
Only the Main Admin changes organization-wide settings (official phone, address, time zone, date and
time formats, alert escalation defaults), with their own password; each change is audited. The
official email is fixed (it is the verified registration address). Every signed-in user can read the
display preferences.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import available_timezones

from src.domain.entities.branch_request import DECISION_WINDOW
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound, PermissionDenied, ValidationFailed
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import PASSWORD_LENGTH, check_phone
from src.domain.value_objects.enums import UserRole

DATE_FORMATS = ("DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD")
TIME_FORMATS = ("24H", "12H")
ORGANIZATION_SETTINGS_UPDATED = "ORGANIZATION_SETTINGS_UPDATED"


@dataclass(frozen=True, slots=True)
class OrganizationSettings:
    name: str
    organization_code: str
    official_email: str | None
    official_phone: str | None
    registered_at: datetime
    address_line: str | None = None
    city: str | None = None
    country: str | None = None
    time_zone: str = "Asia/Karachi"
    date_format: str = "DD/MM/YYYY"
    time_format: str = "24H"
    auto_escalation_enabled: bool = True
    escalation_high_seconds: int = 120
    escalation_standard_seconds: int = 300
    assistance_broadcast_enabled: bool = True
    updated_at: datetime | None = None
    updated_by_name: str | None = None


@dataclass(frozen=True, slots=True)
class SettingsChange:
    official_phone: str | None
    address_line: str | None
    city: str | None
    country: str | None
    time_zone: str
    date_format: str
    time_format: str
    auto_escalation_enabled: bool
    escalation_high_seconds: int
    escalation_standard_seconds: int
    assistance_broadcast_enabled: bool


@dataclass(frozen=True, slots=True)
class CampusSummary:
    campus_name: str
    retention_seconds: int
    retention_label: str
    retention_is_default: bool
    ai_modules: list[str]
    pending_campus_requests: int | None


@dataclass(frozen=True, slots=True)
class SecurityRules:
    sign_in_code_required: bool
    code_expires_seconds: int
    code_max_attempts: int
    lockout_after_failed_sign_ins: int
    lockout_minutes: int
    password_min_length: int
    password_max_length: int


@dataclass(frozen=True, slots=True)
class SettingsView:
    settings: OrganizationSettings
    is_main_admin: bool
    campus: CampusSummary
    security: SecurityRules
    campus_request_window_hours: int


class IOrganizationSettingsRepository(Protocol):
    async def load(self, organization_id: UUID) -> OrganizationSettings | None: ...

    async def save(self, change: SettingsChange) -> str: ...

    async def campus_summary(self, actor: User, default_retention_seconds: int) -> CampusSummary: ...


class IPasswordConfirmation(Protocol):
    async def confirm(self, current_password: str) -> None: ...


def _clean(value: str | None, limit: int, what: str) -> str | None:
    text = " ".join((value or "").split())
    if len(text) > limit:
        raise ValidationFailed(f"{what} can be at most {limit} characters.", code="INVALID_TEXT")
    return text or None


def clean_change(change: SettingsChange) -> SettingsChange:
    if change.time_zone not in available_timezones():
        raise ValidationFailed("Choose a time zone from the list.", code="INVALID_TIME_ZONE")
    if change.date_format not in DATE_FORMATS or change.time_format not in TIME_FORMATS:
        raise ValidationFailed("Choose a date and time format from the list.", code="INVALID_FORMAT")
    if not 30 <= change.escalation_high_seconds <= 3600:
        raise ValidationFailed("High-priority escalation must be 30 seconds to 1 hour.", code="INVALID_ESCALATION")
    if not 30 <= change.escalation_standard_seconds <= 86400:
        raise ValidationFailed("Standard escalation must be 30 seconds to 24 hours.", code="INVALID_ESCALATION")
    phone = check_phone(change.official_phone) if change.official_phone and change.official_phone.strip() else None
    return replace(
        change,
        official_phone=phone,
        address_line=_clean(change.address_line, 300, "The address"),
        city=_clean(change.city, 100, "The city"),
        country=_clean(change.country, 100, "The country"),
    )


class OrganizationSettingsService:
    def __init__(
        self,
        repository: IOrganizationSettingsRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        security: SecurityRules,
        default_retention_seconds: int,
        confirmation: IPasswordConfirmation | None = None,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._transaction = transaction
        self._security = security
        self._default_retention = default_retention_seconds
        self._confirmation = confirmation

    async def _settings(self, actor: User) -> OrganizationSettings:
        settings = await self._repo.load(actor.organization_id)
        if settings is None:
            raise NotFound("Organization not found.")
        return settings

    async def preferences(self, actor: User) -> OrganizationSettings:
        """Display preferences (time zone, formats) for any signed-in user of the organization."""
        return await self._settings(actor)

    async def view(self, actor: User) -> SettingsView:
        if actor.role is not UserRole.ADMIN:
            raise PermissionDenied("Only Admins can open Organization Settings.", code="ADMIN_ONLY")
        return SettingsView(
            settings=await self._settings(actor),
            is_main_admin=actor.is_first_admin,
            campus=await self._repo.campus_summary(actor, self._default_retention),
            security=self._security,
            campus_request_window_hours=int(DECISION_WINDOW.total_seconds() // 3600),
        )

    async def update(self, actor: User, change: SettingsChange, current_password: str) -> SettingsView:
        if actor.role is not UserRole.ADMIN or not actor.is_first_admin:
            raise PermissionDenied(
                "Only the Main Admin changes organization-wide settings.", code="MAIN_ADMIN_ONLY"
            )
        cleaned = clean_change(change)
        before = await self._settings(actor)
        assert self._confirmation is not None
        await self._confirmation.confirm(current_password)
        outcome = await self._repo.save(cleaned)
        if outcome != "SAVED":
            raise PermissionDenied("Only the Main Admin changes organization-wide settings.", code="MAIN_ADMIN_ONLY")
        changed = _changed(before, cleaned)
        await self._audit.record(actor, ORGANIZATION_SETTINGS_UPDATED, "ORGANIZATION", actor.organization_id,
                                 {"changed": changed})  # fmt: skip
        saved = await self.view(actor)  # read before the commit ends the signed-in transaction
        await self._transaction.commit()
        return saved


def _changed(before: OrganizationSettings, after: SettingsChange) -> list[str]:
    names: list[str] = []
    for name in (
        "official_phone",
        "address_line",
        "city",
        "country",
        "time_zone",
        "date_format",
        "time_format",
        "auto_escalation_enabled",
        "escalation_high_seconds",
        "escalation_standard_seconds",
        "assistance_broadcast_enabled",
    ):
        old: Any = getattr(before, name)
        new: Any = getattr(after, name)
        if name == "official_phone" and new is None:
            continue  # not given: unchanged
        if old != new:
            names.append(name)
    return names


def security_rules(settings: Any) -> SecurityRules:
    return SecurityRules(
        sign_in_code_required=True,
        code_expires_seconds=settings.otp_expire_seconds,
        code_max_attempts=settings.otp_max_attempts,
        lockout_after_failed_sign_ins=settings.org_login_max_attempts,
        lockout_minutes=settings.org_login_lockout_minutes,
        password_min_length=PASSWORD_LENGTH[0],
        password_max_length=PASSWORD_LENGTH[1],
    )
