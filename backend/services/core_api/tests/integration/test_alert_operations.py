"""Alert operations of decision D-024 (migration 0026): automatic escalation of unacknowledged alerts,
assistance broadcast on "Additional Guards Required", the responsible Admin or Supervisor, the escalation
history, search / date filters and recipient counts, alert previews; Organization Settings (page 25)."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import DETECTION_TOKEN, DatabaseUrls, _client_for, platform_auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_alerts import new_alert, ok, site  # noqa: F401  (site is a fixture)
from tests.integration.test_ownership_and_deletion import OTHER_PASSWORD, count, headers, one, ucp  # noqa: F401

API = "/api/v1"
SERVICE_TOKEN = "test-only-service-token"
SERVICE = {"X-Service-Token": SERVICE_TOKEN}


@pytest.fixture
async def ops_client(
    site: dict[str, Any], outbox: FakeEmailSender, migrated_database: DatabaseUrls, storage_root: Path
) -> AsyncIterator[AsyncClient]:
    async with _client_for(
        migrated_database,
        email_sender=outbox,
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
        internal_service_token=SERVICE_TOKEN,
    ) as http:
        yield http


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{API}{path}", json=body, headers=headers(user))


def age(owner_execute, alert: str, seconds: int) -> None:
    owner_execute(
        "UPDATE alerts SET created_at = now() - make_interval(secs => %(s)s) WHERE id = %(a)s",
        {"a": alert, "s": seconds},
    )


def recipients(owner_query, alert: str) -> dict[str, str]:
    rows = owner_query(
        "SELECT u.display_id, r.reason FROM alert_recipients r JOIN users u ON u.id = r.guard_user_id "
        "WHERE r.alert_id = %(a)s",
        {"a": alert},
    )
    return {r[0]: r[1] for r in rows}


async def escalate(client: AsyncClient) -> int:
    response = await client.post(f"{API}/internal/alerts/escalate-overdue", headers=SERVICE)
    assert response.status_code == 200, response.text
    escalated: int = response.json()["escalated"]
    return escalated


async def test_unacknowledged_alerts_reach_every_guard_of_the_campus_once(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    client = ops_client
    alert = await new_alert(client, site["camera_a"])  # HIGH: 120 s by default
    before = recipients(owner_query, alert)
    assert await escalate(client) == 0  # not overdue yet
    age(owner_execute, alert, 121)
    assert await escalate(client) == 1
    after = recipients(owner_query, alert)
    added = {k for k, v in after.items() if v == "AUTOMATIC"}
    assert added and not added & set(before)
    assert site["g_none"]["display_id"] in added and site["gb"]["display_id"] not in after  # own campus only
    assert one(owner_query, "SELECT status FROM alerts WHERE id = %(a)s", a=alert) == "ESCALATED"
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'ALERT_AUTO_ESCALATED' "
                 "AND user_id = %(u)s", u=site["sa"]["id"]) == 1  # fmt: skip
    audit = one(owner_query, "SELECT actor_role FROM organization_audit_logs WHERE action = 'ALERT_AUTO_ESCALATED'")
    assert audit == "SYSTEM"
    assert await escalate(client) == 0  # only once

    escalations = await ok(client, site["sa"], "GET", f"/alerts/{alert}/escalations")
    [automatic] = escalations
    assert automatic["scope"] == "AUTOMATIC" and automatic["escalated_by_name"] is None
    assert automatic["added_count"] == len(added) and len(automatic["guards"]) == len(added)
    assert (await call(client, site["ga"], "GET", f"/alerts/{alert}/escalations")).status_code == 403


async def test_acknowledged_alerts_and_switched_off_organizations_are_not_escalated(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    client = ops_client
    acknowledged = await new_alert(client, site["camera_a"])
    await ok(client, site["ga"], "POST", f"/alerts/{acknowledged}/acknowledge")
    age(owner_execute, acknowledged, 3600)
    assert await escalate(client) == 0
    owner_execute(
        "INSERT INTO organization_settings (organization_id, auto_escalation_enabled) VALUES (%(o)s, false)",
        {"o": site["org"]},
    )
    other = await new_alert(client, site["camera_a"], "FIGHT_VIOLENCE")
    age(owner_execute, other, 3600)
    assert await escalate(client) == 0


async def test_additional_guards_required_reaches_standby_and_same_building_guards(
    ops_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client = ops_client
    alert = await new_alert(client, site["camera_a"])  # in the Lobby of Admin Block
    before = recipients(owner_query, alert)
    await ok(client, site["ga"], "POST", f"/alerts/{alert}/acknowledge")
    await ok(client, site["ga"], "POST", f"/alerts/{alert}/assessment", {"assessment": "ADDITIONAL_GUARDS_REQUIRED"})
    after = recipients(owner_query, alert)
    added = {k for k, v in after.items() if v == "ASSISTANCE"}
    assert site["g_none"]["display_id"] in added  # Standby
    assert site["g_roof"]["display_id"] in added or site["g_roof"]["display_id"] in before  # same building
    assert site["g_gate"]["display_id"] not in after  # another building
    assert not added & set(before)
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'ALERT_ASSISTANCE_BROADCAST' "
                 "AND user_id = %(u)s", u=site["g_none"]["id"]) == 1  # fmt: skip
    scopes = [e["scope"] for e in await ok(client, site["a"], "GET", f"/alerts/{alert}/escalations")]
    assert scopes == ["ASSISTANCE"]
    # The new recipients now see the alert.
    assert (await call(client, site["g_none"], "GET", f"/alerts/{alert}")).status_code == 200


async def test_handler_counts_search_and_dates(ops_client: AsyncClient, site: dict[str, Any], owner_query) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    alert = await new_alert(client, site["camera_a"])
    await ok(client, site["ga"], "POST", f"/alerts/{alert}/responding")
    handled = await ok(client, a, "PUT", f"/alerts/{alert}/handler", {"user_id": str(sa["id"])})
    assert handled["handled_by"] == str(sa["id"]) and handled["handled_by_name"] == sa["name"]
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'ALERT_HANDLER_ASSIGNED' "
                 "AND user_id = %(u)s", u=sa["id"]) == 1  # fmt: skip
    guard = await call(client, a, "PUT", f"/alerts/{alert}/handler", {"user_id": str(site["ga"]["id"])})
    assert guard.status_code == 422 and guard.json()["code"] == "INVALID_HANDLER"
    other = await call(client, a, "PUT", f"/alerts/{alert}/handler", {"user_id": str(site["b"]["id"])})
    assert other.status_code == 422
    assert (await call(client, site["ga"], "PUT", f"/alerts/{alert}/handler", {"user_id": None})).status_code == 403

    [listed] = await ok(client, a, "GET", "/alerts")
    assert listed["recipient_count"] >= 1 and listed["acknowledged_count"] == 1 and listed["responding_count"] == 1
    assert listed["has_snapshot"] is False
    for query, hits in (("weapon", 1), ("Gate", 1), ("lobby", 1), (alert[:8], 1), ("fight", 0), ("%", 0)):
        found = await ok(client, a, "GET", f"/alerts?q={query.replace('%', '%25')}")
        assert len(found) == hits, query
    assert len(await ok(client, a, "GET", f"/alerts?handled_by={sa['id']}")) == 1
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    later = await client.get(f"{API}/alerts", params={"since": future}, headers=headers(a))
    assert later.status_code == 200 and later.json() == []
    history = [e["event_type"] for e in await ok(client, a, "GET", f"/alerts/{alert}/history")]
    assert "HANDLER_ASSIGNED" in history


async def test_alert_previews_state_missing_footage(ops_client: AsyncClient, site: dict[str, Any]) -> None:
    alert = await new_alert(ops_client, site["camera_a"])
    for kind in ("snapshot", "clip"):
        response = await call(ops_client, site["a"], "GET", f"/alerts/{alert}/{kind}")
        assert response.status_code == 422 and response.json()["code"] == "NO_RECORDING", kind
        assert (await call(ops_client, site["ga"], "GET", f"/alerts/{alert}/{kind}")).status_code == 403


# --- Organization Settings (page 25) ----------------------------------------------------------------------


def settings_body(**changes: Any) -> dict[str, Any]:
    body = {
        "official_phone": "+92 42 111 222 333",
        "address_line": "1 Canal Road",
        "city": "Lahore",
        "country": "Pakistan",
        "time_zone": "Asia/Karachi",
        "date_format": "YYYY-MM-DD",
        "time_format": "12H",
        "auto_escalation_enabled": True,
        "escalation_high_seconds": 60,
        "escalation_standard_seconds": 600,
        "assistance_broadcast_enabled": False,
        "current_password": "Lakeside-pass-2026",
    }
    body.update(changes)
    return body


async def test_only_the_main_admin_changes_organization_settings(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, super_admin
) -> None:
    client, a, b = ops_client, site["a"], site["b"]
    view = await ok(client, a, "GET", "/organization/settings")
    assert view["is_main_admin"] is True and view["can_edit"] is True
    assert view["profile"]["name"] == "UCP" and view["preferences"]["time_zone"] == "Asia/Karachi"
    assert view["alert_defaults"] == {
        "auto_escalation_enabled": True,
        "escalation_high_seconds": 120,
        "escalation_standard_seconds": 300,
        "assistance_broadcast_enabled": True,
    }
    assert view["security"]["sign_in_code_required"] is True and view["campus_requests"]["decision_window_hours"] == 72
    assert view["campus"]["retention_is_default"] is True and "human_detection" in view["campus"]["ai_modules"]

    body = settings_body(current_password=a["password"])
    wrong = await call(client, a, "PUT", "/organization/settings", {**body, "current_password": "Wrong-pass-2026"})
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    bad_zone = await call(client, a, "PUT", "/organization/settings", {**body, "time_zone": "Mars/Base"})
    assert bad_zone.status_code == 422 and bad_zone.json()["code"] == "INVALID_TIME_ZONE"
    saved = await ok(client, a, "PUT", "/organization/settings", body)
    assert saved["profile"]["city"] == "Lahore" and saved["profile"]["official_phone"] == "+9242111222333"
    assert saved["alert_defaults"]["escalation_high_seconds"] == 60 and saved["updated_by_name"] == a["name"]
    changed = one(owner_query, "SELECT metadata FROM organization_audit_logs WHERE action = "
                  "'ORGANIZATION_SETTINGS_UPDATED'")  # fmt: skip
    assert "time_zone" not in changed["changed"] and "date_format" in changed["changed"]

    branch = await ok(client, b, "GET", "/organization/settings")
    assert branch["is_main_admin"] is False and branch["can_edit"] is False and branch["profile"]["city"] == "Lahore"
    refused = await call(client, b, "PUT", "/organization/settings", settings_body(current_password=OTHER_PASSWORD))
    assert refused.status_code == 403 and refused.json()["code"] == "MAIN_ADMIN_ONLY"
    assert (await call(client, site["sa"], "GET", "/organization/settings")).status_code == 403
    prefs = await ok(client, site["ga"], "GET", "/organization/preferences")
    assert prefs == {"time_zone": "Asia/Karachi", "date_format": "YYYY-MM-DD", "time_format": "12H"}
    platform = await client.get(f"{API}/organization/settings", headers=platform_auth_header(super_admin))
    assert platform.status_code == 403


# --- AI Configuration and live overlays (page 17 / Supervisor 14) --------------------------------------------


def modules(**changes: bool) -> dict[str, bool]:
    base = {
        "human_detection": True,
        "person_reid": True,
        "weapon_detection": True,
        "fight_detection": True,
        "face_recognition": False,
    }
    base.update(changes)
    return base


async def test_ai_configuration_levels_passwords_and_worker_view(
    ops_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client, a, sa, camera = ops_client, site["a"], site["sa"], site["camera_a"]
    view = await ok(client, sa, "GET", "/ai-configuration")
    assert view["platform"]["face_recognition"] is False and view["campus"]["weapon_detection"] is True
    assert {r["detection"]: r["priority"] for r in view["alert_rules"]}["WEAPON"] == "HIGH"
    [cam] = [c for c in view["cameras"] if c["camera_id"] == str(camera)]
    # D-028: a camera runs no module until one is selected for it.
    assert not any(cam["configured"].values()) and not any(cam["effective"].values())
    assert cam["customized"] is False and cam["running_now"] is False
    opted_in = await ok(client, a, "PUT", f"/ai-configuration/cameras/{camera}",
                        {**modules(), "current_password": a["password"]})  # fmt: skip
    [cam] = [c for c in opted_in["cameras"] if c["camera_id"] == str(camera)]
    assert cam["effective"]["weapon_detection"] is True and cam["running_now"] is False

    # Campus: Admins only, never beyond the platform, with the password.
    supervisor = await call(client, sa, "PUT", "/ai-configuration/campus",
                            {**modules(weapon_detection=False), "current_password": OTHER_PASSWORD})  # fmt: skip
    assert supervisor.status_code == 403
    beyond = await call(client, a, "PUT", "/ai-configuration/campus",
                        {**modules(face_recognition=True), "current_password": a["password"]})  # fmt: skip
    assert beyond.status_code == 409 and beyond.json()["code"] == "MODULE_NOT_PERMITTED"
    wrong = await call(client, a, "PUT", "/ai-configuration/campus",
                       {**modules(weapon_detection=False), "current_password": "Wrong-pass-2026"})  # fmt: skip
    assert wrong.status_code == 403
    saved = await ok(client, a, "PUT", "/ai-configuration/campus",
                     {**modules(weapon_detection=False), "current_password": a["password"]})  # fmt: skip
    assert saved["campus_permitted"]["weapon_detection"] is False

    # Camera: a Supervisor cannot switch on what the campus does not permit.
    refused = await call(client, sa, "PUT", f"/ai-configuration/cameras/{camera}",
                         {**modules(), "current_password": OTHER_PASSWORD})  # fmt: skip
    assert refused.status_code == 409
    camera_saved = await ok(client, sa, "PUT", f"/ai-configuration/cameras/{camera}",
                            {**modules(weapon_detection=False, person_reid=False),
                             "current_password": OTHER_PASSWORD})  # fmt: skip
    [cam] = [c for c in camera_saved["cameras"] if c["camera_id"] == str(camera)]
    assert cam["customized"] is True and cam["effective"]["person_reid"] is False
    other = await call(client, site["b"], "PUT", f"/ai-configuration/cameras/{camera}",
                       {**modules(weapon_detection=False), "current_password": OTHER_PASSWORD})  # fmt: skip
    assert other.status_code == 404
    actions = {r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE category = 'AI_SETTINGS'")}
    assert actions == {"AI_SETTINGS_UPDATED", "CAMERA_AI_SETTINGS_UPDATED"}

    # Vision workers: the effective modules, then a status report.
    config = await client.get(f"{API}/internal/cameras/{camera}/ai-config", headers=SERVICE)
    assert config.status_code == 200 and config.json()["modules"] == ["human_detection", "fight_detection"]
    assert (await client.get(f"{API}/internal/cameras/{camera}/ai-config")).status_code == 401
    report = await client.post(
        f"{API}/internal/cameras/{camera}/ai-status",
        json={"modules": ["human_detection"], "frames_per_second": 4.5, "worker": "cv-1"},
        headers=SERVICE,
    )
    assert report.status_code == 204
    [cam] = [c for c in (await ok(client, sa, "GET", "/ai-configuration"))["cameras"] if c["camera_id"] == str(camera)]
    assert cam["running_now"] is True and cam["running"] == ["human_detection"] and cam["frames_per_second"] == 4.5
    assert (await call(client, site["ga"], "GET", "/ai-configuration")).status_code == 403


async def test_live_overlay_reads_only_well_formed_recent_detections(
    ops_client: AsyncClient, site: dict[str, Any], storage_root: Path
) -> None:
    camera = site["camera_a"]
    path = storage_root / "live" / str(site["org"]) / str(site["campus_a"]) / str(camera) / "overlay.json"
    empty = await ok(ops_client, site["sa"], "GET", f"/live/cameras/{camera}/overlay")
    assert empty["stale"] is True and empty["detections"] == []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "frame_width": 1280,
                "frame_height": 720,
                "modules": ["human_detection"],
                "detections": [
                    {"label": "person", "confidence": 0.91, "box": [0.1, 0.2, 0.3, 0.6], "track_id": 7},
                    {"label": "bad", "box": [0.5, 0.5, 0.2, 0.2]},
                    {"label": "nan", "box": ["x", 0, 1, 1]},
                    "garbage",
                ],
            }
        )
    )
    fresh = await ok(ops_client, site["sa"], "GET", f"/live/cameras/{camera}/overlay")
    assert fresh["stale"] is False and fresh["frame_width"] == 1280
    assert fresh["detections"] == [
        {"label": "person", "confidence": 0.91, "box": [0.1, 0.2, 0.3, 0.6], "track_id": 7, "module": None}
    ]
    assert (await call(ops_client, site["b"], "GET", f"/live/cameras/{camera}/overlay")).status_code == 404
    assert (await call(ops_client, site["ga"], "GET", f"/live/cameras/{camera}/overlay")).status_code == 403
