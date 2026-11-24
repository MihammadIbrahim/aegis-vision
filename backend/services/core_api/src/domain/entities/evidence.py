"""The investigation evidence vault (requirements Section 17; decision D-023).

Evidence is a clip or snapshot an Admin or Supervisor saved from an alert or from playback. It is
Tier 2 storage: it never expires, the retention daemon cannot touch it, and only an Admin can
delete it (with their password; every save, view, update and deletion is audited).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.exceptions.domain_errors import ValidationFailed

MAX_TITLE = 200
MAX_NOTES = 2000
MAX_INVESTIGATION_REF = 100


class EvidenceKind(StrEnum):
    CLIP = "CLIP"
    SNAPSHOT = "SNAPSHOT"


class EvidenceSource(StrEnum):
    ALERT = "ALERT"
    PLAYBACK = "PLAYBACK"
    PERSON_SEARCH = "PERSON_SEARCH"  # a reviewed Person Search result's crop (decision D-024)


def clean_title(value: str) -> str:
    title = " ".join(value.split())
    if not title or len(title) > MAX_TITLE:
        raise ValidationFailed(f"The title must be 1 to {MAX_TITLE} characters.", code="INVALID_TITLE")
    return title


def clean_notes(value: str | None) -> str:
    notes = (value or "").strip()
    if len(notes) > MAX_NOTES:
        raise ValidationFailed(f"Notes can be at most {MAX_NOTES} characters.", code="INVALID_NOTES")
    return notes


MAX_TAGS = 10
MAX_TAG = 40
_TAG = re.compile(r"^[\w][\w .-]*$")


def clean_tags(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Up to 10 short labels (letters, digits, spaces, dots, dashes); duplicates ignoring capitals are dropped
    (decision D-024)."""
    tags: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        tag = " ".join(str(raw).split())
        if not tag:
            continue
        if len(tag) > MAX_TAG or not _TAG.fullmatch(tag):
            raise ValidationFailed(
                f"A tag must be 1 to {MAX_TAG} letters, digits, spaces, dots or dashes.", code="INVALID_TAG"
            )
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValidationFailed(f"At most {MAX_TAGS} tags.", code="TOO_MANY_TAGS")
    return tuple(tags)


def clean_investigation_ref(value: str | None) -> str | None:
    ref = " ".join((value or "").split())
    if not ref:
        return None
    if len(ref) > MAX_INVESTIGATION_REF or any(ord(c) < 32 for c in ref):
        raise ValidationFailed(
            f"The investigation reference must be at most {MAX_INVESTIGATION_REF} characters.",
            code="INVALID_INVESTIGATION_REF",
        )
    return ref


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    kind: EvidenceKind
    title: str
    notes: str
    investigation_ref: str | None
    source: EvidenceSource
    alert_id: UUID | None
    camera_id: UUID | None
    camera_name: str
    covers_from: datetime
    covers_to: datetime
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    saved_by: UUID | None
    saved_by_name: str
    saved_at: datetime
    updated_at: datetime
    # Decision D-024 (migration 0025): labels, the case it belongs to, and the camera's location when saved.
    tags: tuple[str, ...] = ()
    incident_id: UUID | None = None
    incident_number: int | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None
    location_label: str | None = None
