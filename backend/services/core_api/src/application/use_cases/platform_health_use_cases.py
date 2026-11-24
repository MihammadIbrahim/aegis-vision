"""Platform system health for the Super Admin (decision D-019).

Only what this installation can really measure is reported as measured: the Core API itself, the
PostgreSQL database (latency, version, extensions, connection pool) and the campus media storage
(reachable, writable, capacity). The decoupled services of the planned architecture (video ingestion,
CV workers, RabbitMQ, object storage) are reported as ``NOT_DEPLOYED``: they expose no health endpoint
yet, and the API never claims they are running. No history is kept, so there are no uptime percentages.
"""

import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OPERATIONAL, DEGRADED, DOWN, NOT_DEPLOYED = "OPERATIONAL", "DEGRADED", "DOWN", "NOT_DEPLOYED"


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    key: str
    name: str
    status: str
    latency_ms: float | None = None
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageHealth:
    path: str
    accessible: bool
    writable: bool
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    used_percent: float | None
    level: str  # OK | WARNING | CRITICAL | UNAVAILABLE
    warning_percent: int
    critical_percent: int


@dataclass(frozen=True, slots=True)
class HealthReport:
    checked_at: datetime
    overall_status: str
    summary: str
    services: list[ServiceHealth]
    storage: StorageHealth
    problems: list[str]


def _storage(root: Path, warning: int, critical: int) -> StorageHealth:
    accessible = root.is_dir()
    writable = False
    if accessible:
        probe = root / f".health-probe-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
            writable = probe.read_bytes() == b"ok"
        except OSError:
            writable = False
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    total = used = free = None
    percent = None
    if accessible:
        usage = shutil.disk_usage(root)
        total, used, free = usage.total, usage.used, usage.free
        percent = round(used * 100 / total, 1) if total else None
    if not accessible or percent is None:
        level = "UNAVAILABLE"
    elif percent >= critical:
        level = "CRITICAL"
    elif percent >= warning:
        level = "WARNING"
    else:
        level = "OK"
    return StorageHealth(str(root), accessible, writable and os.access(root, os.W_OK), total, used, free, percent,
                         level, warning, critical)  # fmt: skip


class CheckPlatformHealth:
    def __init__(
        self,
        *,
        ping_database: Callable[[], Awaitable[None]],
        database_facts: Callable[[], Awaitable[dict[str, Any]]],
        pool_status: Callable[[], dict[str, Any]],
        storage_root: Path,
        storage_warning_percent: int,
        storage_critical_percent: int,
        started_at: datetime,
        detection_intake_enabled: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ping = ping_database
        self._facts = database_facts
        self._pool = pool_status
        self._root = storage_root
        self._warning = storage_warning_percent
        self._critical = storage_critical_percent
        self._started = started_at
        self._intake = detection_intake_enabled
        self._clock = clock

    async def execute(self) -> HealthReport:
        begun = time.perf_counter()
        services: list[ServiceHealth] = []
        problems: list[str] = []
        now = self._clock()

        # Database: a round trip, then facts.
        try:
            t = time.perf_counter()
            await self._ping()
            latency = round((time.perf_counter() - t) * 1000, 2)
            facts = await self._facts()
            extensions = facts.get("extensions", [])
            db = ServiceHealth(
                "database", "Database (PostgreSQL)", OPERATIONAL, latency,
                f"PostgreSQL {facts.get('version')}",
                {
                    "server_version": facts.get("version"),
                    "database_size_bytes": facts.get("size_bytes"),
                    "extensions": extensions,
                    "pgvector_installed": "vector" in extensions,
                    "connection_pool": self._pool(),
                },
            )  # fmt: skip
        except Exception as error:  # noqa: BLE001 - any failure means the database is unreachable
            db = ServiceHealth("database", "Database (PostgreSQL)", DOWN, None, type(error).__name__)
            problems.append("The database did not answer.")
        services.insert(0, db)

        storage = _storage(self._root, self._warning, self._critical)
        if not storage.accessible:
            problems.append("The media storage folder is not reachable.")
        elif not storage.writable:
            problems.append("The media storage folder is not writable.")
        if storage.level == "CRITICAL":
            problems.append(f"Storage is {storage.used_percent}% full (critical at {self._critical}%).")
        elif storage.level == "WARNING":
            problems.append(f"Storage is {storage.used_percent}% full (warning at {self._warning}%).")
        storage_status = (
            DOWN if not storage.accessible else DEGRADED if not storage.writable or storage.level == "CRITICAL"
            else OPERATIONAL
        )  # fmt: skip
        services.append(
            ServiceHealth(
                "media_storage",
                "Media storage (local disk)",
                storage_status,
                None,
                "Campus media folder (CCTV_STORAGE_ROOT)",
                {"used_percent": storage.used_percent, "writable": storage.writable},
            )  # fmt: skip
        )

        not_deployed = "Not deployed in this installation; it exposes no health endpoint yet."
        services += [
            ServiceHealth("video_ingestion", "Video Ingestion Service", NOT_DEPLOYED, detail=not_deployed),
            ServiceHealth(
                "cv_workers",
                "Computer Vision Workers",
                NOT_DEPLOYED,
                detail=not_deployed
                + (
                    " The detection intake is enabled (DETECTION_INGEST_TOKEN set)."
                    if self._intake
                    else " The detection intake is switched off."
                ),
                metrics={"detection_intake_enabled": self._intake},
            ),  # fmt: skip
            ServiceHealth("message_broker", "Message Broker (RabbitMQ)", NOT_DEPLOYED, detail=not_deployed),
            ServiceHealth("object_storage", "Object / Video Storage (cloud)", NOT_DEPLOYED, detail=not_deployed),
        ]

        uptime = max(0.0, (now - self._started).total_seconds())
        services.insert(
            0,
            ServiceHealth(
                "core_api",
                "Core API (FastAPI)",
                OPERATIONAL,
                round((time.perf_counter() - begun) * 1000, 2),
                "Answering requests",
                {"uptime_seconds": round(uptime), "started_at": self._started.isoformat()},
            ),  # fmt: skip
        )

        deployed = [s for s in services if s.status != NOT_DEPLOYED]
        if any(s.status == DOWN and s.key == "database" for s in deployed):
            overall = DOWN
        elif any(s.status in (DOWN, DEGRADED) for s in deployed) or storage.level == "WARNING":
            overall = DEGRADED
        else:
            overall = OPERATIONAL
        affected = sum(1 for s in deployed if s.status != OPERATIONAL)
        missing = len(services) - len(deployed)
        summary = (
            "All deployed services operational" if overall == OPERATIONAL
            else f"{affected} service{'s' if affected != 1 else ''} degraded or down" if affected
            else "Storage warning"
        ) + f" ({missing} planned services not deployed)"  # fmt: skip
        return HealthReport(now, overall, summary, services, storage, problems)
