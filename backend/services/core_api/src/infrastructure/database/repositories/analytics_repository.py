"""Reports & Analytics and the Dashboard (Org Admin pages 1 and 22, Supervisor pages 1 and 18; decision D-024).

Every figure is computed from the records themselves (alerts, recipients, cases, manual reports, camera
status changes, recordings and evidence) for the caller's campus, under Row-Level Security; days are
counted in the organization's time zone. Nothing here is estimated or invented: a figure without data is
reported as empty or null.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.user import User

_UUIDS = ("org", "campus", "building", "floor", "area")
_PARAM = re.compile(r"(?<!:):([a-z_]+)\b")


@dataclass(frozen=True, slots=True)
class Scope:
    since: datetime
    until: datetime
    time_zone: str
    building_id: UUID | None = None
    floor_id: UUID | None = None
    area_id: UUID | None = None


def _location(alias: str, scope: Scope) -> str:
    clauses = []
    if scope.building_id is not None:
        clauses.append(f"{alias}.building_id = :building")
    if scope.floor_id is not None:
        clauses.append(f"{alias}.floor_id = :floor")
    if scope.area_id is not None:
        clauses.append(f"{alias}.area_id = :area")
    return "".join(f" AND {c}" for c in clauses)


_CAMERA_PLACE = """(SELECT COALESCE(c.building_id, f.building_id, ar.building_id) AS building_id,
                           COALESCE(c.floor_id, ar.floor_id) AS floor_id, c.area_id, c.id, c.display_name,
                           c.organization_id, c.campus_id
                      FROM cameras c
                      LEFT JOIN areas ar ON ar.id = c.area_id
                      LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, ar.floor_id))"""


def _seconds(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)


class SqlAnalyticsRepository:
    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._session = session
        self._base = {"org": actor.organization_id, "campus": actor.campus_id}

    async def _rows(self, statement: str, scope: Scope | None = None, **extra: Any) -> list[Any]:
        values: dict[str, Any] = {**self._base, **extra}
        if scope is not None:
            values.update(
                since=scope.since, until=scope.until, tz=scope.time_zone, building=scope.building_id,
                floor=scope.floor_id, area=scope.area_id,
            )  # fmt: skip
        used = set(_PARAM.findall(statement))
        values = {k: v for k, v in values.items() if k in used}
        bound = text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in values))
        return list(await self._session.execute(bound, values))

    # --- Counters ---------------------------------------------------------------------------------------

    async def counters(self, scope: Scope) -> dict[str, Any]:
        cam = _location("p", scope)
        alert = _location("a", scope)
        incident = _location("i", scope)
        report = _location("m", scope)
        [row] = await self._rows(
            f"""SELECT
                 (SELECT count(*) FROM {_CAMERA_PLACE} p WHERE p.organization_id = :org AND p.campus_id = :campus{cam})
                    AS cameras_total,
                 (SELECT count(*) FROM {_CAMERA_PLACE} p JOIN camera_status s ON s.camera_id = p.id
                   WHERE p.organization_id = :org AND p.campus_id = :campus AND s.state = 'ONLINE'{cam})
                    AS cameras_online,
                 (SELECT count(*) FROM {_CAMERA_PLACE} p JOIN camera_status s ON s.camera_id = p.id
                   WHERE p.organization_id = :org AND p.campus_id = :campus AND s.state = 'OFFLINE'{cam})
                    AS cameras_offline,
                 (SELECT count(*) FROM users u WHERE u.organization_id = :org AND u.campus_id = :campus
                     AND u.role = 'GUARD' AND u.deleted_at IS NULL) AS guards_total,
                 (SELECT count(*) FROM users u WHERE u.organization_id = :org AND u.campus_id = :campus
                     AND u.role = 'GUARD' AND u.status = 'ACTIVE') AS guards_active,
                 (SELECT count(*) FROM users u JOIN guard_location_assignments g ON g.guard_user_id = u.id
                   WHERE u.organization_id = :org AND u.campus_id = :campus AND u.status = 'ACTIVE')
                    AS guards_stationed,
                 (SELECT count(*) FROM users u WHERE u.organization_id = :org AND u.campus_id = :campus
                     AND u.role = 'SUPERVISOR' AND u.deleted_at IS NULL) AS supervisors_total,
                 (SELECT count(*) FROM users u WHERE u.organization_id = :org AND u.campus_id = :campus
                     AND u.role = 'SUPERVISOR' AND u.status = 'ACTIVE') AS supervisors_active,
                 (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                     AND a.created_at BETWEEN :since AND :until{alert}) AS alerts_total,
                 (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                     AND a.priority = 'HIGH' AND a.created_at BETWEEN :since AND :until{alert}) AS alerts_high,
                 (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                     AND a.status = 'NEW'{alert}) AS alerts_pending,
                 (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                     AND a.status NOT IN ('RESOLVED', 'DISMISSED'){alert}) AS alerts_open,
                 (SELECT count(*) FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                     AND i.status IN ('OPEN', 'UNDER_INVESTIGATION', 'RESPONDING'){incident}) AS incidents_active,
                 (SELECT count(*) FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                     AND i.status = 'RESOLVED' AND i.closed_at BETWEEN :since AND :until{incident})
                    AS incidents_resolved,
                 (SELECT count(*) FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                     AND i.created_at BETWEEN :since AND :until{incident}) AS incidents_opened,
                 (SELECT count(*) FROM manual_reports m WHERE m.organization_id = :org AND m.campus_id = :campus
                     AND m.created_at BETWEEN :since AND :until{report}) AS manual_reports""",
            scope,
        )
        return dict(row._mapping)

    # --- Alerts -------------------------------------------------------------------------------------

    async def alerts_by_type(self, scope: Scope) -> list[dict[str, Any]]:
        rows = await self._rows(
            f"""SELECT a.alert_type, a.priority, count(*) AS total
                  FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                   AND a.created_at BETWEEN :since AND :until{_location("a", scope)}
                 GROUP BY a.alert_type, a.priority ORDER BY total DESC, a.alert_type""",
            scope,
        )
        return [{"alert_type": r.alert_type, "priority": r.priority, "count": int(r.total)} for r in rows]

    async def alerts_by_status(self, scope: Scope) -> list[dict[str, Any]]:
        rows = await self._rows(
            f"""SELECT a.status, count(*) AS total FROM alerts a
                 WHERE a.organization_id = :org AND a.campus_id = :campus
                   AND a.created_at BETWEEN :since AND :until{_location("a", scope)}
                 GROUP BY a.status ORDER BY a.status""",
            scope,
        )
        return [{"status": r.status, "count": int(r.total)} for r in rows]

    async def daily(self, scope: Scope) -> list[dict[str, Any]]:
        """Per day: alerts by priority, cases opened, manual reports."""
        rows = await self._rows(
            f"""WITH days AS (
                    SELECT generate_series(date_trunc('day', CAST(:since AS timestamptz) AT TIME ZONE :tz),
                                           date_trunc('day', CAST(:until AS timestamptz) AT TIME ZONE :tz),
                                           interval '1 day') AS day)
                SELECT d.day::date AS day,
                       (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                           AND a.priority = 'HIGH' AND a.created_at BETWEEN :since AND :until
                           AND date_trunc('day', a.created_at AT TIME ZONE :tz) = d.day{_location("a", scope)}) AS high,
                       (SELECT count(*) FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                           AND a.priority = 'STANDARD' AND a.created_at BETWEEN :since AND :until
                           AND date_trunc('day', a.created_at AT TIME ZONE :tz) = d.day{_location("a", scope)})
                           AS standard,
                       (SELECT count(*) FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                           AND i.created_at BETWEEN :since AND :until
                           AND date_trunc('day', i.created_at AT TIME ZONE :tz) = d.day{_location("i", scope)})
                           AS incidents,
                       (SELECT count(*) FROM manual_reports m WHERE m.organization_id = :org AND m.campus_id = :campus
                           AND m.created_at BETWEEN :since AND :until
                           AND date_trunc('day', m.created_at AT TIME ZONE :tz) = d.day{_location("m", scope)})
                           AS reports
                  FROM days d ORDER BY d.day""",
            scope,
        )
        return [
            {"day": r.day.isoformat(), "high": int(r.high), "standard": int(r.standard),
             "incidents": int(r.incidents), "manual_reports": int(r.reports)}
            for r in rows
        ]  # fmt: skip

    async def response_times(self, scope: Scope) -> dict[str, Any]:
        """Average and median seconds from an alert to the first acknowledgement, the first "I'm Responding"
        and its resolution; and from a case being opened to being resolved."""
        loc = _location("a", scope)
        [row] = await self._rows(
            f"""WITH firsts AS (
                    SELECT a.id, a.created_at, a.closed_at, a.status,
                           (SELECT min(r.acknowledged_at) FROM alert_recipients r WHERE r.alert_id = a.id) AS ack,
                           (SELECT min(r.responding_at) FROM alert_recipients r WHERE r.alert_id = a.id) AS responding
                      FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                       AND a.created_at BETWEEN :since AND :until{loc})
                SELECT count(*) AS alerts,
                       count(ack) AS acknowledged,
                       count(responding) AS responded,
                       count(*) FILTER (WHERE status = 'RESOLVED') AS resolved,
                       avg(EXTRACT(EPOCH FROM ack - created_at)) AS ack_avg,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM ack - created_at)) AS ack_median,
                       avg(EXTRACT(EPOCH FROM responding - created_at)) AS respond_avg,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM responding - created_at))
                           AS respond_median,
                       avg(EXTRACT(EPOCH FROM closed_at - created_at)) FILTER (WHERE status = 'RESOLVED')
                           AS resolve_avg,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM closed_at - created_at))
                           FILTER (WHERE status = 'RESOLVED') AS resolve_median
                  FROM firsts""",
            scope,
        )
        [cases] = await self._rows(
            f"""SELECT count(*) AS resolved, avg(EXTRACT(EPOCH FROM i.closed_at - i.created_at)) AS avg_seconds,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM i.closed_at - i.created_at))
                           AS median_seconds
                  FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus AND i.status = 'RESOLVED'
                   AND i.closed_at BETWEEN :since AND :until{_location("i", scope)}""",
            scope,
        )
        return {
            "alerts": int(row.alerts),
            "acknowledged": int(row.acknowledged),
            "responded": int(row.responded),
            "resolved": int(row.resolved),
            "acknowledge_avg_seconds": _seconds(row.ack_avg),
            "acknowledge_median_seconds": _seconds(row.ack_median),
            "respond_avg_seconds": _seconds(row.respond_avg),
            "respond_median_seconds": _seconds(row.respond_median),
            "resolve_avg_seconds": _seconds(row.resolve_avg),
            "resolve_median_seconds": _seconds(row.resolve_median),
            "incidents_resolved": int(cases.resolved),
            "incident_resolve_avg_seconds": _seconds(cases.avg_seconds),
            "incident_resolve_median_seconds": _seconds(cases.median_seconds),
        }

    async def guard_activity(self, scope: Scope) -> list[dict[str, Any]]:
        loc = _location("a", scope)
        rows = await self._rows(
            f"""SELECT u.display_id, u.full_name, u.status,
                       count(r.alert_id) AS received,
                       count(r.acknowledged_at) AS acknowledged,
                       count(r.responding_at) AS responding,
                       count(*) FILTER (WHERE r.assessment = 'UNDER_CONTROL') AS under_control,
                       count(*) FILTER (WHERE r.assessment = 'ADDITIONAL_GUARDS_REQUIRED') AS additional_required,
                       (SELECT count(*) FROM manual_reports m WHERE m.reporter_user_id = u.id
                           AND m.created_at BETWEEN :since AND :until{_location("m", scope)}) AS manual_reports
                  FROM users u
                  LEFT JOIN alert_recipients r ON r.guard_user_id = u.id
                       AND r.alert_id IN (SELECT a.id FROM alerts a WHERE a.organization_id = :org
                                             AND a.campus_id = :campus AND a.created_at BETWEEN :since AND :until{loc})
                 WHERE u.organization_id = :org AND u.campus_id = :campus AND u.role = 'GUARD' AND u.deleted_at IS NULL
                 GROUP BY u.id, u.display_id, u.full_name, u.status
                 ORDER BY received DESC, u.full_name""",
            scope,
        )
        return [
            {"guard_id": r.display_id, "full_name": r.full_name, "status": r.status, "received": int(r.received),
             "acknowledged": int(r.acknowledged), "responding": int(r.responding),
             "under_control": int(r.under_control), "additional_required": int(r.additional_required),
             "manual_reports": int(r.manual_reports)}
            for r in rows
        ]  # fmt: skip

    async def manual_reports(self, scope: Scope) -> dict[str, Any]:
        rows = await self._rows(
            f"""SELECT m.report_type, m.status, count(*) AS total, count(m.incident_id) AS linked
                  FROM manual_reports m WHERE m.organization_id = :org AND m.campus_id = :campus
                   AND m.created_at BETWEEN :since AND :until{_location("m", scope)}
                 GROUP BY m.report_type, m.status ORDER BY total DESC, m.report_type""",
            scope,
        )
        return {
            "by_type": [
                {
                    "report_type": r.report_type,
                    "status": r.status,
                    "count": int(r.total),
                    "linked_to_cases": int(r.linked),
                }
                for r in rows
            ],
            "total": sum(int(r.total) for r in rows),
            "reviewed": sum(int(r.total) for r in rows if r.status == "REVIEWED"),
        }

    async def locations(self, scope: Scope) -> list[dict[str, Any]]:
        """Alerts, cases and manual reports per building › floor › area (the names at the time)."""
        rows = await self._rows(
            f"""SELECT building, floor, area, sum(alerts) AS alerts, sum(incidents) AS incidents,
                       sum(reports) AS reports
                  FROM (
                    SELECT a.building_name AS building, a.floor_name AS floor, a.area_name AS area,
                           1 AS alerts, 0 AS incidents, 0 AS reports
                      FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                       AND a.created_at BETWEEN :since AND :until{_location("a", scope)}
                    UNION ALL
                    SELECT i.building_name, i.floor_name, i.area_name, 0, 1, 0
                      FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                       AND i.created_at BETWEEN :since AND :until{_location("i", scope)}
                    UNION ALL
                    SELECT m.building_name, m.floor_name, m.area_name, 0, 0, 1
                      FROM manual_reports m WHERE m.organization_id = :org AND m.campus_id = :campus
                       AND m.created_at BETWEEN :since AND :until{_location("m", scope)}) x
                 GROUP BY building, floor, area
                 ORDER BY sum(alerts) + sum(incidents) + sum(reports) DESC, building NULLS LAST, floor, area
                 LIMIT 100""",
            scope,
        )
        return [
            {"building": r.building, "floor": r.floor, "area": r.area, "alerts": int(r.alerts),
             "incidents": int(r.incidents), "manual_reports": int(r.reports)}
            for r in rows
        ]  # fmt: skip

    async def camera_status_events(self, scope: Scope) -> tuple[list[Any], list[Any]]:
        """Cameras in scope with their current state, and their state changes up to the end of the period."""
        cameras = await self._rows(
            f"""SELECT p.id, p.display_name, COALESCE(s.state, 'UNKNOWN') AS state
                  FROM {_CAMERA_PLACE} p LEFT JOIN camera_status s ON s.camera_id = p.id
                 WHERE p.organization_id = :org AND p.campus_id = :campus{_location("p", scope)}
                 ORDER BY p.display_name""",
            scope,
        )
        events = await self._rows(
            """SELECT e.camera_id, e.state, e.changed_at FROM camera_status_events e
                WHERE e.organization_id = :org AND e.campus_id = :campus AND e.changed_at <= :until
                ORDER BY e.camera_id, e.changed_at""",
            scope,
        )
        return cameras, events

    async def storage(self, scope: Scope) -> dict[str, Any]:
        daily = await self._rows(
            """SELECT date_trunc('day', s.created_at AT TIME ZONE :tz)::date AS day, sum(s.size_bytes) AS bytes,
                      count(*) AS segments
                 FROM recording_segments s WHERE s.organization_id = :org AND s.campus_id = :campus
                  AND s.created_at BETWEEN :since AND :until
                GROUP BY 1 ORDER BY 1""",
            scope,
        )
        [totals] = await self._rows(
            """SELECT (SELECT COALESCE(sum(size_bytes), 0) FROM recording_segments
                        WHERE organization_id = :org AND campus_id = :campus) AS recordings_bytes,
                      (SELECT count(*) FROM recording_segments
                        WHERE organization_id = :org AND campus_id = :campus) AS recordings_segments,
                      (SELECT min(started_at) FROM recording_segments
                        WHERE organization_id = :org AND campus_id = :campus) AS oldest_recording,
                      (SELECT COALESCE(sum(size_bytes), 0) FROM evidence_items
                        WHERE organization_id = :org AND campus_id = :campus) AS evidence_bytes,
                      (SELECT count(*) FROM evidence_items
                        WHERE organization_id = :org AND campus_id = :campus) AS evidence_items"""
        )
        node = await self._rows(
            "SELECT node, total_bytes, used_bytes, free_bytes, percent_used, level, reported_at "
            "FROM app_storage_status()"
        )
        return {
            "daily": [{"day": r.day.isoformat(), "bytes": int(r.bytes), "segments": int(r.segments)} for r in daily],
            "recordings_bytes": int(totals.recordings_bytes),
            "recordings_segments": int(totals.recordings_segments),
            "oldest_recording": totals.oldest_recording.isoformat() if totals.oldest_recording else None,
            "evidence_bytes": int(totals.evidence_bytes),
            "evidence_items": int(totals.evidence_items),
            "nodes": [
                {"node": r.node, "total_bytes": int(r.total_bytes), "used_bytes": int(r.used_bytes),
                 "free_bytes": int(r.free_bytes), "percent_used": float(r.percent_used), "level": r.level,
                 "reported_at": r.reported_at.isoformat()}
                for r in node
            ],
        }  # fmt: skip

    async def person_observations(self, scope: Scope) -> int:
        [row] = await self._rows(
            """SELECT count(*) AS total FROM reid_observations o WHERE o.organization_id = :org
                  AND o.campus_id = :campus AND o.observed_at BETWEEN :since AND :until""",
            scope,
        )
        return int(row.total)

    # --- Dashboard lists -----------------------------------------------------------------------------

    async def recent_alerts(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = await self._rows(
            """SELECT a.id, a.alert_type, a.priority, a.status, a.camera_name, a.building_name, a.floor_name,
                      a.area_name, a.created_at, a.confidence
                 FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus
                ORDER BY a.created_at DESC LIMIT :limit""",
            limit=limit,
        )
        return [
            {"id": str(r.id), "alert_type": r.alert_type, "priority": r.priority, "status": r.status,
             "camera_name": r.camera_name,
             "location": " › ".join(p for p in (r.building_name, r.floor_name, r.area_name) if p) or None,
             "created_at": r.created_at.isoformat(),
             "confidence": float(r.confidence) if r.confidence is not None else None}
            for r in rows
        ]  # fmt: skip

    async def urgent_alerts(self) -> list[dict[str, Any]]:
        rows = await self._rows(
            """SELECT a.id, a.alert_type, a.camera_name, a.status, a.created_at
                 FROM alerts a WHERE a.organization_id = :org AND a.campus_id = :campus AND a.priority = 'HIGH'
                  AND a.status IN ('NEW', 'ACKNOWLEDGED', 'RESPONDING', 'ESCALATED')
                ORDER BY a.created_at DESC LIMIT 5"""
        )
        return [
            {"id": str(r.id), "alert_type": r.alert_type, "camera_name": r.camera_name, "status": r.status,
             "created_at": r.created_at.isoformat()}
            for r in rows
        ]  # fmt: skip

    async def recent_incidents(self, limit: int = 6) -> list[dict[str, Any]]:
        rows = await self._rows(
            """SELECT i.id, i.number, i.title, i.status, i.priority, i.assigned_name, i.updated_at
                 FROM incidents i WHERE i.organization_id = :org AND i.campus_id = :campus
                ORDER BY i.updated_at DESC LIMIT :limit""",
            limit=limit,
        )
        return [
            {"id": str(r.id), "reference": f"INC-{r.number:06d}", "title": r.title, "status": r.status,
             "priority": r.priority, "assigned_name": r.assigned_name, "updated_at": r.updated_at.isoformat()}
            for r in rows
        ]  # fmt: skip

    async def guard_availability(self) -> list[dict[str, Any]]:
        rows = await self._rows(
            """SELECT u.display_id, u.full_name, b.name AS building_name, f.name AS floor_name, a.name AS area_name,
                      g.guard_user_id IS NOT NULL AS stationed
                 FROM users u
                 LEFT JOIN guard_location_assignments g ON g.guard_user_id = u.id
                 LEFT JOIN areas a ON a.id = g.area_id
                 LEFT JOIN floors f ON f.id = COALESCE(g.floor_id, a.floor_id)
                 LEFT JOIN buildings b ON b.id = COALESCE(g.building_id, f.building_id, a.building_id)
                WHERE u.organization_id = :org AND u.campus_id = :campus AND u.role = 'GUARD' AND u.status = 'ACTIVE'
                ORDER BY stationed DESC, u.full_name LIMIT 50"""
        )
        return [
            {"guard_id": r.display_id, "full_name": r.full_name, "stationed": bool(r.stationed),
             "location": " › ".join(p for p in (r.building_name, r.floor_name, r.area_name) if p) or None}
            for r in rows
        ]  # fmt: skip

    async def campus_name(self) -> str:
        [row] = await self._rows("SELECT name FROM campuses WHERE organization_id = :org AND id = :campus")
        return str(row.name)
