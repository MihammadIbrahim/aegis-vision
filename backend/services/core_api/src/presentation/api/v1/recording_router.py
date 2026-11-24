"""Live CCTV monitoring, the recordings archive and the internal service reports (decision D-023).

Admins and Supervisors (own campus):
GET    /live/cameras                        cameras with online/offline status (?q=, ?building_id=, …)
POST   /live/sessions                       audit the start of a live view (camera_ids: [] = all)
GET    /live/cameras/{id}/frame             the newest live frame (image/jpeg; 404 when none or stale)
POST   /live/cameras/{id}/health-check      test the camera's reachability from the server
GET    /recordings                          timeline: segments + AI markers (?camera_id=, ?since=, ?until=, …)
GET    /recordings/{id}                     one segment
GET    /recordings/{id}/media               the MP4 (audited as a playback)
Internal services (header ``X-Service-Token``; the ingestion service and the retention daemon):
GET    /internal/cameras                    every camera of every active campus
POST   /internal/cameras/{id}/status        ONLINE / OFFLINE report
POST   /internal/recordings/segments        register a finished recording file
GET    /internal/recordings/expired         routine recordings past their campus retention
POST   /internal/recordings/purge           forget purged recordings
POST   /internal/storage/report             disk usage of the storage node
"""

import hmac
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import FileResponse

from src.application.dtos.recording_dto import (
    CameraStatusReportRequest,
    CameraStatusReportResponse,
    DiskReportRequest,
    DiskReportResponse,
    ExpiredSegmentResponse,
    HealthCheckResponse,
    LiveSessionRequest,
    MonitoredCameraResponse,
    PurgeRequest,
    PurgeResponse,
    RecordingSegmentResponse,
    SegmentReportRequest,
    SegmentReportResponse,
    ServiceCameraResponse,
    TimelineResponse,
)
from src.application.use_cases.camera_use_cases import CameraFilter
from src.application.use_cases.recording_use_cases import (
    CameraMonitoring,
    RecordingsArchive,
    ServiceReports,
)
from src.config.settings import Settings
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import AuthenticationFailed, ServiceUnavailable, ValidationFailed
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.camera_repository import SqlCameraRepository
from src.infrastructure.database.repositories.recording_repository import SqlRecordingRepository
from src.infrastructure.database.repositories.storage_settings_repository import SqlStorageSettingsRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.network.camera_probe import probe_stream
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/live", tags=["Live monitoring"])
recordings_router = APIRouter(prefix="/recordings", tags=["Recordings"])
internal_router = APIRouter(prefix="/internal", tags=["Internal services"])

_NO_STORE = {"Cache-Control": "no-store"}


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _monitoring(actor: User, session, request: Request) -> CameraMonitoring:  # type: ignore[no-untyped-def]
    return CameraMonitoring(
        SqlCameraRepository(session),
        SqlRecordingRepository(session),
        request.app.state.media_storage,
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        live_stale_seconds=_settings(request).live_snapshot_stale_seconds,
    )


def _archive(session, request: Request) -> RecordingsArchive:  # type: ignore[no-untyped-def]
    return RecordingsArchive(
        SqlCameraRepository(session),
        SqlRecordingRepository(session),
        request.app.state.media_storage,
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        retention=SqlStorageSettingsRepository(session),
        default_retention_seconds=_settings(request).recording_retention_default_days * 86400,
    )


def require_service_token(request: Request, x_service_token: Annotated[str | None, Header()] = None) -> None:
    """The shared secret of the ingestion service and the retention daemon (never a user token)."""
    token = _settings(request).internal_service_token
    expected = token.get_secret_value() if token else ""
    if not expected:
        raise ServiceUnavailable("The internal service endpoints are not configured.", code="SERVICE_INTAKE_DISABLED")
    if not x_service_token or not hmac.compare_digest(x_service_token.encode(), expected.encode()):
        raise AuthenticationFailed("Invalid service token.")


def _reports(session, request: Request) -> ServiceReports:  # type: ignore[no-untyped-def]
    settings = _settings(request)
    return ServiceReports(
        SqlRecordingRepository(session),
        SqlTransaction(session),
        default_retention_seconds=settings.recording_retention_default_days * 86400,
        warning_percent=settings.storage_warning_percent,
        critical_percent=settings.storage_critical_percent,
        node=settings.storage_node_name,
    )


# --- Live monitoring ------------------------------------------------------------------------------


@router.get("/cameras", response_model=list[MonitoredCameraResponse])
async def live_cameras(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    q: str | None = Query(default=None, max_length=200),
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
) -> list[MonitoredCameraResponse]:
    cameras = await _monitoring(actor, session, request).overview(
        actor, CameraFilter(q, building_id, floor_id, area_id)
    )
    return [MonitoredCameraResponse.of(c) for c in cameras]


@router.post("/sessions", response_model=list[MonitoredCameraResponse])
async def start_live_session(
    body: LiveSessionRequest, request: Request, actor: StaffDep, session: SessionDep
) -> list[MonitoredCameraResponse]:
    cameras = await _monitoring(actor, session, request).start_live_view(actor, body.camera_ids)
    return [MonitoredCameraResponse.of(c) for c in cameras]


@router.get("/cameras/{camera_id}/frame")
async def live_frame(camera_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    frame = await _monitoring(actor, session, request).live_frame(actor, camera_id)
    headers = {**_NO_STORE, "Last-Modified": frame.captured_at.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    return Response(content=frame.data, media_type="image/jpeg", headers=headers)


@router.post("/cameras/{camera_id}/health-check", response_model=HealthCheckResponse)
async def health_check(camera_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> HealthCheckResponse:
    monitoring = _monitoring(actor, session, request)
    monitored = await monitoring.one(actor, camera_id)
    if not monitored.camera.stream_url:
        raise ValidationFailed("This camera has no stream URL to test.", code="NO_STREAM_URL")
    result = await probe_stream(monitored.camera.stream_url, _settings(request).camera_health_check_timeout_seconds)
    updated = await monitoring.health_check(actor, camera_id, result)
    return HealthCheckResponse.of(result, updated)


# --- Recordings -----------------------------------------------------------------------------------


@recordings_router.get("", response_model=TimelineResponse)
async def timeline(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    camera_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> TimelineResponse:
    result = await _archive(session, request).timeline(
        actor,
        camera_id=camera_id,
        where=CameraFilter(q, building_id, floor_id, area_id),
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return TimelineResponse.of(result)


@recordings_router.get("/{segment_id}", response_model=RecordingSegmentResponse)
async def get_segment(
    segment_id: UUID, request: Request, actor: StaffDep, session: SessionDep
) -> RecordingSegmentResponse:
    return RecordingSegmentResponse.of(await _archive(session, request).segment(actor, segment_id))


@recordings_router.get("/{segment_id}/media")
async def segment_media(segment_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> FileResponse:
    segment, path = await _archive(session, request).media(actor, segment_id)
    return FileResponse(path, media_type="video/mp4", headers=_NO_STORE, filename=f"recording-{segment.id}.mp4")


# --- Internal services ----------------------------------------------------------------------------


@internal_router.get("/cameras", response_model=list[ServiceCameraResponse])
async def service_cameras(request: Request, session: SessionDep) -> list[ServiceCameraResponse]:
    require_service_token(request, request.headers.get("x-service-token"))
    return [ServiceCameraResponse.of(c) for c in await _reports(session, request).cameras()]


@internal_router.post("/cameras/{camera_id}/status", response_model=CameraStatusReportResponse)
async def report_camera_status(
    camera_id: UUID, body: CameraStatusReportRequest, request: Request, session: SessionDep
) -> CameraStatusReportResponse:
    require_service_token(request, request.headers.get("x-service-token"))
    outcome, previous = await _reports(session, request).camera_status(body.to_report(camera_id))
    return CameraStatusReportResponse(outcome=outcome, previous_state=previous)  # type: ignore[arg-type]


@internal_router.post("/recordings/segments", response_model=SegmentReportResponse, status_code=201)
async def register_segment(body: SegmentReportRequest, request: Request, session: SessionDep) -> SegmentReportResponse:
    require_service_token(request, request.headers.get("x-service-token"))
    segment_id, outcome = await _reports(session, request).register_segment(body.to_report())
    return SegmentReportResponse(segment_id=segment_id, outcome=outcome)  # type: ignore[arg-type]


@internal_router.get("/recordings/expired", response_model=list[ExpiredSegmentResponse])
async def expired_segments(
    request: Request, session: SessionDep, limit: int = Query(default=500, ge=1, le=5000)
) -> list[ExpiredSegmentResponse]:
    require_service_token(request, request.headers.get("x-service-token"))
    return [ExpiredSegmentResponse.of(e) for e in await _reports(session, request).expired(limit)]


@internal_router.post("/recordings/purge", response_model=PurgeResponse)
async def purge_segments(body: PurgeRequest, request: Request, session: SessionDep) -> PurgeResponse:
    require_service_token(request, request.headers.get("x-service-token"))
    return PurgeResponse(purged=await _reports(session, request).purge(body.segment_ids))


@internal_router.post("/storage/report", response_model=DiskReportResponse)
async def report_storage(body: DiskReportRequest, request: Request, session: SessionDep) -> DiskReportResponse:
    require_service_token(request, request.headers.get("x-service-token"))
    level, notified = await _reports(session, request).disk(body.to_report())
    return DiskReportResponse(level=level, notified=notified)  # type: ignore[arg-type]
