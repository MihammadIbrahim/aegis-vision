"""Section 2C: only the First Admin reviews new campus Admin registration requests. Approval needs the
Main Admin's password + emailed code (decision H) and rejection their password (decision D-024); here the
confirmation is prepared directly in the database (the full flow is in test_campus_request_approvals.py)."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import auth_header, run_as
from tests.seed import Seed

REQUESTS = "/api/v1/campus-requests"
MAIN_ADMIN_PASSWORD = "Main-admin-pass-2026"


def confirmed(owner_execute, user_id: UUID) -> dict[str, str]:
    """A verified, unused confirmation of ``user_id`` (what /campus-requests/authorization/{id}/verify gives)."""
    grant = uuid4()
    owner_execute(
        "INSERT INTO admin_step_up_grants (id, user_id, expires_at, verified_at) "
        "VALUES (%(g)s, %(u)s, now() + interval '10 minutes', now())",
        {"g": grant, "u": user_id},
    )
    return {"authorization_id": str(grant)}


@pytest.fixture
def main_admin_password(owner_execute, seed: Seed) -> dict[str, str]:
    owner_execute(
        "INSERT INTO user_credentials (user_id, password_hash) VALUES (%(u)s, %(h)s)",
        {"u": seed.first_admin, "h": Argon2PasswordHasher().hash(MAIN_ADMIN_PASSWORD)},
    )
    return {"current_password": MAIN_ADMIN_PASSWORD}


async def test_first_admin_lists_only_own_org_pending_requests(client, seed: Seed) -> None:
    response = await client.get(REQUESTS, headers=auth_header(seed.first_admin))
    assert response.status_code == 200
    assert {r["id"] for r in response.json()} == {str(seed.request_verified), str(seed.request_unverified)}
    assert set(response.json()[0]) == {
        "id",
        "proposed_campus_name",
        "applicant_name",
        "applicant_email",
        "email_verified",
        "status",
        "submitted_at",
        "reviewed_at",
        # Migration 0021: given by applicants of "Already Registered Organization?"
        "applicant_username",
        "applicant_phone",
        "email_verified_at",
        "decision_deadline",
    }


@pytest.mark.parametrize("caller", ["admin_b", "supervisor_a", "guard_b"])
async def test_nobody_else_can_list_approve_or_reject(
    client, seed: Seed, owner_query, owner_execute, caller: str
) -> None:
    user = getattr(seed, caller)
    headers = auth_header(user)
    assert (await client.get(REQUESTS, headers=headers)).status_code == 403
    hist = await client.get(f"{REQUESTS}/history", headers=headers)
    assert hist.status_code == 403, hist.text
    body = {"current_password": "anything"}
    assert (await client.post(f"{REQUESTS}/authorization", json=body, headers=headers)).status_code == 403
    approve = await client.post(
        f"{REQUESTS}/{seed.request_verified}/approve", json=confirmed(owner_execute, user), headers=headers
    )
    assert approve.status_code == 403
    assert (
        await client.post(f"{REQUESTS}/{seed.request_verified}/reject", json=body, headers=headers)
    ).status_code == 403
    # Without a body the answer is still 403, not a validation error.
    assert (await client.post(f"{REQUESTS}/{seed.request_verified}/approve", headers=headers)).status_code == 403
    assert owner_query(
        "SELECT status FROM campus_registration_requests WHERE id = %(id)s", {"id": seed.request_verified}
    ) == [("PENDING",)]


async def test_first_admin_approval_creates_campus_and_admin(client, seed: Seed, owner_query, owner_execute) -> None:
    response = await client.post(
        f"{REQUESTS}/{seed.request_verified}/approve",
        json=confirmed(owner_execute, seed.first_admin),
        headers=auth_header(seed.first_admin),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"
    [(campus_id, status, reviewed_by)] = owner_query(
        "SELECT approved_campus_id, status, reviewed_by FROM campus_registration_requests WHERE id = %(id)s",
        {"id": seed.request_verified},
    )
    assert status == "APPROVED" and reviewed_by == seed.first_admin
    assert owner_query("SELECT organization_id, name FROM campuses WHERE id = %(id)s", {"id": campus_id}) == [
        (seed.org1, "North Campus")
    ]
    [(role, is_first, display_id)] = owner_query(
        "SELECT role, is_first_admin, display_id FROM users WHERE campus_id = %(id)s", {"id": campus_id}
    )
    assert role == "ADMIN" and is_first is False and display_id.startswith("ADM-")


async def test_approval_does_not_give_first_admin_access_to_new_campus(
    client, seed: Seed, app_role_engine: AsyncEngine, owner_execute
) -> None:
    approved = await client.post(
        f"{REQUESTS}/{seed.request_verified}/approve",
        json=confirmed(owner_execute, seed.first_admin),
        headers=auth_header(seed.first_admin),
    )
    assert approved.status_code == 200, approved.text

    campuses, _ = await run_as(app_role_engine, seed.first_admin, "SELECT id FROM campuses")
    users, _ = await run_as(app_role_engine, seed.first_admin, "SELECT full_name FROM users")
    assert {r.id for r in campuses} == {seed.campus_a}
    assert "Hamza Raza" not in {r.full_name for r in users}

    # ...but the new Admin's name now appears in the shared directory.
    directory = (await client.get("/api/v1/organization/admin-directory", headers=auth_header(seed.admin_b))).json()
    assert {"full_name": "Hamza Raza", "is_first_admin": False} in directory["admins"]


async def test_cannot_approve_unverified_or_already_decided_request(
    client, seed: Seed, owner_execute, main_admin_password: dict[str, str]
) -> None:
    headers = auth_header(seed.first_admin)

    async def approve(request_id: UUID) -> int:
        body = confirmed(owner_execute, seed.first_admin)
        return (await client.post(f"{REQUESTS}/{request_id}/approve", json=body, headers=headers)).status_code

    assert await approve(seed.request_unverified) == 409
    assert await approve(seed.request_verified) == 200
    assert await approve(seed.request_verified) == 409
    path = f"{REQUESTS}/{seed.request_verified}/reject"
    assert (await client.post(path, json=main_admin_password, headers=headers)).status_code == 409


async def test_first_admin_rejects_request_without_creating_anything(
    client, seed: Seed, owner_query, main_admin_password: dict[str, str]
) -> None:
    response = await client.post(
        f"{REQUESTS}/{seed.request_unverified}/reject", json=main_admin_password, headers=auth_header(seed.first_admin)
    )
    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    assert owner_query("SELECT count(*) FROM campuses WHERE name = 'South Campus'") == [(0,)]


async def test_first_admin_cannot_review_another_organizations_request(
    client, seed: Seed, owner_query, owner_execute, main_admin_password: dict[str, str]
) -> None:
    headers = auth_header(seed.first_admin)
    approve = await client.post(
        f"{REQUESTS}/{seed.request_org2}/approve", json=confirmed(owner_execute, seed.first_admin), headers=headers
    )
    assert approve.status_code == 404
    assert (
        await client.post(f"{REQUESTS}/{seed.request_org2}/reject", json=main_admin_password, headers=headers)
    ).status_code == 404
    assert owner_query(
        "SELECT status FROM campus_registration_requests WHERE id = %(id)s", {"id": seed.request_org2}
    ) == [("PENDING",)]


# --- Database-level checks ---


async def test_database_hides_requests_from_other_admins(app_role_engine: AsyncEngine, seed: Seed) -> None:
    rows, _ = await run_as(app_role_engine, seed.admin_b, "SELECT id FROM campus_registration_requests")
    _, updated = await run_as(
        app_role_engine,
        seed.admin_b,
        "UPDATE campus_registration_requests SET status = 'APPROVED' WHERE id = :id",
        {"id": seed.request_verified},
    )
    assert rows == [] and updated == 0


async def test_database_blocks_other_admins_from_creating_campuses_or_cross_campus_admins(
    app_role_engine: AsyncEngine, seed: Seed
) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            seed.admin_b,
            "INSERT INTO campuses (id, organization_id, name, status)"
            " VALUES (gen_random_uuid(), :org, 'Rogue', 'ACTIVE')",
            {"org": seed.org1},
        )
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            seed.admin_b,
            """INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id)
               VALUES (gen_random_uuid(), :org, :campus, 'ADMIN', 'ACTIVE', 'Rogue', 'rogue@x.test',
                       'ADM-99999999999')""",
            {"org": seed.org1, "campus": seed.campus_a},
        )


async def test_nobody_can_create_a_second_first_admin(app_role_engine: AsyncEngine, seed: Seed) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        await run_as(
            app_role_engine,
            seed.first_admin,
            """INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id,
                                   is_first_admin)
               VALUES (gen_random_uuid(), :org, :campus, 'ADMIN', 'ACTIVE', 'Rogue', 'rogue@x.test',
                       'ADM-99999999998', true)""",
            {"org": seed.org1, "campus": seed.campus_a},
        )
