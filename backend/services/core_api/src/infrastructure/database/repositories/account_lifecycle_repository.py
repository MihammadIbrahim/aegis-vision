"""Ownership transfer and Admin account deletion through the narrow functions of migrations 0010 to 0012.
Must run on a session where ``bind_actor`` was called (routes behind ``OrganizationAdminDep``)."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    DeletionOverview,
    DeletionResult,
    FlowOutcome,
    IncomingTransfer,
    IssueResult,
    OwnershipCandidate,
    OwnershipHistoryEntry,
    VerifyResult,
)


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_ISSUE_ARGS = ":subject_id, :challenge_id, :code_hash, :ttl, :max_attempts, :cooldown, :hourly"
_OVERVIEW = text(
    "SELECT is_main_admin, blocker, campus_will_be_deleted, organization_will_be_deleted, transfer_id, "
    "transfer_status, transfer_to_name, transfer_expires_at FROM app_admin_deletion_overview()"
)
_CANDIDATES = text("SELECT admin_id, full_name FROM app_ownership_candidates()")
_START_TRANSFER = _sql("SELECT app_ownership_transfer_start(:id, :recipient, :ttl)", "id")
_ISSUE_TRANSFER = _sql(
    f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_ownership_transfer_issue_otp({_ISSUE_ARGS})",
    "subject_id",
    "challenge_id",
)
_VERIFY_TRANSFER = _sql(
    "SELECT outcome, attempts_remaining, expires_at FROM app_ownership_transfer_verify_otp(:id, :code_hash, :ttl)",
    "id",
)
_CANCEL_TRANSFER = _sql("SELECT app_ownership_transfer_cancel(:id)", "id")
_INCOMING = text("SELECT transfer_id, from_name, created_at, expires_at FROM app_ownership_transfers_incoming()")
_HISTORY = text(
    "SELECT transfer_id, previous_owner_name, previous_owner_username, previous_owner_email, status, "
    "offered_at, accepted_at FROM app_ownership_history()"
)
_DECIDE = _sql("SELECT app_ownership_transfer_decide(:id, :accept)", "id")
_START_DELETION = _sql("SELECT app_admin_deletion_start(:id, :ttl)", "id")
_ISSUE_DELETION = _sql(
    f"SELECT outcome, retry_after_seconds, email, expires_at FROM app_admin_deletion_issue_otp({_ISSUE_ARGS})",
    "subject_id",
    "challenge_id",
)
_COMPLETE_DELETION = _sql(
    "SELECT outcome, attempts_remaining, campus_deleted, organization_deleted "
    "FROM app_admin_deletion_complete(:id, :code_hash)",
    "id",
)


_EXPORT = _sql("SELECT app_admin_deletion_export(:id)", "id")


def _issue_params(subject_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "challenge_id": challenge_id,
        "code_hash": code_hash,
        "ttl": limits.ttl_seconds,
        "max_attempts": limits.max_attempts,
        "cooldown": limits.resend_cooldown_seconds,
        "hourly": limits.hourly_limit,
    }


class SqlAccountLifecycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scalar(self, statement: Any, params: dict[str, Any]) -> Any:
        return (await self._session.execute(statement, params)).scalar_one()

    async def overview(self) -> DeletionOverview | None:
        row = (await self._session.execute(_OVERVIEW)).one_or_none()
        if row is None:
            return None
        return DeletionOverview(
            is_main_admin=row.is_main_admin,
            blocker=FlowOutcome(row.blocker) if row.blocker else None,
            campus_will_be_deleted=row.campus_will_be_deleted,
            organization_will_be_deleted=row.organization_will_be_deleted,
            transfer_id=row.transfer_id,
            transfer_status=row.transfer_status,
            transfer_to_name=row.transfer_to_name,
            transfer_expires_at=row.transfer_expires_at,
        )

    async def candidates(self) -> list[OwnershipCandidate]:
        return [OwnershipCandidate(r.admin_id, r.full_name) for r in (await self._session.execute(_CANDIDATES)).all()]

    async def start_transfer(
        self, transfer_id: UUID, recipient_admin_id: str, *, verify_ttl_seconds: int
    ) -> FlowOutcome:
        params = {"id": transfer_id, "recipient": recipient_admin_id, "ttl": verify_ttl_seconds}
        return FlowOutcome(await self._scalar(_START_TRANSFER, params))

    async def issue_transfer_otp(
        self, transfer_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        row = (
            await self._session.execute(_ISSUE_TRANSFER, _issue_params(transfer_id, challenge_id, code_hash, limits))
        ).one()
        return IssueResult(FlowOutcome(row.outcome), row.retry_after_seconds, row.email, row.expires_at)

    async def verify_transfer_otp(self, transfer_id: UUID, code_hash: str, *, accept_ttl_seconds: int) -> VerifyResult:
        params = {"id": transfer_id, "code_hash": code_hash, "ttl": accept_ttl_seconds}
        row = (await self._session.execute(_VERIFY_TRANSFER, params)).one()
        return VerifyResult(FlowOutcome(row.outcome), row.attempts_remaining, row.expires_at)

    async def cancel_transfer(self, transfer_id: UUID) -> bool:
        return await self._scalar(_CANCEL_TRANSFER, {"id": transfer_id}) == "CANCELLED"

    async def incoming(self) -> list[IncomingTransfer]:
        rows = (await self._session.execute(_INCOMING)).all()
        return [IncomingTransfer(r.transfer_id, r.from_name, r.created_at, r.expires_at) for r in rows]

    async def decide(self, transfer_id: UUID, *, accept: bool) -> FlowOutcome:
        return FlowOutcome(await self._scalar(_DECIDE, {"id": transfer_id, "accept": accept}))

    async def start_deletion(self, request_id: UUID, *, ttl_seconds: int) -> FlowOutcome:
        return FlowOutcome(await self._scalar(_START_DELETION, {"id": request_id, "ttl": ttl_seconds}))

    async def issue_deletion_otp(
        self, request_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult:
        row = (
            await self._session.execute(_ISSUE_DELETION, _issue_params(request_id, challenge_id, code_hash, limits))
        ).one()
        return IssueResult(FlowOutcome(row.outcome), row.retry_after_seconds, row.email, row.expires_at)

    async def complete_deletion(self, request_id: UUID, code_hash: str) -> DeletionResult:
        row = (await self._session.execute(_COMPLETE_DELETION, {"id": request_id, "code_hash": code_hash})).one()
        return DeletionResult(
            FlowOutcome(row.outcome), row.attempts_remaining, row.campus_deleted, row.organization_deleted
        )

    async def export_campus(self, request_id: UUID) -> tuple[FlowOutcome, dict[str, Any]]:
        data = dict(await self._scalar(_EXPORT, {"id": request_id}))
        return FlowOutcome(data.pop("outcome")), data

    async def history(self) -> list[OwnershipHistoryEntry]:
        return [OwnershipHistoryEntry(*row) for row in (await self._session.execute(_HISTORY)).all()]
