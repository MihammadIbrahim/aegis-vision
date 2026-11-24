"""Incidents & Investigations (migration 0025, decision D-024): open, list, filter and search cases; link alerts,
manual reports and evidence; assign, notes, status flow with notes; history and audit; chat with invited
Guards and the Guards of linked alerts; missing-footage flags; campus isolation (API and database); Guard
and Super Admin refusal; records kept when accounts go; the evidence foreign-key fix of 0025."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import platform_auth_header, run_as
from tests.integration.test_alerts import new_alert, ok, report_body, site  # noqa: F401  (site is a fixture)
from tests.integration.test_ownership_and_deletion import count, headers, one, ucp  # noqa: F401

API = "/api/v1"
INC = f"{API}/incidents"


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{API}{path}", json=body, headers=headers(user))


async def open_case(client: AsyncClient, user: dict[str, Any], **body: Any) -> dict[str, Any]:
    payload = {"title": "Weapon seen in lobby", **body}
    created: dict[str, Any] = await ok(client, user, "POST", "/incidents", payload)
    return created


async def submit_report(client: AsyncClient, site: dict[str, Any]) -> str:
    response = await client.post(f"{API}/guard/manual-reports", json=report_body(site), headers=headers(site["ga"]))
    assert response.status_code == 201, response.text
    report_id: str = response.json()["report"]["id"]
    return report_id


def add_evidence(owner_execute, site: dict[str, Any], campus: str = "campus_a", camera: str = "camera_a") -> UUID:
    evidence = uuid4()
    owner_execute(
        """INSERT INTO evidence_items (id, organization_id, campus_id, kind, title, source, camera_id, camera_name,
               covers_from, covers_to, relative_path, media_type, size_bytes, sha256, saved_by_name)
           VALUES (%(id)s, %(org)s, %(campus)s, 'SNAPSHOT', 'Lobby still', 'PLAYBACK', %(cam)s, 'Gate', now(), now(),
                   %(path)s, 'image/jpeg', 10, repeat('a', 64), 'Maryam')""",
        {"id": evidence, "org": site["org"], "campus": site[campus], "cam": site[camera], "path": f"e/{evidence}.jpg"},
    )
    return evidence


async def test_staff_open_link_assign_and_resolve_a_case_with_full_history(
    registration_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    client, a, sa = registration_client, site["a"], site["sa"]
    alert = await new_alert(client, site["camera_a"])
    report = await submit_report(client, site)
    evidence = add_evidence(owner_execute, site)

    staff = await ok(client, sa, "GET", "/incidents/assignable-staff")
    assert {s["user_id"] for s in staff} == {str(a["id"]), str(sa["id"])}

    created = await open_case(
        client,
        sa,
        description="Reported by camera and a Guard.",
        priority="HIGH",
        camera_id=str(site["camera_a"]),
        assigned_to=str(a["id"]),
        alert_ids=[alert],
        report_ids=[report],
        evidence_ids=[str(evidence)],
    )
    assert created["reference"] == "INC-000001" and created["status"] == "OPEN"
    assert created["location"] == "Admin Block › Ground › Lobby" and created["camera_name"] == "Gate"
    assert (created["alert_count"], created["report_count"], created["evidence_count"]) == (1, 1, 1)
    assert created["assigned_name"] == a["name"] or created["assigned_to"] == str(a["id"])
    case = created["id"]
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'INCIDENT_ASSIGNED' AND user_id = %(u)s",
                 u=a["id"]) == 1  # fmt: skip

    second = await open_case(client, a, title="Broken fence")
    assert second["reference"] == "INC-000002"

    detail = await ok(client, a, "GET", f"/incidents/{case}")
    assert [x["alert_id"] for x in detail["alerts"]] == [alert]
    assert detail["alerts"][0]["alert_type"] == "WEAPON" and detail["alerts"][0]["location"]
    assert [x["report_id"] for x in detail["reports"]] == [report]
    assert [x["evidence_id"] for x in detail["evidence"]] == [str(evidence)]
    assert detail["ai_assistance_available"] is False
    kinds = [e["kind"] for e in detail["history"]]
    assert kinds[0] == "CREATED" and {"ALERT_LINKED", "REPORT_LINKED", "EVIDENCE_LINKED", "ASSIGNED"} <= set(kinds)
    # No recording exists around the alert or the case: stated, not hidden.
    assert {g["issue"] for g in detail["footage_gaps"]} == {"NO_RECORDING"}

    # A note, a status flow; resolving needs a note; a resolved case is read-only until reopened.
    assert (
        await call(client, a, "POST", f"/incidents/{case}/notes", {"note": "Called the police."})
    ).status_code == 204
    await ok(client, sa, "POST", f"/incidents/{case}/status", {"status": "UNDER_INVESTIGATION"})
    await ok(client, sa, "POST", f"/incidents/{case}/status", {"status": "RESPONDING"})
    no_note = await call(client, sa, "POST", f"/incidents/{case}/status", {"status": "RESOLVED"})
    assert no_note.status_code == 422 and no_note.json()["code"] == "NOTE_REQUIRED"
    resolved = await ok(client, sa, "POST", f"/incidents/{case}/status", {"status": "RESOLVED", "note": "Handled."})
    assert resolved["status"] == "RESOLVED" and resolved["closed_at"] and resolved["resolution_note"] == "Handled."
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'INCIDENT_STATUS_CHANGED' "
                 "AND user_id = %(u)s", u=a["id"]) >= 1  # fmt: skip
    edit = await call(client, a, "PUT", f"/incidents/{case}", {"title": "Changed"})
    assert edit.status_code == 409 and edit.json()["code"] == "INCIDENT_CLOSED"
    wrong = await call(client, a, "POST", f"/incidents/{case}/status", {"status": "OPEN", "note": "x"})
    assert wrong.status_code == 409
    reopened = await ok(
        client, a, "POST", f"/incidents/{case}/status", {"status": "UNDER_INVESTIGATION", "note": "New footage."}
    )
    assert reopened["closed_at"] is None

    history = [(e["kind"], e["to_status"]) for e in (await ok(client, a, "GET", f"/incidents/{case}"))["history"]]
    assert ("NOTE_ADDED", None) in history and ("STATUS_CHANGED", "RESOLVED") in history
    actions = {
        r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE target_id = %(t)s", {"t": case})
    }
    assert {"INCIDENT_CREATED", "INCIDENT_ALERT_LINKED", "INCIDENT_STATUS_CHANGED", "INCIDENT_NOTE_ADDED"} <= actions


async def test_list_filters_search_and_counts(registration_client: AsyncClient, site: dict[str, Any]) -> None:
    client, a, sa = registration_client, site["a"], site["sa"]
    first = await open_case(client, a, title="Fight near gate", priority="HIGH", assigned_to=str(sa["id"]))
    await open_case(client, a, title="Lost badge")
    closed = await open_case(client, sa, title="False alarm")
    await ok(client, sa, "POST", f"/incidents/{closed['id']}/status", {"status": "DISMISSED", "note": "Cat"})

    everything = await ok(client, a, "GET", "/incidents")
    assert everything["total"] == 3 and everything["counts"]["DISMISSED"] == 1 and everything["counts"]["OPEN"] == 2
    assert everything["items"][-1]["id"] == closed["id"]  # closed cases last
    assert everything["items"][0]["id"] == first["id"]  # high priority first

    def titles(body: dict[str, Any]) -> list[str]:
        return [i["title"] for i in body["items"]]

    assert titles(await ok(client, a, "GET", "/incidents?status=DISMISSED")) == ["False alarm"]
    assert titles(await ok(client, a, "GET", "/incidents?priority=HIGH")) == ["Fight near gate"]
    assert titles(await ok(client, sa, "GET", "/incidents?assigned=me")) == ["Fight near gate"]
    assert set(titles(await ok(client, a, "GET", "/incidents?assigned=none"))) == {"Lost badge", "False alarm"}
    assert titles(await ok(client, a, "GET", "/incidents?q=badge")) == ["Lost badge"]
    assert titles(await ok(client, a, "GET", f"/incidents?q={first['reference']}")) == ["Fight near gate"]
    assert titles(await ok(client, a, "GET", "/incidents?q=%25")) == []
    later = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    future = await registration_client.get(f"{INC}", params={"since": later}, headers=headers(a))
    assert future.json()["items"] == []


async def test_chat_with_invited_guards_and_guards_of_linked_alerts(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client, a, ga, g_none = registration_client, site["a"], site["ga"], site["g_none"]
    alert = await new_alert(client, site["camera_a"])  # reaches ga (Lobby), not g_none
    case = (await open_case(client, a, alert_ids=[alert]))["id"]

    # ga takes part as a recipient of the linked alert; g_none does not, until invited.
    assert (await call(client, ga, "GET", f"/incidents/{case}/messages")).status_code == 200
    assert (await call(client, g_none, "GET", f"/incidents/{case}/messages")).status_code == 404
    assert (await call(client, g_none, "GET", f"/incidents/{case}")).status_code == 403  # details are staff-only
    assert (await call(client, a, "PUT", f"/incidents/{case}/participants/{g_none['id']}")).status_code == 204
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'INCIDENT_INVITED' AND user_id = %(u)s",
                 u=g_none["id"]) == 1  # fmt: skip
    posted = await ok(client, g_none, "POST", f"/incidents/{case}/messages", {"body": "On my way."})
    assert posted["sender_role"] == "GUARD"
    await ok(client, site["sa"], "POST", f"/incidents/{case}/messages", {"body": "Go to the lobby."})
    thread = await ok(client, ga, "GET", f"/incidents/{case}/messages")
    assert [m["body"] for m in thread] == ["On my way.", "Go to the lobby."]
    mine = await ok(client, g_none, "GET", "/guard/incidents")
    assert [i["id"] for i in mine] == [case]

    participants = (await ok(client, a, "GET", f"/incidents/{case}"))["participants"]
    assert {(p["display_id"], p["invited"]) for p in participants} >= {
        (ga["display_id"], False),
        (g_none["display_id"], True),
    }
    assert (await call(client, a, "DELETE", f"/incidents/{case}/participants/{g_none['id']}")).status_code == 204
    assert (await call(client, g_none, "GET", f"/incidents/{case}/messages")).status_code == 404
    guard_write = await call(client, ga, "POST", "/incidents", {"title": "Guard case"})
    assert guard_write.status_code == 403
    audit = {r[0] for r in owner_query("SELECT category FROM organization_audit_logs WHERE action = "
                                       "'INCIDENT_MESSAGE_POSTED'")}  # fmt: skip
    assert audit == {"INCIDENT_CHAT"}


async def test_campus_isolation_and_refusals(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, super_admin, app_role_engine
) -> None:
    client, a, b = registration_client, site["a"], site["b"]
    case = (await open_case(client, a))["id"]
    alert_b = await new_alert(client, site["camera_b"])
    evidence_b = add_evidence(owner_execute, site, "campus_b", "camera_b")
    assert (await call(client, b, "GET", f"/incidents/{case}")).status_code == 404
    assert (await ok(client, b, "GET", "/incidents"))["items"] == []
    for kind, target in (("alerts", alert_b), ("evidence", evidence_b)):
        linked = await call(client, a, "PUT", f"/incidents/{case}/{kind}/{target}")
        assert linked.status_code == 404, kind
    wrong_assignee = await call(client, a, "PUT", f"/incidents/{case}/assignee", {"user_id": str(b["id"])})
    assert wrong_assignee.status_code == 422 and wrong_assignee.json()["code"] == "INVALID_ASSIGNEE"
    guard_assignee = await call(client, a, "PUT", f"/incidents/{case}/assignee", {"user_id": str(site["ga"]["id"])})
    assert guard_assignee.status_code == 422
    platform = await client.get(INC, headers=platform_auth_header(super_admin))
    assert platform.status_code == 403
    rows, _ = await run_as(app_role_engine, b["id"], "SELECT id FROM incidents")
    assert rows == []
    rows, _ = await run_as(app_role_engine, site["g_none"]["id"], "SELECT id FROM incidents")
    assert rows == []
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, a["id"], "DELETE FROM incidents")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, a["id"], "UPDATE incident_events SET note = 'x'")


async def test_linking_rules_and_records_kept_when_accounts_go(
    registration_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    client, a, sa = registration_client, site["a"], site["sa"]
    report = await submit_report(client, site)
    first = (await open_case(client, sa, report_ids=[report]))["id"]
    second = (await open_case(client, a, title="Second case"))["id"]
    taken = await call(client, a, "PUT", f"/incidents/{second}/reports/{report}")
    assert taken.status_code == 409 and taken.json()["code"] == "REPORT_ALREADY_LINKED"
    unlinked = await ok(client, a, "DELETE", f"/incidents/{first}/reports/{report}")
    assert unlinked["report_count"] == 0
    await ok(client, a, "PUT", f"/incidents/{second}/reports/{report}")
    assert (await call(client, a, "DELETE", f"/incidents/{first}/reports/{report}")).status_code == 404

    # The Supervisor leaves and their account is removed: the case keeps who opened it.
    owner_execute("DELETE FROM users WHERE id = %(u)s", {"u": sa["id"]})
    kept = await ok(client, a, "GET", f"/incidents/{first}")
    assert kept["incident"]["created_by"] is None and kept["incident"]["created_by_name"] == sa["name"]
    assert kept["history"][0]["actor_name"] == sa["name"]


async def test_cameras_with_evidence_can_be_removed_and_the_evidence_kept(
    registration_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    """The 0025 fix: deleting a camera used to fail when evidence referred to it."""
    camera = one(
        owner_query,
        "INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (gen_random_uuid(), %(o)s, %(c)s, "
        "'Spare') RETURNING id",
        o=site["org"],
        c=site["campus_a"],
    )
    evidence = uuid4()
    owner_execute(
        """INSERT INTO evidence_items (id, organization_id, campus_id, kind, title, source, camera_id, camera_name,
               covers_from, covers_to, relative_path, media_type, size_bytes, sha256, saved_by_name)
           VALUES (%(id)s, %(org)s, %(campus)s, 'SNAPSHOT', 'Spare still', 'PLAYBACK', %(cam)s, 'Spare', now(), now(),
                   %(path)s, 'image/jpeg', 10, repeat('b', 64), 'Maryam')""",
        {"id": evidence, "org": site["org"], "campus": site["campus_a"], "cam": camera, "path": f"e/{evidence}.jpg"},
    )
    removed = await call(registration_client, site["a"], "DELETE", f"/cameras/{camera}")
    assert removed.status_code == 204, removed.text
    assert owner_query(
        "SELECT camera_id, camera_name, organization_id IS NOT NULL FROM evidence_items WHERE id = %(e)s",
        {"e": evidence},
    ) == [(None, "Spare", True)]
