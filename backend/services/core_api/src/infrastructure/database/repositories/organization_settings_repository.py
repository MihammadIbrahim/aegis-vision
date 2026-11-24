"""Organization settings (migration 0026). Every user of the organization reads them; only the Main Admin
saves them, through ``app_organization_settings_save``."""

from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.organization_settings_use_cases import (
    CampusSummary,
    OrganizationSettings,
    SettingsChange,
)
from src.domain.entities.retention_policy import RetentionUnit
from src.domain.entities.user import User

_LOAD = text(
    """SELECT o.name, o.organization_code, o.official_email, o.official_phone, o.created_at,
              s.address_line, s.city, s.country, s.time_zone, s.date_format, s.time_format,
              s.auto_escalation_enabled, s.escalation_high_seconds, s.escalation_standard_seconds,
              s.assistance_broadcast_enabled, s.updated_at,
              (SELECT u.full_name FROM users u WHERE u.id = s.updated_by) AS updated_by_name
         FROM organizations o LEFT JOIN organization_settings s ON s.organization_id = o.id
        WHERE o.id = :org"""
).bindparams(bindparam("org", type_=Uuid))

_SAVE = text(
    """SELECT app_organization_settings_save(:phone, :address, :city, :country, :tz, :date_format, :time_format,
              :auto_escalation, :high, :standard, :broadcast)"""
)

_SUMMARY = text(
    """SELECT ca.name AS campus_name, r.retention_value, r.retention_unit,
              (SELECT count(*) FROM campus_registration_requests q
                WHERE q.organization_id = ca.organization_id AND q.status = 'PENDING'
                  AND (q.email_verified_at IS NULL OR q.email_verified_at > now() - interval '72 hours')
                  AND q.email_verified) AS pending
         FROM campuses ca
         LEFT JOIN campus_retention_settings r ON r.organization_id = ca.organization_id AND r.campus_id = ca.id
        WHERE ca.organization_id = :org AND ca.id = :campus"""
).bindparams(bindparam("org", type_=Uuid), bindparam("campus", type_=Uuid))

_UNIT_SECONDS = {RetentionUnit.MINUTES: 60, RetentionUnit.HOURS: 3600, RetentionUnit.DAYS: 86400}


def _duration(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds} seconds"


class SqlOrganizationSettingsRepository:
    def __init__(self, session: AsyncSession, ai_modules: list[str] | None = None) -> None:
        self._session = session
        self._ai_modules = ai_modules

    async def load(self, organization_id: UUID) -> OrganizationSettings | None:
        row = (await self._session.execute(_LOAD, {"org": organization_id})).first()
        if row is None:
            return None
        defaults = OrganizationSettings(row.name, row.organization_code, row.official_email, row.official_phone,
                                        row.created_at)  # fmt: skip
        if row.time_zone is None:  # never saved: the defaults
            return defaults
        return OrganizationSettings(
            name=row.name,
            organization_code=row.organization_code,
            official_email=row.official_email,
            official_phone=row.official_phone,
            registered_at=row.created_at,
            address_line=row.address_line,
            city=row.city,
            country=row.country,
            time_zone=row.time_zone,
            date_format=row.date_format,
            time_format=row.time_format,
            auto_escalation_enabled=row.auto_escalation_enabled,
            escalation_high_seconds=row.escalation_high_seconds,
            escalation_standard_seconds=row.escalation_standard_seconds,
            assistance_broadcast_enabled=row.assistance_broadcast_enabled,
            updated_at=row.updated_at,
            updated_by_name=row.updated_by_name,
        )

    async def save(self, change: SettingsChange) -> str:
        params = {
            "phone": change.official_phone,
            "address": change.address_line,
            "city": change.city,
            "country": change.country,
            "tz": change.time_zone,
            "date_format": change.date_format,
            "time_format": change.time_format,
            "auto_escalation": change.auto_escalation_enabled,
            "high": change.escalation_high_seconds,
            "standard": change.escalation_standard_seconds,
            "broadcast": change.assistance_broadcast_enabled,
        }
        return str((await self._session.execute(_SAVE, params)).scalar_one())

    async def campus_summary(self, actor: User, default_retention_seconds: int) -> CampusSummary:
        row = (await self._session.execute(_SUMMARY, {"org": actor.organization_id, "campus": actor.campus_id})).one()
        if row.retention_value is not None:
            seconds = int(row.retention_value) * _UNIT_SECONDS[RetentionUnit(row.retention_unit)]
            is_default = False
        else:
            seconds, is_default = default_retention_seconds, True
        return CampusSummary(
            campus_name=row.campus_name,
            retention_seconds=seconds,
            retention_label=_duration(seconds),
            retention_is_default=is_default,
            ai_modules=list(self._ai_modules or []),
            pending_campus_requests=int(row.pending) if actor.is_first_admin else None,
        )
