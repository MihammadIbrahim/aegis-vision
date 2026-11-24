"""Request and response bodies for Organization Admin sign-in and the Admin dashboard.

Responses never contain a password, password hash, OTP or internal database key.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.auth_use_cases import LoginCodeSent
from src.application.use_cases.organization_admin_dashboard_use_cases import (
    MODULES_NOT_AVAILABLE_YET,
)
from src.domain.repositories.i_organization_repo import AdminDashboardFacts


class OrganizationAdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(max_length=200, description="Exactly as registered, including capitalization")
    organization_id: str = Field(max_length=40, description="e.g. ABCDEF12345678901")
    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class VerifyLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    otp: str = Field(pattern=r"^[0-9]{6}$", description="The 6-digit code from the email")


class LoginCodeSentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    login_id: UUID
    status: Literal["OTP_REQUIRED"] = "OTP_REQUIRED"
    email: str = Field(description="Masked, e.g. ay***@example.com")
    delivery: str = Field(description='"EMAIL" (accepted by the email provider) or "DEV_OUTBOX" (development only)')
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    login_expires_at: datetime

    @classmethod
    def from_result(cls, sent: LoginCodeSent) -> "LoginCodeSentResponse":
        return cls(
            login_id=sent.login_id,
            email=sent.masked_email,
            delivery=sent.delivery_channel,
            otp_expires_in_seconds=sent.code_expires_in_seconds,
            resend_available_in_seconds=sent.resend_available_in_seconds,
            login_expires_at=sent.login_expires_at,
        )


class AdminTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds


class DashboardOrganization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: str
    name: str
    status: str
    official_email: str | None
    official_phone_number: str | None


class DashboardCampus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: str


class DashboardAdmin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    full_name: str
    username: str | None
    email: str
    phone_number: str | None
    role: Literal["ADMIN"] = "ADMIN"
    is_main_admin: bool


class DashboardSummary(BaseModel):
    """Counts for the Admin's own campus, from records that exist today."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_admins: int
    active_supervisors: int
    active_guards: int
    cameras: int
    pending_campus_requests: int | None = Field(description="Main Admin only; null for other Admins")


class AdminDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization: DashboardOrganization
    campus: DashboardCampus
    admin: DashboardAdmin
    summary: DashboardSummary
    not_available_yet: list[str] = Field(description="Modules not built yet; no figures are shown for them")

    @classmethod
    def from_facts(cls, facts: AdminDashboardFacts) -> "AdminDashboardResponse":
        return cls(
            organization=DashboardOrganization(
                organization_id=facts.organization_code,
                name=facts.organization_name,
                status=facts.organization_status,
                official_email=facts.official_email,
                official_phone_number=facts.official_phone,
            ),
            campus=DashboardCampus(name=facts.campus_name, status=facts.campus_status),
            admin=DashboardAdmin(
                user_id=facts.admin_display_id,
                full_name=facts.admin_full_name,
                username=facts.admin_username,
                email=facts.admin_email,
                phone_number=facts.admin_phone,
                is_main_admin=facts.is_main_admin,
            ),
            summary=DashboardSummary(
                active_admins=facts.active_admins,
                active_supervisors=facts.active_supervisors,
                active_guards=facts.active_guards,
                cameras=facts.cameras,
                pending_campus_requests=facts.pending_campus_requests,
            ),
            not_available_yet=list(MODULES_NOT_AVAILABLE_YET),
        )
