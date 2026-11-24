"""Routine-recording retention and storage use (decision D-023).

GET    /recordings/retention     the campus retention, the server default, campus usage by tier, the last disk report
PUT    /recordings/retention     Admins and Supervisors, with their own password: value + unit (MINUTES/HOURS/DAYS)
"""

from fastapi import APIRouter, Request

from src.application.dtos.storage_settings_dto import RetentionChangeRequest, StorageOverviewResponse
from src.application.use_cases.password_confirmation import PasswordConfirmation, PasswordPolicy
from src.application.use_cases.storage_settings_use_cases import RetentionSettings
from src.domain.entities.user import User
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.recording_repository import SqlRecordingRepository
from src.infrastructure.database.repositories.staff_repository import SqlStaffRepository
from src.infrastructure.database.repositories.storage_settings_repository import SqlStorageSettingsRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/recordings/retention", tags=["Recordings"])


def password_confirmation(actor: User, session, request: Request) -> PasswordConfirmation:  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    return PasswordConfirmation(
        SqlStaffRepository(session, actor.organization_id, actor.campus_id),
        SqlTransaction(session),
        Argon2PasswordHasher(),
        PasswordPolicy(settings.org_login_max_attempts, settings.org_login_lockout_minutes * 60),
    )


def _use_case(actor: User, session, request: Request) -> RetentionSettings:  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    return RetentionSettings(
        SqlStorageSettingsRepository(session),
        SqlRecordingRepository(session),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        default_days=settings.recording_retention_default_days,
        warning_percent=settings.storage_warning_percent,
        critical_percent=settings.storage_critical_percent,
        confirmation=password_confirmation(actor, session, request),
    )


@router.get("", response_model=StorageOverviewResponse)
async def view_retention(request: Request, actor: StaffDep, session: SessionDep) -> StorageOverviewResponse:
    return StorageOverviewResponse.of(await _use_case(actor, session, request).view(actor))


@router.put("", response_model=StorageOverviewResponse)
async def change_retention(
    body: RetentionChangeRequest, request: Request, actor: StaffDep, session: SessionDep
) -> StorageOverviewResponse:
    overview = await _use_case(actor, session, request).change(actor, body.value, body.unit, body.current_password)
    return StorageOverviewResponse.of(overview)
