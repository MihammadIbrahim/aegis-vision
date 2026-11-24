"""Staff Management bodies (web Phase 8; decision D-017). Passwords are accepted, never returned."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.location_dto import LocationPathResponse
from src.domain.entities.staff import StaffMember


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StaffMemberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(description="Internal user ID (used in /staff URLs)")
    display_id: str = Field(description="The Guard ID or Supervisor ID shown to users")
    role: Literal["GUARD", "SUPERVISOR"]
    status: Literal["ACTIVE", "LEFT"] = Field(description="LEFT = deactivated: cannot sign in")
    full_name: str
    username: str | None
    email: str
    phone_number: str | None
    created_at: datetime
    category: Literal["STANDBY", "STATIONED"] | None = Field(
        description="Guards only: STANDBY without a location, STATIONED with one"
    )
    location: LocationPathResponse | None

    @classmethod
    def of(cls, m: StaffMember) -> "StaffMemberResponse":
        return cls(
            id=m.user_id,
            display_id=m.display_id,
            role=m.role.value,  # type: ignore[arg-type]
            status=m.status.value,  # type: ignore[arg-type]
            full_name=m.full_name,
            username=m.username,
            email=m.email,
            phone_number=m.phone,
            created_at=m.created_at,
            category=None if m.category is None else m.category.value,  # type: ignore[arg-type]
            location=None if m.location is None else LocationPathResponse.of(m.location),
        )


class UpdateGuardRequest(_Strict):
    full_name: str = Field(max_length=200)
    username: str = Field(max_length=64)
    phone_number: str = Field(max_length=32)
    current_password: str = Field(max_length=256, description="The acting Admin's or Supervisor's own password")


class SetGuardPasswordRequest(_Strict):
    new_password: str = Field(max_length=256)
    current_password: str = Field(max_length=256, description="The acting Admin's or Supervisor's own password")


class StaffStatusRequest(_Strict):
    current_password: str = Field(max_length=256, description="The acting Admin's own password")
