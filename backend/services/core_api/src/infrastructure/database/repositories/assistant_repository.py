"""AI Investigation & Security Analytics Chatbot: chat storage, the campus vocabulary and the read-only tools
(decision D-027, migration 0032).

Every tool here only reads, inside the caller's request transaction, so PostgreSQL Row-Level Security applies exactly
as on the web pages: own organization and campus only; Supervisors see operational audit records and their own only.
Each tool also filters by organization and campus explicitly (defence in depth) and runs in a savepoint, so a failing
query cannot abort the request. The tools never read personnel, restricted-area access, user records, campus
requests, credentials, or organization settings beyond the time zone and the escalation thresholds.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, cast
from uuid import UUID, uuid4

from assistant_engine import CameraRef, Citation, LocationRef, ToolCall, ToolResult, Vocabulary
from assistant_engine.catalog import ALERT_TYPE_LABELS, ArgumentError, validate
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.user import User
from src.infrastructure.database.repositories.incident_repository import SqlIncidentRepository

logger = logging.getLogger(__name__)

_PARAM = re.compile(r"(?<!:):([a-z_]+)\b")
OPERATIONAL_AUDIT_CATEGORIES = (
    "ALERTS",
    "GUARD_RESPONSE",
    "CAMERA_LOCATION",
    "AI_SETTINGS",
    "EVIDENCE",
    "VIDEO",
    "PERSON_SEARCH",
    "MANUAL_REPORTS",
    "INCIDENTS",
    "INCIDENT_CHAT",
)
INCIDENT_WINDOW = timedelta(seconds=60)
ALERT_WINDOW = timedelta(seconds=15)
_CLOSED = "('RESOLVED', 'DISMISSED')"
_EVENT_LABELS = {
    "CREATED": "Alert raised",
    "DETECTION_REPEATED": "Detection repeated",
    "ROUTED": "Sent to Guards",
    "ACKNOWLEDGED": "Acknowledged",
    "RESPONDING": "I'm Responding",
    "ASSESSMENT": "Assessment",
    "ESCALATED": "Escalated by staff",
    "AUTO_ESCALATED": "Automatic escalation",
    "ASSISTANCE_BROADCAST": "Assistance broadcast",
    "HANDLER_ASSIGNED": "Handler assigned",
    "RESOLVED": "Alert resolved",
    "DISMISSED": "Alert dismissed",
}
_ASSESSMENTS = {"UNDER_CONTROL": "Under Control", "ADDITIONAL_GUARDS_REQUIRED": "Additional Guards Required"}


def reference(number: int) -> str:
    return f"INC-{number:06d}"


def _place(building: str | None, floor: str | None, area: str | None) -> str | None:
    parts = [p for p in (building, floor, area) if p]
    return " › ".join(parts) if parts else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _excerpt(value: str | None, limit: int = 140) -> str:
    text_ = " ".join((value or "").split())
    return text_ if len(text_) <= limit else text_[: limit - 1] + "…"


def _alert_label(row: Any) -> str:
    return f"{ALERT_TYPE_LABELS.get(row.alert_type, row.alert_type)} alert · {row.camera_name}"


class CaseNotFound(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: UUID
    title: str
    context_incident_id: UUID | None
    context_incident_reference: str | None
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime
    message_count: int


@dataclass(frozen=True, slots=True)
class MessageRow:
    id: UUID
    session_id: UUID
    role: str
    content: str
    citations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    insufficient_evidence: bool
    provider: str | None
    model_name: str | None
    created_at: datetime


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class _Base:
    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._session = session
        self._actor = actor
        self._scope: dict[str, Any] = {"org": actor.organization_id, "campus": actor.campus_id}

    async def _rows(self, statement: str, **params: Any) -> list[Any]:
        values = {**self._scope, **params}
        used = set(_PARAM.findall(statement))
        bound = {k: v for k, v in values.items() if k in used}
        result = cast("CursorResult[Any]", await self._session.execute(text(statement), bound))
        return list(result) if result.returns_rows else []

    async def _one(self, statement: str, **params: Any) -> Any:
        rows = await self._rows(statement, **params)
        return rows[0] if rows else None


# --- Chat sessions and messages ---------------------------------------------------------------------------------


_SESSION_COLUMNS = """s.id, s.title, s.context_incident_id, i.number AS incident_number, s.created_at, s.updated_at,
       s.last_active_at, (SELECT count(*) FROM ai_chat_messages m WHERE m.session_id = s.id) AS message_count"""
_MESSAGE_COLUMNS = """m.id, m.session_id, m.role, m.content, m.citations, m.tool_calls, m.blocks,
       m.insufficient_evidence, m.provider, m.model_name, m.created_at"""


def _session(row: Any) -> SessionRow:
    return SessionRow(
        row.id,
        row.title,
        row.context_incident_id,
        reference(row.incident_number) if row.incident_number else None,
        row.created_at,
        row.updated_at,
        row.last_active_at,
        int(row.message_count),
    )


def _message(row: Any) -> MessageRow:
    return MessageRow(
        row.id,
        row.session_id,
        row.role,
        row.content,
        _json(row.citations),
        _json(row.tool_calls),
        _json(row.blocks),
        row.insufficient_evidence,
        row.provider,
        row.model_name,
        row.created_at,
    )


class SqlAssistantRepository(_Base):
    """The caller's own chat sessions (Row-Level Security: only the owner reads, writes or deletes them)."""

    async def create_session(self, title: str, incident_id: UUID | None) -> SessionRow:
        session_id = uuid4()
        await self._rows(
            """INSERT INTO ai_chat_sessions (id, organization_id, campus_id, user_id, title, context_incident_id)
               VALUES (:id, :org, :campus, :user, :title, :incident)""",
            id=session_id,
            user=self._actor.id,
            title=title[:120],
            incident=incident_id,
        )
        created = await self.session(session_id)
        assert created is not None
        return created

    async def sessions(self, limit: int = 100) -> list[SessionRow]:
        rows = await self._rows(
            f"""SELECT {_SESSION_COLUMNS} FROM ai_chat_sessions s LEFT JOIN incidents i ON i.id = s.context_incident_id
                 WHERE s.organization_id = :org AND s.campus_id = :campus AND s.user_id = :user
                 ORDER BY s.last_active_at DESC LIMIT :limit""",
            user=self._actor.id,
            limit=limit,
        )
        return [_session(r) for r in rows]

    async def session(self, session_id: UUID) -> SessionRow | None:
        row = await self._one(
            f"""SELECT {_SESSION_COLUMNS} FROM ai_chat_sessions s LEFT JOIN incidents i ON i.id = s.context_incident_id
                 WHERE s.id = :id AND s.organization_id = :org AND s.campus_id = :campus AND s.user_id = :user""",
            id=session_id,
            user=self._actor.id,
        )
        return _session(row) if row else None

    async def latest_case_session(self, incident_id: UUID) -> SessionRow | None:
        row = await self._one(
            f"""SELECT {_SESSION_COLUMNS} FROM ai_chat_sessions s LEFT JOIN incidents i ON i.id = s.context_incident_id
                 WHERE s.context_incident_id = :incident AND s.user_id = :user
                   AND s.organization_id = :org AND s.campus_id = :campus
                 ORDER BY s.last_active_at DESC LIMIT 1""",
            incident=incident_id,
            user=self._actor.id,
        )
        return _session(row) if row else None

    async def delete_session(self, session_id: UUID) -> bool:
        rows = await self._rows(
            "DELETE FROM ai_chat_sessions WHERE id = :id AND user_id = :user RETURNING id",
            id=session_id,
            user=self._actor.id,
        )
        return bool(rows)

    async def touch(self, session_id: UUID, title: str | None = None) -> None:
        await self._rows(
            """UPDATE ai_chat_sessions SET last_active_at = now(), updated_at = now(),
                      title = COALESCE(:title, title) WHERE id = :id AND user_id = :user""",
            id=session_id,
            user=self._actor.id,
            title=title[:120] if title else None,
        )

    async def add_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        *,
        citations: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        blocks: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        insufficient_evidence: bool = False,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> MessageRow:
        message_id = uuid4()
        await self._rows(
            """INSERT INTO ai_chat_messages (id, session_id, organization_id, campus_id, role, content, citations,
                      tool_calls, blocks, context, insufficient_evidence, provider, model_name)
               VALUES (:id, :session, :org, :campus, :role, :content, CAST(:citations AS jsonb),
                       CAST(:tool_calls AS jsonb), CAST(:blocks AS jsonb), CAST(:context AS jsonb), :insufficient,
                       :provider, :model)""",
            id=message_id,
            session=session_id,
            role=role,
            content=content[:20000],
            citations=json.dumps(citations or [], default=str),
            tool_calls=json.dumps(tool_calls or [], default=str),
            blocks=json.dumps(blocks or [], default=str),
            context=json.dumps(context or {}, default=str),
            insufficient=insufficient_evidence,
            provider=provider,
            model=model_name,
        )
        row = await self._one(f"SELECT {_MESSAGE_COLUMNS} FROM ai_chat_messages m WHERE m.id = :id", id=message_id)
        return _message(row)

    async def messages(self, session_id: UUID, limit: int = 400) -> list[MessageRow]:
        rows = await self._rows(
            f"""SELECT {_MESSAGE_COLUMNS} FROM ai_chat_messages m WHERE m.session_id = :session
                 ORDER BY m.created_at, m.id LIMIT :limit""",
            session=session_id,
            limit=limit,
        )
        return [_message(r) for r in rows]

    async def last_context(self, session_id: UUID) -> dict[str, Any]:
        row = await self._one(
            """SELECT m.context FROM ai_chat_messages m WHERE m.session_id = :session AND m.role = 'ASSISTANT'
                ORDER BY m.created_at DESC, m.id DESC LIMIT 1""",
            session=session_id,
        )
        return dict(_json(row.context)) if row else {}

    async def incident(self, incident_id: UUID) -> tuple[UUID, str, str] | None:
        row = await self._one(
            """SELECT i.id, i.number, i.title FROM incidents i
                WHERE i.id = :id AND i.organization_id = :org AND i.campus_id = :campus""",
            id=incident_id,
        )
        return (row.id, reference(row.number), row.title) if row else None

    async def settings(self) -> tuple[str, bool, int, int]:
        """The organization's time zone and automatic-escalation thresholds (nothing else of its settings)."""
        row = await self._one(
            """SELECT s.time_zone, s.auto_escalation_enabled, s.escalation_high_seconds, s.escalation_standard_seconds
                 FROM organization_settings s WHERE s.organization_id = :org"""
        )
        if row is None:
            return "UTC", True, 120, 300
        return (
            row.time_zone or "UTC",
            bool(row.auto_escalation_enabled),
            int(row.escalation_high_seconds),
            int(row.escalation_standard_seconds),
        )

    async def vocabulary(self, now: datetime, time_zone: str) -> Vocabulary:
        places = await self._rows(
            """SELECT 'BUILDING' AS level, b.id, b.name AS name FROM buildings b
                WHERE b.organization_id = :org AND b.campus_id = :campus
               UNION ALL
               SELECT 'FLOOR', f.id, b.name || ' › ' || f.name FROM floors f JOIN buildings b ON b.id = f.building_id
                WHERE f.organization_id = :org AND f.campus_id = :campus
               UNION ALL
               SELECT 'AREA', a.id, concat_ws(' › ', b.name, f.name, a.name) FROM areas a
                 JOIN buildings b ON b.id = a.building_id LEFT JOIN floors f ON f.id = a.floor_id
                WHERE a.organization_id = :org AND a.campus_id = :campus
               LIMIT 2000"""
        )
        cameras = await self._rows(
            """SELECT c.id, c.display_name FROM cameras c WHERE c.organization_id = :org AND c.campus_id = :campus
                ORDER BY c.display_name LIMIT 1000"""
        )
        types = await self._rows(
            """SELECT DISTINCT m.report_type FROM manual_reports m WHERE m.organization_id = :org
                  AND m.campus_id = :campus LIMIT 200"""
        )
        tags = await self._rows(
            """SELECT DISTINCT lower(t) AS tag FROM evidence_items e, unnest(e.tags) t
                WHERE e.organization_id = :org AND e.campus_id = :campus LIMIT 200"""
        )
        return Vocabulary(
            now=now,
            time_zone=time_zone,
            role="ADMIN" if self._actor.role.value == "ADMIN" else "SUPERVISOR",
            locations=tuple(LocationRef(r.level, str(r.id), r.name) for r in places),
            cameras=tuple(CameraRef(str(r.id), r.display_name) for r in cameras),
            report_types=tuple(r.report_type for r in types),
            evidence_tags=tuple(r.tag for r in tags),
        )


# --- The read-only tools ----------------------------------------------------------------------------------------


def _period(args: dict[str, Any]) -> tuple[datetime, datetime]:
    period = args["period"]
    return datetime.fromisoformat(period["since"]), datetime.fromisoformat(period["until"])


def _loc(alias: str, location: dict[str, Any] | None, column_prefix: str = "") -> tuple[str, dict[str, Any]]:
    if not location:
        return "", {}
    column = {"BUILDING": "building_id", "FLOOR": "floor_id", "AREA": "area_id"}[location["level"]]
    return f" AND {alias}.{column_prefix}{column} = :loc", {"loc": UUID(location["id"])}


_CAMERA_PLACE = """(SELECT c.id, c.display_name, c.organization_id, c.campus_id,
                           COALESCE(c.building_id, f.building_id, ar.building_id) AS building_id,
                           COALESCE(c.floor_id, ar.floor_id) AS floor_id, c.area_id,
                           concat_ws(' › ', b.name, f.name, ar.name) AS place
                      FROM cameras c
                      LEFT JOIN areas ar ON ar.id = c.area_id
                      LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, ar.floor_id)
                      LEFT JOIN buildings b ON b.id = COALESCE(c.building_id, f.building_id, ar.building_id))"""


class SqlAssistantTools(_Base):
    """``assistant_engine.ToolExecutor`` for one request: the catalogue's tools, run as the caller."""

    def __init__(
        self, session: AsyncSession, actor: User, *, time_zone: str, thresholds: tuple[bool, int, int]
    ) -> None:
        super().__init__(session, actor)
        self._tz = time_zone
        self._thresholds = thresholds
        self._role = "ADMIN" if actor.role.value == "ADMIN" else "SUPERVISOR"

    async def run(self, call: ToolCall) -> ToolResult:
        started = perf_counter()
        try:
            validate(call.name, call.arguments, self._role)  # type: ignore[arg-type]
        except ArgumentError as error:
            return ToolResult(call.name, call.arguments, "DENIED", error=str(error)[:200])
        try:
            async with self._session.begin_nested():
                data, citations, rows = await getattr(self, f"_{call.name}")(call.arguments)
            outcome = "OK" if rows else "EMPTY"
            result = ToolResult(call.name, call.arguments, outcome, data, citations, rows)  # type: ignore[arg-type]
        except CaseNotFound:
            result = ToolResult(call.name, call.arguments, "EMPTY", {"case": None})
        except Exception as error:  # noqa: BLE001 - one failing tool must not end the answer or the request
            logger.warning("Assistant tool %s failed: %s: %s", call.name, type(error).__name__, str(error)[:300])
            result = ToolResult(call.name, call.arguments, "ERROR", error=type(error).__name__)
        result.latency_ms = (perf_counter() - started) * 1000
        return result

    # --- 1. Trends ---

    async def _counts(self, since: datetime, until: datetime, location: Any, alert_type: str | None) -> dict[str, Any]:
        a_loc, a_params = _loc("a", location)
        i_loc, _ = _loc("i", location)
        m_loc, _ = _loc("m", location)
        type_filter = " AND a.alert_type = :type" if alert_type else ""
        alerts = await self._rows(
            f"""SELECT a.alert_type, count(*) AS total, count(*) FILTER (WHERE a.priority = 'HIGH') AS high
                  FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                   AND a.created_at >= :since AND a.created_at < :until{a_loc}{type_filter}
                 GROUP BY a.alert_type ORDER BY total DESC, a.alert_type""",
            since=since,
            until=until,
            type=alert_type,
            **a_params,
        )
        incidents = await self._rows(
            f"""SELECT i.status, count(*) AS total FROM incidents i
                 WHERE i.organization_id = :org AND i.campus_id = :campus
                   AND i.created_at >= :since AND i.created_at < :until{i_loc}
                 GROUP BY i.status ORDER BY i.status""",
            since=since,
            until=until,
            **a_params,
        )
        reports = await self._rows(
            f"""SELECT m.report_type, count(*) AS total FROM manual_reports m
                 WHERE m.organization_id = :org AND m.campus_id = :campus
                   AND m.created_at >= :since AND m.created_at < :until{m_loc}
                 GROUP BY m.report_type ORDER BY total DESC, m.report_type""",
            since=since,
            until=until,
            **a_params,
        )
        return {
            "alerts_by_type": [{"type": r.alert_type, "count": int(r.total)} for r in alerts],
            "alerts_total": sum(int(r.total) for r in alerts),
            "alerts_high": sum(int(r.high) for r in alerts),
            "incidents_by_status": [{"status": r.status, "count": int(r.total)} for r in incidents],
            "incidents_total": sum(int(r.total) for r in incidents),
            "manual_reports_by_type": [{"type": r.report_type, "count": int(r.total)} for r in reports],
            "manual_reports_total": sum(int(r.total) for r in reports),
        }

    async def _recent_citations(self, since: datetime, until: datetime, location: Any) -> list[Citation]:
        a_loc, params = _loc("a", location)
        i_loc, _ = _loc("i", location)
        alerts = await self._rows(
            f"""SELECT a.id, a.alert_type, a.camera_name FROM alerts a
                 WHERE a.organization_id = :org AND a.campus_id = :campus
                   AND a.created_at >= :since AND a.created_at < :until{a_loc}
                 ORDER BY a.created_at DESC LIMIT 5""",
            since=since,
            until=until,
            **params,
        )
        incidents = await self._rows(
            f"""SELECT i.id, i.number, i.title FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                   AND i.created_at >= :since AND i.created_at < :until{i_loc}
                 ORDER BY i.created_at DESC LIMIT 5""",
            since=since,
            until=until,
            **params,
        )
        return [Citation.of("INCIDENT", r.id, f"{reference(r.number)} · {r.title}") for r in incidents] + [
            Citation.of("ALERT", r.id, _alert_label(r)) for r in alerts
        ]

    async def _trend_summary(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        alert_type = args.get("alert_type")
        counts = await self._counts(since, until, args.get("location"), alert_type)
        if alert_type:
            counts.update(incidents_total=0, incidents_by_status=[], manual_reports_total=0, manual_reports_by_type=[])
        categories = [(x["type"], x["count"]) for x in counts["alerts_by_type"]]
        if counts["manual_reports_total"]:
            categories.append(("MANUAL_REPORTS", counts["manual_reports_total"]))
        best = max((c for _, c in categories), default=0)
        counts["most_frequent"] = [{"category": k, "count": c} for k, c in categories if c == best and best > 0]
        counts["alert_type"] = alert_type
        rows = counts["alerts_total"] + counts["incidents_total"] + counts["manual_reports_total"]
        return counts, await self._recent_citations(since, until, args.get("location")), rows

    async def _peak_times(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        source = args["source"]
        location = args.get("location")
        parts = []
        for name, alias, table in (
            ("alerts", "a", "alerts"),
            ("incidents", "i", "incidents"),
            ("manual_reports", "m", "manual_reports"),
        ):
            if source in (name, "all"):
                loc, _ = _loc(alias, location)
                parts.append(
                    f"""SELECT {alias}.created_at AS at FROM {table} {alias}
                         WHERE {alias}.organization_id = :org AND {alias}.campus_id = :campus
                           AND {alias}.created_at >= :since AND {alias}.created_at < :until{loc}"""
                )
        _, params = _loc("x", location)
        rows = await self._rows(
            f"""SELECT EXTRACT(HOUR FROM e.at AT TIME ZONE :tz)::int AS h,
                       EXTRACT(ISODOW FROM e.at AT TIME ZONE :tz)::int - 1 AS d, count(*) AS total
                  FROM ({" UNION ALL ".join(parts)}) e GROUP BY 1, 2""",
            since=since,
            until=until,
            tz=self._tz,
            **params,
        )
        by_hour, by_day = [0] * 24, [0] * 7
        for r in rows:
            by_hour[r.h] += int(r.total)
            by_day[r.d] += int(r.total)
        total = sum(by_hour)
        peak_hours = sorted(
            ({"hour": h, "count": c} for h, c in enumerate(by_hour) if c), key=lambda x: (-x["count"], x["hour"])
        )
        peak_days = sorted(
            ({"day": d, "count": c} for d, c in enumerate(by_day) if c), key=lambda x: (-x["count"], x["day"])
        )
        window = max(range(24), key=lambda h: (by_hour[h] + by_hour[(h + 1) % 24], -h))
        data = {
            "source": source,
            "total": total,
            "time_zone": self._tz,
            "by_hour": by_hour,
            "by_weekday": by_day,
            "peak_hours": peak_hours[:3],
            "peak_days": peak_days[:3],
            "peak_window": {"start_hour": window, "count": by_hour[window] + by_hour[(window + 1) % 24]},
        }
        return data, [], total

    async def _compare_periods(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        p_since, p_until = _period({"period": args["previous_period"]})
        alert_type = args.get("alert_type")
        cur = await self._counts(since, until, args.get("location"), alert_type)
        prev = await self._counts(p_since, p_until, args.get("location"), alert_type)

        def by_type(counts: dict[str, Any], key: str) -> int:
            return next((x["count"] for x in counts["alerts_by_type"] if x["type"] == key), 0)

        label = f"{ALERT_TYPE_LABELS[alert_type]} alerts" if alert_type else "Alerts (all types)"
        rows = [{"label": label, "current": cur["alerts_total"], "previous": prev["alerts_total"]}]
        if not alert_type:
            for key in ("WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA"):
                rows.append(
                    {
                        "label": f"{ALERT_TYPE_LABELS[key]} alerts",
                        "current": by_type(cur, key),
                        "previous": by_type(prev, key),
                    }
                )
            rows.append(
                {"label": "Cases opened", "current": cur["incidents_total"], "previous": prev["incidents_total"]}
            )
            rows.append(
                {
                    "label": "Manual reports",
                    "current": cur["manual_reports_total"],
                    "previous": prev["manual_reports_total"],
                }
            )
        partial = until >= datetime.now(UTC) - timedelta(seconds=5)
        data = {
            "current": {"label": args["period"]["label"], "partial": partial},
            "previous": {"label": args["previous_period"]["label"]},
            "rows": rows,
        }
        return data, [], sum(r["current"] + r["previous"] for r in rows)

    # --- 2. Hotspots ---

    async def _location_hotspots(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        level, source = args["level"], args["source"]
        key = {"building": "building_id", "floor": "floor_id", "area": "area_id"}[level]
        name = {
            "building": "{a}.building_name",
            "floor": "concat_ws(' › ', {a}.building_name, {a}.floor_name)",
            "area": "concat_ws(' › ', {a}.building_name, {a}.floor_name, {a}.area_name)",
        }[level]
        parts = []
        for kind, alias, table in (
            ("alerts", "a", "alerts"),
            ("incidents", "i", "incidents"),
            ("manual_reports", "m", "manual_reports"),
        ):
            if source in (kind, "all"):
                flags = ", ".join("1" if k == kind else "0" for k in ("alerts", "incidents", "manual_reports"))
                parts.append(
                    f"""SELECT {alias}.{key} AS id, {name.format(a=alias)} AS name, {flags}
                          FROM {table} {alias} WHERE {alias}.organization_id = :org AND {alias}.campus_id = :campus
                           AND {alias}.created_at >= :since AND {alias}.created_at < :until"""
                )
        rows = await self._rows(
            f"""SELECT x.id, max(x.name) AS name, sum(x.a) AS alerts, sum(x.i) AS incidents, sum(x.m) AS reports
                  FROM ({" UNION ALL ".join(parts)}) AS x(id, name, a, i, m)
                 GROUP BY x.id ORDER BY sum(x.a) + sum(x.i) + sum(x.m) DESC, max(x.name)""",
            since=since,
            until=until,
        )
        items: list[dict[str, Any]] = []
        without = 0
        for r in rows:
            total = int(r.alerts) + int(r.incidents) + int(r.reports)
            if r.id is None:
                without += total
                continue
            items.append(
                {
                    "id": str(r.id),
                    "name": r.name or "Unnamed place",
                    "total": total,
                    "alerts": int(r.alerts),
                    "incidents": int(r.incidents),
                    "manual_reports": int(r.reports),
                }
            )
        citations = [Citation.of("LOCATION", x["id"], x["name"]) for x in items[:5]]
        data = {"level": level, "source": source, "items": items[:25], "without_location": without}
        return data, citations, len(items)

    async def _camera_reliability(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        loc, params = _loc("p", args.get("location"))
        cameras = await self._rows(
            f"""SELECT p.id, p.display_name, p.place, COALESCE(s.state, 'UNKNOWN') AS state
                  FROM {_CAMERA_PLACE} p LEFT JOIN camera_status s ON s.camera_id = p.id
                 WHERE p.organization_id = :org AND p.campus_id = :campus{loc} ORDER BY p.display_name""",
            **params,
        )
        ids = [r.id for r in cameras]
        before = await self._rows(
            """SELECT DISTINCT ON (e.camera_id) e.camera_id, e.state FROM camera_status_events e
                WHERE e.organization_id = :org AND e.campus_id = :campus AND e.camera_id = ANY(:ids)
                  AND e.changed_at < :since ORDER BY e.camera_id, e.changed_at DESC""",
            ids=ids,
            since=since,
        )
        during = await self._rows(
            """SELECT e.camera_id, e.state, e.changed_at FROM camera_status_events e
                WHERE e.organization_id = :org AND e.campus_id = :campus AND e.camera_id = ANY(:ids)
                  AND e.changed_at >= :since AND e.changed_at < :until ORDER BY e.camera_id, e.changed_at""",
            ids=ids,
            since=since,
            until=until,
        )
        state = {r.camera_id: r.state for r in before}
        events: dict[Any, list[Any]] = {}
        for r in during:
            events.setdefault(r.camera_id, []).append(r)
        end = min(until, datetime.now(UTC))
        items = []
        for cam in cameras:
            current, at, offline, count = state.get(cam.id), since, 0.0, 0
            for e in events.get(cam.id, []):
                if current == "OFFLINE":
                    offline += (e.changed_at - at).total_seconds()
                if e.state == "OFFLINE" and current != "OFFLINE":
                    count += 1
                current, at = e.state, e.changed_at
            if current == "OFFLINE":
                offline += max(0.0, (end - at).total_seconds())
            items.append(
                {
                    "id": str(cam.id),
                    "name": cam.display_name,
                    "location": cam.place or None,
                    "offline_events": count,
                    "offline_seconds": round(offline, 1),
                    "current_state": cam.state,
                }
            )
        items.sort(key=lambda x: (-x["offline_events"], -x["offline_seconds"], x["name"]))
        affected = [x for x in items if x["offline_events"] or x["offline_seconds"]]
        citations = [Citation.of("CAMERA", x["id"], x["name"]) for x in affected[:5]]
        return {"items": items[:50], "cameras_total": len(items)}, citations, len(affected)

    # --- 3. Case investigation ---

    async def _case(self, args: dict[str, Any]) -> Any:
        case = args.get("case") or {}
        number = None
        if case.get("reference"):
            match = re.search(r"(\d+)", case["reference"])
            number = int(match.group(1)) if match else None
        case_id = UUID(case["id"]) if case.get("id") else None
        row = await self._one(
            """SELECT i.id, i.number, i.title, i.status, i.priority, i.assigned_name, i.created_at, i.occurred_at,
                      i.camera_id, i.camera_name, i.building_id, i.floor_id, i.building_name, i.floor_name,
                      i.area_name, i.closed_at
                 FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                  AND (i.id = :id OR i.number = :number) LIMIT 1""",
            id=case_id,
            number=number,
        )
        if row is None:
            raise CaseNotFound
        return row

    @staticmethod
    def _case_dict(row: Any) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "reference": reference(row.number),
            "title": row.title,
            "status": row.status,
            "priority": row.priority,
            "assigned_name": row.assigned_name,
            "created_at": _iso(row.created_at),
            "occurred_at": _iso(row.occurred_at),
            "place": _place(row.building_name, row.floor_name, row.area_name),
        }

    async def _linked_alerts(self, incident_id: UUID) -> list[Any]:
        return await self._rows(
            """SELECT a.id, a.alert_type, a.camera_id, a.camera_name, a.created_at, a.detected_at, a.last_detected_at,
                      a.building_id, a.floor_id, a.building_name, a.status
                 FROM incident_alerts l JOIN alerts a ON a.id = l.alert_id
                WHERE l.incident_id = :incident AND l.organization_id = :org AND l.campus_id = :campus
                ORDER BY a.created_at""",
            incident=incident_id,
        )

    async def _case_timeline(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        case = await self._case(args)
        alerts = await self._linked_alerts(case.id)
        alert_ids = [a.id for a in alerts]
        events: list[dict[str, Any]] = []
        citations = [Citation.of("INCIDENT", case.id, f"{reference(case.number)} · {case.title}")]
        for a in alerts:
            citations.append(Citation.of("ALERT", a.id, _alert_label(a)))
        for e in await self._rows(
            """SELECT e.alert_id, e.event_type, e.actor_role, e.details, e.created_at, a.alert_type, a.camera_name,
                      COALESCE(u.full_name, e.actor_role) AS actor
                 FROM alert_events e JOIN alerts a ON a.id = e.alert_id LEFT JOIN users u ON u.id = e.actor_user_id
                WHERE e.alert_id = ANY(:ids) AND e.organization_id = :org AND e.campus_id = :campus
                  AND e.event_type <> 'DETECTION_REPEATED'
                ORDER BY e.created_at""",
            ids=alert_ids,
        ):
            details = _json(e.details) or {}
            label = _EVENT_LABELS.get(e.event_type, e.event_type.replace("_", " ").title())
            detail = ""
            if e.event_type == "CREATED":
                label = f"{ALERT_TYPE_LABELS.get(e.alert_type, e.alert_type)} alert at {e.camera_name}"
            elif e.event_type == "ROUTED":
                detail = f"{details.get('guards', 0)} Guards notified"
            elif e.event_type == "ASSESSMENT":
                action = details.get("action") or details.get("assessment")
                label = f"Assessment: {_ASSESSMENTS.get(str(action), str(action))}"
                detail = f"by {e.actor}"
            elif e.actor_role != "SYSTEM":
                detail = f"by {e.actor}"
            events.append(
                {
                    "at": _iso(e.created_at),
                    "label": label,
                    "detail": detail,
                    "entity_type": "ALERT",
                    "entity_id": str(e.alert_id),
                }
            )
        for e in await self._rows(
            """SELECT e.kind, e.actor_name, e.actor_role, e.from_status, e.to_status, e.note, e.details, e.occurred_at
                 FROM incident_events e WHERE e.incident_id = :incident AND e.organization_id = :org
                  AND e.campus_id = :campus ORDER BY e.occurred_at, e.seq""",
            incident=case.id,
        ):
            details = _json(e.details) or {}
            kind = e.kind.replace("_", " ").capitalize()
            label = {
                "CREATED": "Case opened",
                "STATUS_CHANGED": f"Status → {(e.to_status or '').replace('_', ' ').lower()}",
                "NOTE_ADDED": "Investigation note",
                "ASSIGNED": "Case assigned",
            }.get(e.kind, kind)
            detail = _excerpt(e.note) if e.note else ""
            if e.kind == "ASSIGNED":
                detail = str(details.get("assigned_name") or details.get("name") or "")
            events.append(
                {
                    "at": _iso(e.occurred_at),
                    "label": f"{label} ({e.actor_name})",
                    "detail": detail,
                    "entity_type": "INCIDENT",
                    "entity_id": str(case.id),
                }
            )
        messages = 0
        for m in await self._rows(
            """SELECT 'Alert chat' AS kind, m.alert_id AS target, m.sender_name, m.sender_role, m.body, m.created_at
                 FROM alert_messages m
                WHERE m.alert_id = ANY(:ids) AND m.organization_id = :org AND m.campus_id = :campus
               UNION ALL
               SELECT 'Case chat', m.incident_id, m.sender_name, m.sender_role, m.body, m.created_at
                 FROM incident_messages m WHERE m.incident_id = :incident AND m.organization_id = :org
                  AND m.campus_id = :campus
               ORDER BY 6""",
            ids=alert_ids,
            incident=case.id,
        ):
            messages += 1
            events.append(
                {
                    "at": _iso(m.created_at),
                    "label": f"{m.kind}: {m.sender_name} ({m.sender_role.lower()})",
                    "detail": _excerpt(m.body),
                }
            )
        reports = await self._rows(
            """SELECT m.id, m.report_type, m.reporter_name, m.created_at FROM manual_reports m
                WHERE m.incident_id = :incident AND m.organization_id = :org AND m.campus_id = :campus""",
            incident=case.id,
        )
        for r in reports:
            citations.append(Citation.of("MANUAL_REPORT", r.id, f"Manual report · {r.report_type}"))
            events.append(
                {
                    "at": _iso(r.created_at),
                    "label": f"Manual report: {r.report_type}",
                    "detail": f"by {r.reporter_name}",
                    "entity_type": "MANUAL_REPORT",
                    "entity_id": str(r.id),
                }
            )
        evidence = await self._rows(
            """SELECT e.id, e.title, e.kind, e.saved_by_name, e.saved_at FROM evidence_items e
                WHERE e.incident_id = :incident AND e.organization_id = :org AND e.campus_id = :campus""",
            incident=case.id,
        )
        for e in evidence:
            citations.append(Citation.of("EVIDENCE", e.id, f"Evidence · {e.title}"))
            events.append(
                {
                    "at": _iso(e.saved_at),
                    "label": f"Evidence saved: {e.title} ({e.kind.lower()})",
                    "detail": f"by {e.saved_by_name}",
                    "entity_type": "EVIDENCE",
                    "entity_id": str(e.id),
                }
            )
        for c in await self._rows(
            """SELECT c.observed_at, c.camera_name, c.review, c.band FROM person_search_candidates c
                WHERE c.incident_id = :incident AND c.organization_id = :org AND c.campus_id = :campus""",
            incident=case.id,
        ):
            events.append(
                {
                    "at": _iso(c.observed_at),
                    "label": f"Person Search result linked ({c.review.replace('_', ' ').lower()})",
                    "detail": f"{c.camera_name}; a possible match for review, not an identity",
                }
            )
        events.sort(key=lambda e: e["at"] or "")
        data = {
            "case": self._case_dict(case),
            "events": events[:300],
            "counts": {
                "alerts": len(alerts),
                "manual_reports": len(reports),
                "evidence": len(evidence),
                "messages": messages,
            },
        }
        return data, citations, len(events)

    async def _related_events(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        case = await self._case(args)
        window = timedelta(minutes=int(args.get("window_minutes", 30)))
        alerts = await self._linked_alerts(case.id)
        anchor_at = alerts[0].created_at if alerts else (case.occurred_at or case.created_at)
        building = case.building_id or next((a.building_id for a in alerts if a.building_id), None)
        building_name = case.building_name or next((a.building_name for a in alerts if a.building_name), None)
        own_cameras = [c for c in {case.camera_id, *(a.camera_id for a in alerts)} if c is not None]
        floors = [f for f in {case.floor_id, *(a.floor_id for a in alerts)} if f is not None]
        nearby = await self._rows(
            f"""SELECT p.id FROM {_CAMERA_PLACE} p WHERE p.organization_id = :org AND p.campus_id = :campus
                  AND (p.id = ANY(CAST(:own AS uuid[])) OR p.floor_id = ANY(CAST(:floors AS uuid[]))
                       OR (CAST(:building AS uuid) IS NOT NULL AND p.building_id = CAST(:building AS uuid)))""",
            own=own_cameras,
            floors=floors,
            building=building,
        )
        cameras = [r.id for r in nearby]
        since, until = anchor_at - window, anchor_at + window
        whole_campus = building is None and not cameras
        linked = [a.id for a in alerts]
        other_alerts = await self._rows(
            """SELECT a.id, a.alert_type, a.camera_name, a.created_at, a.building_name, a.floor_name, a.area_name
                 FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                  AND a.created_at BETWEEN :since AND :until AND NOT (a.id = ANY(CAST(:linked AS uuid[])))
                  AND (CAST(:whole AS boolean) OR a.building_id = CAST(:building AS uuid)
                       OR a.camera_id = ANY(CAST(:cameras AS uuid[])))
                ORDER BY a.created_at LIMIT 50""",
            since=since,
            until=until,
            linked=linked,
            whole=whole_campus,
            building=building,
            cameras=cameras,
        )
        reports = await self._rows(
            """SELECT m.id, m.report_type, m.created_at, m.building_name, m.floor_name, m.area_name
                 FROM manual_reports m WHERE m.organization_id = :org AND m.campus_id = :campus
                  AND COALESCE(m.occurred_at, m.created_at) BETWEEN :since AND :until
                  AND m.incident_id IS DISTINCT FROM CAST(:incident AS uuid)
                  AND (CAST(:whole AS boolean) OR m.building_id = CAST(:building AS uuid))
                ORDER BY m.created_at LIMIT 50""",
            since=since,
            until=until,
            incident=case.id,
            whole=whole_campus,
            building=building,
        )
        observations = await self._rows(
            """SELECT o.id, o.camera_id, o.camera_name, o.observed_at, o.building_name, o.floor_name, o.area_name
                 FROM reid_observations o WHERE o.organization_id = :org AND o.campus_id = :campus
                  AND o.observed_at BETWEEN :since AND :until
                  AND (CAST(:whole AS boolean) OR o.camera_id = ANY(CAST(:cameras AS uuid[]))
                       OR o.building_name = CAST(:building_name AS text))
                ORDER BY o.observed_at LIMIT 50""",
            since=since,
            until=until,
            whole=whole_campus,
            cameras=cameras,
            building_name=building_name,
        )
        citations = [Citation.of("ALERT", r.id, _alert_label(r)) for r in other_alerts[:10]]
        citations += [Citation.of("MANUAL_REPORT", r.id, f"Manual report · {r.report_type}") for r in reports[:10]]
        seen_cams: set[Any] = set()
        for o in observations:
            if o.camera_id and o.camera_id not in seen_cams and len(seen_cams) < 5:
                seen_cams.add(o.camera_id)
                citations.append(Citation.of("CAMERA", o.camera_id, o.camera_name))
        data = {
            "case": self._case_dict(case),
            "window_minutes": int(window.total_seconds() // 60),
            "anchor": {
                "at": _iso(anchor_at),
                "building": building_name or ("the whole campus" if whole_campus else None),
            },
            "alerts": [
                {
                    "id": str(r.id),
                    "at": _iso(r.created_at),
                    "label": _alert_label(r),
                    "place": _place(r.building_name, r.floor_name, r.area_name),
                }
                for r in other_alerts
            ],
            "manual_reports": [
                {
                    "id": str(r.id),
                    "at": _iso(r.created_at),
                    "label": r.report_type,
                    "place": _place(r.building_name, r.floor_name, r.area_name),
                }
                for r in reports
            ],
            "reid_observations": [
                {
                    "id": str(o.id),
                    "at": _iso(o.observed_at),
                    "label": f"Person observed on {o.camera_name}",
                    "place": _place(o.building_name, o.floor_name, o.area_name),
                }
                for o in observations
            ],
        }
        return data, citations, len(other_alerts) + len(reports) + len(observations)

    async def _evidence_gaps(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        case = await self._case(args)
        alerts = await self._linked_alerts(case.id)
        windows: list[tuple[UUID | None, str, datetime, datetime]] = []
        if case.camera_id or case.camera_name:
            at = case.occurred_at or case.created_at
            windows.append(
                (case.camera_id, case.camera_name or "Case camera", at - INCIDENT_WINDOW, at + INCIDENT_WINDOW)
            )
        for a in alerts:
            windows.append(
                (a.camera_id, a.camera_name, a.detected_at - ALERT_WINDOW, a.last_detected_at + ALERT_WINDOW)
            )
        gaps: list[dict[str, Any]] = []
        found = await SqlIncidentRepository(self._session, self._actor).footage_gaps(windows)
        for g in found:
            detail = ""
            if g.issue == "PARTIAL_RECORDING":
                wanted = int((g.window_to - g.window_from).total_seconds())
                detail = f"{int(g.covered_seconds)} of {wanted} s recorded"
            gaps.append({"kind": g.issue, "camera_name": g.camera_name, "detail": detail})
        for camera_id, camera_name, start, end in windows:
            if camera_id is None:
                continue
            went = await self._one(
                """SELECT count(*) AS n FROM camera_status_events e WHERE e.camera_id = :camera AND e.state = 'OFFLINE'
                      AND e.organization_id = :org AND e.campus_id = :campus
                      AND e.changed_at BETWEEN :since AND :until""",
                camera=camera_id,
                since=start,
                until=end,
            )
            if went and int(went.n):
                gaps.append({"kind": "OFFLINE_DURING_WINDOW", "camera_name": camera_name, "detail": ""})
        if not windows:
            gaps.append(
                {
                    "kind": "NO_CAMERA",
                    "camera_name": None,
                    "detail": "no camera on the case or its alerts, so footage could not be checked",
                }
            )
        evidence = await self._one(
            """SELECT count(*) AS n FROM evidence_items e WHERE e.organization_id = :org AND e.campus_id = :campus
                  AND (e.incident_id = :incident OR e.alert_id = ANY(:alerts))""",
            incident=case.id,
            alerts=[a.id for a in alerts],
        )
        evidence_count = int(evidence.n) if evidence else 0
        if evidence_count == 0:
            gaps.append({"kind": "NO_EVIDENCE_SAVED", "camera_name": None, "detail": "no clip or snapshot saved"})
        pending = await self._one(
            """SELECT count(*) AS n FROM person_search_candidates c
                 LEFT JOIN person_searches s ON s.id = c.search_id
                WHERE c.organization_id = :org AND c.campus_id = :campus AND c.review = 'PENDING'
                  AND (c.incident_id = :incident OR s.incident_id = :incident)""",
            incident=case.id,
        )
        if pending and int(pending.n):
            gaps.append(
                {
                    "kind": "UNVERIFIED_REID",
                    "camera_name": None,
                    "detail": f"{int(pending.n)} candidates still pending review",
                }
            )
        citations = [Citation.of("INCIDENT", case.id, f"{reference(case.number)} · {case.title}")]
        data = {
            "case": self._case_dict(case),
            "gaps": gaps,
            "evidence_count": evidence_count,
            "checked_cameras": len({w[0] for w in windows if w[0] is not None}),
        }
        return data, citations, max(1, len(windows) + evidence_count)

    # --- 4. Guard response and audit ---

    async def _guard_response(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        loc, params = _loc("a", args.get("location"))
        base = f"""SELECT a.* FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                     AND a.created_at >= :since AND a.created_at < :until{loc}"""
        times = await self._one(
            f"""WITH scoped AS ({base}), firsts AS (
                    SELECT s.id, s.created_at, s.closed_at, s.status,
                           (SELECT min(r.acknowledged_at) FROM alert_recipients r WHERE r.alert_id = s.id) AS ack,
                           (SELECT min(r.responding_at) FROM alert_recipients r WHERE r.alert_id = s.id) AS resp
                      FROM scoped s)
                SELECT count(*) AS alerts, count(ack) AS acknowledged, count(resp) AS responded,
                       count(*) FILTER (WHERE status = 'RESOLVED') AS resolved,
                       count(*) FILTER (WHERE ack IS NULL) AS unacknowledged,
                       avg(EXTRACT(EPOCH FROM ack - created_at)) AS ack_avg,
                       avg(EXTRACT(EPOCH FROM resp - created_at)) AS resp_avg,
                       avg(EXTRACT(EPOCH FROM closed_at - created_at)) FILTER (WHERE status = 'RESOLVED') AS res_avg
                  FROM firsts""",
            since=since,
            until=until,
            **params,
        )
        events = await self._one(
            f"""WITH scoped AS ({base})
                SELECT count(*) FILTER (WHERE e.event_type = 'AUTO_ESCALATED') AS auto,
                       count(*) FILTER (WHERE e.event_type = 'ESCALATED') AS manual,
                       count(*) FILTER (WHERE e.event_type = 'ASSISTANCE_BROADCAST') AS broadcasts,
                       count(*) FILTER (WHERE e.event_type = 'ASSESSMENT' AND COALESCE(e.details->>'action',
                                        e.details->>'assessment') = 'ADDITIONAL_GUARDS_REQUIRED') AS additional,
                       count(DISTINCT e.alert_id) FILTER (WHERE e.event_type IN ('ESCALATED', 'AUTO_ESCALATED'))
                           AS escalated_alerts
                  FROM alert_events e JOIN scoped s ON s.id = e.alert_id""",
            since=since,
            until=until,
            **params,
        )
        places = await self._rows(
            f"""WITH scoped AS ({base})
                SELECT concat_ws(' › ', s.building_name, s.floor_name, s.area_name) AS name,
                       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM alert_recipients r WHERE r.alert_id = s.id
                                                           AND r.acknowledged_at IS NOT NULL)) AS unacknowledged,
                       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM alert_events e WHERE e.alert_id = s.id
                                                       AND e.event_type IN ('ESCALATED', 'AUTO_ESCALATED')))
                           AS escalated
                  FROM scoped s GROUP BY 1 ORDER BY 2 DESC, 3 DESC, 1 LIMIT 10""",
            since=since,
            until=until,
            **params,
        )
        modes = await self._rows(
            f"""WITH scoped AS ({base})
                SELECT (g.guard_user_id IS NOT NULL) AS stationed, count(*) AS received,
                       count(r.acknowledged_at) AS acknowledged, count(r.responding_at) AS responding
                  FROM alert_recipients r JOIN scoped s ON s.id = r.alert_id
                  LEFT JOIN guard_location_assignments g ON g.guard_user_id = r.guard_user_id
                 GROUP BY 1""",
            since=since,
            until=until,
            **params,
        )
        mode = {
            True: {"received": 0, "acknowledged": 0, "responding": 0},
            False: {"received": 0, "acknowledged": 0, "responding": 0},
        }
        for m in modes:
            mode[bool(m.stationed)] = {
                "received": int(m.received),
                "acknowledged": int(m.acknowledged),
                "responding": int(m.responding),
            }
        total = int(times.alerts)
        enabled, high, standard = self._thresholds
        escalated = int(events.escalated_alerts or 0)

        def secs(value: Any) -> float | None:
            return None if value is None else round(float(value), 1)

        data = {
            "alerts": total,
            "acknowledged": int(times.acknowledged),
            "responded": int(times.responded),
            "resolved": int(times.resolved),
            "unacknowledged": int(times.unacknowledged),
            "acknowledge_avg_seconds": secs(times.ack_avg),
            "respond_avg_seconds": secs(times.resp_avg),
            "resolve_avg_seconds": secs(times.res_avg),
            "additional_guards_requests": int(events.additional or 0),
            "auto_escalations": int(events.auto or 0),
            "manual_escalations": int(events.manual or 0),
            "assistance_broadcasts": int(events.broadcasts or 0),
            "escalated_alerts": escalated,
            "escalation_rate_pct": round(escalated / total * 100) if total else 0,
            "thresholds": {"enabled": enabled, "high_seconds": high, "standard_seconds": standard},
            "by_location": [
                {"name": p.name or "No place", "unacknowledged": int(p.unacknowledged), "escalated": int(p.escalated)}
                for p in places
            ],
            "by_mode": {"stationed": mode[True], "standby": mode[False]},
        }
        return data, [], total

    async def _audit_activity(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        supervisor = self._role == "SUPERVISOR"
        own = " AND (l.category = ANY(:operational) OR l.actor_user_id = :me)" if supervisor else ""
        category = " AND l.category = :category" if args.get("category") else ""
        rows = await self._rows(
            f"""SELECT l.category, l.action, count(*) AS total FROM organization_audit_logs l
                 WHERE l.organization_id = :org AND l.campus_id = :campus
                   AND l.occurred_at >= :since AND l.occurred_at < :until{own}{category}
                 GROUP BY l.category, l.action""",
            since=since,
            until=until,
            operational=list(OPERATIONAL_AUDIT_CATEGORIES),
            me=self._actor.id,
            category=args.get("category"),
        )
        by_category: dict[str, int] = {}
        for r in rows:
            by_category[r.category] = by_category.get(r.category, 0) + int(r.total)
        data: dict[str, Any] = {
            "total": sum(by_category.values()),
            "role_scope": self._role,
            "by_category": [
                {"category": k, "count": v} for k, v in sorted(by_category.items(), key=lambda x: (-x[1], x[0]))
            ],
            "by_action": sorted(
                ({"action": r.action, "count": int(r.total)} for r in rows), key=lambda x: (-x["count"], x["action"])
            )[:15],
        }
        return data, [], data["total"]

    # --- 5. Search ---

    async def _manual_report_search(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        since, until = _period(args)
        loc, params = _loc("m", args.get("location"))
        kind = " AND lower(m.report_type) = lower(:report_type)" if args.get("report_type") else ""
        where = f"""m.organization_id = :org AND m.campus_id = :campus AND m.created_at >= :since
                    AND m.created_at < :until{loc}{kind}"""
        groups = await self._rows(
            f"""SELECT m.report_type, m.status, count(*) AS total, count(m.incident_id) AS linked
                  FROM manual_reports m WHERE {where} GROUP BY m.report_type, m.status""",
            since=since,
            until=until,
            report_type=args.get("report_type"),
            **params,
        )
        latest = await self._rows(
            f"""SELECT m.id, m.report_type, m.status, m.created_at, m.description, m.building_name, m.floor_name,
                       m.area_name FROM manual_reports m WHERE {where} ORDER BY m.created_at DESC LIMIT 10""",
            since=since,
            until=until,
            report_type=args.get("report_type"),
            **params,
        )
        by_type: dict[str, int] = {}
        for g in groups:
            by_type[g.report_type] = by_type.get(g.report_type, 0) + int(g.total)
        total = sum(by_type.values())
        data = {
            "total": total,
            "by_type": [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: (-x[1], x[0]))],
            "reviewed": sum(int(g.total) for g in groups if g.status == "REVIEWED"),
            "submitted": sum(int(g.total) for g in groups if g.status == "SUBMITTED"),
            "linked": sum(int(g.linked) for g in groups),
            "items": [
                {
                    "id": str(r.id),
                    "at": _iso(r.created_at),
                    "type": r.report_type,
                    "status": r.status,
                    "place": _place(r.building_name, r.floor_name, r.area_name),
                    "excerpt": _excerpt(r.description, 100),
                }
                for r in latest
            ],
        }
        citations = [Citation.of("MANUAL_REPORT", r.id, f"Manual report · {r.report_type}") for r in latest]
        return data, citations, total

    async def _evidence_search(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        clauses = ["e.organization_id = :org", "e.campus_id = :campus"]
        params: dict[str, Any] = {}
        if args.get("period"):
            since, until = _period(args)
            clauses.append("e.saved_at >= :since AND e.saved_at < :until")
            params.update(since=since, until=until)
        if args.get("tags"):
            clauses.append("EXISTS (SELECT 1 FROM unnest(e.tags) t WHERE lower(t) = ANY(:tags))")
            params["tags"] = [t.lower() for t in args["tags"]]
        if args.get("camera"):
            clauses.append("e.camera_id = :camera")
            params["camera"] = UUID(args["camera"]["id"])
        if args.get("case"):
            case = await self._case({"case": args["case"]})
            clauses.append("e.incident_id = :incident")
            params["incident"] = case.id
        loc, loc_params = _loc("e", args.get("location"))
        where = " AND ".join(clauses) + loc
        params.update(loc_params)
        count = await self._one(f"SELECT count(*) AS n FROM evidence_items e WHERE {where}", **params)
        rows = await self._rows(
            f"""SELECT e.id, e.title, e.kind, e.tags, e.camera_name, e.saved_at, i.id AS case_id, i.number
                  FROM evidence_items e LEFT JOIN incidents i ON i.id = e.incident_id
                 WHERE {where} ORDER BY e.saved_at DESC LIMIT 20""",
            **params,
        )
        items = [
            {
                "id": str(r.id),
                "title": r.title,
                "kind": r.kind,
                "tags": list(r.tags or []),
                "camera_name": r.camera_name,
                "saved_at": _iso(r.saved_at),
                "case_reference": reference(r.number) if r.number else None,
            }
            for r in rows
        ]
        citations = [Citation.of("EVIDENCE", r.id, f"Evidence · {r.title}") for r in rows[:10]]
        citations += [Citation.of("INCIDENT", r.case_id, reference(r.number)) for r in rows if r.case_id][:5]
        total = int(count.n) if count else 0
        return {"total": total, "items": items}, citations, total

    async def _case_list(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[Citation], int]:
        source = args["source"]
        alias = "i" if source == "incidents" else "a"
        clauses = [f"{alias}.organization_id = :org", f"{alias}.campus_id = :campus"]
        params: dict[str, Any] = {}
        if args.get("period"):
            since, until = _period(args)
            clauses.append(f"{alias}.created_at >= :since AND {alias}.created_at < :until")
            params.update(since=since, until=until)
        if args.get("unresolved_only"):
            clauses.append(f"{alias}.status NOT IN {_CLOSED}")
        loc, loc_params = _loc(alias, args.get("location"))
        params.update(loc_params)
        where = " AND ".join(clauses) + loc
        if source == "incidents":
            rows = await self._rows(
                f"""SELECT i.id, i.number, i.title, i.status, i.assigned_name AS handler, i.building_name, i.floor_name,
                           i.area_name FROM incidents i WHERE {where} ORDER BY i.created_at DESC""",
                **params,
            )
            items = [
                {
                    "id": str(r.id),
                    "reference": reference(r.number),
                    "title": r.title,
                    "status": r.status,
                    "handler": r.handler,
                    "place": _place(r.building_name, r.floor_name, r.area_name),
                }
                for r in rows
            ]
            citations = [Citation.of("INCIDENT", r.id, f"{reference(r.number)} · {r.title}") for r in rows[:10]]
        else:
            rows = await self._rows(
                f"""SELECT a.id, a.alert_type, a.camera_name, a.status, a.handled_by_name AS handler, a.building_name,
                           a.floor_name, a.area_name FROM alerts a WHERE {where} ORDER BY a.created_at DESC""",
                **params,
            )
            items = [
                {
                    "id": str(r.id),
                    "reference": _alert_label(r),
                    "title": r.camera_name,
                    "status": r.status,
                    "handler": r.handler,
                    "place": _place(r.building_name, r.floor_name, r.area_name),
                }
                for r in rows
            ]
            citations = [Citation.of("ALERT", r.id, _alert_label(r)) for r in rows[:10]]
        handlers: dict[str, int] = {}
        for item in items:
            handlers[item["handler"] or "Unassigned"] = handlers.get(item["handler"] or "Unassigned", 0) + 1
        data = {
            "source": source,
            "total": len(items),
            "handlers": [{"name": k, "count": v} for k, v in sorted(handlers.items(), key=lambda x: (-x[1], x[0]))],
            "items": items[:20],
        }
        return data, citations, len(items)
