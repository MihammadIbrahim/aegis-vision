"""Camera bodies. No credentials are ever accepted or returned (decision D-012)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.location_dto import (
    LocationPathResponse,
    LocationSelectionRequest,
)
from src.application.use_cases.camera_use_cases import CameraDetails
from src.domain.entities.ai_configuration import AiModule
from src.domain.entities.camera import Camera, ConnectionType


class CameraRequest(BaseModel):
    """Add or replace a camera. ``location`` is the building → optional floor → optional area
    selection (the same as ``PUT /locations/camera-links``); null means no location."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(max_length=200)
    connection_type: ConnectionType = Field(description="RTSP or ONVIF")
    stream_url: str | None = Field(
        default=None,
        max_length=2048,
        description="rtsp(s):// for RTSP, http(s):// for ONVIF; no user name or password",
    )
    location: LocationSelectionRequest | None = None
    ai_modules: dict[AiModule, bool] | None = Field(
        default=None,
        max_length=5,
        description="AI features for this camera (module key → selected). Omitted on add: none run (D-028); "
        "omitted on edit: unchanged; keys left out keep their value",
    )

    def to_details(self) -> CameraDetails:
        selection = None if self.location is None else self.location.to_selection()
        modules = None if self.ai_modules is None else {k.value: v for k, v in self.ai_modules.items()}
        return CameraDetails(self.display_name, self.connection_type, self.stream_url, selection, modules)


class CameraResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(description="The Camera ID (system-generated)")
    campus_id: UUID
    display_name: str
    connection_type: ConnectionType
    stream_url: str | None
    environment: Literal["INDOOR", "OUTDOOR"] | None = Field(description="From the linked building or perimeter")
    location: LocationPathResponse | None
    created_at: datetime | None
    updated_at: datetime | None
    ai_modules: list[AiModule] | None = Field(
        default=None, description="Admins and Supervisors: the AI features selected for this camera (visible to them)"
    )
    ai_effective_modules: list[AiModule] | None = Field(
        default=None, description="What it runs: platform AND NOT organization-blocked AND campus AND camera"
    )

    @classmethod
    def from_entity(cls, camera: Camera, ai: tuple[list[str], list[str]] | None = None) -> "CameraResponse":
        environment: Literal["INDOOR", "OUTDOOR"] | None = (
            None if camera.environment is None else "OUTDOOR" if camera.environment == "OUTDOOR" else "INDOOR"
        )
        return cls(
            id=camera.id,
            campus_id=camera.campus_id,
            display_name=camera.display_name,
            connection_type=camera.connection_type,
            stream_url=camera.stream_url,
            environment=environment,
            location=None if camera.location is None else LocationPathResponse.of(camera.location),
            created_at=camera.created_at,
            updated_at=camera.updated_at,
            ai_modules=None if ai is None else [AiModule(m) for m in ai[0]],
            ai_effective_modules=None if ai is None else [AiModule(m) for m in ai[1]],
        )
