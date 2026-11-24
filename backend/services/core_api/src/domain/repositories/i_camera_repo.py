from typing import Protocol
from uuid import UUID

from src.domain.entities.camera import Camera
from src.domain.entities.location import LocationTarget


class ICameraRepository(Protocol):
    """Cameras of one campus. The database (Row-Level Security) repeats the campus rule, and lets
    only Admins and Supervisors write."""

    async def list_cameras(self, organization_id: UUID, campus_id: UUID) -> list[Camera]: ...

    async def get_camera(self, camera_id: UUID, organization_id: UUID, campus_id: UUID) -> Camera | None: ...

    async def add_camera(self, camera: Camera, location: LocationTarget | None) -> None: ...

    async def update_camera(self, camera: Camera, location: LocationTarget | None) -> None: ...

    async def delete_camera(self, camera_id: UUID, organization_id: UUID, campus_id: UUID) -> bool: ...
