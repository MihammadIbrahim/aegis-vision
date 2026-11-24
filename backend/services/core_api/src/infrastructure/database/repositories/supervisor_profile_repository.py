"""Supervisor profile: read the caller's own profile, and request/verify/apply/cancel changes
through the narrow functions of migration 0008. Must run on a session where ``bind_actor``
was called (routes behind ``SupervisorDep``): the functions act only for that caller."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    IssueResult,
    ProfileAccount,
    SupervisorProfile,
    VerifyResult,
)
from src.infrastructure.database.models import OrganizationModel, UserModel


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_LOOKUP = text("SELECT user_id, password_hash, locked_until FROM app_profile_lookup()")
_RECORD_FAILURE = _sql("SELECT app_admin_login_record_failure(:user_id, :max_attempts, :window_seconds)", "user_id")
_SUBMIT = _sql(
    "SELECT app_profile_change_submit(:change_id, :full_name, :username, :email, :password_hash, :digest, :ttl)",
    "change_id",
)
_ISSUE = _sql(
    "SELECT outcome, retry_after_seconds, email, expires_at FROM app_profile_change_issue_otp("
    ":change_id, :new_email, :challenge_id, :code_hash, :ttl, :max_attempts, :cooldown, :hourly)",
    "change_id",
    "challenge_id",
)
_VERIFY = _sql(
    "SELECT outcome, attempts_remaining FROM app_profile_change_verify_otp(:change_id, :new_email, :code_hash)",
    "change_id",
)
_APPLY = _sql("SELECT outcome, password_changed FROM app_profile_change_apply(:change_id)", "change_id")
_CANCEL = _sql("SELECT app_profile_change_cancel(:change_id)", "change_id")


class SqlSupervisorProfileRepository:
    # The Guard repository below swaps these for its own statements.
    LOOKUP_SQL: Any = _LOOKUP
    SUBMIT_SQL: Any = _SUBMIT
    ISSUE_SQL: Any = _ISSUE
    VERIFY_SQL: Any = _VERIFY
    APPLY_SQL: Any = _APPLY
    CANCEL_SQL: Any = _CANCEL

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def profile(self, user_id: UUID, organization_id: UUID, campus_id: UUID) -> SupervisorProfile | None:
        row = (
            await self._session.execute(
                select(UserModel, OrganizationModel)
                .join(OrganizationModel, OrganizationModel.id == UserModel.organization_id)
                .where(
                    UserModel.id == user_id,
                    UserModel.organization_id == organization_id,
                    UserModel.campus_id == campus_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        user, organization = row
        return SupervisorProfile(
            organization_name=organization.name,
            organization_id=organization.organization_code,
            supervisor_id=user.display_id,
            full_name=user.full_name,
            username=user.username,
            email=user.email,
            phone=user.phone,
        )

    async def account(self) -> ProfileAccount | None:
        rows = (await self._session.execute(self.LOOKUP_SQL)).all()
        if len(rows) != 1:
            return None
        return ProfileAccount(rows[0].user_id, rows[0].password_hash, rows[0].locked_until)

    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None:
        params = {"user_id": user_id, "max_attempts": max_attempts, "window_seconds": window_seconds}
        locked: datetime | None = (await self._session.execute(_RECORD_FAILURE, params)).scalar_one()
        return locked

    async def submit(
        self,
        change_id: UUID,
        *,
        full_name: str | None,
        username: str | None,
        email: str | None,
        password_hash: str | None,
        new_email_digest: str | None,
        ttl_seconds: int,
    ) -> FlowOutcome:
        params = {
            "change_id": change_id,
            "full_name": full_name,
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "digest": new_email_digest,
            "ttl": ttl_seconds,
        }
        return FlowOutcome((await self._session.execute(self.SUBMIT_SQL, params)).scalar_one())

    async def issue(
        self, change_id: UUID, *, new_email: bool, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        params: dict[str, Any] = {
            "change_id": change_id,
            "new_email": new_email,
            "challenge_id": challenge_id,
            "code_hash": code_hash,
            "ttl": limits.ttl_seconds,
            "max_attempts": limits.max_attempts,
            "cooldown": limits.resend_cooldown_seconds,
            "hourly": limits.hourly_limit,
        }
        row = (await self._session.execute(self.ISSUE_SQL, params)).one()
        return IssueResult(FlowOutcome(row.outcome), row.retry_after_seconds, row.email, row.expires_at)

    async def verify(self, change_id: UUID, *, new_email: bool, code_hash: str) -> VerifyResult:
        params = {"change_id": change_id, "new_email": new_email, "code_hash": code_hash}
        row = (await self._session.execute(self.VERIFY_SQL, params)).one()
        return VerifyResult(FlowOutcome(row.outcome), row.attempts_remaining)

    async def apply(self, change_id: UUID) -> tuple[FlowOutcome, bool]:
        row = (await self._session.execute(self.APPLY_SQL, {"change_id": change_id})).one()
        return FlowOutcome(row.outcome), bool(row.password_changed)

    async def cancel(self, change_id: UUID) -> bool:
        return (await self._session.execute(self.CANCEL_SQL, {"change_id": change_id})).scalar_one() == "CANCELLED"


class SqlGuardProfileRepository(SqlSupervisorProfileRepository):
    """A Guard's own profile (migration 0009): only email, phone number and password can change."""

    LOOKUP_SQL = text("SELECT user_id, password_hash, locked_until FROM app_guard_profile_lookup()")
    SUBMIT_SQL = _sql(
        "SELECT app_guard_profile_change_submit(:change_id, :email, :phone, :password_hash, :digest, :ttl)",
        "change_id",
    )
    ISSUE_SQL = _sql(
        "SELECT outcome, retry_after_seconds, email, expires_at FROM app_guard_profile_change_issue_otp("
        ":change_id, :new_email, :challenge_id, :code_hash, :ttl, :max_attempts, :cooldown, :hourly)",
        "change_id",
        "challenge_id",
    )
    VERIFY_SQL = _sql(
        "SELECT outcome, attempts_remaining "
        "FROM app_guard_profile_change_verify_otp(:change_id, :new_email, :code_hash)",
        "change_id",
    )
    APPLY_SQL = _sql("SELECT outcome, password_changed FROM app_guard_profile_change_apply(:change_id)", "change_id")
    CANCEL_SQL = _sql("SELECT app_guard_profile_change_cancel(:change_id)", "change_id")

    async def submit_guard_change(
        self,
        change_id: UUID,
        *,
        email: str | None,
        phone: str | None,
        password_hash: str | None,
        new_email_digest: str | None,
        ttl_seconds: int,
    ) -> FlowOutcome:
        params = {
            "change_id": change_id,
            "email": email,
            "phone": phone,
            "password_hash": password_hash,
            "digest": new_email_digest,
            "ttl": ttl_seconds,
        }
        return FlowOutcome((await self._session.execute(self.SUBMIT_SQL, params)).scalar_one())


class SqlAdminProfileRepository(SqlSupervisorProfileRepository):
    """An Admin's own profile changes (migration 0029): the same steps through ``app_admin_profile_*``,
    plus the phone number."""

    SUPPORTS_PHONE = True
    LOOKUP_SQL = text("SELECT user_id, password_hash, locked_until FROM app_admin_profile_lookup()")
    SUBMIT_SQL = _sql(
        "SELECT app_admin_profile_change_submit(:change_id, :full_name, :username, :email, :password_hash, :digest, "
        ":ttl, :phone)",
        "change_id",
    )
    ISSUE_SQL = _sql(
        "SELECT outcome, retry_after_seconds, email, expires_at FROM app_admin_profile_change_issue_otp("
        ":change_id, :new_email, :challenge_id, :code_hash, :ttl, :max_attempts, :cooldown, :hourly)",
        "change_id",
        "challenge_id",
    )
    VERIFY_SQL = _sql(
        "SELECT outcome, attempts_remaining FROM app_admin_profile_change_verify_otp("
        ":change_id, :new_email, :code_hash)",
        "change_id",
    )
    APPLY_SQL = _sql("SELECT outcome, password_changed FROM app_admin_profile_change_apply(:change_id)", "change_id")
    CANCEL_SQL = _sql("SELECT app_admin_profile_change_cancel(:change_id)", "change_id")

    async def submit(  # type: ignore[override]
        self,
        change_id: UUID,
        *,
        full_name: str | None,
        username: str | None,
        email: str | None,
        password_hash: str | None,
        new_email_digest: str | None,
        ttl_seconds: int,
        phone: str | None = None,
    ) -> FlowOutcome:
        params = {
            "change_id": change_id,
            "full_name": full_name,
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "digest": new_email_digest,
            "ttl": ttl_seconds,
            "phone": phone,
        }
        return FlowOutcome((await self._session.execute(self.SUBMIT_SQL, params)).scalar_one())
