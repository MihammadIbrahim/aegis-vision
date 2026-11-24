"""Staff Management (web Phase 8; decision D-017; migration 0018): staff list with Standby/Assigned
Guards, Guard details and password set by Admins and Supervisors, deactivation and reactivation by
Admins only, audit entries, and Admin notifications about Supervisors' Guard changes."""

# ruff: noqa: F811  (imported fixtures are requested by parameter name, as pytest expects)

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from httpx import AsyncClient

from src.infrastructure.security.jwt import create_access_token
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import JWT_ALGORITHM, JWT_SECRET, auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_guards import GUARD_LOGIN, guard, guard_sign_in  # noqa: F401
from tests.integration.test_supervisor_profile import supervisor  # noqa: F401
from tests.integration.test_supervisors_and_password_reset import admin, bearer, one  # noqa: F401
from tests.seed import Seed

STAFF = "/api/v1/staff"
NOTIFICATIONS = "/api/v1/notifications?unread_only=false&limit=100"


async def staff_row(client: AsyncClient, actor: dict[str, Any], display_id: str, role: str = "GUARD") -> dict[str, Any]:
    listed = await client.get(STAFF, params={"role": role}, headers=bearer(actor["token"]))
    assert listed.status_code == 200, listed.text
    return next(m for m in listed.json() if m["display_id"] == display_id)


async def a_location(client: AsyncClient, admin: dict[str, Any]) -> dict[str, Any]:
    building = await client.post(
        "/api/v1/locations/buildings", json={"name": "Gate House", "kind": "INDOOR"}, headers=bearer(admin["token"])
    )
    assert building.status_code == 201, building.text
    return {"building_id": building.json()["id"], "floor_id": None, "area_id": None}


def audit(owner_query, target: str) -> list[tuple[str, str, dict[str, Any]]]:
    rows = owner_query(
        # Sign-in records of the same account (category AUTHENTICATION, decision D-024) are checked elsewhere.
        "SELECT action, actor_role, metadata FROM organization_audit_logs WHERE target_id = %(t)s "
        "AND category <> 'AUTHENTICATION' ORDER BY occurred_at",
        {"t": target},
    )
    return [(a, r, m if isinstance(m, dict) else json.loads(m)) for a, r, m in rows]


async def admin_notifications(client: AsyncClient, admin: dict[str, Any]) -> list[dict[str, Any]]:
    response = await client.get(NOTIFICATIONS, headers=bearer(admin["token"]))
    assert response.status_code == 200, response.text
    return [n for n in response.json() if n["subject_user_id"] is not None]


# --- Listing ---------------------------------------------------------------------------------------


async def test_admin_lists_guards_by_category_status_and_search(
    registration_client: AsyncClient, admin: dict[str, Any], guard: dict[str, Any], supervisor: dict[str, Any]
) -> None:
    row = await staff_row(registration_client, admin, guard["guard_id"])
    assert row["category"] == "STANDBY" and row["location"] is None and row["status"] == "ACTIVE"
    assert row["email"] == guard["email"] and row["phone_number"] == "+923451112233" and row["role"] == "GUARD"
    assert set(row) == {
        "id", "display_id", "role", "status", "full_name", "username", "email", "phone_number", "created_at",
        "category", "location",
    }  # fmt: skip
    where = await a_location(registration_client, admin)
    assigned = await registration_client.put(
        f"/api/v1/locations/guard-assignments/{guard['guard_id']}", json=where, headers=bearer(admin["token"])
    )
    assert assigned.status_code == 200, assigned.text

    async def ids(**params: str) -> list[str]:
        r = await registration_client.get(STAFF, params=params, headers=bearer(admin["token"]))
        assert r.status_code == 200, r.text
        return [m["display_id"] for m in r.json()]

    assert guard["guard_id"] in await ids(role="GUARD", category="STATIONED")
    assert guard["guard_id"] not in await ids(role="GUARD", category="STANDBY")
    assert await ids(role="GUARD", q="usman") == [guard["guard_id"]]
    assert await ids(role="GUARD", q=guard["guard_id"][:8]) == [guard["guard_id"]]
    assert await ids(role="GUARD", q="%") == []  # wildcards are literal
    assert await ids(role="GUARD", status="LEFT") == []
    assert supervisor["supervisor_id"] in await ids(role="SUPERVISOR")
    row = await staff_row(registration_client, admin, guard["guard_id"])
    assert row["category"] == "STATIONED" and row["location"]["building_name"] == "Gate House"
    one_row = await registration_client.get(f"{STAFF}/{row['id']}", headers=bearer(admin["token"]))
    assert one_row.status_code == 200 and one_row.json() == row


async def test_supervisors_see_guards_only_and_guards_see_nothing(
    registration_client: AsyncClient, admin: dict[str, Any], guard: dict[str, Any], supervisor: dict[str, Any]
) -> None:
    assert (await staff_row(registration_client, supervisor, guard["guard_id"]))["category"] == "STANDBY"
    refused = await registration_client.get(STAFF, params={"role": "SUPERVISOR"}, headers=bearer(supervisor["token"]))
    assert refused.status_code == 403 and refused.json()["code"] == "ADMIN_ONLY"
    sup_row = await staff_row(registration_client, admin, supervisor["supervisor_id"], "SUPERVISOR")
    other = await registration_client.get(f"{STAFF}/{sup_row['id']}", headers=bearer(supervisor["token"]))
    assert other.status_code == 403
    assert (await registration_client.get(STAFF, headers=bearer(guard["token"]))).status_code == 403
    assert (await registration_client.get(STAFF)).status_code == 401


async def test_another_organization_sees_none_of_it(
    registration_client: AsyncClient, admin: dict[str, Any], guard: dict[str, Any], seed: Seed
) -> None:
    row = await staff_row(registration_client, admin, guard["guard_id"])
    stranger = auth_header(seed.admin_b)
    assert (await registration_client.get(f"{STAFF}/{row['id']}", headers=stranger)).status_code == 404
    listed = await registration_client.get(STAFF, params={"role": "GUARD"}, headers=stranger)
    assert guard["guard_id"] not in [m["display_id"] for m in listed.json()]
    body = {"full_name": "X Y", "username": "xy.z", "phone_number": "+923000000000", "current_password": "x"}
    assert (await registration_client.put(f"{STAFF}/guards/{row['id']}", json=body, headers=stranger)).status_code in (
        403,
        404,
    )


# --- Guard details and password ---------------------------------------------------------------------


async def test_supervisor_updates_a_guard_with_own_password_audited_and_admins_notified(
    registration_client: AsyncClient,
    admin: dict[str, Any],
    guard: dict[str, Any],
    supervisor: dict[str, Any],
    owner_query,
) -> None:
    row = await staff_row(registration_client, supervisor, guard["guard_id"])
    url = f"{STAFF}/guards/{row['id']}"
    body = {"full_name": "Usman Tariq Khan", "username": "usman.k", "phone_number": "+92 345 0000000"}
    wrong = await registration_client.put(
        url, json={**body, "current_password": "Not-my-pass-1"}, headers=bearer(supervisor["token"])
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    taken = await registration_client.put(
        url,
        json={**body, "username": "maryam.s", "current_password": supervisor["password"]},
        headers=bearer(supervisor["token"]),
    )
    assert taken.status_code == 409 and taken.json()["code"] == "USERNAME_TAKEN"
    done = await registration_client.put(
        url, json={**body, "current_password": supervisor["password"]}, headers=bearer(supervisor["token"])
    )
    assert done.status_code == 200, done.text
    assert (done.json()["full_name"], done.json()["username"], done.json()["phone_number"]) == (
        "Usman Tariq Khan",
        "usman.k",
        "+923450000000",
    )
    assert supervisor["password"] not in done.text
    entries = audit(owner_query, row["id"])
    assert entries[-1][0] == "GUARD_DETAILS_UPDATED" and entries[-1][1] == "SUPERVISOR"
    assert entries[-1][2]["fields"] == ["full_name", "username", "phone"]
    assert "Usman Tariq Khan" not in json.dumps(entries[-1][2])  # field names only, never values
    notes = await admin_notifications(registration_client, admin)
    assert [n["kind"] for n in notes] == ["GUARD_UPDATED"] and notes[0]["subject_user_id"] == row["id"]
    assert supervisor["supervisor_id"] in notes[0]["title"] and guard["guard_id"] in notes[0]["title"]
    # The Admin's own change is audited but notifies nobody.
    mine = await registration_client.put(
        url,
        json={**body, "full_name": "Usman T. Khan", "current_password": admin["password"]},
        headers=bearer(admin["token"]),
    )
    assert mine.status_code == 200, mine.text
    assert audit(owner_query, row["id"])[-1][:2] == ("GUARD_DETAILS_UPDATED", "ADMIN")
    assert len(await admin_notifications(registration_client, admin)) == 1
    invalid = await registration_client.put(
        url, json={**body, "phone_number": "abc", "current_password": admin["password"]}, headers=bearer(admin["token"])
    )
    assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_PHONE_NUMBER"


async def test_setting_a_guard_password_signs_the_guard_out(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    admin: dict[str, Any],
    guard: dict[str, Any],
    supervisor: dict[str, Any],
    owner_query,
    owner_execute,
) -> None:
    row = await staff_row(registration_client, supervisor, guard["guard_id"])
    url = f"{STAFF}/guards/{row['id']}/password"
    weak = await registration_client.put(
        url,
        json={"new_password": "short", "current_password": supervisor["password"]},
        headers=bearer(supervisor["token"]),
    )
    assert weak.status_code == 422 and weak.json()["code"] == "WEAK_PASSWORD"
    # A session opened before the change (tokens carry whole seconds, so it is minted 5 s earlier, after
    # moving the Guard's last password change further back).
    owner_execute(
        "UPDATE user_credentials SET password_changed_at = now() - interval '1 minute' WHERE user_id = %(u)s",
        {"u": row["id"]},
    )
    earlier = create_access_token(
        UUID(row["id"]), secret=JWT_SECRET, algorithm=JWT_ALGORITHM, expires_minutes=30,
        now=datetime.now(UTC) - timedelta(seconds=5),
    )  # fmt: skip
    assert (await registration_client.get("/api/v1/guard/location", headers=bearer(earlier))).status_code == 200
    done = await registration_client.put(
        url,
        json={"new_password": "Fresh-guard-2027", "current_password": supervisor["password"]},
        headers=bearer(supervisor["token"]),
    )
    assert done.status_code == 204, done.text
    assert Argon2PasswordHasher().verify(
        one(owner_query, "SELECT password_hash FROM user_credentials WHERE user_id = %(u)s", u=row["id"]),
        "Fresh-guard-2027",
    )
    assert (await registration_client.get("/api/v1/guard/location", headers=bearer(earlier))).status_code == 401
    token = await guard_sign_in(registration_client, outbox, {**guard, "password": "Fresh-guard-2027"})
    assert token
    assert audit(owner_query, row["id"])[-1] == (
        "GUARD_PASSWORD_SET",
        "SUPERVISOR",
        {"role": "GUARD", "fields": ["password"]},
    )
    assert [n["kind"] for n in await admin_notifications(registration_client, admin)] == ["GUARD_PASSWORD_SET"]


# --- Deactivation and reactivation --------------------------------------------------------------------


async def test_only_admins_deactivate_and_reactivate(
    registration_client: AsyncClient,
    admin: dict[str, Any],
    guard: dict[str, Any],
    supervisor: dict[str, Any],
    owner_query,
) -> None:
    row = await staff_row(registration_client, admin, guard["guard_id"])
    await registration_client.put(
        f"/api/v1/locations/guard-assignments/{guard['guard_id']}",
        json=await a_location(registration_client, admin),
        headers=bearer(admin["token"]),
    )
    sup_says = {"current_password": supervisor["password"]}
    for action in ("deactivate", "reactivate"):
        refused = await registration_client.post(
            f"{STAFF}/{row['id']}/{action}", json=sup_says, headers=bearer(supervisor["token"])
        )
        assert refused.status_code == 403
    me = {"current_password": admin["password"]}
    left = await registration_client.post(f"{STAFF}/{row['id']}/deactivate", json=me, headers=bearer(admin["token"]))
    assert left.status_code == 200, left.text
    assert left.json()["status"] == "LEFT" and left.json()["category"] == "STANDBY"  # location removed
    assert (await registration_client.get("/api/v1/guard/location", headers=bearer(guard["token"]))).status_code == 401
    login = await registration_client.post(
        GUARD_LOGIN,
        json={
            "operational_mode": "STANDBY",
            "guard_id": guard["guard_id"],
            "email": guard["email"],
            "password": guard["password"],
        },
    )
    assert login.status_code != 200
    again = await registration_client.post(f"{STAFF}/{row['id']}/deactivate", json=me, headers=bearer(admin["token"]))
    assert again.status_code == 409 and again.json()["code"] == "STAFF_ALREADY_INACTIVE"
    frozen = await registration_client.put(
        f"{STAFF}/guards/{row['id']}",
        json={"full_name": "A B", "username": "a.b.c", "phone_number": "+923000000001", **me},
        headers=bearer(admin["token"]),
    )
    assert frozen.status_code == 403 and frozen.json()["code"] == "STAFF_INACTIVE"
    back = await registration_client.post(f"{STAFF}/{row['id']}/reactivate", json=me, headers=bearer(admin["token"]))
    assert back.status_code == 200 and back.json()["status"] == "ACTIVE"
    assert [a for a, _, _ in audit(owner_query, row["id"])][-2:] == ["STAFF_DEACTIVATED", "STAFF_REACTIVATED"]
    assert audit(owner_query, row["id"])[-2][2] == {"role": "GUARD", "status": "LEFT", "location_cleared": True}
    # A deactivated Supervisor is signed out at once.
    sup_row = await staff_row(registration_client, admin, supervisor["supervisor_id"], "SUPERVISOR")
    gone = await registration_client.post(
        f"{STAFF}/{sup_row['id']}/deactivate", json=me, headers=bearer(admin["token"])
    )
    assert gone.status_code == 200 and gone.json()["category"] is None
    assert (await registration_client.get(STAFF, headers=bearer(supervisor["token"]))).status_code == 401
    wrong = await registration_client.post(
        f"{STAFF}/{sup_row['id']}/reactivate",
        json={"current_password": "Wrong-pass-2026"},
        headers=bearer(admin["token"]),
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"


# --- Guard locations ------------------------------------------------------------------------------------


async def test_location_changes_are_audited_and_supervisor_changes_notify_admins(
    registration_client: AsyncClient,
    admin: dict[str, Any],
    guard: dict[str, Any],
    supervisor: dict[str, Any],
    owner_query,
) -> None:
    where = await a_location(registration_client, admin)
    url = f"/api/v1/locations/guard-assignments/{guard['guard_id']}"
    assert (await registration_client.put(url, json=where, headers=bearer(supervisor["token"]))).status_code == 200
    assert (await registration_client.delete(url, headers=bearer(supervisor["token"]))).status_code == 204
    assert (await registration_client.put(url, json=where, headers=bearer(admin["token"]))).status_code == 200
    row = await staff_row(registration_client, admin, guard["guard_id"])
    assert audit(owner_query, row["id"]) == [
        ("GUARD_LOCATION_ASSIGNED", "SUPERVISOR", {"role": "GUARD", "level": "BUILDING"}),
        ("GUARD_LOCATION_CLEARED", "SUPERVISOR", {"role": "GUARD", "level": None}),
        ("GUARD_LOCATION_ASSIGNED", "ADMIN", {"role": "GUARD", "level": "BUILDING"}),
    ]
    notes = await admin_notifications(registration_client, admin)
    assert [n["kind"] for n in notes] == ["GUARD_LOCATION_CHANGED", "GUARD_LOCATION_CHANGED"]
    assert {n["subject_user_id"] for n in notes} == {row["id"]}
    # Supervisors receive none of these notifications.
    sup_notes = (await registration_client.get(NOTIFICATIONS, headers=bearer(supervisor["token"]))).json()
    assert [n for n in sup_notes if n["subject_user_id"]] == []
