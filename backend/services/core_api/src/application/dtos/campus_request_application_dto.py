"""Request and response bodies for "Already Registered Organization?" campus requests.

Requests refuse unknown fields. Responses never contain a password, hash, code or token: a
verified request waits for the Main Admin, and the applicant signs in only after approval.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.otp_flow import CodeSent
from src.domain.repositories.i_campus_request_application_repo import (
    VerifiedApplication,
)


class CampusRequestApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(max_length=200, description="The organization's exact registered name")
    organization_id: str = Field(max_length=40, description="The Organization ID, e.g. ABCDEF12345678901")
    campus_name: str = Field(max_length=200, description="The proposed campus, branch or site name")
    admin_full_name: str = Field(max_length=200)
    username: str = Field(max_length=64)
    email: str = Field(max_length=320, description="Official email; a code is sent here")
    phone_number: str = Field(max_length=40)
    password: str = Field(max_length=128)


class VerifyCampusRequestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    otp: str = Field(pattern=r"^[0-9]{6}$", description="The 6-digit code from the email")


class CampusRequestCodeSentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: UUID
    status: str = "PENDING_EMAIL_VERIFICATION"
    email: str = Field(description="Masked, e.g. ay***@example.com")
    delivery: str = Field(description='"EMAIL" or "DEV_OUTBOX" (development only)')
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    application_expires_at: datetime

    @classmethod
    def from_result(cls, sent: CodeSent) -> "CampusRequestCodeSentResponse":
        return cls(
            application_id=sent.subject_id,
            email=sent.masked_email,
            delivery=sent.delivery_channel,
            otp_expires_in_seconds=sent.code_expires_in_seconds,
            resend_available_in_seconds=sent.resend_available_in_seconds,
            application_expires_at=sent.expires_at,
        )


class CampusRequestVerifiedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(default="PENDING_APPROVAL", description="Email verified; waiting for the Main Admin")
    organization_name: str
    campus_name: str
    email: str
    email_verified_at: datetime
    decision_deadline: datetime = Field(description="Expires if the Main Admin has not decided by then (72 hours)")

    @classmethod
    def from_entity(cls, done: VerifiedApplication) -> "CampusRequestVerifiedResponse":
        return cls(
            organization_name=done.organization_name,
            campus_name=done.campus_name,
            email=done.email,
            email_verified_at=done.verified_at,
            decision_deadline=done.decision_deadline,
        )
