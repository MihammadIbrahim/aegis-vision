"""Organization Admin sign-in storage through the narrow database functions of migration 0006.

Runs on the tenant session (the restricted app role) with no signed-in user. That role has
no rights on ``user_credentials`` or the sign-in tables; it can only call these functions.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_admin_login_repo import (
    LoginAccount,
    LoginIssueOutcome,
    LoginIssueResult,
    LoginVerifyOutcome,
    LoginVerifyResult,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits

_LOOKUP = text(
    "SELECT user_id, password_hash, eligible, locked_until "
    "FROM app_admin_login_lookup(:organization_name, :organization_code, :email)"
)
_GUARD_LOOKUP = text(
    "SELECT user_id, password_hash, eligible, locked_until FROM app_guard_login_lookup(:guard_id, :email)"
)
_GUARD_STATIONED = text("SELECT app_guard_is_stationed(:user_id)").bindparams(bindparam("user_id", type_=Uuid))
_RECORD_FAILURE = text("SELECT app_admin_login_record_failure(:user_id, :max_attempts, :window_seconds)").bindparams(
    bindparam("user_id", type_=Uuid)
)
_START = text("SELECT app_admin_login_start(:user_id, :login_id, :ttl_seconds)").bindparams(
    bindparam("user_id", type_=Uuid), bindparam("login_id", type_=Uuid)
)
_ISSUE = text(
    "SELECT outcome, retry_after_seconds, email, login_expires_at FROM app_admin_login_issue_otp("
    ":login_id, :challenge_id, :code_hash, :ttl_seconds, :max_attempts, :cooldown_seconds, :hourly_limit)"
).bindparams(bindparam("login_id", type_=Uuid), bindparam("challenge_id", type_=Uuid))
_VERIFY = text(
    "SELECT outcome, attempts_remaining, user_id FROM app_admin_login_verify_otp(:login_id, :code_hash)"
).bindparams(bindparam("login_id", type_=Uuid))


_PENDING_REQUEST = text(
    "SELECT password_hash FROM app_campus_request_login_lookup(:organization_name, :organization_code, :email)"
)


class SqlAdminLoginRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_account(self, organization_name: str, organization_code: str, email: str) -> LoginAccount | None:
        params = {"organization_name": organization_name, "organization_code": organization_code, "email": email}
        rows = (await self._session.execute(_LOOKUP, params)).all()
        if len(rows) != 1:  # emails are unique, so more than one match cannot happen
            return None
        row = rows[0]
        return LoginAccount(
            user_id=row.user_id, password_hash=row.password_hash, eligible=row.eligible, locked_until=row.locked_until
        )

    async def find_pending_campus_request(
        self, organization_name: str, organization_code: str, email: str
    ) -> str | None:
        params = {"organization_name": organization_name, "organization_code": organization_code, "email": email}
        rows = (await self._session.execute(_PENDING_REQUEST, params)).all()
        return rows[0].password_hash if len(rows) == 1 else None

    async def find_guard_account(self, guard_id: str, email: str) -> LoginAccount | None:
        rows = (await self._session.execute(_GUARD_LOOKUP, {"guard_id": guard_id, "email": email})).all()
        if len(rows) != 1:
            return None
        row = rows[0]
        return LoginAccount(
            user_id=row.user_id, password_hash=row.password_hash, eligible=row.eligible, locked_until=row.locked_until
        )

    async def guard_is_stationed(self, user_id: UUID) -> bool | None:
        """True/False for an active Guard with/without a location; None for anyone else (0019)."""
        stationed: bool | None = await self._session.scalar(_GUARD_STATIONED, {"user_id": user_id})
        return stationed

    async def record_failure(self, user_id: UUID, *, max_attempts: int, window_seconds: int) -> datetime | None:
        params = {"user_id": user_id, "max_attempts": max_attempts, "window_seconds": window_seconds}
        locked_until: datetime | None = (await self._session.execute(_RECORD_FAILURE, params)).scalar_one()
        return locked_until

    async def start(self, user_id: UUID, login_id: UUID, *, ttl_seconds: int) -> None:
        await self._session.execute(_START, {"user_id": user_id, "login_id": login_id, "ttl_seconds": ttl_seconds})

    async def issue_otp(
        self, login_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> LoginIssueResult:
        params = {
            "login_id": login_id,
            "challenge_id": challenge_id,
            "code_hash": code_hash,
            "ttl_seconds": limits.ttl_seconds,
            "max_attempts": limits.max_attempts,
            "cooldown_seconds": limits.resend_cooldown_seconds,
            "hourly_limit": limits.hourly_limit,
        }
        row = (await self._session.execute(_ISSUE, params)).one()
        return LoginIssueResult(
            outcome=LoginIssueOutcome(row.outcome),
            retry_after_seconds=row.retry_after_seconds,
            email=row.email,
            login_expires_at=row.login_expires_at,
        )

    async def verify_otp(self, login_id: UUID, code_hash: str) -> LoginVerifyResult:
        row = (await self._session.execute(_VERIFY, {"login_id": login_id, "code_hash": code_hash})).one()
        return LoginVerifyResult(
            outcome=LoginVerifyOutcome(row.outcome), attempts_remaining=row.attempts_remaining, user_id=row.user_id
        )
