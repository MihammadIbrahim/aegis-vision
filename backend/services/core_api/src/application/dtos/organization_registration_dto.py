"""Request and response bodies for first Organization Admin registration.

Requests refuse unknown fields, so a client cannot choose its own Organization ID,
campus, role or user ID. Length limits here only bound the request size; the content
rules (and their error codes) live in ``domain/services/registration_policy.py``.
Responses never contain a password, password hash, OTP, internal database key or token.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.organization_registration_use_cases import CodeSent
from src.domain.entities.organization import CompletedRegistration


class OrganizationRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_name: str = Field(max_length=200)
    campus_name: str = Field(max_length=200, description="Name of the organization's first campus (decision D-021)")
    admin_full_name: str = Field(max_length=200)
    username: str = Field(max_length=64)
    email: str = Field(max_length=320)
    phone_number: str = Field(max_length=40)
    password: str = Field(max_length=128)


class VerifyRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    otp: str = Field(pattern=r"^[0-9]{6}$", description="The 6-digit code from the email")


class VerificationCodeSentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registration_id: UUID
    status: str = "PENDING_EMAIL_VERIFICATION"
    email: str = Field(description="Masked, e.g. ay***@example.com")
    delivery: str = Field(description='"EMAIL" (accepted by the email provider) or "DEV_OUTBOX" (development only)')
    otp_expires_in_seconds: int
    resend_available_in_seconds: int
    registration_expires_at: datetime

    @classmethod
    def from_result(cls, sent: CodeSent) -> "VerificationCodeSentResponse":
        return cls(
            registration_id=sent.registration_id,
            email=sent.masked_email,
            delivery=sent.delivery_channel,
            otp_expires_in_seconds=sent.code_expires_in_seconds,
            resend_available_in_seconds=sent.resend_available_in_seconds,
            registration_expires_at=sent.registration_expires_at,
        )


class MainAdminResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(description="6 capital letters + 11 digits, e.g. KQMTZX48271930561")
    full_name: str
    username: str
    email: str
    phone_number: str
    role: str = "ADMIN"
    is_main_admin: bool = True


class RegistrationCompletedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "COMPLETED"
    organization_id: str = Field(description="The permanent Organization ID, e.g. ABCDEF12345678901")
    organization_name: str
    official_email: str
    official_phone_number: str
    campus_name: str
    main_admin: MainAdminResponse
    registered_at: datetime

    @classmethod
    def from_entity(cls, done: CompletedRegistration) -> "RegistrationCompletedResponse":
        return cls(
            organization_id=done.organization_code,
            organization_name=done.organization_name,
            official_email=done.official_email,
            official_phone_number=done.official_phone,
            campus_name=done.campus_name,
            main_admin=MainAdminResponse(
                user_id=done.admin_display_id,
                full_name=done.admin_full_name,
                username=done.admin_username,
                email=done.admin_email,
                phone_number=done.admin_phone,
            ),
            registered_at=done.completed_at,
        )
