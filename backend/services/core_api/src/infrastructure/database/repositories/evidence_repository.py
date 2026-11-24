"""The evidence vault index (migration 0023). Files live in the ``evidence`` storage tier."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.evidence import EvidenceItem, EvidenceKind, EvidenceSource
from src.domain.repositories.i_evidence_repo import AlertSource, EvidenceFilter

_COLUMNS = """e.id, e.organization_id, e.campus_id, e.kind, e.title, e.notes, e.investigation_ref, e.source, e.alert_id,
       e.camera_id, e.camera_name, e.covers_from, e.covers_to, e.relative_path, e.media_type, e.size_bytes, e.sha256,
       e.saved_by, e.saved_by_name, e.saved_at, e.updated_at, e.tags, e.incident_id,
       (SELECT i.number FROM incidents i WHERE i.id = e.incident_id) AS incident_number,
       e.building_id, e.floor_id, e.area_id, e.building_name, e.floor_name, e.area_name"""
_SCOPE = "e.organization_id = :org AND e.campus_id = :campus"
_UUIDS = (
    "id", "org", "campus", "alert_id", "camera_id", "saved_by", "building_id", "floor_id", "area_id", "incident_id"
)  # fmt: skip
# The camera's location at the moment of saving (names copied, decision D-024).
_SNAPSHOT_LOCATION = """UPDATE evidence_items e
          SET building_id = b.id, floor_id = f.id, area_id = a.id,
              building_name = b.name, floor_name = f.name, area_name = a.name
         FROM cameras c
         LEFT JOIN areas a ON a.id = c.area_id
         LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, a.floor_id)
         LEFT JOIN buildings b ON b.id = COALESCE(c.building_id, f.building_id, a.building_id)
        WHERE e.id = :id AND c.id = e.camera_id AND b.id IS NOT NULL"""


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _item(r: Any) -> EvidenceItem:
    return EvidenceItem(
        id=r.id,
        organization_id=r.organization_id,
        campus_id=r.campus_id,
        kind=EvidenceKind(r.kind),
        title=r.title,
        notes=r.notes,
        investigation_ref=r.investigation_ref,
        source=EvidenceSource(r.source),
        alert_id=r.alert_id,
        camera_id=r.camera_id,
        camera_name=r.camera_name,
        covers_from=r.covers_from,
        covers_to=r.covers_to,
        relative_path=r.relative_path,
        media_type=r.media_type,
        size_bytes=r.size_bytes,
        sha256=r.sha256,
        saved_by=r.saved_by,
        saved_by_name=r.saved_by_name,
        saved_at=r.saved_at,
        updated_at=r.updated_at,
        tags=tuple(r.tags or ()),
        incident_id=r.incident_id,
        incident_number=r.incident_number,
        building_id=r.building_id,
        floor_id=r.floor_id,
        area_id=r.area_id,
        location_label=" › ".join(p for p in (r.building_name, r.floor_name, r.area_name) if p) or None,
    )


class SqlEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self, organization_id: UUID, campus_id: UUID, where: EvidenceFilter) -> list[EvidenceItem]:
        clauses = [_SCOPE]
        params: dict[str, Any] = {
            "org": organization_id,
            "campus": campus_id,
            "limit": where.limit,
            "offset": where.offset,
        }
        if where.text:
            clauses.append(
                "(e.title ILIKE :q ESCAPE '\\' OR e.notes ILIKE :q ESCAPE '\\' "
                "OR e.investigation_ref ILIKE :q ESCAPE '\\' "
                "OR e.camera_name ILIKE :q ESCAPE '\\' OR e.id::text = :exact "
                "OR EXISTS (SELECT 1 FROM unnest(e.tags) t WHERE t ILIKE :q ESCAPE '\\'))"
            )
            params["q"] = _like(where.text.strip())
            params["exact"] = where.text.strip()
        if where.kind:
            clauses.append("e.kind = :kind")
            params["kind"] = where.kind
        if where.camera_id is not None:
            clauses.append("e.camera_id = :camera_id")
            params["camera_id"] = where.camera_id
        if where.alert_id is not None:
            clauses.append("e.alert_id = :alert_id")
            params["alert_id"] = where.alert_id
        if where.since is not None:
            clauses.append("e.covers_to >= :since")
            params["since"] = where.since
        if where.until is not None:
            clauses.append("e.covers_from <= :until")
            params["until"] = where.until
        if where.tag:
            clauses.append("EXISTS (SELECT 1 FROM unnest(e.tags) t WHERE lower(t) = lower(:tag))")
            params["tag"] = where.tag.strip()
        for level in ("building_id", "floor_id", "area_id", "incident_id"):
            value = getattr(where, level)
            if value is not None:
                clauses.append(f"e.{level} = :{level}")
                params[level] = value
        rows = await self._session.execute(
            _sql(
                f"SELECT {_COLUMNS} FROM evidence_items e WHERE {' AND '.join(clauses)} "
                "ORDER BY e.saved_at DESC, e.id LIMIT :limit OFFSET :offset",
                params,
            ),
            params,
        )
        return [_item(r) for r in rows]

    async def get(self, evidence_id: UUID, organization_id: UUID, campus_id: UUID) -> EvidenceItem | None:
        params = {"id": evidence_id, "org": organization_id, "campus": campus_id}
        row = (
            await self._session.execute(
                _sql(f"SELECT {_COLUMNS} FROM evidence_items e WHERE {_SCOPE} AND e.id = :id", params), params
            )
        ).first()
        return None if row is None else _item(row)

    async def add(self, item: EvidenceItem) -> None:
        params = {
            "id": item.id,
            "org": item.organization_id,
            "campus": item.campus_id,
            "kind": item.kind.value,
            "title": item.title,
            "notes": item.notes,
            "investigation_ref": item.investigation_ref,
            "source": item.source.value,
            "alert_id": item.alert_id,
            "camera_id": item.camera_id,
            "camera_name": item.camera_name,
            "covers_from": item.covers_from,
            "covers_to": item.covers_to,
            "relative_path": item.relative_path,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "saved_by": item.saved_by,
            "saved_by_name": item.saved_by_name,
            "tags": list(item.tags),
        }
        await self._session.execute(
            _sql(
                "INSERT INTO evidence_items (id, organization_id, campus_id, kind, title, notes, investigation_ref, "
                "source, alert_id, camera_id, camera_name, covers_from, covers_to, relative_path, media_type, "
                "size_bytes, sha256, saved_by, saved_by_name, tags) VALUES (:id, :org, :campus, :kind, :title, :notes, "
                ":investigation_ref, :source, :alert_id, :camera_id, :camera_name, :covers_from, :covers_to, "
                ":relative_path, :media_type, :size_bytes, "
                ":sha256, :saved_by, :saved_by_name, :tags)",
                params,
            ),
            params,
        )
        if item.camera_id is not None:
            snapshot = {"id": item.id}
            await self._session.execute(_sql(_SNAPSHOT_LOCATION, snapshot), snapshot)

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
    ) -> bool:
        params = {
            "id": evidence_id,
            "org": organization_id,
            "campus": campus_id,
            "title": title,
            "notes": notes,
            "investigation_ref": investigation_ref,
            "tags": None if tags is None else list(tags),
        }
        row = (
            await self._session.execute(
                _sql(
                    "UPDATE evidence_items e SET title = :title, notes = :notes, "
                    "investigation_ref = :investigation_ref, tags = COALESCE(CAST(:tags AS text[]), e.tags), "
                    f"updated_at = now() WHERE {_SCOPE} AND e.id = :id RETURNING e.id",
                    params,
                ),
                params,
            )
        ).first()
        return row is not None

    async def delete(self, evidence_id: UUID, organization_id: UUID, campus_id: UUID) -> bool:
        params = {"id": evidence_id, "org": organization_id, "campus": campus_id}
        row = (
            await self._session.execute(
                _sql(f"DELETE FROM evidence_items e WHERE {_SCOPE} AND e.id = :id RETURNING e.id", params), params
            )
        ).first()
        return row is not None

    async def alert_source(self, alert_id: UUID, organization_id: UUID, campus_id: UUID) -> AlertSource | None:
        params = {"id": alert_id, "org": organization_id, "campus": campus_id}
        row = (
            await self._session.execute(
                _sql(
                    "SELECT a.id, a.camera_id, a.camera_name, a.alert_type, a.detected_at, a.last_detected_at, "
                    "a.snapshot_ref "
                    "FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus AND a.id = :id",
                    params,
                ),
                params,
            )
        ).first()
        if row is None:
            return None
        return AlertSource(
            row.id,
            row.camera_id,
            row.camera_name,
            row.alert_type,
            row.detected_at,
            row.last_detected_at,
            row.snapshot_ref,
        )
