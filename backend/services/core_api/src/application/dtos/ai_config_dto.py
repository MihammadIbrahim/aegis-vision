"""AI Configuration bodies (decisions D-024 and D-028)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.ai_config_use_cases import AiConfigurationView, CameraView
from src.domain.entities.ai_configuration import ALERT_RULES, MODULE_LABELS, AiModule, CameraAiConfig, ModuleSet


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModuleSetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_detection: bool
    person_reid: bool
    weapon_detection: bool
    fight_detection: bool
    face_recognition: bool

    @classmethod
    def of(cls, m: ModuleSet) -> "ModuleSetBody":
        return cls(
            human_detection=m.human_detection,
            person_reid=m.person_reid,
            weapon_detection=m.weapon_detection,
            fight_detection=m.fight_detection,
            face_recognition=m.face_recognition,
        )

    def to_modules(self) -> ModuleSet:
        return ModuleSet(
            self.human_detection, self.person_reid, self.weapon_detection, self.fight_detection, self.face_recognition
        )


class ModulesChangeRequest(ModuleSetBody):
    current_password: str = Field(min_length=1, max_length=256)


class CampusModulesChangeRequest(ModulesChangeRequest):
    supervisor_visible_modules: ModuleSetBody | None = Field(
        default=None, description="Admins: which modules the campus's Supervisors see and manage (omit to keep)"
    )


class BulkCameraAiRequest(BaseModel):
    """Assign AI features to many cameras of the caller's campus in one step (D-028)."""

    model_config = ConfigDict(extra="forbid")

    camera_ids: list[UUID] = Field(min_length=1, max_length=200)
    mode: Literal["ADD", "REMOVE", "SET"] = Field(
        default="ADD", description="ADD switches the modules on, REMOVE off, SET replaces the camera's switches"
    )
    modules: dict[AiModule, bool] = Field(description="Module key → selected", max_length=5)
    current_password: str = Field(min_length=1, max_length=256)


class ModuleInfo(_Out):
    key: AiModule
    label: str


class AlertRule(_Out):
    detection: str
    priority: Literal["HIGH", "STANDARD"]
    module: AiModule


class CameraAiResponse(_Out):
    camera_id: UUID
    camera_name: str
    location: str | None
    configured: ModuleSetBody = Field(description="The camera's own switches")
    effective: ModuleSetBody = Field(
        description="What it may run: platform AND NOT organization-blocked AND campus AND camera"
    )
    customized: bool
    stream_state: str = Field(description="ONLINE, OFFLINE or UNKNOWN (the video stream)")
    running: list[str] = Field(description="Modules a vision worker last reported running")
    running_now: bool = Field(description="A worker reported within the last minute")
    frames_per_second: float | None
    last_error: str | None
    reported_at: datetime | None
    updated_by_name: str | None
    updated_at: datetime | None

    @classmethod
    def of(cls, c: CameraView) -> "CameraAiResponse":
        s = c.state
        return cls(
            camera_id=s.camera_id,
            camera_name=s.camera_name,
            location=s.location,
            configured=ModuleSetBody.of(c.configured),
            effective=ModuleSetBody.of(c.effective),
            customized=s.customized,
            stream_state=s.stream_state,
            running=s.running,
            running_now=c.running_now,
            frames_per_second=s.frames_per_second,
            last_error=s.last_error,
            reported_at=s.reported_at,
            updated_by_name=s.updated_by_name,
            updated_at=s.updated_at,
        )


class AiConfigurationResponse(_Out):
    modules: list[ModuleInfo]
    platform: ModuleSetBody = Field(description="Permitted by the Super Admin")
    campus: ModuleSetBody = Field(description="The campus switches")
    campus_permitted: ModuleSetBody = Field(description="Platform AND campus: what cameras may run")
    campus_customized: bool
    campus_updated_at: datetime | None
    campus_updated_by_name: str | None
    alert_rules: list[AlertRule]
    cameras: list[CameraAiResponse]
    available_modules: list[AiModule] = Field(description="Modules the caller may switch on for a camera")
    org_blocked: ModuleSetBody = Field(description="Blocked for the organization by the Super Admin")
    blocked_by_platform: list[AiModule] = Field(description="The blocked modules, as keys")
    supervisor_visible_modules: ModuleSetBody | None = Field(
        description="Admins only: the modules the campus's Supervisors see and manage"
    )

    @classmethod
    def of(cls, v: AiConfigurationView) -> "AiConfigurationResponse":
        visible = set(v.visible.enabled())
        return cls(
            modules=[ModuleInfo(key=m, label=MODULE_LABELS[m]) for m in AiModule if m in visible],
            platform=ModuleSetBody.of(v.platform),
            campus=ModuleSetBody.of(v.campus),
            campus_permitted=ModuleSetBody.of(v.campus_permitted),
            campus_customized=v.campus_customized,
            campus_updated_at=v.campus_updated_at,
            campus_updated_by_name=v.campus_updated_by_name,
            alert_rules=[
                AlertRule(detection=d, priority=p, module=m)  # type: ignore[arg-type]
                for d, p, m in ALERT_RULES
                if m in visible
            ],
            cameras=[CameraAiResponse.of(c) for c in v.cameras],
            available_modules=v.available.enabled(),
            org_blocked=ModuleSetBody.of(v.org_blocked),
            blocked_by_platform=v.org_blocked.enabled(),
            supervisor_visible_modules=None
            if v.supervisor_visible is None
            else ModuleSetBody.of(v.supervisor_visible),
        )


class BulkCameraAiResponse(_Out):
    mode: Literal["ADD", "REMOVE", "SET"]
    updated_count: int
    cameras: list[CameraAiResponse] = Field(description="The selected cameras after the change")


class ServiceAiConfigResponse(_Out):
    camera_id: UUID
    organization_id: UUID
    campus_id: UUID
    modules: list[str] = Field(description="The modules to run on this camera now")
    confidence_threshold: float
    frame_sample_fps: int
    detection_cooldown_seconds: int

    @classmethod
    def of(cls, c: CameraAiConfig) -> "ServiceAiConfigResponse":
        return cls(
            camera_id=c.camera_id,
            organization_id=c.organization_id,
            campus_id=c.campus_id,
            modules=[m.value for m in c.modules.enabled()],
            confidence_threshold=c.confidence_threshold,
            frame_sample_fps=c.frame_sample_fps,
            detection_cooldown_seconds=c.detection_cooldown_seconds,
        )


class AiStatusReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modules: list[AiModule] = Field(default_factory=list, max_length=5)
    frames_per_second: float | None = Field(default=None, ge=0, le=1000)
    error: str | None = Field(default=None, max_length=500)
    worker: str | None = Field(default=None, max_length=100)


class OverlayDetection(_Out):
    label: str
    confidence: float | None
    box: list[float] = Field(description="x1, y1, x2, y2 as fractions of the frame (0–1)")
    track_id: int | None = None
    module: str | None = None


class OverlayResponse(_Out):
    camera_id: UUID
    captured_at: datetime | None
    stale: bool = Field(description="True when the newest overlay is older than the live frame limit")
    frame_width: int | None
    frame_height: int | None
    modules: list[str]
    detections: list[OverlayDetection]
