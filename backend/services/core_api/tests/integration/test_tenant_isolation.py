"""Section 2B: campus-level operational isolation (cameras as the example table)."""

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import auth_header, run_as
from tests.seed import Seed


@pytest.mark.parametrize("caller", ["first_admin", "supervisor_a"])
async def test_campus_a_users_list_only_campus_a_cameras(client, seed: Seed, caller: str) -> None:
    response = await client.get("/api/v1/cameras", headers=auth_header(getattr(seed, caller)))
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [str(seed.camera_a)]


async def test_known_id_of_another_campus_camera_returns_404(client, seed: Seed) -> None:
    headers = auth_header(seed.first_admin)  # even the First Admin
    assert (await client.get(f"/api/v1/cameras/{seed.camera_b}", headers=headers)).status_code == 404  # same org
    assert (await client.get(f"/api/v1/cameras/{seed.camera_c}", headers=headers)).status_code == 404  # other org
    assert (await client.get(f"/api/v1/cameras/{seed.camera_a}", headers=headers)).status_code == 200


async def test_guards_cannot_list_cameras(client, seed: Seed) -> None:
    assert (await client.get("/api/v1/cameras", headers=auth_header(seed.guard_b))).status_code == 403


# --- Database-level checks: RLS holds even when a query forgets its WHERE clause ---


async def test_unfiltered_queries_only_return_own_campus_rows(app_role_engine: AsyncEngine, seed: Seed) -> None:
    actor = seed.first_admin
    cameras, _ = await run_as(app_role_engine, actor, "SELECT id FROM cameras")
    users, _ = await run_as(app_role_engine, actor, "SELECT id FROM users")
    campuses, _ = await run_as(app_role_engine, actor, "SELECT id FROM campuses")
    orgs, _ = await run_as(app_role_engine, actor, "SELECT id FROM organizations")

    assert {r.id for r in cameras} == {seed.camera_a}
    assert {r.id for r in users} == {seed.first_admin, seed.supervisor_a}
    assert {r.id for r in campuses} == {seed.campus_a}
    assert {r.id for r in orgs} == {seed.org1}


async def test_cannot_update_another_campus_camera(app_role_engine: AsyncEngine, seed: Seed, owner_query) -> None:
    _, rowcount = await run_as(
        app_role_engine,
        seed.first_admin,
        "UPDATE cameras SET display_name = 'hijacked' WHERE id = :id",
        {"id": seed.camera_b},
    )
    assert rowcount == 0
    assert owner_query("SELECT display_name FROM cameras WHERE id = %(id)s", {"id": seed.camera_b}) == [
        ("B - Library",)
    ]


async def test_cannot_insert_a_camera_into_another_campus(app_role_engine: AsyncEngine, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            seed.first_admin,
            "INSERT INTO cameras (id, organization_id, campus_id, display_name)"
            " VALUES (gen_random_uuid(), :org, :campus, 'x')",
            {"org": seed.org1, "campus": seed.campus_b},
        )


async def test_no_actor_means_no_rows(app_role_engine: AsyncEngine, seed: Seed) -> None:
    for table in ("cameras", "users", "campuses", "organizations", "campus_registration_requests"):
        rows, _ = await run_as(app_role_engine, None, f"SELECT 1 FROM {table}")
        assert rows == [], table


async def test_left_users_lose_all_campus_access(app_role_engine: AsyncEngine, seed: Seed) -> None:
    rows, _ = await run_as(app_role_engine, seed.left_admin_b, "SELECT id FROM cameras")
    assert rows == []
