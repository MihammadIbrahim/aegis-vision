"""Camera Management for the signed-in Admin or Supervisor's own campus (decision D-012).

GET    /cameras         list; optional ?q= (name, Camera ID, stream address) and
                        ?building_id= / ?floor_id= / ?area_id= (at or below that location)
GET    /cameras/{id}    details
POST   /cameras         add a camera (optionally with its location and AI features; none run by default, D-028)
PUT    /cameras/{id}    replace its details and location (and the AI features it names)
DELETE /cameras/{id}    remove it

Location only: PUT/DELETE /locations/camera-links/{id} (Location Management).
"""

from uuid import UUID

from fastapi import APIRouter, Query, status

from src.application.dtos.camera_dto import CameraRequest, CameraResponse
from src.application.use_cases.ai_config_use_cases import AiConfiguration
from src.application.use_cases.camera_use_cases import (
    CameraFilter,
    CreateCamera,
    DeleteCamera,
    GetCamera,
    ListCameras,
    UpdateCamera,
)
from src.domain.entities.user import User
from src.infrastructure.database.repositories.ai_config_repository import SqlAiConfigRepository
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.camera_repository import (
    SqlCameraRepository,
)
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import ActorDep, CameraManagerDep, SessionDep

router = APIRouter(prefix="/cameras", tags=["Cameras"])


def _ai(actor: User, session: SessionDep) -> AiConfiguration:
    return AiConfiguration(SqlAiConfigRepository(session), SqlOrganizationAuditLog(session), SqlTransaction(session))


@router.get("", response_model=list[CameraResponse])
async def list_cameras(
    actor: ActorDep,
    session: SessionDep,
    q: str | None = Query(default=None, max_length=200),
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
) -> list[CameraResponse]:
    where = CameraFilter(q, building_id, floor_id, area_id)
    cameras = await ListCameras(SqlCameraRepository(session)).execute(actor, where)
    ai = await _ai(actor, session).camera_modules(actor)
    return [CameraResponse.from_entity(camera, ai.get(camera.id)) for camera in cameras]


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: UUID, actor: ActorDep, session: SessionDep) -> CameraResponse:
    camera = await GetCamera(SqlCameraRepository(session)).execute(actor, camera_id)
    return CameraResponse.from_entity(camera, (await _ai(actor, session).camera_modules(actor)).get(camera.id))


def _writer_deps(actor, session):  # type: ignore[no-untyped-def]
    return (
        SqlCameraRepository(session),
        SqlLocationRepository(session, actor.organization_id, actor.campus_id),
        SqlTransaction(session),
        SqlOrganizationAuditLog(session),
        _ai(actor, session),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CameraResponse)
async def add_camera(body: CameraRequest, actor: CameraManagerDep, session: SessionDep) -> CameraResponse:
    writer = CreateCamera(*_writer_deps(actor, session))
    camera = await writer.execute(actor, body.to_details())
    return CameraResponse.from_entity(camera, writer.ai_state)


@router.put("/{camera_id}", response_model=CameraResponse)
async def edit_camera(
    camera_id: UUID, body: CameraRequest, actor: CameraManagerDep, session: SessionDep
) -> CameraResponse:
    writer = UpdateCamera(*_writer_deps(actor, session))
    camera = await writer.execute(actor, camera_id, body.to_details())
    return CameraResponse.from_entity(camera, writer.ai_state)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_camera(camera_id: UUID, actor: CameraManagerDep, session: SessionDep) -> None:
    await DeleteCamera(SqlCameraRepository(session), SqlTransaction(session), SqlOrganizationAuditLog(session)).execute(
        actor, camera_id
    )
