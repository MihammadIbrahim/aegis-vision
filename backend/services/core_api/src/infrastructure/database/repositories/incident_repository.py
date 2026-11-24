"""Incidents & Investigations in PostgreSQL (migration 0025). Runs for the signed-in user under Row-Level
Security, so another campus's case, alert, report or evidence is simply not found."""

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.incident import (
    FootageGap,
    Incident,
    IncidentEvent,
    IncidentLocation,
    IncidentMessage,
    IncidentPage,
    IncidentPriority,
    IncidentStatus,
    LinkedAlert,
    LinkedEvidence,
    LinkedReport,
    Participant,
    StaffMember,
)
from src.domain.entities.user import User
from src.domain.repositories.i_incident_repo import CameraRef, IncidentFilter, NewIncident

_UUIDS = (
    "id",
    "org",
    "campus",
    "actor",
    "incident",
    "alert",
    "report",
    "evidence",
    "camera",
    "user",
    "guard",
    "assigned",
    "building",
    "floor",
    "area",
)
# A recording covering less than this share of a window counts as partial.
_COVERED_ENOUGH = 0.95
# Changeable case details: column -> parameter name (the identifiers are typed as UUIDs by ``_sql``).
_DETAIL_PARAMS = {
    "title": "title",
    "description": "description",
    "priority": "priority",
    "occurred_at": "occurred_at",
    "camera_id": "camera",
    "camera_name": "camera_name",
    "building_id": "building",
    "floor_id": "floor",
    "area_id": "area",
    "building_name": "building_name",
    "floor_name": "floor_name",
    "area_name": "area_name",
}


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    used = set(re.findall(r"(?<!:):([a-z_]+)\b", statement))
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params and n in used))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _label(*parts: str | None) -> str | None:
    return " › ".join(p for p in parts if p) or None


_INCIDENT_COLUMNS = """i.id, i.organization_id, i.campus_id, i.number, i.title, i.description, i.status, i.priority,
       i.occurred_at, i.camera_id, i.camera_name, i.building_id, i.floor_id, i.area_id, i.building_name,
       i.floor_name, i.area_name, i.assigned_to, i.assigned_name, i.created_by, i.created_by_name,
       i.resolution_note, i.closed_at, i.created_at, i.updated_at"""
_COUNTS = """(SELECT count(*) FROM incident_alerts ia WHERE ia.incident_id = i.id) AS alert_count,
       (SELECT count(*) FROM manual_reports m WHERE m.incident_id = i.id) AS report_count,
       (SELECT count(*) FROM evidence_items e WHERE e.incident_id = i.id) AS evidence_count"""


def _incident(r: Any, counts: bool = True) -> Incident:
    return Incident(
        id=r.id,
        organization_id=r.organization_id,
        campus_id=r.campus_id,
        number=r.number,
        title=r.title,
        description=r.description,
        status=IncidentStatus(r.status),
        priority=IncidentPriority(r.priority),
        occurred_at=r.occurred_at,
        camera_id=r.camera_id,
        camera_name=r.camera_name,
        location=IncidentLocation(r.building_id, r.floor_id, r.area_id, r.building_name, r.floor_name, r.area_name),
        assigned_to=r.assigned_to,
        assigned_name=r.assigned_name,
        created_by=r.created_by,
        created_by_name=r.created_by_name,
        resolution_note=r.resolution_note,
        closed_at=r.closed_at,
        created_at=r.created_at,
        updated_at=r.updated_at,
        alert_count=int(r.alert_count) if counts else 0,
        report_count=int(r.report_count) if counts else 0,
        evidence_count=int(r.evidence_count) if counts else 0,
    )


class SqlIncidentRepository:
    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._session = session
        self._actor = actor
        self._scope = {"org": actor.organization_id, "campus": actor.campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        values = {**self._scope, **(params or {})}
        used = set(re.findall(r"(?<!:):([a-z_]+)\b", statement))
        values = {k: v for k, v in values.items() if k in used}
        return await self._session.execute(_sql(statement, values), values)

    # --- Cases -----------------------------------------------------------------------------------------------

    async def create(self, new: NewIncident) -> Incident:
        # One number after another per campus, even when two cases are opened at the same moment.
        await self._run(
            "SELECT pg_advisory_xact_lock(hashtextextended('incident-number:' || CAST(:campus AS text), 0))"
        )
        number = (
            await self._run(
                "SELECT COALESCE(max(number), 0) + 1 FROM incidents "
                "WHERE organization_id = :org AND campus_id = :campus"
            )
        ).scalar_one()
        loc = new.location
        await self._run(
            """INSERT INTO incidents (id, organization_id, campus_id, number, title, description, priority, occurred_at,
                   camera_id, camera_name, building_id, floor_id, area_id, building_name, floor_name, area_name,
                   assigned_to, assigned_name, created_by, created_by_name)
               VALUES (:id, :org, :campus, :number, :title, :description, :priority, :occurred_at, :camera,
                   :camera_name,
                   :building, :floor, :area, :building_name, :floor_name, :area_name, :assigned, :assigned_name,
                   :actor, :created_by_name)""",
            {
                "id": new.id,
                "number": number,
                "title": new.title,
                "description": new.description,
                "priority": new.priority,
                "occurred_at": new.occurred_at,
                "camera": new.camera_id,
                "camera_name": new.camera_name,
                "building": loc.building_id,
                "floor": loc.floor_id,
                "area": loc.area_id,
                "building_name": loc.building_name,
                "floor_name": loc.floor_name,
                "area_name": loc.area_name,
                "assigned": new.assigned_to,
                "assigned_name": new.assigned_name,
                "actor": new.created_by,
                "created_by_name": new.created_by_name,
            },
        )
        created = await self.get(new.id)
        assert created is not None
        return created

    def _where(self, where: IncidentFilter, *, with_status: bool) -> tuple[str, dict[str, Any]]:
        clauses = ["i.organization_id = :org", "i.campus_id = :campus"]
        params: dict[str, Any] = {}
        if with_status and where.statuses:
            clauses.append("i.status = ANY (:statuses)")
            params["statuses"] = list(where.statuses)
        if where.priority:
            clauses.append("i.priority = :priority")
            params["priority"] = where.priority
        if where.unassigned:
            clauses.append("i.assigned_to IS NULL")
        elif where.assigned_to is not None:
            clauses.append("i.assigned_to = :assigned")
            params["assigned"] = where.assigned_to
        if where.since is not None:
            clauses.append("i.occurred_at >= :since")
            params["since"] = where.since
        if where.until is not None:
            clauses.append("i.occurred_at <= :until")
            params["until"] = where.until
        if where.text and where.text.strip():
            needle = where.text.strip()
            searchable = (
                "i.title ILIKE :q ESCAPE '\\' OR i.description ILIKE :q ESCAPE '\\' "
                "OR i.camera_name ILIKE :q ESCAPE '\\' OR i.building_name ILIKE :q ESCAPE '\\' "
                "OR i.floor_name ILIKE :q ESCAPE '\\' OR i.area_name ILIKE :q ESCAPE '\\' "
                "OR i.created_by_name ILIKE :q ESCAPE '\\' OR i.assigned_name ILIKE :q ESCAPE '\\'"
            )
            number = re.fullmatch(r"(?i)(?:INC-?)?0*([0-9]{1,9})", needle)
            if number:
                searchable += " OR i.number = :number"
                params["number"] = int(number.group(1))
            clauses.append(f"({searchable})")
            params["q"] = _like(needle)
        return " AND ".join(clauses), params

    async def page(self, where: IncidentFilter) -> IncidentPage:
        counted_where, counted_params = self._where(where, with_status=False)
        counted = await self._run(
            f"SELECT i.status, count(*) FROM incidents i WHERE {counted_where} GROUP BY i.status", counted_params
        )
        counts = {status.value: 0 for status in IncidentStatus}
        counts.update({row[0]: int(row[1]) for row in counted})
        clause, params = self._where(where, with_status=True)
        total = (await self._run(f"SELECT count(*) FROM incidents i WHERE {clause}", params)).scalar_one()
        rows = await self._run(
            f"""SELECT {_INCIDENT_COLUMNS}, {_COUNTS} FROM incidents i WHERE {clause}
                ORDER BY CASE WHEN i.status IN ('RESOLVED', 'DISMISSED') THEN 1 ELSE 0 END,
                         CASE i.priority WHEN 'HIGH' THEN 0 ELSE 1 END, i.updated_at DESC, i.id
                LIMIT :limit OFFSET :offset""",
            {**params, "limit": where.limit, "offset": where.offset},
        )
        return IncidentPage(items=[_incident(r) for r in rows], total=int(total), counts=counts)

    async def get(self, incident_id: UUID, *, lock: bool = False) -> Incident | None:
        suffix = " FOR UPDATE OF i" if lock else ""
        row = (
            await self._run(
                f"""SELECT {_INCIDENT_COLUMNS}, {_COUNTS} FROM incidents i
                    WHERE i.id = :incident AND i.organization_id = :org AND i.campus_id = :campus{suffix}""",
                {"incident": incident_id},
            )
        ).first()
        return None if row is None else _incident(row)

    async def update_details(self, incident_id: UUID, changes: dict[str, Any]) -> None:
        assert set(changes) <= set(_DETAIL_PARAMS), changes
        if not changes:
            return
        sets = ", ".join(f"{column} = :{_DETAIL_PARAMS[column]}" for column in changes)
        params = {_DETAIL_PARAMS[column]: value for column, value in changes.items()}
        await self._run(
            f"UPDATE incidents SET {sets}, updated_at = now() WHERE id = :incident", {**params, "incident": incident_id}
        )

    async def set_status(self, incident_id: UUID, status: str, note: str | None, closed: bool) -> None:
        await self._run(
            """UPDATE incidents SET status = :status, updated_at = now(),
                   closed_at = CASE WHEN :closed THEN now() ELSE NULL END,
                   resolution_note = CASE WHEN :closed THEN :note ELSE resolution_note END
               WHERE id = :incident""",
            {"incident": incident_id, "status": status, "note": note, "closed": closed},
        )

    async def set_assignee(self, incident_id: UUID, user_id: UUID | None, name: str | None) -> None:
        await self._run(
            "UPDATE incidents SET assigned_to = :assigned, assigned_name = :name, updated_at = now() "
            "WHERE id = :incident",
            {"incident": incident_id, "assigned": user_id, "name": name},
        )

    async def touch(self, incident_id: UUID) -> None:
        await self._run("UPDATE incidents SET updated_at = now() WHERE id = :incident", {"incident": incident_id})

    async def add_event(
        self,
        incident_id: UUID,
        kind: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        note: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        actor = self._actor
        await self._run(
            """INSERT INTO incident_events (id, incident_id, organization_id, campus_id, kind, actor_user_id,
                   actor_name,
                   actor_role, from_status, to_status, note, details)
               VALUES (:id, :incident, :org, :campus, :kind, :actor, :actor_name, :role, :from_status, :to_status,
                   :note, CAST(:details AS jsonb))""",
            {
                "id": uuid4(),
                "incident": incident_id,
                "kind": kind,
                "actor": actor.id,
                "actor_name": actor.full_name,
                "role": actor.role.value,
                "from_status": from_status,
                "to_status": to_status,
                "note": note,
                "details": json.dumps(details or {}, default=str),
            },
        )

    async def history(self, incident_id: UUID) -> list[IncidentEvent]:
        rows = await self._run(
            """SELECT id, kind, actor_user_id, actor_name, actor_role, from_status, to_status, note, details,
                      occurred_at
                 FROM incident_events WHERE incident_id = :incident ORDER BY seq""",
            {"incident": incident_id},
        )
        return [
            IncidentEvent(r.id, r.kind, r.actor_user_id, r.actor_name, r.actor_role, r.from_status, r.to_status,
                          r.note, dict(r.details or {}), r.occurred_at)
            for r in rows
        ]  # fmt: skip

    async def camera(self, camera_id: UUID) -> CameraRef | None:
        row = (
            await self._run(
                """SELECT c.id, c.display_name, b.id AS building_id, f.id AS floor_id, a.id AS area_id,
                          b.name AS building_name, f.name AS floor_name, a.name AS area_name
                     FROM cameras c
                     LEFT JOIN areas a ON a.id = c.area_id
                     LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, a.floor_id)
                     LEFT JOIN buildings b ON b.id = COALESCE(c.building_id, f.building_id, a.building_id)
                    WHERE c.id = :camera AND c.organization_id = :org AND c.campus_id = :campus""",
                {"camera": camera_id},
            )
        ).first()
        if row is None:
            return None
        location = IncidentLocation(
            row.building_id, row.floor_id, row.area_id, row.building_name, row.floor_name, row.area_name
        )
        return CameraRef(row.id, row.display_name, location)

    async def _member(self, user_id: UUID, roles: tuple[str, ...]) -> StaffMember | None:
        row = (
            await self._run(
                """SELECT id, display_id, full_name, role FROM users
                    WHERE id = :user AND organization_id = :org AND campus_id = :campus AND status = 'ACTIVE'
                      AND role = ANY (:roles)""",
                {"user": user_id, "roles": list(roles)},
            )
        ).first()
        return None if row is None else StaffMember(row.id, row.display_id, row.full_name, row.role)

    async def staff_member(self, user_id: UUID) -> StaffMember | None:
        return await self._member(user_id, ("ADMIN", "SUPERVISOR"))

    async def guard(self, user_id: UUID) -> StaffMember | None:
        return await self._member(user_id, ("GUARD",))

    async def assignable_staff(self) -> list[StaffMember]:
        rows = await self._run(
            """SELECT id, display_id, full_name, role FROM users
                WHERE organization_id = :org AND campus_id = :campus AND status = 'ACTIVE'
                  AND role IN ('ADMIN', 'SUPERVISOR')
                ORDER BY role, full_name, id"""
        )
        return [StaffMember(r.id, r.display_id, r.full_name, r.role) for r in rows]

    # --- Links ------------------------------------------------------------------------------------------------

    async def alert_exists(self, alert_id: UUID) -> bool:
        row = await self._run(
            "SELECT 1 FROM alerts WHERE id = :alert AND organization_id = :org AND campus_id = :campus",
            {"alert": alert_id},
        )
        return row.first() is not None

    async def link_alert(self, incident_id: UUID, alert_id: UUID, by_name: str) -> bool:
        rows = await self._run(
            """INSERT INTO incident_alerts (incident_id, alert_id, organization_id, campus_id, linked_by,
                   linked_by_name)
               VALUES (:incident, :alert, :org, :campus, :actor, :name) ON CONFLICT DO NOTHING RETURNING alert_id""",
            {"incident": incident_id, "alert": alert_id, "actor": self._actor.id, "name": by_name},
        )
        return rows.first() is not None

    async def unlink_alert(self, incident_id: UUID, alert_id: UUID) -> bool:
        rows = await self._run(
            "DELETE FROM incident_alerts WHERE incident_id = :incident AND alert_id = :alert RETURNING alert_id",
            {"incident": incident_id, "alert": alert_id},
        )
        return rows.first() is not None

    async def alerts(self, incident_id: UUID) -> list[LinkedAlert]:
        rows = await self._run(
            """SELECT a.id, a.alert_type, a.priority, a.status, a.confidence, a.detected_at, a.last_detected_at,
                      a.detection_count, a.camera_id, a.camera_name, a.building_name, a.floor_name, a.area_name,
                      a.snapshot_ref IS NOT NULL AS has_snapshot, ia.linked_by_name, ia.linked_at
                 FROM incident_alerts ia JOIN alerts a ON a.id = ia.alert_id
                WHERE ia.incident_id = :incident ORDER BY a.detected_at, a.id""",
            {"incident": incident_id},
        )
        return [
            LinkedAlert(
                alert_id=r.id,
                alert_type=r.alert_type,
                priority=r.priority,
                status=r.status,
                confidence=float(r.confidence) if r.confidence is not None else None,
                detected_at=r.detected_at,
                last_detected_at=r.last_detected_at,
                detection_count=r.detection_count,
                camera_id=r.camera_id,
                camera_name=r.camera_name,
                location_label=_label(r.building_name, r.floor_name, r.area_name),
                has_snapshot=bool(r.has_snapshot),
                linked_by_name=r.linked_by_name,
                linked_at=r.linked_at,
            )
            for r in rows
        ]

    async def incidents_of_alert(self, alert_id: UUID) -> list[Incident]:
        rows = await self._run(
            f"""SELECT {_INCIDENT_COLUMNS}, {_COUNTS} FROM incidents i
                 JOIN incident_alerts ia ON ia.incident_id = i.id
                WHERE ia.alert_id = :alert ORDER BY i.number""",
            {"alert": alert_id},
        )
        return [_incident(r) for r in rows]

    async def report_incident(self, report_id: UUID) -> tuple[bool, UUID | None]:
        row = (
            await self._run(
                "SELECT incident_id FROM manual_reports WHERE id = :report AND organization_id = :org "
                "AND campus_id = :campus",
                {"report": report_id},
            )
        ).first()
        return (False, None) if row is None else (True, row.incident_id)

    async def set_report_incident(self, report_id: UUID, incident_id: UUID | None) -> None:
        await self._run(
            "UPDATE manual_reports SET incident_id = :incident WHERE id = :report",
            {"report": report_id, "incident": incident_id},
        )

    async def reports(self, incident_id: UUID) -> list[LinkedReport]:
        rows = await self._run(
            """SELECT id, report_type, description, reporter_name, reporter_guard_id, building_name, floor_name,
                      area_name, occurred_at, created_at, status, photo_path IS NOT NULL AS has_photo
                 FROM manual_reports WHERE incident_id = :incident ORDER BY created_at, id""",
            {"incident": incident_id},
        )
        return [
            LinkedReport(r.id, r.report_type, r.description, r.reporter_name, r.reporter_guard_id,
                         _label(r.building_name, r.floor_name, r.area_name), r.occurred_at, r.created_at, r.status,
                         bool(r.has_photo))
            for r in rows
        ]  # fmt: skip

    async def evidence_incident(self, evidence_id: UUID) -> tuple[bool, UUID | None]:
        row = (
            await self._run(
                "SELECT incident_id FROM evidence_items WHERE id = :evidence AND organization_id = :org "
                "AND campus_id = :campus",
                {"evidence": evidence_id},
            )
        ).first()
        return (False, None) if row is None else (True, row.incident_id)

    async def set_evidence_incident(self, evidence_id: UUID, incident_id: UUID | None) -> None:
        await self._run(
            "UPDATE evidence_items SET incident_id = :incident, updated_at = now() WHERE id = :evidence",
            {"evidence": evidence_id, "incident": incident_id},
        )

    async def evidence(self, incident_id: UUID) -> list[LinkedEvidence]:
        rows = await self._run(
            """SELECT id, kind, title, camera_name, covers_from, covers_to, saved_by_name, saved_at, media_type
                 FROM evidence_items WHERE incident_id = :incident ORDER BY saved_at, id""",
            {"incident": incident_id},
        )
        return [
            LinkedEvidence(r.id, r.kind, r.title, r.camera_name, r.covers_from, r.covers_to, r.saved_by_name,
                           r.saved_at, r.media_type)
            for r in rows
        ]  # fmt: skip

    # --- Participants and chat -----------------------------------------------------------------------------

    async def add_participant(self, incident_id: UUID, guard_user_id: UUID) -> bool:
        rows = await self._run(
            """INSERT INTO incident_participants (incident_id, guard_user_id, organization_id, campus_id, added_by)
               VALUES (:incident, :guard, :org, :campus, :actor) ON CONFLICT DO NOTHING RETURNING guard_user_id""",
            {"incident": incident_id, "guard": guard_user_id, "actor": self._actor.id},
        )
        return rows.first() is not None

    async def remove_participant(self, incident_id: UUID, guard_user_id: UUID) -> bool:
        rows = await self._run(
            "DELETE FROM incident_participants WHERE incident_id = :incident AND guard_user_id = :guard "
            "RETURNING guard_user_id",
            {"incident": incident_id, "guard": guard_user_id},
        )
        return rows.first() is not None

    async def participants(self, incident_id: UUID) -> list[Participant]:
        rows = await self._run(
            """SELECT u.id, u.display_id, u.full_name, bool_or(src.invited) AS invited
                 FROM (SELECT p.guard_user_id AS user_id, true AS invited FROM incident_participants p
                        WHERE p.incident_id = :incident
                       UNION ALL
                       SELECT r.guard_user_id, false FROM incident_alerts ia
                         JOIN alert_recipients r ON r.alert_id = ia.alert_id
                        WHERE ia.incident_id = :incident) src
                 JOIN users u ON u.id = src.user_id
                GROUP BY u.id, u.display_id, u.full_name
                ORDER BY u.full_name, u.id""",
            {"incident": incident_id},
        )
        return [Participant(r.id, r.display_id, r.full_name, bool(r.invited)) for r in rows]

    async def messages(self, incident_id: UUID, *, after: datetime | None, limit: int) -> list[IncidentMessage]:
        rows = await self._run(
            """SELECT id, sender_user_id, sender_name, sender_role, body, created_at FROM incident_messages
                WHERE incident_id = :incident AND (CAST(:after AS timestamptz) IS NULL OR created_at > :after)
                ORDER BY created_at, id LIMIT :limit""",
            {"incident": incident_id, "after": after, "limit": limit},
        )
        return [
            IncidentMessage(r.id, r.sender_user_id, r.sender_name, r.sender_role, r.body, r.created_at) for r in rows
        ]

    async def post_message(self, incident_id: UUID, sender_name: str, body: str) -> IncidentMessage:
        row = (
            await self._run(
                """INSERT INTO incident_messages (id, incident_id, organization_id, campus_id, sender_user_id,
                       sender_name,
                       sender_role, body)
                   VALUES (:id, :incident, :org, :campus, :actor, :name, :role, :body)
                   RETURNING id, sender_user_id, sender_name, sender_role, body, created_at""",
                {
                    "id": uuid4(),
                    "incident": incident_id,
                    "actor": self._actor.id,
                    "name": sender_name,
                    "role": self._actor.role.value,
                    "body": body,
                },
            )
        ).one()
        return IncidentMessage(row.id, row.sender_user_id, row.sender_name, row.sender_role, row.body, row.created_at)

    async def participant_incidents(self) -> list[Incident]:
        """The cases a Guard takes part in (Row-Level Security returns nothing else)."""
        rows = await self._run(
            f"""SELECT {_INCIDENT_COLUMNS} FROM incidents i
                WHERE i.organization_id = :org AND i.campus_id = :campus
                ORDER BY CASE WHEN i.status IN ('RESOLVED', 'DISMISSED') THEN 1 ELSE 0 END, i.updated_at DESC
                LIMIT 100"""
        )
        return [_incident(r, counts=False) for r in rows]

    # --- Missing footage ---------------------------------------------------------------------------------------

    async def footage_gaps(self, windows: list[tuple[UUID | None, str, datetime, datetime]]) -> list[FootageGap]:
        gaps: list[FootageGap] = []
        for camera_id, camera_name, start, end in windows:
            if camera_id is None:
                gaps.append(FootageGap(None, camera_name, start, end, "CAMERA_REMOVED"))
                continue
            row = (
                await self._run(
                    """SELECT COALESCE(sum(EXTRACT(EPOCH FROM (LEAST(s.ended_at, :until)
                                                              - GREATEST(s.started_at, :since)))), 0) AS covered,
                              (SELECT st.state FROM camera_status st WHERE st.camera_id = :camera) AS state,
                              EXISTS (SELECT 1 FROM cameras c WHERE c.id = :camera) AS present
                         FROM recording_segments s
                        WHERE s.camera_id = :camera AND s.started_at < :until AND s.ended_at > :since""",
                    {"camera": camera_id, "since": start, "until": end},
                )
            ).one()
            covered = float(row.covered or 0)
            wanted = max((end - start).total_seconds(), 1.0)
            if not row.present:
                gaps.append(FootageGap(camera_id, camera_name, start, end, "CAMERA_REMOVED"))
                continue
            if covered <= 0:
                gaps.append(FootageGap(camera_id, camera_name, start, end, "NO_RECORDING", 0.0))
            elif covered / wanted < _COVERED_ENOUGH:
                gaps.append(FootageGap(camera_id, camera_name, start, end, "PARTIAL_RECORDING", covered))
            if row.state == "OFFLINE":
                gaps.append(FootageGap(camera_id, camera_name, start, end, "CAMERA_OFFLINE", covered))
        return gaps

    async def notify(self, user_id: UUID, kind: str, incident_id: UUID, title: str) -> None:
        await self._run(
            """INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, incident_id, title)
               VALUES (:id, :org, :campus, :user, :kind, :incident, :title)""",
            {"id": uuid4(), "user": user_id, "kind": kind, "incident": incident_id, "title": title[:200]},
        )

    async def person_search_links(self, incident_id: UUID) -> list[dict[str, Any]]:
        """Verified Person Search results linked to the case (migration 0028)."""
        rows = await self._run(
            """SELECT c.id, c.search_id, c.camera_name, c.location_label, c.observed_at, c.similarity, c.band,
                      c.review, c.reviewed_by_name, c.crop_path IS NOT NULL AS has_crop
                 FROM person_search_candidates c WHERE c.incident_id = :incident ORDER BY c.observed_at""",
            {"incident": incident_id},
        )
        return [
            {
                "result_id": str(r.id),
                "search_id": str(r.search_id),
                "camera_name": r.camera_name,
                "location": r.location_label,
                "observed_at": r.observed_at.isoformat(),
                "similarity": float(r.similarity),
                "band": r.band,
                "review": r.review,
                "reviewed_by_name": r.reviewed_by_name,
                "has_crop": bool(r.has_crop),
            }
            for r in rows
        ]
