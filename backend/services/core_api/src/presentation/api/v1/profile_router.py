"""The signed-in Admin's own profile (Org Admin page 24; decision D-024; migration 0029). Admins only.

GET    /organization-admin/profile                       Admin ID, name, username, email, phone, role,
                                                         organization and campus (read-only parts marked)
POST   /organization-admin/profile/changes               current password + changes (name, username, email,
                                                         phone, password); emails code(s)
POST   /organization-admin/profile/changes/{id}/resend-otp
POST   /organization-admin/profile/changes/{id}/verify   code(s); only now are the changes applied
DELETE /organization-admin/profile/changes/{id}          cancel; nothing changes
POST   /organization-admin/profile/regenerate-id         current password: a new Admin ID (no code)
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Uuid, bindparam, text

from src.application.dtos.supervisor_dto import (
    ProfileChangeStartedResponse,
    VerifyProfileChangeRequest,
)
from src.application.use_cases.supervisor_profile_use_cases import (
    CancelProfileChange,
    GetSupervisorProfile,
    ProfileChangeForm,
    ResendProfileChangeCodes,
    StartProfileChange,
    VerifyProfileChange,
)
from src.config.settings import Settings
from src.domain.entities.user import User
from src.infrastructure.database.connection import bind_actor
from src.infrastructure.database.repositories.supervisor_profile_repository import SqlAdminProfileRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.supervisor_profile_router import _limits, _started
from src.presentation.api.v1.user_records_router import NewIdResponse, PasswordRequest, records
from src.presentation.dependencies import OrganizationAdminDep, SessionDep

router = APIRouter(prefix="/organization-admin/profile", tags=["Admin profile"])

_CAMPUS = text("SELECT name FROM campuses WHERE organization_id = :org AND id = :campus").bindparams(
    bindparam("org", type_=Uuid), bindparam("campus", type_=Uuid)
)


class AdminProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    admin_id: str
    full_name: str
    username: str | None
    email: str
    phone_number: str | None
    role: Literal["ADMIN"] = "ADMIN"
    is_main_admin: bool
    organization_name: str
    organization_id: str
    campus_name: str
    editable_fields: list[str] = ["full_name", "username", "email", "phone_number", "password"]
    read_only_fields: list[str] = ["admin_id", "role", "organization_name", "organization_id", "campus_name"]


class AdminProfileChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    phone_number: str | None = Field(default=None, max_length=40)
    new_password: str | None = Field(default=None, max_length=128)
    confirm_new_password: str | None = Field(default=None, max_length=128)


class AdminProfileChangedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["SAVED"] = "SAVED"
    profile: AdminProfileResponse
    signed_out: bool = Field(description="True if the password changed: every session must sign in again")


async def _read(admin: User, session) -> AdminProfileResponse:  # type: ignore[no-untyped-def]
    profile = await GetSupervisorProfile(SqlAdminProfileRepository(session)).execute(admin)
    campus = (await session.execute(_CAMPUS, {"org": admin.organization_id, "campus": admin.campus_id})).scalar_one()
    return AdminProfileResponse(
        admin_id=profile.supervisor_id,
        full_name=profile.full_name,
        username=profile.username,
        email=profile.email,
        phone_number=profile.phone,
        is_main_admin=admin.is_first_admin,
        organization_name=profile.organization_name,
        organization_id=profile.organization_id,
        campus_name=campus,
    )


@router.get("", response_model=AdminProfileResponse)
async def read_profile(admin: OrganizationAdminDep, session: SessionDep) -> AdminProfileResponse:
    return await _read(admin, session)


@router.post("/changes", response_model=ProfileChangeStartedResponse)
async def save_changes(
    body: AdminProfileChangeRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    settings: Settings = request.app.state.settings
    use_case = StartProfileChange(
        SqlAdminProfileRepository(session),
        Argon2PasswordHasher(),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(settings),
        ttl_seconds=settings.supervisor_profile_change_expire_minutes * 60,
        max_failed_attempts=settings.org_login_max_attempts,
        lockout_seconds=settings.org_login_lockout_minutes * 60,
        label="Admin",
    )
    form = ProfileChangeForm(
        current_password=body.current_password,
        full_name=body.full_name,
        username=body.username,
        email=body.email,
        new_password=body.new_password,
        confirm_new_password=body.confirm_new_password,
        phone=body.phone_number,
    )
    return _started(await use_case.execute(admin, form))


@router.post("/changes/{change_id}/resend-otp", response_model=ProfileChangeStartedResponse)
async def resend_codes(
    change_id: UUID, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> ProfileChangeStartedResponse:
    use_case = ResendProfileChangeCodes(
        SqlAdminProfileRepository(session),
        request.app.state.otp_codec,
        request.app.state.email_sender,
        SqlTransaction(session),
        limits=_limits(request.app.state.settings),
        label="Admin",
    )
    return _started(await use_case.execute(change_id))


@router.post("/changes/{change_id}/verify", response_model=AdminProfileChangedResponse)
async def verify_changes(
    change_id: UUID,
    body: VerifyProfileChangeRequest,
    request: Request,
    admin: OrganizationAdminDep,
    session: SessionDep,
) -> AdminProfileChangedResponse:
    repository = SqlAdminProfileRepository(session)
    signed_out = await VerifyProfileChange(repository, request.app.state.otp_codec, SqlTransaction(session)).execute(
        change_id, body.otp, body.new_email_otp
    )
    await bind_actor(session, admin.id)  # the commit ended the Row-Level Security binding
    return AdminProfileChangedResponse(profile=await _read(admin, session), signed_out=signed_out)


@router.delete("/changes/{change_id}", status_code=204)
async def cancel_changes(change_id: UUID, admin: OrganizationAdminDep, session: SessionDep) -> None:
    await CancelProfileChange(SqlAdminProfileRepository(session), SqlTransaction(session)).execute(change_id)


@router.post("/regenerate-id", response_model=NewIdResponse)
async def regenerate_id(
    body: PasswordRequest, request: Request, admin: OrganizationAdminDep, session: SessionDep
) -> NewIdResponse:
    new_id = await records(admin, session, request).regenerate_own_admin_id(admin, body.current_password)
    return NewIdResponse(display_id=new_id)
