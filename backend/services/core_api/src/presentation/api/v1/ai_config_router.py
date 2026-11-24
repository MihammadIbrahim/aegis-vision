"""AI Configuration (Org Admin page 17, Supervisor page 14) and live detection overlays (Live items 16–22),
decisions D-024 and D-028. Admins and Supervisors of the campus; Guards and Super Admin tokens 403.

GET  /ai-configuration                        platform, organization blocks, campus and per-camera modules,
                                              worker status; Supervisors see only the modules visible to them
PUT  /ai-configuration/campus                 Admins: campus modules and Supervisor visibility
                                              {…, supervisor_visible_modules?, current_password}
PUT  /ai-configuration/cameras/bulk           Admins and Supervisors: {camera_ids, mode ADD|REMOVE|SET, modules,
                                              current_password}, all or nothing
PUT  /ai-configuration/cameras/{camera_id}    Admins and Supervisors: camera modules {…, current_password}
GET  /live/cameras/{camera_id}/overlay        the newest detections of a vision worker (boxes, tracks, scores)

Vision workers (header X-Service-Token):
GET  /internal/cameras/{camera_id}/ai-config  the modules to run now and the detection parameters
POST /internal/cameras/{camera_id}/ai-status  what the worker runs (modules, frames per second, error)
"""

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.ai_config_dto import (
    AiConfigurationResponse,
    AiStatusReport,
    BulkCameraAiRequest,
    BulkCameraAiResponse,
    CameraAiResponse,
    CampusModulesChangeRequest,
    ModulesChangeRequest,
    OverlayDetection,
    OverlayResponse,
    ServiceAiConfigResponse,
)
from src.application.use_cases.ai_config_use_cases import AiConfiguration
from src.application.use_cases.camera_use_cases import GetCamera
from src.domain.entities.user import User
from src.infrastructure.database.repositories.ai_config_repository import SqlAiConfigRepository
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.camera_repository import SqlCameraRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.api.v1.recording_router import require_service_token
from src.presentation.api.v1.storage_settings_router import password_confirmation
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/ai-configuration", tags=["AI Configuration"])
live_router = APIRouter(prefix="/live", tags=["Live monitoring"])
internal_router = APIRouter(prefix="/internal", tags=["Vision workers"])

OVERLAY_FILE = "overlay.json"
MAX_OVERLAY_BYTES = 256 * 1024
MAX_DETECTIONS = 200


def configuration(actor: User | None, session: AsyncSession, request: Request) -> AiConfiguration:
    return AiConfiguration(
        SqlAiConfigRepository(session),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        confirmation=password_confirmation(actor, session, request) if actor is not None else None,
    )


@router.get("", response_model=AiConfigurationResponse)
async def view(request: Request, actor: StaffDep, session: SessionDep) -> AiConfigurationResponse:
    return AiConfigurationResponse.of(await configuration(actor, session, request).view(actor))


@router.put("/campus", response_model=AiConfigurationResponse)
async def update_campus(
    body: CampusModulesChangeRequest, request: Request, actor: StaffDep, session: SessionDep
) -> AiConfigurationResponse:
    use_case = configuration(actor, session, request)
    visible = None if body.supervisor_visible_modules is None else body.supervisor_visible_modules.to_modules()
    return AiConfigurationResponse.of(
        await use_case.update_campus(actor, body.to_modules(), body.current_password, visible)
    )


@router.put("/cameras/bulk", response_model=BulkCameraAiResponse)
async def update_cameras_bulk(
    body: BulkCameraAiRequest, request: Request, actor: StaffDep, session: SessionDep
) -> BulkCameraAiResponse:
    use_case = configuration(actor, session, request)
    cameras = await use_case.update_cameras_bulk(
        actor, body.camera_ids, body.mode, {k.value: v for k, v in body.modules.items()}, body.current_password
    )
    return BulkCameraAiResponse(
        mode=body.mode, updated_count=len(cameras), cameras=[CameraAiResponse.of(c) for c in cameras]
    )


@router.put("/cameras/{camera_id}", response_model=AiConfigurationResponse)
async def update_camera(
    camera_id: UUID, body: ModulesChangeRequest, request: Request, actor: StaffDep, session: SessionDep
) -> AiConfigurationResponse:
    use_case = configuration(actor, session, request)
    view_ = await use_case.update_camera(actor, camera_id, body.to_modules(), body.current_password)
    return AiConfigurationResponse.of(view_)


def _fraction(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not a number")
    return min(1.0, max(0.0, number))


def _detections(raw: Any) -> list[OverlayDetection]:
    """Only well-formed entries; anything else in the worker's file is ignored."""
    found: list[OverlayDetection] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            box = [_fraction(v) for v in item["box"]][:4]
            if len(box) != 4 or box[2] < box[0] or box[3] < box[1]:
                continue
            confidence = item.get("confidence")
            track = item.get("track_id")
            found.append(
                OverlayDetection(
                    label=str(item.get("label", "object"))[:40],
                    confidence=None if confidence is None else round(_fraction(confidence), 4),
                    box=box,
                    track_id=int(track) if isinstance(track, int | float) and not isinstance(track, bool) else None,
                    module=str(item["module"])[:40] if item.get("module") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
        if len(found) >= MAX_DETECTIONS:
            break
    return found


@live_router.get("/cameras/{camera_id}/overlay", response_model=OverlayResponse)
async def overlay(camera_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> OverlayResponse:
    camera = await GetCamera(SqlCameraRepository(session)).execute(actor, camera_id)
    storage = request.app.state.media_storage
    stale_after = timedelta(seconds=request.app.state.settings.live_snapshot_stale_seconds)
    empty = OverlayResponse(
        camera_id=camera.id, captured_at=None, stale=True, frame_width=None, frame_height=None, modules=[],
        detections=[],
    )  # fmt: skip
    try:
        path = storage.tier_path("live", actor.organization_id, actor.campus_id, f"{camera.id}/{OVERLAY_FILE}")
        if not path.is_file() or path.stat().st_size > MAX_OVERLAY_BYTES:
            return empty
        data = json.loads(path.read_text(encoding="utf-8"))
        captured_at = datetime.fromisoformat(str(data["captured_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return empty
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    width, height = data.get("frame_width"), data.get("frame_height")
    modules = data.get("modules") if isinstance(data.get("modules"), list) else []
    return OverlayResponse(
        camera_id=camera.id,
        captured_at=captured_at,
        stale=datetime.now(UTC) - captured_at > stale_after,
        frame_width=width if isinstance(width, int) and width > 0 else None,
        frame_height=height if isinstance(height, int) and height > 0 else None,
        modules=[str(m)[:40] for m in modules][:5],
        detections=_detections(data.get("detections")),
    )


@internal_router.get("/cameras/{camera_id}/ai-config", response_model=ServiceAiConfigResponse)
async def service_config(
    camera_id: UUID,
    request: Request,
    session: SessionDep,
    _: Annotated[None, Depends(require_service_token)],
) -> ServiceAiConfigResponse:
    return ServiceAiConfigResponse.of(await configuration(None, session, request).service_config(camera_id))


@internal_router.post("/cameras/{camera_id}/ai-status", status_code=204)
async def report_status(
    camera_id: UUID,
    body: AiStatusReport,
    request: Request,
    session: SessionDep,
    _: Annotated[None, Depends(require_service_token)],
) -> None:
    await configuration(None, session, request).report_status(
        camera_id, [m.value for m in body.modules], body.frames_per_second, body.error, body.worker
    )
