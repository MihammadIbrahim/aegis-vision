"""Alerts through Row-Level Security and the 0015 database functions. Must run on a session where
``bind_actor`` was called, except ``ingest``, which the detection intake calls without a user."""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.alert import (
    Alert,
    AlertEscalation,
    AlertEvent,
    AlertMessage,
    AlertPriority,
    AlertRecipient,
    AlertStatus,
    AlertType,
)
from src.domain.entities.location import LocationLevel, LocationTarget
from src.domain.exceptions.domain_errors import Conflict
from src.domain.repositories.i_alert_repo import AlertFilter, Detection

_UUIDS = ("id", "alert", "actor", "camera", "client", "escalation", "target", "message", "org", "campus")


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params))


_COLUMNS = """a.id, a.organization_id, a.campus_id, a.camera_id, a.camera_name, a.alert_type, a.priority, a.status,
    a.confidence, a.detected_at, a.last_detected_at, a.detection_count, a.building_id, a.building_name,
    a.building_kind, a.floor_id, a.floor_name, a.area_id, a.area_name, a.closed_at, a.close_note,
    a.created_at, a.updated_at, a.handled_by, a.handled_by_name, a.handled_at,
    a.snapshot_ref IS NOT NULL AS has_snapshot,
    (SELECT count(*) FROM alert_recipients r WHERE r.alert_id = a.id) AS recipient_count,
    (SELECT count(*) FROM alert_recipients r WHERE r.alert_id = a.id AND r.state <> 'NOTIFIED') AS acknowledged_count,
    (SELECT count(*) FROM alert_recipients r WHERE r.alert_id = a.id AND r.state = 'RESPONDING') AS responding_count"""


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _alert(row: Any, recipients: list[AlertRecipient] | None = None) -> Alert:
    return Alert(
        id=row.id,
        organization_id=row.organization_id,
        campus_id=row.campus_id,
        camera_id=row.camera_id,
        camera_name=row.camera_name,
        alert_type=AlertType(row.alert_type),
        priority=AlertPriority(row.priority),
        status=AlertStatus(row.status),
        confidence=row.confidence,
        detected_at=row.detected_at,
        last_detected_at=row.last_detected_at,
        detection_count=row.detection_count,
        building_id=row.building_id,
        building_name=row.building_name,
        building_kind=row.building_kind,
        floor_id=row.floor_id,
        floor_name=row.floor_name,
        area_id=row.area_id,
        area_name=row.area_name,
        closed_at=row.closed_at,
        close_note=row.close_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        recipients=recipients or [],
        handled_by=row.handled_by,
        handled_by_name=row.handled_by_name,
        handled_at=row.handled_at,
        recipient_count=int(row.recipient_count),
        acknowledged_count=int(row.acknowledged_count),
        responding_count=int(row.responding_count),
        has_snapshot=bool(row.has_snapshot),
    )


class SqlAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        try:
            return await self._session.execute(_sql(statement, params), params)
        except IntegrityError as error:
            raise Conflict("The alert changed meanwhile. Reload it and try again.", code="ALERT_CONFLICT") from error

    async def ingest(self, detection: Detection, *, dedup_seconds: int) -> tuple[UUID | None, str]:
        row = (
            await self._run(
                "SELECT alert_id, outcome FROM app_alert_ingest(:camera, :type, :confidence, :detected_at, "
                ":detection_id, :snapshot_ref, :dedup)",
                {
                    "camera": detection.camera_id,
                    "type": detection.alert_type,
                    "confidence": detection.confidence,
                    "detected_at": detection.detected_at,
                    "detection_id": detection.detection_id,
                    "snapshot_ref": detection.snapshot_ref,
                    "dedup": dedup_seconds,
                },
            )
        ).one()
        return row.alert_id, row.outcome

    async def list_alerts(self, where: AlertFilter) -> list[Alert]:
        clauses: list[str] = ["true"]
        params: dict[str, Any] = {"limit": where.limit, "offset": where.offset}
        for column, value in (("status", where.status), ("priority", where.priority), ("alert_type", where.alert_type)):
            if value is not None:
                clauses.append(f"a.{column} = :{column}")
                params[column] = value
        for column, location_id in (
            ("building", where.building_id),
            ("floor", where.floor_id),
            ("area", where.area_id),
        ):
            if location_id is not None:
                clauses.append(f"a.{column}_id = :{column}_id")
                params[f"{column}_id"] = location_id
        if where.text and where.text.strip():
            needle = where.text.strip()
            clauses.append(
                "(a.id::text ILIKE :q ESCAPE '\\' OR a.camera_id::text = :exact OR a.alert_type ILIKE :q ESCAPE '\\' "
                "OR replace(a.alert_type, '_', ' ') ILIKE :q ESCAPE '\\' OR a.camera_name ILIKE :q ESCAPE '\\' "
                "OR a.building_name ILIKE :q ESCAPE '\\' OR a.floor_name ILIKE :q ESCAPE '\\' "
                "OR a.area_name ILIKE :q ESCAPE '\\')"
            )
            params["q"] = _like(needle)
            params["exact"] = needle.lower()
        if where.since is not None:
            clauses.append("a.last_detected_at >= :since")
            params["since"] = where.since
        if where.until is not None:
            clauses.append("a.detected_at <= :until")
            params["until"] = where.until
        if where.handled_by is not None:
            clauses.append("a.handled_by = :handled_by")
            params["handled_by"] = where.handled_by
        uuids = [f"{c}_id" for c in ("building", "floor", "area") if f"{c}_id" in params]
        if "handled_by" in params:
            uuids.append("handled_by")
        statement = text(
            f"SELECT {_COLUMNS} FROM alerts a WHERE {' AND '.join(clauses)} "
            "ORDER BY (a.status IN ('RESOLVED', 'DISMISSED')), a.priority = 'STANDARD', a.created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ).bindparams(*(bindparam(name, type_=Uuid) for name in uuids))
        return [_alert(r) for r in await self._session.execute(statement, params)]

    async def get(self, alert_id: UUID, *, lock: bool = False) -> Alert | None:
        row = (
            await self._run(
                f"SELECT {_COLUMNS} FROM alerts a WHERE a.id = :id{' FOR UPDATE' if lock else ''}", {"id": alert_id}
            )
        ).one_or_none()
        if row is None:
            return None
        recipients = await self._run(
            "SELECT r.guard_user_id, u.display_id, u.full_name, r.reason, r.state, r.assessment, r.added_at, "
            "r.acknowledged_at, r.responding_at FROM alert_recipients r JOIN users u ON u.id = r.guard_user_id "
            "WHERE r.alert_id = :id ORDER BY r.added_at, u.full_name",
            {"id": alert_id},
        )
        return _alert(row, [AlertRecipient(*r) for r in recipients])

    async def history(self, alert_id: UUID) -> list[AlertEvent]:
        rows = await self._run(
            "SELECT e.id, e.event_type, e.actor_role, u.full_name, e.from_status, e.to_status, e.details, e.created_at "
            "FROM alert_events e LEFT JOIN users u ON u.id = e.actor_user_id WHERE e.alert_id = :id "
            "ORDER BY e.created_at, e.id",
            {"id": alert_id},
        )
        return [AlertEvent(*r) for r in rows]

    async def action_replayed(self, actor_id: UUID, client_action_id: UUID | None) -> bool:
        if client_action_id is None:
            return False
        row = (
            await self._run(
                "SELECT 1 FROM alert_events WHERE actor_user_id = :actor AND client_action_id = :client",
                {"actor": actor_id, "client": client_action_id},
            )
        ).first()
        return row is not None

    async def guard_action(self, alert_id: UUID, action: str, client_action_id: UUID | None) -> tuple[str, str | None]:
        row = (
            await self._run(
                "SELECT outcome, status FROM app_alert_guard_action(:alert, :action, :client)",
                {"alert": alert_id, "action": action, "client": client_action_id},
            )
        ).one()
        return row.outcome, row.status

    async def close(self, alert_id: UUID, status: AlertStatus, actor_id: UUID, note: str | None) -> None:
        await self._run(
            "UPDATE alerts SET status = :status, closed_by = :actor, closed_at = now(), close_note = :note, "
            "updated_at = now() WHERE id = :id",
            {"id": alert_id, "status": status.value, "actor": actor_id, "note": note},
        )

    async def set_status(self, alert_id: UUID, status: AlertStatus) -> None:
        await self._run("UPDATE alerts SET status = :status WHERE id = :id", {"id": alert_id, "status": status.value})

    async def add_event(
        self,
        alert: Alert,
        actor_id: UUID,
        actor_role: str,
        event_type: str,
        to_status: str,
        details: dict[str, object],
        client_action_id: UUID | None,
    ) -> None:
        await self._run(
            "INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_user_id, actor_role, "
            "event_type, from_status, to_status, details, client_action_id) "
            "VALUES (:id, :alert, :org, :campus, :actor, :role, :event, :from_status, :to_status, "
            "CAST(:details AS jsonb), :client)",
            {
                "id": uuid4(),
                "alert": alert.id,
                "org": alert.organization_id,
                "campus": alert.campus_id,
                "actor": actor_id,
                "role": actor_role,
                "event": event_type,
                "from_status": alert.status.value,
                "to_status": to_status,
                "details": json.dumps(details, default=str),
                "client": client_action_id,
            },
        )

    async def eligible_guards(
        self, alert: Alert, *, location: LocationTarget | None, guard_ids: list[str] | None
    ) -> list[UUID]:
        """Active Guards of the alert's campus who are not recipients yet, limited by the scope."""
        params: dict[str, Any] = {"alert": alert.id, "org": alert.organization_id, "campus": alert.campus_id}
        where = ""
        if guard_ids is not None:
            where = "AND u.display_id = ANY (:guard_ids)"
            params["guard_ids"] = guard_ids
        elif location is not None:
            params["target"] = location.id
            where = {
                # At or below the chosen place.
                LocationLevel.BUILDING: """AND (g.building_id = :target
                    OR g.floor_id IN (SELECT f.id FROM floors f WHERE f.building_id = :target)
                    OR g.area_id IN (SELECT ar.id FROM areas ar WHERE ar.building_id = :target))""",
                LocationLevel.FLOOR: """AND (g.floor_id = :target
                    OR g.area_id IN (SELECT ar.id FROM areas ar WHERE ar.floor_id = :target))""",
                LocationLevel.AREA: "AND g.area_id = :target",
            }[location.level]
        join = "JOIN guard_location_assignments g ON g.guard_user_id = u.id" if location is not None else ""
        rows = await self._run(
            f"SELECT u.id FROM users u {join} WHERE u.organization_id = :org AND u.campus_id = :campus "
            "AND u.role = 'GUARD' AND u.status = 'ACTIVE' "
            f"AND NOT EXISTS (SELECT 1 FROM alert_recipients r WHERE r.alert_id = :alert AND r.guard_user_id = u.id) "
            f"{where} ORDER BY u.full_name",
            params,
        )
        return [r.id for r in rows]

    async def add_escalation(
        self,
        alert: Alert,
        escalation_id: UUID,
        actor_id: UUID,
        scope: str,
        location: LocationTarget | None,
        note: str | None,
        guards: list[UUID],
    ) -> None:
        ids: dict[str, UUID | None] = {"building_id": None, "floor_id": None, "area_id": None}
        if location is not None:
            ids[f"{location.level.value.lower()}_id"] = location.id
        await self._session.execute(
            text(
                "INSERT INTO alert_escalations (id, alert_id, organization_id, campus_id, escalated_by, scope, "
                "building_id, floor_id, area_id, note, added_count) VALUES (:escalation, :alert, :org, :campus, "
                ":actor, :scope, :building_id, :floor_id, :area_id, :note, :count)"
            ).bindparams(
                *(bindparam(n, type_=Uuid) for n in ("escalation", "alert", "org", "campus", "actor")),
                *(bindparam(n, type_=Uuid) for n in ids),
            ),
            {
                "escalation": escalation_id,
                "alert": alert.id,
                "org": alert.organization_id,
                "campus": alert.campus_id,
                "actor": actor_id,
                "scope": scope,
                "note": note,
                "count": len(guards),
                **ids,
            },
        )
        for guard in guards:
            await self._run(
                "INSERT INTO alert_recipients (alert_id, guard_user_id, organization_id, campus_id, reason, "
                "escalation_id) VALUES (:alert, :id, :org, :campus, 'ESCALATION', :escalation)",
                {
                    "alert": alert.id,
                    "id": guard,
                    "org": alert.organization_id,
                    "campus": alert.campus_id,
                    "escalation": escalation_id,
                },
            )

    async def notify(self, alert: Alert, user_ids: list[UUID], kind: str, title: str) -> None:
        for user_id in user_ids:
            await self._run(
                "INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, alert_id, title) "
                "VALUES (:id, :org, :campus, :actor, :kind, :alert, :title)",
                {
                    "id": uuid4(),
                    "org": alert.organization_id,
                    "campus": alert.campus_id,
                    "actor": user_id,
                    "kind": kind,
                    "alert": alert.id,
                    "title": title[:200],
                },
            )

    async def campus_staff(self, alert: Alert) -> list[UUID]:
        rows = await self._run(
            "SELECT id FROM users WHERE organization_id = :org AND campus_id = :campus "
            "AND role IN ('ADMIN', 'SUPERVISOR') AND status = 'ACTIVE'",
            {"org": alert.organization_id, "campus": alert.campus_id},
        )
        return [r.id for r in rows]

    async def messages(self, alert_id: UUID) -> list[AlertMessage]:
        rows = await self._run(
            "SELECT id, sender_role, sender_name, body, created_at FROM alert_messages WHERE alert_id = :alert "
            "ORDER BY created_at, id",
            {"alert": alert_id},
        )
        return [AlertMessage(*r) for r in rows]

    async def post_message(
        self,
        alert: Alert,
        message_id: UUID,
        sender_id: UUID,
        sender_role: str,
        sender_name: str,
        body: str,
        client_message_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """The message ID, and False when this ``client_message_id`` was already posted."""
        if client_message_id is not None:
            existing = (
                await self._run(
                    "SELECT id FROM alert_messages WHERE sender_user_id = :actor AND client_message_id = :client",
                    {"actor": sender_id, "client": client_message_id},
                )
            ).first()
            if existing is not None:
                return existing.id, False
        await self._run(
            "INSERT INTO alert_messages (id, alert_id, organization_id, campus_id, sender_user_id, sender_role, "
            "sender_name, body, client_message_id) VALUES (:message, :alert, :org, :campus, :actor, :role, :name, "
            ":body, :client)",
            {
                "message": message_id,
                "alert": alert.id,
                "org": alert.organization_id,
                "campus": alert.campus_id,
                "actor": sender_id,
                "role": sender_role,
                "name": sender_name,
                "body": body,
                "client": client_message_id,
            },
        )
        return message_id, True


_ESCALATIONS = """SELECT e.id, e.scope, u.full_name AS by_name, u.role AS by_role, e.note, e.added_count, e.created_at,
           COALESCE(array_agg(g.display_id || ' · ' || g.full_name ORDER BY g.full_name)
                    FILTER (WHERE g.id IS NOT NULL), '{}') AS guards
      FROM alert_escalations e
      LEFT JOIN users u ON u.id = e.escalated_by
      LEFT JOIN alert_recipients r ON r.escalation_id = e.id
      LEFT JOIN users g ON g.id = r.guard_user_id
     WHERE e.alert_id = :id
     GROUP BY e.id, u.full_name, u.role
     ORDER BY e.created_at, e.id"""


class SqlAlertOperations:
    """Responsible staff, escalation history and automatic escalation (migration 0026, decision D-024)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def escalations(self, alert_id: UUID) -> list[AlertEscalation]:
        rows = await self._session.execute(
            text(_ESCALATIONS).bindparams(bindparam("id", type_=Uuid)), {"id": alert_id}
        )
        return [
            AlertEscalation(r.id, r.scope, r.by_name, r.by_role, r.note, r.added_count, r.created_at, list(r.guards))
            for r in rows
        ]

    async def set_handler(self, alert_id: UUID, user_id: UUID | None, name: str | None) -> None:
        statement = text(
            "UPDATE alerts SET handled_by = :user_id, handled_by_name = :name, "
            "handled_at = CASE WHEN CAST(:name AS text) IS NULL THEN NULL ELSE now() END, updated_at = now() "
            "WHERE id = :id"
        ).bindparams(bindparam("id", type_=Uuid), bindparam("user_id", type_=Uuid))
        await self._session.execute(statement, {"id": alert_id, "user_id": user_id, "name": name})

    async def escalate_overdue(self, limit: int = 100) -> int:
        """Alerts no Guard acknowledged in time; runs without a signed-in user."""
        statement = text("SELECT app_alerts_escalate_overdue(:limit)")
        return int((await self._session.execute(statement, {"limit": limit})).scalar_one())
