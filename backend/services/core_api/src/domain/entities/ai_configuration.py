"""AI Configuration (requirements Sections 4 and 14; Org Admin page 17, Supervisor page 14; decision D-024).

Five vision modules. A camera runs a module only when all four levels allow it (decision D-028):
1. the platform (the Super Admin's switches),
2. its organization (the Super Admin can block a module for one organization),
3. its campus (the campus's Admins decide, with their password),
4. the camera itself (Admins and Supervisors select modules per camera; a new camera runs none).
A Supervisor or Admin can never switch on a module a level above does not permit (page 14 item 7). Admins also
decide, per module, whether the campus's Supervisors see and manage it; hiding a module from Supervisors never
switches it off on a camera, and a Supervisor's change keeps the hidden modules as they were.
Weapon and fight/violence detections always raise HIGH-priority alerts (a database rule since
migration 0015); restricted-area alerts are STANDARD.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AiModule(StrEnum):
    HUMAN_DETECTION = "human_detection"
    PERSON_REID = "person_reid"
    WEAPON_DETECTION = "weapon_detection"
    FIGHT_DETECTION = "fight_detection"
    FACE_RECOGNITION = "face_recognition"


MODULE_LABELS = {
    AiModule.HUMAN_DETECTION: "Human detection & single-camera tracking",
    AiModule.PERSON_REID: "Cross-camera person re-identification (Re-ID)",
    AiModule.WEAPON_DETECTION: "Weapon detection",
    AiModule.FIGHT_DETECTION: "Fight / violence detection",
    AiModule.FACE_RECOGNITION: "Restricted-area face recognition",
}

# Alert priority by detection (fixed by requirements 14.3, 14.4 and 15.1).
ALERT_RULES = (
    ("WEAPON", "HIGH", AiModule.WEAPON_DETECTION),
    ("FIGHT_VIOLENCE", "HIGH", AiModule.FIGHT_DETECTION),
    ("RESTRICTED_AREA", "STANDARD", AiModule.FACE_RECOGNITION),
)


@dataclass(frozen=True, slots=True)
class ModuleSet:
    human_detection: bool = True
    person_reid: bool = True
    weapon_detection: bool = True
    fight_detection: bool = True
    face_recognition: bool = False

    def enabled(self) -> list[AiModule]:
        return [AiModule(f.name) for f in fields(self) if getattr(self, f.name)]

    def __and__(self, other: "ModuleSet") -> "ModuleSet":
        return ModuleSet(**{f.name: getattr(self, f.name) and getattr(other, f.name) for f in fields(self)})

    def beyond(self, allowed: "ModuleSet") -> list[AiModule]:
        """Modules switched on here that ``allowed`` does not permit."""
        return [AiModule(f.name) for f in fields(self) if getattr(self, f.name) and not getattr(allowed, f.name)]

    def changes_from(self, before: "ModuleSet") -> dict[str, bool]:
        return {f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) != getattr(before, f.name)}

    def without(self, other: "ModuleSet") -> "ModuleSet":
        """On here and not on in ``other`` (e.g. permitted minus blocked)."""
        return ModuleSet(**{f.name: getattr(self, f.name) and not getattr(other, f.name) for f in fields(self)})

    def keeping(self, before: "ModuleSet", editable: "ModuleSet") -> "ModuleSet":
        """These switches where ``editable`` is on; ``before`` everywhere else (hidden modules stay as they were)."""
        return ModuleSet(
            **{
                f.name: getattr(self, f.name) if getattr(editable, f.name) else getattr(before, f.name)
                for f in fields(self)
            }
        )

    def with_changes(self, changes: Mapping[str, bool]) -> "ModuleSet":
        return ModuleSet(**{f.name: bool(changes.get(f.name, getattr(self, f.name))) for f in fields(self)})

    @classmethod
    def of_keys(cls, keys: Iterable[str]) -> "ModuleSet":
        wanted = set(keys)
        return cls(**{f.name: f.name in wanted for f in fields(cls)})


ALL_ON = ModuleSet(True, True, True, True, True)
NONE = ModuleSet(False, False, False, False, False)

# Long feature names (Super Admin API and the database columns of decision D-028) for the module keys.
FEATURE_NAMES = {
    AiModule.HUMAN_DETECTION: "human_detection_tracking",
    AiModule.PERSON_REID: "person_reid",
    AiModule.WEAPON_DETECTION: "weapon_detection",
    AiModule.FIGHT_DETECTION: "fight_violence_detection",
    AiModule.FACE_RECOGNITION: "restricted_area_face_recognition",
}
# The four core modules the Super Admin blocks per organization (face recognition stays platform-controlled).
CORE_MODULES = (AiModule.HUMAN_DETECTION, AiModule.PERSON_REID, AiModule.WEAPON_DETECTION, AiModule.FIGHT_DETECTION)


def module_key(name: str) -> AiModule | None:
    """A module key from its key or its long feature name."""
    for module, feature in FEATURE_NAMES.items():
        if name in (module.value, feature):
            return module
    return None


@dataclass(frozen=True, slots=True)
class CampusAiSettings:
    modules: ModuleSet
    updated_at: datetime | None = None
    updated_by_name: str | None = None
    supervisor_visible: ModuleSet = ALL_ON


@dataclass(frozen=True, slots=True)
class CameraAiState:
    """One camera on the AI Configuration page."""

    camera_id: UUID
    camera_name: str
    location: str | None
    configured: ModuleSet  # the camera's own switches (none without a row, D-028)
    customized: bool
    stream_state: str  # ONLINE / OFFLINE / UNKNOWN (camera status)
    running: list[str] = field(default_factory=list)  # what a vision worker last reported
    frames_per_second: float | None = None
    last_error: str | None = None
    reported_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CameraAiConfig:
    """What a vision worker should run on one camera (service view)."""

    camera_id: UUID
    organization_id: UUID
    campus_id: UUID
    modules: ModuleSet
    confidence_threshold: float
    frame_sample_fps: int
    detection_cooldown_seconds: int
