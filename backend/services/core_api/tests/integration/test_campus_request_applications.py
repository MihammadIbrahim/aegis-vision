""" "Already Registered Organization?" campus requests (migration 0021), end to end against PostgreSQL.

Covers: submit with the exact organization name and Organization ID, email code, nothing in the
Main Admin's queue before verification, the verified request with the applicant's details,
"approval pending" at sign-in (decision L), approval creating a working Admin account for a new
campus, rejection and the 72-hour expiry (decision I), conflicts, code limits, and direct
database access rules.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.exc import DBAPIError

from tests.conftest import run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import bearer, register_admin, sign_in, start, wrong

APPLY = "/api/v1/auth/campus-requests"
QUEUE = "/api/v1/campus-requests"
LOGIN = "/api/v1/auth/organization-admin/login"
PASSWORD = "Northside-pass-2026"


def application(main: dict[str, Any], **changes: str) -> dict[str, str]:
    body = {
        "organization_name": main["organization_name"],
        "organization_id": main["organization_id"],
        "campus_name": "North Campus",
        "admin_full_name": "Hamza Raza",
        "username": "hamza.r",
        "email": "hamza@north.example.com",
        "phone_number": "+92 321 765 4321",
        "password": PASSWORD,
    }
    body.update(changes)
    return body


async def apply(client: AsyncClient, main: dict[str, Any], **changes: str) -> str:
    response = await client.post(APPLY, json=application(main, **changes))
    assert response.status_code == 201, response.text
    return response.json()["application_id"]


async def verify(client: AsyncClient, application_id: str, code: str) -> Response:
    return await client.post(f"{APPLY}/{application_id}/verify", json={"otp": code})


async def apply_and_verify(client: AsyncClient, outbox: FakeEmailSender, main: dict[str, Any], **changes: str):
    application_id = await apply(client, main, **changes)
    done = await verify(client, application_id, outbox.last_code(changes.get("email", "hamza@north.example.com")))
    assert done.status_code == 200, done.text
    return done.json()


async def confirm_approval(client: AsyncClient, outbox: FakeEmailSender, main: dict[str, Any]) -> str:
    """The Main Admin's password, then the code emailed to them (decision H): one approval may follow."""
    started = await client.post(
        f"{QUEUE}/authorization", json={"current_password": main["password"]}, headers=bearer(main["token"])
    )
    assert started.status_code == 200, started.text
    authorization_id = started.json()["id"]
    done = await client.post(
        f"{QUEUE}/authorization/{authorization_id}/verify",
        json={"otp": outbox.last_code(main["email"])},
        headers=bearer(main["token"]),
    )
    assert done.status_code == 200, done.text
    return authorization_id


async def approve(client: AsyncClient, outbox: FakeEmailSender, main: dict[str, Any], request_id: str) -> Response:
    authorization_id = await confirm_approval(client, outbox, main)
    return await client.post(
        f"{QUEUE}/{request_id}/approve", json={"authorization_id": authorization_id}, headers=bearer(main["token"])
    )


async def reject(client: AsyncClient, main: dict[str, Any], request_id: str, password: str | None = None) -> Response:
    body = {"current_password": password or main["password"]}
    return await client.post(f"{QUEUE}/{request_id}/reject", json=body, headers=bearer(main["token"]))


def applicant(main: dict[str, Any], email: str = "hamza@north.example.com") -> dict[str, Any]:
    return {**main, "email": email, "password": PASSWORD}


async def queue(client: AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get(QUEUE, headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def main_admin(registration_client: AsyncClient, outbox: FakeEmailSender) -> dict[str, Any]:
    main = await register_admin(registration_client, outbox)
    main["token"] = await sign_in(registration_client, outbox, main)
    return main


async def test_verified_request_waits_for_the_main_admin_then_becomes_a_working_campus_admin(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_query
) -> None:
    client, token = registration_client, main_admin["token"]
    application_id = await apply(client, main_admin)
    [message] = [m for m in outbox.messages if m.to_email == "hamza@north.example.com"]
    assert "North Campus" not in message.text_body  # fixed wording only
    assert await queue(client, token) == []  # nothing before the email is verified

    done = await verify(client, application_id, outbox.last_code("hamza@north.example.com"))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "PENDING_APPROVAL" and body["campus_name"] == "North Campus"
    assert body["organization_name"] == "Lakeside Institute"

    [request] = await queue(client, token)
    assert request["applicant_username"] == "hamza.r" and request["applicant_phone"] == "+923217654321"
    assert request["email_verified"] is True and request["email_verified_at"] and request["decision_deadline"]
    assert "password" not in str(request).lower()

    # Decision L: right password before approval -> "approval pending", no code is sent.
    sent_before = len(outbox.messages)
    pending = await start(client, applicant(main_admin))
    assert pending.status_code == 403 and pending.json()["code"] == "ADMIN_APPROVAL_PENDING"
    assert len(outbox.messages) == sent_before
    wrong_password = await start(client, applicant(main_admin), password="Wrong-pass-2026")
    assert wrong_password.status_code == 401 and wrong_password.json()["code"] == "INVALID_CREDENTIALS"

    approved = await approve(client, outbox, main_admin, request["id"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    # The applicant now signs in normally and manages only the new campus.
    new_token = await sign_in(client, outbox, applicant(main_admin))
    dashboard = (await client.get("/api/v1/organization-admin/dashboard", headers=bearer(new_token))).json()
    assert dashboard["campus"]["name"] == "North Campus"
    assert dashboard["admin"]["is_main_admin"] is False
    assert dashboard["summary"]["pending_campus_requests"] is None  # not the Main Admin
    assert owner_query("SELECT count(*) FROM campus_request_credentials")[0][0] == 0
    [(username, phone, first)] = owner_query(
        "SELECT username, phone, is_first_admin FROM users WHERE email = 'hamza@north.example.com'"
    )
    assert (username, phone, first) == ("hamza.r", "+923217654321", False)
    [(details,)] = owner_query(
        "SELECT num_nonnulls(full_name, username, email, password_hash) FROM campus_request_submissions"
    )
    assert details == 0  # the application's own copy is erased


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_id": "ZZZZZZ00000000000"},
        {"organization_name": "lakeside institute"},  # matched exactly, like sign-in
        {"organization_name": "Lakeside  Institute"},
    ],
)
async def test_organization_must_match_its_exact_name_and_id(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], changes: dict[str, str]
) -> None:
    sent_before = len(outbox.messages)
    response = await registration_client.post(APPLY, json=application(main_admin, **changes))
    assert response.status_code == 404 and response.json()["code"] == "ORGANIZATION_NOT_MATCHED"
    assert len(outbox.messages) == sent_before


async def test_conflicts_are_refused_and_create_nothing(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_query
) -> None:
    client = registration_client
    taken = await client.post(APPLY, json=application(main_admin, campus_name="main campus"))
    assert taken.status_code == 409 and taken.json()["code"] == "CAMPUS_NAME_TAKEN"
    username = await client.post(APPLY, json=application(main_admin, username="MARYAM.S"))
    assert username.status_code == 409 and username.json()["code"] == "USERNAME_TAKEN"

    # An email that already has an account is found only after its code (no email enumeration).
    application_id = await apply(client, main_admin, email=main_admin["email"])
    response = await verify(client, application_id, outbox.last_code(main_admin["email"]))
    assert response.status_code == 409 and response.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    created = owner_query(
        "SELECT count(*) FROM campus_registration_requests WHERE applicant_email = %(e)s", {"e": main_admin["email"]}
    )
    assert created[0][0] == 0

    await apply_and_verify(client, outbox, main_admin)
    again = await client.post(APPLY, json=application(main_admin, campus_name="South Campus", username="hamza.2"))
    application_id = again.json()["application_id"]
    response = await verify(client, application_id, outbox.last_code("hamza@north.example.com"))
    assert response.status_code == 409 and response.json()["code"] == "EMAIL_ALREADY_PENDING"


async def test_codes_are_counted_and_limited(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any]
) -> None:
    client = registration_client
    application_id = await apply(client, main_admin)
    code = outbox.last_code("hamza@north.example.com")
    bad = await verify(client, application_id, wrong(code))
    assert bad.status_code == 400 and bad.json()["code"] == "OTP_INVALID"
    cooldown = await client.post(f"{APPLY}/{application_id}/resend-otp")
    assert cooldown.status_code == 429 and cooldown.json()["code"] == "OTP_RESEND_COOLDOWN"
    assert (await verify(client, application_id, code)).status_code == 200
    again = await verify(client, application_id, code)
    assert again.status_code == 409 and again.json()["code"] == "CAMPUS_REQUEST_ALREADY_VERIFIED"


async def test_rejected_or_expired_requests_cannot_sign_in_or_be_approved(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_query, owner_execute
) -> None:
    client, token = registration_client, main_admin["token"]
    await apply_and_verify(client, outbox, main_admin)
    [request] = await queue(client, token)
    rejected = await reject(client, main_admin, request["id"])
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert owner_query("SELECT count(*) FROM campus_request_credentials")[0][0] == 0
    refused = await start(client, applicant(main_admin))
    assert refused.status_code == 401 and refused.json()["code"] == "INVALID_CREDENTIALS"

    await apply_and_verify(client, outbox, main_admin, email="nida@north.example.com", username="nida.a")
    owner_execute(
        "UPDATE campus_registration_requests SET email_verified_at = now() - interval '73 hours' "
        "WHERE applicant_email = 'nida@north.example.com'"
    )
    [(expired_id,)] = owner_query(
        "SELECT id FROM campus_registration_requests WHERE applicant_email = 'nida@north.example.com'"
    )
    assert await queue(client, token) == []
    gone = await approve(client, outbox, main_admin, expired_id)
    assert gone.status_code == 410 and gone.json()["code"] == "CAMPUS_REQUEST_EXPIRED"
    late = await start(client, applicant(main_admin, "nida@north.example.com"))
    assert late.status_code == 401


async def test_app_role_reaches_applications_only_through_the_functions(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    main_admin: dict[str, Any],
    app_role_engine,
    platform_role_engine,
) -> None:
    await apply_and_verify(registration_client, outbox, main_admin)
    for table in ("campus_request_submissions", "campus_request_credentials"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, None, f"SELECT * FROM {table}")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, None, "SELECT app_campus_requests_expire_stale()")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(
            platform_role_engine,
            None,
            "SELECT * FROM app_campus_request_verify_otp(gen_random_uuid(), 'x', gen_random_uuid())",
        )
