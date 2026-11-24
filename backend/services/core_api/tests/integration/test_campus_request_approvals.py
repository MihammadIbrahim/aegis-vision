"""The Main Admin's approval page (Org Admin page 15; decisions H, I and D-024; migration 0024).

Covers: approval needs the Main Admin's password and a fresh code emailed to them, the confirmation is
single use and only the Main Admin's own; a failed approval does not use it up; rejection needs the
password and erases the applicant's details; request history with status filter, counts and search;
both decisions in the organization audit log (none of the erased details in it).
"""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import platform_auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_campus_request_applications import (
    QUEUE,
    apply_and_verify,
    approve,
    confirm_approval,
    queue,
    reject,
)
from tests.integration.test_organization_admin_login import bearer, register_admin, sign_in

HISTORY = f"{QUEUE}/history"


@pytest.fixture
async def main_admin(registration_client: AsyncClient, outbox: FakeEmailSender) -> dict[str, Any]:
    main = await register_admin(registration_client, outbox)
    main["token"] = await sign_in(registration_client, outbox, main)
    return main


async def two_requests(client: AsyncClient, outbox: FakeEmailSender, main: dict[str, Any]) -> list[dict[str, Any]]:
    await apply_and_verify(client, outbox, main)
    await apply_and_verify(
        client, outbox, main, email="nida@south.example.com", username="nida.a", campus_name="South Campus",
        admin_full_name="Nida Aslam",
    )  # fmt: skip
    return sorted(await queue(client, main["token"]), key=lambda r: r["proposed_campus_name"])


async def history(client: AsyncClient, main: dict[str, Any], **params: Any) -> dict[str, Any]:
    response = await client.get(HISTORY, params=params, headers=bearer(main["token"]))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_approval_needs_password_and_a_fresh_code_used_once(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_query
) -> None:
    client, token = registration_client, main_admin["token"]
    north, south = await two_requests(client, outbox, main_admin)

    # The password first: wrong -> 403, nothing emailed.
    sent = len(outbox.messages)
    wrong = await client.post(
        f"{QUEUE}/authorization", json={"current_password": "Wrong-pass-2026"}, headers=bearer(token)
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    assert len(outbox.messages) == sent

    # A started but unverified confirmation cannot approve.
    started = await client.post(
        f"{QUEUE}/authorization", json={"current_password": main_admin["password"]}, headers=bearer(token)
    )
    assert started.status_code == 200 and started.json()["status"] == "OTP_REQUIRED"
    assert "approving a campus request" in outbox.messages[-1].text_body
    unverified = await client.post(
        f"{QUEUE}/{north['id']}/approve", json={"authorization_id": started.json()["id"]}, headers=bearer(token)
    )
    assert unverified.status_code == 403 and unverified.json()["code"] == "AUTHORIZATION_NOT_VERIFIED"
    missing = await client.post(f"{QUEUE}/{north['id']}/approve", json={}, headers=bearer(token))
    assert missing.status_code == 422

    authorization = await confirm_approval(client, outbox, main_admin)
    approved = await client.post(
        f"{QUEUE}/{north['id']}/approve", json={"authorization_id": authorization}, headers=bearer(token)
    )
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    again = await client.post(
        f"{QUEUE}/{south['id']}/approve", json={"authorization_id": authorization}, headers=bearer(token)
    )
    assert again.status_code == 409 and again.json()["code"] == "AUTHORIZATION_USED"
    assert owner_query("SELECT status FROM campus_registration_requests WHERE id = %(id)s", {"id": south["id"]}) == [
        ("PENDING",)
    ]

    [(action, target_type, details)] = owner_query(
        "SELECT action, target_type, metadata FROM organization_audit_logs WHERE target_id = %(id)s",
        {"id": north["id"]},
    )
    assert (action, target_type) == ("CAMPUS_REQUEST_APPROVED", "CAMPUS_REQUEST")
    assert details["campus_name"] == "North Campus" and details["applicant_name"] == "Hamza Raza"


async def test_a_failed_approval_does_not_use_up_the_confirmation(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_execute
) -> None:
    client, token = registration_client, main_admin["token"]
    north, south = await two_requests(client, outbox, main_admin)
    # Another campus took North's name meanwhile: approving it fails, nothing is created.
    owner_execute(
        "INSERT INTO campuses (id, organization_id, name, status) SELECT gen_random_uuid(), organization_id, "
        "'North Campus', 'ACTIVE' FROM campus_registration_requests WHERE id = %(id)s",
        {"id": north["id"]},
    )
    authorization = await confirm_approval(client, outbox, main_admin)
    taken = await client.post(
        f"{QUEUE}/{north['id']}/approve", json={"authorization_id": authorization}, headers=bearer(token)
    )
    assert taken.status_code == 409 and taken.json()["code"] == "CAMPUS_NAME_TAKEN"
    # ...so the same confirmation still approves another request.
    ok = await client.post(
        f"{QUEUE}/{south['id']}/approve", json={"authorization_id": authorization}, headers=bearer(token)
    )
    assert ok.status_code == 200, ok.text


async def test_another_admins_confirmation_cannot_be_used(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_execute
) -> None:
    client = registration_client
    [north, _] = await two_requests(client, outbox, main_admin)
    other = await register_admin(
        client,
        outbox,
        organization_name="Other Institute",
        email="owner@other.example.com",
        username="other.owner",
        phone_number="+92 300 765-4321",
    )
    other["token"] = await sign_in(client, outbox, other)
    foreign = await confirm_approval(client, outbox, other)
    refused = await client.post(
        f"{QUEUE}/{north['id']}/approve", json={"authorization_id": foreign}, headers=bearer(main_admin["token"])
    )
    assert refused.status_code == 404 and refused.json()["code"] == "AUTHORIZATION_NOT_FOUND"
    # And the other organization's Main Admin cannot see or decide this organization's request.
    cross = await client.post(
        f"{QUEUE}/{north['id']}/approve", json={"authorization_id": foreign}, headers=bearer(other["token"])
    )
    assert cross.status_code == 404
    assert (await history(client, other))["items"] == []


async def test_rejection_needs_the_password_and_erases_the_applicant_details(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], owner_query
) -> None:
    client = registration_client
    north, _ = await two_requests(client, outbox, main_admin)

    wrong = await reject(client, main_admin, north["id"], password="Wrong-pass-2026")
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    assert owner_query(
        "SELECT status, applicant_name FROM campus_registration_requests WHERE id = %(id)s", {"id": north["id"]}
    ) == [("PENDING", "Hamza Raza")]
    missing = await client.post(f"{QUEUE}/{north['id']}/reject", json={}, headers=bearer(main_admin["token"]))
    assert missing.status_code == 422

    rejected = await reject(client, main_admin, north["id"])
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "REJECTED" and body["applicant_name"] is None and body["applicant_email"] is None
    [row] = owner_query(
        "SELECT proposed_campus_name, applicant_name, applicant_email, applicant_username, applicant_phone, "
        "details_erased_at IS NOT NULL FROM campus_registration_requests WHERE id = %(id)s",
        {"id": north["id"]},
    )
    assert row == (None, None, None, None, None, True)
    assert owner_query(
        "SELECT count(*) FROM campus_request_credentials WHERE request_id = %(id)s", {"id": north["id"]}
    ) == [(0,)]
    [(action, details)] = owner_query(
        "SELECT action, metadata FROM organization_audit_logs WHERE target_id = %(id)s", {"id": north["id"]}
    )
    assert action == "CAMPUS_REQUEST_REJECTED"
    assert not any(word in str(details).lower() for word in ("hamza", "north", "321"))  # decision I


async def test_history_filters_counts_and_search(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any]
) -> None:
    client = registration_client
    north, south = await two_requests(client, outbox, main_admin)
    await apply_and_verify(
        client, outbox, main_admin, email="omar@east.example.com", username="omar.k", campus_name="East Campus",
        admin_full_name="Omar Khan",
    )  # fmt: skip
    assert (await approve(client, outbox, main_admin, north["id"])).status_code == 200
    assert (await reject(client, main_admin, south["id"])).status_code == 200

    everything = await history(client, main_admin)
    assert everything["counts"] == {"PENDING": 1, "APPROVED": 1, "REJECTED": 1} and everything["total"] == 3
    submitted = [i["submitted_at"] for i in everything["items"]]
    assert submitted == sorted(submitted, reverse=True)

    approved = await history(client, main_admin, status="APPROVED")
    [item] = approved["items"]
    assert item["proposed_campus_name"] == "North Campus" and item["reviewed_by_name"]
    assert item["details_erased_at"] is None and approved["total"] == 1

    [erased] = (await history(client, main_admin, status="REJECTED"))["items"]
    assert erased["applicant_name"] is None and erased["details_erased_at"] and erased["reviewed_at"]

    assert [i["applicant_name"] for i in (await history(client, main_admin, q="omar"))["items"]] == ["Omar Khan"]
    assert [i["status"] for i in (await history(client, main_admin, q="north"))["items"]] == ["APPROVED"]
    assert (await history(client, main_admin, q="nida"))["items"] == []  # erased by the rejection
    assert (await history(client, main_admin, q="%"))["items"] == []  # wildcards are literal
    paged = await history(client, main_admin, limit=1, offset=1)
    assert len(paged["items"]) == 1 and paged["total"] == 3


async def test_only_the_main_admin_and_never_the_super_admin(
    registration_client: AsyncClient, outbox: FakeEmailSender, main_admin: dict[str, Any], super_admin
) -> None:
    for path in (HISTORY, QUEUE):
        refused = await registration_client.get(path, headers=platform_auth_header(super_admin))
        assert refused.status_code == 403 and refused.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED"
    started = await registration_client.post(
        f"{QUEUE}/authorization", json={"current_password": "x"}, headers=platform_auth_header(super_admin)
    )
    assert started.status_code == 403
