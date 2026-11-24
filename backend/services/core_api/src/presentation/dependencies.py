from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import AuthenticationFailed, PermissionDenied
from src.domain.services.permission_policy import ensure_admin, ensure_first_admin
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.connection import (
    actor_password_changed_at,
    actor_sessions_revoked_at,
    bind_actor,
)
from src.infrastructure.database.repositories.organization_admin_repository import (
    SqlOrganizationAdminRepository,
)
from src.infrastructure.database.repositories.platform_admin_repository import (
    SqlPlatformAdminRepository,
)
from src.infrastructure.database.repositories.user_repository import SqlUserRepository
from src.infrastructure.security.jwt import (
    PLATFORM_AUDIENCE,
    TENANT_AUDIENCE,
    decode_access_token,
    decode_access_token_issued_at,
    decode_platform_token,
    issued_time_is_exact,
)

_bearer = HTTPBearer(auto_error=False)
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One tenant database transaction per request (DATABASE_URL, RLS applies).

    Nothing is committed here: endpoints that write call `session.commit()`
    themselves, so a failed commit is reported to the client. Anything not
    committed is rolled back when the session closes.
    """
    async with request.app.state.session_factory() as session:
        yield session


async def get_platform_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One platform database transaction per request (PLATFORM_DATABASE_URL).
    Used only by Super Admin routes; that role cannot read tenant tables."""
    async with request.app.state.platform_session_factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
PlatformSessionDep = Annotated[AsyncSession, Depends(get_platform_session)]


def _refuse_super_admin(token: str, secret: str, algorithm: str) -> None:
    """A genuine Super Admin token is an identified caller without access to organization data:
    403, not 401 (decision D-024). The platform role cannot read tenant tables either."""
    try:
        decode_access_token(token, secret=secret, algorithm=algorithm, audience=PLATFORM_AUDIENCE)
    except AuthenticationFailed:
        return
    raise PermissionDenied(
        "The Super Admin cannot open organization pages or data.", code="SUPER_ADMIN_NOT_ALLOWED"
    )


async def authenticate_tenant_token(session: AsyncSession, settings: Settings, token: str) -> User:
    """The signed-in organization user of an access token, with every rule of the REST routes: a valid tenant
    token (Super Admin tokens 403 SUPER_ADMIN_NOT_ALLOWED), an active account, no password change since the token
    was issued, an ACTIVE organization (403 ORGANIZATION_INACTIVE) and, for Guards, no assignment change since
    (401 ASSIGNMENT_CHANGED). Binds the user to ``session`` for Row-Level Security. Used by every route and by the
    real-time WebSocket (decision D-025)."""
    secret = settings.jwt_secret_key.get_secret_value()
    try:
        user_id, issued_at = decode_access_token_issued_at(
            token, secret=secret, algorithm=settings.jwt_algorithm, audience=TENANT_AUDIENCE
        )
    except AuthenticationFailed:
        _refuse_super_admin(token, secret, settings.jwt_algorithm)
        raise
    await bind_actor(session, user_id)
    actor = await SqlUserRepository(session).get_actor(user_id)
    if actor is None or not actor.is_active:
        raise AuthenticationFailed("Account is not active.")
    changed_at = await actor_password_changed_at(session)
    if changed_at is not None and issued_at < changed_at.replace(microsecond=0):
        # The password changed (e.g. reset) after this token was issued.
        raise AuthenticationFailed("Session expired. Please log in again.")
    # An organization the Super Admin made INACTIVE or SUSPENDED is blocked on every tenant route,
    # not only at sign-in (decision D-019).
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    if actor.role is UserRole.GUARD:
        revoked_at = await actor_sessions_revoked_at(session)
        # A Guard's assignment changed after this token was issued (Standby <-> Stationed, or a new
        # post): the app must sign in again into the matching dashboard (decision D-018). Tokens
        # without an exact issue time are refused for the whole second of the change.
        if revoked_at is not None and (
            issued_at < revoked_at if issued_time_is_exact(issued_at) else issued_at <= revoked_at
        ):
            raise AuthenticationFailed(
                "Your operational assignment changed. Please sign in again.", code="ASSIGNMENT_CHANGED"
            )
    return actor


async def get_current_actor(request: Request, session: SessionDep, credentials: BearerDep) -> User:
    if credentials is None:
        raise AuthenticationFailed("Missing bearer token.")
    return await authenticate_tenant_token(session, request.app.state.settings, credentials.credentials)


@dataclass(frozen=True, slots=True)
class PlatformSession:
    """The signed-in Super Admin and how the session was opened (NORMAL or RECOVERY)."""

    admin: PlatformAdmin
    login_method: str


async def get_current_platform_session(
    request: Request, session: PlatformSessionDep, credentials: BearerDep
) -> PlatformSession:
    if credentials is None:
        raise AuthenticationFailed("Missing bearer token.")
    settings: Settings = request.app.state.settings
    claims = decode_platform_token(
        credentials.credentials, secret=settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    admin = await SqlPlatformAdminRepository(session).get_by_id(claims.admin_id)
    if admin is None or not admin.is_active:
        raise AuthenticationFailed("Account is not active.")
    if admin.token_version != claims.token_version:
        # The username or password changed after this token was issued.
        raise AuthenticationFailed("Session expired. Please log in again.")
    return PlatformSession(admin=admin, login_method=claims.login_method)


async def get_current_platform_admin(
    platform_session: Annotated[PlatformSession, Depends(get_current_platform_session)],
) -> PlatformAdmin:
    return platform_session.admin


ActorDep = Annotated[User, Depends(get_current_actor)]
PlatformAdminDep = Annotated[PlatformAdmin, Depends(get_current_platform_admin)]
PlatformSessionInfoDep = Annotated[PlatformSession, Depends(get_current_platform_session)]


async def get_current_organization_admin(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin (role ADMIN) of an active organization. Supervisors, Guards,
    Super Admin tokens and suspended organizations are refused."""
    ensure_admin(actor)
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


OrganizationAdminDep = Annotated[User, Depends(get_current_organization_admin)]


async def get_current_main_admin(admin: OrganizationAdminDep) -> User:
    """The organization's Main (first) Admin: campus approval requests (page 15). Checked before the
    request body, so other callers get 403 whatever they send."""
    ensure_first_admin(admin)
    return admin


MainAdminDep = Annotated[User, Depends(get_current_main_admin)]


async def get_current_supervisor(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Supervisor of an active organization."""
    if actor.role is not UserRole.SUPERVISOR:
        raise PermissionDenied("Only Supervisors can use this page.")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


SupervisorDep = Annotated[User, Depends(get_current_supervisor)]


async def get_current_guard_creator(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin of an active organization. Only Admins create Guards (decision
    D-017; until then Supervisors could too). The database functions check the same (migration 0018)."""
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied("Only an Organization Admin can add Guards.", code="ADMIN_ONLY")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


async def get_current_staff_manager(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin or Supervisor of an active organization (Staff Management). What
    each may do is decided per action (decision D-017)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage staff.", code="STAFF_ONLY")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


StaffManagerDep = Annotated[User, Depends(get_current_staff_manager)]


async def get_current_guard(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Guard of an active organization (the Guard app)."""
    if actor.role is not UserRole.GUARD:
        raise PermissionDenied("Only Guards can use the Guard app.")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


GuardCreatorDep = Annotated[User, Depends(get_current_guard_creator)]
GuardDep = Annotated[User, Depends(get_current_guard)]


async def get_current_location_staff(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin or Supervisor of an active organization (location pages). Which
    of them may change buildings is decided per action (Admins only)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage locations.")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


LocationStaffDep = Annotated[User, Depends(get_current_location_staff)]


async def get_current_camera_manager(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin or Supervisor of an active organization (camera changes)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage cameras.")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


CameraManagerDep = Annotated[User, Depends(get_current_camera_manager)]


async def get_current_member(actor: ActorDep, session: SessionDep) -> User:
    """Any signed-in, active organization user (Admin, Supervisor or Guard) of an active organization.
    What each may see is then limited by role and by the database (alerts, notifications)."""
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


MemberDep = Annotated[User, Depends(get_current_member)]


async def get_current_staff(actor: ActorDep, session: SessionDep) -> User:
    """A signed-in, active Admin or Supervisor of an active organization (alert and report review)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can use this.", code="STAFF_ONLY")
    if await SqlOrganizationAdminRepository(session).organization_status(actor.organization_id) != "ACTIVE":
        raise PermissionDenied("This organization is not active.", code="ORGANIZATION_INACTIVE")
    return actor


StaffDep = Annotated[User, Depends(get_current_staff)]
