from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.entities.platform_admin import PlatformAdmin


class PlatformLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChangeUsernameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_username: str = Field(min_length=1, max_length=64)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)
    confirm_new_password: str = Field(min_length=1, max_length=1024)


class RecoverCredentialsRequest(BaseModel):
    """Emergency recovery: re-enter the recovery credentials and choose new normal credentials."""

    model_config = ConfigDict(extra="forbid")

    recovery_username: str = Field(min_length=1, max_length=64)
    recovery_password: str = Field(min_length=1, max_length=1024)
    new_username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=1, max_length=1024)
    confirm_new_password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds


class PlatformAdminResponse(BaseModel):
    """What a Super Admin may see about their own account. Never the password hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    username: str
    role: Literal["PLATFORM_SUPER_ADMIN"] = "PLATFORM_SUPER_ADMIN"
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def from_entity(cls, admin: PlatformAdmin) -> "PlatformAdminResponse":
        return cls(id=admin.id, username=admin.username, created_at=admin.created_at, last_login_at=admin.last_login_at)


class DeletedOrganizationsResponse(BaseModel):
    """Names only: no Organization ID, campus, user or contact details are kept."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_names: list[str]
