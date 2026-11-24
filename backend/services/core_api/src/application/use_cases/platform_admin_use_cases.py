from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.entities.platform_audit_event import PlatformAuditEvent
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    CurrentPasswordIncorrect,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_platform_admin_repo import (
    IPlatformAdminRepository,
    IPlatformAuditLog,
)
from src.domain.services.credential_policy import (
    ensure_acceptable_new_password,
    ensure_valid_username,
)
from src.domain.value_objects.recovery_credentials import RecoveryCredentials

INVALID_CREDENTIALS = "Invalid username or password."
ACCOUNT_LOCKED = "Too many failed login attempts. Try again later."
LOGIN_NORMAL = "NORMAL"
LOGIN_RECOVERY = "RECOVERY"

# An Argon2 hash of random bytes, used only so an unknown username costs the same
# time as a wrong password. It is not a secret and matches no password.
_TIMING_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$09LEPR2XUrGVUAZjFg4d6w$2z8Stpj3br/ULyW58AZ0vDRtEcpUbBpXgRLXw4xR0us"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Where a request came from, for the audit log."""

    ip_address: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformLoginResult:
    admin_id: UUID
    access_token: str
    login_method: str = LOGIN_NORMAL


class _NoAudit:
    async def record(self, event: PlatformAuditEvent) -> None:
        return None


class _Audited:
    def __init__(self, audit: IPlatformAuditLog | None, clock: Callable[[], datetime]) -> None:
        self._audit_log = audit or _NoAudit()
        self._clock = clock

    async def _audit(
        self,
        action: str,
        outcome: str,
        context: RequestContext,
        actor: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._audit_log.record(
            PlatformAuditEvent(
                action=action,
                outcome=outcome,
                occurred_at=self._clock(),
                actor_platform_admin_id=actor,
                metadata=metadata or {},
                ip_address=context.ip_address,
                user_agent=context.user_agent,
            )
        )


class LoginPlatformAdmin(_Audited):
    """Super Admin login. No email OTP, by requirement. Two ways in:

    * NORMAL: the username and Argon2id password hash stored in the database.
    * RECOVERY: the emergency credentials from the server environment, which always
      log in to the primary Super Admin with the same, normal permissions.

    Unknown usernames, wrong passwords and wrong recovery passwords all get the same
    message. Failures of both methods count toward the same per-account lock.
    The caller must commit the session even when this raises, so failures are kept.
    """

    def __init__(
        self,
        admins: IPlatformAdminRepository,
        hasher: IPasswordHasher,
        issue_token: Callable[[UUID], str] | None = None,
        *,
        max_attempts: int,
        lockout: timedelta,
        clock: Callable[[], datetime] = _utcnow,
        recovery: RecoveryCredentials | None = None,
        audit: IPlatformAuditLog | None = None,
        token_factory: Callable[[PlatformAdmin], str] | None = None,
        session_token_factory: Callable[[PlatformAdmin, str], str] | None = None,
    ) -> None:
        if issue_token is None and token_factory is None and session_token_factory is None:
            raise ValueError("issue_token, token_factory or session_token_factory is required")
        super().__init__(audit, clock)
        self._admins = admins
        self._hasher = hasher
        self._issue_token = issue_token
        self._token_factory = token_factory
        self._session_token_factory = session_token_factory
        self._max_attempts = max_attempts
        self._lockout = lockout
        self._recovery = recovery

    async def execute(self, username: str, password: str, context: RequestContext | None = None) -> PlatformLoginResult:
        context = context or RequestContext()
        now = self._clock()
        admin = await self._admins.get_by_username(username, for_update=True)
        recovery_match = self._recovery is not None and self._recovery.matches(username, password)

        # The account whose failure counter and lock apply to this attempt.
        target = admin
        if target is None and self._recovery is not None and self._recovery.username_matches(username):
            target = await self._admins.get_primary(for_update=True)
        if target is None:
            self._hasher.verify(_TIMING_DUMMY_HASH, password)
            await self._audit("LOGIN_FAILED", "FAILURE", context)
            raise AuthenticationFailed(INVALID_CREDENTIALS)
        if target.is_locked(now):
            await self._audit("LOGIN_BLOCKED", "FAILURE", context, target.id, {"reason": "ACCOUNT_LOCKED"})
            raise AccountLocked(ACCOUNT_LOCKED)

        if admin is not None and admin.is_active:
            normal_ok = self._hasher.verify(admin.password_hash, password)
        else:
            self._hasher.verify(_TIMING_DUMMY_HASH, password)  # same cost as a real check
            normal_ok = False

        account: PlatformAdmin | None = None
        method = LOGIN_NORMAL
        if normal_ok:
            account = admin
        elif recovery_match:
            primary = target if target.is_primary else await self._admins.get_primary(for_update=True)
            if primary is not None and primary.is_active and not primary.is_locked(now):
                account, method = primary, LOGIN_RECOVERY

        if account is None:
            failed = target.after_failed_login(now, self._max_attempts, self._lockout)
            await self._admins.save_login_state(failed)
            await self._audit(
                "LOGIN_FAILED", "FAILURE", context, target.id, {"account_locked": failed.locked_until is not None}
            )
            raise AuthenticationFailed(INVALID_CREDENTIALS)

        signed_in = account.after_successful_login(now)
        await self._admins.save_login_state(signed_in)
        await self._audit("LOGIN_SUCCEEDED", "SUCCESS", context, account.id, {"login_method": method})
        if self._session_token_factory is not None:
            token = self._session_token_factory(signed_in, method)
        elif self._token_factory is not None:
            token = self._token_factory(signed_in)
        else:
            assert self._issue_token is not None
            token = self._issue_token(account.id)
        return PlatformLoginResult(admin_id=account.id, access_token=token, login_method=method)


class _CredentialChange(_Audited):
    """Shared steps for changing the normal (database) credentials.

    The current database password must be given. Wrong current passwords count toward
    the same lock as failed logins. A successful change increases the token version,
    which signs out every earlier token. The emergency recovery credentials are not
    touched: they live only in the server environment.
    """

    def __init__(
        self,
        admins: IPlatformAdminRepository,
        hasher: IPasswordHasher,
        *,
        max_attempts: int,
        lockout: timedelta,
        audit: IPlatformAuditLog | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        super().__init__(audit, clock)
        self._admins = admins
        self._hasher = hasher
        self._max_attempts = max_attempts
        self._lockout = lockout

    async def _verify_current_password(
        self, admin_id: UUID, current_password: str, action: str, context: RequestContext
    ) -> PlatformAdmin:
        now = self._clock()
        admin = await self._admins.get_by_id(admin_id, for_update=True)
        if admin is None or not admin.is_active:
            raise AuthenticationFailed("Account is not active.")
        if admin.is_locked(now):
            raise AccountLocked(ACCOUNT_LOCKED)
        if not self._hasher.verify(admin.password_hash, current_password):
            await self._admins.save_login_state(admin.after_failed_login(now, self._max_attempts, self._lockout))
            await self._audit(action, "FAILURE", context, admin.id, {"reason": "CURRENT_PASSWORD_INCORRECT"})
            raise CurrentPasswordIncorrect("The current password is incorrect.")
        return admin


class ChangePlatformUsername(_CredentialChange):
    async def execute(
        self, admin_id: UUID, current_password: str, new_username: str, context: RequestContext | None = None
    ) -> PlatformAdmin:
        context = context or RequestContext()
        ensure_valid_username(new_username)
        admin = await self._verify_current_password(admin_id, current_password, "USERNAME_CHANGED", context)
        if new_username == admin.username:
            raise ValidationFailed("The new username is the same as the current one.")
        updated = admin.with_new_username(new_username)
        await self._admins.save_credentials(updated)
        await self._audit("USERNAME_CHANGED", "SUCCESS", context, admin.id)
        return updated


class ChangePlatformPassword(_CredentialChange):
    async def execute(
        self,
        admin_id: UUID,
        current_password: str,
        new_password: str,
        confirm_new_password: str,
        context: RequestContext | None = None,
    ) -> PlatformAdmin:
        context = context or RequestContext()
        admin = await self._verify_current_password(admin_id, current_password, "PASSWORD_CHANGED", context)
        ensure_acceptable_new_password(new_password, confirm_new_password, admin.username)
        if self._hasher.verify(admin.password_hash, new_password):
            raise ValidationFailed("The new password must be different from the current one.")
        updated = admin.with_new_password_hash(self._hasher.hash(new_password))
        await self._admins.save_credentials(updated)
        await self._audit("PASSWORD_CHANGED", "SUCCESS", context, admin.id)
        return updated


class RecoverPlatformCredentials(_Audited):
    """Emergency recovery: set a new normal username AND password without the forgotten
    database password.

    Allowed only in a session opened with the emergency recovery credentials, for the
    primary Super Admin, and only after those credentials are entered again. Wrong
    re-entries count toward the same lock as failed logins. The emergency credentials
    themselves are never changed: they live only in the server environment.
    """

    def __init__(
        self,
        admins: IPlatformAdminRepository,
        hasher: IPasswordHasher,
        recovery: RecoveryCredentials | None,
        *,
        max_attempts: int,
        lockout: timedelta,
        audit: IPlatformAuditLog | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        super().__init__(audit, clock)
        self._admins = admins
        self._hasher = hasher
        self._recovery = recovery
        self._max_attempts = max_attempts
        self._lockout = lockout

    async def execute(
        self,
        admin_id: UUID,
        session_login_method: str,
        recovery_username: str,
        recovery_password: str,
        new_username: str,
        new_password: str,
        confirm_new_password: str,
        context: RequestContext | None = None,
    ) -> PlatformAdmin:
        context = context or RequestContext()
        if session_login_method != LOGIN_RECOVERY or self._recovery is None:
            raise PermissionDenied("This operation requires a session opened with the emergency recovery credentials.")

        # Input rules first: a malformed request does not count as a failed verification.
        ensure_valid_username(new_username)
        ensure_acceptable_new_password(new_password, confirm_new_password, new_username)
        if new_password == self._recovery.password:
            raise ValidationFailed("The new password must be different from the emergency recovery password.")

        now = self._clock()
        admin = await self._admins.get_by_id(admin_id, for_update=True)
        if admin is None or not admin.is_active:
            raise AuthenticationFailed("Account is not active.")
        if not admin.is_primary:
            raise PermissionDenied("Emergency recovery applies only to the primary Super Admin account.")
        if admin.is_locked(now):
            raise AccountLocked(ACCOUNT_LOCKED)
        if not self._recovery.matches(recovery_username, recovery_password):
            await self._admins.save_login_state(admin.after_failed_login(now, self._max_attempts, self._lockout))
            await self._audit(
                "CREDENTIALS_RECOVERED", "FAILURE", context, admin.id, {"reason": "RECOVERY_CREDENTIALS_INCORRECT"}
            )
            raise CurrentPasswordIncorrect("The emergency recovery credentials are incorrect.")

        updated = admin.with_new_credentials(new_username, self._hasher.hash(new_password))
        await self._admins.save_credentials(updated)
        await self._audit(
            "CREDENTIALS_RECOVERED",
            "SUCCESS",
            context,
            admin.id,
            {"login_method": LOGIN_RECOVERY, "username_changed": new_username != admin.username},
        )
        return updated
