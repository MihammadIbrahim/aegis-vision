"""Super Admin: normal database credentials, credential changes, and emergency recovery.

The server under test has SUPER_ADMIN_RECOVERY_USERNAME=admin and
SUPER_ADMIN_RECOVERY_PASSWORD=admin@123, and the account was seeded from
SUPER_ADMIN_USERNAME=admin / SUPER_ADMIN_PASSWORD=admin@123.
"""

import logging
from uuid import UUID

import psycopg
import pytest

from src.entrypoints.seed_super_admin import seed_super_admin
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import (
    RECOVERY_PASSWORD,
    RECOVERY_USERNAME,
    DatabaseUrls,
    platform_auth_header,
)
from tests.seed import Seed

LOGIN = "/api/v1/platform/auth/login"
ME = "/api/v1/platform/me"
NEW_USERNAME = "boss.admin"
NEW_PASSWORD = "Stronger-pass-2026"
ALL_PASSWORDS = ["admin@123", NEW_PASSWORD, "Wrong-guess-111", "Another-pass-2027"]


async def login(client, username: str, password: str):
    return await client.post(LOGIN, json={"username": username, "password": password})


async def token(client, username: str, password: str) -> str:
    response = await login(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def change_both(client, access_token: str) -> str:
    """Change username, then password, through the API. Returns the newest token."""
    response = await client.post(
        f"{ME}/username",
        headers=bearer(access_token),
        json={"current_password": "admin@123", "new_username": NEW_USERNAME},
    )
    assert response.status_code == 200, response.text
    response = await client.post(
        f"{ME}/password",
        headers=bearer(response.json()["access_token"]),
        json={"current_password": "admin@123", "new_password": NEW_PASSWORD, "confirm_new_password": NEW_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


# 1. Initial account creation


def test_seed_creates_the_primary_account_from_the_initial_values(dev_super_admin: UUID, owner_query) -> None:
    [(username, password_hash, is_primary, version)] = owner_query(
        "SELECT username, password_hash, is_primary, token_version FROM platform_admins"
    )
    assert (username, is_primary, version) == ("admin", True, 1)
    assert password_hash.startswith("$argon2id$") and "admin@123" not in password_hash


async def test_seed_never_overwrites_changed_credentials(
    recovery_client, dev_super_admin: UUID, migrated_database: DatabaseUrls, owner_query
) -> None:
    await change_both(recovery_client, await token(recovery_client, "admin", "admin@123"))
    with psycopg.connect(migrated_database.owner_test) as conn:
        outcome = seed_super_admin(conn, username="admin", password="admin@123", hasher=Argon2PasswordHasher())
    assert outcome == "exists"
    assert owner_query("SELECT count(*), max(username) FROM platform_admins") == [(1, NEW_USERNAME)]
    assert (await login(recovery_client, NEW_USERNAME, NEW_PASSWORD)).status_code == 200


# 2-5. Normal login, credential change, new credentials, recovery afterwards


async def test_full_credential_change_flow(recovery_client, dev_super_admin: UUID, owner_query) -> None:
    first = await token(recovery_client, "admin", "admin@123")  # 2. normal login
    newest = await change_both(recovery_client, first)  # 3. change username and password

    assert (await login(recovery_client, "admin", "Wrong-guess-111")).status_code == 401
    normal = await token(recovery_client, NEW_USERNAME, NEW_PASSWORD)  # 4. new credentials work
    assert (await recovery_client.get(ME, headers=bearer(normal))).json()["username"] == NEW_USERNAME
    assert (await recovery_client.get(ME, headers=bearer(newest))).status_code == 200

    recovery = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)  # 5. recovery still works
    me = (await recovery_client.get(ME, headers=bearer(recovery))).json()
    assert me["id"] == str(dev_super_admin) and me["username"] == NEW_USERNAME  # same account, same rights

    methods = owner_query(
        "SELECT metadata->>'login_method' FROM platform_audit_logs "
        "WHERE action = 'LOGIN_SUCCEEDED' AND actor_platform_admin_id = %(id)s ORDER BY occurred_at",
        {"id": dev_super_admin},
    )
    assert methods == [("NORMAL",), ("NORMAL",), ("RECOVERY",)]
    changes = owner_query(
        "SELECT action FROM platform_audit_logs WHERE outcome = 'SUCCESS' AND action LIKE '%%_CHANGED' "
        "AND actor_platform_admin_id = %(id)s ORDER BY occurred_at",
        {"id": dev_super_admin},
    )
    assert changes == [("USERNAME_CHANGED",), ("PASSWORD_CHANGED",)]


# 6. Incorrect emergency password


async def test_wrong_recovery_password_gets_the_generic_error(recovery_client, dev_super_admin: UUID) -> None:
    await change_both(recovery_client, await token(recovery_client, "admin", "admin@123"))
    wrong = await login(recovery_client, RECOVERY_USERNAME, "Wrong-guess-111")
    unknown = await login(recovery_client, "nobody.here", "Wrong-guess-111")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password."}


async def test_recovery_is_off_when_the_variables_are_not_set(client, recovery_client, dev_super_admin: UUID) -> None:
    await change_both(recovery_client, await token(recovery_client, "admin", "admin@123"))
    assert (await login(client, RECOVERY_USERNAME, RECOVERY_PASSWORD)).status_code == 401


# 7. Rate limiting applies to both methods


async def test_failed_recovery_attempts_lock_normal_login_too(recovery_client, dev_super_admin: UUID) -> None:
    await change_both(recovery_client, await token(recovery_client, "admin", "admin@123"))
    for _ in range(5):
        assert (await login(recovery_client, RECOVERY_USERNAME, "Wrong-guess-111")).status_code == 401
    assert (await login(recovery_client, NEW_USERNAME, NEW_PASSWORD)).status_code == 429
    assert (await login(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)).status_code == 429


async def test_failed_normal_attempts_lock_recovery_too(recovery_client, dev_super_admin: UUID) -> None:
    await change_both(recovery_client, await token(recovery_client, "admin", "admin@123"))
    for _ in range(5):
        assert (await login(recovery_client, NEW_USERNAME, "Wrong-guess-111")).status_code == 401
    assert (await login(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)).status_code == 429


async def test_wrong_current_password_counts_toward_the_lock(recovery_client, dev_super_admin: UUID) -> None:
    access = await token(recovery_client, "admin", "admin@123")
    for _ in range(5):
        response = await recovery_client.post(
            f"{ME}/username",
            headers=bearer(access),
            json={"current_password": "Wrong-guess-111", "new_username": "x.y.z"},
        )
        assert response.status_code == 403
    assert (await login(recovery_client, "admin", "admin@123")).status_code == 429


# 8. Token invalidation after a normal credential change


async def test_tokens_issued_before_a_change_stop_working(recovery_client, dev_super_admin: UUID) -> None:
    before_normal = await token(recovery_client, "admin", "admin@123")
    before_recovery = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    newest = await change_both(recovery_client, before_normal)
    for old in (before_normal, before_recovery):
        response = await recovery_client.get(ME, headers=bearer(old))
        assert response.status_code == 401 and response.json() == {"detail": "Session expired. Please log in again."}
    assert (await recovery_client.get(ME, headers=bearer(newest))).status_code == 200


# Validation rules for changes


async def test_change_rules(recovery_client, dev_super_admin: UUID, owner_query) -> None:
    access = await token(recovery_client, "admin", "admin@123")
    mismatch = await recovery_client.post(
        f"{ME}/password",
        headers=bearer(access),
        json={"current_password": "admin@123", "new_password": NEW_PASSWORD, "confirm_new_password": "Different-2026"},
    )
    assert mismatch.status_code == 422 and "do not match" in mismatch.json()["detail"]
    weak = await recovery_client.post(
        f"{ME}/password",
        headers=bearer(access),
        json={"current_password": "admin@123", "new_password": "short1", "confirm_new_password": "short1"},
    )
    assert weak.status_code == 422
    bad_name = await recovery_client.post(
        f"{ME}/username", headers=bearer(access), json={"current_password": "admin@123", "new_username": "no spaces"}
    )
    assert bad_name.status_code == 422
    # Nothing changed, so the original token still works.
    assert (await recovery_client.get(ME, headers=bearer(access))).status_code == 200


async def test_username_must_be_unique(recovery_client, dev_super_admin: UUID, owner_query) -> None:
    owner_query(
        "INSERT INTO platform_admins (id, username, password_hash) "
        "VALUES (gen_random_uuid(), 'taken.name', '$argon2id$placeholder') RETURNING 1"
    )
    access = await token(recovery_client, "admin", "admin@123")
    response = await recovery_client.post(
        f"{ME}/username", headers=bearer(access), json={"current_password": "admin@123", "new_username": "TAKEN.NAME"}
    )
    assert response.status_code == 409
    assert (await recovery_client.get(ME, headers=bearer(access))).json()["username"] == "admin"


# 9. No plaintext passwords in database rows, logs or responses


async def test_no_plaintext_password_anywhere(
    recovery_client, dev_super_admin: UUID, owner_query, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies = []
    access = await token(recovery_client, "admin", "admin@123")
    await change_both(recovery_client, access)
    for username, password in [(RECOVERY_USERNAME, "Wrong-guess-111"), (RECOVERY_USERNAME, RECOVERY_PASSWORD)]:
        bodies.append((await login(recovery_client, username, password)).text)
    malformed = await recovery_client.post(
        LOGIN, json={"username": "admin", "password": "Another-pass-2027", "extra": 1}
    )
    assert malformed.status_code == 422
    bodies.append(malformed.text)

    stored = owner_query("SELECT row_to_json(a)::text FROM platform_admins a") + owner_query(
        "SELECT row_to_json(l)::text FROM platform_audit_logs l"
    )
    everything = " ".join(row[0] for row in stored) + " ".join(bodies) + caplog.text
    for password in ALL_PASSWORDS:
        assert password not in everything


# 10. Super Admin tokens, including recovery ones, cannot reach organization routes


async def test_recovery_token_cannot_open_organization_routes(
    recovery_client, seed: Seed, dev_super_admin: UUID
) -> None:
    recovery = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    for path in ("/api/v1/cameras", "/api/v1/organization/admin-directory", "/api/v1/campus-requests"):
        # Identified as the Super Admin, so refused with 403 (decision D-024), never let in.
        assert (await recovery_client.get(path, headers=bearer(recovery))).status_code == 403
    assert (await recovery_client.get(ME, headers=platform_auth_header(dev_super_admin, version=99))).status_code == 401
