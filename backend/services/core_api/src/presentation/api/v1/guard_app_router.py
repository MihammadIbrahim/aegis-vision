"""The Guard app: sign-in, the Guard dashboard and the Guard's own profile. Forgot password is
``POST /auth/password-reset`` with ``{"guard_id", "email", "operational_mode"}``.

POST   /auth/guard/login                      operational mode + Guard ID + email + password; emails a code
                                              (no token yet). A mode that does not match the assignment: 409
POST   /auth/guard/login/{login_id}/resend-otp
POST   /auth/guard/login/{login_id}/verify    code + operational mode (checked again); returns the access
                                              token, the mode and the dashboard to open
GET    /guard/dashboard                        Guards only; includes the mode and dashboard

Operational modes (decision D-018): STANDBY (no location; Standby Emergency Dashboard) and STATIONED
(Stationed / Standalone, a building, floor or area; Stationed Monitoring Dashboard). Any change to the
Guard's assignment ends the Guard's sessions (401 ASSIGNMENT_CHANGED), so the app signs in again.
GET    /guard/profile
POST   /guard/profile/changes                  current password + email / phone / new password
POST   /guard/profile/changes/{id}/resend-otp
POST   /guard/profile/changes/{id}/verify      code(s); only now are the changes applied
DELETE /guard/profile/changes/{id}             cancel; nothing changes
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from src.application.dtos.auth_dto import LoginCodeSentResponse
from src.application.dtos.guard_dto import (
    GuardDashboardResponse,
    GuardLoginRequest,
    GuardProfileChangedResponse,
    GuardProfileChangeRequest,
    GuardProfileResponse,
    GuardTokenResponse,
    GuardVerifyLoginRequest,
    dashboard_for,
)
from src.application.dtos.location_dto import OwnGuardLocationResponse
from src.application.dtos.supervisor_dto import (
    ProfileChangeStartedResponse,
    VerifyProfileChangeRequest,
)
from src.application.use_cases.auth_use_cases import (
    STANDBY,
    STATIONED,
    GuardLoginForm,
    ResendOrganizationAdminLoginCode,
    StartGuardLogin,
    VerifyOrganizationAdminLogin,
    operational_mode_mismatch,
)
from src.application.use_cases.guard_use_cases import (
    GetGuardProfile,
    GuardProfileChangeForm,
    StartGuardProfileChange,
)
from src.application.use_cases.location_use_cases import GetOwnGuardLocation
from src.application.use_cases.organization_admin_dashboard_use_cases import (
    GetGuardDashboard,
)
from src.application.use_cases.supervisor_profile_use_cases import (
    CancelProfileChange,
    ProfileChangeStarted,
    ResendProfileChangeCodes,
    VerifyProfileChange,
)
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import AuthenticationFailed
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.infrastructure.database.connection import bind_actor
from src.infrastructure.database.repositories.admin_login_repository import (
    SqlAdminLoginRepository,
)
from src.infrastructure.database.repositories.audit_log_repository import SqlAuthEventLog
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.repositories.organization_admin_repository import (
    SqlOrganizationAdminRepository,
)
from src.infrastructure.database.repositories.supervisor_profile_repository import (
    SqlGuardProfileRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.jwt import TENANT_AUDIENCE, create_access_token
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.auth_router import _limit_per_client_ip
from src.presentation.dependencies import GuardDep, SessionDep

auth_router = APIRouter(prefix="/auth/guard", tags=["Guard sign-in"], dependencies=[Depends(_limit_per_client_ip)])
app_router = APIRouter(prefix="/guard", tags=["Guard app"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


# --- Sign-in -------------------------------------------------------------------------------------


@auth_router.post("/login", response_model=LoginCodeSentResponse)
async def login(body: GuardLoginRequest, request: Request, session: SessionDep) -> LoginCodeSentResponse:
    """Any wrong value, or an inactive or removed Guard, gives the same 401 INVALID_CREDENTIALS."""
    settings = _settings(request)
    use_case = StartGuardLogin(
        SqlAdminLoginRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        login_ttl_seconds=settings.org_login_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
        auth_events=SqlAuthEventLog(session),
    )
    form = GuardLoginForm(
        guard_id=body.guard_id, email=body.email, password=body.password, operational_mode=body.operational_mode
    )
    sent = await use_case.execute(form)
    return LoginCodeSentResponse.from_result(sent)


@auth_router.post("/login/{login_id}/resend-otp", response_model=LoginCodeSentResponse)
async def resend_login_code(login_id: UUID, request: Request, session: SessionDep) -> LoginCodeSentResponse:
    use_case = ResendOrganizationAdminLoginCode(
        SqlAdminLoginRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return LoginCodeSentResponse.from_result(await use_case.execute(login_id))


@auth_router.post("/login/{login_id}/verify", response_model=GuardTokenResponse)
async def verify_login(
    login_id: UUID, body: GuardVerifyLoginRequest, request: Request, session: SessionDep
) -> GuardTokenResponse:
    settings = _settings(request)
    repository = SqlAdminLoginRepository(session)
    user_id = await VerifyOrganizationAdminLogin(
        repository, request.app.state.otp_codec, SqlTransaction(session), SqlAuthEventLog(session)
    ).execute(login_id, body.otp)
    # The session is bound to the mode only if the assignment still matches it now (decision D-018).
    stationed = await repository.guard_is_stationed(user_id)
    if stationed is None:
        raise AuthenticationFailed(
            "The organization details, email address or password are incorrect.", code="INVALID_CREDENTIALS"
        )
    problem = operational_mode_mismatch(body.operational_mode, stationed)
    if problem is not None:
        raise problem
    token = create_access_token(
        user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_access_token_expire_minutes,
        audience=TENANT_AUDIENCE,
    )
    return GuardTokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        operational_mode=body.operational_mode,
        dashboard=dashboard_for(body.operational_mode),
    )


# --- Dashboard and profile -----------------------------------------------------------------------


@app_router.get("/dashboard", response_model=GuardDashboardResponse)
async def dashboard(guard: GuardDep, session: SessionDep) -> GuardDashboardResponse:
    facts = await GetGuardDashboard(SqlOrganizationAdminRepository(session)).execute(guard)
    stationed = await SqlAdminLoginRepository(session).guard_is_stationed(guard.id)
    return GuardDashboardResponse.from_facts(facts, STATIONED if stationed else STANDBY)


async def _read(guard, session) -> GuardProfileResponse:  # type: ignore[no-untyped-def]
    profile = await GetGuardProfile(SqlGuardProfileRepository(session)).execute(guard)
    facts = await GetGuardDashboard(SqlOrganizationAdminRepository(session)).execute(guard)
    stationed = await SqlAdminLoginRepository(session).guard_is_stationed(guard.id)
    return GuardProfileResponse.from_entity(
        profile,
        campus_name=facts.campus_name,
        status=guard.status.value,
        operational_mode=STATIONED if stationed else STANDBY,
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


@app_router.get("/profile", response_model=GuardProfileResponse)
async def read_profile(guard: GuardDep, session: SessionDep) -> GuardProfileResponse:
    return await _read(guard, session)


@app_router.post("/profile/changes", response_model=ProfileChangeStartedResponse)
async def save_changes(
    body: GuardProfileChangeRequest, request: Request, guard: GuardDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    settings = _settings(request)
    use_case = StartGuardProfileChange(
        SqlGuardProfileRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_profile_change_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
    )
    form = GuardProfileChangeForm(
        current_password=body.current_password,
        email=body.email,
        phone=body.phone_number,
        new_password=body.new_password,
        confirm_new_password=body.confirm_new_password,
    )
    return _started(await use_case.execute(guard, form))


@app_router.post("/profile/changes/{change_id}/resend-otp", response_model=ProfileChangeStartedResponse)
async def resend_codes(
    change_id: UUID, request: Request, guard: GuardDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    use_case = ResendProfileChangeCodes(
        SqlGuardProfileRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
        label="Guard",
    )
    return _started(await use_case.execute(change_id))


@app_router.post("/profile/changes/{change_id}/verify", response_model=GuardProfileChangedResponse)
async def verify_changes(
    change_id: UUID, body: VerifyProfileChangeRequest, request: Request, guard: GuardDep, session: SessionDep
) -> GuardProfileChangedResponse:
    signed_out = await VerifyProfileChange(
        SqlGuardProfileRepository(session), request.app.state.otp_codec, SqlTransaction(session)
    ).execute(change_id, body.otp, body.new_email_otp)
    await bind_actor(session, guard.id)  # the commit ended the RLS binding; bind again to read back
    return GuardProfileChangedResponse(profile=await _read(guard, session), signed_out=signed_out)


@app_router.delete("/profile/changes/{change_id}", status_code=204)
async def cancel_changes(change_id: UUID, guard: GuardDep, session: SessionDep) -> None:
    await CancelProfileChange(SqlGuardProfileRepository(session), SqlTransaction(session)).execute(change_id)


@app_router.get("/location", response_model=OwnGuardLocationResponse)
async def my_location(guard: GuardDep, session: SessionDep) -> OwnGuardLocationResponse:
    """The Guard's assigned location (read-only; set by an Admin or Supervisor)."""
    found = await GetOwnGuardLocation(SqlLocationRepository(session, guard.organization_id, guard.campus_id)).execute()
    if found is None:
        return OwnGuardLocationResponse(assigned=False)
    return OwnGuardLocationResponse.model_validate({"assigned": True, **found})
