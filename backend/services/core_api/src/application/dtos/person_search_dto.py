"""Person Search bodies (decision D-024). Never an identity: results are resemblances with a band."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.domain.entities.person_search import Band, Candidate, Limitation, Observation, PersonSearch, Review


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationResponse(_Out):
    id: UUID
    camera_id: UUID
    camera_name: str
    location: str | None
    track_id: int
    observed_at: datetime
    quality: float | None
    has_crop: bool
    model_version: str

    @classmethod
    def of(cls, o: Observation) -> "ObservationResponse":
        return cls(
            id=o.id,
            camera_id=o.camera_id,
            camera_name=o.camera_name,
            location=o.location_label,
            track_id=o.track_id,
            observed_at=o.observed_at,
            quality=o.quality,
            has_crop=o.has_crop,
            model_version=o.model_version,
        )


class LimitationResponse(_Out):
    camera_id: UUID | None
    camera_name: str
    issue: str = Field(description="CAMERA_OFFLINE, REID_OFF or NO_OBSERVATIONS")
    detail: str | None

    @classmethod
    def of(cls, x: Limitation) -> "LimitationResponse":
        return cls(camera_id=x.camera_id, camera_name=x.camera_name, issue=x.issue, detail=x.detail)


class CandidateResponse(_Out):
    id: UUID
    search_id: UUID
    rank: int
    observation_id: UUID | None
    camera_id: UUID | None
    camera_name: str
    location: str | None
    track_id: int | None
    observed_at: datetime
    similarity: float = Field(description="Cosine similarity of the appearance vectors (−1 to 1)")
    band: Band = Field(description="LIKELY_MATCH or UNCERTAIN; never a confirmed identity")
    has_crop: bool
    review: Review
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    incident_id: UUID | None
    incident_reference: str | None
    evidence_id: UUID | None

    @classmethod
    def of(cls, c: Candidate) -> "CandidateResponse":
        return cls(
            id=c.id,
            search_id=c.search_id,
            rank=c.rank,
            observation_id=c.observation_id,
            camera_id=c.camera_id,
            camera_name=c.camera_name,
            location=c.location_label,
            track_id=c.track_id,
            observed_at=c.observed_at,
            similarity=c.similarity,
            band=c.band,
            has_crop=c.has_crop,
            review=c.review,
            reviewed_by_name=c.reviewed_by_name,
            reviewed_at=c.reviewed_at,
            review_note=c.review_note,
            incident_id=c.incident_id,
            incident_reference=f"INC-{c.incident_number:06d}" if c.incident_number is not None else None,
            evidence_id=c.evidence_id,
        )


class SearchResponse(_Out):
    id: UUID
    kind: str = Field(description="PHOTO or OBSERVATION")
    reference_observation_id: UUID | None
    has_reference: bool
    period_from: datetime
    period_to: datetime
    camera_ids: list[UUID]
    model_version: str | None
    candidate_count: int
    searched_observations: int = Field(description="Observations compared in the period and cameras")
    complete: bool = Field(description="False when cameras were offline, without Re-ID or without observations")
    limitations: list[LimitationResponse]
    note: str | None
    incident_id: UUID | None
    created_by_name: str
    created_at: datetime
    candidates: list[CandidateResponse]

    @classmethod
    def of(cls, s: PersonSearch) -> "SearchResponse":
        return cls(
            id=s.id,
            kind=s.kind,
            reference_observation_id=s.reference_observation_id,
            has_reference=s.has_reference,
            period_from=s.period_from,
            period_to=s.period_to,
            camera_ids=s.camera_ids,
            model_version=s.model_version,
            candidate_count=s.candidate_count,
            searched_observations=s.searched_observations,
            complete=not s.limitations,
            limitations=[LimitationResponse.of(x) for x in s.limitations],
            note=s.note,
            incident_id=s.incident_id,
            created_by_name=s.created_by_name,
            created_at=s.created_at,
            candidates=[CandidateResponse.of(c) for c in s.candidates],
        )


class ObservationSearchRequest(_In):
    observation_id: UUID
    since: datetime | None = None
    until: datetime | None = None
    camera_ids: list[UUID] = Field(default_factory=list, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class ReviewRequest(_In):
    review: Review
    note: str | None = Field(default=None, max_length=1000)


class LinkIncidentRequest(_In):
    incident_id: UUID


class SaveEvidenceRequest(_In):
    title: str = Field(max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=10)


class SavedEvidenceResponse(_Out):
    evidence_id: UUID


class ModelStatusResponse(_Out):
    photo_search_available: bool
    model_version: str | None
    reason: str | None
    likely_threshold: float
    uncertain_threshold: float


class ObservationIntake(_In):
    camera_id: UUID
    track_id: int = Field(ge=0)
    observed_at: datetime
    embedding: list[float] = Field(min_length=512, max_length=512)
    model_version: str = Field(min_length=1, max_length=100)
    quality: float | None = Field(default=None, ge=0, le=1)
    crop_jpeg_base64: str | None = Field(default=None, max_length=700_000)


class IntakeResponse(_Out):
    observation_id: UUID


def as_dict(response: BaseModel) -> dict[str, Any]:
    return response.model_dump(mode="json")
