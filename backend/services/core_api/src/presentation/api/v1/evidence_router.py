"""The investigation evidence vault (decision D-023). Admins and Supervisors of the own campus:

GET    /evidence                    list (?q=, ?kind=, ?camera_id=, ?alert_id=, ?tag=, ?building_id=, ?floor_id=,
                                    ?area_id=, ?incident_id=, ?since=, ?until=, ?limit=, ?offset=)
POST   /evidence/from-alert         save a clip or snapshot of an alert
POST   /evidence/from-playback      save a clip or snapshot from a recording segment
GET    /evidence/{id}               details
GET    /evidence/{id}/media         the file (audited as a view)
PATCH  /evidence/{id}               title, notes, investigation reference, tags
POST   /evidence/{id}/delete        Admins only, with their password
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse

from src.application.dtos.evidence_dto import (
    EvidenceDeleteRequest,
    EvidenceResponse,
    EvidenceUpdateRequest,
    SaveFromAlertRequest,
    SaveFromPlaybackRequest,
)
from src.application.use_cases.evidence_use_cases import ClipWindow, EvidenceVault
from src.domain.entities.user import User
from src.domain.repositories.i_evidence_repo import EvidenceFilter
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.evidence_repository import SqlEvidenceRepository
from src.infrastructure.database.repositories.recording_repository import SqlRecordingRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.media.clips import FfmpegClipTool
from src.presentation.api.v1.incident_router import service as incident_service
from src.presentation.api.v1.storage_settings_router import password_confirmation
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/evidence", tags=["Evidence vault"])

_NO_STORE = {"Cache-Control": "no-store"}


def vault(actor: User, session, request: Request) -> EvidenceVault:  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    return EvidenceVault(
        SqlEvidenceRepository(session),
        SqlRecordingRepository(session),
        request.app.state.media_storage,
        FfmpegClipTool(settings.ffmpeg_path),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        window=ClipWindow(
            settings.evidence_clip_pre_seconds, settings.evidence_clip_post_seconds, settings.evidence_clip_max_seconds
        ),
        confirmation=password_confirmation(actor, session, request),
        linker=incident_service(actor, session),
    )


@router.get("", response_model=list[EvidenceResponse])
async def list_evidence(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    q: str | None = Query(default=None, max_length=200),
    kind: Literal["CLIP", "SNAPSHOT"] | None = None,
    camera_id: UUID | None = None,
    alert_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tag: str | None = Query(default=None, max_length=40),
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
    incident_id: UUID | None = None,
) -> list[EvidenceResponse]:
    where = EvidenceFilter(
        q, kind, camera_id, alert_id, since, until, limit, offset, tag, building_id, floor_id, area_id, incident_id
    )
    return [EvidenceResponse.of(e) for e in await vault(actor, session, request).list_items(actor, where)]


@router.post("/from-alert", response_model=EvidenceResponse, status_code=201)
async def save_from_alert(
    body: SaveFromAlertRequest, request: Request, actor: StaffDep, session: SessionDep
) -> EvidenceResponse:
    item = await vault(actor, session, request).save_from_alert(actor, body.alert_id, body.kind, body.to_details())
    return EvidenceResponse.of(item)


@router.post("/from-playback", response_model=EvidenceResponse, status_code=201)
async def save_from_playback(
    body: SaveFromPlaybackRequest, request: Request, actor: StaffDep, session: SessionDep
) -> EvidenceResponse:
    item = await vault(actor, session, request).save_from_playback(
        actor, body.segment_id, body.kind, body.start_offset_seconds, body.end_offset_seconds, body.to_details()
    )
    return EvidenceResponse.of(item)


@router.get("/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence(evidence_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> EvidenceResponse:
    return EvidenceResponse.of(await vault(actor, session, request).get(actor, evidence_id))


@router.get("/{evidence_id}/media")
async def evidence_media(evidence_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> FileResponse:
    item, path = await vault(actor, session, request).media(actor, evidence_id)
    extension = "mp4" if item.media_type == "video/mp4" else "jpg"
    return FileResponse(path, media_type=item.media_type, headers=_NO_STORE, filename=f"evidence-{item.id}.{extension}")


@router.patch("/{evidence_id}", response_model=EvidenceResponse)
async def update_evidence(
    evidence_id: UUID, body: EvidenceUpdateRequest, request: Request, actor: StaffDep, session: SessionDep
) -> EvidenceResponse:
    return EvidenceResponse.of(await vault(actor, session, request).update(actor, evidence_id, body.to_details()))


@router.post("/{evidence_id}/delete", status_code=204)
async def delete_evidence(
    evidence_id: UUID, body: EvidenceDeleteRequest, request: Request, actor: StaffDep, session: SessionDep
) -> Response:
    await vault(actor, session, request).delete(actor, evidence_id, body.current_password)
    return Response(status_code=204)
