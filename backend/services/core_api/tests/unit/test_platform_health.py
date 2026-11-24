"""Platform health and CSV export helpers (decision D-019), without a database."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src.application.use_cases import platform_health_use_cases as health
from src.application.use_cases.platform_health_use_cases import CheckPlatformHealth
from src.application.use_cases.platform_management_use_cases import to_csv

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("no route")


async def _facts() -> dict[str, Any]:
    return {"version": "16.4", "size_bytes": 1024, "extensions": ["plpgsql", "vector"]}


def _check(tmp_path: Path, ping=_ok, warning: int = 80, critical: int = 90) -> CheckPlatformHealth:  # type: ignore[no-untyped-def]
    return CheckPlatformHealth(
        ping_database=ping,
        database_facts=_facts,
        pool_status=lambda: {"size": 5},
        storage_root=tmp_path,
        storage_warning_percent=warning,
        storage_critical_percent=critical,
        started_at=NOW - timedelta(hours=2),
        detection_intake_enabled=False,
        clock=lambda: NOW,
    )


async def test_all_measured_services_operational(tmp_path: Path) -> None:
    report = await _check(tmp_path, warning=99, critical=100).execute()
    services = {s.key: s for s in report.services}
    assert report.overall_status == "OPERATIONAL" and report.problems == []
    assert services["core_api"].metrics["uptime_seconds"] == 7200
    assert services["database"].metrics["pgvector_installed"] is True
    assert services["cv_workers"].metrics == {"detection_intake_enabled": False}
    assert report.summary == "All deployed services operational (4 planned services not deployed)"


async def test_database_down_means_platform_down(tmp_path: Path) -> None:
    report = await _check(tmp_path, ping=_down).execute()
    assert report.overall_status == "DOWN"
    assert {s.key: s.status for s in report.services}["database"] == "DOWN"
    assert "The database did not answer." in report.problems


async def test_storage_thresholds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Usage:
        total, used, free = 100, 95, 5

    monkeypatch.setattr(health.shutil, "disk_usage", lambda _: Usage())
    report = await _check(tmp_path).execute()
    assert report.storage.level == "CRITICAL" and report.overall_status == "DEGRADED"
    Usage.used = 85
    report = await _check(tmp_path).execute()
    assert report.storage.level == "WARNING" and report.overall_status == "DEGRADED"
    assert any("85.0% full" in p for p in report.problems)


def test_csv_cells_cannot_become_formulas() -> None:
    text = to_csv(["a", "b"], [["=1+1", "+cmd"], ["-2", "@x"], ["plain", None]])
    assert text.splitlines() == ["a,b", "'=1+1,'+cmd", "'-2,'@x", "plain,"]
