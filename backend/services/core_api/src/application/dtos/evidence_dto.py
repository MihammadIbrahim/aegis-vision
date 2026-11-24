"""Evidence vault bodies (decision D-023). File-system paths are never returned."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.use_cases.evidence_use_cases import EvidenceDetails
from src.domain.entities.evidence import EvidenceItem, EvidenceKind, EvidenceSource


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    kind: EvidenceKind
    title: str
    notes: str
    investigation_ref: str | None
    source: EvidenceSource
    alert_id: UUID | None
    camera_id: UUID | None = Field(description="None once the camera was removed; camera_name stays")
    camera_name: str
    covers_from: datetime
    covers_to: datetime
    media_type: str
    size_bytes: int
    sha256: str
    saved_by: UUID | None
    saved_by_name: str
    saved_at: datetime
    updated_at: datetime
    # Decision D-024.
    tags: list[str] = Field(default_factory=list)
    incident_id: UUID | None = None
    incident_reference: str | None = Field(default=None, description="INC-000001 of the linked case")
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None
    location: str | None = Field(default=None, description="The camera's location when the item was saved")

    @classmethod
    def of(cls, e: EvidenceItem) -> "EvidenceResponse":
        return cls(
            id=e.id,
            kind=e.kind,
            title=e.title,
            notes=e.notes,
            investigation_ref=e.investigation_ref,
            source=e.source,
            alert_id=e.alert_id,
            camera_id=e.camera_id,
            camera_name=e.camera_name,
            covers_from=e.covers_from,
            covers_to=e.covers_to,
            media_type=e.media_type,
            size_bytes=e.size_bytes,
            sha256=e.sha256,
            saved_by=e.saved_by,
            saved_by_name=e.saved_by_name,
            saved_at=e.saved_at,
            updated_at=e.updated_at,
            tags=list(e.tags),
            incident_id=e.incident_id,
            incident_reference=f"INC-{e.incident_number:06d}" if e.incident_number is not None else None,
            building_id=e.building_id,
            floor_id=e.floor_id,
            area_id=e.area_id,
            location=e.location_label,
        )


class _Details(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    investigation_ref: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = Field(default=None, max_length=10, description="Up to 10 labels")

    def to_details(self) -> EvidenceDetails:
        return EvidenceDetails(self.title, self.notes, self.investigation_ref, self.tags)


class _Save(_Details):
    incident_id: UUID | None = Field(default=None, description="Link the saved item to this open case")

    def to_details(self) -> EvidenceDetails:
        return EvidenceDetails(self.title, self.notes, self.investigation_ref, self.tags, self.incident_id)


class SaveFromAlertRequest(_Save):
    alert_id: UUID
    kind: EvidenceKind = EvidenceKind.CLIP


class SaveFromPlaybackRequest(_Save):
    segment_id: UUID
    kind: EvidenceKind = EvidenceKind.CLIP
    start_offset_seconds: float = Field(ge=0, le=86400)
    end_offset_seconds: float | None = Field(default=None, ge=0, le=86400, description="Clips only")


class EvidenceUpdateRequest(_Details):
    pass


class EvidenceDeleteRequest(BaseModel):
    """Admins only; confirmed with the Admin's own password."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
