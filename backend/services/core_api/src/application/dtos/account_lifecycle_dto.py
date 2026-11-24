"""Bodies for Main Admin ownership transfer and Admin account deletion. Responses never contain
passwords, codes, internal keys, or other Admins' private details (only names and Admin IDs of
transfer candidates)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.supervisor_dto import CodeSentResponse
from src.application.use_cases.account_lifecycle_use_cases import DeletionStatus
from src.application.use_cases.otp_flow import CodeSent


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OwnershipCandidateResponse(_Frozen):
    admin_id: str
    full_name: str


class OwnershipTransferStatus(_Frozen):
    transfer_id: UUID
    status: str = Field(description="AWAITING_VERIFICATION, PENDING_ACCEPTANCE, ACCEPTED, REJECTED, CANCELLED, EXPIRED")
    to_name: str
    expires_at: datetime


class DeletionStatusResponse(_Frozen):
    is_main_admin: bool
    can_delete: bool
    blocked_by: str | None = Field(
        description="OWNERSHIP_TRANSFER_REQUIRED, OWNERSHIP_TRANSFER_PENDING or OWNERSHIP_SUCCESSOR_REQUIRED"
    )
    campus_will_be_deleted: bool
    organization_will_be_deleted: bool
    transfer_candidates: list[OwnershipCandidateResponse]
    latest_transfer: OwnershipTransferStatus | None

    @classmethod
    def from_result(cls, status: DeletionStatus) -> "DeletionStatusResponse":
        o = status.overview
        latest = None
        if o.transfer_id is not None and o.transfer_status and o.transfer_to_name and o.transfer_expires_at:
            latest = OwnershipTransferStatus(
                transfer_id=o.transfer_id,
                status=o.transfer_status,
                to_name=o.transfer_to_name,
                expires_at=o.transfer_expires_at,
            )
        return cls(
            is_main_admin=o.is_main_admin,
            can_delete=o.blocker is None,
            blocked_by=o.blocker.value if o.blocker else None,
            campus_will_be_deleted=o.campus_will_be_deleted,
            organization_will_be_deleted=o.organization_will_be_deleted,
            transfer_candidates=[
                OwnershipCandidateResponse(admin_id=c.admin_id, full_name=c.full_name) for c in status.candidates
            ],
            latest_transfer=latest,
        )


class StartOwnershipTransferRequest(_Strict):
    recipient_admin_id: str = Field(max_length=40)
    current_password: str = Field(max_length=128)


class TransferVerifiedResponse(_Frozen):
    transfer_id: UUID
    status: Literal["PENDING_ACCEPTANCE"] = "PENDING_ACCEPTANCE"
    accept_by: datetime


class IncomingTransferResponse(_Frozen):
    transfer_id: UUID
    from_name: str
    created_at: datetime
    expires_at: datetime


class OwnershipHistoryResponse(_Frozen):
    transfer_id: UUID
    previous_owner_name: str
    previous_owner_username: str | None
    previous_owner_email: str
    status: Literal["ACCEPTED"]
    summary: str = Field(description="Who offered ownership to whom")
    offered_at: datetime
    accepted_at: datetime


class TransferDecisionResponse(_Frozen):
    transfer_id: UUID
    status: Literal["ACCEPTED", "REJECTED"]
    you_are_main_admin: bool


class StartAccountDeletionRequest(_Strict):
    admin_id: str = Field(max_length=40)
    current_password: str = Field(max_length=128)


class DeletionExportOption(_Frozen):
    optional: Literal[True] = True
    download_url: str = Field(description="GET while the request is open; skipping it changes nothing")
    not_included: list[str] = Field(description="Data the application does not hold yet")


class DeletionStartedResponse(CodeSentResponse):
    export: DeletionExportOption

    @classmethod
    def build(cls, sent: CodeSent, not_included: list[str]) -> "DeletionStartedResponse":
        base = CodeSentResponse.from_result(sent).model_dump()
        url = f"/api/v1/organization-admin/account/deletion/{sent.subject_id}/export"
        return cls(**base, export=DeletionExportOption(download_url=url, not_included=not_included))


class AccountDeletedResponse(_Frozen):
    status: Literal["DELETED"] = "DELETED"
    campus_deleted: bool
    organization_deleted: bool
