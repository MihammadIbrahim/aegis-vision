"""User Records & Rejoining (Org Admin page 13), Guard ID regeneration (pages 11) and permanent deletion;
decision D-024. Admins only, except Guard ID regeneration (Admins and Supervisors). Super Admin tokens 403.

GET  /user-records                          ?role=SUPERVISOR|GUARD&status=ACTIVE|LEFT&q=&include_deleted=&limit=&offset=
GET  /user-records/export                   the same, as CSV (audited)
GET  /user-records/lookup?display_id=       a former account, to start rejoining
GET  /user-records/{id}                     details, status history and recent activity
POST /user-records/{id}/rejoin              {full_name, username, phone_number, new_password?, location?,
                                            current_password}
POST /user-records/{id}/permanent-delete    {current_password, confirmation: "DELETE"}
POST /staff/guards/{id}/regenerate-id       {current_password}: a new Guard ID (Admins and Supervisors)
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.location_dto import LocationSelectionRequest
from src.application.use_cases.location_use_cases import LocationResolver
from src.application.use_cases.user_records_use_cases import (
    ActivityEntry,
    RecordDetail,
    RejoinForm,
    StatusChange,
    UserRecord,
    UserRecordFilter,
    UserRecords,
)
from src.domain.entities.location import LocationSelection
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.location_repository import SqlLocationRepository
from src.infrastructure.database.repositories.user_records_repository import SqlUserRecordsRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.exporters.csv_exporter import to_csv
from src.infrastructure.security.password import Argon2PasswordHasher
from src.presentation.api.v1.storage_settings_router import password_confirmation
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/user-records", tags=["User records"])
staff_router = APIRouter(prefix="/staff", tags=["Staff management"])


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserRecordResponse(_Out):
    user_id: UUID
    display_id: str
    role: str
    status: str
    full_name: str
    username: str | None
    email: str | None
    phone_number: str | None
    created_at: datetime
    deleted: bool = Field(description="Personal data permanently deleted")
    deleted_at: datetime | None
    location: str | None
    status_changed_at: datetime | None

    @classmethod
    def of(cls, r: UserRecord) -> "UserRecordResponse":
        return cls(
            user_id=r.user_id,
            display_id=r.display_id,
            role=r.role,
            status=r.status,
            full_name=r.full_name,
            username=r.username,
            email=r.email,
            phone_number=r.phone,
            created_at=r.created_at,
            deleted=r.deleted_at is not None,
            deleted_at=r.deleted_at,
            location=r.location,
            status_changed_at=r.status_changed_at,
        )


class UserRecordPage(_Out):
    items: list[UserRecordResponse]
    total: int
    limit: int
    offset: int


class StatusChangeResponse(_Out):
    from_status: str | None
    to_status: str
    event: str
    changed_at: datetime
    changed_by_name: str | None

    @classmethod
    def of(cls, h: StatusChange) -> "StatusChangeResponse":
        return cls(
            from_status=h.from_status,
            to_status=h.to_status,
            event=h.event,
            changed_at=h.changed_at,
            changed_by_name=h.changed_by_name,
        )


class ActivityResponse(_Out):
    occurred_at: datetime
    action: str
    category: str
    outcome: str
    target_type: str
    by_user: bool

    @classmethod
    def of(cls, a: ActivityEntry) -> "ActivityResponse":
        return cls(
            occurred_at=a.occurred_at,
            action=a.action,
            category=a.category,
            outcome=a.outcome,
            target_type=a.target_type,
            by_user=a.by_user,
        )


class RecordDetailResponse(_Out):
    record: UserRecordResponse
    history: list[StatusChangeResponse]
    activity: list[ActivityResponse]

    @classmethod
    def of(cls, d: RecordDetail) -> "RecordDetailResponse":
        return cls(
            record=UserRecordResponse.of(d.record),
            history=[StatusChangeResponse.of(h) for h in d.history],
            activity=[ActivityResponse.of(a) for a in d.activity],
        )


class RejoinRequest(_In):
    full_name: str = Field(max_length=200)
    username: str | None = Field(default=None, max_length=64)
    phone_number: str | None = Field(default=None, max_length=40)
    new_password: str | None = Field(default=None, max_length=128)
    location: LocationSelectionRequest | None = Field(default=None, description="Guards: none = Standby")
    current_password: str = Field(min_length=1, max_length=256)


class DeleteRequest(_In):
    current_password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(max_length=20, description='Type "DELETE"')


class PasswordRequest(_In):
    current_password: str = Field(min_length=1, max_length=256)


class NewIdResponse(_Out):
    display_id: str


class _Placement:
    """A returning Guard's location, inside the rejoining transaction."""

    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._repo = SqlLocationRepository(session, actor.organization_id, actor.campus_id)
        self._resolver = LocationResolver(self._repo, SqlTransaction(session))

    async def place(self, actor: User, display_id: str, selection: LocationSelection) -> None:
        guard = await self._repo.find_guard(display_id)
        if guard is None:
            raise NotFound("Guard not found.", code="GUARD_NOT_FOUND")
        await self._repo.set_guard_location(guard, await self._resolver.resolve(selection))


def records(actor: User, session: AsyncSession, request: Request) -> UserRecords:
    return UserRecords(
        SqlUserRecordsRepository(session, actor),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        password_confirmation(actor, session, request),
        Argon2PasswordHasher().hash,
        _Placement(session, actor),
    )


def _filter(
    role: str | None, status: str | None, q: str | None, include_deleted: bool, limit: int, offset: int
) -> UserRecordFilter:
    return UserRecordFilter(role, status, q, include_deleted, limit, offset)


Role = Literal["SUPERVISOR", "GUARD"]
Status = Literal["ACTIVE", "LEFT"]


@router.get("", response_model=UserRecordPage)
async def list_records(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    role: Role | None = None,
    status: Status | None = None,
    q: str | None = Query(default=None, max_length=200),
    include_deleted: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserRecordPage:
    items, total = await records(actor, session, request).search(
        actor, _filter(role, status, q, include_deleted, limit, offset)
    )
    return UserRecordPage(items=[UserRecordResponse.of(r) for r in items], total=total, limit=limit, offset=offset)


@router.get("/export")
async def export_records(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    role: Role | None = None,
    status: Status | None = None,
    q: str | None = Query(default=None, max_length=200),
    include_deleted: bool = False,
) -> Response:
    rows = await records(actor, session, request).export(actor, _filter(role, status, q, include_deleted, 5000, 0))
    body = to_csv(
        ["user_id", "role", "full_name", "username", "email", "phone", "status", "location", "created_at",
         "status_changed_at", "personal_data_deleted"],
        ([r.display_id, r.role, r.full_name, r.username, r.email, r.phone, r.status, r.location, r.created_at,
          r.status_changed_at, "yes" if r.deleted_at else "no"] for r in rows),
    )  # fmt: skip
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="user-records.csv"', "Cache-Control": "no-store"},
    )


@router.get("/lookup", response_model=UserRecordResponse)
async def lookup(
    request: Request, actor: StaffDep, session: SessionDep, display_id: str = Query(min_length=4, max_length=40)
) -> UserRecordResponse:
    return UserRecordResponse.of(await records(actor, session, request).lookup(actor, display_id))


@router.get("/{user_id}", response_model=RecordDetailResponse)
async def record_detail(user_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> RecordDetailResponse:
    return RecordDetailResponse.of(await records(actor, session, request).detail(actor, user_id))


@router.post("/{user_id}/rejoin", response_model=UserRecordResponse)
async def rejoin(
    user_id: UUID, body: RejoinRequest, request: Request, actor: StaffDep, session: SessionDep
) -> UserRecordResponse:
    form = RejoinForm(
        full_name=body.full_name,
        username=body.username,
        phone=body.phone_number,
        new_password=body.new_password,
        location=body.location.to_selection() if body.location else None,
        current_password=body.current_password,
    )
    return UserRecordResponse.of(await records(actor, session, request).rejoin(actor, user_id, form))


@router.post("/{user_id}/permanent-delete", status_code=204)
async def permanently_delete(
    user_id: UUID, body: DeleteRequest, request: Request, actor: StaffDep, session: SessionDep
) -> None:
    await records(actor, session, request).permanently_delete(
        actor, user_id, body.current_password, body.confirmation
    )


@staff_router.post("/guards/{user_id}/regenerate-id", response_model=NewIdResponse)
async def regenerate_guard_id(
    user_id: UUID, body: PasswordRequest, request: Request, actor: StaffDep, session: SessionDep
) -> NewIdResponse:
    new_id = await records(actor, session, request).regenerate_guard_id(actor, user_id, body.current_password)
    return NewIdResponse(display_id=new_id)
