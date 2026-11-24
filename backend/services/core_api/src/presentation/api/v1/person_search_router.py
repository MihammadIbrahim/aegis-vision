"""Person Search (Org Admin page 7, Supervisor page 7; requirements Section 16; decision D-024). Admins and
Supervisors of the campus; Guards and Super Admin tokens 403.

GET  /person-search/model                          whether photo search is available (Re-ID model)
GET  /person-search/observations                  recent people the cameras observed (?camera_id, since, until)
GET  /person-search/observations/{id}/crop         the observed person's crop (JPEG)
POST /person-search/photo                          search by a reference photo (multipart: photo, since, until,
                                                   camera_ids, note)
POST /person-search/observation                    search by an observed person
GET  /person-search                                earlier searches
GET  /person-search/{id}                           a search: results, bands, limitations
GET  /person-search/{id}/reference                 the reference picture
GET  /person-search/results/{id}/crop              a result's crop
GET  /person-search/results/{id}/clip              the recording around the result (audited)
POST /person-search/results/{id}/review            {review: VERIFIED_MATCH | NOT_A_MATCH | PENDING, note}
POST /person-search/results/{id}/incident          {incident_id}: link a verified result to an open case
POST /person-search/results/{id}/evidence          save the result's crop as evidence

Vision workers (X-Service-Token):
POST /internal/reid/observations                   one person observation (512-number vector + crop)
POST /internal/reid/purge-expired                  delete observations past the campus retention
"""

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from src.application.dtos.person_search_dto import (
    CandidateResponse,
    IntakeResponse,
    LinkIncidentRequest,
    ModelStatusResponse,
    ObservationIntake,
    ObservationResponse,
    ObservationSearchRequest,
    ReviewRequest,
    SavedEvidenceResponse,
    SaveEvidenceRequest,
    SearchResponse,
)
from src.application.use_cases.person_search_use_cases import PersonSearchService, SearchScope
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.person_search_repository import SqlPersonSearchRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.infrastructure.ml.reid_embedder import ReIdModelUnavailable, load_embedder
from src.presentation.api.v1.evidence_router import vault as evidence_vault
from src.presentation.api.v1.incident_router import service as incident_service
from src.presentation.api.v1.recording_router import require_service_token
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/person-search", tags=["Person Search"])
internal_router = APIRouter(prefix="/internal/reid", tags=["Vision workers"])

CLIP_AROUND = timedelta(seconds=5)
_JPEG = {"Cache-Control": "no-store"}


def _service(actor: User | None, session: AsyncSession, request: Request) -> PersonSearchService:
    settings = request.app.state.settings
    manifest = settings.reid_model_manifest
    return PersonSearchService(
        SqlPersonSearchRepository(session, actor),
        request.app.state.media_storage,
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        likely=settings.reid_likely_threshold,
        uncertain=settings.reid_uncertain_threshold,
        max_results=settings.person_search_max_results,
        embedder=(lambda: load_embedder(manifest)) if manifest else None,
        incidents=incident_service(actor, session) if actor is not None else None,
        evidence=evidence_vault(actor, session, request) if actor is not None else None,
    )


@router.get("/model", response_model=ModelStatusResponse)
async def model_status(request: Request, actor: StaffDep) -> ModelStatusResponse:
    settings = request.app.state.settings
    version, reason = None, None
    try:
        version = load_embedder(settings.reid_model_manifest).version
    except ReIdModelUnavailable as error:
        reason = str(error)
    return ModelStatusResponse(
        photo_search_available=version is not None,
        model_version=version,
        reason=reason,
        likely_threshold=settings.reid_likely_threshold,
        uncertain_threshold=settings.reid_uncertain_threshold,
    )


@router.get("/observations", response_model=list[ObservationResponse])
async def observations(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    camera_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=60, ge=1, le=300),
) -> list[ObservationResponse]:
    found = await _service(actor, session, request).observations(
        actor, camera_id=camera_id, since=since, until=until, limit=limit
    )
    return [ObservationResponse.of(o) for o in found]


@router.get("/observations/{observation_id}/crop")
async def observation_crop(observation_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    data = await _service(actor, session, request).observation_crop(actor, observation_id)
    return Response(data, media_type="image/jpeg", headers=_JPEG)


@router.post("/photo", response_model=SearchResponse, status_code=201)
async def search_by_photo(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    photo: Annotated[UploadFile, File()],
    since: Annotated[datetime | None, Form()] = None,
    until: Annotated[datetime | None, Form()] = None,
    camera_ids: Annotated[list[UUID] | None, Form()] = None,
    note: Annotated[str | None, Form(max_length=1000)] = None,
) -> SearchResponse:
    data = await photo.read(8 * 1024 * 1024 + 1)
    scope = SearchScope(since, until, tuple(camera_ids or ()), note)
    return SearchResponse.of(await _service(actor, session, request).search_by_photo(actor, data, scope))


@router.post("/observation", response_model=SearchResponse, status_code=201)
async def search_by_observation(
    body: ObservationSearchRequest, request: Request, actor: StaffDep, session: SessionDep
) -> SearchResponse:
    scope = SearchScope(body.since, body.until, tuple(body.camera_ids), body.note)
    found = await _service(actor, session, request).search_by_observation(actor, body.observation_id, scope)
    return SearchResponse.of(found)


@router.get("", response_model=list[SearchResponse])
async def searches(
    request: Request,
    actor: StaffDep,
    session: SessionDep,
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[SearchResponse]:
    return [SearchResponse.of(s) for s in await _service(actor, session, request).searches(actor, limit, offset)]


@router.get("/results/{candidate_id}/crop")
async def result_crop(candidate_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    data = await _service(actor, session, request).candidate_crop(actor, candidate_id)
    return Response(data, media_type="image/jpeg", headers=_JPEG)


@router.get("/results/{candidate_id}/clip")
async def result_clip(candidate_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> FileResponse:
    found = await _service(actor, session, request).candidate(actor, candidate_id)
    if found.camera_id is None:
        raise NotFound("The camera of this result was removed.", code="CAMERA_NOT_FOUND")
    folder = Path(tempfile.mkdtemp(prefix="person-clip-"))
    output = folder / "clip.mp4"
    try:
        await evidence_vault(actor, session, request).moment_clip(
            actor,
            found.camera_id,
            found.observed_at - CLIP_AROUND,
            found.observed_at + CLIP_AROUND,
            output,
            "PERSON_SEARCH_CLIP_VIEWED",
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return FileResponse(
        output, media_type="video/mp4", headers=_JPEG, background=BackgroundTask(shutil.rmtree, folder, True)
    )


@router.post("/results/{candidate_id}/review", response_model=CandidateResponse)
async def review_result(
    candidate_id: UUID, body: ReviewRequest, request: Request, actor: StaffDep, session: SessionDep
) -> CandidateResponse:
    found = await _service(actor, session, request).review(actor, candidate_id, body.review, body.note)
    return CandidateResponse.of(found)


@router.post("/results/{candidate_id}/incident", response_model=CandidateResponse)
async def link_result(
    candidate_id: UUID, body: LinkIncidentRequest, request: Request, actor: StaffDep, session: SessionDep
) -> CandidateResponse:
    found = await _service(actor, session, request).link_incident(actor, candidate_id, body.incident_id)
    return CandidateResponse.of(found)


@router.post("/results/{candidate_id}/evidence", response_model=SavedEvidenceResponse, status_code=201)
async def save_result_evidence(
    candidate_id: UUID, body: SaveEvidenceRequest, request: Request, actor: StaffDep, session: SessionDep
) -> SavedEvidenceResponse:
    evidence_id = await _service(actor, session, request).save_evidence(
        actor, candidate_id, body.title, body.notes, body.tags
    )
    return SavedEvidenceResponse(evidence_id=evidence_id)


@router.get("/{search_id}", response_model=SearchResponse)
async def search_detail(search_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> SearchResponse:
    return SearchResponse.of(await _service(actor, session, request).search(actor, search_id))


@router.get("/{search_id}/reference")
async def reference(search_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> Response:
    data = await _service(actor, session, request).reference(actor, search_id)
    return Response(data, media_type="image/jpeg", headers=_JPEG)


@internal_router.post("/observations", response_model=IntakeResponse, status_code=201)
async def intake(
    body: ObservationIntake,
    request: Request,
    session: SessionDep,
    _: Annotated[None, Depends(require_service_token)],
) -> IntakeResponse:
    observation_id = await _service(None, session, request).record_observation(
        body.camera_id, body.track_id, body.observed_at, body.embedding, body.model_version, body.quality,
        body.crop_jpeg_base64,
    )  # fmt: skip
    return IntakeResponse(observation_id=observation_id)


@internal_router.post("/purge-expired")
async def purge_expired(
    request: Request, session: SessionDep, _: Annotated[None, Depends(require_service_token)]
) -> dict[str, int]:
    days = request.app.state.settings.recording_retention_default_days
    return {"deleted": await _service(None, session, request).purge_expired(days * 86400)}
