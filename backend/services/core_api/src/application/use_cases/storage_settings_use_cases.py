"""Routine-recording retention of a campus (requirements Section 17; decision D-023).

Admins and Supervisors of the campus see the setting, what the campus stores in each tier and the
storage node's last disk report. Both may change the retention (requirements Section 17; Supervisor
spec page 8), each confirming it with their own password; the change is audited with the old and the
new value and the actor's role. Evidence in the vault is outside any retention (``evidence_use_cases``).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.use_cases.password_confirmation import PasswordConfirmation
from src.domain.entities.recording import CampusUsage, StorageStatus
from src.domain.entities.retention_policy import RetentionPolicy, RetentionUnit
from src.domain.entities.user import User
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_recording_repo import IRecordingRepository
from src.domain.repositories.i_storage_settings_repo import IStorageSettingsRepository
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import ensure_camera_viewer

RECORDING_RETENTION_CHANGED = "RECORDING_RETENTION_CHANGED"


@dataclass(frozen=True, slots=True)
class StorageOverview:
    retention: RetentionPolicy
    default_retention: RetentionPolicy
    storage: StorageStatus | None
    usage: CampusUsage
    warning_percent: int
    critical_percent: int


class RetentionSettings:
    def __init__(
        self,
        settings: IStorageSettingsRepository,
        recordings: IRecordingRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        default_days: int,
        warning_percent: int,
        critical_percent: int,
        confirmation: PasswordConfirmation | None = None,
    ) -> None:
        self._settings = settings
        self._recordings = recordings
        self._audit = audit
        self._transaction = transaction
        self._default = RetentionPolicy.default_days(default_days)
        self._warning = warning_percent
        self._critical = critical_percent
        self._confirmation = confirmation

    async def view(self, actor: User) -> StorageOverview:
        ensure_camera_viewer(actor)
        current = await self._settings.get_retention(actor.organization_id, actor.campus_id)
        storage = await self._recordings.storage_status()
        usage = await self._recordings.campus_usage(actor.organization_id, actor.campus_id)
        return StorageOverview(current or self._default, self._default, storage, usage, self._warning, self._critical)

    async def change(self, actor: User, value: int, unit: RetentionUnit, current_password: str) -> StorageOverview:
        ensure_camera_viewer(actor)  # Admins and Supervisors; Guards are refused
        assert self._confirmation is not None
        wanted = RetentionPolicy.checked(value, unit)
        await self._confirmation.confirm(current_password)
        before = await self._settings.get_retention(actor.organization_id, actor.campus_id) or self._default
        await self._settings.set_retention(actor.organization_id, actor.campus_id, wanted.value, wanted.unit, actor.id)
        await self._audit.record(
            actor,
            RECORDING_RETENTION_CHANGED,
            "CAMPUS",
            actor.campus_id,
            {
                "from": {"value": before.value, "unit": before.unit.value, "default": before.is_default},
                "to": {"value": wanted.value, "unit": wanted.unit.value},
            },
        )
        overview = await self.view(actor)  # read back before commit: the RLS actor is per transaction
        await self._transaction.commit()
        return overview
