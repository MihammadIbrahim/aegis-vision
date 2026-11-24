"""Step 1: Platform Super Admin login, and its separation from organization users."""

from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.infrastructure.security.jwt import create_platform_token
from tests.conftest import (
    JWT_ALGORITHM,
    SUPER_ADMIN_PASSWORD,
    SUPER_ADMIN_USERNAME,
    auth_header,
    platform_auth_header,
    run_as,
)
from tests.seed import Seed

LOGIN = "/api/v1/platform/auth/login"
ME = "/api/v1/platform/me"


async def login(client, username: str = SUPER_ADMIN_USERNAME, password: str = SUPER_ADMIN_PASSWORD):
    return await client.post(LOGIN, json={"username": username, "password": password})


async def test_login_returns_a_platform_token_that_opens_me(client, super_admin: UUID) -> None:
    response = await login(client)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer" and body["expires_in"] == 30 * 60

    me = await client.get(ME, headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    # Exactly these fields: never the password hash or lockout internals.
    assert set(me.json()) == {"id", "username", "role", "created_at", "last_login_at"}
    assert me.json()["id"] == str(super_admin) and me.json()["role"] == "PLATFORM_SUPER_ADMIN"
    assert me.json()["last_login_at"] is not None


async def test_username_is_case_insensitive(client, super_admin: UUID) -> None:
    assert (await login(client, username=SUPER_ADMIN_USERNAME.upper())).status_code == 200


async def test_wrong_password_and_unknown_user_look_identical(client, super_admin: UUID) -> None:
    wrong = await login(client, password="not-the-password")
    unknown = await login(client, username="nobody.here")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password."}


async def test_five_failures_lock_the_account(client, super_admin: UUID, owner_query) -> None:
    for _ in range(5):
        assert (await login(client, password="not-the-password")).status_code == 401
    locked = await login(client)  # correct password, but locked
    assert locked.status_code == 429
    [(locked_until,)] = owner_query("SELECT locked_until FROM platform_admins")
    assert locked_until is not None


async def test_login_works_again_after_the_lock_expires(client, super_admin: UUID, owner_query) -> None:
    for _ in range(5):
        await login(client, password="not-the-password")
    owner_query_sql = "UPDATE platform_admins SET locked_until = now() - interval '1 second' RETURNING 1"
    owner_query(owner_query_sql)
    assert (await login(client)).status_code == 200
    assert owner_query("SELECT failed_login_attempts, locked_until FROM platform_admins") == [(0, None)]


async def test_inactive_super_admin_cannot_log_in_or_use_a_token(client, super_admin: UUID, owner_query) -> None:
    owner_query("UPDATE platform_admins SET is_active = false RETURNING 1")
    assert (await login(client)).status_code == 401
    assert (await client.get(ME, headers=platform_auth_header(super_admin))).status_code == 401


async def test_password_is_stored_only_as_argon2_hash(super_admin: UUID, owner_query) -> None:
    [(password_hash,)] = owner_query("SELECT password_hash FROM platform_admins")
    assert password_hash.startswith("$argon2id$") and SUPER_ADMIN_PASSWORD not in password_hash


# --- Separation from organization users ---


async def test_organization_user_token_cannot_open_platform_routes(client, seed: Seed, super_admin: UUID) -> None:
    assert (await client.get(ME, headers=auth_header(seed.first_admin))).status_code == 401


async def test_super_admin_token_cannot_open_organization_routes(client, seed: Seed, super_admin: UUID) -> None:
    # An identified Super Admin is refused with 403, not 401 (decision D-024).
    headers = platform_auth_header(super_admin)
    for path in ("/api/v1/cameras", "/api/v1/organization/admin-directory", "/api/v1/audit-logs"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403, path
        assert response.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED"
    # A platform-audience token signed with another key is still just an invalid token.
    forged = create_platform_token(
        super_admin, 1, secret="another-secret-0123456789abcdef-xyz", algorithm=JWT_ALGORITHM, expires_minutes=5
    )
    assert (await client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_tenant_database_role_cannot_read_super_admins(app_role_engine: AsyncEngine, super_admin: UUID) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, None, "SELECT * FROM platform_admins")


@pytest.mark.parametrize("table", ["users", "cameras", "campuses", "organizations", "campus_registration_requests"])
async def test_platform_database_role_cannot_read_tenant_tables(
    platform_role_engine: AsyncEngine, seed: Seed, table: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, f"SELECT 1 FROM {table}")


async def test_platform_database_role_cannot_create_admins_or_change_passwords(
    platform_role_engine: AsyncEngine, super_admin: UUID
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(
            platform_role_engine,
            None,
            "INSERT INTO platform_admins (id, username, password_hash) "
            "VALUES (gen_random_uuid(), 'intruder', '$argon2id$x')",
        )
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(platform_role_engine, None, "UPDATE platform_admins SET password_hash = '$argon2id$x'")
