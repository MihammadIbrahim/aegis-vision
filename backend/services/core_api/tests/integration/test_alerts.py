"""Alert Management (migration 0015, decision D-013): detection intake, priority, location routing,
the Guard lifecycle, staff resolve/dismiss/escalate, campus isolation (API and database), alert chat,
manual reports, offline sync, in-app notifications, audit records, and campus deletion/export.
Uses the three-campus organization ``ucp`` of the deletion tests."""

# ruff: noqa: F811  (the imported ``ucp`` fixture is used as a test parameter)

import base64
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import DETECTION_TOKEN, DatabaseUrls, _client_for, run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_locations import area, building, floor
from tests.integration.test_locations import ok as loc_ok
from tests.integration.test_ownership_and_deletion import (
    add_user,
    count,
    delete_account,
    headers,
    one,
    start_deletion,
    traces,
    ucp,  # noqa: F401  (a fixture, used by name)
)

API = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{API}{path}", json=body, headers=headers(user))


async def ok(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None) -> Any:
    response = await call(client, user, method, path, body)
    assert response.status_code in (200, 201, 204), (method, path, response.text)
    return response.json() if response.content else None


async def detect(client: AsyncClient, camera: UUID, kind: str = "WEAPON", **extra: Any):  # type: ignore[no-untyped-def]
    body = {"camera_id": str(camera), "detection_type": kind, "detected_at": "2026-10-31T10:00:00Z", **extra}
    return await client.post(f"{API}/internal/detections", json=body, headers={"X-Detection-Token": DETECTION_TOKEN})


async def new_alert(client: AsyncClient, camera: UUID, kind: str = "WEAPON", **extra: Any) -> str:
    response = await detect(client, camera, kind, **extra)
    assert response.status_code == 200 and response.json()["outcome"] == "CREATED", response.text
    return response.json()["alert_id"]


def notifications(owner_query, user_id: UUID, kind: str | None = None) -> int:
    sql = "SELECT 1 FROM user_notifications WHERE user_id = %(u)s" + (" AND kind = %(k)s" if kind else "")
    return count(owner_query, sql, u=user_id, k=kind)


def audit_actions(owner_query, target: str) -> list[str]:
    rows = owner_query(
        "SELECT action FROM organization_audit_logs WHERE target_id = %(t)s ORDER BY occurred_at, action", {"t": target}
    )
    return [r[0] for r in rows]


@pytest.fixture
async def site(
    registration_client: AsyncClient, ucp: dict[str, Any], migrated_database: DatabaseUrls, owner_query
) -> dict[str, Any]:
    """Main Campus: Admin Block (Ground floor with Lobby; Roof area without a floor), North Fence
    perimeter with Gate area. Guards: ga (Lobby), g_floor (Ground), g_building (Block), g_roof (Roof),
    g_none (no location). The campus camera is in the Lobby. Campus B has gb assigned in B Block."""
    a, b = ucp["a"], ucp["b"]
    block = await building(registration_client, a, "Admin Block")
    ground = await floor(registration_client, a, block, "Ground", 0)
    lobby = await area(registration_client, a, block, "Lobby", floor_id=ground)
    roof = await area(registration_client, a, block, "Roof")
    fence = await building(registration_client, a, "North Fence", "OUTDOOR_PERIMETER")
    gate = await area(registration_client, a, fence, "Gate", boundary_direction="NORTH")
    with psycopg.connect(migrated_database.owner_test) as conn:
        extra = {
            name: add_user(conn, ucp["org"], ucp["campus_a"], "GUARD", name, f"{name}@ucp.example.com")
            for name in ("g_floor", "g_building", "g_roof", "g_none", "g_gate")
        }
    data: dict[str, Any] = {
        **ucp,
        **extra,
        "block": block,
        "ground": ground,
        "lobby": lobby,
        "roof": roof,
        "fence": fence,
        "gate": gate,
    }
    for guard, selection in [
        ("ga", {"building_id": block, "floor_id": ground, "area_id": lobby}),
        ("g_floor", {"building_id": block, "floor_id": ground}),
        ("g_building", {"building_id": block}),
        ("g_roof", {"building_id": block, "area_id": roof}),
        ("g_gate", {"building_id": fence, "area_id": gate}),
    ]:
        await loc_ok(registration_client, a, "PUT", f"/guard-assignments/{data[guard]['display_id']}", selection)
    data["camera_a"] = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=ucp["campus_a"])
    await loc_ok(
        registration_client,
        a,
        "PUT",
        f"/camera-links/{data['camera_a']}",
        {"building_id": block, "floor_id": ground, "area_id": lobby},
    )
    b_block = await building(registration_client, b, "B Block")
    await loc_ok(
        registration_client, b, "PUT", f"/guard-assignments/{ucp['gb']['display_id']}", {"building_id": b_block}
    )
    data["camera_b"] = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=ucp["campus_b"])
    await loc_ok(registration_client, b, "PUT", f"/camera-links/{data['camera_b']}", {"building_id": b_block})
    return data


def guard_ids(alert: dict[str, Any]) -> set[str]:
    return {r["guard_id"] for r in alert["recipients"]}


# --- Creation, priority, routing ------------------------------------------------------------------------


async def test_a_weapon_detection_becomes_a_high_priority_alert_for_the_guards_of_that_place(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"], confidence="0.91", detection_id="w-1")
    alert = await ok(registration_client, site["sa"], "GET", f"/alerts/{alert_id}")
    assert (alert["alert_type"], alert["priority"], alert["status"]) == ("WEAPON", "HIGH", "NEW")
    assert alert["location"]["building_name"] == "Admin Block" and alert["location"]["area_name"] == "Lobby"
    assert alert["location"]["floor_name"] == "Ground" and alert["confidence"] == "0.9100"
    # Lobby, its floor and its building contain the camera; the Roof, the Gate and the unassigned do not.
    assert guard_ids(alert) == {site[g]["display_id"] for g in ("ga", "g_floor", "g_building")}
    for g in ("ga", "g_floor", "g_building"):
        assert notifications(owner_query, site[g]["id"], "ALERT_NEW") == 1
    for g in ("g_roof", "g_none", "g_gate", "gb"):
        assert notifications(owner_query, site[g]["id"]) == 0
    for staff in ("a", "sa"):
        assert notifications(owner_query, site[staff]["id"], "ALERT_NEW") == 1
    for other in ("b", "sb", "c"):
        assert notifications(owner_query, site[other]["id"]) == 0
    assert audit_actions(owner_query, alert_id) == ["ALERT_CREATED"]
    history = await ok(registration_client, site["a"], "GET", f"/alerts/{alert_id}/history")
    assert [e["event_type"] for e in history] == ["CREATED", "ROUTED"]
    assert history[1]["details"]["guards"] == 3


async def test_priority_follows_the_detection_type(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute
) -> None:
    fight = await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")
    restricted = await new_alert(registration_client, site["camera_a"], "RESTRICTED_AREA")
    assert (await ok(registration_client, site["a"], "GET", f"/alerts/{fight}"))["priority"] == "HIGH"
    assert (await ok(registration_client, site["a"], "GET", f"/alerts/{restricted}"))["priority"] == "STANDARD"
    assert (await detect(registration_client, site["camera_a"], "HUMAN")).status_code == 422
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_alerts_high_priority"):
        owner_execute("UPDATE alerts SET priority = 'STANDARD' WHERE id = %(a)s", {"a": fight})


async def test_repeats_are_merged_and_resends_ignored(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    first = await new_alert(registration_client, site["camera_a"], detection_id="d-1")
    duplicate = (await detect(registration_client, site["camera_a"], detection_id="d-1")).json()
    assert duplicate == {"alert_id": first, "outcome": "DUPLICATE"}
    repeat = (
        await detect(registration_client, site["camera_a"], detection_id="d-2", detected_at="2026-10-31T10:00:30Z")
    ).json()
    assert repeat == {"alert_id": first, "outcome": "REPEATED"}
    alert = await ok(registration_client, site["a"], "GET", f"/alerts/{first}")
    assert alert["detection_count"] == 2 and alert["last_detected_at"].startswith("2026-10-31T10:00:30")
    # A merged detection sent again is also ignored, and does not count twice (migration 0016).
    resent = (
        await detect(registration_client, site["camera_a"], detection_id="d-2", detected_at="2026-10-31T10:00:30Z")
    ).json()
    assert resent == {"alert_id": first, "outcome": "DUPLICATE"}
    assert (await ok(registration_client, site["a"], "GET", f"/alerts/{first}"))["detection_count"] == 2
    assert notifications(owner_query, site["ga"]["id"]) == 1  # no second notification
    # Outside the window, or after the alert is closed, a new alert is raised.
    later = await detect(registration_client, site["camera_a"], detected_at="2026-10-31T10:05:00Z")
    assert later.json()["outcome"] == "CREATED"
    await ok(registration_client, site["sa"], "POST", f"/alerts/{later.json()['alert_id']}/resolve")
    again = await detect(registration_client, site["camera_a"], detected_at="2026-10-31T10:05:10Z")
    assert again.json()["outcome"] == "CREATED"


async def test_the_detection_intake_is_protected(
    registration_client: AsyncClient, site: dict[str, Any], migrated_database: DatabaseUrls, owner_query
) -> None:
    body = {"camera_id": str(site["camera_a"]), "detection_type": "WEAPON", "detected_at": "2026-10-31T10:00:00Z"}
    wrong = await registration_client.post(f"{API}/internal/detections", json=body, headers={"X-Detection-Token": "x"})
    assert wrong.status_code == 401
    assert (await registration_client.post(f"{API}/internal/detections", json=body)).status_code == 401
    # A signed-in user's token is not a detection token.
    signed_in = await registration_client.post(f"{API}/internal/detections", json=body, headers=headers(site["a"]))
    assert signed_in.status_code == 401
    async with _client_for(migrated_database) as no_token:
        off = await no_token.post(f"{API}/internal/detections", json=body, headers={"X-Detection-Token": "x"})
        assert off.status_code == 503 and off.json()["code"] == "DETECTION_INTAKE_DISABLED"
    unknown = await detect(registration_client, uuid4())
    assert unknown.status_code == 404 and unknown.json()["code"] == "CAMERA_NOT_FOUND"
    assert count(owner_query, "SELECT 1 FROM alerts") == 0


async def test_a_camera_without_location_alerts_the_staff_only(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    await loc_ok(registration_client, site["a"], "DELETE", f"/camera-links/{site['camera_a']}")
    alert_id = await new_alert(registration_client, site["camera_a"])
    alert = await ok(registration_client, site["a"], "GET", f"/alerts/{alert_id}")
    assert alert["recipients"] == [] and alert["location"]["building_id"] is None
    assert notifications(owner_query, site["sa"]["id"], "ALERT_NEW") == 1
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'ALERT_NEW'") == 2  # a and sa


async def test_outdoor_perimeter_alerts_reach_the_perimeter_guards(
    registration_client: AsyncClient, site: dict[str, Any], migrated_database: DatabaseUrls
) -> None:
    gate_camera = (
        await ok(
            registration_client,
            site["a"],
            "POST",
            "/cameras",
            {
                "display_name": "Gate Cam",
                "connection_type": "RTSP",
                "stream_url": None,
                "location": {"building_id": site["fence"], "area_id": site["gate"]},
            },
        )
    )["id"]
    alert = await ok(
        registration_client, site["a"], "GET", f"/alerts/{await new_alert(registration_client, gate_camera)}"
    )
    assert guard_ids(alert) == {site["g_gate"]["display_id"]}
    assert alert["location"]["building_kind"] == "OUTDOOR_PERIMETER" and alert["location"]["floor_id"] is None


# --- Guard lifecycle ---------------------------------------------------------------------------------


async def test_the_guard_lifecycle_and_staff_resolution(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    ga, sa = site["ga"], site["sa"]
    alert_id = await new_alert(registration_client, site["camera_a"])
    early = await call(
        registration_client, ga, "POST", f"/alerts/{alert_id}/assessment", {"assessment": "UNDER_CONTROL"}
    )
    assert early.status_code == 409 and early.json()["code"] == "ACKNOWLEDGE_FIRST"
    assert (await ok(registration_client, ga, "POST", f"/alerts/{alert_id}/acknowledge"))["status"] == "ACKNOWLEDGED"
    again = await ok(registration_client, ga, "POST", f"/alerts/{alert_id}/acknowledge")
    assert again == {"alert_id": alert_id, "outcome": "UNCHANGED", "status": "ACKNOWLEDGED"}
    assert (await ok(registration_client, ga, "POST", f"/alerts/{alert_id}/responding"))["status"] == "RESPONDING"
    assessed = await ok(
        registration_client, ga, "POST", f"/alerts/{alert_id}/assessment", {"assessment": "UNDER_CONTROL"}
    )
    assert assessed["status"] == "RESPONDING"  # an assessment never resolves the alert (15.3)
    await ok(
        registration_client, ga, "POST", f"/alerts/{alert_id}/assessment", {"assessment": "ADDITIONAL_GUARDS_REQUIRED"}
    )
    assert notifications(owner_query, sa["id"], "ALERT_ASSISTANCE_REQUESTED") == 1
    assert notifications(owner_query, sa["id"], "ALERT_RESPONDING") == 1
    mine = next(r for r in (await ok(registration_client, ga, "GET", f"/alerts/{alert_id}"))["recipients"])
    assert (mine["state"], mine["assessment"]) == ("RESPONDING", "ADDITIONAL_GUARDS_REQUIRED")

    resolved = await ok(registration_client, sa, "POST", f"/alerts/{alert_id}/resolve", {"note": "Cleared"})
    assert (resolved["status"], resolved["close_note"]) == ("RESOLVED", "Cleared")
    assert notifications(owner_query, ga["id"], "ALERT_RESOLVED") == 1
    for response in (
        await call(registration_client, ga, "POST", f"/alerts/{alert_id}/responding"),
        await call(registration_client, sa, "POST", f"/alerts/{alert_id}/resolve"),
        await call(registration_client, sa, "POST", f"/alerts/{alert_id}/dismiss", {"reason": "x"}),
    ):
        assert response.status_code == 409 and response.json()["code"] == "ALERT_CLOSED"
    history = [e["event_type"] for e in await ok(registration_client, sa, "GET", f"/alerts/{alert_id}/history")]
    # "Additional Guards Required" also reaches Standby and same-building Guards (decision D-024).
    assert history == [
        "CREATED", "ROUTED", "ACKNOWLEDGED", "RESPONDING", "ASSESSMENT", "ASSESSMENT", "ASSISTANCE_BROADCAST",
        "RESOLVED",
    ]  # fmt: skip
    assert set(audit_actions(owner_query, alert_id)) == {
        "ALERT_CREATED",
        "ALERT_ACKNOWLEDGE",
        "ALERT_RESPONDING",
        "ALERT_UNDER_CONTROL",
        "ALERT_ADDITIONAL_GUARDS_REQUIRED",
        "ALERT_ASSISTANCE_BROADCAST",
        "ALERT_RESOLVED",
    }


async def test_responding_directly_also_acknowledges(registration_client: AsyncClient, site: dict[str, Any]) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    assert (await ok(registration_client, site["g_floor"], "POST", f"/alerts/{alert_id}/responding"))[
        "status"
    ] == "RESPONDING"
    entry = next(
        r for r in (await ok(registration_client, site["g_floor"], "GET", f"/alerts/{alert_id}"))["recipients"]
    )
    assert entry["acknowledged_at"] is not None and entry["state"] == "RESPONDING"


# --- Permissions and isolation ----------------------------------------------------------------------------


async def test_role_rules(registration_client: AsyncClient, site: dict[str, Any], owner_query) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    for guard in ("g_roof", "g_none"):  # not recipients
        assert (await call(registration_client, site[guard], "GET", f"/alerts/{alert_id}")).status_code == 404
        assert (
            await call(registration_client, site[guard], "POST", f"/alerts/{alert_id}/acknowledge")
        ).status_code == 404
        assert await ok(registration_client, site[guard], "GET", "/alerts") == []
    for path, body in [("resolve", None), ("dismiss", {"reason": "x"}), ("escalate", {"scope": "CAMPUS"})]:
        response = await call(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/{path}", body)
        assert response.status_code == 403 and response.json()["code"] == "STAFF_ONLY", path
    for staff in ("a", "sa"):
        assert (
            await call(registration_client, site[staff], "POST", f"/alerts/{alert_id}/acknowledge")
        ).status_code == 403
    no_reason = await call(registration_client, site["sa"], "POST", f"/alerts/{alert_id}/dismiss", {"reason": "  "})
    assert no_reason.status_code == 422
    dismissed = await ok(
        registration_client, site["a"], "POST", f"/alerts/{alert_id}/dismiss", {"reason": "False alarm"}
    )
    assert dismissed["status"] == "DISMISSED" and dismissed["close_note"] == "False alarm"
    assert one(owner_query, "SELECT status FROM alerts WHERE id = %(a)s", a=alert_id) == "DISMISSED"


async def test_the_database_enforces_the_lifecycle_and_roles(
    registration_client: AsyncClient, site: dict[str, Any], app_role_engine
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    await ok(registration_client, site["sa"], "POST", f"/alerts/{alert_id}/resolve")
    with pytest.raises(DBAPIError, match="alert status cannot change from RESOLVED to NEW"):
        await run_as(
            app_role_engine,
            site["sa"]["id"],
            f"UPDATE alerts SET status = 'NEW', closed_at = NULL WHERE id = '{alert_id}'",
        )
    open_id = await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")
    _, changed = await run_as(
        app_role_engine, site["ga"]["id"], f"UPDATE alerts SET status = 'RESOLVED' WHERE id = '{open_id}'"
    )
    assert changed == 0  # Guards change alerts only through their actions
    # Records are readable since migration 0024 (the audit-log viewer, scoped by Row-Level Security), but never
    # changed or deleted.
    for statement in (
        "UPDATE organization_audit_logs SET action = 'X_X_X'",
        "DELETE FROM organization_audit_logs",
        "INSERT INTO alerts (id) VALUES (gen_random_uuid())",
        "DELETE FROM alerts",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, site["a"]["id"], statement)
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            site["ga"]["id"],
            "INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, alert_id, title) "
            f"SELECT gen_random_uuid(), organization_id, campus_id, id, 'ALERT_NEW', '{open_id}', 'x' FROM users "
            f"WHERE id = '{site['sa']['id']}'",
        )
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            site["ga"]["id"],
            "INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action, "
            "target_type) SELECT gen_random_uuid(), organization_id, campus_id, id, 'ADMIN', 'FAKE_ACTION', 'ALERT' "
            f"FROM users WHERE id = '{site['ga']['id']}'",
        )


async def test_another_campus_cannot_see_or_touch_the_alert(
    registration_client: AsyncClient, site: dict[str, Any], app_role_engine
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    b_alert = await new_alert(registration_client, site["camera_b"])
    for intruder in ("b", "sb", "gb", "c"):
        assert (await call(registration_client, site[intruder], "GET", f"/alerts/{alert_id}")).status_code == 404
        assert alert_id not in str(await ok(registration_client, site[intruder], "GET", "/alerts"))
        assert (
            await call(registration_client, site[intruder], "GET", f"/alerts/{alert_id}/messages")
        ).status_code == 404
    for path, body in [("resolve", None), ("dismiss", {"reason": "x"}), ("escalate", {"scope": "CAMPUS"})]:
        assert (
            await call(registration_client, site["sb"], "POST", f"/alerts/{alert_id}/{path}", body)
        ).status_code == 404
    assert (await call(registration_client, site["gb"], "POST", f"/alerts/{alert_id}/acknowledge")).status_code == 404
    # The Main Admin sees only Main Campus alerts.
    assert [x["id"] for x in await ok(registration_client, site["a"], "GET", "/alerts")] == [alert_id]
    assert (await call(registration_client, site["a"], "GET", f"/alerts/{b_alert}")).status_code == 404
    # Escalating to another campus's Guard adds nobody.
    cross = await call(
        registration_client,
        site["a"],
        "POST",
        f"/alerts/{alert_id}/escalate",
        {"scope": "GUARDS", "guard_ids": [site["gb"]["display_id"]]},
    )
    assert cross.status_code == 422 and cross.json()["code"] == "NO_ELIGIBLE_GUARDS"
    rows, _ = await run_as(app_role_engine, site["sb"]["id"], "SELECT id FROM alerts")
    assert [str(r[0]) for r in rows] == [b_alert]


# --- Escalation -----------------------------------------------------------------------------------------


async def test_escalation_adds_guards_of_the_same_campus(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    sa = site["sa"]
    alert_id = await new_alert(registration_client, site["camera_a"])
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/acknowledge")
    by_location = await ok(
        registration_client,
        sa,
        "POST",
        f"/alerts/{alert_id}/escalate",
        {
            "scope": "LOCATION",
            "location": {"building_id": site["block"], "area_id": site["roof"]},
            "note": "Cover the roof",
        },
    )
    assert by_location["added_guards"] == 1 and by_location["alert"]["status"] == "ESCALATED"
    assert site["g_roof"]["display_id"] in guard_ids(by_location["alert"])
    chosen = await ok(
        registration_client,
        site["a"],
        "POST",
        f"/alerts/{alert_id}/escalate",
        {"scope": "GUARDS", "guard_ids": [site["g_none"]["display_id"]]},
    )
    assert chosen["added_guards"] == 1
    everyone = await ok(registration_client, sa, "POST", f"/alerts/{alert_id}/escalate", {"scope": "CAMPUS"})
    assert {"g_gate", "ga", "g_floor", "g_building", "g_roof", "g_none"} <= {
        g for g in site if isinstance(site[g], dict) and site[g].get("display_id") in guard_ids(everyone["alert"])
    }
    nobody = await call(registration_client, sa, "POST", f"/alerts/{alert_id}/escalate", {"scope": "CAMPUS"})
    assert nobody.status_code == 422 and nobody.json()["code"] == "NO_ELIGIBLE_GUARDS"
    assert notifications(owner_query, site["g_none"]["id"], "ALERT_ESCALATED") == 1
    assert site["gb"]["display_id"] not in guard_ids(everyone["alert"])
    # An escalated Guard responds; the alert stays escalated until staff close it.
    assert (await ok(registration_client, site["g_roof"], "POST", f"/alerts/{alert_id}/responding"))[
        "status"
    ] == "ESCALATED"
    assert count(owner_query, "SELECT 1 FROM alert_escalations WHERE alert_id = %(a)s", a=alert_id) == 3
    reasons = {r["guard_id"]: r["reason"] for r in everyone["alert"]["recipients"]}
    assert reasons[site["ga"]["display_id"]] == "LOCATION" and reasons[site["g_roof"]["display_id"]] == "ESCALATION"
    assert audit_actions(owner_query, alert_id).count("ALERT_ESCALATED") == 3
    missing = await call(registration_client, sa, "POST", f"/alerts/{alert_id}/escalate", {"scope": "LOCATION"})
    assert missing.status_code == 422 and missing.json()["code"] == "LOCATION_REQUIRED"


# --- Chat -------------------------------------------------------------------------------------------------


async def test_alert_chat_is_limited_to_staff_and_the_alerts_guards(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    client_id = str(uuid4())
    first = await ok(
        registration_client,
        site["ga"],
        "POST",
        f"/alerts/{alert_id}/messages",
        {"body": "On my way", "client_message_id": client_id},
    )
    resent = await ok(
        registration_client,
        site["ga"],
        "POST",
        f"/alerts/{alert_id}/messages",
        {"body": "On my way", "client_message_id": client_id},
    )
    assert len(first) == 1 and resent == first
    thread = await ok(
        registration_client, site["sa"], "POST", f"/alerts/{alert_id}/messages", {"body": "Go to the lobby"}
    )
    assert [(m["sender_role"], m["body"]) for m in thread] == [
        ("GUARD", "On my way"),
        ("SUPERVISOR", "Go to the lobby"),
    ]
    assert thread[0]["sender_name"] == site["ga"]["name"]
    for outsider in ("g_none", "sb"):
        assert (
            await call(registration_client, site[outsider], "GET", f"/alerts/{alert_id}/messages")
        ).status_code == 404
        assert (
            await call(registration_client, site[outsider], "POST", f"/alerts/{alert_id}/messages", {"body": "hi"})
        ).status_code == 404
    metadata = [
        r[0]
        for r in owner_query("SELECT metadata::text FROM organization_audit_logs WHERE action = 'ALERT_MESSAGE_POSTED'")
    ]
    assert len(metadata) == 2 and all("lobby" not in m.lower() and "my way" not in m for m in metadata)
    await ok(registration_client, site["sa"], "POST", f"/alerts/{alert_id}/resolve")
    closed = await call(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/messages", {"body": "late"})
    assert closed.status_code == 409
    assert len(await ok(registration_client, site["ga"], "GET", f"/alerts/{alert_id}/messages")) == 2


# --- Manual reports ---------------------------------------------------------------------------------------


def report_body(site: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "client_report_id": str(uuid4()),
        "report_type": "Suspicious Person",
        "description": "Near the gate",
        "building_id": site["fence"],
        "area_id": site["gate"],
        **changes,
    }


async def test_guards_submit_manual_reports_separately_from_alerts(
    registration_client: AsyncClient, site: dict[str, Any], owner_query, storage_root: Path
) -> None:
    ga = site["ga"]
    options = await ok(registration_client, ga, "GET", "/guard/locations")
    assert {(o["building_name"], o["floor_name"], o["area_name"]) for o in options} >= {
        ("Admin Block", "Ground", "Lobby"),
        ("Admin Block", None, "Roof"),
        ("North Fence", None, "Gate"),
    }
    body = report_body(site, photo_base64=base64.b64encode(PNG).decode())
    first = await registration_client.post(f"{API}/guard/manual-reports", json=body, headers=headers(ga))
    assert first.status_code == 201 and first.json()["created"] is True
    report = first.json()["report"]
    assert (report["building_name"], report["area_name"], report["has_photo"], report["status"]) == (
        "North Fence",
        "Gate",
        True,
        "SUBMITTED",
    )
    resend = await registration_client.post(f"{API}/guard/manual-reports", json=body, headers=headers(ga))
    assert (
        resend.status_code == 200
        and resend.json()["created"] is False
        and resend.json()["report"]["id"] == report["id"]
    )
    assert count(owner_query, "SELECT 1 FROM manual_reports") == 1
    photos = list((storage_root / str(site["org"]) / str(site["campus_a"]) / "manual_reports").iterdir())
    assert len(photos) == 1
    assert count(owner_query, "SELECT 1 FROM alerts") == 0  # a report never creates an alert
    assert notifications(owner_query, site["sa"]["id"], "MANUAL_REPORT_SUBMITTED") == 1
    assert notifications(owner_query, site["g_floor"]["id"]) == 0

    listed = await ok(registration_client, site["sa"], "GET", "/manual-reports")
    assert [r["id"] for r in listed] == [report["id"]]
    photo = await registration_client.get(f"{API}/manual-reports/{report['id']}/photo", headers=headers(site["sa"]))
    assert photo.status_code == 200 and photo.content == PNG and photo.headers["content-type"] == "image/png"
    reviewed = await ok(
        registration_client, site["a"], "POST", f"/manual-reports/{report['id']}/review", {"note": "Seen"}
    )
    assert reviewed["status"] == "REVIEWED"
    again = await call(registration_client, site["sa"], "POST", f"/manual-reports/{report['id']}/review")
    assert again.status_code == 409
    for outsider in ("sb", "b"):
        assert (
            await call(registration_client, site[outsider], "GET", f"/manual-reports/{report['id']}")
        ).status_code == 404
    assert await ok(registration_client, site["g_floor"], "GET", "/guard/manual-reports") == []
    assert (await call(registration_client, ga, "GET", "/manual-reports")).status_code == 403
    assert set(audit_actions(owner_query, report["id"])) == {"MANUAL_REPORT_SUBMITTED", "MANUAL_REPORT_REVIEWED"}


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"floor_id": "ground"}, 422, "INVALID_LOCATION"),  # a floor of another building
        ({"building_id": "block", "area_id": "lobby"}, 422, "INVALID_LOCATION"),  # area on a floor not chosen
        ({"building_id": "unknown"}, 404, "LOCATION_NOT_FOUND"),
        ({"photo_base64": "not base64!"}, 422, "INVALID_PHOTO"),
        ({"photo_base64": base64.b64encode(b"GIF89a....").decode()}, 422, "INVALID_PHOTO"),
        ({"description": "  "}, 422, "INVALID_TEXT"),
        ({"occurred_at": "2099-01-01T00:00:00Z"}, 422, "INVALID_TIME"),
    ],
)
async def test_invalid_reports_are_refused(
    changes: dict[str, Any],
    status: int,
    code: str,
    registration_client: AsyncClient,
    site: dict[str, Any],
    owner_query,
    storage_root: Path,
) -> None:
    resolved = {
        k: (str(site[v]) if v in site else str(uuid4())) if k.endswith("_id") and v != "x" else v
        for k, v in changes.items()
    }
    response = await registration_client.post(
        f"{API}/guard/manual-reports", json=report_body(site, **resolved), headers=headers(site["ga"])
    )
    assert response.status_code == status and response.json()["code"] == code, response.text
    assert count(owner_query, "SELECT 1 FROM manual_reports") == 0
    folder = storage_root / str(site["org"]) / str(site["campus_a"]) / "manual_reports"
    assert not folder.exists() or list(folder.iterdir()) == []


async def test_only_guards_submit_reports_and_large_photos_are_refused(
    registration_client: AsyncClient, site: dict[str, Any], migrated_database: DatabaseUrls
) -> None:
    assert (
        await call(registration_client, site["sa"], "POST", "/guard/manual-reports", report_body(site))
    ).status_code == 403
    async with _client_for(migrated_database, manual_report_photo_max_bytes=10_000) as small:
        big = base64.b64encode(PNG + b"\x00" * 20_000).decode()
        response = await small.post(
            f"{API}/guard/manual-reports", json=report_body(site, photo_base64=big), headers=headers(site["ga"])
        )
        assert response.status_code == 422 and response.json()["code"] == "PHOTO_TOO_LARGE"


# --- Offline sync -----------------------------------------------------------------------------------------


async def test_offline_queue_is_applied_once_in_order(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    ack, resp = str(uuid4()), str(uuid4())
    items = [
        {"kind": "ALERT_ACTION", "client_action_id": ack, "alert_id": alert_id, "action": "ACKNOWLEDGE"},
        {"kind": "ALERT_ACTION", "client_action_id": str(uuid4()), "alert_id": str(uuid4()), "action": "ACKNOWLEDGE"},
        {"kind": "ALERT_ACTION", "client_action_id": resp, "alert_id": alert_id, "action": "RESPONDING"},
        {"kind": "MANUAL_REPORT", "report": report_body(site)},
        {"kind": "ALERT_MESSAGE", "client_message_id": str(uuid4()), "alert_id": alert_id, "body": "Arrived"},
    ]
    first = await ok(registration_client, site["ga"], "POST", "/guard/sync", {"items": items})
    assert [(r["ok"], r["http_status"], r["outcome"]) for r in first] == [
        (True, 200, "DONE"),
        (False, 404, None),
        (True, 200, "DONE"),
        (True, 201, "CREATED"),
        (True, 201, "POSTED"),
    ]
    assert first[1]["code"] == "ALERT_NOT_FOUND"
    resent = await ok(registration_client, site["ga"], "POST", "/guard/sync", {"items": items})
    assert [r["outcome"] for r in resent] == ["REPLAYED", None, "REPLAYED", "REPLAYED", "POSTED"]
    assert count(owner_query, "SELECT 1 FROM manual_reports") == 1
    assert count(owner_query, "SELECT 1 FROM alert_messages") == 1
    assert count(owner_query, "SELECT 1 FROM alert_events WHERE event_type IN ('ACKNOWLEDGED', 'RESPONDING')") == 2
    assert one(owner_query, "SELECT status FROM alerts WHERE id = %(a)s", a=alert_id) == "RESPONDING"
    assert (await call(registration_client, site["sa"], "POST", "/guard/sync", {"items": items[:1]})).status_code == 403


# --- Notifications ------------------------------------------------------------------------------------------


async def test_notifications_are_private_and_can_be_marked_read(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    await new_alert(registration_client, site["camera_a"])
    await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")
    mine = await ok(registration_client, site["ga"], "GET", "/notifications")
    assert len(mine) == 2 and all(n["kind"] == "ALERT_NEW" and n["read_at"] is None for n in mine)
    await ok(registration_client, site["ga"], "POST", f"/notifications/{mine[0]['id']}/read")
    assert len(await ok(registration_client, site["ga"], "GET", "/notifications?unread_only=true")) == 1
    # Someone else's notification answers 404.
    assert (
        await call(registration_client, site["sa"], "POST", f"/notifications/{mine[1]['id']}/read")
    ).status_code == 404
    assert (await ok(registration_client, site["ga"], "POST", "/notifications/read-all"))["marked_read"] == 1
    assert len(await ok(registration_client, site["sa"], "GET", "/notifications")) == 2


# --- Cameras, campus deletion and export ------------------------------------------------------------------


async def test_a_camera_with_alert_history_is_kept(registration_client: AsyncClient, site: dict[str, Any]) -> None:
    await new_alert(registration_client, site["camera_a"])
    refused = await call(registration_client, site["a"], "DELETE", f"/cameras/{site['camera_a']}")
    assert refused.status_code == 409 and refused.json()["code"] == "CAMERA_HAS_ALERTS"


async def test_campus_deletion_erases_its_alerts_and_reports(
    registration_client: AsyncClient, outbox: FakeEmailSender, site: dict[str, Any], owner_query, storage_root: Path
) -> None:
    a_alert = await new_alert(registration_client, site["camera_a"])
    b_alert = await new_alert(registration_client, site["camera_b"])
    await ok(registration_client, site["gb"], "POST", f"/alerts/{b_alert}/acknowledge")
    await ok(registration_client, site["sb"], "POST", f"/alerts/{b_alert}/messages", {"body": "Check it"})
    b_options = await ok(registration_client, site["gb"], "GET", "/guard/locations")
    b_report = (
        await registration_client.post(
            f"{API}/guard/manual-reports",
            json={
                "client_report_id": str(uuid4()),
                "report_type": "Property Damage",
                "description": "Broken window",
                "building_id": b_options[0]["building_id"],
                "photo_base64": base64.b64encode(PNG).decode(),
            },
            headers=headers(site["gb"]),
        )
    ).json()["report"]["id"]

    started = (await start_deletion(registration_client, site["b"])).json()
    export = (await registration_client.get(started["export"]["download_url"], headers=headers(site["b"]))).json()
    assert [(x["alert_type"], x["status"]) for x in export["alerts"]] == [("WEAPON", "ACKNOWLEDGED")]
    assert [(r["report_type"], r["has_photo"]) for r in export["manual_reports"]] == [("Property Damage", True)]
    assert "alerts" not in started["export"]["not_included"]
    assert "Check it" not in str(export) and a_alert not in str(export)

    await delete_account(registration_client, outbox, site["b"])
    for value in (b_alert, b_report):
        assert traces(owner_query, value) == [], value
    assert not (storage_root / str(site["org"]) / str(site["campus_b"])).exists()
    assert one(owner_query, "SELECT status FROM alerts WHERE id = %(a)s", a=a_alert) == "NEW"
    assert count(owner_query, "SELECT 1 FROM organization_audit_logs WHERE campus_id = %(c)s", c=site["campus_b"]) == 0
    assert count(owner_query, "SELECT 1 FROM organization_audit_logs WHERE campus_id = %(c)s", c=site["campus_a"]) >= 1
