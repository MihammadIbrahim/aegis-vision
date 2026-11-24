""" "Already Registered Organization?" applications through the public database functions of
migration 0021. Nobody is signed in on these sessions."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_campus_request_application_repo import (
    ApplicationVerifyResult,
    CampusApplicationForm,
    VerifiedApplication,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import FlowOutcome, IssueResult


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_SUBMIT = _sql(
    "SELECT app_campus_request_submit(:submission_id, :organization_name, :organization_code, :campus_name, "
    ":full_name, :username, :email, :phone, :password_hash, :recipient_digest, :ttl_seconds)",
    "submission_id",
)
_ISSUE = _sql(
    "SELECT outcome, retry_after_seconds, email, expires_at FROM app_campus_request_issue_otp("
    ":submission_id, :challenge_id, :code_hash, :ttl_seconds, :max_attempts, :cooldown_seconds, :hourly_limit)",
    "submission_id",
    "challenge_id",
)
_VERIFY = _sql(
    "SELECT outcome, attempts_remaining, organization_name, campus_name, email, verified_at, decision_deadline "
    "FROM app_campus_request_verify_otp(:submission_id, :code_hash, :request_id)",
    "submission_id",
    "request_id",
)


class SqlCampusRequestApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, statement: Any, params: dict[str, Any]) -> Any:
        return (await self._session.execute(statement, params)).one()

    async def submit(self, form: CampusApplicationForm) -> FlowOutcome:
        params = {
            "submission_id": form.submission_id,
            "organization_name": form.organization_name,
            "organization_code": form.organization_code,
            "campus_name": form.campus_name,
            "full_name": form.full_name,
            "username": form.username,
            "email": form.email,
            "phone": form.phone,
            "password_hash": form.password_hash,
            "recipient_digest": form.recipient_digest,
            "ttl_seconds": form.ttl_seconds,
        }
        return FlowOutcome((await self._session.execute(_SUBMIT, params)).scalar_one())

    async def issue_otp(
        self, submission_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        row = await self._one(
            _ISSUE,
            {
                "submission_id": submission_id,
                "challenge_id": challenge_id,
                "code_hash": code_hash,
                "ttl_seconds": limits.ttl_seconds,
                "max_attempts": limits.max_attempts,
                "cooldown_seconds": limits.resend_cooldown_seconds,
                "hourly_limit": limits.hourly_limit,
            },
        )
        return IssueResult(FlowOutcome(row.outcome), row.retry_after_seconds, row.email, row.expires_at)

    async def verify(self, submission_id: UUID, code_hash: str, request_id: UUID) -> ApplicationVerifyResult:
        row = await self._one(
            _VERIFY, {"submission_id": submission_id, "code_hash": code_hash, "request_id": request_id}
        )
        outcome = FlowOutcome(row.outcome)
        if outcome is not FlowOutcome.COMPLETED:
            return ApplicationVerifyResult(outcome, row.attempts_remaining)
        return ApplicationVerifyResult(
            outcome,
            application=VerifiedApplication(
                request_id=request_id,
                organization_name=row.organization_name,
                campus_name=row.campus_name,
                email=row.email,
                verified_at=row.verified_at,
                decision_deadline=row.decision_deadline,
            ),
        )
