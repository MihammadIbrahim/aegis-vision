"""Bodies for Guard creation, Guard sign-in, the Guard dashboard and the Guard profile.
Responses never contain a password, hash, code or internal key."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.organization_admin_dashboard_use_cases import (
    MODULES_NOT_AVAILABLE_YET,
)
from src.domain.repositories.i_organization_repo import AdminDashboardFacts
from src.domain.repositories.i_staff_account_repo import (
    CreatedSupervisor,
    SupervisorProfile,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# Decision D-018: the Guard's operational mode and the dashboard it opens.
OperationalMode = Literal["STANDBY", "STATIONED"]
GuardDashboardKind = Literal["STANDBY_EMERGENCY", "STATIONED_MONITORING"]


def dashboard_for(mode: str) -> GuardDashboardKind:
    return "STATIONED_MONITORING" if mode == "STATIONED" else "STANDBY_EMERGENCY"


class GuardCreatedResponse(_Frozen):
    guard_id: str = Field(description="6 capital letters + 21 digits, e.g. ABCDEF123456789012345678901")
    full_name: str
    username: str
    email: str
    phone_number: str
    role: Literal["GUARD"] = "GUARD"
    organization_id: str
    campus_name: str
    created_at: datetime

    @classmethod
    def from_entity(cls, created: CreatedSupervisor) -> "GuardCreatedResponse":
        # The creation result type is shared with Supervisor creation; its ID field holds the Guard ID.
        return cls(
            guard_id=created.supervisor_id,
            full_name=created.full_name,
            username=created.username,
            email=created.email,
            phone_number=created.phone,
            organization_id=created.organization_code,
            campus_name=created.campus_name,
            created_at=created.created_at,
        )


class GuardLoginRequest(_Strict):
    operational_mode: OperationalMode = Field(
        description="STANDBY (no post) or STATIONED (Stationed / Standalone, a post); must match the assignment"
    )
    guard_id: str = Field(max_length=40)
    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class GuardDashboardResponse(_Frozen):
    organization: dict[str, str]
    campus: dict[str, str]
    guard: dict[str, str | None]
    not_available_yet: list[str]
    operational_mode: OperationalMode = Field(description="From the Guard's current assignment")
    dashboard: GuardDashboardKind

    @classmethod
    def from_facts(cls, facts: AdminDashboardFacts, mode: str) -> "GuardDashboardResponse":
        return cls(
            organization={"organization_id": facts.organization_code, "name": facts.organization_name},
            campus={"name": facts.campus_name},
            guard={
                "guard_id": facts.admin_display_id,
                "full_name": facts.admin_full_name,
                "username": facts.admin_username,
                "email": facts.admin_email,
                "phone_number": facts.admin_phone,
                "role": "GUARD",
            },
            not_available_yet=list(MODULES_NOT_AVAILABLE_YET),
            operational_mode="STATIONED" if mode == "STATIONED" else "STANDBY",
            dashboard=dashboard_for(mode),
        )


class GuardProfileResponse(_Frozen):
    organization_name: str
    organization_id: str
    guard_id: str
    full_name: str
    username: str | None
    email: str
    phone_number: str | None
    editable_fields: list[str] = ["email", "phone_number", "password"]
    read_only_fields: list[str] = ["full_name", "username", "guard_id", "organization_name", "organization_id"]
    # Decision D-025 (Guard app screen 10): read-only context, from the account and its current assignment.
    campus_name: str | None = None
    status: Literal["ACTIVE", "LEFT"] | None = None
    operational_mode: OperationalMode | None = None

    @classmethod
    def from_entity(
        cls,
        profile: SupervisorProfile,
        *,
        campus_name: str | None = None,
        status: str | None = None,
        operational_mode: str | None = None,
    ) -> "GuardProfileResponse":
        return cls(
            organization_name=profile.organization_name,
            organization_id=profile.organization_id,
            guard_id=profile.supervisor_id,
            full_name=profile.full_name,
            username=profile.username,
            email=profile.email,
            phone_number=profile.phone,
            campus_name=campus_name,
            status=status,  # type: ignore[arg-type]
            operational_mode=operational_mode,  # type: ignore[arg-type]
        )


class GuardProfileChangeRequest(_Strict):
    """Only email, phone number and password; anything else is refused (422)."""

    current_password: str = Field(max_length=128)
    email: str | None = Field(default=None, max_length=320)
    phone_number: str | None = Field(default=None, max_length=40)
    new_password: str | None = Field(default=None, max_length=128)
    confirm_new_password: str | None = Field(default=None, max_length=128)


class GuardProfileChangedResponse(_Frozen):
    status: Literal["SAVED"] = "SAVED"
    profile: GuardProfileResponse
    signed_out: bool = Field(description="True if the password changed: sign in again")


class GuardVerifyLoginRequest(_Strict):
    """The emailed code and, again, the operational mode: checked against the assignment at the moment
    the session is created (it may have changed since the code was sent)."""

    otp: str = Field(pattern=r"^[0-9]{6}$", description="The 6-digit code from the email")
    operational_mode: OperationalMode


class GuardTokenResponse(_Frozen):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
    operational_mode: OperationalMode
    dashboard: GuardDashboardKind = Field(
        description="STANDBY_EMERGENCY (Standby Emergency Dashboard) or STATIONED_MONITORING (Stationed Monitoring)"
    )
