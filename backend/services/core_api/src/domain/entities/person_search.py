"""Person Search (requirements Section 16; Org Admin page 7, Supervisor page 7; decision D-024).

A search compares a reference (an uploaded photo, or a person a camera observed) with the appearance
vectors the vision workers recorded in the chosen period and cameras of the campus. Results are
*resemblances*: LIKELY_MATCH or UNCERTAIN by similarity; weaker ones are not shown. Staff review each
result. Only a LIKELY_MATCH can be marked a VERIFIED_MATCH, and that still means "the reviewer judged
it the same person", never a confirmed identity or an accusation. The limits of every search are kept
with it: cameras offline or without Re-ID, and periods without observations.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.exceptions.domain_errors import Conflict


class Band(StrEnum):
    LIKELY_MATCH = "LIKELY_MATCH"
    UNCERTAIN = "UNCERTAIN"


class Review(StrEnum):
    PENDING = "PENDING"
    VERIFIED_MATCH = "VERIFIED_MATCH"
    NOT_A_MATCH = "NOT_A_MATCH"


def band_for(similarity: float, likely: float, uncertain: float) -> Band | None:
    if similarity >= likely:
        return Band.LIKELY_MATCH
    if similarity >= uncertain:
        return Band.UNCERTAIN
    return None


def check_review(band: Band, review: Review) -> None:
    if review is Review.VERIFIED_MATCH and band is not Band.LIKELY_MATCH:
        raise Conflict("An uncertain result cannot be marked as a verified match.", code="UNCERTAIN_CANNOT_BE_VERIFIED")


@dataclass(frozen=True, slots=True)
class Observation:
    id: UUID
    camera_id: UUID
    camera_name: str
    location_label: str | None
    track_id: int
    observed_at: datetime
    quality: float | None
    has_crop: bool
    model_version: str


@dataclass(frozen=True, slots=True)
class Limitation:
    camera_id: UUID | None
    camera_name: str
    issue: str  # CAMERA_OFFLINE | REID_OFF | NO_OBSERVATIONS
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    id: UUID
    search_id: UUID
    rank: int
    observation_id: UUID | None
    camera_id: UUID | None
    camera_name: str
    location_label: str | None
    track_id: int | None
    observed_at: datetime
    similarity: float
    band: Band
    has_crop: bool
    review: Review
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    incident_id: UUID | None
    incident_number: int | None
    evidence_id: UUID | None
    crop_path: str | None = None


@dataclass(frozen=True, slots=True)
class PersonSearch:
    id: UUID
    kind: str  # PHOTO | OBSERVATION
    reference_observation_id: UUID | None
    has_reference: bool
    period_from: datetime
    period_to: datetime
    camera_ids: list[UUID]
    model_version: str | None
    candidate_count: int
    searched_observations: int
    limitations: list[Limitation]
    note: str | None
    incident_id: UUID | None
    created_by_name: str
    created_at: datetime
    candidates: list[Candidate] = field(default_factory=list)
    reference_path: str | None = None
