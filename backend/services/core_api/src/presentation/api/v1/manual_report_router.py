"""Manual security reports (requirements 15.6; decision D-013).

Guards:
POST   /guard/manual-reports          submit (idempotent by client_report_id; optional base64 photo)
GET    /guard/manual-reports          their own reports
GET    /guard/locations               building → floor → area options for the report form
Admins and Supervisors (own campus):
GET    /manual-reports                list
GET    /manual-reports/{id}           details
GET    /manual-reports/{id}/photo     the photo (no caching)
POST   /manual-reports/{id}/review    mark as reviewed (optional note)
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from src.application.dtos.manual_report_dto import (
    GuardLocationOption,
    ManualReportRequest,
    ManualReportResponse,
    ReviewRequest,
    SubmittedReportResponse,
)
from src.application.use_cases.manual_report_use_cases import (
    ReviewManualReport,
    SubmitManualReport,
    ViewManualReports,
)
from src.infrastructure.database.repositories.audit_log_repository import (
    SqlOrganizationAuditLog,
)
from src.infrastructure.database.repositories.manual_report_repository import (
    SqlManualReportRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import GuardDep, SessionDep, StaffDep

guard_router = APIRouter(prefix="/guard", tags=["Guard app"])
router = APIRouter(prefix="/manual-reports", tags=["Manual reports"])


@guard_router.post("/manual-reports", response_model=SubmittedReportResponse, status_code=201)
async def submit_report(
    body: ManualReportRequest, request: Request, guard: GuardDep, session: SessionDep
) -> JSONResponse:
    report, created = await SubmitManualReport(
        SqlManualReportRepository(session),
        request.app.state.media_storage,
        SqlTransaction(session),
        max_photo_bytes=request.app.state.settings.manual_report_photo_max_bytes,
    ).execute(guard, body.to_draft())
    payload = SubmittedReportResponse(report=ManualReportResponse.of(report), created=created)
    return JSONResponse(payload.model_dump(mode="json"), status_code=201 if created else 200)


@guard_router.get("/manual-reports", response_model=list[ManualReportResponse])
async def my_reports(
    guard: GuardDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ManualReportResponse]:
    reports = await ViewManualReports(SqlManualReportRepository(session)).list_reports(
        guard, limit=limit, offset=offset
    )
    return [ManualReportResponse.of(r) for r in reports]


@guard_router.get("/locations", response_model=list[GuardLocationOption])
async def report_locations(guard: GuardDep, session: SessionDep) -> list[GuardLocationOption]:
    rows = await ViewManualReports(SqlManualReportRepository(session)).locations(guard)
    return [GuardLocationOption.model_validate(r) for r in rows]


@router.get("", response_model=list[ManualReportResponse])
async def list_reports(
    actor: StaffDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ManualReportResponse]:
    reports = await ViewManualReports(SqlManualReportRepository(session)).list_reports(
        actor, limit=limit, offset=offset
    )
    return [ManualReportResponse.of(r) for r in reports]


@router.get("/{report_id}", response_model=ManualReportResponse)
async def get_report(report_id: UUID, actor: StaffDep, session: SessionDep) -> ManualReportResponse:
    return ManualReportResponse.of(await ViewManualReports(SqlManualReportRepository(session)).get(actor, report_id))


@router.get("/{report_id}/photo")
async def report_photo(report_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    data, content_type = await ViewManualReports(
        SqlManualReportRepository(session), request.app.state.media_storage
    ).photo(actor, report_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})


@router.post("/{report_id}/review", response_model=ManualReportResponse)
async def review_report(
    report_id: UUID, actor: StaffDep, session: SessionDep, body: ReviewRequest | None = None
) -> ManualReportResponse:
    report = await ReviewManualReport(
        SqlManualReportRepository(session), SqlOrganizationAuditLog(session), SqlTransaction(session)
    ).execute(actor, report_id, None if body is None else body.note)
    return ManualReportResponse.of(report)
