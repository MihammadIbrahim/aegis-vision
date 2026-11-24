"""Step-up, Supervisor creation and password reset through the narrow database functions of
migration 0007. The step-up and creation functions act only for the caller bound for
Row-Level Security, so this repository must run on a session where ``bind_actor`` was called
(every route behind ``OrganizationAdminDep``). Password-reset functions are public."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    CompleteSupervisorResult,
    CreatedSupervisor,
    FlowOutcome,
    IssueResult,
    ResetIdentity,
    StepUpAccount,
    SupervisorForm,
    VerifyResult,
)


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_ISSUE_ARGS = ":subject_id, :challenge_id, :code_hash, :ttl_seconds, :max_attempts, :cooldown_seconds, :hourly_limit"

_STEP_UP_LOOKUP = text("SELECT user_id, password_hash, locked_until FROM app_step_up_lookup()")
_RECORD_FAILURE = _sql("SELECT app_admin_login_record_failure(:user_id, :max_attempts, :window_seconds)", "user_id")
_STEP_UP_START = _sql("SELECT app_step_up_start(:grant_id, :ttl_seconds)", "grant_id")
_STEP_UP_CONSUME = _sql("SELECT app_step_up_consume(:grant_id)", "grant_id")  # migration 0024
_STEP_UP_ISSUE = _sql(
    f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_step_up_issue_otp({_ISSUE_ARGS})",
    "subject_id",
    "challenge_id",
)
_STEP_UP_VERIFY = _sql(
    "SELECT outcome, attempts_remaining, expires_at FROM app_step_up_verify_otp(:grant_id, :code_hash, :form_ttl)",
    "grant_id",
)
_SUPERVISOR_SUBMIT = _sql(
    "SELECT app_supervisor_creation_submit(:creation_id, :grant_id, :full_name, :username, :email, :phone, "
    ":password_hash, :recipient_digest, :ttl_seconds)",
    "creation_id",
    "grant_id",
)
_SUPERVISOR_ISSUE = _sql(
    f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_supervisor_creation_issue_otp({_ISSUE_ARGS})",
    "subject_id",
    "challenge_id",
)
_SUPERVISOR_VERIFY = _sql(
    "SELECT outcome, attempts_remaining FROM app_supervisor_creation_verify_otp(:creation_id, :code_hash)",
    "creation_id",
)
_SUPERVISOR_COMPLETE = _sql(
    "SELECT outcome, full_name, username, email, phone, campus_name, organization_code, completed_at "
    "FROM app_supervisor_creation_complete(:creation_id, :user_id, :display_id)",
    "creation_id",
    "user_id",
)
_RESET_REQUEST = _sql(
    "SELECT app_password_reset_request(:request_id, :organization_code, :email, :subject_digest, :ttl_seconds)",
    "request_id",
)
_RESET_REQUEST_BY_SUPERVISOR = _sql(
    "SELECT app_password_reset_request_by_supervisor_id(:request_id, :supervisor_id, :email, :subject_digest, "
    ":ttl_seconds)",
    "request_id",
)
_RESET_REQUEST_BY_GUARD = _sql(
    "SELECT app_password_reset_request_by_guard_mode(:request_id, :guard_id, :email, :stationed, :subject_digest, "
    ":ttl_seconds)",
    "request_id",
)
_RESET_ISSUE = _sql(
    f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_password_reset_issue_otp({_ISSUE_ARGS})",
    "subject_id",
    "challenge_id",
)
_RESET_VERIFY = _sql(
    "SELECT outcome, attempts_remaining FROM app_password_reset_verify_otp(:request_id, :code_hash, :token_hash, "
    ":token_ttl)",
    "request_id",
)
_RESET_IDENTITY = _sql(
    "SELECT outcome, username, email FROM app_password_reset_identity(:request_id, :token_hash)", "request_id"
)
_RESET_COMPLETE = _sql("SELECT app_password_reset_complete(:request_id, :token_hash, :password_hash)", "request_id")


def _issue_params(subject_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "challenge_id": challenge_id,
        "code_hash": code_hash,
        "ttl_seconds": limits.ttl_seconds,
        "max_attempts": limits.max_attempts,
        "cooldown_seconds": limits.resend_cooldown_seconds,
        "hourly_limit": limits.hourly_limit,
    }


def _issue_result(row: Any) -> IssueResult:
    return IssueResult(FlowOutcome(row.outcome), row.retry_after_seconds, row.email, row.expires_at)


class SqlStaffAccountRepository:
    # The step-up and creation statements; the Guard repository below swaps them for its own.
    STEP_UP_LOOKUP_SQL: Any = _STEP_UP_LOOKUP
    STEP_UP_START_SQL: Any = _STEP_UP_START
    STEP_UP_ISSUE_SQL: Any = _STEP_UP_ISSUE
    STEP_UP_VERIFY_SQL: Any = _STEP_UP_VERIFY
    SUPERVISOR_SUBMIT_SQL: Any = _SUPERVISOR_SUBMIT
    SUPERVISOR_ISSUE_SQL: Any = _SUPERVISOR_ISSUE
    SUPERVISOR_VERIFY_SQL: Any = _SUPERVISOR_VERIFY
    SUPERVISOR_COMPLETE_SQL: Any = _SUPERVISOR_COMPLETE

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, statement: Any, params: dict[str, Any]) -> Any:
        return (await self._session.execute(statement, params)).one()

    # --- step-up ---
    async def step_up_account(self) -> StepUpAccount | None:
        rows = (await self._session.execute(self.STEP_UP_LOOKUP_SQL)).all()
        if len(rows) != 1:
            return None
        return StepUpAccount(
            user_id=rows[0].user_id, password_hash=rows[0].password_hash, locked_until=rows[0].locked_until
        )

    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None:
        params = {"user_id": user_id, "max_attempts": max_attempts, "window_seconds": window_seconds}
        locked: datetime | None = (await self._session.execute(_RECORD_FAILURE, params)).scalar_one()
        return locked

    async def start_step_up(self, grant_id: UUID, *, ttl_seconds: int) -> None:
        await self._session.execute(self.STEP_UP_START_SQL, {"grant_id": grant_id, "ttl_seconds": ttl_seconds})

    async def consume_step_up(self, grant_id: UUID) -> str:
        """Use up one verified, unexpired confirmation of the caller: ``CONSUMED``, or ``NOT_FOUND``,
        ``ALREADY_USED``, ``NOT_VERIFIED``, ``EXPIRED`` (campus-request approval, decision H)."""
        return str((await self._session.execute(_STEP_UP_CONSUME, {"grant_id": grant_id})).scalar_one())

    async def issue_step_up_otp(
        self, grant_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        return _issue_result(
            await self._one(self.STEP_UP_ISSUE_SQL, _issue_params(grant_id, challenge_id, code_hash, limits))
        )

    async def verify_step_up_otp(self, grant_id: UUID, code_hash: str, *, form_ttl_seconds: int) -> VerifyResult:
        row = await self._one(
            self.STEP_UP_VERIFY_SQL, {"grant_id": grant_id, "code_hash": code_hash, "form_ttl": form_ttl_seconds}
        )
        return VerifyResult(FlowOutcome(row.outcome), row.attempts_remaining, row.expires_at)

    # --- Supervisor creation ---
    async def submit_supervisor(self, form: SupervisorForm) -> FlowOutcome:
        params = {
            "creation_id": form.creation_id,
            "grant_id": form.authorization_id,
            "full_name": form.full_name,
            "username": form.username,
            "email": form.email,
            "phone": form.phone,
            "password_hash": form.password_hash,
            "recipient_digest": form.recipient_digest,
            "ttl_seconds": form.ttl_seconds,
        }
        return FlowOutcome((await self._session.execute(self.SUPERVISOR_SUBMIT_SQL, params)).scalar_one())

    async def issue_supervisor_otp(
        self, creation_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        return _issue_result(
            await self._one(self.SUPERVISOR_ISSUE_SQL, _issue_params(creation_id, challenge_id, code_hash, limits))
        )

    async def verify_supervisor_otp(self, creation_id: UUID, code_hash: str) -> VerifyResult:
        row = await self._one(self.SUPERVISOR_VERIFY_SQL, {"creation_id": creation_id, "code_hash": code_hash})
        return VerifyResult(FlowOutcome(row.outcome), row.attempts_remaining)

    async def complete_supervisor(
        self, creation_id: UUID, *, user_id: UUID, display_id: str
    ) -> CompleteSupervisorResult:
        row = await self._one(
            self.SUPERVISOR_COMPLETE_SQL, {"creation_id": creation_id, "user_id": user_id, "display_id": display_id}
        )
        outcome = FlowOutcome(row.outcome)
        if outcome is not FlowOutcome.COMPLETED:
            return CompleteSupervisorResult(outcome)
        return CompleteSupervisorResult(
            outcome,
            CreatedSupervisor(
                supervisor_id=display_id,
                full_name=row.full_name,
                username=row.username,
                email=row.email,
                phone=row.phone,
                campus_name=row.campus_name,
                organization_code=row.organization_code,
                created_at=row.completed_at,
            ),
        )

    # --- password reset ---
    async def request_password_reset(
        self, request_id: UUID, organization_code: str, email: str, subject_digest: str, *, ttl_seconds: int
    ) -> None:
        params = {
            "request_id": request_id,
            "organization_code": organization_code,
            "email": email,
            "subject_digest": subject_digest,
            "ttl_seconds": ttl_seconds,
        }
        await self._session.execute(_RESET_REQUEST, params)

    async def request_password_reset_by_supervisor_id(
        self, request_id: UUID, supervisor_id: str, email: str, subject_digest: str, *, ttl_seconds: int
    ) -> None:
        params = {
            "request_id": request_id,
            "supervisor_id": supervisor_id,
            "email": email,
            "subject_digest": subject_digest,
            "ttl_seconds": ttl_seconds,
        }
        await self._session.execute(_RESET_REQUEST_BY_SUPERVISOR, params)

    async def request_password_reset_by_guard_id(
        self, request_id: UUID, guard_id: str, email: str, subject_digest: str, *, ttl_seconds: int, stationed: bool
    ) -> None:
        """Matches only a Guard whose assignment fits the chosen mode (migration 0019)."""
        params = {
            "request_id": request_id,
            "guard_id": guard_id,
            "email": email,
            "stationed": stationed,
            "subject_digest": subject_digest,
            "ttl_seconds": ttl_seconds,
        }
        await self._session.execute(_RESET_REQUEST_BY_GUARD, params)

    async def issue_reset_otp(
        self, request_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        return _issue_result(await self._one(_RESET_ISSUE, _issue_params(request_id, challenge_id, code_hash, limits)))

    async def verify_reset_otp(
        self, request_id: UUID, code_hash: str, token_hash: str, *, token_ttl_seconds: int
    ) -> VerifyResult:
        params = {
            "request_id": request_id,
            "code_hash": code_hash,
            "token_hash": token_hash,
            "token_ttl": token_ttl_seconds,
        }
        row = await self._one(_RESET_VERIFY, params)
        return VerifyResult(FlowOutcome(row.outcome), row.attempts_remaining)

    async def reset_identity(self, request_id: UUID, token_hash: str) -> ResetIdentity:
        row = await self._one(_RESET_IDENTITY, {"request_id": request_id, "token_hash": token_hash})
        return ResetIdentity(FlowOutcome(row.outcome), row.username, row.email)

    async def complete_reset(self, request_id: UUID, token_hash: str, password_hash: str) -> FlowOutcome:
        params = {"request_id": request_id, "token_hash": token_hash, "password_hash": password_hash}
        return FlowOutcome((await self._session.execute(_RESET_COMPLETE, params)).scalar_one())


class SqlGuardCreationRepository(SqlStaffAccountRepository):
    """Guard creation (migration 0009) by an Admin or Supervisor: the same steps as Supervisor
    creation, through the ``app_guard_*`` functions and Guard-only tables."""

    STEP_UP_LOOKUP_SQL = text("SELECT user_id, password_hash, locked_until FROM app_guard_step_up_lookup()")
    STEP_UP_START_SQL = _sql("SELECT app_guard_step_up_start(:grant_id, :ttl_seconds)", "grant_id")
    STEP_UP_ISSUE_SQL = _sql(
        f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_guard_step_up_issue_otp({_ISSUE_ARGS})",
        "subject_id",
        "challenge_id",
    )
    STEP_UP_VERIFY_SQL = _sql(
        "SELECT outcome, attempts_remaining, expires_at FROM app_guard_step_up_verify_otp(:grant_id, :code_hash, "
        ":form_ttl)",
        "grant_id",
    )
    SUPERVISOR_SUBMIT_SQL = _sql(
        "SELECT app_guard_creation_submit(:creation_id, :grant_id, :full_name, :username, :email, :phone, "
        ":password_hash, :recipient_digest, :ttl_seconds)",
        "creation_id",
        "grant_id",
    )
    SUPERVISOR_ISSUE_SQL = _sql(
        f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_guard_creation_issue_otp({_ISSUE_ARGS})",
        "subject_id",
        "challenge_id",
    )
    SUPERVISOR_VERIFY_SQL = _sql(
        "SELECT outcome, attempts_remaining FROM app_guard_creation_verify_otp(:creation_id, :code_hash)",
        "creation_id",
    )
    SUPERVISOR_COMPLETE_SQL = _sql(
        "SELECT outcome, full_name, username, email, phone, campus_name, organization_code, completed_at "
        "FROM app_guard_creation_complete(:creation_id, :user_id, :display_id)",
        "creation_id",
        "user_id",
    )
