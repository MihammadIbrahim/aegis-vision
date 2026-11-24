"""Supervisor profile changes (current password + email code, applied only after verification),
immutable IDs, and forgot-password with Supervisor ID + email (migration 0008)."""

# ruff: noqa: F811  (the imported ``admin`` fixture is requested by parameter name, as pytest expects)

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from httpx import AsyncClient

from src.infrastructure.security.jwt import create_access_token
from tests.conftest import JWT_ALGORITHM, JWT_SECRET, auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import LOGIN, sign_in
from tests.integration.test_supervisors_and_password_reset import (  # noqa: F401  (admin is a fixture)
    RESET,
    SUPERVISOR_PASSWORD,
    admin,
    bearer,
    create_supervisor,
    one,
)
from tests.seed import Seed

PROFILE = "/api/v1/supervisor/profile"
SUP_EMAIL = "sara@lakeside.example.com"


@pytest.fixture
async def supervisor(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> dict[str, Any]:
    created = await create_supervisor(registration_client, outbox, admin)
    login = {
        "organization_name": admin["organization_name"],
        "organization_id": admin["organization_id"],
        "email": created["email"],
        "password": SUPERVISOR_PASSWORD,
    }
    return {**created, **login, "token": await sign_in(registration_client, outbox, login)}


async def save(client: AsyncClient, sup: dict[str, Any], **changes: str):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{PROFILE}/changes", json={"current_password": sup["password"], **changes}, headers=bearer(sup["token"])
    )


async def verify(client: AsyncClient, sup: dict[str, Any], change_id: str, otp: str, new_email_otp: str | None = None):  # type: ignore[no-untyped-def]
    body = {"otp": otp} | ({"new_email_otp": new_email_otp} if new_email_otp else {})
    return await client.post(f"{PROFILE}/changes/{change_id}/verify", json=body, headers=bearer(sup["token"]))


def stored(owner_query, supervisor_id: str) -> tuple[Any, ...]:
    return one(owner_query, "SELECT full_name, username, email FROM users WHERE display_id = %(d)s", d=supervisor_id)


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


# --- Reading and immutable fields ----------------------------------------------------------------


async def test_profile_shows_organization_and_supervisor_id_as_read_only(
    registration_client: AsyncClient,
    supervisor: dict[str, Any],
    admin: dict[str, Any],
    seed: Seed,
) -> None:
    profile = (await registration_client.get(PROFILE, headers=bearer(supervisor["token"]))).json()
    assert profile == {
        "organization_name": "Lakeside Institute",
        "organization_id": admin["organization_id"],
        "supervisor_id": supervisor["supervisor_id"],
        "full_name": "Sara Khan",
        "username": "sara.khan",
        "email": SUP_EMAIL,
        "phone_number": "+923331112233",
        "editable_fields": ["full_name", "username", "email", "password"],
        "read_only_fields": ["organization_name", "organization_id", "supervisor_id"],
    }
    for headers in (bearer(admin["token"]), auth_header(seed.guard_b)):
        assert (await registration_client.get(PROFILE, headers=headers)).status_code == 403


@pytest.mark.parametrize("field", ["supervisor_id", "organization_id", "organization_name", "role", "phone_number"])
async def test_read_only_fields_cannot_be_sent(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query, field: str
) -> None:
    before = len(outbox.messages)
    response = await save(registration_client, supervisor, **{field: "ABCDEF123456789012345678901"})
    assert response.status_code == 422 and [e["type"] for e in response.json()["detail"]] == ["extra_forbidden"]
    assert len(outbox.messages) == before


async def test_database_refuses_any_change_of_a_supervisor_id(
    supervisor: dict[str, Any], owner_execute, owner_query
) -> None:
    with pytest.raises(psycopg.Error, match="permanent"):
        owner_execute(
            "UPDATE users SET display_id = 'ZZZZZZ000000000000000000000' WHERE display_id = %(d)s",
            {"d": supervisor["supervisor_id"]},
        )
    assert one(owner_query, "SELECT count(*) FROM users WHERE display_id = %(d)s", d=supervisor["supervisor_id"]) == 1


# --- Saving changes ----------------------------------------------------------------------------------


async def test_wrong_current_password_changes_nothing_and_sends_no_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query
) -> None:
    before = len(outbox.messages)
    response = await registration_client.post(
        f"{PROFILE}/changes",
        json={"current_password": "Wrong-pass-2026", "full_name": "Sara Ahmed"},
        headers=bearer(supervisor["token"]),
    )
    assert response.status_code == 403 and response.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    assert len(outbox.messages) == before
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Sara Khan"


async def test_saving_without_changes_is_refused(registration_client: AsyncClient, supervisor: dict[str, Any]) -> None:
    response = await save(registration_client, supervisor, full_name="Sara Khan", email=SUP_EMAIL.upper())
    assert response.status_code == 422 and response.json()["code"] == "NO_CHANGES"


async def test_name_and_username_change_only_after_the_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query
) -> None:
    started = await save(registration_client, supervisor, full_name="Sara Ahmed", username="sara.ahmed")
    assert started.status_code == 200, started.text
    assert started.json()["email"] == "sa***@lakeside.example.com" and started.json()["new_email"] is None
    assert outbox.messages[-1].to_email == SUP_EMAIL
    assert stored(owner_query, supervisor["supervisor_id"]) == ("Sara Khan", "sara.khan", SUP_EMAIL)  # not yet

    done = await verify(registration_client, supervisor, started.json()["change_id"], outbox.last_code(SUP_EMAIL))
    assert done.status_code == 200, done.text
    assert done.json()["signed_out"] is False
    assert done.json()["profile"]["full_name"] == "Sara Ahmed" and done.json()["profile"]["username"] == "sara.ahmed"
    assert done.json()["profile"]["supervisor_id"] == supervisor["supervisor_id"]
    assert stored(owner_query, supervisor["supervisor_id"]) == ("Sara Ahmed", "sara.ahmed", SUP_EMAIL)
    assert (await registration_client.get(PROFILE, headers=bearer(supervisor["token"]))).status_code == 200
    replay = await verify(registration_client, supervisor, started.json()["change_id"], outbox.last_code(SUP_EMAIL))
    assert replay.status_code == 409 and replay.json()["code"] == "PROFILE_CHANGE_ALREADY_APPLIED"


async def test_failed_codes_keep_the_previous_profile(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query
) -> None:
    change = (await save(registration_client, supervisor, full_name="Sara Ahmed")).json()["change_id"]
    code = outbox.last_code(SUP_EMAIL)
    for _ in range(4):
        response = await verify(registration_client, supervisor, change, wrong(code))
        assert response.status_code == 400 and response.json()["code"] == "OTP_INVALID"
    locked = await verify(registration_client, supervisor, change, wrong(code))
    assert locked.status_code == 429 and locked.json()["code"] == "OTP_ATTEMPTS_EXCEEDED"
    assert (await verify(registration_client, supervisor, change, code)).status_code == 429
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Sara Khan"


async def test_expired_change_keeps_the_previous_profile(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query, owner_execute
) -> None:
    change = (await save(registration_client, supervisor, full_name="Sara Ahmed")).json()["change_id"]
    owner_execute(
        "UPDATE supervisor_profile_changes SET created_at = now() - interval '1 hour', "
        "expires_at = now() - interval '1 second' WHERE id = %(c)s",
        {"c": change},
    )
    response = await verify(registration_client, supervisor, change, outbox.last_code(SUP_EMAIL))
    assert response.status_code == 410 and response.json()["code"] == "PROFILE_CHANGE_EXPIRED"
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Sara Khan"


async def test_cancelled_change_keeps_the_previous_profile_and_erases_the_request(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query
) -> None:
    change = (
        await save(
            registration_client,
            supervisor,
            full_name="Sara Ahmed",
            new_password="Sara-new-pass-2027",
            confirm_new_password="Sara-new-pass-2027",
        )
    ).json()["change_id"]
    cancelled = await registration_client.delete(f"{PROFILE}/changes/{change}", headers=bearer(supervisor["token"]))
    assert cancelled.status_code == 204
    response = await verify(registration_client, supervisor, change, outbox.last_code(SUP_EMAIL))
    assert response.status_code == 410
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Sara Khan"
    status, values = one(
        owner_query,
        "SELECT status, num_nonnulls(full_name, username, email, password_hash) FROM supervisor_profile_changes "
        "WHERE id = %(c)s",
        c=change,
    )
    assert (status, values) == ("CANCELLED", 0)
    await sign_in(registration_client, outbox, supervisor)  # the old password still works


async def test_a_new_save_replaces_an_unfinished_one(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query, owner_execute
) -> None:
    first = (await save(registration_client, supervisor, full_name="First Name")).json()["change_id"]
    first_code = outbox.last_code(SUP_EMAIL)
    second = (await save(registration_client, supervisor, full_name="Second Name")).json()["change_id"]
    assert (await verify(registration_client, supervisor, first, first_code)).status_code == 410
    assert (await verify(registration_client, supervisor, second, outbox.last_code(SUP_EMAIL))).status_code == 200
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Second Name"


async def test_resend_has_a_cooldown(registration_client: AsyncClient, supervisor: dict[str, Any]) -> None:
    change = (await save(registration_client, supervisor, full_name="Sara Ahmed")).json()["change_id"]
    response = await registration_client.post(
        f"{PROFILE}/changes/{change}/resend-otp", headers=bearer(supervisor["token"])
    )
    assert response.status_code == 429 and response.json()["code"] == "OTP_RESEND_COOLDOWN"


# --- Email and password changes --------------------------------------------------------------------


async def test_new_email_must_be_verified_by_its_own_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query
) -> None:
    new_email = "sara.new@example.com"
    started = (await save(registration_client, supervisor, email=new_email)).json()
    assert started["new_email"] == "sa***@example.com"
    recipients = [m.to_email for m in outbox.messages[-2:]]
    assert recipients == [SUP_EMAIL, new_email]
    current_code, new_code = outbox.last_code(SUP_EMAIL), outbox.last_code(new_email)

    only_current = await verify(registration_client, supervisor, started["change_id"], current_code)
    assert only_current.status_code == 400 and only_current.json()["code"] == "NEW_EMAIL_OTP_REQUIRED"
    assert stored(owner_query, supervisor["supervisor_id"])[2] == SUP_EMAIL
    bad_new = await verify(registration_client, supervisor, started["change_id"], current_code, wrong(new_code))
    assert bad_new.status_code == 400 and bad_new.json()["code"] == "OTP_INVALID"
    assert stored(owner_query, supervisor["supervisor_id"])[2] == SUP_EMAIL

    done = await verify(registration_client, supervisor, started["change_id"], current_code, new_code)
    assert done.status_code == 200, done.text
    assert stored(owner_query, supervisor["supervisor_id"])[2] == new_email
    old_email_login = await registration_client.post(
        LOGIN, json={k: supervisor[k] for k in ("organization_name", "organization_id", "email", "password")}
    )
    assert old_email_login.status_code == 401
    await sign_in(registration_client, outbox, {**supervisor, "email": new_email})


async def test_taken_username_or_email_is_refused(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    supervisor: dict[str, Any],
    admin: dict[str, Any],
    owner_query,
) -> None:
    taken = await save(registration_client, supervisor, username="MARYAM.S")
    assert taken.status_code == 409 and taken.json()["code"] == "USERNAME_TAKEN"
    started = (await save(registration_client, supervisor, email=admin["email"])).json()
    done = await verify(
        registration_client,
        supervisor,
        started["change_id"],
        outbox.last_code(SUP_EMAIL),
        outbox.last_code(admin["email"]),
    )
    assert done.status_code == 409 and done.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    assert stored(owner_query, supervisor["supervisor_id"])[2] == SUP_EMAIL


async def test_password_change_signs_out_earlier_sessions(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any], owner_query, owner_execute
) -> None:
    user_id: UUID = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=supervisor["supervisor_id"])
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
    weak = await save(registration_client, supervisor, new_password="short-1", confirm_new_password="short-1")
    assert weak.status_code == 422 and weak.json()["code"] == "WEAK_PASSWORD"
    new_password = "Sara-new-pass-2027"
    change = (
        await save(registration_client, supervisor, new_password=new_password, confirm_new_password=new_password)
    ).json()["change_id"]
    assert (await registration_client.get(PROFILE, headers=bearer(earlier))).status_code == 200  # not yet changed
    done = await verify(registration_client, supervisor, change, outbox.last_code(SUP_EMAIL))
    assert done.status_code == 200 and done.json()["signed_out"] is True
    assert new_password not in done.text
    assert (await registration_client.get(PROFILE, headers=bearer(earlier))).status_code == 401
    assert (
        await registration_client.post(
            LOGIN, json={k: supervisor[k] for k in ("organization_name", "organization_id", "email", "password")}
        )
    ).status_code == 401
    await sign_in(registration_client, outbox, {**supervisor, "password": new_password})


async def test_a_supervisor_cannot_use_another_supervisors_change(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    supervisor: dict[str, Any],
    admin: dict[str, Any],
    owner_query,
) -> None:
    other = await create_supervisor(registration_client, outbox, admin, username="omar.sup", email="omar@example.com")
    other_login = {k: supervisor[k] for k in ("organization_name", "organization_id")} | {
        "email": other["email"],
        "password": SUPERVISOR_PASSWORD,
    }
    other_token = await sign_in(registration_client, outbox, other_login)
    change = (await save(registration_client, supervisor, full_name="Sara Ahmed")).json()["change_id"]
    code = outbox.last_code(SUP_EMAIL)
    stolen = await registration_client.post(
        f"{PROFILE}/changes/{change}/verify", json={"otp": code}, headers=bearer(other_token)
    )
    assert stolen.status_code == 404
    assert (
        await registration_client.delete(f"{PROFILE}/changes/{change}", headers=bearer(other_token))
    ).status_code == 404
    assert stored(owner_query, supervisor["supervisor_id"])[0] == "Sara Khan"


# --- Forgot password with Supervisor ID ---------------------------------------------------------------


async def test_supervisor_resets_password_with_supervisor_id_and_email(
    registration_client: AsyncClient, outbox: FakeEmailSender, supervisor: dict[str, Any]
) -> None:
    started = await registration_client.post(
        RESET, json={"supervisor_id": supervisor["supervisor_id"], "email": SUP_EMAIL}
    )
    assert started.status_code == 202
    reset_id = started.json()["reset_id"]
    token = (
        await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": outbox.last_code(SUP_EMAIL)})
    ).json()["reset_token"]
    new_password = "Sara-reset-pass-2027"
    done = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": new_password, "confirm_new_password": new_password},
    )
    assert done.status_code == 200
    await sign_in(registration_client, outbox, {**supervisor, "password": new_password})


@pytest.mark.parametrize("case", ["unknown-id", "wrong-email", "admin-id"])
async def test_supervisor_id_reset_answers_the_same_for_non_matching_details(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    supervisor: dict[str, Any],
    admin: dict[str, Any],
    case: str,
) -> None:
    real = await registration_client.post(
        RESET, json={"supervisor_id": supervisor["supervisor_id"], "email": SUP_EMAIL}
    )
    sent = len(outbox.messages)
    body = {
        "unknown-id": {"supervisor_id": "ZZZZZZ000000000000000000000", "email": SUP_EMAIL},
        "wrong-email": {"supervisor_id": supervisor["supervisor_id"], "email": "nobody@example.com"},
        "admin-id": {"supervisor_id": admin["display_id"], "email": admin["email"]},  # not a Supervisor
    }[case]
    fake = await registration_client.post(RESET, json=body)
    assert fake.status_code == real.status_code == 202 and set(fake.json()) == set(real.json())
    assert len(outbox.messages) == sent


@pytest.mark.parametrize(
    "body", [{"email": SUP_EMAIL}, {"organization_id": "ABCDEF12345678901", "supervisor_id": "X", "email": SUP_EMAIL}]
)
async def test_reset_needs_exactly_one_identifier(registration_client: AsyncClient, body: dict[str, str]) -> None:
    assert (await registration_client.post(RESET, json=body)).status_code == 422


async def test_organization_id_reset_still_works_for_admins(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    admin: dict[str, Any],
) -> None:
    started = await registration_client.post(
        RESET, json={"organization_id": admin["organization_id"], "email": admin["email"]}
    )
    assert started.status_code == 202 and outbox.messages[-1].to_email == admin["email"]
