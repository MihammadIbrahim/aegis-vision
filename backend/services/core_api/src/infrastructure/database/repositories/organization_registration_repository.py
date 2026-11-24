"""Registration storage through the narrow database functions from migration 0004.

Runs on the tenant session (the restricted app role) with no signed-in user. That role
has no rights on the registration, OTP or credential tables; it can only call these four
functions, and each one does exactly one step.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.organization import CompletedRegistration, PendingRegistration
from src.domain.repositories.i_organization_registration_repo import (
    CompleteOutcome,
    CompleteResult,
    IssueOutcome,
    IssueResult,
    OtpLimits,
    SubmitOutcome,
    VerifyOutcome,
    VerifyResult,
)

_SUBMIT = text(
    "SELECT app_org_registration_submit(:id, :organization_code, :organization_name, :campus_name, :admin_full_name, "
    ":username, :email, :phone, :password_hash, :recipient_digest, :ttl_seconds)"
).bindparams(bindparam("id", type_=Uuid))

_ISSUE = text(
    "SELECT outcome, retry_after_seconds, email, registration_expires_at FROM app_org_registration_issue_otp("
    ":registration_id, :challenge_id, :code_hash, :ttl_seconds, :max_attempts, :cooldown_seconds, :hourly_limit)"
).bindparams(bindparam("registration_id", type_=Uuid), bindparam("challenge_id", type_=Uuid))

_VERIFY = text(
    "SELECT outcome, attempts_remaining FROM app_org_registration_verify_otp(:registration_id, :code_hash)"
).bindparams(bindparam("registration_id", type_=Uuid))

_COMPLETE = text(
    "SELECT outcome, organization_code, organization_name, campus_name, admin_full_name, username, email, phone, "
    "completed_at FROM app_org_registration_complete(:registration_id, :organization_id, :campus_id, :user_id, "
    ":display_id)"
).bindparams(
    bindparam("registration_id", type_=Uuid),
    bindparam("organization_id", type_=Uuid),
    bindparam("campus_id", type_=Uuid),
    bindparam("user_id", type_=Uuid),
)


class SqlOrganizationRegistrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def submit(self, registration: PendingRegistration) -> SubmitOutcome:
        params = {
            "id": registration.id,
            "organization_code": registration.organization_code,
            "organization_name": registration.organization_name,
            "campus_name": registration.campus_name,
            "admin_full_name": registration.admin_full_name,
            "username": registration.username,
            "email": registration.email,
            "phone": registration.phone,
            "password_hash": registration.password_hash,
            "recipient_digest": registration.recipient_digest,
            "ttl_seconds": registration.ttl_seconds,
        }
        return SubmitOutcome((await self._session.execute(_SUBMIT, params)).scalar_one())

    async def issue_otp(
        self, registration_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        params = {
            "registration_id": registration_id,
            "challenge_id": challenge_id,
            "code_hash": code_hash,
            "ttl_seconds": limits.ttl_seconds,
            "max_attempts": limits.max_attempts,
            "cooldown_seconds": limits.resend_cooldown_seconds,
            "hourly_limit": limits.hourly_limit,
        }
        row = (await self._session.execute(_ISSUE, params)).one()
        return IssueResult(
            outcome=IssueOutcome(row.outcome),
            retry_after_seconds=row.retry_after_seconds,
            email=row.email,
            registration_expires_at=row.registration_expires_at,
        )

    async def verify_otp(self, registration_id: UUID, code_hash: str) -> VerifyResult:
        params = {"registration_id": registration_id, "code_hash": code_hash}
        row = (await self._session.execute(_VERIFY, params)).one()
        return VerifyResult(outcome=VerifyOutcome(row.outcome), attempts_remaining=row.attempts_remaining)

    async def complete(
        self, registration_id: UUID, *, organization_id: UUID, campus_id: UUID, user_id: UUID, display_id: str
    ) -> CompleteResult:
        params: dict[str, Any] = {
            "registration_id": registration_id,
            "organization_id": organization_id,
            "campus_id": campus_id,
            "user_id": user_id,
            "display_id": display_id,
        }
        row = (await self._session.execute(_COMPLETE, params)).one()
        outcome = CompleteOutcome(row.outcome)
        if outcome is not CompleteOutcome.COMPLETED:
            return CompleteResult(outcome=outcome)
        completed_at: datetime = row.completed_at
        return CompleteResult(
            outcome=outcome,
            registration=CompletedRegistration(
                organization_code=row.organization_code,
                organization_name=row.organization_name,
                official_email=row.email,
                official_phone=row.phone,
                campus_name=row.campus_name,
                admin_display_id=display_id,
                admin_full_name=row.admin_full_name,
                admin_username=row.username,
                admin_email=row.email,
                admin_phone=row.phone,
                completed_at=completed_at,
            ),
        )
