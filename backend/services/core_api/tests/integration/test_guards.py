"""Guards: creation by Admins (Supervisors until decision D-017), Guard sign-in, forgot password with Guard ID,
Guard profile changes (migration 0009)."""

# ruff: noqa: F811  (imported fixtures are requested by parameter name, as pytest expects)

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from src.infrastructure.security.jwt import create_access_token
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import (
    JWT_ALGORITHM,
    JWT_SECRET,
    auth_header,
    platform_auth_header,
    run_as,
)
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import (
    LOGIN,
    register_admin,
    sign_in,
)
from tests.integration.test_supervisor_profile import supervisor  # noqa: F401
from tests.integration.test_supervisors_and_password_reset import (  # noqa: F401
    RESET,
    SUPERVISORS,
    admin,
    bearer,
    one,
)
from tests.seed import Seed

GUARDS = "/api/v1/guards"
GUARD_LOGIN = "/api/v1/auth/guard/login"
GUARD_APP = "/api/v1/guard"
GUARD_ID = re.compile(r"[A-Z]{6}[0-9]{21}")
GUARD_EMAIL = "usman.guard@lakeside.example.com"
GUARD_PASSWORD = "Guard-pass-2026"
INVALID = {
    "detail": "The organization details, email address or password are incorrect.",
    "code": "INVALID_CREDENTIALS",
}


def guard_form(authorization_id: str, **changes: str) -> dict[str, str]:
    return {
        "authorization_id": authorization_id,
        "full_name": "Usman Tariq",
        "username": "usman.guard",
        "email": GUARD_EMAIL,
        "phone_number": "+92 345 1112233",
        "password": GUARD_PASSWORD,
        **changes,
    }


async def authorize(client: AsyncClient, outbox: FakeEmailSender, actor: dict[str, Any]) -> str:
    started = await client.post(
        f"{GUARDS}/authorization", json={"current_password": actor["password"]}, headers=bearer(actor["token"])
    )
    assert started.status_code == 200, started.text
    grant = started.json()["id"]
    done = await client.post(
        f"{GUARDS}/authorization/{grant}/verify",
        json={"otp": outbox.last_code(actor["email"])},
        headers=bearer(actor["token"]),
    )
    assert done.status_code == 200, done.text
    return grant


async def create_guard(
    client: AsyncClient, outbox: FakeEmailSender, actor: dict[str, Any], **changes: str
) -> dict[str, Any]:
    form = guard_form(await authorize(client, outbox, actor), **changes)
    submitted = await client.post(GUARDS, json=form, headers=bearer(actor["token"]))
    assert submitted.status_code == 201, submitted.text
    done = await client.post(
        f"{GUARDS}/creations/{submitted.json()['id']}/verify",
        json={"otp": outbox.last_code(form["email"])},
        headers=bearer(actor["token"]),
    )
    assert done.status_code == 201, done.text
    return {**done.json(), "password": form["password"]}


async def guard_sign_in(
    client: AsyncClient, outbox: FakeEmailSender, guard: dict[str, Any], mode: str = "STANDBY"
) -> str:
    """Sign in through the Guard app's steps; ``mode`` must match the Guard's assignment (D-018)."""
    login = {"operational_mode": mode, "guard_id": guard["guard_id"], "email": guard["email"]}
    started = await client.post(GUARD_LOGIN, json={**login, "password": guard["password"]})
    assert started.status_code == 200, started.text
    done = await client.post(
        f"{GUARD_LOGIN}/{started.json()['login_id']}/verify",
        json={"otp": outbox.last_code(guard["email"]), "operational_mode": mode},
    )
    assert done.status_code == 200, done.text
    return done.json()["access_token"]


@pytest.fixture
async def guard(registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]) -> dict[str, Any]:
    created = await create_guard(registration_client, outbox, admin)
    return {**created, "token": await guard_sign_in(registration_client, outbox, created)}


def stored(owner_query, guard_id: str) -> tuple[Any, ...]:
    return one(owner_query, "SELECT full_name, username, email, phone FROM users WHERE display_id = %(d)s", d=guard_id)


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


# --- Creation ---------------------------------------------------------------------------------------


async def test_admin_creates_a_guard_after_both_codes(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    grant = await authorize(registration_client, outbox, admin)
    submitted = await registration_client.post(GUARDS, json=guard_form(grant), headers=bearer(admin["token"]))
    assert submitted.status_code == 201 and outbox.messages[-1].to_email == GUARD_EMAIL
    assert one(owner_query, "SELECT count(*) FROM users WHERE email = %(e)s", e=GUARD_EMAIL) == 0  # not yet
    code = outbox.last_code(GUARD_EMAIL)
    done = await registration_client.post(
        f"{GUARDS}/creations/{submitted.json()['id']}/verify", json={"otp": code}, headers=bearer(admin["token"])
    )
    assert done.status_code == 201, done.text
    data = done.json()
    assert GUARD_ID.fullmatch(data["guard_id"]) and len(data["guard_id"]) == 27
    assert data == {
        "guard_id": data["guard_id"],
        "full_name": "Usman Tariq",
        "username": "usman.guard",
        "email": GUARD_EMAIL,
        "phone_number": "+923451112233",
        "role": "GUARD",
        "organization_id": admin["organization_id"],
        "campus_name": "Main Campus",
        "created_at": data["created_at"],
    }
    assert GUARD_PASSWORD not in done.text and code not in done.text
    role, org, campus, user_id = one(
        owner_query,
        "SELECT role, organization_id, campus_id, id FROM users WHERE display_id = %(d)s",
        d=data["guard_id"],
    )
    admin_org, admin_campus = one(
        owner_query, "SELECT organization_id, campus_id FROM users WHERE display_id = %(d)s", d=admin["display_id"]
    )
    assert (role, org, campus) == ("GUARD", admin_org, admin_campus)
    assert Argon2PasswordHasher().verify(
        one(owner_query, "SELECT password_hash FROM user_credentials WHERE user_id = %(u)s", u=user_id), GUARD_PASSWORD
    )
    replay = await registration_client.post(
        f"{GUARDS}/creations/{submitted.json()['id']}/verify", json={"otp": code}, headers=bearer(admin["token"])
    )
    assert replay.status_code == 409 and replay.json()["code"] == "GUARD_ALREADY_CREATED"


async def test_supervisors_cannot_create_guards_any_more(
    registration_client: AsyncClient, supervisor: dict[str, Any], owner_query, app_role_engine
) -> None:
    """Decision D-017 (migration 0018): only Admins create Guards, in the API and in the database."""
    body = {"current_password": supervisor["password"]}
    refused = await registration_client.post(f"{GUARDS}/authorization", json=body, headers=bearer(supervisor["token"]))
    assert refused.status_code == 403 and refused.json()["code"] == "ADMIN_ONLY"
    sup_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=supervisor["supervisor_id"])
    rows, _ = await run_as(app_role_engine, sup_id, "SELECT * FROM app_guard_step_up_lookup()")
    assert rows == []
    with pytest.raises(DBAPIError, match="only an active Admin"):
        await run_as(app_role_engine, sup_id, "SELECT app_guard_step_up_start(:g, 600)", {"g": uuid4()})


async def test_only_admins_can_create_guards(
    registration_client: AsyncClient, guard: dict[str, Any], seed: Seed, super_admin
) -> None:
    body = {"current_password": "anything-1234"}
    assert (await registration_client.post(f"{GUARDS}/authorization", json=body)).status_code == 401
    refused = await registration_client.post(f"{GUARDS}/authorization", json=body, headers=bearer(guard["token"]))
    assert refused.status_code == 403 and refused.json()["detail"] == "Only an Organization Admin can add Guards."
    assert (
        await registration_client.post(f"{GUARDS}/authorization", json=body, headers=auth_header(seed.guard_b))
    ).status_code == 403
    assert (
        await registration_client.post(f"{GUARDS}/authorization", json=body, headers=platform_auth_header(super_admin))
    ).status_code == 403


async def test_password_and_code_are_required_and_confirmations_do_not_mix(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    wrong_password = await registration_client.post(
        f"{GUARDS}/authorization", json={"current_password": "Wrong-pass-2026"}, headers=bearer(admin["token"])
    )
    assert wrong_password.status_code == 403 and wrong_password.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    started = await registration_client.post(
        f"{GUARDS}/authorization", json={"current_password": admin["password"]}, headers=bearer(admin["token"])
    )
    unverified = await registration_client.post(
        GUARDS, json=guard_form(started.json()["id"]), headers=bearer(admin["token"])
    )
    assert unverified.status_code == 403 and unverified.json()["code"] == "REVERIFICATION_REQUIRED"
    # A confirmation for adding a Supervisor cannot be used to add a Guard, and the reverse.
    sup_started = await registration_client.post(
        f"{SUPERVISORS}/authorization", json={"current_password": admin["password"]}, headers=bearer(admin["token"])
    )
    sup_grant = sup_started.json()["id"]
    await registration_client.post(
        f"{SUPERVISORS}/authorization/{sup_grant}/verify",
        json={"otp": outbox.last_code(admin["email"])},
        headers=bearer(admin["token"]),
    )
    mixed = await registration_client.post(GUARDS, json=guard_form(sup_grant), headers=bearer(admin["token"]))
    assert mixed.status_code == 403 and mixed.json()["code"] == "REVERIFICATION_REQUIRED"
    guard_grant = await authorize(registration_client, outbox, admin)
    reverse = await registration_client.post(
        SUPERVISORS,
        json={**guard_form(guard_grant), "username": "not.a.sup", "email": "nas@example.com"},
        headers=bearer(admin["token"]),
    )
    assert reverse.status_code == 403
    # One confirmation opens one form.
    assert (
        await registration_client.post(GUARDS, json=guard_form(guard_grant), headers=bearer(admin["token"]))
    ).status_code == 201
    again = await registration_client.post(
        GUARDS, json=guard_form(guard_grant, username="g.two", email="g2@example.com"), headers=bearer(admin["token"])
    )
    assert again.status_code == 403


async def test_duplicate_username_and_email_are_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    grant = await authorize(registration_client, outbox, admin)
    taken = await registration_client.post(
        GUARDS, json=guard_form(grant, username="Maryam.S"), headers=bearer(admin["token"])
    )
    assert taken.status_code == 409 and taken.json()["code"] == "USERNAME_TAKEN"
    submitted = await registration_client.post(
        GUARDS, json=guard_form(grant, email=admin["email"]), headers=bearer(admin["token"])
    )
    done = await registration_client.post(
        f"{GUARDS}/creations/{submitted.json()['id']}/verify",
        json={"otp": outbox.last_code(admin["email"])},
        headers=bearer(admin["token"]),
    )
    assert done.status_code == 409 and done.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    assert one(owner_query, "SELECT count(*) FROM users WHERE role = 'GUARD'") == 1  # only the seeded Guard


async def test_another_organization_cannot_use_this_organizations_confirmation_or_form(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    other = await register_admin(
        registration_client, outbox, organization_name="Harbor Point", username="other.admin", email="o@example.com"
    )
    other["token"] = await sign_in(registration_client, outbox, other)
    grant = await authorize(registration_client, outbox, admin)
    stolen = await registration_client.post(GUARDS, json=guard_form(grant), headers=bearer(other["token"]))
    assert stolen.status_code == 403
    creation = (await registration_client.post(GUARDS, json=guard_form(grant), headers=bearer(admin["token"]))).json()[
        "id"
    ]
    response = await registration_client.post(
        f"{GUARDS}/creations/{creation}/verify",
        json={"otp": outbox.last_code(GUARD_EMAIL)},
        headers=bearer(other["token"]),
    )
    assert response.status_code == 404 and response.json()["code"] == "GUARD_CREATION_NOT_FOUND"
    assert one(owner_query, "SELECT count(*) FROM users WHERE email = %(e)s", e=GUARD_EMAIL) == 0


async def test_guard_ids_are_unique(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    first = await create_guard(registration_client, outbox, admin)
    second = await create_guard(registration_client, outbox, admin, username="g.two", email="g2@example.com")
    assert first["guard_id"] != second["guard_id"] and GUARD_ID.fullmatch(second["guard_id"])


# --- Sign-in -------------------------------------------------------------------------------------------


async def test_guard_signs_in_with_guard_id_email_and_password_then_a_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    created = await create_guard(registration_client, outbox, admin)
    started = await registration_client.post(
        GUARD_LOGIN,
        json={
            "operational_mode": "STANDBY",
            "guard_id": created["guard_id"],
            "email": GUARD_EMAIL,
            "password": GUARD_PASSWORD,
        },
    )
    assert started.status_code == 200 and "access_token" not in started.text
    login_id, code = started.json()["login_id"], outbox.last_code(GUARD_EMAIL)
    standby = {"operational_mode": "STANDBY"}
    bad = await registration_client.post(f"{GUARD_LOGIN}/{login_id}/verify", json={"otp": wrong(code), **standby})
    assert bad.status_code == 400 and "access_token" not in bad.text
    done = await registration_client.post(f"{GUARD_LOGIN}/{login_id}/verify", json={"otp": code, **standby})
    assert done.status_code == 200
    assert (done.json()["operational_mode"], done.json()["dashboard"]) == ("STANDBY", "STANDBY_EMERGENCY")
    token = done.json()["access_token"]
    replay = await registration_client.post(f"{GUARD_LOGIN}/{login_id}/verify", json={"otp": code, **standby})
    assert replay.status_code == 409 and replay.json()["code"] == "LOGIN_ALREADY_COMPLETED"

    me = (await registration_client.get("/api/v1/auth/me", headers=bearer(token))).json()
    assert me["role"] == "GUARD" and me["dashboard"] == "/api/v1/guard/dashboard"
    dashboard = await registration_client.get(f"{GUARD_APP}/dashboard", headers=bearer(token))
    assert dashboard.status_code == 200
    assert dashboard.json()["guard"]["guard_id"] == created["guard_id"]
    assert (dashboard.json()["operational_mode"], dashboard.json()["dashboard"]) == ("STANDBY", "STANDBY_EMERGENCY")
    assert dashboard.json()["organization"]["organization_id"] == admin["organization_id"]
    for path in ("/api/v1/organization-admin/dashboard", "/api/v1/supervisor/dashboard"):
        assert (await registration_client.get(path, headers=bearer(token))).status_code == 403
    for path in ("dashboard", "profile"):
        refused = await registration_client.get(f"{GUARD_APP}/{path}", headers=bearer(admin["token"]))
        assert refused.status_code == 403 and refused.json()["detail"] == "Only Guards can use the Guard app."


async def test_wrong_details_and_the_admin_page_do_not_sign_a_guard_in(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    created = await create_guard(registration_client, outbox, admin)
    base = {"guard_id": created["guard_id"], "email": GUARD_EMAIL, "password": GUARD_PASSWORD}
    base["operational_mode"] = "STANDBY"
    for changes in (
        {"password": "Wrong-pass-2026"},
        {"email": "other@example.com"},
        {"guard_id": "ZZZZZZ000000000000000000000"},
        {"guard_id": admin["display_id"], "email": admin["email"], "password": admin["password"]},  # an Admin
    ):
        response = await registration_client.post(GUARD_LOGIN, json={**base, **changes})
        assert response.status_code == 401 and response.json() == INVALID
    via_admin_page = await registration_client.post(
        LOGIN,
        json={
            "organization_name": admin["organization_name"],
            "organization_id": admin["organization_id"],
            "email": GUARD_EMAIL,
            "password": GUARD_PASSWORD,
        },
    )
    assert via_admin_page.status_code == 401
    owner_execute("UPDATE users SET status = 'LEFT' WHERE display_id = %(d)s", {"d": created["guard_id"]})
    removed = await registration_client.post(GUARD_LOGIN, json=base)
    assert removed.status_code == 401 and removed.json() == INVALID


async def test_expired_guard_login_code_is_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    created = await create_guard(registration_client, outbox, admin)
    started = await registration_client.post(
        GUARD_LOGIN,
        json={
            "operational_mode": "STANDBY",
            "guard_id": created["guard_id"],
            "email": GUARD_EMAIL,
            "password": GUARD_PASSWORD,
        },
    )
    login_id = started.json()["login_id"]
    owner_execute(
        "UPDATE admin_login_otp_challenges SET issued_at = now() - interval '10 minutes', "
        "expires_at = now() - interval '1 second' WHERE login_session_id = %(l)s",
        {"l": login_id},
    )
    expired = await registration_client.post(
        f"{GUARD_LOGIN}/{login_id}/verify", json={"otp": outbox.last_code(GUARD_EMAIL), "operational_mode": "STANDBY"}
    )
    assert expired.status_code == 400 and expired.json()["code"] == "OTP_EXPIRED"


# --- Forgot password ---------------------------------------------------------------------------------------


async def test_guard_resets_password_with_guard_id_and_email(
    registration_client: AsyncClient, outbox: FakeEmailSender, guard: dict[str, Any], owner_query, owner_execute
) -> None:
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=guard["guard_id"])
    owner_execute(
        "UPDATE user_credentials SET password_changed_at = now() - interval '1 minute' WHERE user_id = %(u)s",
        {"u": user_id},
    )
    earlier = create_access_token(
        user_id,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_minutes=30,
        now=datetime.now(UTC) - timedelta(seconds=5),
    )
    started = await registration_client.post(
        RESET, json={"guard_id": guard["guard_id"], "email": GUARD_EMAIL, "operational_mode": "STANDBY"}
    )
    assert started.status_code == 202
    reset_id = started.json()["reset_id"]
    token = (
        await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": outbox.last_code(GUARD_EMAIL)})
    ).json()["reset_token"]
    new_password = "Guard-new-pass-2027"
    done = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": new_password, "confirm_new_password": new_password},
    )
    assert done.status_code == 200
    assert (await registration_client.get(f"{GUARD_APP}/dashboard", headers=bearer(earlier))).status_code == 401
    await guard_sign_in(registration_client, outbox, {**guard, "password": new_password})


@pytest.mark.parametrize("case", ["unknown-id", "wrong-email", "supervisor-id", "wrong-mode"])
async def test_guard_reset_answers_the_same_for_non_matching_details(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    guard: dict[str, Any],
    supervisor: dict[str, Any],
    case: str,
) -> None:
    standby = {"operational_mode": "STANDBY"}
    real = await registration_client.post(RESET, json={"guard_id": guard["guard_id"], "email": GUARD_EMAIL, **standby})
    sent = len(outbox.messages)
    body = {
        "unknown-id": {"guard_id": "ZZZZZZ000000000000000000000", "email": GUARD_EMAIL, **standby},
        "wrong-email": {"guard_id": guard["guard_id"], "email": "nobody@example.com", **standby},
        "supervisor-id": {"guard_id": supervisor["supervisor_id"], "email": supervisor["email"], **standby},
        # The Guard has no post: Stationed does not match, so no code (and the same answer, decision D-018).
        "wrong-mode": {"guard_id": guard["guard_id"], "email": GUARD_EMAIL, "operational_mode": "STATIONED"},
    }[case]
    fake = await registration_client.post(RESET, json=body)
    assert fake.status_code == real.status_code == 202 and set(fake.json()) == set(real.json())
    assert len(outbox.messages) == sent
    guess = await registration_client.post(f"{RESET}/{fake.json()['reset_id']}/verify", json={"otp": "123456"})
    assert guess.status_code == 400 and guess.json()["code"] == "OTP_INVALID"


# --- Profile ----------------------------------------------------------------------------------------------------


async def test_guard_profile_shows_read_only_fields_and_refuses_them(
    registration_client: AsyncClient, guard: dict[str, Any]
) -> None:
    profile = (await registration_client.get(f"{GUARD_APP}/profile", headers=bearer(guard["token"]))).json()
    assert profile["guard_id"] == guard["guard_id"] and profile["editable_fields"] == [
        "email",
        "phone_number",
        "password",
    ]
    for field in ("full_name", "username", "guard_id", "organization_id", "campus_name"):
        response = await registration_client.post(
            f"{GUARD_APP}/profile/changes",
            json={"current_password": GUARD_PASSWORD, field: "Changed"},
            headers=bearer(guard["token"]),
        )
        assert response.status_code == 422


async def test_guard_profile_change_needs_the_current_password_and_the_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, guard: dict[str, Any], owner_query, owner_execute
) -> None:
    wrong_password = await registration_client.post(
        f"{GUARD_APP}/profile/changes",
        json={"current_password": "Wrong-pass-2026", "phone_number": "+923000000000"},
        headers=bearer(guard["token"]),
    )
    assert wrong_password.status_code == 403 and wrong_password.json()["code"] == "CURRENT_PASSWORD_INCORRECT"

    async def save(**changes: str):  # type: ignore[no-untyped-def]
        return await registration_client.post(
            f"{GUARD_APP}/profile/changes",
            json={"current_password": GUARD_PASSWORD, **changes},
            headers=bearer(guard["token"]),
        )

    async def verify(change: str, otp: str, new_email_otp: str | None = None):  # type: ignore[no-untyped-def]
        body = {"otp": otp} | ({"new_email_otp": new_email_otp} if new_email_otp else {})
        return await registration_client.post(
            f"{GUARD_APP}/profile/changes/{change}/verify", json=body, headers=bearer(guard["token"])
        )

    # Failed, expired and cancelled verifications keep the previous profile.
    change = (await save(phone_number="+92 300 9998887")).json()["change_id"]
    assert outbox.messages[-1].to_email == GUARD_EMAIL
    assert (await verify(change, wrong(outbox.last_code(GUARD_EMAIL)))).json()["code"] == "OTP_INVALID"
    assert stored(owner_query, guard["guard_id"])[3] == "+923451112233"
    owner_execute(
        "UPDATE guard_profile_changes SET created_at = now() - interval '1 hour', "
        "expires_at = now() - interval '1 second' WHERE id = %(c)s",
        {"c": change},
    )
    assert (await verify(change, outbox.last_code(GUARD_EMAIL))).status_code == 410
    change = (await save(phone_number="+92 300 9998887")).json()["change_id"]
    assert (
        await registration_client.delete(f"{GUARD_APP}/profile/changes/{change}", headers=bearer(guard["token"]))
    ).status_code == 204
    assert (await verify(change, outbox.last_code(GUARD_EMAIL))).status_code == 410
    assert stored(owner_query, guard["guard_id"])[3] == "+923451112233"

    # A verified change applies.
    change = (await save(phone_number="+92 300 9998887")).json()["change_id"]
    done = await verify(change, outbox.last_code(GUARD_EMAIL))
    assert done.status_code == 200 and done.json()["profile"]["phone_number"] == "+923009998887"
    assert stored(owner_query, guard["guard_id"])[:2] == ("Usman Tariq", "usman.guard")  # name and username unchanged

    # A new email needs its own code.
    new_email = "usman.new@example.com"
    started = (await save(email=new_email)).json()
    only_current = await verify(started["change_id"], outbox.last_code(GUARD_EMAIL))
    assert only_current.status_code == 400 and only_current.json()["code"] == "NEW_EMAIL_OTP_REQUIRED"
    assert stored(owner_query, guard["guard_id"])[2] == GUARD_EMAIL
    done = await verify(started["change_id"], outbox.last_code(GUARD_EMAIL), outbox.last_code(new_email))
    assert done.status_code == 200 and stored(owner_query, guard["guard_id"])[2] == new_email


async def test_guard_password_change_signs_out_earlier_sessions(
    registration_client: AsyncClient, outbox: FakeEmailSender, guard: dict[str, Any], owner_query, owner_execute
) -> None:
    user_id: UUID = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=guard["guard_id"])
    owner_execute(
        "UPDATE user_credentials SET password_changed_at = now() - interval '1 minute' WHERE user_id = %(u)s",
        {"u": user_id},
    )
    earlier = create_access_token(
        user_id,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_minutes=30,
        now=datetime.now(UTC) - timedelta(seconds=5),
    )
    new_password = "Guard-new-pass-2027"
    change = (
        await registration_client.post(
            f"{GUARD_APP}/profile/changes",
            json={
                "current_password": GUARD_PASSWORD,
                "new_password": new_password,
                "confirm_new_password": new_password,
            },
            headers=bearer(guard["token"]),
        )
    ).json()["change_id"]
    done = await registration_client.post(
        f"{GUARD_APP}/profile/changes/{change}/verify",
        json={"otp": outbox.last_code(GUARD_EMAIL)},
        headers=bearer(guard["token"]),
    )
    assert done.status_code == 200 and done.json()["signed_out"] is True and new_password not in done.text
    assert (await registration_client.get(f"{GUARD_APP}/profile", headers=bearer(earlier))).status_code == 401
    await guard_sign_in(registration_client, outbox, {**guard, "password": new_password})


# --- Database access ---------------------------------------------------------------------------------------------


async def test_app_role_reaches_guard_tables_only_through_the_functions(
    registration_client: AsyncClient, guard: dict[str, Any], owner_query, app_role_engine
) -> None:
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=guard["guard_id"])
    for actor in (None, user_id):
        for table in ("guard_step_up_grants", "guard_creations", "guard_profile_changes"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
    # A Guard cannot open a Guard-creation confirmation even directly in the database.
    rows, _ = await run_as(app_role_engine, user_id, "SELECT * FROM app_guard_step_up_lookup()")
    assert rows == []
    with pytest.raises(DBAPIError, match="only an active Admin"):
        await run_as(app_role_engine, user_id, "SELECT app_guard_step_up_start(:g, 600)", {"g": uuid4()})
