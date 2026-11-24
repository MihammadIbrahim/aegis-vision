"""Organization Admin sign-in (credentials -> email OTP -> token) and the Admin dashboard,
end to end against PostgreSQL (migration 0006)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.exc import DBAPIError

from src.domain.exceptions.domain_errors import AuthenticationFailed
from src.infrastructure.security.jwt import (
    PLATFORM_AUDIENCE,
    TENANT_AUDIENCE,
    create_access_token,
    decode_access_token,
)
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
from tests.seed import Seed

REGISTER = "/api/v1/auth/organization-registrations"
LOGIN = "/api/v1/auth/organization-admin/login"
DASHBOARD = "/api/v1/organization-admin/dashboard"
PASSWORD = "Lakeside-pass-2026"
INVALID = {
    "detail": "The organization details, email address or password are incorrect.",
    "code": "INVALID_CREDENTIALS",
}


async def register_admin(client: AsyncClient, outbox: FakeEmailSender, **changes: str) -> dict[str, Any]:
    form = {
        "organization_name": "Lakeside Institute",
        "campus_name": "Main Campus",
        "admin_full_name": "Maryam Siddiqui",
        "username": "maryam.s",
        "email": "maryam@lakeside.example.com",
        "phone_number": "+92 300 123-4567",
        "password": PASSWORD,
        **changes,
    }
    submitted = await client.post(REGISTER, json=form)
    assert submitted.status_code == 201, submitted.text
    done = await client.post(
        f"{REGISTER}/{submitted.json()['registration_id']}/verify", json={"otp": outbox.last_code(form["email"])}
    )
    assert done.status_code == 200, done.text
    data = done.json()
    return {
        "organization_name": form["organization_name"],
        "organization_id": data["organization_id"],
        "email": form["email"],
        "password": form["password"],
        "display_id": data["main_admin"]["user_id"],
    }


def credentials(admin: dict[str, Any], **changes: str) -> dict[str, str]:
    body = {k: admin[k] for k in ("organization_name", "organization_id", "email", "password")}
    body.update(changes)
    return body


async def start(client: AsyncClient, admin: dict[str, Any], **changes: str) -> Response:
    return await client.post(LOGIN, json=credentials(admin, **changes))


async def verify(client: AsyncClient, login_id: str, code: str) -> Response:
    return await client.post(f"{LOGIN}/{login_id}/verify", json={"otp": code})


async def resend(client: AsyncClient, login_id: str) -> Response:
    return await client.post(f"{LOGIN}/{login_id}/resend-otp")


async def sign_in(client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]) -> str:
    started = await start(client, admin)
    assert started.status_code == 200, started.text
    done = await verify(client, started.json()["login_id"], outbox.last_code(admin["email"]))
    assert done.status_code == 200, done.text
    return done.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def one(owner_query, sql: str, **params: Any) -> Any:
    rows = owner_query(sql, params)
    assert len(rows) == 1, rows
    return rows[0][0] if len(rows[0]) == 1 else rows[0]


def count(owner_query, sql: str, **params: Any) -> int:
    return one(owner_query, f"SELECT count(*) FROM ({sql}) AS q", **params)


@pytest.fixture
async def admin(registration_client: AsyncClient, outbox: FakeEmailSender) -> dict[str, Any]:
    """A freshly registered organization and its Main Admin."""
    return await register_admin(registration_client, outbox)


@pytest.fixture
def age_codes(owner_execute):
    def run(login_id: str, seconds: int = 61) -> None:
        owner_execute(
            """UPDATE admin_login_otp_challenges
                  SET issued_at = issued_at - make_interval(secs => %(s)s),
                      expires_at = expires_at - make_interval(secs => %(s)s)
                WHERE login_session_id = %(l)s""",
            {"s": seconds, "l": login_id},
        )

    return run


# --- Credentials --------------------------------------------------------------------------


async def test_right_credentials_send_a_code_but_no_token(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    before = len(outbox.messages)
    response = await start(registration_client, admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "login_id",
        "status",
        "email",
        "delivery",
        "otp_expires_in_seconds",
        "resend_available_in_seconds",
        "login_expires_at",
    }
    assert body["status"] == "OTP_REQUIRED" and body["email"] == "ma***@lakeside.example.com"
    assert len(outbox.messages) == before + 1
    message = outbox.messages[-1]
    assert message.to_email == "maryam@lakeside.example.com" and message.subject == "Your sign-in code"
    code = outbox.last_code()
    assert PASSWORD not in response.text and code not in response.text and "token" not in response.text
    # Only a keyed hash of the code is stored.
    code_hash = one(
        owner_query,
        "SELECT code_hash FROM admin_login_otp_challenges WHERE login_session_id = %(l)s",
        l=body["login_id"],
    )
    assert len(code_hash) == 64 and code not in code_hash


async def test_right_code_issues_an_organization_token_that_opens_the_dashboard(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    started = await start(registration_client, admin)
    code = outbox.last_code()
    done = await verify(registration_client, started.json()["login_id"], code)
    assert done.status_code == 200, done.text
    body = done.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer" and body["expires_in"] == 30 * 60
    assert code not in done.text

    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=admin["display_id"])
    token = body["access_token"]
    assert decode_access_token(token, secret=JWT_SECRET, algorithm=JWT_ALGORITHM, audience=TENANT_AUDIENCE) == user_id
    with pytest.raises(AuthenticationFailed):  # not a Super Admin token
        decode_access_token(token, secret=JWT_SECRET, algorithm=JWT_ALGORITHM, audience=PLATFORM_AUDIENCE)
    assert (await registration_client.get("/api/v1/platform/me", headers=bearer(token))).status_code == 401
    assert one(owner_query, "SELECT last_login_at IS NOT NULL FROM user_credentials WHERE user_id = %(u)s", u=user_id)

    dashboard = await registration_client.get(DASHBOARD, headers=bearer(token))
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json() == {
        "organization": {
            "organization_id": admin["organization_id"],
            "name": "Lakeside Institute",
            "status": "ACTIVE",
            "official_email": "maryam@lakeside.example.com",
            "official_phone_number": "+923001234567",
        },
        "campus": {"name": "Main Campus", "status": "ACTIVE"},
        "admin": {
            "user_id": admin["display_id"],
            "full_name": "Maryam Siddiqui",
            "username": "maryam.s",
            "email": "maryam@lakeside.example.com",
            "phone_number": "+923001234567",
            "role": "ADMIN",
            "is_main_admin": True,
        },
        "summary": {
            "active_admins": 1,
            "active_supervisors": 0,
            "active_guards": 0,
            "cameras": 0,
            "pending_campus_requests": 0,
        },
        "not_available_yet": ["alerts", "incidents", "recordings", "evidence", "ai_detection", "guard_reports"],
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "Wrong-pass-2026"},
        {"organization_name": "lakeside institute"},  # the name must match exactly, including capitalization
        {"organization_name": "Lakeside Institute "},
        {"organization_name": "Unknown Organization"},
        {"organization_id": "ZZZZZZ00000000000"},
        {"organization_id": ""},
        {"email": "someone.else@lakeside.example.com"},
        {"email": ""},
    ],
)
async def test_every_wrong_value_gets_the_same_answer_and_no_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], changes: dict[str, str]
) -> None:
    before = len(outbox.messages)
    response = await start(registration_client, admin, **changes)
    assert response.status_code == 401 and response.json() == INVALID
    assert len(outbox.messages) == before


async def test_email_is_matched_ignoring_capitalization(
    registration_client: AsyncClient, admin: dict[str, Any]
) -> None:
    assert (await start(registration_client, admin, email="MARYAM@Lakeside.Example.com")).status_code == 200


async def test_organization_name_and_id_must_belong_together(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    other = await register_admin(
        registration_client, outbox, organization_name="Harbor Point", username="other.admin", email="o@example.com"
    )
    # Each value is real, but they belong to different organizations.
    mixed = await start(registration_client, admin, organization_id=other["organization_id"])
    assert mixed.status_code == 401 and mixed.json() == INVALID
    mixed = await start(registration_client, other, email=admin["email"], password=admin["password"])
    assert mixed.status_code == 401 and mixed.json() == INVALID


async def test_pending_registration_cannot_sign_in(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    submitted = await registration_client.post(
        REGISTER,
        json={
            "organization_name": "Unverified Org",
            "campus_name": "Main Campus",
            "admin_full_name": "Pending Person",
            "username": "pending.person",
            "email": "pending@example.com",
            "phone_number": "+923001112233",
            "password": PASSWORD,
        },
    )
    reserved = one(
        owner_query,
        "SELECT organization_code FROM organization_registrations WHERE id = %(r)s",
        r=submitted.json()["registration_id"],
    )
    pending = {
        "organization_name": "Unverified Org",
        "organization_id": reserved,
        "email": "pending@example.com",
        "password": PASSWORD,
    }
    response = await start(registration_client, pending)
    assert response.status_code == 401 and response.json() == INVALID


async def test_wrong_passwords_lock_the_account(
    registration_client: AsyncClient, admin: dict[str, Any], owner_query, owner_execute
) -> None:
    for _ in range(4):
        response = await start(registration_client, admin, password="Wrong-pass-2026")
        assert response.status_code == 401 and response.json() == INVALID
    locked = await start(registration_client, admin, password="Wrong-pass-2026")
    assert locked.status_code == 429 and locked.json()["code"] == "ACCOUNT_LOCKED"
    assert 1 <= int(locked.headers["Retry-After"]) <= 15 * 60
    # While locked, even the right password is refused.
    still = await start(registration_client, admin)
    assert still.status_code == 429 and still.json()["code"] == "ACCOUNT_LOCKED"

    owner_execute("UPDATE user_credentials SET locked_until = now() - interval '1 second'")
    assert (await start(registration_client, admin)).status_code == 200
    attempts = one(
        owner_query,
        """SELECT c.failed_login_attempts FROM user_credentials c JOIN users u ON u.id = c.user_id
            WHERE u.display_id = %(d)s""",
        d=admin["display_id"],
    )
    assert attempts == 0  # a right password clears the count


@pytest.mark.parametrize(
    "deactivate",
    [
        "UPDATE users SET status = 'LEFT' WHERE display_id = %(d)s",
        "UPDATE campuses SET status = 'INACTIVE' WHERE id = (SELECT campus_id FROM users WHERE display_id = %(d)s)",
        """UPDATE organizations SET status = 'SUSPENDED'
            WHERE id = (SELECT organization_id FROM users WHERE display_id = %(d)s)""",
    ],
    ids=["admin-left", "campus-inactive", "organization-suspended"],
)
async def test_inactive_accounts_campuses_and_organizations_cannot_sign_in(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute, deactivate: str
) -> None:
    owner_execute(deactivate, {"d": admin["display_id"]})
    before = len(outbox.messages)
    response = await start(registration_client, admin)
    assert response.status_code == 401 and response.json() == INVALID  # same answer as a wrong password
    assert len(outbox.messages) == before


async def test_supervisors_may_use_sign_in_but_accounts_without_a_password_may_not(
    registration_client: AsyncClient, seed: Seed, owner_query, owner_execute
) -> None:
    """Changed 2026-10-27: Supervisors now sign in on the same page (previously refused)."""
    org_code = one(owner_query, "SELECT organization_code FROM organizations WHERE id = %(o)s", o=seed.org1)
    owner_execute(
        "INSERT INTO user_credentials (user_id, password_hash) VALUES (%(u)s, %(h)s)",
        {"u": seed.supervisor_a, "h": Argon2PasswordHasher().hash(PASSWORD)},
    )
    base = {"organization_name": "Riverside University", "organization_id": org_code, "password": PASSWORD}
    supervisor = await registration_client.post(LOGIN, json={**base, "email": "sana@riverside.test"})
    assert supervisor.status_code == 200 and supervisor.json()["status"] == "OTP_REQUIRED"
    no_password = await registration_client.post(LOGIN, json={**base, "email": "ayesha@riverside.test"})
    assert no_password.status_code == 401 and no_password.json() == INVALID
    guard = await registration_client.post(LOGIN, json={**base, "email": "usman@riverside.test"})
    assert guard.status_code == 401 and guard.json() == INVALID


# --- One-time codes -------------------------------------------------------------------------


async def test_wrong_code_is_refused_and_no_token_is_issued(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], age_codes
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    code = outbox.last_code()
    for left in (4, 3, 2, 1):
        response = await verify(registration_client, login_id, wrong(code))
        assert response.status_code == 400 and response.json()["code"] == "OTP_INVALID"
        assert f"{left} attempt" in response.json()["detail"] and "access_token" not in response.text
    fifth = await verify(registration_client, login_id, wrong(code))
    assert fifth.status_code == 429 and fifth.json()["code"] == "OTP_ATTEMPTS_EXCEEDED"
    right = await verify(registration_client, login_id, code)
    assert right.status_code == 429 and "access_token" not in right.text

    age_codes(login_id)
    assert (await resend(registration_client, login_id)).status_code == 200
    assert (await verify(registration_client, login_id, outbox.last_code())).status_code == 200


async def test_expired_code_is_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    owner_execute(
        """UPDATE admin_login_otp_challenges
              SET issued_at = now() - interval '10 minutes', expires_at = now() - interval '1 second'
            WHERE login_session_id = %(l)s""",
        {"l": login_id},
    )
    response = await verify(registration_client, login_id, outbox.last_code())
    assert response.status_code == 400 and response.json()["code"] == "OTP_EXPIRED"


async def test_expired_sign_in_cannot_be_finished(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    owner_execute(
        """UPDATE admin_login_sessions
              SET created_at = now() - interval '1 hour', expires_at = now() - interval '1 second'
            WHERE id = %(l)s""",
        {"l": login_id},
    )
    for response in (
        await verify(registration_client, login_id, outbox.last_code()),
        await resend(registration_client, login_id),
    ):
        assert response.status_code == 410 and response.json()["code"] == "LOGIN_EXPIRED"


async def test_resend_cooldown_and_old_code_stops_working(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], age_codes
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    old_code = outbox.last_code()
    too_soon = await resend(registration_client, login_id)
    assert too_soon.status_code == 429 and too_soon.json()["code"] == "OTP_RESEND_COOLDOWN"
    assert 1 <= int(too_soon.headers["Retry-After"]) <= 60

    age_codes(login_id)
    assert (await resend(registration_client, login_id)).status_code == 200
    new_code = outbox.last_code()
    if new_code != old_code:  # equal only by a one-in-a-million chance
        stale = await verify(registration_client, login_id, old_code)
        assert stale.status_code == 400 and stale.json()["code"] == "OTP_INVALID"
    assert (await verify(registration_client, login_id, new_code)).status_code == 200


async def test_code_cannot_be_replayed(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    code = outbox.last_code()
    assert (await verify(registration_client, login_id, code)).status_code == 200
    for response in (await verify(registration_client, login_id, code), await resend(registration_client, login_id)):
        assert response.status_code == 409 and response.json()["code"] == "LOGIN_ALREADY_COMPLETED"
        assert "access_token" not in response.text


async def test_concurrent_verification_issues_one_token(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any]
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    code = outbox.last_code()
    results = await asyncio.gather(*(verify(registration_client, login_id, code) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]


async def test_codes_per_account_are_limited_per_hour(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_query
) -> None:
    for _ in range(5):
        assert (await start(registration_client, admin)).status_code == 200
    sixth = await start(registration_client, admin)
    assert sixth.status_code == 429 and sixth.json()["code"] == "OTP_HOURLY_LIMIT"
    assert count(owner_query, "SELECT 1 FROM admin_login_sessions") == 5  # the refused sign-in was not kept


async def test_account_deactivated_after_the_password_check_gets_no_token(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    owner_execute("UPDATE users SET status = 'LEFT' WHERE display_id = %(d)s", {"d": admin["display_id"]})
    response = await verify(registration_client, login_id, outbox.last_code())
    assert response.status_code == 401 and response.json() == INVALID


async def test_malformed_code_is_refused_without_counting(
    registration_client: AsyncClient, admin: dict[str, Any], owner_query
) -> None:
    login_id = (await start(registration_client, admin)).json()["login_id"]
    assert (await verify(registration_client, login_id, "12ab56")).status_code == 422
    failed = one(
        owner_query, "SELECT failed_attempts FROM admin_login_otp_challenges WHERE login_session_id = %(l)s", l=login_id
    )
    assert failed == 0


async def test_unknown_sign_in_is_not_found(registration_client: AsyncClient) -> None:
    unknown = str(uuid4())
    for response in (await verify(registration_client, unknown, "123456"), await resend(registration_client, unknown)):
        assert response.status_code == 404 and response.json()["code"] == "LOGIN_NOT_FOUND"


# --- Email delivery -------------------------------------------------------------------------


async def test_without_an_email_provider_no_sign_in_starts(
    registration_client: AsyncClient, admin: dict[str, Any], migrated_database: DatabaseUrls, owner_query
) -> None:
    async with _client_for(migrated_database) as client:  # EMAIL_DELIVERY_MODE defaults to "none"
        response = await start(client, admin)
    assert response.status_code == 503 and response.json()["code"] == "EMAIL_DELIVERY_NOT_CONFIGURED"
    assert count(owner_query, "SELECT 1 FROM admin_login_sessions") == 0


async def test_when_the_provider_refuses_the_email_no_sign_in_is_kept(
    registration_client: AsyncClient, admin: dict[str, Any], migrated_database: DatabaseUrls, owner_query
) -> None:
    async with _client_for(migrated_database, email_sender=FakeEmailSender(fail=True)) as client:
        response = await start(client, admin)
    assert response.status_code == 502 and response.json()["code"] == "EMAIL_DELIVERY_FAILED"
    assert count(owner_query, "SELECT 1 FROM admin_login_sessions") == 0
    assert count(owner_query, "SELECT 1 FROM admin_login_otp_challenges") == 0


async def test_sign_in_requests_are_limited_per_client_address(seed: Seed, migrated_database: DatabaseUrls) -> None:
    async with _client_for(migrated_database, email_sender=FakeEmailSender(), org_login_ip_limit_per_hour=2) as client:
        for _ in range(2):
            assert (await resend(client, str(uuid4()))).status_code == 404
        limited = await resend(client, str(uuid4()))
    assert limited.status_code == 429 and limited.json()["code"] == "TOO_MANY_REQUESTS"


# --- Tokens and the dashboard -----------------------------------------------------------------


async def test_dashboard_refuses_missing_expired_forged_and_super_admin_tokens(
    registration_client: AsyncClient, admin: dict[str, Any], owner_query, super_admin: UUID
) -> None:
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=admin["display_id"])
    expired = create_access_token(
        user_id,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_minutes=5,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    forged = create_access_token(
        user_id, secret="another-secret-0123456789abcdef-xyz", algorithm=JWT_ALGORITHM, expires_minutes=5
    )
    for headers in (
        {},
        bearer("not-a-token"),
        bearer(expired),
        bearer(forged),
    ):
        response = await registration_client.get(DASHBOARD, headers=headers)
        assert response.status_code == 401, headers
    # A Super Admin token is not an organization token: identified, but refused (decision D-024).
    refused = await registration_client.get(DASHBOARD, headers=platform_auth_header(super_admin))
    assert refused.status_code == 403 and refused.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED"


async def test_dashboard_is_for_admins_only(registration_client: AsyncClient, seed: Seed) -> None:
    for user_id in (seed.supervisor_a, seed.guard_b):
        assert (await registration_client.get(DASHBOARD, headers=auth_header(user_id))).status_code == 403
    left = await registration_client.get(DASHBOARD, headers=auth_header(seed.left_admin_b))
    assert left.status_code == 401


async def test_dashboard_shows_only_the_callers_own_organization_and_campus(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], seed: Seed, owner_query
) -> None:
    other = await register_admin(
        registration_client, outbox, organization_name="Harbor Point", username="other.admin", email="o@example.com"
    )
    token = await sign_in(registration_client, outbox, admin)
    # Extra parameters naming another organization are ignored: there is no way to ask for it.
    response = await registration_client.get(
        DASHBOARD, headers=bearer(token), params={"organization_id": other["organization_id"]}
    )
    data = response.json()
    assert data["organization"]["organization_id"] == admin["organization_id"]
    assert other["organization_id"] not in response.text and "Harbor Point" not in response.text

    # Seeded Admins: the counts cover only their own campus.
    first = (await registration_client.get(DASHBOARD, headers=auth_header(seed.first_admin))).json()
    assert first["organization"]["name"] == "Riverside University" and first["campus"]["name"] == "Main Campus"
    pending = count(
        owner_query,
        "SELECT 1 FROM campus_registration_requests WHERE organization_id = %(o)s AND status = 'PENDING'",
        o=seed.org1,
    )
    assert first["summary"] == {
        "active_admins": 1,
        "active_supervisors": 1,
        "active_guards": 0,
        "cameras": 1,
        "pending_campus_requests": pending,
    }
    branch = (await registration_client.get(DASHBOARD, headers=auth_header(seed.admin_b))).json()
    assert branch["campus"]["name"] == "City Campus" and branch["admin"]["is_main_admin"] is False
    assert branch["summary"] == {
        "active_admins": 1,  # Omar Farooq has left
        "active_supervisors": 0,
        "active_guards": 1,
        "cameras": 1,
        "pending_campus_requests": None,  # only the Main Admin sees requests
    }
    org2 = (await registration_client.get(DASHBOARD, headers=auth_header(seed.org2_first_admin))).json()
    assert org2["organization"]["name"] == "Harbor Logistics" and "Riverside" not in str(org2)


async def test_suspending_an_organization_stops_its_existing_tokens(
    registration_client: AsyncClient, outbox: FakeEmailSender, admin: dict[str, Any], owner_execute
) -> None:
    token = await sign_in(registration_client, outbox, admin)
    owner_execute(
        "UPDATE organizations SET status = 'SUSPENDED' WHERE organization_code = %(c)s", {"c": admin["organization_id"]}
    )
    response = await registration_client.get(DASHBOARD, headers=bearer(token))
    assert response.status_code == 403 and response.json()["code"] == "ORGANIZATION_INACTIVE"


# --- Database access ---------------------------------------------------------------------------


async def test_app_role_reaches_credentials_and_sign_ins_only_through_the_functions(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    admin: dict[str, Any],
    owner_query,
    app_role_engine,
    platform_role_engine,
) -> None:
    await start(registration_client, admin)
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=admin["display_id"])
    for actor in (None, user_id):
        for table in ("user_credentials", "admin_login_sessions", "admin_login_otp_challenges"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, "SELECT * FROM app_admin_login_lookup('a', 'b', 'c')")
    # The look-up gives nothing unless all three values match one Admin.
    rows, _ = await run_as(
        app_role_engine,
        None,
        "SELECT * FROM app_admin_login_lookup(:n, :c, :e)",
        {"n": "Lakeside Institute", "c": admin["organization_id"], "e": "nobody@example.com"},
    )
    assert rows == []
