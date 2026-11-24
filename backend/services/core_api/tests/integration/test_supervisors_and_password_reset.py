"""Supervisor creation, Supervisor sign-in and password reset, end to end against PostgreSQL
(migration 0007)."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from httpx import AsyncClient, Response
from sqlalchemy.exc import DBAPIError

from src.infrastructure.security.jwt import create_access_token
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import (
    JWT_ALGORITHM,
    JWT_SECRET,
    DatabaseUrls,
    _client_for,
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
from tests.seed import Seed

SUPERVISORS = "/api/v1/organization-admin/supervisors"
RESET = "/api/v1/auth/password-reset"
SUPERVISOR_ID = re.compile(r"[A-Z]{6}[0-9]{21}")
ADMIN_EMAIL = "maryam@lakeside.example.com"
SUPERVISOR_PASSWORD = "Supervisor-pass-2026"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def one(owner_query, sql: str, **params: Any) -> Any:
    rows = owner_query(sql, params)
    assert len(rows) == 1, rows
    return rows[0][0] if len(rows[0]) == 1 else rows[0]


def supervisor_form(authorization_id: str, **changes: str) -> dict[str, str]:
    return {
        "authorization_id": authorization_id,
        "full_name": "Sara Khan",
        "username": "sara.khan",
        "email": "sara@lakeside.example.com",
        "phone_number": "+92 333 1112233",
        "password": SUPERVISOR_PASSWORD,
        **changes,
    }


@pytest.fixture
async def admin(registration_client: AsyncClient, outbox: FakeEmailSender) -> dict[str, Any]:
    data = await register_admin(registration_client, outbox)
    data["token"] = await sign_in(registration_client, outbox, data)
    return data


async def authorize(client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]) -> str:
    started = await client.post(
        f"{SUPERVISORS}/authorization", json={"current_password": admin["password"]}, headers=bearer(admin["token"])
    )
    assert started.status_code == 200, started.text
    grant = started.json()["id"]
    done = await client.post(
        f"{SUPERVISORS}/authorization/{grant}/verify",
        json={"otp": outbox.last_code(admin["email"])},
        headers=bearer(admin["token"]),
    )
    assert done.status_code == 200, done.text
    return grant


async def create_supervisor(
    client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], **changes: str
) -> dict[str, Any]:
    grant = await authorize(client, outbox, admin)
    form = supervisor_form(grant, **changes)
    submitted = await client.post(SUPERVISORS, json=form, headers=bearer(admin["token"]))
    assert submitted.status_code == 201, submitted.text
    creation = submitted.json()["id"]
    done = await client.post(
        f"{SUPERVISORS}/creations/{creation}/verify",
        json={"otp": outbox.last_code(form["email"])},
        headers=bearer(admin["token"]),
    )
    assert done.status_code == 201, done.text
    return {**done.json(), "password": form["password"]}


# --- Creation: who may start it -------------------------------------------------------------


async def test_only_signed_in_admins_can_start_supervisor_creation(
    registration_client: AsyncClient, seed: Seed, super_admin
) -> None:
    body = {"current_password": "anything-1234"}
    assert (await registration_client.post(f"{SUPERVISORS}/authorization", json=body)).status_code == 401
    for user in (seed.supervisor_a, seed.guard_b):
        response = await registration_client.post(f"{SUPERVISORS}/authorization", json=body, headers=auth_header(user))
        assert response.status_code == 403
    response = await registration_client.post(
        f"{SUPERVISORS}/authorization", json=body, headers=platform_auth_header(super_admin)
    )
    assert response.status_code == 403


async def test_wrong_current_password_is_refused_and_counts_toward_the_lock(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    before = len(outbox.messages)
    for _ in range(4):
        response = await registration_client.post(
            f"{SUPERVISORS}/authorization", json={"current_password": "Wrong-pass-2026"}, headers=bearer(admin["token"])
        )
        assert response.status_code == 403 and response.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    locked = await registration_client.post(
        f"{SUPERVISORS}/authorization", json={"current_password": "Wrong-pass-2026"}, headers=bearer(admin["token"])
    )
    assert locked.status_code == 429 and locked.json()["code"] == "ACCOUNT_LOCKED"
    assert len(outbox.messages) == before


async def test_admin_code_is_checked_before_the_form_opens(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    started = await registration_client.post(
        f"{SUPERVISORS}/authorization", json={"current_password": admin["password"]}, headers=bearer(admin["token"])
    )
    grant = started.json()["id"]
    assert outbox.messages[-1].to_email == ADMIN_EMAIL
    code = outbox.last_code()
    # Not verified yet: the form is refused.
    early = await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(admin["token"]))
    assert early.status_code == 403 and early.json()["code"] == "REVERIFICATION_REQUIRED"
    wrong = await registration_client.post(
        f"{SUPERVISORS}/authorization/{grant}/verify",
        json={"otp": f"{(int(code) + 1) % 10**6:06d}"},
        headers=bearer(admin["token"]),
    )
    assert wrong.status_code == 400 and wrong.json()["code"] == "OTP_INVALID"
    cooldown = await registration_client.post(
        f"{SUPERVISORS}/authorization/{grant}/resend-otp", headers=bearer(admin["token"])
    )
    assert cooldown.status_code == 429 and cooldown.json()["code"] == "OTP_RESEND_COOLDOWN"
    done = await registration_client.post(
        f"{SUPERVISORS}/authorization/{grant}/verify", json={"otp": code}, headers=bearer(admin["token"])
    )
    assert done.status_code == 200 and done.json()["status"] == "VERIFIED"
    # One confirmation opens one form.
    assert (
        await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(admin["token"]))
    ).status_code == 201
    again = await registration_client.post(
        SUPERVISORS,
        json=supervisor_form(grant, username="other.sup", email="o@example.com"),
        headers=bearer(admin["token"]),
    )
    assert again.status_code == 403 and again.json()["code"] == "REVERIFICATION_REQUIRED"


async def test_unknown_confirmation_is_refused(registration_client: AsyncClient, admin: dict[str, Any]) -> None:
    response = await registration_client.post(
        SUPERVISORS, json=supervisor_form(str(uuid4())), headers=bearer(admin["token"])
    )
    assert response.status_code == 403 and response.json()["code"] == "REVERIFICATION_REQUIRED"


# --- Creation: the Supervisor ------------------------------------------------------------------


async def test_supervisor_is_created_in_the_admins_organization_and_campus_after_its_email_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    grant = await authorize(registration_client, outbox, admin)
    submitted = await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(admin["token"]))
    assert submitted.status_code == 201
    assert outbox.messages[-1].to_email == "sara@lakeside.example.com"
    # Nothing exists before the Supervisor's code is verified.
    assert one(owner_query, "SELECT count(*) FROM users WHERE email = 'sara@lakeside.example.com'") == 0
    creation = submitted.json()["id"]
    code = outbox.last_code("sara@lakeside.example.com")
    done = await registration_client.post(
        f"{SUPERVISORS}/creations/{creation}/verify", json={"otp": code}, headers=bearer(admin["token"])
    )
    assert done.status_code == 201, done.text
    data = done.json()
    assert SUPERVISOR_ID.fullmatch(data["supervisor_id"]) and len(data["supervisor_id"]) == 27
    assert data == {
        "supervisor_id": data["supervisor_id"],
        "full_name": "Sara Khan",
        "username": "sara.khan",
        "email": "sara@lakeside.example.com",
        "phone_number": "+923331112233",
        "role": "SUPERVISOR",
        "organization_id": admin["organization_id"],
        "campus_name": "Main Campus",
        "created_at": data["created_at"],
    }
    assert SUPERVISOR_PASSWORD not in done.text and code not in done.text
    role, sup_org, sup_campus, user_id = one(
        owner_query,
        "SELECT role, organization_id, campus_id, id FROM users WHERE display_id = %(d)s",
        d=data["supervisor_id"],
    )
    admin_org, admin_campus = one(
        owner_query, "SELECT organization_id, campus_id FROM users WHERE display_id = %(d)s", d=admin["display_id"]
    )
    assert (role, sup_org, sup_campus) == ("SUPERVISOR", admin_org, admin_campus)
    password_hash = one(owner_query, "SELECT password_hash FROM user_credentials WHERE user_id = %(u)s", u=user_id)
    assert Argon2PasswordHasher().verify(password_hash, SUPERVISOR_PASSWORD)
    assert (
        one(
            owner_query,
            "SELECT num_nonnulls(full_name, username, email, phone, password_hash) "
            "FROM supervisor_creations WHERE id = %(c)s",
            c=creation,
        )
        == 0
    )
    replay = await registration_client.post(
        f"{SUPERVISORS}/creations/{creation}/verify", json={"otp": code}, headers=bearer(admin["token"])
    )
    assert replay.status_code == 409 and replay.json()["code"] == "SUPERVISOR_ALREADY_CREATED"


async def test_wrong_supervisor_code_creates_nothing(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    grant = await authorize(registration_client, outbox, admin)
    creation = (
        await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(admin["token"]))
    ).json()["id"]
    code = outbox.last_code("sara@lakeside.example.com")
    wrong = await registration_client.post(
        f"{SUPERVISORS}/creations/{creation}/verify",
        json={"otp": f"{(int(code) + 1) % 10**6:06d}"},
        headers=bearer(admin["token"]),
    )
    assert wrong.status_code == 400 and wrong.json()["code"] == "OTP_INVALID"
    assert (
        one(owner_query, "SELECT count(*) FROM users WHERE role = 'SUPERVISOR' AND email = 'sara@lakeside.example.com'")
        == 0
    )


async def test_duplicate_username_and_email_are_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    grant = await authorize(registration_client, outbox, admin)
    taken = await registration_client.post(
        SUPERVISORS, json=supervisor_form(grant, username="MARYAM.S"), headers=bearer(admin["token"])
    )
    assert taken.status_code == 409 and taken.json()["code"] == "USERNAME_TAKEN"
    # The confirmation was not used up by the refused form; the email is checked after its code.
    submitted = await registration_client.post(
        SUPERVISORS, json=supervisor_form(grant, email=ADMIN_EMAIL.upper()), headers=bearer(admin["token"])
    )
    assert submitted.status_code == 201
    done = await registration_client.post(
        f"{SUPERVISORS}/creations/{submitted.json()['id']}/verify",
        json={"otp": outbox.last_code(ADMIN_EMAIL)},
        headers=bearer(admin["token"]),
    )
    assert done.status_code == 409 and done.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    assert one(owner_query, "SELECT count(*) FROM users WHERE role = 'SUPERVISOR'") == 1  # only the seeded one


async def test_another_organizations_admin_cannot_touch_this_admins_confirmation_or_form(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    other = await register_admin(
        registration_client, outbox, organization_name="Harbor Point", username="other.admin", email="o@example.com"
    )
    other["token"] = await sign_in(registration_client, outbox, other)
    grant = await authorize(registration_client, outbox, admin)
    stolen = await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(other["token"]))
    assert stolen.status_code == 403 and stolen.json()["code"] == "REVERIFICATION_REQUIRED"
    creation = (
        await registration_client.post(SUPERVISORS, json=supervisor_form(grant), headers=bearer(admin["token"]))
    ).json()["id"]
    code = outbox.last_code("sara@lakeside.example.com")
    for path in (f"{SUPERVISORS}/creations/{creation}/verify", f"{SUPERVISORS}/creations/{creation}/resend-otp"):
        response = await registration_client.post(path, json={"otp": code}, headers=bearer(other["token"]))
        assert response.status_code == 404 and response.json()["code"] == "SUPERVISOR_CREATION_NOT_FOUND"
    assert one(owner_query, "SELECT count(*) FROM users WHERE email = 'sara@lakeside.example.com'") == 0

    created = await create_supervisor(registration_client, outbox, other, username="harbor.sup", email="hs@example.com")
    assert created["organization_id"] == other["organization_id"]


async def test_supervisor_ids_are_unique_and_only_for_supervisors(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query, owner_execute
) -> None:
    first = await create_supervisor(registration_client, outbox, admin)
    second = await create_supervisor(registration_client, outbox, admin, username="omar.sup", email="omar@example.com")
    assert first["supervisor_id"] != second["supervisor_id"]
    org_id, campus_id = one(
        owner_query, "SELECT organization_id, campus_id FROM users WHERE display_id = %(d)s", d=first["supervisor_id"]
    )

    def add(role: str, display_id: str) -> None:
        owner_execute(
            """INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id)
               VALUES (%(id)s, %(o)s, %(c)s, %(r)s, 'ACTIVE', 'Test', %(e)s, %(d)s)""",
            {"id": uuid4(), "o": org_id, "c": campus_id, "r": role, "e": f"{uuid4().hex}@x.org", "d": display_id},
        )

    with pytest.raises(psycopg.errors.UniqueViolation):
        add("SUPERVISOR", first["supervisor_id"])
    for role, bad in (
        ("SUPERVISOR", first["supervisor_id"].lower()),
        ("ADMIN", "ABCDEF123456789012345678901"),
        ("SUPERVISOR", "ABCDEF1234567890123456789"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            add(role, bad)
    add("SUPERVISOR", "SUP-12345678901")  # the original format stays valid
    add("GUARD", "GHIJKL123456789012345678901")  # changed 2026-10-27 (migration 0009): Guards use 6 + 21 too


# --- Supervisor sign-in -------------------------------------------------------------------------


async def test_supervisor_signs_in_on_the_admin_page_and_lands_on_the_supervisor_dashboard(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    created = await create_supervisor(registration_client, outbox, admin)
    supervisor = {
        "organization_name": admin["organization_name"],
        "organization_id": admin["organization_id"],
        "email": created["email"],
        "password": SUPERVISOR_PASSWORD,
    }
    started = await registration_client.post(LOGIN, json=supervisor)
    assert started.status_code == 200 and "access_token" not in started.text
    token = await sign_in(registration_client, outbox, supervisor)

    me = (await registration_client.get("/api/v1/auth/me", headers=bearer(token))).json()
    assert me.pop("account_id")
    assert me == {
        "user_id": created["supervisor_id"],
        "full_name": "Sara Khan",
        "role": "SUPERVISOR",
        "dashboard": "/api/v1/supervisor/dashboard",
        "is_main_admin": False,
    }
    dashboard = await registration_client.get("/api/v1/supervisor/dashboard", headers=bearer(token))
    assert dashboard.status_code == 200
    assert dashboard.json()["organization"]["organization_id"] == admin["organization_id"]
    assert dashboard.json()["supervisor"]["user_id"] == created["supervisor_id"]
    # Role-based access: each dashboard refuses the other role, and Supervisors cannot add Supervisors.
    assert (
        await registration_client.get("/api/v1/organization-admin/dashboard", headers=bearer(token))
    ).status_code == 403
    assert (
        await registration_client.get("/api/v1/supervisor/dashboard", headers=bearer(admin["token"]))
    ).status_code == 403
    assert (
        await registration_client.post(
            f"{SUPERVISORS}/authorization", json={"current_password": SUPERVISOR_PASSWORD}, headers=bearer(token)
        )
    ).status_code == 403
    admin_me = (await registration_client.get("/api/v1/auth/me", headers=bearer(admin["token"]))).json()
    assert admin_me["role"] == "ADMIN" and admin_me["dashboard"] == "/api/v1/organization-admin/dashboard"


async def test_supervisor_with_another_organizations_details_cannot_sign_in(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], seed: Seed, owner_query
) -> None:
    created = await create_supervisor(registration_client, outbox, admin)
    seed_code = one(owner_query, "SELECT organization_code FROM organizations WHERE id = %(o)s", o=seed.org1)
    response = await registration_client.post(
        LOGIN,
        json={
            "organization_name": "Riverside University",
            "organization_id": seed_code,
            "email": created["email"],
            "password": SUPERVISOR_PASSWORD,
        },
    )
    assert response.status_code == 401 and response.json()["code"] == "INVALID_CREDENTIALS"


# --- Password reset --------------------------------------------------------------------------------


async def request_reset(client: AsyncClient, organization_id: str, email: str) -> Response:
    return await client.post(RESET, json={"organization_id": organization_id, "email": email})


async def test_admin_resets_password_and_earlier_sessions_stop_working(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query, owner_execute
) -> None:
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=admin["display_id"])
    # The account was created a moment ago; date its password a minute back so that a token
    # issued 5 seconds ago is valid before the reset.
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
    assert (await registration_client.get("/api/v1/auth/me", headers=bearer(earlier))).status_code == 200

    started = await request_reset(registration_client, admin["organization_id"], admin["email"])
    assert started.status_code == 202
    reset_id = started.json()["reset_id"]
    assert outbox.messages[-1].subject == "Reset your password"
    verified = await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": outbox.last_code()})
    assert verified.status_code == 200, verified.text
    token = verified.json()["reset_token"]
    new_password = "Brand-new-pass-2027"
    done = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": new_password, "confirm_new_password": new_password},
    )
    assert done.status_code == 200 and done.json()["status"] == "PASSWORD_CHANGED"
    assert new_password not in done.text

    expired = await registration_client.get("/api/v1/auth/me", headers=bearer(earlier))
    assert expired.status_code == 401 and expired.json()["detail"] == "Session expired. Please log in again."
    old_login = await registration_client.post(
        LOGIN,
        json={
            **{k: admin[k] for k in ("organization_name", "organization_id", "email")},
            "password": admin["password"],
        },
    )
    assert old_login.status_code == 401
    await sign_in(registration_client, outbox, {**admin, "password": new_password})
    # The token is single-use.
    again = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": "Another-pass-2028", "confirm_new_password": "Another-pass-2028"},
    )
    assert again.status_code == 400 and again.json()["code"] == "RESET_TOKEN_INVALID"


async def test_supervisor_can_reset_their_password(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    created = await create_supervisor(registration_client, outbox, admin)
    reset_id = (await request_reset(registration_client, admin["organization_id"], created["email"])).json()["reset_id"]
    token = (
        await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": outbox.last_code(created["email"])})
    ).json()["reset_token"]
    new_password = "Sara-new-pass-2027"
    done = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": new_password, "confirm_new_password": new_password},
    )
    assert done.status_code == 200
    await sign_in(
        registration_client,
        outbox,
        {
            "organization_name": admin["organization_name"],
            "organization_id": admin["organization_id"],
            "email": created["email"],
            "password": new_password,
        },
    )


@pytest.mark.parametrize(
    "who",
    ["unknown-email", "wrong-organization", "inactive-account"],
)
async def test_reset_answers_the_same_whether_or_not_an_account_matches(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    admin: dict[str, Any],
    seed: Seed,
    owner_query,
    owner_execute,
    who: str,
) -> None:
    real = await request_reset(registration_client, admin["organization_id"], admin["email"])
    sent = len(outbox.messages)
    if who == "unknown-email":
        target = (admin["organization_id"], "nobody@lakeside.example.com")
    elif who == "wrong-organization":
        target = (
            one(owner_query, "SELECT organization_code FROM organizations WHERE id = %(o)s", o=seed.org1),
            admin["email"],
        )
    else:
        owner_execute("UPDATE users SET status = 'LEFT' WHERE display_id = %(d)s", {"d": admin["display_id"]})
        target = (admin["organization_id"], "MARYAM@lakeside.example.com")
    fake = await request_reset(registration_client, *target)

    assert fake.status_code == real.status_code == 202
    assert set(fake.json()) == set(real.json())
    assert {k: v for k, v in fake.json().items() if k not in ("reset_id", "expires_at")} == {
        k: v for k, v in real.json().items() if k not in ("reset_id", "expires_at")
    }
    assert len(outbox.messages) == sent  # no email for a non-matching request
    fake_id = fake.json()["reset_id"]
    guess = await registration_client.post(f"{RESET}/{fake_id}/verify", json={"otp": "123456"})
    assert guess.status_code == 400 and guess.json()["code"] == "OTP_INVALID"
    resend = await registration_client.post(f"{RESET}/{fake_id}/resend-otp")
    assert resend.status_code == 429 and resend.json()["code"] == "OTP_RESEND_COOLDOWN"


async def test_reset_codes_have_attempt_limits_and_cannot_be_replayed(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    reset_id = (await request_reset(registration_client, admin["organization_id"], admin["email"])).json()["reset_id"]
    code = outbox.last_code()
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(4):
        assert (await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": wrong})).json()[
            "code"
        ] == "OTP_INVALID"
    last = await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": wrong})
    assert last.status_code == 429 and last.json()["code"] == "OTP_ATTEMPTS_EXCEEDED"
    assert (await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": code})).status_code == 429

    owner_execute(
        "UPDATE auth_otp_challenges SET issued_at = issued_at - interval '2 minutes', "
        "expires_at = expires_at - interval '2 minutes' WHERE subject_id = %(r)s",
        {"r": reset_id},
    )
    assert (await registration_client.post(f"{RESET}/{reset_id}/resend-otp")).status_code == 202
    code = outbox.last_code()
    owner_execute(
        """UPDATE auth_otp_challenges SET issued_at = now() - interval '10 minutes',
               expires_at = now() - interval '1 second'
            WHERE subject_id = %(r)s AND consumed_at IS NULL AND superseded_at IS NULL""",
        {"r": reset_id},
    )
    expired = await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": code})
    assert expired.status_code == 400 and expired.json()["code"] == "OTP_EXPIRED"


async def test_reset_code_cannot_be_used_twice_and_rules_apply_to_the_new_password(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    reset_id = (await request_reset(registration_client, admin["organization_id"], admin["email"])).json()["reset_id"]
    code = outbox.last_code()
    token = (await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": code})).json()["reset_token"]
    replay = await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": code})
    assert replay.status_code == 409 and replay.json()["code"] == "RESET_ALREADY_VERIFIED"
    for new, confirm, error in (
        ("Short-1", "Short-1", "WEAK_PASSWORD"),
        ("my-maryam.s-pass-2027", "my-maryam.s-pass-2027", "WEAK_PASSWORD"),
        ("Brand-new-pass-2027", "Different-pass-2027", "PASSWORD_MISMATCH"),
    ):
        response = await registration_client.post(
            f"{RESET}/{reset_id}/complete",
            json={"reset_token": token, "new_password": new, "confirm_new_password": confirm},
        )
        assert response.status_code == 422 and response.json()["code"] == error
    forged = await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={
            "reset_token": "not-the-token",
            "new_password": "Brand-new-pass-2027",
            "confirm_new_password": "Brand-new-pass-2027",
        },
    )
    assert forged.status_code == 400 and forged.json()["code"] == "RESET_TOKEN_INVALID"


async def test_reset_unlocks_a_locked_account(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    creds = {k: admin[k] for k in ("organization_name", "organization_id", "email")}
    for _ in range(5):
        await registration_client.post(LOGIN, json={**creds, "password": "Wrong-pass-2026"})
    assert (await registration_client.post(LOGIN, json={**creds, "password": admin["password"]})).status_code == 429
    reset_id = (await request_reset(registration_client, admin["organization_id"], admin["email"])).json()["reset_id"]
    token = (await registration_client.post(f"{RESET}/{reset_id}/verify", json={"otp": outbox.last_code()})).json()[
        "reset_token"
    ]
    new_password = "Unlocked-pass-2027"
    await registration_client.post(
        f"{RESET}/{reset_id}/complete",
        json={"reset_token": token, "new_password": new_password, "confirm_new_password": new_password},
    )
    await sign_in(registration_client, outbox, {**admin, "password": new_password})


async def test_reset_without_an_email_provider_answers_503(
    registration_client: AsyncClient, admin: dict[str, Any], migrated_database: DatabaseUrls
) -> None:
    async with _client_for(migrated_database) as client:
        response = await request_reset(client, admin["organization_id"], admin["email"])
    assert response.status_code == 503 and response.json()["code"] == "EMAIL_DELIVERY_NOT_CONFIGURED"


async def test_reset_requests_are_limited_per_client_address(seed: Seed, migrated_database: DatabaseUrls) -> None:
    async with _client_for(
        migrated_database, email_sender=FakeEmailSender(), password_reset_ip_limit_per_hour=2
    ) as client:
        for _ in range(2):
            assert (await request_reset(client, "ABCDEF12345678901", "x@example.com")).status_code == 202
        limited = await request_reset(client, "ABCDEF12345678901", "x@example.com")
    assert limited.status_code == 429 and limited.json()["code"] == "TOO_MANY_REQUESTS"


# --- Database access ---------------------------------------------------------------------------------


async def test_app_role_reaches_the_new_tables_only_through_the_functions(
    registration_client: AsyncClient, admin: dict[str, Any], owner_query, app_role_engine, platform_role_engine
) -> None:
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=admin["display_id"])
    for actor in (None, user_id):
        for table in ("auth_otp_challenges", "admin_step_up_grants", "supervisor_creations", "password_reset_requests"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
    for helper in ("app_otp_verify('PASSWORD_RESET', gen_random_uuid(), 'x')",):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, None, f"SELECT * FROM {helper}")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, "SELECT app_password_reset_complete(gen_random_uuid(), 'x', 'y')")
    # Without a signed-in Admin, the step-up look-up returns nothing.
    rows, _ = await run_as(app_role_engine, None, "SELECT * FROM app_step_up_lookup()")
    assert rows == []
