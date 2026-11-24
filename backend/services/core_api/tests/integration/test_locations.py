"""Building, Floor & Area Management (migration 0013): hierarchy rules, the Admin/Supervisor role
matrix, campus isolation (API and database), safe deletion, Guard and camera locations, and
campus deletion / export. Uses the three-campus organization ``ucp`` of the deletion tests."""

# ruff: noqa: F811  (the imported ``ucp`` fixture is used as a test parameter)

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import DatabaseUrls, run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import register_admin
from tests.integration.test_ownership_and_deletion import (
    OTHER_PASSWORD,
    add_user,
    count,
    delete_account,
    headers,
    one,
    start_deletion,
    traces,
    ucp,  # noqa: F401  (a fixture, used by name)
)

LOC = "/api/v1/locations"


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{LOC}{path}", json=body, headers=headers(user))


async def ok(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None) -> Any:
    response = await call(client, user, method, path, body)
    assert response.status_code in (200, 201, 204), (method, path, response.text)
    return response.json() if response.content else None


async def building(client: AsyncClient, admin: dict[str, Any], name: str, kind: str = "INDOOR") -> str:
    return (await ok(client, admin, "POST", "/buildings", {"name": name, "kind": kind}))["id"]


async def floor(
    client: AsyncClient, user: dict[str, Any], building_id: str, name: str, level: int | None = None
) -> str:
    return (await ok(client, user, "POST", f"/buildings/{building_id}/floors", {"name": name, "level": level}))["id"]


async def area(client: AsyncClient, user: dict[str, Any], building_id: str, name: str, **extra: Any) -> str:
    return (await ok(client, user, "POST", f"/buildings/{building_id}/areas", {"name": name, **extra}))["id"]


def camera_of(owner_query, campus: UUID) -> UUID:
    return one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=campus)


# --- Hierarchy and the role matrix -----------------------------------------------------------------


async def test_admin_builds_the_hierarchy_and_supervisor_views_it(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    block = await building(registration_client, a, "Admin Block")
    perimeter = await building(registration_client, a, "North Perimeter", "OUTDOOR_PERIMETER")
    ground = await floor(registration_client, a, block, "Ground Floor", 0)
    first = await floor(registration_client, sa, block, "First Floor", 1)  # Supervisors add floors too
    server_room = await area(registration_client, a, block, "Server Room", floor_id=first)
    await area(registration_client, sa, block, "Reception", floor_id=ground)
    await area(registration_client, sa, block, "Roof Access")  # an indoor area need not be on a floor
    gate = await area(
        registration_client,
        sa,
        perimeter,
        "Main Gate",
        boundary_direction="NORTH",
        boundary_side="OUTSIDE",
    )
    await area(registration_client, a, perimeter, "Car Park", boundary_direction="CUSTOM", boundary_name="Riverside")

    for viewer in (a, sa):
        tree = await ok(registration_client, viewer, "GET", "")
        assert [(b["name"], b["kind"]) for b in tree] == [
            ("Admin Block", "INDOOR"),
            ("North Perimeter", "OUTDOOR_PERIMETER"),
        ]
        block_node, perimeter_node = tree
        assert [(f["name"], f["level"]) for f in block_node["floors"]] == [("Ground Floor", 0), ("First Floor", 1)]
        assert [x["name"] for x in block_node["floors"][1]["areas"]] == ["Server Room"]
        assert [x["name"] for x in block_node["areas"]] == ["Roof Access"]
        assert perimeter_node["floors"] == []
        gate_node = next(x for x in perimeter_node["areas"] if x["id"] == gate)
        assert (gate_node["boundary_direction"], gate_node["boundary_name"], gate_node["boundary_side"]) == (
            "NORTH",
            None,
            "OUTSIDE",
        )
        assert next(x for x in perimeter_node["areas"] if x["name"] == "Car Park")["boundary_name"] == "Riverside"

    # Admins rename and retype buildings; renaming keeps every ID.
    renamed = await ok(
        registration_client, a, "PUT", f"/buildings/{block}", {"name": "Admin Block A", "kind": "INDOOR"}
    )
    assert renamed == {"id": block, "name": "Admin Block A", "kind": "INDOOR"}
    # Supervisors edit and remove floors and areas.
    edited = await ok(
        registration_client,
        sa,
        "PUT",
        f"/areas/{server_room}",
        {"name": "Server Room 1", "floor_id": ground, "boundary_side": "INSIDE"},
    )
    assert (edited["floor_id"], edited["boundary_side"]) == (ground, "INSIDE")
    assert (await ok(registration_client, sa, "PUT", f"/floors/{first}", {"name": "Level 1", "level": 1}))[
        "name"
    ] == "Level 1"
    await ok(registration_client, sa, "DELETE", f"/floors/{first}")
    await ok(registration_client, sa, "DELETE", f"/areas/{server_room}")
    # Admins remove an empty building.
    empty = await building(registration_client, a, "Old Store")
    await ok(registration_client, a, "DELETE", f"/buildings/{empty}")
    assert "Old Store" not in str(await ok(registration_client, a, "GET", ""))


async def test_supervisors_cannot_create_change_or_remove_buildings(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    block = await building(registration_client, a, "Library")
    attempts = [
        ("POST", "/buildings", {"name": "Sup Block", "kind": "INDOOR"}),
        ("PUT", f"/buildings/{block}", {"name": "Renamed", "kind": "INDOOR"}),
        ("PUT", f"/buildings/{block}", {"name": "Library", "kind": "OUTDOOR_PERIMETER"}),
        ("DELETE", f"/buildings/{block}", None),
    ]
    for method, path, body in attempts:
        response = await call(registration_client, sa, method, path, body)
        assert response.status_code == 403 and response.json()["code"] == "ADMIN_ONLY", (method, path)
    assert one(owner_query, "SELECT name, kind FROM buildings WHERE id = %(b)s", b=block) == ("Library", "INDOOR")
    assert count(owner_query, "SELECT 1 FROM buildings") == 1


async def test_guards_have_no_access_to_location_management(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    block = await building(registration_client, ucp["a"], "Hall")
    for method, path, body in [
        ("GET", "", None),
        ("POST", "/buildings", {"name": "G", "kind": "INDOOR"}),
        ("POST", f"/buildings/{block}/floors", {"name": "F"}),
        ("POST", f"/buildings/{block}/areas", {"name": "A"}),
        ("GET", "/guard-assignments", None),
        ("PUT", f"/guard-assignments/{ucp['ga']['display_id']}", {"building_id": block}),
    ]:
        assert (await call(registration_client, ucp["ga"], method, path, body)).status_code == 403, (method, path)


# --- Hierarchy rules ---------------------------------------------------------------------------------


async def test_floors_belong_only_to_indoor_buildings(
    registration_client: AsyncClient, ucp: dict[str, Any], app_role_engine
) -> None:
    a = ucp["a"]
    perimeter = await building(registration_client, a, "East Fence", "OUTDOOR_PERIMETER")
    refused = await call(registration_client, a, "POST", f"/buildings/{perimeter}/floors", {"name": "Ground"})
    assert refused.status_code == 422 and refused.json()["code"] == "FLOORS_NOT_ALLOWED"
    await area(registration_client, a, perimeter, "Fence Line")  # perimeters need no floor

    block = await building(registration_client, a, "Lab")
    await floor(registration_client, a, block, "Ground")
    to_perimeter = await call(
        registration_client, a, "PUT", f"/buildings/{block}", {"name": "Lab", "kind": "OUTDOOR_PERIMETER"}
    )
    assert to_perimeter.status_code == 409 and to_perimeter.json()["code"] == "BUILDING_HAS_FLOORS"
    # A perimeter without floors can become a building and the reverse.
    assert (
        await ok(registration_client, a, "PUT", f"/buildings/{perimeter}", {"name": "East Wing", "kind": "INDOOR"})
    )["kind"] == "INDOOR"

    # The database refuses a floor under a perimeter even without the API.
    other = await building(registration_client, a, "West Fence", "OUTDOOR_PERIMETER")
    with pytest.raises(DBAPIError, match="fk_floors_building"):
        await run_as(
            app_role_engine,
            a["id"],
            "INSERT INTO floors (id, organization_id, campus_id, building_id, name) "
            f"VALUES (gen_random_uuid(), app_actor_org_id(), app_actor_campus_id(), '{other}', 'Sneaky')",
        )


async def test_invalid_hierarchy_links_are_refused(
    registration_client: AsyncClient, ucp: dict[str, Any], app_role_engine
) -> None:
    a = ucp["a"]
    one_block, two_block = await building(registration_client, a, "One"), await building(registration_client, a, "Two")
    two_floor = await floor(registration_client, a, two_block, "Two Ground")
    wrong_floor = await call(
        registration_client, a, "POST", f"/buildings/{one_block}/areas", {"name": "X", "floor_id": two_floor}
    )
    assert wrong_floor.status_code == 422 and wrong_floor.json()["code"] == "INVALID_LOCATION"
    unknown = await call(
        registration_client, a, "POST", f"/buildings/{one_block}/areas", {"name": "X", "floor_id": str(uuid4())}
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "LOCATION_NOT_FOUND"
    for body, code in [
        ({"name": "Y", "boundary_direction": "CUSTOM"}, "INVALID_BOUNDARY"),
        ({"name": "Y", "boundary_direction": "NORTH", "boundary_name": "Gate"}, "INVALID_BOUNDARY"),
        ({"name": "  "}, "INVALID_NAME"),
    ]:
        response = await call(registration_client, a, "POST", f"/buildings/{one_block}/areas", body)
        assert response.status_code == 422 and response.json()["code"] == code, body
    bad = await call(
        registration_client, a, "POST", f"/buildings/{one_block}/areas", {"name": "Z", "boundary_side": "UP"}
    )
    assert bad.status_code == 422
    level = await call(registration_client, a, "POST", f"/buildings/{one_block}/floors", {"name": "Sky", "level": 999})
    assert level.status_code == 422 and level.json()["code"] == "INVALID_FLOOR_LEVEL"
    # The database refuses an area whose floor is in another building.
    with pytest.raises(DBAPIError, match="fk_areas_floor"):
        await run_as(
            app_role_engine,
            a["id"],
            "INSERT INTO areas (id, organization_id, campus_id, building_id, floor_id, name) VALUES "
            f"(gen_random_uuid(), app_actor_org_id(), app_actor_campus_id(), '{one_block}', '{two_floor}', 'X')",
        )


async def test_names_are_unique_within_their_parent_ignoring_case(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a, b = ucp["a"], ucp["b"]
    block = await building(registration_client, a, "Science Block")
    dup = await call(
        registration_client, a, "POST", "/buildings", {"name": "science block", "kind": "OUTDOOR_PERIMETER"}
    )
    assert dup.status_code == 409 and dup.json()["code"] == "LOCATION_NAME_TAKEN"
    await building(registration_client, b, "Science Block")  # another campus may use the same name
    other = await building(registration_client, a, "Arts Block")
    ground = await floor(registration_client, a, block, "Ground", 0)
    await floor(registration_client, a, other, "Ground", 0)  # same floor name and level in another building
    same_floor = await call(registration_client, a, "POST", f"/buildings/{block}/floors", {"name": "GROUND"})
    assert same_floor.status_code == 409 and same_floor.json()["code"] == "LOCATION_NAME_TAKEN"
    same_level = await call(registration_client, a, "POST", f"/buildings/{block}/floors", {"name": "Lobby", "level": 0})
    assert same_level.status_code == 409 and same_level.json()["code"] == "FLOOR_LEVEL_TAKEN"
    await area(registration_client, a, block, "Lobby", floor_id=ground)
    await area(registration_client, a, block, "Lobby")  # same name, not on that floor
    same_area = await call(
        registration_client, a, "POST", f"/buildings/{block}/areas", {"name": "lobby", "floor_id": ground}
    )
    assert same_area.status_code == 409 and same_area.json()["code"] == "LOCATION_NAME_TAKEN"
    rename = await call(
        registration_client, a, "PUT", f"/buildings/{other}", {"name": "SCIENCE BLOCK", "kind": "INDOOR"}
    )
    assert rename.status_code == 409


# --- Campus isolation --------------------------------------------------------------------------------


async def test_another_campus_cannot_reach_these_locations(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a, b, sb = ucp["a"], ucp["b"], ucp["sb"]
    block = await building(registration_client, a, "Main Hall")
    ground = await floor(registration_client, a, block, "Ground")
    lobby = await area(registration_client, a, block, "Lobby", floor_id=ground)
    b_block = await building(registration_client, b, "B Hall")

    # The Main Admin (A) sees only Main Campus; B sees only Campus B.
    assert [x["name"] for x in await ok(registration_client, a, "GET", "")] == ["Main Hall"]
    for intruder in (b, sb):
        assert [x["name"] for x in await ok(registration_client, intruder, "GET", "")] == (
            ["B Hall"] if intruder is b else ["B Hall"]
        )
        for method, path, body in [
            ("PUT", f"/floors/{ground}", {"name": "Hacked"}),
            ("DELETE", f"/floors/{ground}", None),
            ("POST", f"/buildings/{block}/floors", {"name": "F"}),
            ("POST", f"/buildings/{block}/areas", {"name": "A"}),
            ("PUT", f"/areas/{lobby}", {"name": "Hacked"}),
            ("DELETE", f"/areas/{lobby}", None),
            ("GET", f"/buildings/{block}/linked", None),
            ("GET", f"/floors/{ground}/linked", None),
            ("GET", f"/areas/{lobby}/linked", None),
            ("PUT", f"/guard-assignments/{ucp['gb']['display_id']}", {"building_id": block}),
            ("PUT", f"/camera-links/{camera_of(owner_query, ucp['campus_b'])}", {"building_id": block}),
        ]:
            response = await call(registration_client, intruder, method, path, body)
            assert response.status_code == 404, (intruder["name"], method, path, response.text)
    for method, path, body in [
        ("PUT", f"/buildings/{block}", {"name": "Hacked", "kind": "INDOOR"}),
        ("DELETE", f"/buildings/{block}", None),
    ]:
        assert (await call(registration_client, b, method, path, body)).status_code == 404
    # Another campus's Guards and cameras cannot be linked from here.
    assert (
        await call(
            registration_client, a, "PUT", f"/guard-assignments/{ucp['gb']['display_id']}", {"building_id": block}
        )
    ).status_code == 404
    camera_b = camera_of(owner_query, ucp["campus_b"])
    assert (
        await call(registration_client, a, "PUT", f"/camera-links/{camera_b}", {"building_id": block})
    ).status_code == 404
    assert (
        await call(
            registration_client,
            a,
            "PUT",
            f"/camera-links/{camera_of(owner_query, ucp['campus_a'])}",
            {"building_id": b_block},
        )
    ).status_code == 404
    assert one(owner_query, "SELECT name FROM floors WHERE id = %(f)s", f=ground) == "Ground"
    assert count(owner_query, "SELECT 1 FROM guard_location_assignments") == 0


async def test_the_database_enforces_campus_and_role_rules(
    registration_client: AsyncClient, ucp: dict[str, Any], app_role_engine
) -> None:
    a, b, sa, ga = ucp["a"], ucp["b"], ucp["sa"], ucp["ga"]
    block = await building(registration_client, a, "Tower")
    await floor(registration_client, a, block, "Ground")
    # Supervisors cannot write buildings, even directly.
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            sa["id"],
            "INSERT INTO buildings (id, organization_id, campus_id, kind, name) "
            "VALUES (gen_random_uuid(), app_actor_org_id(), app_actor_campus_id(), 'INDOOR', 'Sneaky')",
        )
    _, changed = await run_as(app_role_engine, sa["id"], "UPDATE buildings SET name = 'Hacked'")
    assert changed == 0
    _, removed = await run_as(app_role_engine, sa["id"], "DELETE FROM buildings")
    assert removed == 0
    rows, _ = await run_as(app_role_engine, sa["id"], "SELECT name FROM buildings")
    assert rows == [("Tower",)]  # but they can read them
    # Guards and other campuses see nothing; nobody can write into another campus.
    for actor in (ga["id"], b["id"], None):
        for table in ("buildings", "floors", "areas", "guard_location_assignments"):
            rows, _ = await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
            assert rows == [], (actor, table)
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            b["id"],
            "INSERT INTO floors (id, organization_id, campus_id, building_id, name) "
            f"SELECT gen_random_uuid(), organization_id, '{ucp['campus_a']}', '{block}', 'Sneaky' FROM users "
            f"WHERE id = '{b['id']}'",
        )
    rows, _ = await run_as(app_role_engine, ga["id"], "SELECT * FROM app_guard_location()")
    assert rows == []


# --- Safe deletion ---------------------------------------------------------------------------------------


async def test_a_location_in_use_cannot_be_removed(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    block = await building(registration_client, a, "Annex")
    ground = await floor(registration_client, a, block, "Ground")
    lobby = await area(registration_client, a, block, "Lobby", floor_id=ground)
    camera = camera_of(owner_query, ucp["campus_a"])
    await ok(
        registration_client,
        sa,
        "PUT",
        f"/camera-links/{camera}",
        {"building_id": block, "floor_id": ground, "area_id": lobby},
    )
    await ok(
        registration_client,
        sa,
        "PUT",
        f"/guard-assignments/{ucp['ga']['display_id']}",
        {"building_id": block, "floor_id": ground, "area_id": lobby},
    )

    for user, path, expected in [
        (a, f"/buildings/{block}", "1 floor(s), 1 area(s)"),
        (sa, f"/floors/{ground}", "1 area(s)"),
        (sa, f"/areas/{lobby}", "1 camera(s), 1 Guard(s)"),
    ]:
        response = await call(registration_client, user, "DELETE", path)
        assert response.status_code == 409 and response.json()["code"] == "LOCATION_NOT_EMPTY", path
        assert expected in response.json()["detail"]
    assert count(owner_query, "SELECT 1 FROM areas WHERE id = %(a)s", a=lobby) == 1
    assert count(owner_query, "SELECT 1 FROM cameras WHERE id = %(c)s AND area_id = %(a)s", c=camera, a=lobby) == 1

    # Once nothing points at it, it can go, from the bottom up. Nothing else is deleted with it.
    await ok(registration_client, sa, "DELETE", f"/camera-links/{camera}")
    await ok(registration_client, sa, "DELETE", f"/guard-assignments/{ucp['ga']['display_id']}")
    await ok(registration_client, sa, "DELETE", f"/areas/{lobby}")
    await ok(registration_client, sa, "DELETE", f"/floors/{ground}")
    await ok(registration_client, a, "DELETE", f"/buildings/{block}")
    assert count(owner_query, "SELECT 1 FROM cameras WHERE id = %(c)s", c=camera) == 1
    assert count(owner_query, "SELECT 1 FROM users WHERE id = %(g)s", g=ucp["ga"]["id"]) == 1


async def test_the_database_blocks_deleting_a_location_in_use(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query, app_role_engine
) -> None:
    """The same rule without the API's check: no cascade, the foreign key refuses."""
    a = ucp["a"]
    block = await building(registration_client, a, "Depot")
    camera = camera_of(owner_query, ucp["campus_a"])
    await ok(registration_client, a, "PUT", f"/camera-links/{camera}", {"building_id": block})
    with pytest.raises(DBAPIError, match="fk_cameras_building"):
        await run_as(app_role_engine, a["id"], f"DELETE FROM buildings WHERE id = '{block}'")
    assert count(owner_query, "SELECT 1 FROM buildings WHERE id = %(b)s", b=block) == 1


# --- Guard and camera locations ------------------------------------------------------------------------


async def test_guard_locations_are_optional_and_set_by_admins_or_supervisors(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a, sa, ga = ucp["a"], ucp["sa"], ucp["ga"]
    block = await building(registration_client, a, "Hostel")
    ground = await floor(registration_client, a, block, "Ground")
    gate = await area(registration_client, a, block, "Gate", floor_id=ground, boundary_side="INSIDE")
    loose = await area(registration_client, a, block, "Yard")

    # Not assigned: the Guard sees that, and nothing is assumed.
    assert (await registration_client.get("/api/v1/guard/location", headers=headers(ga))).json() == {
        "assigned": False,
        "building_name": None,
        "building_kind": None,
        "floor_name": None,
        "floor_level": None,
        "area_name": None,
        "boundary_direction": None,
        "boundary_name": None,
        "boundary_side": None,
    }
    for user, selection, level in [
        (a, {"building_id": block}, "BUILDING"),
        (sa, {"building_id": block, "floor_id": ground}, "FLOOR"),
        (sa, {"building_id": block, "floor_id": ground, "area_id": gate}, "AREA"),
    ]:
        assigned = await ok(registration_client, user, "PUT", f"/guard-assignments/{ga['display_id']}", selection)
        assert assigned["guard_id"] == ga["display_id"] and assigned["location"]["level"] == level
    mine = (await registration_client.get("/api/v1/guard/location", headers=headers(ga))).json()
    assert (mine["assigned"], mine["building_name"], mine["floor_name"], mine["area_name"], mine["boundary_side"]) == (
        True,
        "Hostel",
        "Ground",
        "Gate",
        "INSIDE",
    )
    listed = await ok(registration_client, sa, "GET", "/guard-assignments")
    assert [(g["guard_id"], g["location"]["area_name"]) for g in listed] == [(ga["display_id"], "Gate")]
    tree = await ok(registration_client, a, "GET", "")
    assert tree[0]["guard_count"] == 1 and tree[0]["floors"][0]["areas"][0]["guard_count"] == 1
    linked = await ok(registration_client, a, "GET", f"/floors/{ground}/linked")
    assert [g["guard_id"] for g in linked["guards"]] == [ga["display_id"]]

    # The dropdowns must agree: the area must be on the chosen floor.
    for selection in (
        {"building_id": block, "area_id": gate},
        {"building_id": block, "floor_id": ground, "area_id": loose},
    ):
        bad = await call(registration_client, a, "PUT", f"/guard-assignments/{ga['display_id']}", selection)
        assert bad.status_code == 422 and bad.json()["code"] == "INVALID_LOCATION"
    # Only Guards can be assigned; the Guard cannot change it.
    assert (
        await call(registration_client, a, "PUT", f"/guard-assignments/{sa['display_id']}", {"building_id": block})
    ).status_code == 404
    assert (await call(registration_client, ga, "DELETE", f"/guard-assignments/{ga['display_id']}")).status_code == 403
    await ok(registration_client, sa, "DELETE", f"/guard-assignments/{ga['display_id']}")
    assert (await registration_client.get("/api/v1/guard/location", headers=headers(ga))).json()["assigned"] is False
    again = await call(registration_client, sa, "DELETE", f"/guard-assignments/{ga['display_id']}")
    assert again.status_code == 404 and again.json()["code"] == "GUARD_NOT_ASSIGNED"


async def test_cameras_are_linked_to_one_place(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    perimeter = await building(registration_client, a, "South Fence", "OUTDOOR_PERIMETER")
    gate = await area(registration_client, a, perimeter, "South Gate", boundary_direction="SOUTH")
    camera = camera_of(owner_query, ucp["campus_a"])
    linked = await ok(
        registration_client, sa, "PUT", f"/camera-links/{camera}", {"building_id": perimeter, "area_id": gate}
    )
    assert linked["location"] | {} == {
        "level": "AREA",
        "building_id": perimeter,
        "building_name": "South Fence",
        "building_kind": "OUTDOOR_PERIMETER",
        "floor_id": None,
        "floor_name": None,
        "area_id": gate,
        "area_name": "South Gate",
    }
    assert (
        one(owner_query, "SELECT num_nonnulls(building_id, floor_id, area_id) FROM cameras WHERE id = %(c)s", c=camera)
        == 1
    )
    assert [c["camera_id"] for c in await ok(registration_client, a, "GET", "/camera-links")] == [str(camera)]
    assert (await ok(registration_client, a, "GET", f"/buildings/{perimeter}/linked"))["cameras"][0][
        "display_name"
    ] == "Gate"
    await ok(registration_client, a, "DELETE", f"/camera-links/{camera}")
    assert await ok(registration_client, a, "GET", "/camera-links") == []
    assert (
        await call(registration_client, a, "PUT", f"/camera-links/{uuid4()}", {"building_id": perimeter})
    ).status_code == 404


# --- Campus deletion and export -------------------------------------------------------------------------------


async def _furnish(
    client: AsyncClient, ucp: dict[str, Any], owner_query, who: str, campus: str, guard: str
) -> dict[str, str]:
    admin = ucp[who]
    block = await building(client, admin, f"{campus} Block")
    ground = await floor(client, admin, block, f"{campus} Ground", 0)
    lobby = await area(client, admin, block, f"{campus} Lobby", floor_id=ground, boundary_direction="EAST")
    await ok(
        client,
        admin,
        "PUT",
        f"/guard-assignments/{ucp[guard]['display_id']}",
        {"building_id": block, "floor_id": ground, "area_id": lobby},
    )
    camera = camera_of(owner_query, ucp[f"campus_{who}"])
    await ok(client, admin, "PUT", f"/camera-links/{camera}", {"building_id": block, "floor_id": ground})
    return {"building": block, "floor": ground, "area": lobby}


async def test_export_contains_the_campus_locations_only(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    b_ids = await _furnish(registration_client, ucp, owner_query, "b", "B", "gb")
    await _furnish(registration_client, ucp, owner_query, "a", "A", "ga")
    started = (await start_deletion(registration_client, ucp["b"])).json()
    assert "buildings_floors_areas" not in started["export"]["not_included"]
    export = (await registration_client.get(started["export"]["download_url"], headers=headers(ucp["b"]))).json()
    assert export["locations"] == [
        {
            "name": "B Block",
            "kind": "INDOOR",
            "created_at": export["locations"][0]["created_at"],
            "floors": [{"name": "B Ground", "level": 0}],
            "areas": [
                {
                    "name": "B Lobby",
                    "floor": "B Ground",
                    "boundary_direction": "EAST",
                    "boundary_name": None,
                    "boundary_side": None,
                }
            ],
        }
    ]
    assert export["guard_locations"] == [
        {
            "user_id": ucp["gb"]["display_id"],
            "location": {"building": "B Block", "floor": "B Ground", "area": "B Lobby"},
        }
    ]
    assert export["camera_locations"] == [
        {"display_name": "Gate", "location": {"building": "B Block", "floor": "B Ground", "area": None}}
    ]
    text = str(export)
    assert "A Block" not in text and "A Lobby" not in text
    for internal in b_ids.values():
        assert internal not in text


async def test_campus_deletion_erases_its_locations_and_keeps_other_campuses(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    ucp: dict[str, Any],
    owner_query,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "training_dataset"
    dataset.mkdir()
    (dataset / "sample.jpg").write_bytes(b"training sample")
    a_ids = await _furnish(registration_client, ucp, owner_query, "a", "A", "ga")
    b_ids = await _furnish(registration_client, ucp, owner_query, "b", "B", "gb")

    assert (await delete_account(registration_client, outbox, ucp["b"]))["campus_deleted"] is True
    for value in (*b_ids.values(), str(ucp["campus_b"]), "B Block", "B Lobby"):
        assert traces(owner_query, value) == [], value
    # Main Campus keeps its whole hierarchy and links.
    tree = await ok(registration_client, ucp["a"], "GET", "")
    assert [(x["id"], x["camera_count"], x["guard_count"]) for x in tree] == [(a_ids["building"], 1, 1)]
    assert count(owner_query, "SELECT 1 FROM guard_location_assignments WHERE area_id = %(a)s", a=a_ids["area"]) == 1
    assert (dataset / "sample.jpg").read_bytes() == b"training sample"


async def test_deleting_the_last_campus_erases_its_locations_with_the_organization(
    registration_client: AsyncClient, outbox: FakeEmailSender, migrated_database: DatabaseUrls, owner_query
) -> None:
    solo = await register_admin(registration_client, outbox, organization_name="Solo Locations")
    admin_id, org, campus = one(
        owner_query, "SELECT id, organization_id, campus_id FROM users WHERE display_id = %(d)s", d=solo["display_id"]
    )
    admin = {**solo, "id": admin_id}
    with psycopg.connect(migrated_database.owner_test) as conn:
        guard = add_user(conn, org, campus, "GUARD", "Solo Guard", "sg@solo.example.com", password=OTHER_PASSWORD)
    block = await building(registration_client, admin, "Solo Block")
    gate = await area(registration_client, admin, block, "Solo Gate")
    await ok(
        registration_client,
        admin,
        "PUT",
        f"/guard-assignments/{guard['display_id']}",
        {"building_id": block, "area_id": gate},
    )
    assert (await delete_account(registration_client, outbox, admin))["organization_deleted"] is True
    for value in (block, gate, str(org), str(campus), "Solo Block"):
        assert traces(owner_query, value) == [], value
    assert one(owner_query, "SELECT organization_name FROM deleted_organization_names") == "Solo Locations"
