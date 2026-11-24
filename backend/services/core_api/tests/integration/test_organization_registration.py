"""First Organization Admin registration with email OTP, end to end against PostgreSQL.

Covers: valid registration, field validation, duplicate names (ignoring capitalization),
usernames and emails, OTP success / wrong / expired / reused / too many attempts,
resend cooldown and hourly limit, nothing created before verification, exactly one
organization + Main Campus + Main Admin, Organization ID and Admin ID format and
uniqueness, atomicity under an injected failure, and direct database access rules.
"""

import asyncio
import re
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import AsyncClient, Response
from sqlalchemy.exc import DBAPIError

from src.config.settings import Settings
from src.infrastructure.security.otp import HmacOtpCodec, derive_otp_key
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import JWT_SECRET, DatabaseUrls, _client_for, auth_header, run_as
from tests.fakes import FakeEmailSender
from tests.seed import Seed

BASE = "/api/v1/auth/organization-registrations"
PASSWORD = "Lakeside-pass-2026"
ORG_ID = re.compile(r"[A-Z]{6}[0-9]{11}")
STAFF_ID = re.compile(r"[A-Z]{6}[0-9]{11}")


def form(**changes: str) -> dict[str, str]:
    data = {
        "organization_name": "Lakeside Institute",
        "campus_name": "Main Campus",
        "admin_full_name": "Maryam Siddiqui",
        "username": "maryam.s",
        "email": "maryam@lakeside.example.com",
        "phone_number": "+92 300 123-4567",
        "password": PASSWORD,
    }
    data.update(changes)
    return data


async def submit(client: AsyncClient, **changes: str) -> str:
    response = await client.post(BASE, json=form(**changes))
    assert response.status_code == 201, response.text
    return response.json()["registration_id"]


async def verify(client: AsyncClient, registration_id: str | UUID, code: str) -> Response:
    return await client.post(f"{BASE}/{registration_id}/verify", json={"otp": code})


async def resend(client: AsyncClient, registration_id: str | UUID) -> Response:
    return await client.post(f"{BASE}/{registration_id}/resend-otp")


async def register(client: AsyncClient, outbox: FakeEmailSender, **changes: str) -> dict[str, Any]:
    registration_id = await submit(client, **changes)
    response = await verify(client, registration_id, outbox.last_code(changes.get("email")))
    assert response.status_code == 200, response.text
    return response.json()


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def error_code(response: Response) -> str | None:
    return response.json().get("code")


def one(owner_query, sql: str, **params: Any) -> Any:
    rows = owner_query(sql, params)
    assert len(rows) == 1, rows
    return rows[0][0] if len(rows[0]) == 1 else rows[0]


def count(owner_query, sql: str, **params: Any) -> int:
    return one(owner_query, f"SELECT count(*) FROM ({sql}) AS q", **params)


@pytest.fixture
def age_codes(owner_execute):
    """Move a registration's codes back in time (to get past the resend cooldown)."""

    def run(registration_id: str, seconds: int = 61) -> None:
        owner_execute(
            """UPDATE email_otp_challenges
                  SET issued_at = issued_at - make_interval(secs => %(s)s),
                      expires_at = expires_at - make_interval(secs => %(s)s)
                WHERE registration_id = %(r)s""",
            {"s": seconds, "r": registration_id},
        )

    return run


# --- 1, 5, 12: valid registration -------------------------------------------------------


async def test_valid_registration_creates_the_organization_its_main_campus_and_main_admin(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    submitted = await registration_client.post(BASE, json=form())
    assert submitted.status_code == 201, submitted.text
    started = submitted.json()
    assert set(started) == {
        "registration_id",
        "status",
        "email",
        "delivery",
        "otp_expires_in_seconds",
        "resend_available_in_seconds",
        "registration_expires_at",
    }
    assert started["status"] == "PENDING_EMAIL_VERIFICATION"
    assert started["email"] == "ma***@lakeside.example.com"
    assert started["delivery"] == "EMAIL"
    assert started["otp_expires_in_seconds"] == 300 and started["resend_available_in_seconds"] == 60
    [message] = outbox.messages
    assert message.to_email == "maryam@lakeside.example.com"
    assert "Lakeside" not in message.text_body  # nothing typed into the form is emailed
    code = outbox.last_code()

    completed = await verify(registration_client, started["registration_id"], code)
    assert completed.status_code == 200, completed.text
    data = completed.json()
    assert ORG_ID.fullmatch(data["organization_id"])
    admin_id = data["main_admin"]["user_id"]
    assert STAFF_ID.fullmatch(admin_id)
    assert data == {
        "status": "COMPLETED",
        "organization_id": data["organization_id"],
        "organization_name": "Lakeside Institute",
        "official_email": "maryam@lakeside.example.com",
        "official_phone_number": "+923001234567",
        "campus_name": "Main Campus",
        "main_admin": {
            "user_id": admin_id,
            "full_name": "Maryam Siddiqui",
            "username": "maryam.s",
            "email": "maryam@lakeside.example.com",
            "phone_number": "+923001234567",
            "role": "ADMIN",
            "is_main_admin": True,
        },
        "registered_at": data["registered_at"],
    }
    for text in (submitted.text, completed.text):
        assert PASSWORD not in text and code not in text and "argon2" not in text and "token" not in text

    org_id, name, off_email, off_phone = one(
        owner_query,
        "SELECT id, name, official_email, official_phone FROM organizations WHERE organization_code = %(c)s",
        c=data["organization_id"],
    )
    assert (name, off_email, off_phone) == ("Lakeside Institute", "maryam@lakeside.example.com", "+923001234567")
    campus_id, campus_name, campus_status = one(
        owner_query, "SELECT id, name, status FROM campuses WHERE organization_id = %(o)s", o=org_id
    )
    assert (campus_name, campus_status) == ("Main Campus", "ACTIVE")
    user = one(
        owner_query,
        """SELECT id, campus_id, role, status, is_first_admin, display_id, full_name, email, username, phone
             FROM users WHERE organization_id = %(o)s""",
        o=org_id,
    )
    assert user[1:] == (
        campus_id,
        "ADMIN",
        "ACTIVE",
        True,
        admin_id,
        "Maryam Siddiqui",
        "maryam@lakeside.example.com",
        "maryam.s",
        "+923001234567",
    )
    password_hash = one(owner_query, "SELECT password_hash FROM user_credentials WHERE user_id = %(u)s", u=user[0])
    assert password_hash.startswith("$argon2id$") and Argon2PasswordHasher().verify(password_hash, PASSWORD)

    # The registration keeps only its Organization ID and outcome; the applicant's details are erased.
    status, kept_code, linked_org, details = one(
        owner_query,
        """SELECT status, organization_code, organization_id,
                  num_nonnulls(organization_name, admin_full_name, username, email, phone, password_hash,
                               recipient_digest)
             FROM organization_registrations WHERE id = %(r)s""",
        r=started["registration_id"],
    )
    assert (status, kept_code, linked_org, details) == ("COMPLETED", data["organization_id"], org_id, 0)


async def test_new_main_admin_is_isolated_to_their_own_organization_like_any_other_admin(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, app_role_engine, seed: Seed
) -> None:
    data = await register(registration_client, outbox)
    user_id, org_id, campus_id = one(
        owner_query,
        "SELECT id, organization_id, campus_id FROM users WHERE display_id = %(d)s",
        d=data["main_admin"]["user_id"],
    )
    rows, _ = await run_as(app_role_engine, user_id, "SELECT id FROM organizations")
    assert {row[0] for row in rows} == {org_id}
    rows, _ = await run_as(app_role_engine, user_id, "SELECT id FROM campuses")
    assert {row[0] for row in rows} == {campus_id}
    rows, _ = await run_as(app_role_engine, seed.first_admin, "SELECT id FROM organizations")
    assert {row[0] for row in rows} == {seed.org1}

    # The existing organization routes treat the new account as its organization's First Admin.
    directory = await registration_client.get("/api/v1/organization/admin-directory", headers=auth_header(user_id))
    assert directory.json() == {"admins": [{"full_name": "Maryam Siddiqui", "is_first_admin": True}]}


# --- 2: field validation -------------------------------------------------------------------


async def test_missing_fields_are_listed_without_echoing_values(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    response = await registration_client.post(BASE, json={"password": PASSWORD})
    assert response.status_code == 422
    missing = {tuple(e["loc"]) for e in response.json()["detail"] if e["type"] == "missing"}
    assert missing == {
        ("body", name)
        for name in ("organization_name", "campus_name", "admin_full_name", "username", "email", "phone_number")
    }
    assert PASSWORD not in response.text
    assert outbox.messages == []
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"organization_name": ""}, "INVALID_ORGANIZATION_NAME"),
        ({"organization_name": " Lakeside Institute"}, "INVALID_ORGANIZATION_NAME"),
        ({"organization_name": "Lakeside  Institute"}, "INVALID_ORGANIZATION_NAME"),
        ({"admin_full_name": "   "}, "INVALID_FULL_NAME"),
        ({"username": "ma"}, "INVALID_USERNAME"),
        ({"username": "maryam s"}, "INVALID_USERNAME"),
        ({"email": "not-an-email"}, "INVALID_EMAIL"),
        ({"phone_number": "12345"}, "INVALID_PHONE_NUMBER"),
        ({"phone_number": ""}, "INVALID_PHONE_NUMBER"),
        ({"password": "short-1"}, "WEAK_PASSWORD"),
        ({"password": "only-letters-here"}, "WEAK_PASSWORD"),
        ({"password": "maryam.s-pass-2026"}, "WEAK_PASSWORD"),
    ],
)
async def test_invalid_values_are_refused_with_a_field_code(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, changes: dict[str, str], code: str
) -> None:
    response = await registration_client.post(BASE, json=form(**changes))
    assert response.status_code == 422, response.text
    assert error_code(response) == code
    assert PASSWORD not in response.text and changes.get("password", PASSWORD) not in response.text
    assert outbox.messages == []
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0


async def test_first_campus_is_named_by_the_applicant(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    # Decision D-021: the campus name is required and becomes the first campus's name.
    data = await register(registration_client, outbox, campus_name="  City   Campus ")
    assert data["campus_name"] == "City Campus"
    assert STAFF_ID.fullmatch(data["main_admin"]["user_id"]) and ORG_ID.fullmatch(data["organization_id"])
    campuses = owner_query(
        "SELECT ca.name FROM campuses ca JOIN organizations o ON o.id = ca.organization_id "
        "WHERE o.organization_code = %(c)s",
        {"c": data["organization_id"]},
    )
    assert campuses == [("City Campus",)]
    [(details,)] = owner_query("SELECT num_nonnulls(campus_name, organization_name) FROM organization_registrations")
    assert details == 0  # erased on completion with the other details


@pytest.mark.parametrize("campus_name", [None, "", "  ", "--", "x" * 101])
async def test_campus_name_is_required_and_checked(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, campus_name: str | None
) -> None:
    body = form()
    if campus_name is None:
        del body["campus_name"]
    else:
        body["campus_name"] = campus_name
    response = await registration_client.post(BASE, json=body)
    assert response.status_code == 422, response.text
    if campus_name is None:  # missing field: refused by the request schema
        assert [e["loc"][-1] for e in response.json()["detail"]] == ["campus_name"]
    else:
        assert error_code(response) == "INVALID_CAMPUS_NAME"
    assert outbox.messages == []
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0


@pytest.mark.parametrize("field", ["organization_id", "campus_id", "role", "user_id"])
async def test_fields_the_client_may_not_choose_are_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, field: str
) -> None:
    response = await registration_client.post(BASE, json={**form(), field: "ORG-00000000001"})
    assert response.status_code == 422
    assert [e["type"] for e in response.json()["detail"]] == ["extra_forbidden"]
    assert outbox.messages == []


# --- 3: organization names -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["Riverside University", "riverside university", "RIVERSIDE UNIVERSITY"])
async def test_existing_organization_name_is_refused_ignoring_capitalization(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, name: str
) -> None:
    response = await registration_client.post(BASE, json=form(organization_name=name))
    assert response.status_code == 409 and error_code(response) == "ORGANIZATION_NAME_TAKEN"
    assert outbox.messages == []
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0


async def test_organization_name_is_stored_exactly_as_entered(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    data = await register(registration_client, outbox, organization_name="lakeSide INSTITUTE of Technology")
    assert data["organization_name"] == "lakeSide INSTITUTE of Technology"
    stored = one(
        owner_query, "SELECT name FROM organizations WHERE organization_code = %(c)s", c=data["organization_id"]
    )
    assert stored == "lakeSide INSTITUTE of Technology"

    again = await registration_client.post(
        BASE,
        json=form(
            organization_name="Lakeside Institute Of Technology", username="other.admin", email="other@example.com"
        ),
    )
    assert again.status_code == 409 and error_code(again) == "ORGANIZATION_NAME_TAKEN"


async def test_two_registrations_racing_for_the_same_name_create_one_organization(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    first = await submit(registration_client)
    first_code = outbox.last_code("maryam@lakeside.example.com")
    second = await submit(
        registration_client, organization_name="LAKESIDE INSTITUTE", username="other.admin", email="other@example.com"
    )
    second_code = outbox.last_code("other@example.com")

    results = await asyncio.gather(
        verify(registration_client, first, first_code), verify(registration_client, second, second_code)
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    loser_index = 0 if results[0].status_code == 409 else 1
    assert error_code(results[loser_index]) == "ORGANIZATION_NAME_TAKEN"
    assert count(owner_query, "SELECT 1 FROM organizations WHERE lower(name) = 'lakeside institute'") == 1
    assert (
        count(owner_query, "SELECT 1 FROM users WHERE email IN ('maryam@lakeside.example.com', 'other@example.com')")
        == 1
    )

    # The losing registration is left exactly as it was: pending, email not verified, code unused.
    loser = (first, second)[loser_index]
    status, verified_at = one(
        owner_query, "SELECT status, email_verified_at FROM organization_registrations WHERE id = %(r)s", r=loser
    )
    assert (status, verified_at) == ("PENDING", None)
    assert (
        one(owner_query, "SELECT consumed_at FROM email_otp_challenges WHERE registration_id = %(r)s", r=loser) is None
    )


# --- 4: usernames and emails ---------------------------------------------------------------


async def test_taken_username_is_refused_ignoring_capitalization(
    registration_client: AsyncClient, outbox: FakeEmailSender
) -> None:
    await register(registration_client, outbox)
    response = await registration_client.post(
        BASE, json=form(organization_name="Other Org", username="MARYAM.S", email="other@example.com")
    )
    assert response.status_code == 409 and error_code(response) == "USERNAME_TAKEN"


async def test_taken_email_is_refused_only_after_the_code_proves_ownership(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    await register(registration_client, outbox)
    # Submitting does not reveal whether the email is registered...
    second = await submit(
        registration_client,
        organization_name="Other Org",
        username="other.admin",
        email="MARYAM@lakeside.example.com",
    )
    # ...only the owner of the inbox, holding the code, learns it.
    response = await verify(registration_client, second, outbox.last_code())
    assert response.status_code == 409 and error_code(response) == "EMAIL_ALREADY_REGISTERED"
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Other Org'") == 0
    assert count(owner_query, "SELECT 1 FROM users WHERE lower(email) = 'maryam@lakeside.example.com'") == 1


# --- 6–10: one-time codes --------------------------------------------------------------------


async def test_wrong_code_is_refused_and_counted(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    response = await verify(registration_client, registration_id, wrong(code))
    assert response.status_code == 400 and error_code(response) == "OTP_INVALID"
    assert response.json()["detail"] == "The code is incorrect. 4 attempts left."
    failed = one(
        owner_query, "SELECT failed_attempts FROM email_otp_challenges WHERE registration_id = %(r)s", r=registration_id
    )
    assert failed == 1
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 0
    assert (await verify(registration_client, registration_id, code)).status_code == 200


async def test_expired_code_is_refused(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    owner_execute(
        """UPDATE email_otp_challenges
              SET issued_at = now() - interval '10 minutes', expires_at = now() - interval '1 second'
            WHERE registration_id = %(r)s""",
        {"r": registration_id},
    )
    response = await verify(registration_client, registration_id, code)
    assert response.status_code == 400 and error_code(response) == "OTP_EXPIRED"
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 0

    assert (await resend(registration_client, registration_id)).status_code == 200
    assert (await verify(registration_client, registration_id, outbox.last_code())).status_code == 200


async def test_code_cannot_be_used_twice(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    assert (await verify(registration_client, registration_id, code)).status_code == 200

    again = await verify(registration_client, registration_id, code)
    assert again.status_code == 409 and error_code(again) == "REGISTRATION_ALREADY_COMPLETED"
    another = await resend(registration_client, registration_id)
    assert another.status_code == 409 and error_code(another) == "REGISTRATION_ALREADY_COMPLETED"
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 1
    assert count(owner_query, "SELECT 1 FROM users WHERE email = 'maryam@lakeside.example.com'") == 1


async def test_previous_code_stops_working_once_a_new_one_is_sent(
    registration_client: AsyncClient, outbox: FakeEmailSender, age_codes
) -> None:
    registration_id = await submit(registration_client)
    old_code = outbox.last_code()
    age_codes(registration_id)
    assert (await resend(registration_client, registration_id)).status_code == 200
    new_code = outbox.last_code()
    if new_code != old_code:  # equal only by a one-in-a-million chance
        stale = await verify(registration_client, registration_id, old_code)
        assert stale.status_code == 400 and error_code(stale) == "OTP_INVALID"
    assert (await verify(registration_client, registration_id, new_code)).status_code == 200


async def test_too_many_wrong_codes_lock_the_code_until_a_new_one_is_sent(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, age_codes
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    for left in (4, 3, 2, 1):
        response = await verify(registration_client, registration_id, wrong(code))
        assert response.status_code == 400 and error_code(response) == "OTP_INVALID"
        assert f"{left} attempt" in response.json()["detail"]
    fifth = await verify(registration_client, registration_id, wrong(code))
    assert fifth.status_code == 429 and error_code(fifth) == "OTP_ATTEMPTS_EXCEEDED"
    # Even the right code no longer works.
    right = await verify(registration_client, registration_id, code)
    assert right.status_code == 429 and error_code(right) == "OTP_ATTEMPTS_EXCEEDED"
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 0

    age_codes(registration_id)
    assert (await resend(registration_client, registration_id)).status_code == 200
    assert (await verify(registration_client, registration_id, outbox.last_code())).status_code == 200


async def test_resend_has_a_cooldown(registration_client: AsyncClient, outbox: FakeEmailSender, age_codes) -> None:
    registration_id = await submit(registration_client)
    too_soon = await resend(registration_client, registration_id)
    assert too_soon.status_code == 429 and error_code(too_soon) == "OTP_RESEND_COOLDOWN"
    retry_after = int(too_soon.headers["Retry-After"])
    assert 1 <= retry_after <= 60 and too_soon.json()["retry_after_seconds"] == retry_after
    assert len(outbox.messages) == 1

    age_codes(registration_id)
    assert (await resend(registration_client, registration_id)).status_code == 200
    assert len(outbox.messages) == 2


async def test_codes_per_email_address_are_limited_per_hour_across_registrations(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute, age_codes
) -> None:
    first = await submit(registration_client)  # code 1
    for _ in range(3):  # codes 2-4
        age_codes(first)
        assert (await resend(registration_client, first)).status_code == 200
    await submit(registration_client, organization_name="Second Org", username="second.admin")  # code 5, same email

    sixth = await registration_client.post(BASE, json=form(organization_name="Third Org", username="third.admin"))
    assert sixth.status_code == 429 and error_code(sixth) == "OTP_HOURLY_LIMIT"
    assert 1 <= int(sixth.headers["Retry-After"]) <= 3600
    assert count(owner_query, "SELECT 1 FROM organization_registrations WHERE organization_name = 'Third Org'") == 0
    age_codes(first)
    blocked = await resend(registration_client, first)
    assert blocked.status_code == 429 and error_code(blocked) == "OTP_HOURLY_LIMIT"
    assert len(outbox.messages) == 5

    # Another address is not affected.
    await submit(registration_client, organization_name="Fourth Org", username="fourth.admin", email="x@example.com")
    # An hour later, codes can be sent again.
    owner_execute(
        "UPDATE email_otp_challenges SET issued_at = issued_at - interval '1 hour', "
        "expires_at = expires_at - interval '1 hour'"
    )
    assert (await resend(registration_client, first)).status_code == 200


# --- 11: nothing exists before verification --------------------------------------------------


async def test_nothing_is_created_before_the_code_is_verified(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    def totals() -> tuple[int, ...]:
        return tuple(
            count(owner_query, f"SELECT 1 FROM {table}")
            for table in ("organizations", "campuses", "users", "user_credentials")
        )

    before = totals()
    response = await registration_client.post(BASE, json=form())
    registration_id = response.json()["registration_id"]
    code = outbox.last_code()
    assert totals() == before
    assert "access_token" not in response.json()

    status, password_hash, verified_at, organization_id = one(
        owner_query,
        """SELECT status, password_hash, email_verified_at, organization_id
             FROM organization_registrations WHERE id = %(r)s""",
        r=registration_id,
    )
    assert (status, verified_at, organization_id) == ("PENDING", None, None)
    assert password_hash.startswith("$argon2id$")
    # The code is stored only as its keyed hash, and the password nowhere in plain text.
    code_hash = one(
        owner_query, "SELECT code_hash FROM email_otp_challenges WHERE registration_id = %(r)s", r=registration_id
    )
    settings = Settings(
        database_url="x", platform_database_url="x", migration_database_url="x", jwt_secret_key=JWT_SECRET
    )
    assert code_hash == HmacOtpCodec(derive_otp_key(settings)).hash_code(UUID(registration_id), code)
    for table in ("organization_registrations", "email_otp_challenges"):
        rows = owner_query(f"SELECT t::text FROM {table} t")
        assert rows and all(PASSWORD not in row[0] for row in rows)


# --- 12, 15: exactly one of everything, all or nothing ----------------------------------------


async def test_concurrent_verification_creates_exactly_one_organization(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    results = await asyncio.gather(*(verify(registration_client, registration_id, code) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert {error_code(r) for r in results if r.status_code == 409} == {"REGISTRATION_ALREADY_COMPLETED"}
    org_id = one(owner_query, "SELECT id FROM organizations WHERE name = 'Lakeside Institute'")
    assert count(owner_query, "SELECT 1 FROM campuses WHERE organization_id = %(o)s", o=org_id) == 1
    assert count(owner_query, "SELECT 1 FROM users WHERE organization_id = %(o)s", o=org_id) == 1


async def test_failure_during_completion_leaves_nothing_and_the_same_code_works_afterwards(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    registration_id = await submit(registration_client)
    code = outbox.last_code()
    reserved = one(
        owner_query, "SELECT organization_code FROM organization_registrations WHERE id = %(r)s", r=registration_id
    )
    # Make the very last insert (the password hash) fail, after the organization, campus and user inserts.
    owner_execute(
        """CREATE FUNCTION test_fail_credentials() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN RAISE EXCEPTION 'injected failure'; END $$"""
    )
    owner_execute(
        """CREATE TRIGGER test_fail_credentials BEFORE INSERT ON user_credentials
           FOR EACH ROW EXECUTE FUNCTION test_fail_credentials()"""
    )
    try:
        with pytest.raises(DBAPIError, match="injected failure"):
            await verify(registration_client, registration_id, code)
    finally:
        owner_execute("DROP TRIGGER test_fail_credentials ON user_credentials")
        owner_execute("DROP FUNCTION test_fail_credentials()")

    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 0
    assert (
        count(
            owner_query,
            "SELECT 1 FROM campuses c LEFT JOIN organizations o ON o.id = c.organization_id WHERE o.id IS NULL",
        )
        == 0
    )
    assert count(owner_query, "SELECT 1 FROM users WHERE email = 'maryam@lakeside.example.com'") == 0
    status, verified_at, kept = one(
        owner_query,
        "SELECT status, email_verified_at, organization_code FROM organization_registrations WHERE id = %(r)s",
        r=registration_id,
    )
    assert (status, verified_at, kept) == ("PENDING", None, reserved)
    assert (
        one(
            owner_query, "SELECT consumed_at FROM email_otp_challenges WHERE registration_id = %(r)s", r=registration_id
        )
        is None
    )

    # The retry uses the same code and gets the same, already reserved, Organization ID.
    retried = await verify(registration_client, registration_id, code)
    assert retried.status_code == 200, retried.text
    assert retried.json()["organization_id"] == reserved


# --- 13, 14: Organization ID and Admin ID ----------------------------------------------------


async def test_organization_id_is_reserved_at_submission_and_kept_through_retries(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, age_codes
) -> None:
    registration_id = await submit(registration_client)

    def reserved() -> str:
        return one(
            owner_query, "SELECT organization_code FROM organization_registrations WHERE id = %(r)s", r=registration_id
        )

    code = reserved()
    assert ORG_ID.fullmatch(code)
    await verify(registration_client, registration_id, wrong(outbox.last_code()))
    age_codes(registration_id)
    assert (await resend(registration_client, registration_id)).status_code == 200
    assert reserved() == code
    done = await verify(registration_client, registration_id, outbox.last_code())
    assert done.json()["organization_id"] == code


async def test_organization_ids_are_unique_and_permanent(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    first = (await register(registration_client, outbox))["organization_id"]
    second = (
        await register(
            registration_client, outbox, organization_name="Second Org", username="second.admin", email="b@example.com"
        )
    )["organization_id"]
    assert first != second
    codes = [row[0] for row in owner_query("SELECT organization_code FROM organizations")]
    assert len(codes) == 4 and len(set(codes)) == 4  # includes the two seeded organizations
    assert all(ORG_ID.fullmatch(c) for c in codes)

    with pytest.raises(psycopg.Error, match="permanent"):
        owner_execute(
            "UPDATE organizations SET organization_code = 'AAAAAA00000000000' WHERE organization_code = %(c)s",
            {"c": first},
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner_execute(
            "INSERT INTO organizations (id, name, organization_code) VALUES (gen_random_uuid(), 'Copy', %(c)s)",
            {"c": first},
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_execute(
            "INSERT INTO organizations (id, name, organization_code) VALUES (gen_random_uuid(), 'Bad', 'ORG-123')"
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        owner_execute("INSERT INTO organizations (id, name) VALUES (gen_random_uuid(), 'LAKESIDE institute')")


@pytest.mark.parametrize(
    ("code", "accepted"),
    [
        ("QWERTY12345678901", True),
        ("ORG-12345678901", True),  # issued before migration 0005; stays valid
        ("qwerty12345678901", False),
        ("QWERTY1234567890", False),
        ("QWERTYU12345678901", False),
        ("QWE-RTY1234567890", False),
        ("ORG-1234567890", False),
    ],
)
def test_database_accepts_only_the_current_and_the_earlier_organization_id_formats(
    seed: Seed, owner_execute, code: str, accepted: bool
) -> None:
    for sql in (
        "INSERT INTO organizations (id, name, organization_code) VALUES (%(id)s, %(n)s, %(c)s)",
        """INSERT INTO organization_registrations (id, organization_code, status, expires_at)
           VALUES (%(id)s, %(c)s, 'EXPIRED', now())""",
    ):
        params = {"id": uuid4(), "n": f"Format test {uuid4().hex[:8]}", "c": code}
        if accepted:
            owner_execute(sql, params)
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                owner_execute(sql, params)


async def test_registration_reserved_before_the_format_change_keeps_its_organization_id(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    """A registration submitted before migration 0005 reserved an ORG- ID. Completing it after
    the change must keep that ID, not generate a new one."""
    registration_id = await submit(registration_client)
    owner_execute(
        "UPDATE organization_registrations SET organization_code = 'ORG-12345678901' WHERE id = %(r)s",
        {"r": registration_id},
    )
    done = await verify(registration_client, registration_id, outbox.last_code())
    assert done.status_code == 200, done.text
    assert done.json()["organization_id"] == "ORG-12345678901"
    assert one(owner_query, "SELECT name FROM organizations WHERE organization_code = 'ORG-12345678901'") == (
        "Lakeside Institute"
    )


async def test_main_admin_ids_are_unique_and_well_formed(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    first = (await register(registration_client, outbox))["main_admin"]["user_id"]
    second = (
        await register(
            registration_client, outbox, organization_name="Second Org", username="second.admin", email="b@example.com"
        )
    )["main_admin"]["user_id"]
    assert first != second and STAFF_ID.fullmatch(first) and STAFF_ID.fullmatch(second)

    org_id, campus_id = one(
        owner_query, "SELECT organization_id, campus_id FROM users WHERE display_id = %(d)s", d=first
    )

    def add_user(role: str, display_id: str) -> None:
        owner_execute(
            """INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id)
               VALUES (%(id)s, %(o)s, %(c)s, %(role)s, 'ACTIVE', 'Test User', %(email)s, %(d)s)""",
            {
                "id": uuid4(),
                "o": org_id,
                "c": campus_id,
                "role": role,
                "email": f"{uuid4().hex}@x.org",
                "d": display_id,
            },
        )

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_user("ADMIN", first)
    with pytest.raises(psycopg.errors.CheckViolation):
        add_user("ADMIN", first.lower())
    with pytest.raises(psycopg.errors.CheckViolation):
        add_user("GUARD", "ABCDEF12345678901")  # the new format is for Admins and Supervisors only
    add_user("ADMIN", "ADM-12345678901")  # the original format stays valid
    add_user("SUPERVISOR", "ABCDEF12345678901")


# --- Email delivery, expiry, unknown registrations, request limits -----------------------------


async def test_without_an_email_provider_registration_answers_503_and_stores_nothing(
    seed: Seed, migrated_database: DatabaseUrls, owner_query
) -> None:
    async with _client_for(migrated_database) as client:  # EMAIL_DELIVERY_MODE defaults to "none"
        response = await client.post(BASE, json=form())
    assert response.status_code == 503 and error_code(response) == "EMAIL_DELIVERY_NOT_CONFIGURED"
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0


async def test_when_the_provider_refuses_the_email_nothing_is_stored(
    seed: Seed, migrated_database: DatabaseUrls, owner_query
) -> None:
    async with _client_for(migrated_database, email_sender=FakeEmailSender(fail=True)) as client:
        response = await client.post(BASE, json=form())
    assert response.status_code == 502 and error_code(response) == "EMAIL_DELIVERY_FAILED"
    assert count(owner_query, "SELECT 1 FROM organization_registrations") == 0
    assert count(owner_query, "SELECT 1 FROM email_otp_challenges") == 0


async def test_expired_registration_is_refused_and_its_details_erased(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    def expire(registration_id: str) -> None:
        owner_execute(
            """UPDATE organization_registrations
                  SET created_at = now() - interval '2 hours', expires_at = now() - interval '1 second'
                WHERE id = %(r)s""",
            {"r": registration_id},
        )

    def state(registration_id: str) -> tuple[Any, ...]:
        return one(
            owner_query,
            """SELECT status, organization_code,
                      num_nonnulls(organization_name, admin_full_name, username, email, phone, password_hash,
                                   recipient_digest)
                 FROM organization_registrations WHERE id = %(r)s""",
            r=registration_id,
        )

    registration_id = await submit(registration_client)
    code = outbox.last_code()
    reserved = state(registration_id)[1]
    expire(registration_id)
    response = await verify(registration_client, registration_id, code)
    assert response.status_code == 410 and error_code(response) == "REGISTRATION_EXPIRED"
    assert state(registration_id) == ("EXPIRED", reserved, 0)  # the Organization ID stays reserved
    assert (await resend(registration_client, registration_id)).status_code == 410

    # Stale registrations are also erased whenever anyone submits a new one.
    stale = await submit(registration_client, organization_name="Second Org", username="second.admin")
    expire(stale)
    await submit(registration_client, organization_name="Third Org", username="third.admin", email="c@example.com")
    assert state(stale)[0::2] == ("EXPIRED", 0)


async def test_unknown_registration_is_not_found(registration_client: AsyncClient) -> None:
    unknown = uuid4()
    for response in (await verify(registration_client, unknown, "123456"), await resend(registration_client, unknown)):
        assert response.status_code == 404 and error_code(response) == "REGISTRATION_NOT_FOUND"


async def test_malformed_code_is_refused_without_counting_an_attempt(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query
) -> None:
    registration_id = await submit(registration_client)
    for bad in ("12345", "abcdef", "1234567", " 123456"):
        assert (await verify(registration_client, registration_id, bad)).status_code == 422
    failed = one(
        owner_query, "SELECT failed_attempts FROM email_otp_challenges WHERE registration_id = %(r)s", r=registration_id
    )
    assert failed == 0


async def test_registration_requests_are_limited_per_client_address(
    seed: Seed, migrated_database: DatabaseUrls
) -> None:
    async with _client_for(
        migrated_database, email_sender=FakeEmailSender(), org_registration_ip_limit_per_hour=2
    ) as client:
        for _ in range(2):
            assert (await resend(client, uuid4())).status_code == 404
        limited = await resend(client, uuid4())
    assert limited.status_code == 429 and error_code(limited) == "TOO_MANY_REQUESTS"
    assert 1 <= int(limited.headers["Retry-After"]) <= 3600


# --- 16: database access stays narrow ----------------------------------------------------------


async def test_app_role_reaches_registration_data_only_through_the_registration_functions(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, app_role_engine, platform_role_engine
) -> None:
    data = await register(registration_client, outbox)
    user_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=data["main_admin"]["user_id"])
    for actor in (None, user_id):  # not signed in, and signed in as the new Main Admin
        for table in ("organization_registrations", "email_otp_challenges", "user_credentials"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
    for helper in ("app_org_registration_expire_stale()", "app_new_organization_code()"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, None, f"SELECT {helper}")
    # The Super Admin's database role cannot use the registration functions at all.
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(
            platform_role_engine, None, "SELECT * FROM app_org_registration_verify_otp(gen_random_uuid(), 'x')"
        )


async def test_database_refuses_to_complete_a_registration_without_a_verified_code(
    registration_client: AsyncClient, owner_query, app_role_engine
) -> None:
    """Even code that skips the OTP step (or a direct database call as the app role) cannot
    create the organization: the completion function itself requires the verified email."""
    registration_id = await submit(registration_client)
    rows, _ = await run_as(
        app_role_engine,
        None,
        """SELECT outcome FROM app_org_registration_complete(
               :r, gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'KQMTZX48271930561')""",
        {"r": UUID(registration_id)},
    )
    assert [tuple(row) for row in rows] == [("EMAIL_NOT_VERIFIED",)]
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'Lakeside Institute'") == 0
    assert count(owner_query, "SELECT 1 FROM users WHERE email = 'maryam@lakeside.example.com'") == 0
