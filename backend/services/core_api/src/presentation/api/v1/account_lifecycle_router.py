"""Delete Account and Main Admin ownership transfer, for the signed-in Admin (``OrganizationAdminDep``).

GET    /organization-admin/account/deletion                        what Delete Account will do; transfer status
POST   /organization-admin/account/ownership-transfer              recipient + current password; emails a code
POST   /organization-admin/account/ownership-transfer/{id}/resend-otp
POST   /organization-admin/account/ownership-transfer/{id}/verify  code; sends the request to the recipient
DELETE /organization-admin/account/ownership-transfer/{id}         cancel before acceptance
GET    /organization-admin/ownership-transfers/incoming            requests waiting for the caller
GET    /organization-admin/ownership-transfers/history             Main Admin only: accepted transfers to them
POST   /organization-admin/ownership-transfers/{id}/accept | /reject
POST   /organization-admin/account/deletion                        Admin ID + current password; emails a code
GET    /organization-admin/account/deletion/{id}/export            optional JSON copy of the caller's campus
POST   /organization-admin/account/deletion/{id}/resend-otp
POST   /organization-admin/account/deletion/{id}/verify            fresh code; deletes the account (and campus)
"""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.application.dtos.account_lifecycle_dto import (
    AccountDeletedResponse,
    DeletionStartedResponse,
    DeletionStatusResponse,
    IncomingTransferResponse,
    OwnershipHistoryResponse,
    StartAccountDeletionRequest,
    StartOwnershipTransferRequest,
    TransferDecisionResponse,
    TransferVerifiedResponse,
)
from src.application.dtos.supervisor_dto import CodeSentResponse, OtpRequest
from src.application.use_cases.account_lifecycle_use_cases import (
    EXPORT_NOT_INCLUDED,
    CancelOwnershipTransfer,
    CompleteAccountDeletion,
    DecideOwnershipTransfer,
    ExportCampusData,
    GetDeletionStatus,
    ListIncomingTransfers,
    ListOwnershipHistory,
    ResendAccountDeletionCode,
    ResendOwnershipTransferCode,
    StartAccountDeletion,
    StartOwnershipTransfer,
    VerifyOwnershipTransfer,
)
from src.config.settings import Settings
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import FlowOutcome
from src.infrastructure.database.repositories.account_lifecycle_repository import (
    SqlAccountLifecycleRepository,
)
from src.infrastructure.database.repositories.staff_account_repository import (
    SqlStaffAccountRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.dependencies import OrganizationAdminDep, SessionDep

router = APIRouter(prefix="/organization-admin", tags=["Account deletion and ownership transfer"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _limits(settings: Settings) -> OtpLimits:
    return OtpLimits(
        ttl_seconds=settings.otp_expire_seconds,
        max_attempts=settings.otp_max_attempts,
        resend_cooldown_seconds=settings.otp_resend_cooldown_seconds,
        hourly_limit=settings.otp_hourly_limit,
    )


@router.get("/account/deletion", response_model=DeletionStatusResponse)
async def deletion_status(admin: OrganizationAdminDep, session: SessionDep) -> DeletionStatusResponse:
    status = await GetDeletionStatus(SqlAccountLifecycleRepository(session)).execute()
    await session.commit()  # keeps the expiry of out-of-date transfers
    return DeletionStatusResponse.from_result(status)


@router.post("/account/ownership-transfer", response_model=CodeSentResponse)
async def start_transfer(
    body: StartOwnershipTransferRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    settings = _settings(request)
    use_case = StartOwnershipTransfer(
        SqlAccountLifecycleRepository(session),
        SqlStaffAccountRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        verify_ttl_seconds=settings.account_action_verify_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
    )
    return CodeSentResponse.from_result(await use_case.execute(body.recipient_admin_id, body.current_password))


@router.post("/account/ownership-transfer/{transfer_id}/resend-otp", response_model=CodeSentResponse)
async def resend_transfer_code(
    transfer_id: UUID, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendOwnershipTransferCode(
        SqlAccountLifecycleRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return CodeSentResponse.from_result(await use_case.execute(transfer_id))


@router.post("/account/ownership-transfer/{transfer_id}/verify", response_model=TransferVerifiedResponse)
async def verify_transfer(
    transfer_id: UUID, body: OtpRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> TransferVerifiedResponse:
    use_case = VerifyOwnershipTransfer(
        SqlAccountLifecycleRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        accept_ttl_seconds=_settings(request).ownership_transfer_accept_hours * 3600,
    )
    return TransferVerifiedResponse(transfer_id=transfer_id, accept_by=await use_case.execute(transfer_id, body.otp))


@router.delete("/account/ownership-transfer/{transfer_id}", status_code=204)
async def cancel_transfer(transfer_id: UUID, admin: OrganizationAdminDep, session: SessionDep) -> None:
    await CancelOwnershipTransfer(SqlAccountLifecycleRepository(session), SqlTransaction(session)).execute(transfer_id)


@router.get("/ownership-transfers/incoming", response_model=list[IncomingTransferResponse])
async def incoming_transfers(admin: OrganizationAdminDep, session: SessionDep) -> list[IncomingTransferResponse]:
    items = await ListIncomingTransfers(SqlAccountLifecycleRepository(session), SqlTransaction(session)).execute()
    return [
        IncomingTransferResponse(
            transfer_id=i.transfer_id, from_name=i.from_name, created_at=i.created_at, expires_at=i.expires_at
        )
        for i in items
    ]


@router.get("/ownership-transfers/history", response_model=list[OwnershipHistoryResponse])
async def ownership_history(admin: OrganizationAdminDep, session: SessionDep) -> list[OwnershipHistoryResponse]:
    entries = await ListOwnershipHistory(SqlAccountLifecycleRepository(session), SqlTransaction(session)).execute(admin)
    return [
        OwnershipHistoryResponse(
            transfer_id=e.transfer_id,
            previous_owner_name=e.previous_owner_name,
            previous_owner_username=e.previous_owner_username,
            previous_owner_email=e.previous_owner_email,
            status="ACCEPTED",
            summary=f"{e.previous_owner_name} offered ownership to you, and you accepted it.",
            offered_at=e.offered_at,
            accepted_at=e.accepted_at,
        )
        for e in entries
    ]


async def _decide(transfer_id: UUID, accept: bool, session) -> TransferDecisionResponse:  # type: ignore[no-untyped-def]
    outcome = await DecideOwnershipTransfer(SqlAccountLifecycleRepository(session), SqlTransaction(session)).execute(
        transfer_id, accept=accept
    )
    accepted = outcome is FlowOutcome.ACCEPTED
    return TransferDecisionResponse(
        transfer_id=transfer_id, status="ACCEPTED" if accepted else "REJECTED", you_are_main_admin=accepted
    )


@router.post("/ownership-transfers/{transfer_id}/accept", response_model=TransferDecisionResponse)
async def accept_transfer(
    transfer_id: UUID, admin: OrganizationAdminDep, session: SessionDep
) -> TransferDecisionResponse:
    return await _decide(transfer_id, True, session)


@router.post("/ownership-transfers/{transfer_id}/reject", response_model=TransferDecisionResponse)
async def reject_transfer(
    transfer_id: UUID, admin: OrganizationAdminDep, session: SessionDep
) -> TransferDecisionResponse:
    return await _decide(transfer_id, False, session)


@router.post("/account/deletion", response_model=DeletionStartedResponse)
async def start_deletion(
    body: StartAccountDeletionRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> DeletionStartedResponse:
    settings = _settings(request)
    use_case = StartAccountDeletion(
        SqlAccountLifecycleRepository(session),
        SqlStaffAccountRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.account_action_verify_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
    )
    sent = await use_case.execute(admin, body.admin_id, body.current_password)
    return DeletionStartedResponse.build(sent, [category for category, _ in EXPORT_NOT_INCLUDED])


@router.get("/account/deletion/{request_id}/export")
async def export_campus(request_id: UUID, admin: OrganizationAdminDep, session: SessionDep) -> JSONResponse:
    """A one-off download, generated in memory: no copy is kept on the server."""
    data = await ExportCampusData(SqlAccountLifecycleRepository(session), SqlTransaction(session)).execute(request_id)
    return JSONResponse(
        data,
        headers={
            "Content-Disposition": 'attachment; filename="campus-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/account/deletion/{request_id}/resend-otp", response_model=CodeSentResponse)
async def resend_deletion_code(
    request_id: UUID, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> CodeSentResponse:
    use_case = ResendAccountDeletionCode(
        SqlAccountLifecycleRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(_settings(request)),
    )
    return CodeSentResponse.from_result(await use_case.execute(request_id))


@router.post("/account/deletion/{request_id}/verify", response_model=AccountDeletedResponse)
async def complete_deletion(
    request_id: UUID, body: OtpRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> AccountDeletedResponse:
    done = await CompleteAccountDeletion(
        SqlAccountLifecycleRepository(session),
        request.app.state.otp_codec,
        SqlTransaction(session),
        request.app.state.media_storage,
    ).execute(admin, request_id, body.otp)
    return AccountDeletedResponse(campus_deleted=done.campus_deleted, organization_deleted=done.organization_deleted)
