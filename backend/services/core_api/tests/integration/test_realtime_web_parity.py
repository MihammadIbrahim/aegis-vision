"""Decision D-026: the web app's Admins and Supervisors on the same real-time channel and voice calls as the Guard app.

Admin and Supervisor tokens open the WebSocket; they hear about Guard actions, alert chat, case notes and case chat at
once; a Guard hears staff replies; voice calls started by staff ring the Guards and the other staff, carry the target's
title and location, and end for everyone; another campus hears nothing; a Guard's assignment change ends only that
Guard's socket. Uses the ``site`` fixture of the alert tests (Main Campus: Admin a, Supervisor sa, Guards ga, g_floor,
g_building …; Campus B: Admin b)."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls
from tests.integration.test_alerts import call, new_alert, ok, site  # noqa: F401
from tests.integration.test_locations import ok as loc_ok
from tests.integration.test_ownership_and_deletion import headers, ucp  # noqa: F401
from tests.integration.test_realtime_calls_push import _connected, voice  # noqa: F401

CASE = {"title": "Weapon at the lobby", "priority": "HIGH"}


async def test_staff_sockets_follow_guard_actions_and_both_chats(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    admin = await _connected(registration_client, site["a"])
    supervisor = await _connected(registration_client, site["sa"])
    guard = await _connected(registration_client, site["ga"])
    alert_id = await new_alert(registration_client, site["camera_a"])
    assert (await admin.event("alert.updated"))["alert_id"] == alert_id
    assert (await guard.event("alert.assigned"))["alert_id"] == alert_id

    # Guard acknowledges, asks for more Guards and responds: the web hears each change.
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/acknowledge")
    assert (await supervisor.event("alert.updated"))["alert_id"] == alert_id
    await ok(
        registration_client,
        site["ga"],
        "POST",
        f"/alerts/{alert_id}/assessment",
        {"assessment": "ADDITIONAL_GUARDS_REQUIRED"},
    )
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/responding")
    assert (await admin.event("alert.updated"))["alert_id"] == alert_id

    # Alert chat both ways.
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/messages", {"body": "At the lobby"})
    assert (await admin.event("alert.message"))["alert_id"] == alert_id
    assert (await supervisor.event("alert.message"))["alert_id"] == alert_id
    await ok(registration_client, site["sa"], "POST", f"/alerts/{alert_id}/messages", {"body": "Hold the door"})
    assert (await guard.event("alert.message"))["alert_id"] == alert_id

    # A case: invitation, a note (no status change) and chat reach the right people at once.
    case = await ok(registration_client, site["sa"], "POST", "/incidents", CASE)
    await ok(registration_client, site["sa"], "PUT", f"/incidents/{case['id']}/participants/{site['g_roof']['id']}")
    roof = await _connected(registration_client, site["g_roof"])
    await ok(registration_client, site["sa"], "POST", f"/incidents/{case['id']}/notes", {"note": "Footage requested."})
    note = await admin.event("incident.updated")
    while note.get("kind") != "NOTE_ADDED":
        note = await admin.event("incident.updated")
    assert note["incident_id"] == case["id"] and "state" not in note
    await ok(registration_client, site["g_roof"], "POST", f"/incidents/{case['id']}/messages", {"body": "On my way"})
    assert (await admin.event("incident.message"))["incident_id"] == case["id"]
    await ok(registration_client, site["a"], "POST", f"/incidents/{case['id']}/messages", {"body": "Thanks"})
    assert (await roof.event("incident.message"))["incident_id"] == case["id"]
    await ok(
        registration_client, site["sa"], "POST", f"/incidents/{case['id']}/status", {"status": "UNDER_INVESTIGATION"}
    )
    status = await admin.event("incident.updated")
    while status.get("kind") != "STATUS_CHANGED":
        status = await admin.event("incident.updated")
    assert status["state"] == "UNDER_INVESTIGATION"
    for socket in (admin, supervisor, guard, roof):
        await socket.close()


async def test_staff_calls_ring_guards_carry_the_target_and_end_for_everyone(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any]
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    guard = await _connected(voice, site["ga"])
    other_campus = await _connected(voice, site["b"])
    started = await voice.post(f"/api/v1/alerts/{alert_id}/calls", headers=headers(site["a"]))
    assert started.status_code == 201, started.text
    call_body = started.json()["call"]
    call_id = call_body["id"]
    assert call_body["started_by_role"] == "ADMIN" and call_body["can_end"]
    assert call_body["target_title"] == "Weapon alert · Gate"
    assert call_body["target_location"] == "Admin Block → Ground → Lobby"
    invited = await guard.event("call.invited")
    assert invited["call_id"] == call_id and invited["alert_id"] == alert_id
    ringing = await ok(voice, site["ga"], "GET", "/calls")
    assert [c["id"] for c in ringing] == [call_id] and ringing[0]["my_state"] == "INVITED"
    assert ringing[0]["target_location"] == "Admin Block → Ground → Lobby"

    # The Supervisor joins, the Guard joins, and the Supervisor (not the starter) ends it for everyone.
    joined = await ok(voice, site["sa"], "POST", f"/calls/{call_id}/join")
    assert joined["call"]["can_end"] and joined["connection"]["room"] == f"cctv-call-{call_id}"
    await ok(voice, site["ga"], "POST", f"/calls/{call_id}/join")
    ended = await ok(voice, site["sa"], "POST", f"/calls/{call_id}/end")
    assert ended["status"] == "ENDED" and ended["end_reason"] == "ENDED_BY_USER"
    update = await guard.event("call.updated")
    while update.get("state") != "ENDED":
        update = await guard.event("call.updated")
    assert await ok(voice, site["ga"], "GET", "/calls") == []
    await other_campus.nothing()
    for socket in (guard, other_campus):
        await socket.close()


async def test_a_guard_started_case_call_rings_the_web(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any]
) -> None:
    case = await ok(registration_client, site["sa"], "POST", "/incidents", CASE)
    await ok(registration_client, site["sa"], "PUT", f"/incidents/{case['id']}/participants/{site['g_roof']['id']}")
    admin = await _connected(voice, site["a"])
    started = await ok(voice, site["g_roof"], "POST", f"/incidents/{case['id']}/calls")
    assert started["call"]["target_title"] == f"{case['reference']} · Weapon at the lobby"
    invited = await admin.event("call.invited")
    assert invited["incident_id"] == case["id"]
    ringing = await ok(voice, site["a"], "GET", "/calls")
    assert ringing[0]["started_by_name"] == "g_roof" and ringing[0]["started_by_role"] == "GUARD"
    declined = await ok(voice, site["a"], "POST", f"/calls/{ringing[0]['id']}/decline")
    assert declined["my_state"] == "DECLINED"
    left = await ok(voice, site["g_roof"], "POST", f"/calls/{ringing[0]['id']}/leave")
    assert left["call_ended"] and left["call"]["end_reason"] == "EVERYONE_LEFT"
    await admin.close()


async def test_an_assignment_change_ends_only_the_guards_socket(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    guard = await _connected(registration_client, site["ga"])
    admin = await _connected(registration_client, site["a"])
    await loc_ok(registration_client, site["sa"], "DELETE", f"/guard-assignments/{site['ga']['display_id']}")
    assert (await guard.receive())["code"] == "ASSIGNMENT_CHANGED"
    await admin.send({"type": "ping"})
    message = await admin.receive()
    while message.get("type") != "pong":
        message = await admin.receive()
    for socket in (guard, admin):
        await socket.close()


# --- Migration 0031 -------------------------------------------------------------------------------------------------

SCRATCH = "cctv_migration_0031_roundtrip"


@pytest.fixture
def scratch(database_urls: DatabaseUrls) -> Iterator[tuple[Config, str]]:
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{SCRATCH}"')
    owner = make_url(database_urls.owner_server).set(database=SCRATCH)
    config = Config(str(CORE_API_DIR / "alembic.ini"))
    url = owner.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APP_DB_USER", database_urls.app_user)
        mp.setenv("PLATFORM_DB_USER", database_urls.platform_user)
        yield config, owner.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


def _triggers(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        return {r[0] for r in conn.execute("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'trg_rt_incident%'")}


def test_migration_0031_round_trips(scratch: tuple[Config, str]) -> None:
    config, dsn = scratch
    command.upgrade(config, "0031")
    assert _triggers(dsn) == {"trg_rt_incident_events", "trg_rt_incident_messages", "trg_rt_incident_participants"}
    command.downgrade(config, "0030")
    assert _triggers(dsn) == {"trg_rt_incidents", "trg_rt_incident_messages", "trg_rt_incident_participants"}
    command.upgrade(config, "head")
    assert "trg_rt_incident_events" in _triggers(dsn)
