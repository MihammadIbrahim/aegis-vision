"""Audit-log viewer (Org Admin page 21, Supervisor page 17; migration 0024, decision D-024).

Covers: Admins read every record of their own campus only; Supervisors read the operational categories
and their own records; Guards and the Super Admin are refused; filters, search, paging and the CSV
export (formula-safe, itself audited); camera, location and sign-in actions writing records; nobody can
change or delete a record; the platform role cannot read them.
"""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import platform_auth_header, run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import sign_in, start
from tests.integration.test_ownership_and_deletion import (
    headers,
    one,
    ucp,  # noqa: F401  (a fixture, used by name)
)

API = "/api/v1"
LOGS = f"{API}/audit-logs"


def add_record(
    owner_execute,
    ucp: dict[str, Any],
    campus: str,
    actor: dict[str, Any] | None,
    role: str,
    action: str,
    *,
    target_type: str = "USER",
    metadata: dict[str, Any] | None = None,
    outcome: str = "SUCCESS",
    at: datetime | None = None,
) -> UUID:
    record = uuid4()
    owner_execute(
        """INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
               target_type, target_id, metadata, outcome, occurred_at)
           VALUES (%(id)s, %(org)s, %(campus)s, %(actor)s, %(role)s, %(action)s, %(tt)s, NULL, %(meta)s, %(outcome)s,
                   %(at)s)""",
        {
            "id": record,
            "org": ucp["org"],
            "campus": ucp[campus],
            "actor": actor["id"] if actor else None,
            "role": role,
            "action": action,
            "tt": target_type,
            "meta": json.dumps(metadata or {}),
            "outcome": outcome,
            "at": at or datetime.now(UTC),
        },
    )
    return record


@pytest.fixture
def records(ucp: dict[str, Any], owner_execute) -> dict[str, UUID]:
    """A mix of records in campus A (and one in campus B)."""
    a, sa, ga, b = ucp["a"], ucp["sa"], ucp["ga"], ucp["b"]
    old = datetime.now(UTC) - timedelta(days=10)
    return {
        "alert": add_record(owner_execute, ucp, "campus_a", sa, "SUPERVISOR", "ALERT_CLOSED", target_type="ALERT"),
        "guard_ack": add_record(owner_execute, ucp, "campus_a", ga, "GUARD", "ALERT_ACKNOWLEDGE", target_type="ALERT"),
        "admin_private": add_record(owner_execute, ucp, "campus_a", a, "ADMIN", "SUPERVISOR_CREATED"),
        "own_staff": add_record(owner_execute, ucp, "campus_a", sa, "SUPERVISOR", "GUARD_DETAILS_UPDATED"),
        "denied": add_record(
            owner_execute, ucp, "campus_a", a, "ADMIN", "AUTH_SIGN_IN_FAILED", outcome="FAILURE", at=old
        ),
        "formula": add_record(
            owner_execute,
            ucp,
            "campus_a",
            a,
            "ADMIN",
            "EVIDENCE_SAVED",
            target_type="EVIDENCE",
            metadata={"title": '=HYPERLINK("http://x")'},
        ),
        "campus_b": add_record(owner_execute, ucp, "campus_b", b, "ADMIN", "CAMERA_CREATED", target_type="CAMERA"),
    }


async def page(client: AsyncClient, user: dict[str, Any], **params: Any) -> dict[str, Any]:
    response = await client.get(LOGS, params=params, headers=headers(user))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(body: dict[str, Any]) -> set[str]:
    return {item["id"] for item in body["items"]}


async def test_admin_reads_every_record_of_their_own_campus_only(
    registration_client: AsyncClient, ucp: dict[str, Any], records: dict[str, UUID]
) -> None:
    body = await page(registration_client, ucp["a"])
    expected = {str(v) for k, v in records.items() if k != "campus_b"}
    assert expected <= ids(body) and str(records["campus_b"]) not in ids(body)
    item = next(i for i in body["items"] if i["id"] == str(records["admin_private"]))
    assert item["category"] == "USER_MANAGEMENT" and item["actor_display_id"] == ucp["a"]["display_id"]
    assert item["actor_role"] == "ADMIN" and item["outcome"] == "SUCCESS"
    other = await page(registration_client, ucp["b"])
    assert str(records["campus_b"]) in ids(other) and not expected & ids(other)


async def test_supervisor_reads_operational_records_and_their_own(
    registration_client: AsyncClient, ucp: dict[str, Any], records: dict[str, UUID]
) -> None:
    visible = ids(await page(registration_client, ucp["sa"]))
    assert {str(records["alert"]), str(records["guard_ack"]), str(records["formula"])} <= visible
    assert str(records["own_staff"]) in visible  # their own Staff Management record
    assert str(records["admin_private"]) not in visible and str(records["denied"]) not in visible
    # Forging a filter cannot widen it.
    forged = await page(registration_client, ucp["sa"], category="USER_MANAGEMENT")
    assert ids(forged) == {str(records["own_staff"])}
    categories = (await registration_client.get(f"{LOGS}/categories", headers=headers(ucp["sa"]))).json()
    assert "ALERTS" in categories["categories"] and "AUTHENTICATION" not in categories["categories"]
    admin_categories = (await registration_client.get(f"{LOGS}/categories", headers=headers(ucp["a"]))).json()
    assert "AUTHENTICATION" in admin_categories["categories"]


async def test_guards_and_the_super_admin_are_refused(
    registration_client: AsyncClient, ucp: dict[str, Any], records: dict[str, UUID], super_admin: UUID
) -> None:
    for path in (LOGS, f"{LOGS}/categories", f"{LOGS}/export"):
        guard = await registration_client.get(path, headers=headers(ucp["ga"]))
        assert guard.status_code == 403, path
        platform = await registration_client.get(path, headers=platform_auth_header(super_admin))
        assert platform.status_code == 403 and platform.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED", path
        assert (await registration_client.get(path)).status_code == 401


async def test_filters_search_and_paging(
    registration_client: AsyncClient, ucp: dict[str, Any], records: dict[str, UUID]
) -> None:
    client, admin = registration_client, ucp["a"]
    assert ids(await page(client, admin, category="GUARD_RESPONSE")) == {str(records["guard_ack"])}
    both = await page(client, admin, category=["GUARD_RESPONSE", "ALERTS"])
    assert ids(both) == {str(records["guard_ack"]), str(records["alert"])}
    assert ids(await page(client, admin, actor=ucp["ga"]["display_id"])) == {str(records["guard_ack"])}
    assert str(records["alert"]) in ids(await page(client, admin, actor_role="SUPERVISOR"))
    assert ids(await page(client, admin, action="SUPERVISOR_CREATED")) == {str(records["admin_private"])}
    assert ids(await page(client, admin, outcome="FAILURE")) == {str(records["denied"])}
    assert ids(await page(client, admin, q="hyperlink")) == {str(records["formula"])}
    assert ids(await page(client, admin, q="%")) == set()  # wildcards are literal
    since = (datetime.now(UTC) - timedelta(days=11)).isoformat()
    until = (datetime.now(UTC) - timedelta(days=9)).isoformat()
    assert ids(await page(client, admin, since=since, until=until)) == {str(records["denied"])}
    bad = await client.get(LOGS, params={"since": until, "until": since}, headers=headers(admin))
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_PERIOD"
    first = await page(client, admin, limit=2)
    second = await page(client, admin, limit=2, offset=2)
    assert len(first["items"]) == 2 and first["total"] >= 6 and not ids(first) & ids(second)
    newest_first = [i["occurred_at"] for i in (await page(client, admin))["items"]]
    assert newest_first == sorted(newest_first, reverse=True)


async def test_export_is_formula_safe_and_audited(
    registration_client: AsyncClient, ucp: dict[str, Any], records: dict[str, UUID], owner_query
) -> None:
    response = await registration_client.get(
        f"{LOGS}/export", params={"category": "EVIDENCE"}, headers=headers(ucp["a"])
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["occurred_at", "category", "action"] and len(rows) == 2
    details = rows[1][-1]
    assert details.startswith("{") and "=HYPERLINK" in details  # inside JSON, not at the start of a cell
    assert all(not cell.startswith(("=", "+", "-", "@")) for cell in rows[1])
    exported = one(
        owner_query,
        "SELECT metadata FROM organization_audit_logs WHERE action = 'AUDIT_LOGS_EXPORTED' AND actor_user_id = %(a)s",
        a=ucp["a"]["id"],
    )
    assert exported["rows"] == 1 and exported["categories"] == ["EVIDENCE"]


async def test_camera_and_location_changes_are_recorded(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    client, sa = registration_client, ucp["sa"]
    building = await client.post(
        f"{API}/locations/buildings", json={"name": "Block A", "kind": "INDOOR"}, headers=headers(ucp["a"])
    )
    assert building.status_code == 201, building.text
    building_id = building.json()["id"]
    floor = await client.post(
        f"{API}/locations/buildings/{building_id}/floors", json={"name": "Ground", "level": 0}, headers=headers(sa)
    )
    area = await client.post(
        f"{API}/locations/buildings/{building_id}/areas",
        json={"name": "Lobby", "floor_id": floor.json()["id"]},
        headers=headers(sa),
    )
    assert area.status_code == 201, area.text
    camera = await client.post(
        f"{API}/cameras",
        json={"display_name": "Lobby cam", "connection_type": "RTSP", "stream_url": "rtsp://10.0.0.9/secret-path"},
        headers=headers(sa),
    )
    assert camera.status_code == 201, camera.text
    camera_id = camera.json()["id"]
    selection = {"building_id": building_id, "floor_id": floor.json()["id"], "area_id": area.json()["id"]}
    linked = await client.put(f"{API}/locations/camera-links/{camera_id}", json=selection, headers=headers(sa))
    assert linked.status_code == 200, linked.text
    edited = await client.put(
        f"{API}/cameras/{camera_id}",
        json={"display_name": "Lobby camera", "connection_type": "RTSP", "stream_url": "rtsp://10.0.0.9/other"},
        headers=headers(sa),
    )
    assert edited.status_code == 200, edited.text
    assert (await client.delete(f"{API}/cameras/{camera_id}", headers=headers(sa))).status_code == 204
    assert (await client.delete(f"{API}/locations/areas/{area.json()['id']}", headers=headers(sa))).status_code == 204

    actions = [
        r[0]
        for r in owner_query(
            "SELECT action FROM organization_audit_logs WHERE campus_id = %(c)s AND category = 'CAMERA_LOCATION' "
            "ORDER BY occurred_at, action",
            {"c": ucp["campus_a"]},
        )
    ]
    for action in (
        "BUILDING_CREATED",
        "FLOOR_CREATED",
        "AREA_CREATED",
        "CAMERA_CREATED",
        "CAMERA_LOCATION_SET",
        "CAMERA_UPDATED",
        "CAMERA_DELETED",
        "AREA_DELETED",
    ):
        assert action in actions, action
    updated = one(
        owner_query,
        "SELECT metadata FROM organization_audit_logs WHERE action = 'CAMERA_UPDATED' AND target_id = %(c)s",
        c=camera_id,
    )
    assert updated["name"] == "Lobby camera" and "stream_address" in updated["changed"]
    assert "secret-path" not in json.dumps(updated) and "10.0.0.9" not in json.dumps(updated)
    # The Supervisor sees these operational records in the viewer, with readable labels.
    seen = await page(client, sa, category="CAMERA_LOCATION")
    assert {"CAMERA_CREATED", "BUILDING_CREATED"} <= {i["action"] for i in seen["items"]}


async def test_sign_in_attempts_are_recorded_for_admins_only(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    wrong = await start(registration_client, a, password="Wrong-pass-2026")
    assert wrong.status_code == 401
    await sign_in(registration_client, outbox, a)
    rows = owner_query(
        "SELECT action, outcome, metadata FROM organization_audit_logs WHERE actor_user_id = %(a)s "
        "AND category = 'AUTHENTICATION' ORDER BY occurred_at",
        {"a": a["id"]},
    )
    assert [(r[0], r[1]) for r in rows] == [("AUTH_SIGN_IN_FAILED", "FAILURE"), ("AUTH_SIGN_IN_SUCCEEDED", "SUCCESS")]
    assert rows[0][2] == {"reason": "WRONG_PASSWORD"}
    assert "Wrong-pass" not in str(rows) and a["password"] not in str(rows)  # never a secret
    seen = await page(registration_client, a, category="AUTHENTICATION")
    assert {"AUTH_SIGN_IN_FAILED", "AUTH_SIGN_IN_SUCCEEDED"} <= {i["action"] for i in seen["items"]}
    hidden = await page(registration_client, ucp["sa"], q="AUTH_SIGN_IN")
    assert hidden["items"] == []


async def test_nobody_can_change_or_delete_records_and_the_platform_role_cannot_read(
    ucp: dict[str, Any], records: dict[str, UUID], app_role_engine, platform_role_engine
) -> None:
    for statement in (
        "UPDATE organization_audit_logs SET action = 'X_X_X' WHERE id = :id",
        "DELETE FROM organization_audit_logs WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, ucp["a"]["id"], statement, {"id": records["alert"]})
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, "SELECT 1 FROM organization_audit_logs")
    # Guards read nothing even directly.
    rows, _ = await run_as(app_role_engine, ucp["ga"]["id"], "SELECT id FROM organization_audit_logs")
    assert rows == []
    # A Supervisor cannot insert a record for someone else or another category of actor.
    with pytest.raises(DBAPIError):
        await run_as(
            app_role_engine,
            ucp["sa"]["id"],
            "INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action, "
            "target_type) VALUES (gen_random_uuid(), :org, :campus, :actor, 'ADMIN', 'FORGED_ACTION', 'USER')",
            {"org": ucp["org"], "campus": ucp["campus_a"], "actor": ucp["a"]["id"]},
        )
