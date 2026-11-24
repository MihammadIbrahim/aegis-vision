"""Bodies for Supervisor creation, password reset, the session check and the Supervisor
dashboard. Responses never contain a password, hash, code, token secret or internal key."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.application.use_cases.organization_admin_dashboard_use_cases import (
    MODULES_NOT_AVAILABLE_YET,
)
from src.application.use_cases.otp_flow import CodeSent
from src.application.use_cases.password_reset_use_cases import ResetStarted
from src.domain.repositories.i_organization_repo import AdminDashboardFacts
from src.domain.repositories.i_staff_account_repo import CreatedSupervisor


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OtpRequest(_Strict):
    otp: str = Field(pattern=r"^[0-9]{6}$", description="The 6-digit code from the email")


# --- Supervisor creation -------------------------------------------------------------------


class SupervisorAuthorizationRequest(_Strict):
    current_password: str = Field(max_length=128)


class CodeSentResponse(_Frozen):
    id: UUID
    status: Literal["OTP_REQUIRED"] = "OTP_REQUIRED"
    email: str = Field(description="Masked, e.g. ay***@example.com")
    delivery: str
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime

    @classmethod
    def from_result(cls, sent: CodeSent) -> "CodeSentResponse":
        return cls(
            id=sent.subject_id,
            email=sent.masked_email,
            delivery=sent.delivery_channel,
            otp_expires_in_seconds=sent.code_expires_in_seconds,
            resend_available_in_seconds=sent.resend_available_in_seconds,
            expires_at=sent.expires_at,
        )


class AuthorizationVerifiedResponse(_Frozen):
    authorization_id: UUID
    status: Literal["VERIFIED"] = "VERIFIED"
    form_expires_at: datetime = Field(description="Submit the Supervisor form before this time; usable once")


class CreateSupervisorRequest(_Strict):
    authorization_id: UUID
    full_name: str = Field(max_length=200)
    username: str = Field(max_length=64)
    email: str = Field(max_length=320)
    phone_number: str = Field(max_length=40)
    password: str = Field(max_length=128)


class SupervisorCreatedResponse(_Frozen):
    supervisor_id: str = Field(description="6 capital letters + 21 digits, e.g. ABCDEF123456789012345678901")
    full_name: str
    username: str
    email: str
    phone_number: str
    role: Literal["SUPERVISOR"] = "SUPERVISOR"
    organization_id: str
    campus_name: str
    created_at: datetime

    @classmethod
    def from_entity(cls, supervisor: CreatedSupervisor) -> "SupervisorCreatedResponse":
        return cls(
            supervisor_id=supervisor.supervisor_id,
            full_name=supervisor.full_name,
            username=supervisor.username,
            email=supervisor.email,
            phone_number=supervisor.phone,
            organization_id=supervisor.organization_code,
            campus_name=supervisor.campus_name,
            created_at=supervisor.created_at,
        )


# --- Password reset --------------------------------------------------------------------------


class PasswordResetRequest(_Strict):
    """Organization ID + email (Admins and Supervisors), Supervisor ID + email (Supervisors) or
    Guard ID + email (Guards)."""

    organization_id: str | None = Field(default=None, max_length=40)
    supervisor_id: str | None = Field(default=None, max_length=40)
    guard_id: str | None = Field(default=None, max_length=40)
    email: str = Field(max_length=320)
    operational_mode: Literal["STANDBY", "STATIONED"] | None = Field(
        default=None, description="Required with guard_id, not allowed otherwise (decision D-018)"
    )

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> "PasswordResetRequest":
        if sum(value is not None for value in (self.organization_id, self.supervisor_id, self.guard_id)) != 1:
            raise ValueError("send exactly one of organization_id, supervisor_id or guard_id")
        if (self.guard_id is None) != (self.operational_mode is None):
            raise ValueError("operational_mode is required with guard_id and not allowed otherwise")
        return self


class PasswordResetStartedResponse(_Frozen):
    reset_id: UUID
    status: Literal["OTP_REQUIRED"] = "OTP_REQUIRED"
    message: str = (
        "If an Admin or Supervisor account matches these details, a verification code is emailed to its "
        "registered address. If no code arrives, request a new one."
    )
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime

    @classmethod
    def from_result(cls, started: ResetStarted) -> "PasswordResetStartedResponse":
        return cls(
            reset_id=started.request_id,
            otp_expires_in_seconds=started.code_expires_in_seconds,
            resend_available_in_seconds=started.resend_available_in_seconds,
            expires_at=started.expires_at,
        )


class ResetTokenResponse(_Frozen):
    reset_token: str = Field(description="Send it with the new password; usable once")
    expires_in: int


class CompletePasswordResetRequest(_Strict):
    reset_token: str = Field(max_length=200)
    new_password: str = Field(max_length=128)
    confirm_new_password: str = Field(max_length=128)


class PasswordChangedResponse(_Frozen):
    status: Literal["PASSWORD_CHANGED"] = "PASSWORD_CHANGED"
    message: str = "Your password has been changed. Every earlier session has been signed out. Please sign in again."


# --- Session and Supervisor dashboard --------------------------------------------------------


class SessionResponse(_Frozen):
    user_id: str
    account_id: UUID = Field(description="The account's internal ID (used by filters such as ?handled_by=)")
    full_name: str
    role: str
    dashboard: str | None = Field(description="Where the client should go after sign-in")
    is_main_admin: bool = Field(default=False, description="The organization's Main Admin (campus approvals)")


class SupervisorDashboardResponse(_Frozen):
    organization: dict[str, str | None]
    campus: dict[str, str]
    supervisor: dict[str, str | None]
    summary: dict[str, int]
    not_available_yet: list[str]

    @classmethod
    def from_facts(cls, facts: AdminDashboardFacts) -> "SupervisorDashboardResponse":
        return cls(
            organization={
                "organization_id": facts.organization_code,
                "name": facts.organization_name,
                "status": facts.organization_status,
            },
            campus={"name": facts.campus_name, "status": facts.campus_status},
            supervisor={
                "user_id": facts.admin_display_id,
                "full_name": facts.admin_full_name,
                "username": facts.admin_username,
                "email": facts.admin_email,
                "phone_number": facts.admin_phone,
                "role": "SUPERVISOR",
            },
            summary={"active_guards": facts.active_guards, "cameras": facts.cameras},
            not_available_yet=list(MODULES_NOT_AVAILABLE_YET),
        )


# --- Supervisor profile ------------------------------------------------------------------------


class SupervisorProfileResponse(_Frozen):
    organization_name: str
    organization_id: str
    supervisor_id: str
    full_name: str
    username: str | None
    email: str
    phone_number: str | None
    editable_fields: list[str] = ["full_name", "username", "email", "password"]
    read_only_fields: list[str] = ["organization_name", "organization_id", "supervisor_id"]


class ProfileChangeRequest(_Strict):
    """Only the editable fields; sending organization_name, organization_id or supervisor_id is
    refused (422). Omitted fields stay as they are."""

    current_password: str = Field(max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    new_password: str | None = Field(default=None, max_length=128)
    confirm_new_password: str | None = Field(default=None, max_length=128)


class ProfileChangeStartedResponse(_Frozen):
    change_id: UUID
    status: Literal["OTP_REQUIRED"] = "OTP_REQUIRED"
    email: str = Field(description="Masked registered email; it received a code")
    new_email: str | None = Field(description="Masked new email, if changing it; it received a second code")
    delivery: str
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    expires_at: datetime


class VerifyProfileChangeRequest(_Strict):
    otp: str = Field(pattern=r"^[0-9]{6}$", description="Code sent to the registered email")
    new_email_otp: str | None = Field(default=None, pattern=r"^[0-9]{6}$", description="Code sent to the new email")


class ProfileChangedResponse(_Frozen):
    status: Literal["SAVED"] = "SAVED"
    profile: SupervisorProfileResponse
    signed_out: bool = Field(description="True if the password changed: every session must sign in again")
