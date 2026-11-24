"""Section 2A: the same-organization Admin directory."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import auth_header, run_as
from tests.seed import Seed

URL = "/api/v1/organization/admin-directory"
ORG1_DIRECTORY = [
    {"full_name": "Ayesha Khan", "is_first_admin": True},
    {"full_name": "Bilal Ahmed", "is_first_admin": False},
]


@pytest.mark.parametrize("caller", ["first_admin", "admin_b"])
async def test_every_admin_in_the_org_sees_all_admin_names_with_first_admin_marked(
    client, seed: Seed, caller: str
) -> None:
    response = await client.get(URL, headers=auth_header(getattr(seed, caller)))

    assert response.status_code == 200
    # Exactly these fields: no IDs, emails, campuses, or anything else.
    assert response.json() == {"admins": ORG1_DIRECTORY}


async def test_left_admins_are_not_listed(client, seed: Seed) -> None:
    names = [a["full_name"] for a in (await client.get(URL, headers=auth_header(seed.admin_b))).json()["admins"]]
    assert "Omar Farooq" not in names


async def test_admin_sees_only_their_own_organization(client, seed: Seed) -> None:
    response = await client.get(URL, headers=auth_header(seed.org2_admin))
    assert response.json() == {
        "admins": [
            {"full_name": "Zara Sheikh", "is_first_admin": True},
            {"full_name": "Hina Malik", "is_first_admin": False},
        ]
    }


@pytest.mark.parametrize("caller", ["supervisor_a", "guard_b"])
async def test_supervisors_and_guards_cannot_see_directory(client, seed: Seed, caller: str) -> None:
    response = await client.get(URL, headers=auth_header(getattr(seed, caller)))
    assert response.status_code == 403


async def test_unauthenticated_and_inactive_callers_are_rejected(client, seed: Seed) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(URL, headers={"Authorization": "Bearer not-a-token"})).status_code == 401
    assert (
        await client.get(URL, headers=auth_header(seed.first_admin, secret="wrong-signing-key-0123456789abcdef-0000"))
    ).status_code == 401
    assert (await client.get(URL, headers=auth_header(uuid4()))).status_code == 401  # unknown user
    assert (await client.get(URL, headers=auth_header(seed.left_admin_b))).status_code == 401  # status LEFT


# --- Database-level checks (Row-Level Security / SECURITY DEFINER function) ---


async def test_database_refuses_another_organizations_directory(app_role_engine: AsyncEngine, seed: Seed) -> None:
    sql = "SELECT full_name FROM app_admin_directory(:org)"
    own, _ = await run_as(app_role_engine, seed.admin_b, sql, {"org": seed.org1})
    other, _ = await run_as(app_role_engine, seed.admin_b, sql, {"org": seed.org2})
    assert len(own) == 2
    assert other == []


async def test_database_directory_is_empty_for_non_admins(app_role_engine: AsyncEngine, seed: Seed) -> None:
    rows, _ = await run_as(
        app_role_engine, seed.supervisor_a, "SELECT * FROM app_admin_directory(:org)", {"org": seed.org1}
    )
    assert rows == []


async def test_directory_is_the_only_way_to_see_other_campus_admins(app_role_engine: AsyncEngine, seed: Seed) -> None:
    # Reading the users table directly shows nothing from campus A to a campus B Admin.
    rows, _ = await run_as(
        app_role_engine,
        seed.admin_b,
        "SELECT full_name, email FROM users WHERE campus_id = :campus",
        {"campus": seed.campus_a},
    )
    assert rows == []
