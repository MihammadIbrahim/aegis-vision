"""Reports & Analytics and the Dashboard (decision D-024): figures computed from the campus's own records,
periods and location filters, response times, Guard activity, camera uptime from status changes, CSV and
PDF exports (audited), the dashboard feed, campus isolation and refusals."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient

from tests.conftest import platform_auth_header
from tests.integration.test_alert_operations import ops_client  # noqa: F401  (a fixture)
from tests.integration.test_alerts import new_alert, ok, report_body, site  # noqa: F401  (site is a fixture)
from tests.integration.test_ownership_and_deletion import count, headers, ucp  # noqa: F401

API = "/api/v1"


async def activity(client: AsyncClient, site: dict[str, Any], owner_execute) -> dict[str, str]:
    weapon = await new_alert(client, site["camera_a"])
    fight = await new_alert(client, site["camera_a"], "FIGHT_VIOLENCE")
    await ok(client, site["ga"], "POST", f"/alerts/{weapon}/acknowledge")
    await ok(client, site["ga"], "POST", f"/alerts/{weapon}/responding")
    await ok(client, site["ga"], "POST", f"/alerts/{weapon}/assessment", {"assessment": "UNDER_CONTROL"})
    await ok(client, site["sa"], "POST", f"/alerts/{weapon}/resolve", {"note": "Handled"})
    report = await client.post(f"{API}/guard/manual-reports", json=report_body(site), headers=headers(site["ga"]))
    assert report.status_code == 201
    case = await ok(client, site["a"], "POST", "/incidents", {"title": "Weapon in lobby", "alert_ids": [weapon]})
    await ok(client, site["a"], "POST", f"/incidents/{case['id']}/status", {"status": "RESOLVED", "note": "Done"})
    now = datetime.now(UTC)
    owner_execute(
        "INSERT INTO camera_status (camera_id, organization_id, campus_id, state, source, state_changed_at, "
        "reported_at) VALUES (%(c)s, %(o)s, %(k)s, 'ONLINE', 'INGESTION', %(t)s, %(t)s)",
        {"c": site["camera_a"], "o": site["org"], "k": site["campus_a"], "t": now - timedelta(hours=2)},
    )
    owner_execute(
        "UPDATE camera_status SET state = 'OFFLINE', state_changed_at = %(t)s WHERE camera_id = %(c)s",
        {"c": site["camera_a"], "t": now - timedelta(hours=1)},
    )
    return {"weapon": weapon, "fight": fight, "case": case["id"]}


async def test_report_figures_filters_and_uptime(ops_client: AsyncClient, site: dict[str, Any], owner_execute) -> None:
    client, sa = ops_client, site["sa"]
    await activity(client, site, owner_execute)
    report = await ok(client, sa, "GET", "/analytics?period=LAST_7_DAYS")
    o = report["overview"]
    assert (o["alerts_total"], o["alerts_high"], o["alerts_pending"]) == (2, 2, 1)
    assert (o["incidents_opened"], o["incidents_resolved"], o["manual_reports"]) == (1, 1, 1)
    assert o["cameras_offline"] == 1 and o["guards_active"] >= 1
    assert {(x["alert_type"], x["count"]) for x in report["alerts_by_type"]} == {("WEAPON", 1), ("FIGHT_VIOLENCE", 1)}
    assert len(report["daily"]) == 7 and sum(d["high"] for d in report["daily"]) == 2
    times = report["response_times"]
    assert times["acknowledged"] == 1 and times["acknowledge_avg_seconds"] is not None
    assert times["resolved"] == 1 and times["incidents_resolved"] == 1
    [ga] = [g for g in report["guard_activity"] if g["guard_id"] == site["ga"]["display_id"]]
    assert (ga["acknowledged"], ga["responding"], ga["under_control"], ga["manual_reports"]) == (1, 1, 1, 1)
    assert report["manual_reports"]["total"] == 1
    places = {(x["building"], x["alerts"]) for x in report["locations"]}
    assert ("Admin Block", 2) in places

    since = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    until = datetime.now(UTC).isoformat()
    custom = await client.get(f"{API}/analytics", params={"period": "CUSTOM", "since": since, "until": until},
                              headers=headers(sa))  # fmt: skip
    uptime = custom.json()["camera_uptime"]
    [camera] = [c for c in uptime["cameras"] if c["camera_id"] == str(site["camera_a"])]
    assert 49 <= camera["uptime_percent"] <= 51 and camera["failures"] == 1 and camera["state"] == "OFFLINE"

    fence = await ok(client, sa, "GET", f"/analytics?building_id={site['fence']}")
    assert fence["overview"]["alerts_total"] == 0 and fence["overview"]["cameras_total"] == 0
    block = await ok(client, sa, "GET", f"/analytics?building_id={site['block']}")
    assert block["overview"]["alerts_total"] == 2
    missing = await client.get(f"{API}/analytics", params={"period": "CUSTOM"}, headers=headers(sa))
    assert missing.status_code == 422
    too_long = await client.get(f"{API}/analytics", headers=headers(sa),
                                params={"period": "CUSTOM", "since": "2025-01-01T00:00:00Z",
                                        "until": "2026-06-01T00:00:00Z"})  # fmt: skip
    assert too_long.status_code == 422 and too_long.json()["code"] == "PERIOD_TOO_LONG"


async def test_exports_and_dashboard(ops_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    created = await activity(client, site, owner_execute)
    pdf = await client.get(f"{API}/analytics/export", params={"format": "pdf"}, headers=headers(a))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]
    exported = await client.get(f"{API}/analytics/export", params={"format": "csv"}, headers=headers(sa))
    text = exported.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Reports & Analytics"] and ["Overview"] in rows and ["Camera uptime"] in rows
    assert count(owner_query, "SELECT 1 FROM organization_audit_logs WHERE action = 'REPORT_EXPORTED'") == 2

    board = await ok(client, sa, "GET", "/analytics/dashboard")
    assert board["counters"]["alerts_total"] == 2 and board["campus"]
    assert [x["id"] for x in board["urgent_alerts"]] == [created["fight"]]  # the resolved one is not urgent
    assert board["recent_incidents"][0]["reference"] == "INC-000001"
    assert any(g["guard_id"] == site["ga"]["display_id"] and g["stationed"] for g in board["guards"])
    assert board["activity"] and all(x["category"] != "AUTHENTICATION" for x in board["activity"])


async def test_isolation_and_refusals(
    ops_client: AsyncClient, site: dict[str, Any], owner_execute, super_admin
) -> None:
    await activity(ops_client, site, owner_execute)
    other = await ok(ops_client, site["b"], "GET", "/analytics")
    assert other["overview"]["alerts_total"] == 0 and other["overview"]["incidents_opened"] == 0
    for path in ("/analytics", "/analytics/dashboard", "/analytics/export"):
        assert (await ops_client.get(f"{API}{path}", headers=headers(site["ga"]))).status_code == 403, path
        platform = await ops_client.get(f"{API}{path}", headers=platform_auth_header(super_admin))
        assert platform.status_code == 403, path
