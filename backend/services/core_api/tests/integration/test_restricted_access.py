"""Restricted-Area Access Management (migration 0017, decision D-016): Admins mark restricted areas
and grant, change and revoke access; Supervisors read current authorizations and check access;
validity, schedules, history, audit, deactivation, and campus isolation."""

# ruff: noqa: F811  (the imported ``ucp`` fixture is used as a test parameter)

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import run_as
from tests.integration.test_locations import area, building, floor
from tests.integration.test_ownership_and_deletion import (
    count,
    headers,
    ucp,  # noqa: F401  (a fixture, used by name)
)
from tests.integration.test_personnel import register

R = "/api/v1/restricted-access"


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None, **kw: Any):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{R}{path}", json=body, headers=headers(user), **kw)


async def ok(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None, **kw: Any) -> Any:
    response = await call(client, user, method, path, body, **kw)
    assert response.status_code in (200, 201, 204), (method, path, response.text)
    return response.json() if response.content else None


@pytest.fixture
async def site(registration_client: AsyncClient, ucp: dict[str, Any]) -> dict[str, Any]:
    """Campus A: an IT block with a server room, a network room and a lobby; a person. Campus B:
    one restricted area and a person."""
    a, b = ucp["a"], ucp["b"]
    block = await building(registration_client, a, "IT Block")
    ground = await floor(registration_client, a, block, "Ground", 0)
    server = await area(registration_client, a, block, "Server Room", floor_id=ground)
    network = await area(registration_client, a, block, "Network Room", floor_id=ground)
    lobby = await area(registration_client, a, block, "Lobby", floor_id=ground)
    b_block = await building(registration_client, b, "B Block")
    b_lab = await area(registration_client, b, b_block, "B Lab")
    for user, area_id in ((a, server), (a, network), (b, b_lab)):
        await ok(registration_client, user, "PUT", f"/areas/{area_id}", {"access_note": "Authorized staff only"})
    engineer = await register(registration_client, a, "Imran Engineer", department="IT")
    b_person = await register(registration_client, b, "B Person")
    return {
        "server": server,
        "network": network,
        "lobby": lobby,
        "b_lab": b_lab,
        "engineer": engineer,
        "b_person": b_person,
        "block": block,
    }


async def test_admin_grants_access_to_several_areas_with_history_and_audit(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    areas = await ok(registration_client, a, "GET", "/areas")
    assert [x["area_name"] for x in areas] == ["Network Room", "Server Room"]
    every = await ok(registration_client, a, "GET", "/areas", params={"include_unrestricted": "true"})
    assert {x["area_name"]: x["is_restricted"] for x in every} == {
        "Lobby": False,
        "Network Room": True,
        "Server Room": True,
    }
    person = site["engineer"]["id"]
    until = (datetime.now(UTC) + timedelta(days=90)).isoformat()
    granted = await ok(
        registration_client,
        a,
        "POST",
        "/grants",
        {
            "personnel_id": person,
            "area_ids": [site["server"], site["network"]],
            "valid_until": until,
            "schedule": {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00", "timezone": "Asia/Karachi"},
            "note": "On-call engineer",
        },
    )
    assert len(granted) == 2 and {g["area"]["name"] for g in granted} == {"Server Room", "Network Room"}
    assert all(g["status"] == "ACTIVE" and g["granted_by"] == "Maryam Siddiqui" for g in granted)
    assert granted[0]["schedule"]["days"] == [1, 2, 3, 4, 5]

    room = await ok(registration_client, a, "GET", f"/areas/{site['server']}/grants")
    assert [g["personnel"]["full_name"] for g in room] == ["Imran Engineer"]
    mine = await ok(registration_client, a, "GET", f"/personnel/{person}/grants")
    assert len(mine) == 2
    server_grant = next(g for g in granted if g["area"]["id"] == site["server"])
    changed = await ok(
        registration_client,
        a,
        "PUT",
        f"/grants/{server_grant['id']}",
        {"valid_until": None, "schedule": None, "note": "Permanent"},
    )
    assert changed["valid_until"] is None and changed["schedule"] is None and changed["note"] == "Permanent"
    revoked = await ok(
        registration_client, a, "POST", f"/grants/{server_grant['id']}/revoke", {"reason": "Role change"}
    )
    assert revoked["status"] == "REVOKED" and revoked["revoke_reason"] == "Role change"
    assert (await call(registration_client, a, "PUT", f"/grants/{server_grant['id']}", {})).status_code == 409
    assert len(await ok(registration_client, a, "GET", f"/personnel/{person}/grants")) == 1
    history = await ok(registration_client, a, "GET", f"/personnel/{person}/grants", params={"include_revoked": "true"})
    assert sorted(g["status"] for g in history) == ["ACTIVE", "REVOKED"]
    trail = await ok(registration_client, a, "GET", f"/grants/{server_grant['id']}/history")
    assert [e["action"] for e in trail["history"]] == [
        "AREA_ACCESS_GRANTED",
        "AREA_ACCESS_UPDATED",
        "AREA_ACCESS_REVOKED",
    ]
    assert set(trail["history"][1]["details"]["changed"]) == {"valid_until", "schedule", "note"}
    # The same person and area can be granted again after a revocation.
    again = await ok(registration_client, a, "POST", "/grants", {"personnel_id": person, "area_ids": [site["server"]]})
    assert again[0]["status"] == "ACTIVE"
    actions = [
        r[0]
        for r in owner_query(
            "SELECT action FROM organization_audit_logs WHERE campus_id = %(c)s "
            "AND target_type IN ('AREA_ACCESS', 'RESTRICTED_AREA') ORDER BY occurred_at",
            {"c": ucp["campus_a"]},
        )
    ]
    assert actions.count("RESTRICTED_AREA_MARKED") == 2 and actions.count("AREA_ACCESS_GRANTED") == 3


async def test_grant_rules(registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any]) -> None:
    a = ucp["a"]
    person = site["engineer"]["id"]

    async def refused(body: dict[str, Any], status: int, code: str) -> None:
        response = await call(registration_client, a, "POST", "/grants", {"personnel_id": person, **body})
        assert response.status_code == status and response.json().get("code") == code, response.text

    await refused({"area_ids": [site["lobby"]]}, 409, "AREA_NOT_RESTRICTED")
    await ok(registration_client, a, "POST", "/grants", {"personnel_id": person, "area_ids": [site["server"]]})
    await refused({"area_ids": [site["network"], site["server"]]}, 409, "ACCESS_ALREADY_GRANTED")
    # All or nothing: the network room was not granted either.
    assert len(await ok(registration_client, a, "GET", f"/personnel/{person}/grants")) == 1
    now = datetime.now(UTC)
    await refused(
        {
            "area_ids": [site["network"]],
            "valid_from": now.isoformat(),
            "valid_until": (now - timedelta(hours=1)).isoformat(),
        },
        422,
        "INVALID_VALIDITY",
    )
    await refused(
        {
            "area_ids": [site["network"]],
            "schedule": {"days": [1], "start": "18:00", "end": "08:00", "timezone": "Asia/Karachi"},
        },
        422,
        "INVALID_SCHEDULE",
    )
    await refused(
        {
            "area_ids": [site["network"]],
            "schedule": {"days": [1], "start": "08:00", "end": "18:00", "timezone": "Nowhere/City"},
        },
        422,
        "INVALID_TIMEZONE",
    )
    naive = await call(
        registration_client,
        a,
        "POST",
        "/grants",
        {"personnel_id": person, "area_ids": [site["network"]], "valid_until": "2030-01-01T00:00:00"},
    )
    assert naive.status_code == 422  # times must carry a time zone
    await registration_client.post(f"/api/v1/personnel/{person}/deactivate", headers=headers(a))
    await refused({"area_ids": [site["network"]]}, 409, "PERSONNEL_INACTIVE")


async def test_supervisors_read_current_authorizations_but_change_nothing(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any]
) -> None:
    a, sa, ga = ucp["a"], ucp["sa"], ucp["ga"]
    person = site["engineer"]["id"]
    grant = (
        await ok(
            registration_client,
            a,
            "POST",
            "/grants",
            {"personnel_id": person, "area_ids": [site["server"]], "note": "Admin-only note"},
        )
    )[0]
    assert [x["area_name"] for x in await ok(registration_client, sa, "GET", "/areas")] == [
        "Network Room",
        "Server Room",
    ]
    room = await ok(registration_client, sa, "GET", f"/areas/{site['server']}/grants")
    assert room[0]["personnel"]["full_name"] == "Imran Engineer" and room[0]["note"] is None
    assert (await ok(registration_client, sa, "GET", f"/personnel/{person}/grants"))[0]["id"] == grant["id"]
    check = await ok(
        registration_client, sa, "GET", "/check", params={"personnel_id": person, "area_id": site["server"]}
    )
    assert check["decision"] == "AUTHORIZED"
    for method, path, body, params in (
        ("GET", "/areas", None, {"include_unrestricted": "true"}),
        ("GET", f"/areas/{site['server']}/grants", None, {"include_revoked": "true"}),
        ("PUT", f"/areas/{site['lobby']}", {}, None),
        ("DELETE", f"/areas/{site['network']}", None, None),
        ("POST", "/grants", {"personnel_id": person, "area_ids": [site["network"]]}, None),
        ("PUT", f"/grants/{grant['id']}", {"note": "x"}, None),
        ("POST", f"/grants/{grant['id']}/revoke", {}, None),
        ("GET", f"/grants/{grant['id']}/history", None, None),
    ):
        response = await call(registration_client, sa, method, path, body, params=params)
        assert response.status_code == 403, (method, path, response.text)
    for path in ("/areas", f"/areas/{site['server']}/grants", f"/personnel/{person}/grants"):
        assert (await call(registration_client, ga, "GET", path)).status_code == 403


async def test_access_check_decisions(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any]
) -> None:
    a = ucp["a"]
    person = site["engineer"]["id"]

    async def decision(when: datetime, area_id: str | None = None) -> str:
        result = await ok(
            registration_client,
            a,
            "GET",
            "/check",
            params={"personnel_id": person, "area_id": area_id or site["server"], "at": when.isoformat()},
        )
        return result["decision"]

    monday_10 = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # 10:00 in Karachi
    assert await decision(monday_10) == "NO_PERMISSION"
    assert await decision(monday_10, site["lobby"]) == "AREA_NOT_RESTRICTED"
    grant = (
        await ok(
            registration_client,
            a,
            "POST",
            "/grants",
            {
                "personnel_id": person,
                "area_ids": [site["server"]],
                "valid_from": "2026-10-01T00:00:00+00:00",
                "valid_until": "2026-12-01T00:00:00+00:00",
                "schedule": {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00", "timezone": "Asia/Karachi"},
            },
        )
    )[0]
    assert await decision(monday_10) == "AUTHORIZED"
    assert await decision(monday_10 + timedelta(hours=9)) == "OUTSIDE_SCHEDULE"  # 19:00 in Karachi
    assert await decision(datetime(2026, 9, 7, 5, 0, tzinfo=UTC)) == "NOT_YET_VALID"
    assert await decision(datetime(2026, 12, 7, 5, 0, tzinfo=UTC)) == "EXPIRED"
    await ok(registration_client, a, "POST", f"/grants/{grant['id']}/revoke")
    assert await decision(monday_10) == "NO_PERMISSION"
    await ok(registration_client, a, "POST", "/grants", {"personnel_id": person, "area_ids": [site["server"]]})
    await registration_client.post(f"/api/v1/personnel/{person}/deactivate", headers=headers(a))
    assert await decision(monday_10) == "PERSONNEL_INACTIVE"


async def test_deactivation_revokes_access_and_restricted_areas_keep_their_history(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    person = site["engineer"]["id"]
    await ok(registration_client, a, "POST", "/grants", {"personnel_id": person, "area_ids": [site["server"]]})
    busy = await call(registration_client, a, "DELETE", f"/areas/{site['server']}")
    assert busy.status_code == 409 and busy.json()["code"] == "AREA_HAS_ACTIVE_ACCESS"
    off = await registration_client.post(f"/api/v1/personnel/{person}/deactivate", headers=headers(a))
    assert off.status_code == 200
    grants = await ok(registration_client, a, "GET", f"/personnel/{person}/grants", params={"include_revoked": "true"})
    assert [(g["status"], g["revoke_reason"]) for g in grants] == [("REVOKED", "Personnel record deactivated.")]
    # The person stays in the directory (inactive), so history and alerts stay valid.
    listing = await registration_client.get("/api/v1/personnel", params={"status": "INACTIVE"}, headers=headers(a))
    assert [p["full_name"] for p in listing.json()["items"]] == ["Imran Engineer"]
    # A restricted area with access history cannot be deleted from Location Management.
    await ok(registration_client, a, "DELETE", f"/areas/{site['server']}")
    removed = await registration_client.delete(f"/api/v1/locations/areas/{site['server']}", headers=headers(a))
    assert removed.status_code == 409
    marked = await registration_client.delete(f"/api/v1/locations/areas/{site['network']}", headers=headers(a))
    assert marked.status_code == 409  # still marked restricted
    assert count(owner_query, "SELECT 1 FROM personnel_area_access") == 1


async def test_restricted_access_is_campus_isolated(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any], app_role_engine
) -> None:
    a, b = ucp["a"], ucp["b"]
    a_person, b_person = site["engineer"]["id"], site["b_person"]["id"]
    grant = (
        await ok(registration_client, a, "POST", "/grants", {"personnel_id": a_person, "area_ids": [site["server"]]})
    )[0]
    assert [x["area_name"] for x in await ok(registration_client, b, "GET", "/areas")] == ["B Lab"]
    for body in (
        {"personnel_id": a_person, "area_ids": [site["b_lab"]]},
        {"personnel_id": b_person, "area_ids": [site["server"]]},
    ):
        response = await call(registration_client, b, "POST", "/grants", body)
        assert response.status_code == 404, response.text
    for method, path in (
        ("GET", f"/areas/{site['server']}/grants"),
        ("GET", f"/personnel/{a_person}/grants"),
        ("PUT", f"/areas/{site['lobby']}"),
        ("POST", f"/grants/{grant['id']}/revoke"),
        ("GET", f"/grants/{grant['id']}/history"),
    ):
        assert (await call(registration_client, b, method, path, {} if method != "GET" else None)).status_code == 404
    check = await call(
        registration_client, b, "GET", "/check", params={"personnel_id": a_person, "area_id": site["server"]}
    )
    assert check.status_code == 404
    listing = "SELECT count(*) FROM app_area_access_list(NULL, NULL, NULL, true)"
    assert (await run_as(app_role_engine, a["id"], listing))[0][0][0] == 1
    assert (await run_as(app_role_engine, b["id"], listing))[0][0][0] == 0
    assert (await run_as(app_role_engine, ucp["ga"]["id"], listing))[0][0][0] == 0
    assert (await run_as(app_role_engine, ucp["sa"]["id"], "SELECT count(*) FROM personnel_area_access"))[0][0][0] == 0
    sneaky = (
        "INSERT INTO personnel_area_access (id, organization_id, campus_id, personnel_id, area_id, valid_from) "
        f"VALUES (gen_random_uuid(), '{ucp['org']}', '{ucp['campus_a']}', '{a_person}', '{site['network']}', now())"
    )
    for user in ("sa", "b"):
        with pytest.raises(DBAPIError):
            await run_as(app_role_engine, ucp[user]["id"], sneaky)
    mark = (
        f"INSERT INTO restricted_areas (area_id, organization_id, campus_id) VALUES ('{site['lobby']}', "
        f"'{ucp['org']}', '{ucp['campus_a']}')"
    )
    with pytest.raises(DBAPIError):
        await run_as(app_role_engine, ucp["sa"]["id"], mark)


async def test_the_area_access_note_is_for_admins_only(
    registration_client: AsyncClient, ucp: dict[str, Any], site: dict[str, Any]
) -> None:
    """The area's ``access_note`` is written by an Admin: an Admin note. Supervisors get the same
    restricted areas, with their current grant counts, but never the field (D-016 item 16)."""
    a, sa, sb = ucp["a"], ucp["sa"], ucp["sb"]
    person = site["engineer"]["id"]
    await ok(registration_client, a, "POST", "/grants", {"personnel_id": person, "area_ids": [site["server"]]})

    admin_view = {x["area_id"]: x for x in await ok(registration_client, a, "GET", "/areas")}
    assert admin_view[site["server"]]["access_note"] == "Authorized staff only"
    everything = await ok(registration_client, a, "GET", "/areas", params={"include_unrestricted": "true"})
    assert {x["area_name"]: x["access_note"] for x in everything} == {
        "Lobby": None,
        "Network Room": "Authorized staff only",
        "Server Room": "Authorized staff only",
    }

    response = await call(registration_client, sa, "GET", "/areas")
    assert response.status_code == 200
    assert "access_note" not in response.text and "Authorized staff only" not in response.text
    supervisor_view = {x["area_id"]: x for x in response.json()}
    assert set(supervisor_view) == {site["server"], site["network"]}
    assert set(supervisor_view[site["server"]]) == {
        "area_id", "area_name", "building_id", "building_name", "floor_id", "floor_name",
        "is_restricted", "marked_at", "active_grants",
    }
    assert supervisor_view[site["server"]]["active_grants"] == 1
    assert supervisor_view[site["server"]]["is_restricted"] is True
    # No other way to the note through this endpoint: every area is Admin-only, and so is changing one.
    for params in ({"include_unrestricted": "true"}, {"include_unrestricted": "1"}):
        assert (await call(registration_client, sa, "GET", "/areas", params=params)).status_code == 403
    change = await call(registration_client, sa, "PUT", f"/areas/{site['server']}", {"access_note": None})
    assert change.status_code == 403
    # Grants for the area show basic personnel details only, as before.
    room = await ok(registration_client, sa, "GET", f"/areas/{site['server']}/grants")
    assert "access_note" not in str(room) and room[0]["note"] is None and "email" not in room[0]["personnel"]
    # Another campus's Supervisor sees only their own campus's area, also without the note.
    other = await call(registration_client, sb, "GET", "/areas")
    assert [x["area_name"] for x in other.json()] == ["B Lab"] and "access_note" not in other.text
    assert (await call(registration_client, sb, "GET", f"/areas/{site['server']}/grants")).status_code == 404
