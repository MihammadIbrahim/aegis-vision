"""Camera Management (migration 0014, decision D-012): full camera management for Admins and
Supervisors in their own campus, no camera changes for Guards, indoor and outdoor locations,
validation, duplicates, campus isolation (API and database), and the export."""

# ruff: noqa: F811  (the imported ``ucp`` fixture is used as a test parameter)

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_locations import area, building, floor
from tests.integration.test_ownership_and_deletion import (
    count,
    headers,
    one,
    start_deletion,
    ucp,  # noqa: F401  (a fixture, used by name)
)

CAMERAS = "/api/v1/cameras"


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str = "", body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{CAMERAS}{path}", json=body, headers=headers(user))


async def ok(client: AsyncClient, user: dict[str, Any], method: str, path: str = "", body: Any = None) -> Any:
    response = await call(client, user, method, path, body)
    assert response.status_code in (200, 201, 204), (method, path, response.text)
    return response.json() if response.content else None


def body(
    name: str, url: str | None = None, kind: str = "RTSP", location: dict[str, str] | None = None
) -> dict[str, Any]:
    return {"display_name": name, "connection_type": kind, "stream_url": url, "location": location}


@pytest.fixture
async def sites(registration_client: AsyncClient, ucp: dict[str, Any]) -> dict[str, str]:
    """Main Campus: an indoor block (a floor, an area on it, an area with no floor) and an outdoor
    perimeter with an area. Campus B: one building with an area."""
    a, b = ucp["a"], ucp["b"]
    block = await building(registration_client, a, "Admin Block")
    ground = await floor(registration_client, a, block, "Ground", 0)
    lobby = await area(registration_client, a, block, "Lobby", floor_id=ground)
    roof = await area(registration_client, a, block, "Roof")
    fence = await building(registration_client, a, "North Fence", "OUTDOOR_PERIMETER")
    gate = await area(registration_client, a, fence, "North Gate", boundary_direction="NORTH", boundary_side="OUTSIDE")
    b_block = await building(registration_client, b, "B Block")
    b_area = await area(registration_client, b, b_block, "B Hall")
    return {
        "block": block,
        "ground": ground,
        "lobby": lobby,
        "roof": roof,
        "fence": fence,
        "gate": gate,
        "b_block": b_block,
        "b_area": b_area,
    }


# --- Full camera management for Admins and Supervisors -----------------------------------------------


@pytest.mark.parametrize("who", ["a", "sa"])
async def test_admins_and_supervisors_fully_manage_cameras(
    who: str,
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
    owner_query,
) -> None:
    user = ucp[who]
    created = await ok(
        registration_client,
        user,
        "POST",
        body=body(
            "Roof Cam",
            "rtsp://10.0.0.21:554/stream1",
            location={"building_id": sites["block"], "area_id": sites["roof"]},
        ),
    )
    camera_id = created["id"]
    assert created["display_name"] == "Roof Cam" and created["connection_type"] == "RTSP"
    assert created["stream_url"] == "rtsp://10.0.0.21:554/stream1" and created["environment"] == "INDOOR"
    assert created["location"]["level"] == "AREA" and created["location"]["area_name"] == "Roof"
    assert created["location"]["floor_id"] is None  # an indoor area with no floor

    assert await ok(registration_client, user, "GET", f"/{camera_id}") == created
    assert camera_id in [c["id"] for c in await ok(registration_client, user, "GET")]

    updated = await ok(
        registration_client,
        user,
        "PUT",
        f"/{camera_id}",
        body(
            "Lobby Cam",
            "https://cam-lobby.local/onvif/device_service",
            "ONVIF",
            {"building_id": sites["block"], "floor_id": sites["ground"], "area_id": sites["lobby"]},
        ),
    )
    assert (updated["display_name"], updated["connection_type"]) == ("Lobby Cam", "ONVIF")
    assert (updated["location"]["floor_name"], updated["location"]["area_name"]) == ("Ground", "Lobby")
    assert updated["updated_at"] >= created["updated_at"]

    cleared = await ok(registration_client, user, "PUT", f"/{camera_id}", body("Lobby Cam", kind="ONVIF"))
    assert cleared["location"] is None and cleared["environment"] is None and cleared["stream_url"] is None

    await ok(registration_client, user, "DELETE", f"/{camera_id}")
    assert (await call(registration_client, user, "GET", f"/{camera_id}")).status_code == 404
    assert (await call(registration_client, user, "DELETE", f"/{camera_id}")).status_code == 404
    assert count(owner_query, "SELECT 1 FROM cameras WHERE id = %(c)s", c=camera_id) == 0


async def test_a_camera_can_be_registered_before_it_is_connected(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
) -> None:
    created = await ok(registration_client, ucp["sa"], "POST", body=body("Spare Camera"))
    assert (created["stream_url"], created["location"], created["environment"]) == (None, None, None)


async def test_supervisors_still_cannot_manage_buildings(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
) -> None:
    sa = ucp["sa"]
    for method, path, payload in [
        ("POST", "/api/v1/locations/buildings", {"name": "Sup Block", "kind": "INDOOR"}),
        ("POST", "/api/v1/locations/buildings", {"name": "Sup Fence", "kind": "OUTDOOR_PERIMETER"}),
        ("PUT", f"/api/v1/locations/buildings/{sites['fence']}", {"name": "X", "kind": "OUTDOOR_PERIMETER"}),
        ("DELETE", f"/api/v1/locations/buildings/{sites['fence']}", None),
    ]:
        response = await registration_client.request(method, path, json=payload, headers=headers(sa))
        assert response.status_code == 403 and response.json()["code"] == "ADMIN_ONLY", (method, path)


# --- Guards ----------------------------------------------------------------------------------------------


async def test_guards_cannot_manage_cameras(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    owner_query,
    app_role_engine,
) -> None:
    ga = ucp["ga"]
    camera = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=ucp["campus_a"])
    for method, path, payload in [
        ("GET", "", None),
        ("GET", f"/{camera}", None),
        ("POST", "", body("Guard Cam")),
        ("PUT", f"/{camera}", body("Hacked")),
        ("DELETE", f"/{camera}", None),
    ]:
        assert (await call(registration_client, ga, method, path, payload)).status_code == 403, (method, path)
    # The database refuses Guards' camera writes too (they still read their campus, as before).
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            ga["id"],
            "INSERT INTO cameras (id, organization_id, campus_id, display_name) "
            "VALUES (gen_random_uuid(), app_actor_org_id(), app_actor_campus_id(), 'Guard Cam')",
        )
    _, changed = await run_as(app_role_engine, ga["id"], "UPDATE cameras SET display_name = 'Hacked'")
    _, removed = await run_as(app_role_engine, ga["id"], "DELETE FROM cameras")
    assert (changed, removed) == (0, 0)
    rows, _ = await run_as(app_role_engine, ga["id"], "SELECT display_name FROM cameras")
    assert rows == [("Gate",)]


# --- Validation and duplicates ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        (body("  "), 422, "INVALID_NAME"),
        (body("x" * 121), 422, "INVALID_NAME"),
        (body("Cam", "http://10.0.0.5/stream"), 422, "INVALID_STREAM_URL"),  # RTSP needs rtsp://
        (body("Cam", "rtsp://10.0.0.5/stream", "ONVIF"), 422, "INVALID_STREAM_URL"),  # ONVIF needs http(s)://
        (body("Cam", "rtsp://admin:secret@10.0.0.5/stream"), 422, "STREAM_CREDENTIALS_NOT_ALLOWED"),
        (body("Cam", "rtsp://admin@10.0.0.5/stream"), 422, "STREAM_CREDENTIALS_NOT_ALLOWED"),
        (body("Cam", "rtsp://10.0.0.5:99999/stream"), 422, "INVALID_STREAM_URL"),
        (body("Cam", "rtsp://bad_host!/stream"), 422, "INVALID_STREAM_URL"),
        (body("Cam", "rtsp://10.0.0.5/my stream"), 422, "INVALID_STREAM_URL"),
        (body("Cam", "rtsp://"), 422, "INVALID_STREAM_URL"),
        (body("Cam", kind="HTTP"), 422, None),
        ({**body("Cam"), "password": "secret"}, 422, None),
    ],
)
async def test_invalid_camera_details_are_refused(
    payload: dict[str, Any],
    status: int,
    code: str | None,
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    owner_query,
) -> None:
    before = count(owner_query, "SELECT 1 FROM cameras")
    response = await call(registration_client, ucp["a"], "POST", body=payload)
    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code
    assert "secret" not in response.text
    assert count(owner_query, "SELECT 1 FROM cameras") == before


async def test_the_database_refuses_credentials_in_a_stream_url(ucp: dict[str, Any], app_role_engine) -> None:
    with pytest.raises(DBAPIError, match="ck_cameras_stream_url"):
        await run_as(
            app_role_engine,
            ucp["a"]["id"],
            "INSERT INTO cameras (id, organization_id, campus_id, display_name, stream_url) VALUES "
            "(gen_random_uuid(), app_actor_org_id(), app_actor_campus_id(), 'X', 'rtsp://u:p@10.0.0.9/s')",
        )


async def test_names_and_stream_urls_are_unique_per_campus(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
) -> None:
    a, sa, b = ucp["a"], ucp["sa"], ucp["b"]
    first = await ok(registration_client, a, "POST", body=body("Entrance", "rtsp://10.1.1.1/live"))
    same_name = await call(registration_client, sa, "POST", body=body("ENTRANCE"))
    assert same_name.status_code == 409 and same_name.json()["code"] == "CAMERA_NAME_TAKEN"
    same_stream = await call(registration_client, sa, "POST", body=body("Entrance 2", "rtsp://10.1.1.1/live"))
    assert same_stream.status_code == 409 and same_stream.json()["code"] == "CAMERA_STREAM_TAKEN"
    # Another campus may use the same name and address (a different site and network).
    await ok(registration_client, b, "POST", body=body("Entrance", "rtsp://10.1.1.1/live"))
    second = await ok(registration_client, a, "POST", body=body("Exit"))
    rename = await call(registration_client, a, "PUT", f"/{second['id']}", body("entrance"))
    assert rename.status_code == 409 and rename.json()["code"] == "CAMERA_NAME_TAKEN"
    # Keeping its own name is fine.
    assert (await ok(registration_client, a, "PUT", f"/{first['id']}", body("Entrance", "rtsp://10.1.1.1/live")))[
        "id"
    ] == first["id"]


# --- Indoor and outdoor locations ---------------------------------------------------------------------------


async def test_indoor_and_outdoor_locations(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
) -> None:
    sa = ucp["sa"]
    outdoor = await ok(
        registration_client,
        sa,
        "POST",
        body=body("Gate Cam", location={"building_id": sites["fence"], "area_id": sites["gate"]}),
    )
    assert outdoor["environment"] == "OUTDOOR"
    assert (
        outdoor["location"]["building_kind"],
        outdoor["location"]["floor_id"],
        outdoor["location"]["area_name"],
    ) == (
        "OUTDOOR_PERIMETER",
        None,
        "North Gate",
    )
    under_floor = await ok(
        registration_client,
        sa,
        "POST",
        body=body(
            "Lobby Cam",
            location={"building_id": sites["block"], "floor_id": sites["ground"], "area_id": sites["lobby"]},
        ),
    )
    assert under_floor["environment"] == "INDOOR" and under_floor["location"]["floor_name"] == "Ground"
    no_floor = await ok(
        registration_client,
        sa,
        "POST",
        body=body("Roof Cam", location={"building_id": sites["block"], "area_id": sites["roof"]}),
    )
    assert no_floor["location"]["floor_id"] is None and no_floor["location"]["area_name"] == "Roof"

    # Filters: at or below a location; text search by name.
    def names(cameras: list[dict[str, Any]]) -> list[str]:
        return sorted(c["display_name"] for c in cameras)

    assert names(await ok(registration_client, sa, "GET", f"?building_id={sites['block']}")) == [
        "Lobby Cam",
        "Roof Cam",
    ]
    assert names(await ok(registration_client, sa, "GET", f"?floor_id={sites['ground']}")) == ["Lobby Cam"]
    assert names(await ok(registration_client, sa, "GET", f"?area_id={sites['gate']}")) == ["Gate Cam"]
    assert names(await ok(registration_client, sa, "GET", "?q=roof")) == ["Roof Cam"]
    assert names(await ok(registration_client, sa, "GET", f"?q={outdoor['id'][:8]}")) == ["Gate Cam"]


@pytest.mark.parametrize(
    ("selection", "status", "code"),
    [
        ({"building": "block", "area": "lobby"}, 422, "INVALID_LOCATION"),  # the area is on a floor not selected
        ({"building": "block", "floor": "ground", "area": "roof"}, 422, "INVALID_LOCATION"),  # area not on that floor
        ({"building": "fence", "area": "lobby"}, 422, "INVALID_LOCATION"),  # area of another building
        ({"building": "fence", "floor": "ground", "area": "gate"}, 422, "INVALID_LOCATION"),  # floors are not outdoor
        ({"building": "b_block", "area": "b_area"}, 404, "LOCATION_NOT_FOUND"),  # another campus
        ({"building": "block", "area": "b_area"}, 404, "LOCATION_NOT_FOUND"),  # another campus's area
        ({"building": "unknown"}, 404, "LOCATION_NOT_FOUND"),
    ],
)
async def test_invalid_locations_are_refused(
    selection: dict[str, str],
    status: int,
    code: str,
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
    owner_query,
) -> None:
    ids = {**sites, "unknown": str(uuid4())}
    location = {f"{level}_id": ids[name] for level, name in selection.items()}
    before = count(owner_query, "SELECT 1 FROM cameras")
    response = await call(registration_client, ucp["a"], "POST", body=body("Odd Cam", location=location))
    assert response.status_code == status and response.json()["code"] == code, response.text
    assert count(owner_query, "SELECT 1 FROM cameras") == before


# --- Campus isolation ----------------------------------------------------------------------------------------


async def test_another_campus_cannot_reach_these_cameras(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
    owner_query,
) -> None:
    a = ucp["a"]
    camera = await ok(registration_client, a, "POST", body=body("Private Cam", "rtsp://10.9.9.9/live"))
    for intruder in (ucp["b"], ucp["sb"]):
        listed = await ok(registration_client, intruder, "GET")
        assert camera["id"] not in str(listed) and "Private Cam" not in str(listed)
        for method, path, payload in [
            ("GET", f"/{camera['id']}", None),
            ("PUT", f"/{camera['id']}", body("Hijacked")),
            ("DELETE", f"/{camera['id']}", None),
        ]:
            assert (await call(registration_client, intruder, method, path, payload)).status_code == 404, (method, path)
        assert (await ok(registration_client, intruder, "GET", "?q=10.9.9.9")) == []
    assert one(owner_query, "SELECT display_name FROM cameras WHERE id = %(c)s", c=camera["id"]) == "Private Cam"

    # The Main Admin cannot reach Campus B's cameras either.
    b_camera = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=ucp["campus_b"])
    assert (await call(registration_client, a, "GET", f"/{b_camera}")).status_code == 404
    assert str(b_camera) not in str(await ok(registration_client, a, "GET"))
    # A location ID from another campus does not move a camera there.
    moved = await call(
        registration_client,
        a,
        "PUT",
        f"/{camera['id']}",
        body("Private Cam", location={"building_id": sites["b_block"]}),
    )
    assert moved.status_code == 404
    assert one(owner_query, "SELECT building_id FROM cameras WHERE id = %(c)s", c=camera["id"]) is None


async def test_the_database_keeps_cameras_inside_one_campus(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
    owner_query,
    app_role_engine,
) -> None:
    a_camera = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=ucp["campus_a"])
    _, changed = await run_as(
        app_role_engine, ucp["sb"]["id"], f"UPDATE cameras SET display_name = 'Hijacked' WHERE id = '{a_camera}'"
    )
    assert changed == 0
    # A camera cannot point at another campus's location, even directly.
    with pytest.raises(DBAPIError, match="fk_cameras_building"):
        await run_as(app_role_engine, ucp["a"]["id"], f"UPDATE cameras SET building_id = '{sites['b_block']}'")


# --- Locations, deletion and export --------------------------------------------------------------------------


async def test_a_location_with_cameras_is_kept_until_they_move(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    sites: dict[str, str],
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    camera = await ok(
        registration_client,
        sa,
        "POST",
        body=body("Gate Cam", location={"building_id": sites["fence"], "area_id": sites["gate"]}),
    )
    blocked = await registration_client.delete(f"/api/v1/locations/areas/{sites['gate']}", headers=headers(sa))
    assert blocked.status_code == 409 and blocked.json()["code"] == "LOCATION_NOT_EMPTY"
    await ok(registration_client, sa, "DELETE", f"/{camera['id']}")  # removing the camera frees the area
    assert (
        await registration_client.delete(f"/api/v1/locations/areas/{sites['gate']}", headers=headers(sa))
    ).status_code == 204
    assert (
        await registration_client.delete(f"/api/v1/locations/buildings/{sites['fence']}", headers=headers(a))
    ).status_code == 204


async def test_export_lists_camera_details(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    ucp: dict[str, Any],
    sites: dict[str, str],
) -> None:
    b = ucp["b"]
    await ok(
        registration_client,
        b,
        "POST",
        body=body(
            "B Hall Cam",
            "rtsp://192.168.5.10/live",
            location={"building_id": sites["b_block"], "area_id": sites["b_area"]},
        ),
    )
    started = (await start_deletion(registration_client, b)).json()
    export = (await registration_client.get(started["export"]["download_url"], headers=headers(b))).json()
    entry = next(c for c in export["cameras"] if c["display_name"] == "B Hall Cam")
    assert (entry["connection_type"], entry["stream_url"]) == ("RTSP", "rtsp://192.168.5.10/live")
    assert {
        "display_name": "B Hall Cam",
        "location": {"building": "B Block", "floor": None, "area": "B Hall"},
    } in export["camera_locations"]
    assert "Admin Block" not in str(export)
