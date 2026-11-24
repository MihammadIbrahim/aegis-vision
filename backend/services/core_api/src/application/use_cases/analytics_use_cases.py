"""Reports & Analytics and the Dashboard (Org Admin pages 1 and 22, Supervisor pages 1 and 18; decision D-024).

Admins and Supervisors of a campus; Guards and the Super Admin have no access. Periods: today, the last 7
or 30 days, or a custom range of at most 366 days; filters: building, floor, area. Exports (CSV, PDF) are
audited. Camera uptime is measured from the recorded state changes: time before a camera's first known
state is "unknown", never counted as up or down.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import PermissionDenied, ValidationFailed
from src.domain.repositories.i_audit_log_repo import AuditFilter, IAuditLogReader, IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

MAX_PERIOD = timedelta(days=366)
PRESETS = ("TODAY", "LAST_7_DAYS", "LAST_30_DAYS", "CUSTOM")


@dataclass(frozen=True, slots=True)
class Period:
    preset: str
    since: datetime
    until: datetime
    time_zone: str


def resolve_period(
    preset: str, since: datetime | None, until: datetime | None, time_zone: str, now: datetime
) -> Period:
    zone = ZoneInfo(time_zone)
    if preset == "CUSTOM":
        if since is None or until is None:
            raise ValidationFailed("A custom period needs a start and an end.", code="INVALID_PERIOD")
        if since > until:
            raise ValidationFailed("The start of the period must be before its end.", code="INVALID_PERIOD")
        if until - since > MAX_PERIOD:
            raise ValidationFailed("Choose at most 366 days.", code="PERIOD_TOO_LONG")
        return Period(preset, since, until, time_zone)
    local_now = now.astimezone(zone)
    start_of_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = {"TODAY": 0, "LAST_7_DAYS": 6, "LAST_30_DAYS": 29}.get(preset)
    if days is None:
        raise ValidationFailed("Unknown period.", code="INVALID_PERIOD")
    return Period(preset, (start_of_today - timedelta(days=days)).astimezone(UTC), now, time_zone)


def uptime(cameras: list[Any], events: list[Any], since: datetime, until: datetime) -> dict[str, Any]:
    """Online share of each camera in [since, until] from its state changes (known time only)."""
    by_camera: dict[UUID, list[Any]] = {}
    for event in events:
        by_camera.setdefault(event.camera_id, []).append(event)
    rows = []
    online_total = known_total = 0.0
    failures_by_day: dict[str, int] = {}
    for camera in cameras:
        online = known = 0.0
        failures = 0
        state, cursor = None, since
        for event in by_camera.get(camera.id, []):
            when = max(event.changed_at, since)
            if event.changed_at > since and state is not None:
                span = (when - cursor).total_seconds()
                if state == "ONLINE":
                    online += span
                if state in ("ONLINE", "OFFLINE"):
                    known += span
            if event.changed_at >= since and event.state == "OFFLINE" and state != "OFFLINE":
                failures += 1
                day = event.changed_at.date().isoformat()
                failures_by_day[day] = failures_by_day.get(day, 0) + 1
            state, cursor = event.state, when
        if state is not None and until > cursor:
            span = (until - cursor).total_seconds()
            if state == "ONLINE":
                online += span
            if state in ("ONLINE", "OFFLINE"):
                known += span
        online_total += online
        known_total += known
        rows.append(
            {
                "camera_id": str(camera.id),
                "camera_name": camera.display_name,
                "state": camera.state,
                "uptime_percent": round(100 * online / known, 1) if known > 0 else None,
                "known_hours": round(known / 3600, 2),
                "failures": failures,
            }
        )
    return {
        "cameras": rows,
        "overall_uptime_percent": round(100 * online_total / known_total, 1) if known_total > 0 else None,
        "failures_by_day": [{"day": d, "failures": n} for d, n in sorted(failures_by_day.items())],
    }


class IAnalyticsRepository(Protocol):
    async def counters(self, scope: Any) -> dict[str, Any]: ...


class Analytics:
    def __init__(
        self,
        repository: Any,
        audit: IOrganizationAuditLog,
        audit_reader: IAuditLogReader,
        transaction: ITransaction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._reader = audit_reader
        self._transaction = transaction
        self._clock = clock

    @staticmethod
    def _staff(actor: User) -> None:
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            raise PermissionDenied("Only Admins and Supervisors can open Reports & Analytics.", code="STAFF_ONLY")

    async def report(self, actor: User, period: Period, scope: Any) -> dict[str, Any]:
        self._staff(actor)
        counters = await self._repo.counters(scope)
        response = await self._repo.response_times(scope)
        cameras, events = await self._repo.camera_status_events(scope)
        storage = await self._repo.storage(scope)
        days = max(1.0, (period.until - period.since).total_seconds() / 86400)
        recorded = sum(d["bytes"] for d in storage["daily"])
        per_day = recorded / days if recorded else 0.0
        free = min((n["free_bytes"] for n in storage["nodes"]), default=None)
        storage["average_bytes_per_day"] = round(per_day)
        storage["projected_days_until_full"] = round(free / per_day, 1) if free is not None and per_day > 0 else None
        received = response["alerts"]
        return {
            "period": {
                "preset": period.preset,
                "since": period.since.isoformat(),
                "until": period.until.isoformat(),
                "time_zone": period.time_zone,
            },  # fmt: skip
            "campus": await self._repo.campus_name(),
            "overview": {
                **counters,
                "guard_response_rate_percent": round(100 * response["acknowledged"] / received, 1)
                if received
                else None,
            },
            "alerts_by_type": await self._repo.alerts_by_type(scope),
            "alerts_by_status": await self._repo.alerts_by_status(scope),
            "daily": await self._repo.daily(scope),
            "response_times": response,
            "guard_activity": await self._repo.guard_activity(scope),
            "manual_reports": await self._repo.manual_reports(scope),
            "locations": await self._repo.locations(scope),
            "camera_uptime": uptime(list(cameras), list(events), period.since, period.until),
            "storage": storage,
            "person_observations": await self._repo.person_observations(scope),
        }

    async def record_export(self, actor: User, period: Period, file_format: str) -> None:
        await self._audit.record(
            actor, "REPORT_EXPORTED", "REPORT", None,
            {"format": file_format, "since": period.since.isoformat(), "until": period.until.isoformat()},
        )  # fmt: skip
        await self._transaction.commit()

    async def dashboard(self, actor: User, scope: Any) -> dict[str, Any]:
        self._staff(actor)
        activity = await self._reader.page(AuditFilter(limit=12))
        return {
            "campus": await self._repo.campus_name(),
            "counters": await self._repo.counters(scope),
            "urgent_alerts": await self._repo.urgent_alerts(),
            "recent_alerts": await self._repo.recent_alerts(),
            "recent_incidents": await self._repo.recent_incidents(),
            "guards": await self._repo.guard_availability(),
            "alerts_by_type": await self._repo.alerts_by_type(scope),
            "daily": await self._repo.daily(scope),
            "person_observations": await self._repo.person_observations(scope),
            "activity": [
                {
                    "occurred_at": r.occurred_at.isoformat(),
                    "action": r.action,
                    "category": r.category.value,
                    "actor_name": r.actor_name,
                    "actor_role": r.actor_role,
                    "target_label": r.target_label,
                    "outcome": r.outcome.value,
                }
                for r in activity.items
            ],  # fmt: skip
        }
