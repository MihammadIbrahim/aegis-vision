"""Guard operational mode, Standby vs Stationed / Standalone (decision D-018; migration 0019): new Guards
start as Standby; sign-in and forgot password name the mode and the server checks it against the
assignment; any change to the assignment ends the Guard's open sessions at once."""

# ruff: noqa: F811  (imported fixtures are requested by parameter name, as pytest expects)

from typing import Any

from httpx import AsyncClient

from tests.fakes import FakeEmailSender
from tests.integration.test_guards import (
    GUARD_EMAIL,
    GUARD_LOGIN,
    create_guard,
    guard_sign_in,
)
from tests.integration.test_supervisor_profile import supervisor  # noqa: F401
from tests.integration.test_supervisors_and_password_reset import (  # noqa: F401
    RESET,
    admin,
    bearer,
    one,
)

API = "/api/v1"


async def building(client: AsyncClient, admin: dict[str, Any], name: str) -> str:
    created = await client.post(
        f"{API}/locations/buildings", json={"name": name, "kind": "INDOOR"}, headers=bearer(admin["token"])
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def at(building_id: str) -> dict[str, Any]:
    return {"building_id": building_id, "floor_id": None, "area_id": None}


async def assign(client: AsyncClient, actor: dict[str, Any], guard_id: str, where: dict[str, Any] | None) -> None:
    url = f"{API}/locations/guard-assignments/{guard_id}"
    response = await (
        client.put(url, json=where, headers=bearer(actor["token"]))
        if where
        else client.delete(url, headers=bearer(actor["token"]))
    )
    assert response.status_code in (200, 204), response.text


async def dashboard(client: AsyncClient, token: str):  # type: ignore[no-untyped-def]
    return await client.get(f"{API}/guard/dashboard", headers=bearer(token))


async def test_new_guards_start_as_standby_and_sign_in_only_in_standby_mode(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    guard = await create_guard(registration_client, outbox, admin)
    assert one(owner_query, "SELECT count(*) FROM guard_location_assignments") == 0
    login = {"guard_id": guard["guard_id"], "email": GUARD_EMAIL, "password": guard["password"]}
    wrong = await registration_client.post(GUARD_LOGIN, json={**login, "operational_mode": "STATIONED"})
    assert wrong.status_code == 409 and wrong.json()["code"] == "OPERATIONAL_MODE_MISMATCH"
    assert "Standby" in wrong.json()["detail"]
    # The mode is checked only after the right password: a wrong password never reveals the assignment.
    bad = await registration_client.post(
        GUARD_LOGIN, json={**login, "password": "Wrong-pass-2026", "operational_mode": "STATIONED"}
    )
    assert bad.status_code == 401 and bad.json()["code"] == "INVALID_CREDENTIALS"
    missing = await registration_client.post(GUARD_LOGIN, json=login)
    assert missing.status_code == 422
    token = await guard_sign_in(registration_client, outbox, guard, "STANDBY")
    shown = (await dashboard(registration_client, token)).json()
    assert (shown["operational_mode"], shown["dashboard"]) == ("STANDBY", "STANDBY_EMERGENCY")


async def test_assigning_a_post_signs_the_guard_out_and_only_stationed_mode_works(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], supervisor: dict[str, Any]
) -> None:
    guard = await create_guard(registration_client, outbox, admin)
    token = await guard_sign_in(registration_client, outbox, guard, "STANDBY")
    assert (await dashboard(registration_client, token)).status_code == 200
    gate = await building(registration_client, admin, "Gate House")
    await assign(registration_client, supervisor, guard["guard_id"], at(gate))  # Standby -> Stationed

    ended = await dashboard(registration_client, token)
    assert ended.status_code == 401 and ended.json()["code"] == "ASSIGNMENT_CHANGED"
    standby = await registration_client.post(
        GUARD_LOGIN,
        json={
            "operational_mode": "STANDBY",
            "guard_id": guard["guard_id"],
            "email": GUARD_EMAIL,
            "password": guard["password"],
        },
    )
    assert standby.status_code == 409 and "Stationed" in standby.json()["detail"]
    stationed = await guard_sign_in(registration_client, outbox, guard, "STATIONED")
    shown = (await dashboard(registration_client, stationed)).json()
    assert (shown["operational_mode"], shown["dashboard"]) == ("STATIONED", "STATIONED_MONITORING")
    # Staff sessions are untouched.
    assert (await registration_client.get(f"{API}/staff", headers=bearer(supervisor["token"]))).status_code == 200

    # Setting the same post again changes nothing, so the session stays.
    await assign(registration_client, admin, guard["guard_id"], at(gate))
    assert (await dashboard(registration_client, stationed)).status_code == 200
    # Moving the post ends it (still Stationed, but a different post).
    store = await building(registration_client, admin, "Store")
    await assign(registration_client, admin, guard["guard_id"], at(store))
    assert (await dashboard(registration_client, stationed)).json()["code"] == "ASSIGNMENT_CHANGED"

    # Stationed -> Standby: the location is cleared and the session ends again.
    again = await guard_sign_in(registration_client, outbox, guard, "STATIONED")
    await assign(registration_client, supervisor, guard["guard_id"], None)
    assert (await dashboard(registration_client, again)).status_code == 401
    back = await guard_sign_in(registration_client, outbox, guard, "STANDBY")
    assert (await dashboard(registration_client, back)).json()["dashboard"] == "STANDBY_EMERGENCY"


async def test_the_mode_is_checked_again_when_the_code_is_verified(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    guard = await create_guard(registration_client, outbox, admin)
    started = await registration_client.post(
        GUARD_LOGIN,
        json={
            "operational_mode": "STANDBY",
            "guard_id": guard["guard_id"],
            "email": GUARD_EMAIL,
            "password": guard["password"],
        },
    )
    assert started.status_code == 200
    # The Guard is given a post between the two steps.
    await assign(registration_client, admin, guard["guard_id"], at(await building(registration_client, admin, "Lab")))
    done = await registration_client.post(
        f"{GUARD_LOGIN}/{started.json()['login_id']}/verify",
        json={"otp": outbox.last_code(GUARD_EMAIL), "operational_mode": "STANDBY"},
    )
    assert done.status_code == 409 and done.json()["code"] == "OPERATIONAL_MODE_MISMATCH"
    assert "access_token" not in done.text


async def test_deactivation_also_ends_the_session(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    guard = await create_guard(registration_client, outbox, admin)
    await assign(registration_client, admin, guard["guard_id"], at(await building(registration_client, admin, "Yard")))
    token = await guard_sign_in(registration_client, outbox, guard, "STATIONED")
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=guard["guard_id"])
    left = await registration_client.post(
        f"{API}/staff/{user_id}/deactivate",
        json={"current_password": admin["password"]},
        headers=bearer(admin["token"]),
    )
    assert left.status_code == 200 and left.json()["category"] == "STANDBY"
    assert (await dashboard(registration_client, token)).status_code == 401


async def test_forgot_password_needs_the_mode_that_matches_the_assignment(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    guard = await create_guard(registration_client, outbox, admin)
    body = {"guard_id": guard["guard_id"], "email": GUARD_EMAIL}
    assert (await registration_client.post(RESET, json=body)).status_code == 422  # mode required with a Guard ID
    with_org = {"organization_id": admin["organization_id"], "email": admin["email"], "operational_mode": "STANDBY"}
    assert (await registration_client.post(RESET, json=with_org)).status_code == 422  # only with a Guard ID
    await assign(registration_client, admin, guard["guard_id"], at(await building(registration_client, admin, "Dock")))
    sent = len(outbox.messages)
    mismatch = await registration_client.post(RESET, json={**body, "operational_mode": "STANDBY"})
    assert mismatch.status_code == 202 and len(outbox.messages) == sent  # same answer, no code
    match = await registration_client.post(RESET, json={**body, "operational_mode": "STATIONED"})
    assert match.status_code == 202 and set(match.json()) == set(mismatch.json())
    assert len(outbox.messages) == sent + 1 and outbox.messages[-1].to_email == GUARD_EMAIL
