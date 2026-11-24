"""The signed-in Supervisor's own profile (only Supervisors: ``SupervisorDep``).

GET    /supervisor/profile                         read (Organization Name/ID and Supervisor ID are read-only)
POST   /supervisor/profile/changes                 Save: current password + changes; emails code(s)
POST   /supervisor/profile/changes/{id}/resend-otp
POST   /supervisor/profile/changes/{id}/verify     code(s); only now are the changes applied
DELETE /supervisor/profile/changes/{id}            cancel; nothing changes
"""

from uuid import UUID

from fastapi import APIRouter, Request

from src.application.dtos.supervisor_dto import (
    ProfileChangedResponse,
    ProfileChangeRequest,
    ProfileChangeStartedResponse,
    SupervisorProfileResponse,
    VerifyProfileChangeRequest,
)
from src.application.use_cases.supervisor_profile_use_cases import (
    CancelProfileChange,
    GetSupervisorProfile,
    ProfileChangeForm,
    ProfileChangeStarted,
    ResendProfileChangeCodes,
    StartProfileChange,
    VerifyProfileChange,
)
from src.config.settings import Settings
from src.domain.entities.user import User
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import SupervisorProfile
from src.infrastructure.database.connection import bind_actor
from src.infrastructure.database.repositories.supervisor_profile_repository import (
    SqlSupervisorProfileRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import SessionDep, SupervisorDep

router = APIRouter(prefix="/supervisor/profile", tags=["Supervisor profile"])


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


def _profile(profile: SupervisorProfile) -> SupervisorProfileResponse:
    return SupervisorProfileResponse(
        organization_name=profile.organization_name,
        organization_id=profile.organization_id,
        supervisor_id=profile.supervisor_id,
        full_name=profile.full_name,
        username=profile.username,
        email=profile.email,
        phone_number=profile.phone,
    )


def _started(started: ProfileChangeStarted) -> ProfileChangeStartedResponse:
    return ProfileChangeStartedResponse(
        change_id=started.change_id,
        email=started.masked_email,
        new_email=started.masked_new_email,
        delivery=started.delivery_channel,
        otp_expires_in_seconds=started.code_expires_in_seconds,
        resend_available_in_seconds=started.resend_available_in_seconds,
        expires_at=started.expires_at,
    )


async def _read(supervisor: User, session) -> SupervisorProfileResponse:  # type: ignore[no-untyped-def]
    return _profile(await GetSupervisorProfile(SqlSupervisorProfileRepository(session)).execute(supervisor))


@router.get("", response_model=SupervisorProfileResponse)
async def read_profile(supervisor: SupervisorDep, session: SessionDep) -> SupervisorProfileResponse:
    return await _read(supervisor, session)


@router.post("/changes", response_model=ProfileChangeStartedResponse)
async def save_changes(
    body: ProfileChangeRequest, request: Request, supervisor: SupervisorDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    """Check the current password, then email a code. Nothing changes until it is verified."""
    settings: Settings = request.app.state.settings
    use_case = StartProfileChange(
        SqlSupervisorProfileRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_profile_change_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
    )
    form = ProfileChangeForm(
        current_password=body.current_password,
        full_name=body.full_name,
        username=body.username,
        email=body.email,
        new_password=body.new_password,
        confirm_new_password=body.confirm_new_password,
    )
    return _started(await use_case.execute(supervisor, form))


@router.post("/changes/{change_id}/resend-otp", response_model=ProfileChangeStartedResponse)
async def resend_codes(
    change_id: UUID, request: Request, supervisor: SupervisorDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    use_case = ResendProfileChangeCodes(
        SqlSupervisorProfileRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(request.app.state.settings),
    )
    return _started(await use_case.execute(change_id))


@router.post("/changes/{change_id}/verify", response_model=ProfileChangedResponse)
async def verify_changes(
    change_id: UUID, body: VerifyProfileChangeRequest, request: Request, supervisor: SupervisorDep, session: SessionDep
) -> ProfileChangedResponse:
    repository = SqlSupervisorProfileRepository(session)
    signed_out = await VerifyProfileChange(repository, request.app.state.otp_codec, SqlTransaction(session)).execute(
        change_id, body.otp, body.new_email_otp
    )
    # The commit ended the transaction, and with it the Row-Level Security binding: bind again to read back.
    await bind_actor(session, supervisor.id)
    return ProfileChangedResponse(profile=await _read(supervisor, session), signed_out=signed_out)


@router.delete("/changes/{change_id}", status_code=204)
async def cancel_changes(change_id: UUID, supervisor: SupervisorDep, session: SessionDep) -> None:
    await CancelProfileChange(SqlSupervisorProfileRepository(session), SqlTransaction(session)).execute(change_id)
