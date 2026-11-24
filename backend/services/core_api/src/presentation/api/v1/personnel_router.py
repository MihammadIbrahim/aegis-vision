"""Organization Personnel Management for the signed-in user's own campus (decision D-016).

Organization Admins (every write) and Supervisors (read-only, basic details); Guards: 403.

GET    /personnel/fields                   the canonical fields (required / optional, per category)
GET    /personnel/categories               categories                       (Admins, Supervisors)
POST   /personnel/categories               add a category                   (Admins)
PUT    /personnel/categories/{id}          change a category                (Admins)
GET    /personnel/custom-fields            custom fields                    (Admins)
POST   /personnel/custom-fields            add a custom field               (Admins)
PUT    /personnel/custom-fields/{id}       change a custom field            (Admins)
GET    /personnel                          directory: ?q= &category_id= &status= &department=
                                           &designation= &area_id= &limit= &offset=
POST   /personnel                          register a person                (Admins)
GET    /personnel/{id}                     Admins: full record; Supervisors: basic details
PUT    /personnel/{id}                     replace the details              (Admins)
POST   /personnel/{id}/deactivate          deactivate (access is revoked)   (Admins)
POST   /personnel/{id}/reactivate          reactivate                       (Admins)
GET    /personnel/{id}/history             audit history                    (Admins)
PUT    /personnel/{id}/photo               upload a JPEG/PNG photo          (Admins)
GET    /personnel/{id}/photo               the photo                        (Admins; Supervisors 403)
DELETE /personnel/{id}/photo               remove the photo                 (Admins)
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, UploadFile, status
from fastapi.responses import Response

from src.application.dtos.personnel_dto import (
    AuditEntryResponse,
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    CustomFieldCreateRequest,
    CustomFieldResponse,
    CustomFieldUpdateRequest,
    DeactivateRequest,
    DirectoryResponse,
    DirectoryRowResponse,
    FieldDefinitionResponse,
    IssueResponse,
    PersonnelBasicResponse,
    PersonnelDetailResponse,
    PersonnelRequest,
    PersonnelWriteResponse,
)
from src.application.use_cases.personnel_use_cases import (
    ChangePersonnelStatus,
    ManagePersonnelConfiguration,
    ManagePersonnelPhoto,
    RegisterPersonnel,
    UpdatePersonnel,
    ViewPersonnel,
)
from src.domain.entities.personnel import PersonnelFilter, PersonnelStatus
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.permission_policy import ensure_personnel_viewer
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.repositories.area_access_repository import (
    SqlAreaAccessRepository,
)
from src.infrastructure.database.repositories.audit_log_repository import (
    SqlOrganizationAuditLog,
)
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.repositories.personnel_repository import (
    SqlPersonnelRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import OrganizationAdminDep, SessionDep, StaffDep

router = APIRouter(prefix="/personnel", tags=["Personnel"])


def _repo(session, actor: User) -> SqlPersonnelRepository:  # type: ignore[no-untyped-def]
    return SqlPersonnelRepository(session, actor.organization_id, actor.campus_id)


def _config(session, actor: User) -> ManagePersonnelConfiguration:  # type: ignore[no-untyped-def]
    return ManagePersonnelConfiguration(
        _repo(session, actor), SqlOrganizationAuditLog(session), SqlTransaction(session)
    )


def _writer_deps(session, actor: User):  # type: ignore[no-untyped-def]
    return (
        _repo(session, actor),
        SqlLocationRepository(session, actor.organization_id, actor.campus_id),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
    )


# --- Fields, categories, custom fields (declared before /{personnel_id}) ------------------------------------


@router.get("/fields", response_model=list[FieldDefinitionResponse])
async def fields(actor: StaffDep) -> list[FieldDefinitionResponse]:
    ensure_personnel_viewer(actor)
    return FieldDefinitionResponse.all()


@router.get("/categories", response_model=list[CategoryResponse])
async def categories(actor: StaffDep, session: SessionDep) -> list[CategoryResponse]:
    return [CategoryResponse.of(c) for c in await _config(session, actor).categories(actor)]


@router.post("/categories", status_code=status.HTTP_201_CREATED, response_model=CategoryResponse)
async def add_category(
    body: CategoryCreateRequest, actor: OrganizationAdminDep, session: SessionDep
) -> CategoryResponse:
    return CategoryResponse.of(await _config(session, actor).create_category(actor, body.code, body.to_details()))


@router.put("/categories/{category_id}", response_model=CategoryResponse)
async def edit_category(
    category_id: UUID, body: CategoryUpdateRequest, actor: OrganizationAdminDep, session: SessionDep
) -> CategoryResponse:
    return CategoryResponse.of(await _config(session, actor).update_category(actor, category_id, body.to_details()))


@router.get("/custom-fields", response_model=list[CustomFieldResponse])
async def custom_fields(actor: OrganizationAdminDep, session: SessionDep) -> list[CustomFieldResponse]:
    return [CustomFieldResponse.of(f) for f in await _config(session, actor).custom_fields(actor)]


@router.post("/custom-fields", status_code=status.HTTP_201_CREATED, response_model=CustomFieldResponse)
async def add_custom_field(
    body: CustomFieldCreateRequest, actor: OrganizationAdminDep, session: SessionDep
) -> CustomFieldResponse:
    created = await _config(session, actor).create_custom_field(actor, body.key, body.field_type, body.to_details())
    return CustomFieldResponse.of(created)


@router.put("/custom-fields/{field_id}", response_model=CustomFieldResponse)
async def edit_custom_field(
    field_id: UUID, body: CustomFieldUpdateRequest, actor: OrganizationAdminDep, session: SessionDep
) -> CustomFieldResponse:
    return CustomFieldResponse.of(await _config(session, actor).update_custom_field(actor, field_id, body.to_details()))


# --- Directory and records --------------------------------------------------------------------------------------


@router.get("", response_model=DirectoryResponse)
async def directory(
    actor: StaffDep,
    session: SessionDep,
    q: Annotated[
        str | None, Query(max_length=200, description="Name, Personnel ID, Institutional ID; email (Admins)")
    ] = None,
    category_id: UUID | None = None,
    status_: Literal["ACTIVE", "INACTIVE"] | None = Query(default=None, alias="status"),
    department: str | None = Query(default=None, max_length=120),
    designation: str | None = Query(default=None, max_length=120),
    area_id: Annotated[UUID | None, Query(description="People with active access to this restricted area")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> DirectoryResponse:
    where = PersonnelFilter(
        q,
        category_id,
        None if status_ is None else PersonnelStatus(status_),
        department,
        designation,
        area_id,
        limit,
        offset,
    )
    items, total = await ViewPersonnel(_repo(session, actor)).directory(actor, where)
    return DirectoryResponse(items=[DirectoryRowResponse.of(i) for i in items], total=total, limit=limit, offset=offset)


def _write_response(result) -> PersonnelWriteResponse:  # type: ignore[no-untyped-def]
    person, warnings = result
    return PersonnelWriteResponse(
        personnel=PersonnelDetailResponse.of(person), warnings=[IssueResponse.of(w) for w in warnings]
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonnelWriteResponse)
async def register(body: PersonnelRequest, actor: OrganizationAdminDep, session: SessionDep) -> PersonnelWriteResponse:
    return _write_response(await RegisterPersonnel(*_writer_deps(session, actor)).execute(actor, body.to_input()))


@router.get("/{personnel_id}", response_model=PersonnelDetailResponse | PersonnelBasicResponse)
async def details(
    personnel_id: UUID, actor: StaffDep, session: SessionDep
) -> PersonnelDetailResponse | PersonnelBasicResponse:
    view = ViewPersonnel(_repo(session, actor))
    if actor.role is UserRole.ADMIN:
        return PersonnelDetailResponse.of(await view.details(actor, personnel_id))
    return PersonnelBasicResponse.of(await view.summary(actor, personnel_id))


@router.put("/{personnel_id}", response_model=PersonnelWriteResponse)
async def edit(
    personnel_id: UUID, body: PersonnelRequest, actor: OrganizationAdminDep, session: SessionDep
) -> PersonnelWriteResponse:
    use_case = UpdatePersonnel(*_writer_deps(session, actor))
    return _write_response(await use_case.execute(actor, personnel_id, body.to_input()))


def _status(session, actor: User) -> ChangePersonnelStatus:  # type: ignore[no-untyped-def]
    return ChangePersonnelStatus(
        _repo(session, actor),
        SqlAreaAccessRepository(session, actor.organization_id, actor.campus_id),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
    )


@router.post("/{personnel_id}/deactivate", response_model=PersonnelDetailResponse)
async def deactivate(
    personnel_id: UUID, actor: OrganizationAdminDep, session: SessionDep, body: DeactivateRequest | None = None
) -> PersonnelDetailResponse:
    person = await _status(session, actor).deactivate(actor, personnel_id, None if body is None else body.reason)
    return PersonnelDetailResponse.of(person)


@router.post("/{personnel_id}/reactivate", response_model=PersonnelDetailResponse)
async def reactivate(personnel_id: UUID, actor: OrganizationAdminDep, session: SessionDep) -> PersonnelDetailResponse:
    return PersonnelDetailResponse.of(await _status(session, actor).reactivate(actor, personnel_id))


@router.get("/{personnel_id}/history", response_model=list[AuditEntryResponse])
async def history(personnel_id: UUID, actor: OrganizationAdminDep, session: SessionDep) -> list[AuditEntryResponse]:
    return [AuditEntryResponse.of(e) for e in await ViewPersonnel(_repo(session, actor)).history(actor, personnel_id)]


def _photo(request: Request, session, actor: User) -> ManagePersonnelPhoto:  # type: ignore[no-untyped-def]
    return ManagePersonnelPhoto(
        _repo(session, actor),
        request.app.state.media_storage,
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        max_bytes=request.app.state.settings.personnel_photo_max_bytes,
    )


@router.put("/{personnel_id}/photo", response_model=PersonnelDetailResponse)
async def upload_photo(
    personnel_id: UUID,
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
) -> PersonnelDetailResponse:
    limit = request.app.state.settings.personnel_photo_max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed(f"The photo must be at most {limit} bytes.", code="PHOTO_TOO_LARGE")
    return PersonnelDetailResponse.of(await _photo(request, session, actor).upload(actor, personnel_id, data))


@router.get("/{personnel_id}/photo")
async def photo(personnel_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    data, content_type = await ViewPersonnel(_repo(session, actor), request.app.state.media_storage).photo(
        actor, personnel_id
    )
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})


@router.delete("/{personnel_id}/photo", status_code=status.HTTP_204_NO_CONTENT)
async def remove_photo(personnel_id: UUID, request: Request, actor: OrganizationAdminDep, session: SessionDep) -> None:
    await _photo(request, session, actor).remove(actor, personnel_id)
