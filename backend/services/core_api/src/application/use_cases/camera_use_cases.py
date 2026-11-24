"""Camera Management (requirements Section 13; decision D-012).

Admins and Supervisors list, view, add, edit and remove the cameras of their own campus, and set
their location. Guards cannot. Another campus's camera answers 404, exactly like a camera that
does not exist. Buildings and perimeters themselves stay Admin-only (Location Management, D-010).
Every change is written to the organization audit log (CAMERA_CREATED / _UPDATED / _DELETED, D-024);
stream addresses are never copied into the record.

AI features with the camera's details (decision D-028): a new camera runs no AI module unless the request selects
some (``ai_modules``); an edit changes only the modules it names. They are validated (organization blocks, campus,
Supervisor visibility) before anything is written and saved in the same transaction as the camera.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID, uuid4

from src.application.use_cases.location_use_cases import LocationResolver
from src.domain.entities.ai_configuration import ModuleSet
from src.domain.entities.camera import (
    Camera,
    ConnectionType,
    clean_display_name,
    clean_stream_url,
)
from src.domain.entities.location import LocationSelection, LocationTarget
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import NotFound
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_camera_repo import ICameraRepository
from src.domain.repositories.i_location_repo import ILocationRepository
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import (
    ensure_camera_manager,
    ensure_camera_viewer,
)

CAMERA_CREATED = "CAMERA_CREATED"
CAMERA_UPDATED = "CAMERA_UPDATED"
CAMERA_DELETED = "CAMERA_DELETED"


def _summary(camera: Camera) -> dict[str, object]:
    """What the audit record keeps about a camera: never the stream address (it may hold credentials)."""
    return {
        "name": camera.display_name,
        "connection_type": camera.connection_type.value,
        "location": camera.location.label if camera.location is not None else None,
    }


def _missing() -> NotFound:
    # Same answer whether the camera is missing or belongs to another campus,
    # so a known ID reveals nothing.
    return NotFound("Camera not found.")


@dataclass(frozen=True, slots=True)
class CameraFilter:
    """Search (name, Camera ID or stream address) and location filters (at or below)."""

    text: str | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None

    def matches(self, camera: Camera) -> bool:
        if self.text:
            needle = self.text.strip().lower()
            haystack = " ".join([camera.display_name, str(camera.id), camera.stream_url or ""]).lower()
            if needle not in haystack:
                return False
        path = camera.location
        for level, wanted in (("BUILDING", self.building_id), ("FLOOR", self.floor_id), ("AREA", self.area_id)):
            if wanted is not None and (path is None or not path.is_within(level, wanted)):
                return False
        return True


@dataclass(frozen=True, slots=True)
class CameraDetails:
    display_name: str
    connection_type: ConnectionType
    stream_url: str | None = None
    location: LocationSelection | None = None
    ai_modules: Mapping[str, bool] | None = None  # module key → selected; None: all off (add) / unchanged (edit)


class ICameraAiLink(Protocol):
    """The camera's AI switches (implemented by the AI Configuration use cases)."""

    async def resolve_for_camera(
        self, actor: User, requested: Mapping[str, bool] | None, camera_id: UUID | None
    ) -> ModuleSet | None: ...

    async def save_for_camera(
        self, actor: User, camera_id: UUID, camera_name: str, modules: ModuleSet, created: bool
    ) -> None: ...

    async def camera_modules(self, actor: User) -> dict[UUID, tuple[list[str], list[str]]]: ...


class ListCameras:
    def __init__(self, cameras: ICameraRepository) -> None:
        self._cameras = cameras

    async def execute(self, actor: User, where: CameraFilter | None = None) -> list[Camera]:
        ensure_camera_viewer(actor)
        cameras = await self._cameras.list_cameras(actor.organization_id, actor.campus_id)
        return [c for c in cameras if where is None or where.matches(c)]


class GetCamera:
    def __init__(self, cameras: ICameraRepository) -> None:
        self._cameras = cameras

    async def execute(self, actor: User, camera_id: UUID) -> Camera:
        ensure_camera_viewer(actor)
        camera = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if camera is None:
            raise _missing()
        return camera


class _Writer:
    def __init__(
        self,
        cameras: ICameraRepository,
        locations: ILocationRepository,
        transaction: ITransaction,
        audit: IOrganizationAuditLog | None = None,
        ai: ICameraAiLink | None = None,
    ) -> None:
        self._cameras = cameras
        self._locations = LocationResolver(locations, transaction)
        self._transaction = transaction
        self._audit = audit
        self._ai = ai
        # (selected, effective) AI module keys after the write, read before the commit (D-028).
        self.ai_state: tuple[list[str], list[str]] | None = None

    async def _save_ai(self, actor: User, camera: Camera, modules: ModuleSet | None, created: bool) -> None:
        if self._ai is None:
            return
        if modules is not None:
            await self._ai.save_for_camera(actor, camera.id, camera.display_name, modules, created)
        self.ai_state = (await self._ai.camera_modules(actor)).get(camera.id)

    async def _location(self, selection: LocationSelection | None) -> LocationTarget | None:
        # Checked inside the caller's campus: another campus's IDs are "not found".
        return None if selection is None else await self._locations.resolve(selection)

    async def _read_back(self, actor: User, camera_id: UUID) -> Camera:
        camera = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if camera is None:
            raise _missing()
        return camera


class CreateCamera(_Writer):
    async def execute(self, actor: User, details: CameraDetails) -> Camera:
        ensure_camera_manager(actor)
        camera = Camera(
            id=uuid4(),
            organization_id=actor.organization_id,
            campus_id=actor.campus_id,
            display_name=clean_display_name(details.display_name),
            connection_type=details.connection_type,
            stream_url=clean_stream_url(details.connection_type, details.stream_url),
        )
        modules = None if self._ai is None else await self._ai.resolve_for_camera(actor, details.ai_modules, None)
        await self._cameras.add_camera(camera, await self._location(details.location))
        created = await self._read_back(actor, camera.id)
        if self._audit is not None:
            await self._audit.record(actor, CAMERA_CREATED, "CAMERA", created.id, _summary(created))
        await self._save_ai(actor, created, modules, created=True)
        await self._transaction.commit()
        return created


class UpdateCamera(_Writer):
    """Replaces the camera's details and location; ``location: null`` removes the location."""

    async def execute(self, actor: User, camera_id: UUID, details: CameraDetails) -> Camera:
        ensure_camera_manager(actor)
        current = await self._read_back(actor, camera_id)
        camera = replace(
            current,
            display_name=clean_display_name(details.display_name),
            connection_type=details.connection_type,
            stream_url=clean_stream_url(details.connection_type, details.stream_url),
        )
        modules = (
            None if self._ai is None else await self._ai.resolve_for_camera(actor, details.ai_modules, camera_id)
        )
        await self._cameras.update_camera(camera, await self._location(details.location))
        updated = await self._read_back(actor, camera_id)
        if self._audit is not None:
            before, after = _summary(current), _summary(updated)
            changed = sorted(k for k in after if before[k] != after[k])
            if current.stream_url != updated.stream_url:
                changed.append("stream_address")
            await self._audit.record(
                actor, CAMERA_UPDATED, "CAMERA", camera_id, {**after, "changed": changed, "previous": before}
            )
        await self._save_ai(actor, updated, modules, created=False)
        await self._transaction.commit()
        return updated


class DeleteCamera:
    """Removes the camera record. A camera with alert history cannot be removed (409
    CAMERA_HAS_ALERTS, D-013); recordings and evidence must do the same once they exist (D-012)."""

    def __init__(
        self, cameras: ICameraRepository, transaction: ITransaction, audit: IOrganizationAuditLog | None = None
    ) -> None:
        self._cameras = cameras
        self._transaction = transaction
        self._audit = audit

    async def execute(self, actor: User, camera_id: UUID) -> None:
        ensure_camera_manager(actor)
        current = await self._cameras.get_camera(camera_id, actor.organization_id, actor.campus_id)
        if current is None or not await self._cameras.delete_camera(camera_id, actor.organization_id, actor.campus_id):
            raise _missing()
        if self._audit is not None:
            await self._audit.record(actor, CAMERA_DELETED, "CAMERA", camera_id, _summary(current))
        await self._transaction.commit()
