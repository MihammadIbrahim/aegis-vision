"""Excel/CSV personnel import for the signed-in Organization Admin's own campus (decision D-016).
Every route is Admin-only; Supervisors, Guards and Super Admin tokens are refused.

GET    /personnel-imports/template.xlsx              the campus's import template
GET    /personnel-imports/mapping-profiles           saved column mappings
DELETE /personnel-imports/mapping-profiles/{id}      delete one
POST   /personnel-imports                            upload (multipart: file, sheet_name?, header_row?)
GET    /personnel-imports                            import history (?limit= &offset=)
GET    /personnel-imports/{id}                       batch, columns with suggested or confirmed fields
PUT    /personnel-imports/{id}/mapping               confirm the mapping and validate every row
GET    /personnel-imports/{id}/issues                validation issues (?severity= &limit= &offset=)
GET    /personnel-imports/{id}/annotated-workbook    the marked copy of the file (.xlsx)
GET    /personnel-imports/{id}/preview               the values that would be imported
POST   /personnel-imports/{id}/confirm               import everything, in one transaction
POST   /personnel-imports/{id}/rollback              deactivate the records this import created
DELETE /personnel-imports/{id}                       discard an import that was not confirmed
"""

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response

from src.application.dtos.personnel_dto import (
    ConfirmImportRequest,
    ImportBatchResponse,
    ImportColumn,
    ImportDetailResponse,
    ImportIssueResponse,
    ImportTarget,
    MappingProfileResponse,
    MappingRequest,
    PreviewRowResponse,
    RollbackImportRequest,
)
from src.application.services.personnel_import.mapping import REQUIRED_TARGETS
from src.application.use_cases.personnel_import_use_cases import (
    ConfirmPersonnelImport,
    DiscardPersonnelImport,
    ImportLimits,
    ImportView,
    RollBackPersonnelImport,
    UploadPersonnelImport,
    ValidatePersonnelImport,
    ViewPersonnelImports,
)
from src.domain.entities.user import User
from src.infrastructure.database.repositories.area_access_repository import (
    SqlAreaAccessRepository,
)
from src.infrastructure.database.repositories.audit_log_repository import (
    SqlOrganizationAuditLog,
)
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.repositories.personnel_import_repository import (
    SqlPersonnelImportRepository,
)
from src.infrastructure.database.repositories.personnel_repository import (
    SqlPersonnelRepository,
)
from src.infrastructure.database.unit_of_work import SqlActorBinding, SqlTransaction
from src.presentation.dependencies import OrganizationAdminDep, SessionDep

router = APIRouter(prefix="/personnel-imports", tags=["Personnel import"])
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _deps(request: Request, session, actor: User, use_case):  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    org, campus = actor.organization_id, actor.campus_id
    return use_case(
        SqlPersonnelImportRepository(session, org, campus),
        SqlPersonnelRepository(session, org, campus),
        SqlLocationRepository(session, org, campus),
        request.app.state.media_storage,
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        ImportLimits(
            settings.personnel_import_max_upload_bytes,
            settings.personnel_import_max_uncompressed_bytes,
            settings.personnel_import_file_retention_hours,
            settings.personnel_import_chunk_rows,
        ),
        access=SqlAreaAccessRepository(session, org, campus),
        binding=SqlActorBinding(session),
    )


def _detail(view: ImportView) -> ImportDetailResponse:
    confirmed = view.batch.mapping is not None
    return ImportDetailResponse(
        batch=ImportBatchResponse.of(view.batch),
        sheets=view.sheets,
        columns=[
            ImportColumn(index=i, header=h, target=s.target, confidence=s.confidence.value, confirmed=confirmed)
            for i, (h, s) in enumerate(zip(view.batch.headers, view.suggestions, strict=True))
        ],
        targets=[
            ImportTarget(target=t, label=label, required=t in REQUIRED_TARGETS) for t, label in view.targets.items()
        ]
        + [ImportTarget(target="IGNORE", label="Do not import this column", required=False)],
    )


# --- Declared before /{batch_id} -----------------------------------------------------------------------------


@router.get("/template.xlsx")
async def template(request: Request, actor: OrganizationAdminDep, session: SessionDep) -> Response:
    data = await _deps(request, session, actor, ViewPersonnelImports).template(actor)
    return Response(
        content=data,
        media_type=_XLSX,
        headers={
            "Content-Disposition": 'attachment; filename="personnel-import-template.xlsx"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/mapping-profiles", response_model=list[MappingProfileResponse])
async def profiles(request: Request, actor: OrganizationAdminDep, session: SessionDep) -> list[MappingProfileResponse]:
    found = await _deps(request, session, actor, ViewPersonnelImports).profiles(actor)
    return [MappingProfileResponse(id=i, name=n, mapping=m) for i, n, m in found]


@router.delete("/mapping-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: UUID, request: Request, actor: OrganizationAdminDep, session: SessionDep) -> None:
    await _deps(request, session, actor, ViewPersonnelImports).delete_profile(actor, profile_id)


# --- Batches ----------------------------------------------------------------------------------------------------


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(1 << 20):
        yield chunk


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ImportDetailResponse)
async def upload(
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    file: Annotated[UploadFile, File(description=".xlsx or .csv (UTF-8)")],
    sheet_name: Annotated[str | None, Form(max_length=64)] = None,
    header_row: Annotated[int, Form(ge=1, le=1000)] = 1,
) -> ImportDetailResponse:
    view = await _deps(request, session, actor, UploadPersonnelImport).execute(
        actor, file.filename, _chunks(file), sheet_name=sheet_name or None, header_row=header_row
    )
    return _detail(view)


@router.get("", response_model=list[ImportBatchResponse])
async def history(
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ImportBatchResponse]:
    batches = await _deps(request, session, actor, ViewPersonnelImports).history(actor, limit, offset)
    return [ImportBatchResponse.of(b) for b in batches]


@router.get("/{batch_id}", response_model=ImportDetailResponse)
async def get_import(
    batch_id: UUID, request: Request, actor: OrganizationAdminDep, session: SessionDep
) -> ImportDetailResponse:
    return _detail(await _deps(request, session, actor, ViewPersonnelImports).get(actor, batch_id))


@router.put("/{batch_id}/mapping", response_model=ImportDetailResponse)
async def confirm_mapping(
    batch_id: UUID, body: MappingRequest, request: Request, actor: OrganizationAdminDep, session: SessionDep
) -> ImportDetailResponse:
    decisions = {c.index: c.target for c in body.columns}
    view = await _deps(request, session, actor, ValidatePersonnelImport).execute(
        actor, batch_id, decisions, save_profile_as=body.save_profile_as
    )
    return _detail(view)


@router.get("/{batch_id}/issues", response_model=list[ImportIssueResponse])
async def issues(
    batch_id: UUID,
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    severity: Literal["ERROR", "WARNING"] | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ImportIssueResponse]:
    found = await _deps(request, session, actor, ViewPersonnelImports).issues(actor, batch_id, severity, limit, offset)
    return [ImportIssueResponse.of(i) for i in found]


@router.get("/{batch_id}/annotated-workbook")
async def annotated_workbook(
    batch_id: UUID, request: Request, actor: OrganizationAdminDep, session: SessionDep
) -> FileResponse:
    path, name = await _deps(request, session, actor, ViewPersonnelImports).annotated_workbook(actor, batch_id)
    return FileResponse(path, media_type=_XLSX, filename=name, headers={"Cache-Control": "no-store"})


@router.get("/{batch_id}/preview", response_model=list[PreviewRowResponse])
async def preview(
    batch_id: UUID,
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[PreviewRowResponse]:
    rows = await _deps(request, session, actor, ViewPersonnelImports).preview(actor, batch_id, limit, offset)
    return [
        PreviewRowResponse(
            row_number=r.row_number,
            category=r.category_label,
            values={k: v for k, v in asdict(r.values).items() if k != "category_id"},
            location=r.location,
        )
        for r in rows
    ]


@router.post("/{batch_id}/confirm", response_model=ImportDetailResponse)
async def confirm(
    batch_id: UUID,
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    body: ConfirmImportRequest | None = None,
) -> ImportDetailResponse:
    view = await _deps(request, session, actor, ConfirmPersonnelImport).execute(
        actor, batch_id, acknowledge_warnings=bool(body and body.acknowledge_warnings)
    )
    return _detail(view)


@router.post("/{batch_id}/rollback", response_model=ImportDetailResponse)
async def rollback(
    batch_id: UUID,
    request: Request,
    actor: OrganizationAdminDep,
    session: SessionDep,
    body: RollbackImportRequest | None = None,
) -> ImportDetailResponse:
    view = await _deps(request, session, actor, RollBackPersonnelImport).execute(
        actor, batch_id, None if body is None else body.reason
    )
    return _detail(view)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard(batch_id: UUID, request: Request, actor: OrganizationAdminDep, session: SessionDep) -> None:
    await _deps(request, session, actor, DiscardPersonnelImport).execute(actor, batch_id)
