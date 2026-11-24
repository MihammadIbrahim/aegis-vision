"""Main Admin ownership transfer, complete campus erasure on Admin deletion, the optional export, and
the Super Admin's deleted-organization names, and the Main Admin's ownership history (migrations 0010-0012)."""

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from src.application.interfaces.i_campus_media_storage import MediaCleanupFailed
from src.domain.value_objects.system_id import (
    generate_guard_id,
    generate_staff_id,
    generate_supervisor_id,
)
from src.infrastructure.security.password import Argon2PasswordHasher
from src.infrastructure.storage.local_ssd_storage import LocalCampusMediaStorage
from tests.conftest import (
    SUPER_ADMIN_PASSWORD,
    SUPER_ADMIN_USERNAME,
    DatabaseUrls,
    _client_for,
    auth_header,
    run_as,
)
from tests.fakes import FakeEmailSender
from tests.integration.test_organization_admin_login import register_admin
from tests.integration.test_organization_admin_login import start as start_sign_in
from tests.seed import Seed

ACCOUNT = "/api/v1/organization-admin/account"
TRANSFERS = "/api/v1/organization-admin/ownership-transfers"
PASSWORD = "Lakeside-pass-2026"
OTHER_PASSWORD = "Campus-admin-pass-2026"


def one(owner_query, sql: str, **params: Any) -> Any:
    rows = owner_query(sql, params)
    assert len(rows) == 1, rows
    return rows[0][0] if len(rows[0]) == 1 else rows[0]


def count(owner_query, sql: str, **params: Any) -> int:
    return one(owner_query, f"SELECT count(*) FROM ({sql}) q", **params)


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


def add_user(
    conn, org: UUID, campus: UUID, role: str, name: str, email: str, *, password: str | None = None
) -> dict[str, Any]:
    display = {"ADMIN": generate_staff_id, "SUPERVISOR": generate_supervisor_id, "GUARD": generate_guard_id}[role]()
    user_id = uuid4()
    conn.execute(
        """INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id, username)
           VALUES (%s, %s, %s, %s, 'ACTIVE', %s, %s, %s, %s)""",
        (user_id, org, campus, role, name, email, display, "u." + email.split("@")[0]),
    )
    if password:
        conn.execute(
            "INSERT INTO user_credentials (user_id, password_hash) VALUES (%s, %s)",
            (user_id, Argon2PasswordHasher().hash(password)),
        )
    return {"id": user_id, "display_id": display, "email": email, "password": password, "name": name}


@pytest.fixture
async def ucp(registration_client: AsyncClient, outbox: FakeEmailSender, migrated_database: DatabaseUrls, owner_query):
    """An organization with three campuses:
    Main Campus — Admin A (Main Admin), Supervisor SA, Guard GA, a camera
    Campus B    — Admin B, Supervisor SB, Guard GB, a camera
    Campus C    — Admin C, Guard GC, a camera
    """
    a = await register_admin(registration_client, outbox, organization_name="UCP")
    a_id, org, campus_a = one(
        owner_query, "SELECT id, organization_id, campus_id FROM users WHERE display_id = %(d)s", d=a["display_id"]
    )
    data: dict[str, Any] = {
        "org": org,
        "campus_a": campus_a,
        "campus_b": uuid4(),
        "campus_c": uuid4(),
        "a": {**a, "id": a_id, "name": "Maryam Siddiqui"},
    }
    with psycopg.connect(migrated_database.owner_test) as conn:
        for key, name in (("campus_b", "Campus B"), ("campus_c", "Campus C")):
            conn.execute(
                "INSERT INTO campuses (id, organization_id, name, status) VALUES (%s, %s, %s, 'ACTIVE')",
                (data[key], org, name),
            )
        data["sa"] = add_user(
            conn, org, campus_a, "SUPERVISOR", "Sana A", "sa@ucp.example.com", password=OTHER_PASSWORD
        )
        data["ga"] = add_user(conn, org, campus_a, "GUARD", "Gul A", "ga@ucp.example.com", password=OTHER_PASSWORD)
        data["b"] = add_user(
            conn, org, data["campus_b"], "ADMIN", "Bilal B", "b@ucp.example.com", password=OTHER_PASSWORD
        )
        data["sb"] = add_user(
            conn, org, data["campus_b"], "SUPERVISOR", "Saad B", "sb@ucp.example.com", password=OTHER_PASSWORD
        )
        data["gb"] = add_user(
            conn, org, data["campus_b"], "GUARD", "Gohar B", "gb@ucp.example.com", password=OTHER_PASSWORD
        )
        data["c"] = add_user(
            conn, org, data["campus_c"], "ADMIN", "Chand C", "c@ucp.example.com", password=OTHER_PASSWORD
        )
        data["gc"] = add_user(
            conn, org, data["campus_c"], "GUARD", "Ghazi C", "gc@ucp.example.com", password=OTHER_PASSWORD
        )
        for campus in (campus_a, data["campus_b"], data["campus_c"]):
            conn.execute(
                "INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (%s, %s, %s, 'Gate')",
                (uuid4(), org, campus),
            )
        # Organization history: a campus request that Admin A approved for Campus C.
        conn.execute(
            """INSERT INTO campus_registration_requests (id, organization_id, proposed_campus_name, applicant_name,
                   applicant_email, email_verified, status, reviewed_by, reviewed_at, approved_campus_id)
               VALUES (%s, %s, 'Campus C', 'Chand C', 'c@ucp.example.com', true, 'APPROVED', %s, now(), %s)""",
            (uuid4(), org, a_id, data["campus_c"]),
        )
    return data


def headers(user: dict[str, Any]) -> dict[str, str]:
    return auth_header(user["id"])


async def transfer_to(
    client: AsyncClient, outbox: FakeEmailSender, sender: dict[str, Any], recipient: dict[str, Any]
) -> str:
    started = await client.post(
        f"{ACCOUNT}/ownership-transfer",
        json={"recipient_admin_id": recipient["display_id"], "current_password": sender["password"]},
        headers=headers(sender),
    )
    assert started.status_code == 200, started.text
    transfer = started.json()["id"]
    done = await client.post(
        f"{ACCOUNT}/ownership-transfer/{transfer}/verify",
        json={"otp": outbox.last_code(sender["email"])},
        headers=headers(sender),
    )
    assert done.status_code == 200, done.text
    return transfer


async def start_deletion(client: AsyncClient, user: dict[str, Any], **changes: str):  # type: ignore[no-untyped-def]
    body = {"admin_id": user["display_id"], "current_password": user["password"], **changes}
    return await client.post(f"{ACCOUNT}/deletion", json=body, headers=headers(user))


async def delete_account(client: AsyncClient, outbox: FakeEmailSender, user: dict[str, Any]) -> dict[str, Any]:
    started = await start_deletion(client, user)
    assert started.status_code == 200, started.text
    done = await client.post(
        f"{ACCOUNT}/deletion/{started.json()['id']}/verify",
        json={"otp": outbox.last_code(user["email"])},
        headers=headers(user),
    )
    assert done.status_code == 200, done.text
    return done.json()


def campus_counts(owner_query, campus: UUID) -> tuple[int, int, int]:
    return (
        count(owner_query, "SELECT 1 FROM campuses WHERE id = %(c)s", c=campus),
        count(owner_query, "SELECT 1 FROM users WHERE campus_id = %(c)s", c=campus),
        count(owner_query, "SELECT 1 FROM cameras WHERE campus_id = %(c)s", c=campus),
    )


def put_media(root: Path, org: UUID, campus: UUID) -> Path:
    """A stand-in campus file where campus media is kept (CCTV_STORAGE_ROOT/<org>/<campus>/)."""
    folder = root / str(org) / str(campus) / "recordings"
    folder.mkdir(parents=True)
    (folder / "gate.mp4").write_bytes(b"not a real video")
    return folder.parent


def traces(owner_query, value: str) -> list[str]:
    """Tables with any row whose text mentions `value`."""
    tables = [r[0] for r in owner_query("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    return [t for t in tables if count(owner_query, f"SELECT 1 FROM {t} x WHERE x::text LIKE %(v)s", v=f"%{value}%")]


def main_admin(owner_query, org: UUID) -> UUID:
    return one(owner_query, "SELECT id FROM users WHERE organization_id = %(o)s AND is_first_admin", o=org)


# --- Case A: one Admin, one campus ----------------------------------------------------------------


async def test_single_admin_single_campus_erases_the_campus_and_the_organization(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    migrated_database: DatabaseUrls,
    owner_query,
    seed: Seed,
    storage_root: Path,
) -> None:
    a = await register_admin(registration_client, outbox, organization_name="UMT")
    a_id, org, campus, code = one(
        owner_query,
        """SELECT u.id, u.organization_id, u.campus_id, o.organization_code
             FROM users u JOIN organizations o ON o.id = u.organization_id WHERE u.display_id = %(d)s""",
        d=a["display_id"],
    )
    with psycopg.connect(migrated_database.owner_test) as conn:
        add_user(conn, org, campus, "SUPERVISOR", "Sup", "sup@umt.example.com", password=OTHER_PASSWORD)
        add_user(conn, org, campus, "GUARD", "Grd", "grd@umt.example.com", password=OTHER_PASSWORD)
        conn.execute(
            "INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (%s, %s, %s, 'Gate')",
            (uuid4(), org, campus),
        )
    media = put_media(storage_root, org, campus)
    seeded_before = count(
        owner_query, "SELECT 1 FROM users WHERE organization_id IN (%(a)s, %(b)s)", a=seed.org1, b=seed.org2
    )
    admin = {**a, "id": a_id}
    audit_before = count(owner_query, "SELECT 1 FROM platform_audit_logs WHERE action = 'ADMIN_ACCOUNT_DELETED'")

    status = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(admin))).json()
    assert status["is_main_admin"] and status["can_delete"] and status["transfer_candidates"] == []
    assert status["campus_will_be_deleted"] and status["organization_will_be_deleted"]

    result = await delete_account(registration_client, outbox, admin)
    assert result == {"status": "DELETED", "campus_deleted": True, "organization_deleted": True}
    # Nothing in the operational database mentions the organization, its campus or its Admin.
    for trace in (str(org), str(campus), code, a["display_id"], a["email"], str(a_id)):
        assert traces(owner_query, trace) == [], trace
    assert count(owner_query, "SELECT 1 FROM organizations WHERE name = 'UMT'") == 0
    assert (
        count(owner_query, "SELECT 1 FROM user_credentials c LEFT JOIN users u ON u.id = c.user_id WHERE u.id IS NULL")
        == 0
    )
    assert not media.exists() and not (storage_root / str(org)).exists()
    assert list((storage_root / ".deleting").iterdir()) == []
    assert one(owner_query, "SELECT organization_name FROM deleted_organization_names") == "UMT"
    # Only the name is kept: no audit entry is written for the deleted organization (migration 0012).
    # (The audit table is shared by all tests, so compare with the count before the deletion.)
    assert count(owner_query, "SELECT 1 FROM platform_audit_logs WHERE action = 'ADMIN_ACCOUNT_DELETED'") == (
        audit_before
    )
    # The organization cannot be used any more: no sign-in, no API access, no recovery.
    assert (
        await registration_client.get("/api/v1/organization-admin/dashboard", headers=headers(admin))
    ).status_code == 401
    login = await start_sign_in(registration_client, a)
    assert login.status_code == 401 and code not in login.text
    # Other organizations are untouched.
    assert count(
        owner_query, "SELECT 1 FROM users WHERE organization_id IN (%(a)s, %(b)s)", a=seed.org1, b=seed.org2
    ) == (seeded_before)


async def test_admin_id_password_and_codes_are_checked(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, owner_execute
) -> None:
    b = ucp["b"]
    mismatch = await start_deletion(registration_client, b, admin_id=ucp["c"]["display_id"])
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "ADMIN_ID_MISMATCH"
    bad_password = await start_deletion(registration_client, b, current_password="Wrong-pass-2026")
    assert bad_password.status_code == 403 and bad_password.json()["code"] == "CURRENT_PASSWORD_INCORRECT"

    started = await start_deletion(registration_client, b)
    request_id = started.json()["id"]
    code = outbox.last_code(b["email"])
    invalid = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": wrong(code)}, headers=headers(b)
    )
    assert invalid.status_code == 400 and invalid.json()["code"] == "OTP_INVALID"
    owner_execute(
        "UPDATE auth_otp_challenges SET issued_at = now() - interval '10 minutes', "
        "expires_at = now() - interval '1 second' WHERE subject_id = %(r)s",
        {"r": request_id},
    )
    expired = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": code}, headers=headers(b)
    )
    assert expired.status_code == 400 and expired.json()["code"] == "OTP_EXPIRED"
    assert campus_counts(owner_query, ucp["campus_b"]) == (1, 3, 1)  # nothing deleted

    # A new request needs a new code; the old one does not work for it.
    again = await start_deletion(registration_client, b)
    reused = await registration_client.post(
        f"{ACCOUNT}/deletion/{again.json()['id']}/verify", json={"otp": code}, headers=headers(b)
    )
    if outbox.last_code(b["email"]) != code:
        assert reused.status_code == 400
    assert (
        await registration_client.post(
            f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": code}, headers=headers(b)
        )
    ).status_code == 404  # the earlier request was replaced


# --- Case B: Main Admin with other Admins -------------------------------------------------------


async def test_main_admin_must_transfer_ownership_to_an_eligible_admin_first(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], seed: Seed
) -> None:
    a = ucp["a"]
    blocked = await start_deletion(registration_client, a)
    assert blocked.status_code == 409 and blocked.json()["code"] == "OWNERSHIP_TRANSFER_REQUIRED"
    status = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(a))).json()
    assert status["can_delete"] is False and status["blocked_by"] == "OWNERSHIP_TRANSFER_REQUIRED"
    assert status["transfer_candidates"] == [
        {"admin_id": ucp["b"]["display_id"], "full_name": "Bilal B"},
        {"admin_id": ucp["c"]["display_id"], "full_name": "Chand C"},
    ]
    for not_eligible in (ucp["sa"]["display_id"], ucp["gb"]["display_id"], a["display_id"], "ADM-00000000004"):
        response = await registration_client.post(
            f"{ACCOUNT}/ownership-transfer",
            json={"recipient_admin_id": not_eligible, "current_password": a["password"]},
            headers=headers(a),
        )
        assert response.status_code == 422 and response.json()["code"] == "RECIPIENT_NOT_ELIGIBLE"
    # Additional Admins see no candidates and cannot start a transfer.
    b_status = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(ucp["b"]))).json()
    assert b_status["is_main_admin"] is False and b_status["transfer_candidates"] == []
    refused = await registration_client.post(
        f"{ACCOUNT}/ownership-transfer",
        json={"recipient_admin_id": ucp["c"]["display_id"], "current_password": OTHER_PASSWORD},
        headers=headers(ucp["b"]),
    )
    assert refused.status_code == 403 and refused.json()["code"] == "NOT_MAIN_ADMIN"


async def test_pending_or_rejected_transfer_does_not_allow_deletion(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    a, c = ucp["a"], ucp["c"]
    transfer = await transfer_to(registration_client, outbox, a, c)
    transfer_code = outbox.last_code(a["email"])
    pending = await start_deletion(registration_client, a)
    assert pending.status_code == 409 and pending.json()["code"] == "OWNERSHIP_TRANSFER_PENDING"
    latest = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(a))).json()["latest_transfer"]
    assert (
        latest["transfer_id"] == transfer
        and latest["status"] == "PENDING_ACCEPTANCE"
        and latest["to_name"] == "Chand C"
    )
    duplicate = await registration_client.post(
        f"{ACCOUNT}/ownership-transfer",
        json={"recipient_admin_id": ucp["b"]["display_id"], "current_password": a["password"]},
        headers=headers(a),
    )
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "OWNERSHIP_TRANSFER_PENDING"
    replay = await registration_client.post(
        f"{ACCOUNT}/ownership-transfer/{transfer}/verify", json={"otp": transfer_code}, headers=headers(a)
    )
    assert replay.status_code == 409

    incoming = (await registration_client.get(f"{TRANSFERS}/incoming", headers=headers(c))).json()
    assert [i["transfer_id"] for i in incoming] == [transfer] and incoming[0]["from_name"] == "Maryam Siddiqui"
    rejected = await registration_client.post(f"{TRANSFERS}/{transfer}/reject", headers=headers(c))
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert main_admin(owner_query, ucp["org"]) == a["id"]
    after = await start_deletion(registration_client, a)
    assert after.status_code == 409 and after.json()["code"] == "OWNERSHIP_TRANSFER_REQUIRED"
    assert campus_counts(owner_query, ucp["campus_a"]) == (1, 3, 1)
    assert (await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(c))).status_code == 410


async def test_only_the_chosen_recipient_can_decide_and_only_the_sender_can_cancel(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, seed: Seed
) -> None:
    a, b, c = ucp["a"], ucp["b"], ucp["c"]
    transfer = await transfer_to(registration_client, outbox, a, c)
    other = await register_admin(
        registration_client, outbox, organization_name="Other Org", username="other.admin", email="o@example.com"
    )
    other_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=other["display_id"])
    for intruder in (b, a, {"id": other_id}):
        assert (
            await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(intruder))
        ).status_code == 404
        assert (await registration_client.get(f"{TRANSFERS}/incoming", headers=headers(intruder))).json() == []
    assert (
        await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(ucp["sa"]))
    ).status_code == 403  # Supervisors cannot use Admin routes
    for intruder in (c, {"id": other_id}):
        assert (
            await registration_client.delete(f"{ACCOUNT}/ownership-transfer/{transfer}", headers=headers(intruder))
        ).status_code == 404
    assert main_admin(owner_query, ucp["org"]) == a["id"]
    assert (
        await registration_client.delete(f"{ACCOUNT}/ownership-transfer/{transfer}", headers=headers(a))
    ).status_code == 204
    assert (await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(c))).status_code == 410


async def test_accepted_transfer_then_deletion_removes_only_the_senders_campus(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    a, c = ucp["a"], ucp["c"]
    media = {k: put_media(storage_root, ucp["org"], ucp[k]) for k in ("campus_a", "campus_b", "campus_c")}
    transfer = await transfer_to(registration_client, outbox, a, c)
    transfer_code = outbox.last_code(a["email"])
    accepted = await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(c))
    assert accepted.status_code == 200 and accepted.json() == {
        "transfer_id": transfer,
        "status": "ACCEPTED",
        "you_are_main_admin": True,
    }
    assert main_admin(owner_query, ucp["org"]) == c["id"]
    assert count(owner_query, "SELECT 1 FROM users WHERE organization_id = %(o)s AND is_first_admin", o=ucp["org"]) == 1
    # Ownership grants no new campus access.
    c_dashboard = (await registration_client.get("/api/v1/organization-admin/dashboard", headers=headers(c))).json()
    assert c_dashboard["campus"]["name"] == "Campus C" and c_dashboard["admin"]["is_main_admin"] is True
    assert c_dashboard["summary"]["active_guards"] == 1

    started = await start_deletion(registration_client, a)
    assert started.status_code == 200, started.text
    request_id = started.json()["id"]
    fresh_code = outbox.last_code(a["email"])
    if fresh_code != transfer_code:
        old = await registration_client.post(
            f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": transfer_code}, headers=headers(a)
        )
        assert old.status_code == 400 and old.json()["code"] == "OTP_INVALID"
    done = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": fresh_code}, headers=headers(a)
    )
    assert done.status_code == 200 and done.json() == {
        "status": "DELETED",
        "campus_deleted": True,
        "organization_deleted": False,
    }
    assert campus_counts(owner_query, ucp["campus_a"]) == (0, 0, 0)
    assert campus_counts(owner_query, ucp["campus_b"]) == (1, 3, 1)
    assert campus_counts(owner_query, ucp["campus_c"]) == (1, 2, 1)
    assert main_admin(owner_query, ucp["org"]) == c["id"]
    assert one(owner_query, "SELECT status FROM organizations WHERE id = %(o)s", o=ucp["org"]) == "ACTIVE"
    reviewed_by, approved = one(
        owner_query,
        "SELECT reviewed_by, approved_campus_id FROM campus_registration_requests WHERE organization_id = %(o)s",
        o=ucp["org"],
    )
    assert (reviewed_by, approved) == (None, ucp["campus_c"])  # history kept, link to the deleted Admin cleared
    # Campus A leaves no trace; the completed transfer stays, without the deleted Admin.
    for trace in (str(ucp["campus_a"]), str(a["id"]), a["display_id"], ucp["sa"]["email"]):
        assert traces(owner_query, trace) == [], trace
    assert traces(owner_query, a["email"]) == ["ownership_transfers"]  # the new Main Admin's history snapshot
    assert one(
        owner_query, "SELECT from_user_id, to_user_id, status FROM ownership_transfers WHERE id = %(t)s", t=transfer
    ) == (None, c["id"], "ACCEPTED")
    assert not media["campus_a"].exists() and media["campus_b"].exists() and media["campus_c"].exists()
    assert one(owner_query, "SELECT count(*) FROM deleted_organization_names") == 0
    for user in (ucp["b"], ucp["sb"], ucp["gb"], c, ucp["gc"]):
        assert count(owner_query, "SELECT 1 FROM user_credentials WHERE user_id = %(u)s", u=user["id"]) == 1
    actions = [r[0] for r in owner_query("SELECT action FROM platform_audit_logs ORDER BY occurred_at")]
    assert actions[-3:] == ["OWNERSHIP_TRANSFER_REQUESTED", "OWNERSHIP_TRANSFER_ACCEPTED", "ADMIN_ACCOUNT_DELETED"]

    # Later the remaining Admins leave too; the last one (now the only Admin) erases the organization.
    code = one(owner_query, "SELECT organization_code FROM organizations WHERE id = %(o)s", o=ucp["org"])
    assert (await delete_account(registration_client, outbox, ucp["b"]))["organization_deleted"] is False
    assert (await delete_account(registration_client, outbox, c))["organization_deleted"] is True
    for trace in (str(ucp["org"]), code, str(ucp["campus_c"]), c["email"], str(transfer)):
        assert traces(owner_query, trace) == [], trace
    assert one(owner_query, "SELECT organization_name FROM deleted_organization_names") == "UCP"
    assert list((storage_root / ".deleting").iterdir()) == [] and not (storage_root / str(ucp["org"])).exists()


async def test_expired_and_stale_transfers_change_nothing(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, owner_execute
) -> None:
    a, b, c = ucp["a"], ucp["b"], ucp["c"]
    transfer = await transfer_to(registration_client, outbox, a, c)
    owner_execute(
        "UPDATE ownership_transfers SET created_at = now() - interval '4 days', "
        "expires_at = now() - interval '1 second' WHERE id = %(t)s",
        {"t": transfer},
    )
    expired = await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(c))
    assert expired.status_code == 410 and expired.json()["code"] == "TRANSFER_EXPIRED"
    assert main_admin(owner_query, ucp["org"]) == a["id"]

    transfer = await transfer_to(registration_client, outbox, a, c)
    # Ownership moved by other means in the meantime: the old request is stale.
    owner_execute("UPDATE users SET is_first_admin = false WHERE id = %(a)s", {"a": a["id"]})
    owner_execute("UPDATE users SET is_first_admin = true WHERE id = %(b)s", {"b": b["id"]})
    stale = await registration_client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(c))
    assert stale.status_code == 410 and stale.json()["code"] == "TRANSFER_STALE"
    assert main_admin(owner_query, ucp["org"]) == b["id"]


async def test_main_admin_without_a_successor_but_with_other_campuses_cannot_delete(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_execute, owner_query
) -> None:
    owner_execute(
        "DELETE FROM user_credentials WHERE user_id IN (%(b)s, %(c)s)", {"b": ucp["b"]["id"], "c": ucp["c"]["id"]}
    )
    blocked = await start_deletion(registration_client, ucp["a"])
    assert blocked.status_code == 409 and blocked.json()["code"] == "OWNERSHIP_SUCCESSOR_REQUIRED"
    assert main_admin(owner_query, ucp["org"]) == ucp["a"]["id"]
    assert campus_counts(owner_query, ucp["campus_a"]) == (1, 3, 1)
    assert count(owner_query, "SELECT 1 FROM admin_account_deletions") == 0


# --- Additional Admins ------------------------------------------------------------------------------


async def test_additional_admin_deletes_only_their_own_campus(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    b = ucp["b"]
    status = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(b))).json()
    assert status["is_main_admin"] is False and status["can_delete"] and status["latest_transfer"] is None
    assert status["campus_will_be_deleted"] and status["organization_will_be_deleted"] is False
    assert (await delete_account(registration_client, outbox, b))["campus_deleted"] is True
    assert campus_counts(owner_query, ucp["campus_b"]) == (0, 0, 0)
    assert campus_counts(owner_query, ucp["campus_a"]) == (1, 3, 1)
    assert campus_counts(owner_query, ucp["campus_c"]) == (1, 2, 1)
    assert main_admin(owner_query, ucp["org"]) == ucp["a"]["id"]
    assert count(owner_query, "SELECT 1 FROM ownership_transfers") == 0
    assert traces(owner_query, str(ucp["campus_b"])) == []


async def test_shared_campus_keeps_the_campus_until_its_last_admin_is_deleted(
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    ucp: dict[str, Any],
    migrated_database: DatabaseUrls,
    owner_query,
    storage_root: Path,
) -> None:
    with psycopg.connect(migrated_database.owner_test) as conn:
        d = add_user(conn, ucp["org"], ucp["campus_c"], "ADMIN", "Dua D", "d@ucp.example.com", password=OTHER_PASSWORD)
    media = put_media(storage_root, ucp["org"], ucp["campus_c"])
    status = (await registration_client.get(f"{ACCOUNT}/deletion", headers=headers(d))).json()
    assert status["campus_will_be_deleted"] is False
    assert (await delete_account(registration_client, outbox, d)) == {
        "status": "DELETED",
        "campus_deleted": False,
        "organization_deleted": False,
    }
    assert count(owner_query, "SELECT 1 FROM users WHERE id = %(d)s", d=d["id"]) == 0
    assert campus_counts(owner_query, ucp["campus_c"]) == (1, 2, 1) and media.exists()
    assert traces(owner_query, d["email"]) == []
    # Admin C is now the campus's last Admin: their deletion erases Campus C.
    assert (await delete_account(registration_client, outbox, ucp["c"]))["campus_deleted"] is True
    assert campus_counts(owner_query, ucp["campus_c"]) == (0, 0, 0) and not media.exists()
    assert traces(owner_query, str(ucp["campus_c"])) == []
    assert campus_counts(owner_query, ucp["campus_a"]) == (1, 3, 1)


async def test_nobody_can_use_another_admins_deletion_request(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, seed: Seed
) -> None:
    b, c = ucp["b"], ucp["c"]
    request_id = (await start_deletion(registration_client, b)).json()["id"]
    code = outbox.last_code(b["email"])
    other = await register_admin(
        registration_client, outbox, organization_name="Other Org", username="other.admin", email="o@example.com"
    )
    other_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=other["display_id"])
    for intruder in (c, ucp["a"], {"id": other_id}):  # other campus, Main Admin, other organization
        for path in ("verify", "export"):
            method = registration_client.post if path == "verify" else registration_client.get
            kwargs: dict[str, Any] = {"json": {"otp": code}} if path == "verify" else {}
            response = await method(f"{ACCOUNT}/deletion/{request_id}/{path}", headers=headers(intruder), **kwargs)
            assert response.status_code == 404, (path, response.text)
    for intruder in (ucp["sb"], ucp["gb"]):  # Supervisors and Guards cannot reach Admin routes
        response = await registration_client.get(f"{ACCOUNT}/deletion/{request_id}/export", headers=headers(intruder))
        assert response.status_code == 403
    assert campus_counts(owner_query, ucp["campus_b"]) == (1, 3, 1)
    # The real owner can still finish with the same code.
    done = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": code}, headers=headers(b)
    )
    assert done.status_code == 200


# --- Optional export -------------------------------------------------------------------------------


async def test_export_is_optional_and_contains_only_the_admins_own_campus(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    b = ucp["b"]
    started = await start_deletion(registration_client, b)
    body = started.json()
    request_id = body["id"]
    assert body["export"]["optional"] is True
    assert body["export"]["download_url"] == f"{ACCOUNT}/deletion/{request_id}/export"
    assert "recordings" in body["export"]["not_included"] and "evidence_files" in body["export"]["not_included"]

    tmp_before = set(Path(tempfile.gettempdir()).iterdir())
    response = await registration_client.get(body["export"]["download_url"], headers=headers(b))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].startswith("attachment")
    export = response.json()
    assert export["organization"] == {"name": "UCP"} and export["campus"]["name"] == "Campus B"
    assert sorted(u["full_name"] for u in export["users"]) == ["Bilal B", "Gohar B", "Saad B"]
    assert {u["user_id"] for u in export["users"]} == {
        b["display_id"],
        ucp["sb"]["display_id"],
        ucp["gb"]["display_id"],
    }
    assert len(export["cameras"]) == 1 and export["pending_account_creations"] == []
    assert {c["category"] for c in export["not_included"]} >= {
        "recordings",
        "event_clips",
        "evidence_files",
        "incidents",
    }  # alerts are exported since 0015
    text = response.text
    for other in ("a", "sa", "ga", "c", "gc"):  # nothing from other campuses
        assert ucp[other]["email"] not in text and ucp[other]["display_id"] not in text
    for secret in ("argon2", "password", "hash", "otp", "token", OTHER_PASSWORD, outbox.last_code(b["email"])):
        assert secret not in text.lower() if secret.islower() else secret not in text
    for internal in (str(ucp["org"]), str(ucp["campus_b"]), str(b["id"])):
        assert internal not in text
    # Nothing is kept on the server: no file in the storage root or the temporary folder.
    assert not storage_root.exists() or list(storage_root.rglob("*")) == []
    assert {p for p in set(Path(tempfile.gettempdir()).iterdir()) - tmp_before if "export" in p.name.lower()} == set()

    # Exporting is not required, and does not keep anything from deletion.
    done = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": outbox.last_code(b["email"])}, headers=headers(b)
    )
    assert done.status_code == 200 and campus_counts(owner_query, ucp["campus_b"]) == (0, 0, 0)
    gone = await registration_client.get(body["export"]["download_url"], headers=headers(b))
    assert gone.status_code == 401  # the account no longer exists


async def test_export_needs_an_open_unexpired_request(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_execute
) -> None:
    b = ucp["b"]
    assert (
        await registration_client.get(f"{ACCOUNT}/deletion/{uuid4()}/export", headers=headers(b))
    ).status_code == 404
    request_id = (await start_deletion(registration_client, b)).json()["id"]
    owner_execute(
        "UPDATE admin_account_deletions SET created_at = now() - interval '1 hour', "
        "expires_at = now() - interval '1 second' WHERE id = %(r)s",
        {"r": request_id},
    )
    expired = await registration_client.get(f"{ACCOUNT}/deletion/{request_id}/export", headers=headers(b))
    assert expired.status_code == 410 and expired.json()["code"] == "DELETION_EXPIRED"
    replaced = (await start_deletion(registration_client, b)).json()["id"]
    assert (
        await registration_client.get(f"{ACCOUNT}/deletion/{replaced}/export", headers=headers(b))
    ).status_code == 200
    assert (
        await registration_client.get(f"{ACCOUNT}/deletion/{request_id}/export", headers=headers(b))
    ).status_code == 404


# --- Failures never report success ----------------------------------------------------------------


class _BrokenStorage(LocalCampusMediaStorage):
    def __init__(self, root: Path, *, fail: str) -> None:
        super().__init__(root)
        self._fail = fail

    def stage(self, organization_id, campus_id, *, whole_organization):  # type: ignore[no-untyped-def]
        if self._fail == "stage":
            raise OSError("disk unavailable")
        return super().stage(organization_id, campus_id, whole_organization=whole_organization)

    def finalize(self, staged):  # type: ignore[no-untyped-def]
        if self._fail == "finalize":
            raise MediaCleanupFailed("Some campus files could not be erased.")
        super().finalize(staged)


async def _verify_with(client: AsyncClient, outbox: FakeEmailSender, user: dict[str, Any]):  # type: ignore[no-untyped-def]
    request_id = (await start_deletion(client, user)).json()["id"]
    return await client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": outbox.last_code(user["email"])}, headers=headers(user)
    )


async def test_file_staging_failure_deletes_nothing(
    outbox: FakeEmailSender, ucp: dict[str, Any], migrated_database: DatabaseUrls, owner_query, storage_root: Path
) -> None:
    media = put_media(storage_root, ucp["org"], ucp["campus_b"])
    async with _client_for(
        migrated_database, email_sender=outbox, media_storage=_BrokenStorage(storage_root, fail="stage")
    ) as client:
        response = await _verify_with(client, outbox, ucp["b"])
    assert response.status_code == 500 and response.json()["code"] == "DELETION_FAILED"
    assert campus_counts(owner_query, ucp["campus_b"]) == (1, 3, 1) and media.exists()


async def test_file_erase_failure_is_reported_not_hidden(
    outbox: FakeEmailSender, ucp: dict[str, Any], migrated_database: DatabaseUrls, owner_query, storage_root: Path
) -> None:
    put_media(storage_root, ucp["org"], ucp["campus_b"])
    async with _client_for(
        migrated_database, email_sender=outbox, media_storage=_BrokenStorage(storage_root, fail="finalize")
    ) as client:
        response = await _verify_with(client, outbox, ucp["b"])
    assert response.status_code == 500 and response.json()["code"] == "MEDIA_CLEANUP_INCOMPLETE"
    assert "DELETED" not in response.text
    # The records are gone; the files are out of every campus path and are erased by the next clean-up.
    assert campus_counts(owner_query, ucp["campus_b"]) == (0, 0, 0)
    assert not (storage_root / str(ucp["org"]) / str(ucp["campus_b"])).exists()
    assert len(list((storage_root / ".deleting").iterdir())) == 1
    LocalCampusMediaStorage(storage_root).sweep()
    assert list((storage_root / ".deleting").iterdir()) == []


@pytest.mark.parametrize("when", ["during the deletion", "at commit"])
async def test_database_failure_deletes_nothing_and_keeps_the_files(
    when: str,
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    ucp: dict[str, Any],
    owner_execute,
    owner_query,
    storage_root: Path,
) -> None:
    media = put_media(storage_root, ucp["org"], ucp["campus_b"])
    timing = "" if when == "during the deletion" else "CONSTRAINT"
    deferral = "" if when == "during the deletion" else "DEFERRABLE INITIALLY DEFERRED"
    owner_execute(
        """CREATE FUNCTION test_refuse() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN RAISE EXCEPTION 'simulated database failure'; END $$"""
    )
    owner_execute(
        f"CREATE {timing} TRIGGER test_refuse AFTER DELETE ON cameras {deferral} "
        "FOR EACH ROW EXECUTE FUNCTION test_refuse()"
    )
    try:
        response = await _verify_with(registration_client, outbox, ucp["b"])
    finally:
        owner_execute("DROP TRIGGER test_refuse ON cameras; DROP FUNCTION test_refuse()")
    assert response.status_code == 500 and response.json()["code"] == "DELETION_FAILED"
    assert "simulated" not in response.text
    assert campus_counts(owner_query, ucp["campus_b"]) == (1, 3, 1) and media.exists()
    assert not (storage_root / ".deleting").exists() or list((storage_root / ".deleting").iterdir()) == []


# --- Super Admin: deleted organization names --------------------------------------------------------


async def test_super_admin_sees_only_the_names_of_deleted_organizations(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, super_admin: UUID, seed: Seed
) -> None:
    a = await register_admin(registration_client, outbox, organization_name="Lakeside Academy")
    a_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=a["display_id"])
    admin = {**a, "id": a_id}
    request_id = (await start_deletion(registration_client, admin)).json()["id"]
    code = outbox.last_code(a["email"])
    done = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": code}, headers=headers(admin)
    )
    assert done.json()["organization_deleted"] is True
    # Replaying the request adds nothing.
    replay = await registration_client.post(
        f"{ACCOUNT}/deletion/{request_id}/verify", json={"otp": code}, headers=headers(admin)
    )
    assert replay.status_code == 401
    columns = [
        r[0]
        for r in owner_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'deleted_organization_names'"
        )
    ]
    assert sorted(columns) == ["id", "organization_name"]

    login = await registration_client.post(
        "/api/v1/platform/auth/login", json={"username": SUPER_ADMIN_USERNAME, "password": SUPER_ADMIN_PASSWORD}
    )
    platform = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await registration_client.get("/api/v1/platform/deleted-organizations", headers=platform)
    assert response.status_code == 200 and response.json() == {"organization_names": ["Lakeside Academy"]}
    assert a["display_id"] not in response.text and a["email"] not in response.text


async def test_organization_users_cannot_read_deleted_organization_names(
    registration_client: AsyncClient, ucp: dict[str, Any], app_role_engine, owner_execute
) -> None:
    owner_execute("INSERT INTO deleted_organization_names VALUES (gen_random_uuid(), 'Old Org')")
    for user in (ucp["a"], ucp["sa"], ucp["ga"]):
        response = await registration_client.get("/api/v1/platform/deleted-organizations", headers=headers(user))
        assert response.status_code == 401 and "Old Org" not in response.text
    for actor in (None, ucp["a"]["id"]):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, actor, "SELECT * FROM deleted_organization_names")


# --- Database access ----------------------------------------------------------------------------------


async def test_app_role_reaches_the_new_tables_only_through_the_functions(
    ucp: dict[str, Any], app_role_engine, platform_role_engine
) -> None:
    for actor in (None, ucp["a"]["id"]):
        for table in ("ownership_transfers", "admin_account_deletions"):
            with pytest.raises(DBAPIError, match="permission denied"):
                await run_as(app_role_engine, actor, f"SELECT * FROM {table}")
    # Without the right signed-in Admin the functions do nothing.
    rows, _ = await run_as(app_role_engine, ucp["sa"]["id"], "SELECT * FROM app_ownership_candidates()")
    assert rows == []
    rows, _ = await run_as(
        app_role_engine, None, "SELECT app_admin_deletion_complete(gen_random_uuid(), repeat('a', 64))"
    )
    assert "NOT_FOUND" in str(rows[0][0])
    rows, _ = await run_as(app_role_engine, ucp["a"]["id"], "SELECT app_admin_deletion_export(gen_random_uuid())")
    assert rows[0][0] == {"outcome": "NOT_FOUND"}
    for function in ("app_admin_deletion_overview()", "app_admin_deletion_export(gen_random_uuid())"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(platform_role_engine, None, f"SELECT * FROM {function}")


# --- Ownership history (migration 0012) ------------------------------------------------------------

HISTORY = f"{TRANSFERS}/history"


async def _accept(
    client: AsyncClient, outbox: FakeEmailSender, sender: dict[str, Any], recipient: dict[str, Any]
) -> str:
    transfer = await transfer_to(client, outbox, sender, recipient)
    accepted = await client.post(f"{TRANSFERS}/{transfer}/accept", headers=headers(recipient))
    assert accepted.status_code == 200, accepted.text
    return transfer


async def test_main_admin_keeps_the_previous_owners_identity_after_their_deletion(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    a, b = ucp["a"], ucp["b"]
    transfer = await _accept(registration_client, outbox, a, b)
    expected = {
        "transfer_id": transfer,
        "previous_owner_name": "Maryam Siddiqui",
        "previous_owner_username": "maryam.s",
        "previous_owner_email": a["email"],
        "status": "ACCEPTED",
        "summary": "Maryam Siddiqui offered ownership to you, and you accepted it.",
    }
    before = (await registration_client.get(HISTORY, headers=headers(b))).json()
    assert [{k: e[k] for k in expected} for e in before] == [expected]

    await delete_account(registration_client, outbox, a)
    assert campus_counts(owner_query, ucp["campus_a"]) == (0, 0, 0)
    after = await registration_client.get(HISTORY, headers=headers(b))
    assert after.status_code == 200 and after.json() == before

    # The snapshot is the only place Admin A remains, and it holds only name, username and email.
    assert traces(owner_query, a["email"]) == ["ownership_transfers"]
    for gone_value in (a["display_id"], str(a["id"]), "+923001234567", str(ucp["campus_a"])):
        assert traces(owner_query, gone_value) == [], gone_value
    row = one(owner_query, "SELECT to_jsonb(t)::text FROM ownership_transfers t WHERE id = %(t)s", t=transfer)
    for secret in ("argon2", PASSWORD, "+923001234567", "Main Campus", a["display_id"]):
        assert secret not in row
    columns = {
        r[0]
        for r in owner_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ownership_transfers'"
        )
    }
    assert {c for c in columns if c.startswith("from_")} == {
        "from_user_id",
        "from_full_name",
        "from_username",
        "from_email",
    }
    # The snapshot cannot be used to sign in.
    assert (await start_sign_in(registration_client, a)).status_code == 401


async def test_only_the_current_main_admin_sees_their_ownership_history(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, super_admin: UUID
) -> None:
    a, b, c = ucp["a"], ucp["b"], ucp["c"]
    first = await _accept(registration_client, outbox, a, b)
    for user in (ucp["sb"], ucp["gb"], ucp["sa"], ucp["gc"]):  # Supervisors and Guards
        assert (await registration_client.get(HISTORY, headers=headers(user))).status_code == 403
    for user in (a, c):  # the former Main Admin and an ordinary Admin
        refused = await registration_client.get(HISTORY, headers=headers(user))
        assert refused.status_code == 403 and refused.json()["code"] == "NOT_MAIN_ADMIN"
    login = await registration_client.post(
        "/api/v1/platform/auth/login", json={"username": SUPER_ADMIN_USERNAME, "password": SUPER_ADMIN_PASSWORD}
    )
    platform = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await registration_client.get(HISTORY, headers=platform)).status_code == 403  # decision D-024

    # Another organization's Main Admin sees only their own (empty) history.
    other = await register_admin(
        registration_client, outbox, organization_name="Other Org", username="other.admin", email="o@example.com"
    )
    other_id = one(owner_query, "SELECT id FROM users WHERE display_id = %(d)s", d=other["display_id"])
    response = await registration_client.get(HISTORY, headers=headers({"id": other_id}))
    assert response.status_code == 200 and response.json() == []
    assert str(first) not in response.text

    # B hands over to C: B loses access; C sees B's offer, not A's.
    second = await _accept(registration_client, outbox, b, c)
    assert (await registration_client.get(HISTORY, headers=headers(b))).status_code == 403
    entries = (await registration_client.get(HISTORY, headers=headers(c))).json()
    assert [(e["transfer_id"], e["previous_owner_name"]) for e in entries] == [(second, "Bilal B")]


async def test_history_function_is_main_admin_only_in_the_database(
    ucp: dict[str, Any],
    registration_client: AsyncClient,
    outbox: FakeEmailSender,
    app_role_engine,
    platform_role_engine,
) -> None:
    await _accept(registration_client, outbox, ucp["a"], ucp["b"])
    for actor in (None, ucp["a"]["id"], ucp["c"]["id"], ucp["sb"]["id"], ucp["gb"]["id"]):
        rows, _ = await run_as(app_role_engine, actor, "SELECT * FROM app_ownership_history()")
        assert rows == [], actor
    rows, _ = await run_as(app_role_engine, ucp["b"]["id"], "SELECT previous_owner_name FROM app_ownership_history()")
    assert rows == [("Maryam Siddiqui",)]
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, "SELECT * FROM app_ownership_history()")


async def test_deleting_the_new_main_admin_erases_the_snapshot_of_their_predecessor(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    """Nobody else can ever see that record, so it is not kept."""
    a, b, c = ucp["a"], ucp["b"], ucp["c"]
    await _accept(registration_client, outbox, a, b)
    await _accept(registration_client, outbox, b, c)
    await delete_account(registration_client, outbox, b)  # the recipient of A's offer, while A still exists
    assert count(owner_query, "SELECT 1 FROM ownership_transfers WHERE from_email = %(e)s", e=a["email"]) == 0
    await delete_account(registration_client, outbox, a)
    assert traces(owner_query, a["email"]) == [] and traces(owner_query, "Maryam Siddiqui") == []
    assert [e["previous_owner_name"] for e in (await registration_client.get(HISTORY, headers=headers(c))).json()] == [
        "Bilal B"
    ]


async def test_failed_organization_deletion_keeps_everything_and_writes_no_name(
    registration_client: AsyncClient, outbox: FakeEmailSender, owner_query, owner_execute
) -> None:
    a = await register_admin(registration_client, outbox, organization_name="Solo Org")
    a_id, org = one(owner_query, "SELECT id, organization_id FROM users WHERE display_id = %(d)s", d=a["display_id"])
    owner_execute(
        """CREATE FUNCTION test_refuse() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN RAISE EXCEPTION 'simulated database failure'; END $$"""
    )
    owner_execute(
        "CREATE CONSTRAINT TRIGGER test_refuse AFTER DELETE ON organizations DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION test_refuse()"
    )
    try:
        response = await _verify_with(registration_client, outbox, {**a, "id": a_id})
    finally:
        owner_execute("DROP TRIGGER test_refuse ON organizations; DROP FUNCTION test_refuse()")
    assert response.status_code == 500 and response.json()["code"] == "DELETION_FAILED"
    assert count(owner_query, "SELECT 1 FROM deleted_organization_names") == 0
    assert count(owner_query, "SELECT 1 FROM organizations WHERE id = %(o)s", o=org) == 1
    assert count(owner_query, "SELECT 1 FROM users WHERE id = %(u)s", u=a_id) == 1
    assert count(owner_query, "SELECT 1 FROM user_credentials WHERE user_id = %(u)s", u=a_id) == 1


async def test_deletion_does_not_touch_files_outside_the_campus_folders(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], storage_root: Path, tmp_path: Path
) -> None:
    """No training-data module exists yet. This checks only that deletion leaves a stand-in dataset
    folder next to the storage root, and the repository's planned ml_training/datasets folder, as
    they were."""
    stand_in = tmp_path / "training_dataset" / "images"
    stand_in.mkdir(parents=True)
    (stand_in / "sample.jpg").write_bytes(b"training sample")
    (storage_root / "shared_training").mkdir(parents=True)
    (storage_root / "shared_training" / "labels.txt").write_text("person")
    planned = Path(__file__).resolve().parents[4] / "ml_training" / "datasets"

    def snapshot(folder: Path) -> dict[str, bytes]:
        return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}

    before = (
        snapshot(stand_in),
        snapshot(storage_root / "shared_training"),
        snapshot(planned) if planned.exists() else {},
    )
    await _accept(registration_client, outbox, ucp["a"], ucp["b"])
    for admin in ("a", "c", "b"):  # the last deletion erases the organization
        await delete_account(registration_client, outbox, ucp[admin])
    after = (
        snapshot(stand_in),
        snapshot(storage_root / "shared_training"),
        snapshot(planned) if planned.exists() else {},
    )
    assert after == before


async def test_a_snapshot_is_taken_only_at_acceptance_and_never_changes(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query, owner_execute
) -> None:
    a, b = ucp["a"], ucp["b"]
    pending = await transfer_to(registration_client, outbox, a, b)
    assert one(owner_query, "SELECT from_email FROM ownership_transfers WHERE id = %(t)s", t=pending) is None
    await registration_client.post(f"{TRANSFERS}/{pending}/accept", headers=headers(b))
    owner_execute("UPDATE users SET email = 'changed@ucp.example.com' WHERE id = %(a)s", {"a": a["id"]})
    owner_execute(
        "UPDATE ownership_transfers SET from_email = 'forged@example.com', from_full_name = 'Someone' WHERE id = %(t)s",
        {"t": pending},
    )
    assert one(
        owner_query, "SELECT from_full_name, from_email FROM ownership_transfers WHERE id = %(t)s", t=pending
    ) == ("Maryam Siddiqui", a["email"])
