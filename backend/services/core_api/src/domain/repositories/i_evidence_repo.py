"""The evidence vault (migration 0023; decision D-023). Row-Level Security repeats the campus
rule; Admins and Supervisors save, view and update, only Admins delete."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.entities.evidence import EvidenceItem


@dataclass(frozen=True, slots=True)
class AlertSource:
    """What an alert contributes when evidence is saved from it."""

    alert_id: UUID
    camera_id: UUID
    camera_name: str
    alert_type: str
    detected_at: datetime
    last_detected_at: datetime
    snapshot_ref: str | None


@dataclass(frozen=True, slots=True)
class EvidenceFilter:
    text: str | None = None
    kind: str | None = None
    camera_id: UUID | None = None
    alert_id: UUID | None = None
    since: datetime | None = None  # items whose covered period overlaps [since, until]
    until: datetime | None = None
    limit: int = 100
    offset: int = 0
    # Decision D-024: a tag, a place (at or below) and a case.
    tag: str | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None
    incident_id: UUID | None = None


class IEvidenceRepository(Protocol):
    async def list_items(self, organization_id: UUID, campus_id: UUID, where: EvidenceFilter) -> list[EvidenceItem]: ...

    async def get(self, evidence_id: UUID, organization_id: UUID, campus_id: UUID) -> EvidenceItem | None: ...

    async def add(self, item: EvidenceItem) -> None: ...

    async def update(
        self,
        evidence_id: UUID,
        organization_id: UUID,
        campus_id: UUID,
        *,
        title: str,
        notes: str,
        investigation_ref: str | None,
        tags: tuple[str, ...] | None = None,
    ) -> bool: ...

    async def delete(self, evidence_id: UUID, organization_id: UUID, campus_id: UUID) -> bool: ...

    async def alert_source(self, alert_id: UUID, organization_id: UUID, campus_id: UUID) -> AlertSource | None: ...
