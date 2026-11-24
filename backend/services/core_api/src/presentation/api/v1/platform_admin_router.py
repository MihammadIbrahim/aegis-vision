"""Platform Super Admin routes. They use the platform database session and accept
only `platform` tokens; organization users can never call them."""

from datetime import timedelta

from fastapi import APIRouter, Request

from src.application.dtos.platform_admin_dto import (
    ChangePasswordRequest,
    ChangeUsernameRequest,
    DeletedOrganizationsResponse,
    PlatformAdminResponse,
    PlatformLoginRequest,
    RecoverCredentialsRequest,
    TokenResponse,
)
from src.application.use_cases.deleted_organization_use_cases import (
    ListDeletedOrganizationNames,
)
from src.application.use_cases.platform_admin_use_cases import (
    ChangePlatformPassword,
    ChangePlatformUsername,
    LoginPlatformAdmin,
    RecoverPlatformCredentials,
    RequestContext,
)
from src.config.settings import Settings
from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    CurrentPasswordIncorrect,
)
from src.domain.value_objects.recovery_credentials import RecoveryCredentials
from src.infrastructure.database.repositories.deleted_organization_repository import (
    SqlDeletedOrganizationHistory,
)
from src.infrastructure.database.repositories.platform_admin_repository import (
    SqlPlatformAdminRepository,
    SqlPlatformAuditLog,
)
from src.infrastructure.security.jwt import create_platform_token
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import (
    PlatformAdminDep,
    PlatformSessionDep,
    PlatformSessionInfoDep,
)

router = APIRouter(prefix="/platform", tags=["Platform Super Admin"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _recovery(settings: Settings) -> RecoveryCredentials | None:
    if settings.super_admin_recovery_username is None or settings.super_admin_recovery_password is None:
        return None
    return RecoveryCredentials(
        username=settings.super_admin_recovery_username,
        password=settings.super_admin_recovery_password.get_secret_value(),
    )


def _token_response(settings: Settings, admin: PlatformAdmin, login_method: str) -> TokenResponse:
    token = create_platform_token(
        admin.id,
        admin.token_version,
        secret=settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_access_token_expire_minutes,
        login_method=login_method,
    )
    return TokenResponse(access_token=token, expires_in=settings.jwt_access_token_expire_minutes * 60)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: PlatformLoginRequest, request: Request, session: PlatformSessionDep) -> TokenResponse:
    """Username + password (no email OTP, requirement 9.1). Accepts either the normal
    database credentials or the emergency recovery credentials from the server
    environment; the response is the same either way."""
    settings = _settings(request)
    use_case = LoginPlatformAdmin(
        SqlPlatformAdminRepository(session),
        Argon2PasswordHasher(),
        max_attempts=settings.platform_login_max_attempts,
        lockout=timedelta(minutes=settings.platform_login_lockout_minutes),
        recovery=_recovery(settings),
        audit=SqlPlatformAuditLog(session),
        session_token_factory=lambda admin, method: _token_response(settings, admin, method).access_token,
    )
    try:
        result = await use_case.execute(body.username, body.password, _context(request))
    except (AuthenticationFailed, AccountLocked):
        await session.commit()  # keep the failure count and the audit row
        raise
    await session.commit()
    return TokenResponse(access_token=result.access_token, expires_in=settings.jwt_access_token_expire_minutes * 60)


@router.get("/me", response_model=PlatformAdminResponse)
async def me(admin: PlatformAdminDep) -> PlatformAdminResponse:
    return PlatformAdminResponse.from_entity(admin)


@router.get("/deleted-organizations", response_model=DeletedOrganizationsResponse)
async def deleted_organizations(admin: PlatformAdminDep, session: PlatformSessionDep) -> DeletedOrganizationsResponse:
    names = await ListDeletedOrganizationNames(SqlDeletedOrganizationHistory(session)).execute()
    return DeletedOrganizationsResponse(organization_names=names)


@router.post("/me/username", response_model=TokenResponse)
async def change_username(
    body: ChangeUsernameRequest, request: Request, current: PlatformSessionInfoDep, session: PlatformSessionDep
) -> TokenResponse:
    """Change the normal login username. Signs out all earlier tokens and returns a new one."""
    settings = _settings(request)
    use_case = ChangePlatformUsername(
        SqlPlatformAdminRepository(session),
        Argon2PasswordHasher(),
        max_attempts=settings.platform_login_max_attempts,
        lockout=timedelta(minutes=settings.platform_login_lockout_minutes),
        audit=SqlPlatformAuditLog(session),
    )
    try:
        updated = await use_case.execute(current.admin.id, body.current_password, body.new_username, _context(request))
    except (CurrentPasswordIncorrect, AccountLocked):
        await session.commit()  # keep the failure count and the audit row
        raise
    await session.commit()
    return _token_response(settings, updated, current.login_method)


@router.post("/me/password", response_model=TokenResponse)
async def change_password(
    body: ChangePasswordRequest, request: Request, current: PlatformSessionInfoDep, session: PlatformSessionDep
) -> TokenResponse:
    """Change the normal login password. Signs out all earlier tokens and returns a new one.
    The emergency recovery credentials are not affected."""
    settings = _settings(request)
    use_case = ChangePlatformPassword(
        SqlPlatformAdminRepository(session),
        Argon2PasswordHasher(),
        max_attempts=settings.platform_login_max_attempts,
        lockout=timedelta(minutes=settings.platform_login_lockout_minutes),
        audit=SqlPlatformAuditLog(session),
    )
    try:
        updated = await use_case.execute(
            current.admin.id, body.current_password, body.new_password, body.confirm_new_password, _context(request)
        )
    except (CurrentPasswordIncorrect, AccountLocked):
        await session.commit()
        raise
    await session.commit()
    return _token_response(settings, updated, current.login_method)


@router.post("/recovery/credentials", response_model=TokenResponse)
async def recover_credentials(
    body: RecoverCredentialsRequest, request: Request, current: PlatformSessionInfoDep, session: PlatformSessionDep
) -> TokenResponse:
    """Emergency recovery: set a new normal username and password without the forgotten
    database password. Needs a session opened with the emergency recovery credentials
    (403 otherwise) and those credentials entered again in the body. Signs out every
    earlier Super Admin token and returns a new emergency-session token. The emergency
    credentials themselves never change."""
    settings = _settings(request)
    use_case = RecoverPlatformCredentials(
        SqlPlatformAdminRepository(session),
        Argon2PasswordHasher(),
        _recovery(settings),
        max_attempts=settings.platform_login_max_attempts,
        lockout=timedelta(minutes=settings.platform_login_lockout_minutes),
        audit=SqlPlatformAuditLog(session),
    )
    try:
        updated = await use_case.execute(
            current.admin.id,
            current.login_method,
            body.recovery_username,
            body.recovery_password,
            body.new_username,
            body.new_password,
            body.confirm_new_password,
            _context(request),
        )
    except (CurrentPasswordIncorrect, AccountLocked):
        await session.commit()  # keep the failure count and the audit row
        raise
    await session.commit()
    return _token_response(settings, updated, current.login_method)
