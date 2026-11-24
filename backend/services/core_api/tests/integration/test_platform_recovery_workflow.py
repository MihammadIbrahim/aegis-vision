"""Emergency recovery workflow: a Super Admin who forgot the normal credentials logs in with
the emergency credentials and sets a new normal username AND password.

Server under test: SUPER_ADMIN_RECOVERY_USERNAME=admin, SUPER_ADMIN_RECOVERY_PASSWORD=admin@123.
Account seeded from admin / admin@123, then (in most tests) changed to forgotten values.
"""

import logging
from uuid import UUID

import pytest

from src.infrastructure.security.jwt import create_access_token
from tests.conftest import (
    JWT_ALGORITHM,
    JWT_SECRET,
    RECOVERY_PASSWORD,
    RECOVERY_USERNAME,
    auth_header,
    platform_auth_header,
)
from tests.seed import Seed

LOGIN = "/api/v1/platform/auth/login"
ME = "/api/v1/platform/me"
RECOVER = "/api/v1/platform/recovery/credentials"
FORGOTTEN_USERNAME = "forgotten.name"
FORGOTTEN_PASSWORD = "Forgotten-pass-2026"
NEW_USERNAME = "restored.admin"
NEW_PASSWORD = "Restored-pass-2027"
SECRETS = [RECOVERY_PASSWORD, FORGOTTEN_PASSWORD, NEW_PASSWORD, "Wrong-guess-111"]


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def login(client, username: str, password: str):
    return await client.post(LOGIN, json={"username": username, "password": password})


async def token(client, username: str, password: str) -> str:
    response = await login(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def recover_body(**overrides: str) -> dict[str, str]:
    body = {
        "recovery_username": RECOVERY_USERNAME,
        "recovery_password": RECOVERY_PASSWORD,
        "new_username": NEW_USERNAME,
        "new_password": NEW_PASSWORD,
        "confirm_new_password": NEW_PASSWORD,
    }
    return body | overrides


@pytest.fixture
async def forgotten(recovery_client, dev_super_admin: UUID) -> str:
    """Change the normal credentials to values the admin then 'forgets'. Returns that session's token."""
    access = await token(recovery_client, "admin", "admin@123")
    response = await recovery_client.post(
        f"{ME}/username",
        headers=bearer(access),
        json={"current_password": "admin@123", "new_username": FORGOTTEN_USERNAME},
    )
    response = await recovery_client.post(
        f"{ME}/password",
        headers=bearer(response.json()["access_token"]),
        json={
            "current_password": "admin@123",
            "new_password": FORGOTTEN_PASSWORD,
            "confirm_new_password": FORGOTTEN_PASSWORD,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def account(owner_query) -> tuple[str, str, int, int]:
    [row] = owner_query("SELECT username, password_hash, token_version, failed_login_attempts FROM platform_admins")
    return row


# 1, 7, 8, 9. Emergency login, then a full change; new normal login and emergency login both work.


async def test_emergency_session_sets_new_normal_credentials(recovery_client, forgotten: str, owner_query) -> None:
    username_before, hash_before, version_before, _ = account(owner_query)
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)

    response = await recovery_client.post(RECOVER, headers=bearer(emergency), json=recover_body())
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"access_token", "token_type", "expires_in"}

    username, password_hash, version, _ = account(owner_query)  # 7. stored in the database
    assert username == NEW_USERNAME and username_before == FORGOTTEN_USERNAME
    assert password_hash.startswith("$argon2id$") and password_hash != hash_before
    assert version == version_before + 1  # one change, one version step

    assert (await login(recovery_client, NEW_USERNAME, NEW_PASSWORD)).status_code == 200  # 8.
    assert (await login(recovery_client, FORGOTTEN_USERNAME, FORGOTTEN_PASSWORD)).status_code == 401
    again = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)  # 9. unchanged
    assert (await recovery_client.get(ME, headers=bearer(again))).json()["username"] == NEW_USERNAME
    assert (await recovery_client.get(ME, headers=bearer(response.json()["access_token"]))).status_code == 200


# 2. Incorrect emergency username or password


@pytest.mark.parametrize(
    ("username", "password"), [("admim", RECOVERY_PASSWORD), (RECOVERY_USERNAME, "Wrong-guess-111")]
)
async def test_wrong_emergency_login_is_refused_generically(
    recovery_client, forgotten: str, username, password
) -> None:
    response = await login(recovery_client, username, password)
    assert response.status_code == 401 and response.json() == {"detail": "Invalid username or password."}


@pytest.mark.parametrize("overrides", [{"recovery_username": "admim"}, {"recovery_password": "Wrong-guess-111"}])
async def test_wrong_emergency_reentry_changes_nothing(recovery_client, forgotten: str, owner_query, overrides) -> None:
    before = account(owner_query)
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    response = await recovery_client.post(RECOVER, headers=bearer(emergency), json=recover_body(**overrides))
    assert response.status_code == 403
    assert response.json() == {"detail": "The emergency recovery credentials are incorrect."}
    assert account(owner_query)[:3] == before[:3]


# 3. No authenticated emergency session


async def test_recovery_needs_an_authenticated_session(
    recovery_client, forgotten: str, seed: Seed, owner_query
) -> None:
    before = account(owner_query)
    assert (await recovery_client.post(RECOVER, json=recover_body())).status_code == 401
    assert (await recovery_client.post(RECOVER, headers=bearer("not-a-token"), json=recover_body())).status_code == 401
    # An organization user's token is never accepted on platform routes.
    assert (
        await recovery_client.post(RECOVER, headers=auth_header(seed.first_admin), json=recover_body())
    ).status_code == 401
    assert account(owner_query) == before


# 4. A normal-login session cannot use recovery


async def test_normal_session_cannot_use_recovery(
    recovery_client, forgotten: str, owner_query, dev_super_admin
) -> None:
    before = account(owner_query)
    response = await recovery_client.post(RECOVER, headers=bearer(forgotten), json=recover_body())
    assert response.status_code == 403
    # A token from before login methods were recorded counts as a normal session.
    legacy = create_access_token(
        dev_super_admin,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_minutes=5,
        audience="platform",
        extra_claims={"ver": before[2]},
    )
    assert (await recovery_client.post(RECOVER, headers=bearer(legacy), json=recover_body())).status_code == 403
    assert account(owner_query) == before


async def test_recovery_is_refused_when_the_server_has_it_turned_off(
    client, forgotten: str, dev_super_admin, owner_query
) -> None:
    before = account(owner_query)
    headers = platform_auth_header(dev_super_admin, version=before[2], login_method="RECOVERY")
    assert (await client.post(RECOVER, headers=headers, json=recover_body())).status_code == 403


async def test_recovery_cannot_change_another_account(recovery_client, forgotten: str, owner_query) -> None:
    [(other_id,)] = owner_query(
        "INSERT INTO platform_admins (id, username, password_hash) "
        "VALUES (gen_random_uuid(), 'second.admin', '$argon2id$placeholder') RETURNING id"
    )
    headers = platform_auth_header(other_id, login_method="RECOVERY")
    assert (await recovery_client.post(RECOVER, headers=headers, json=recover_body())).status_code == 403
    assert owner_query("SELECT username FROM platform_admins WHERE id = %(id)s", {"id": other_id}) == [
        ("second.admin",)
    ]


# 5. Weak password and mismatched confirmation (and other input rules)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confirm_new_password": "Different-pass-2027"}, "do not match"),
        ({"new_password": "short1", "confirm_new_password": "short1"}, "12 to 128"),
        ({"new_password": "no-digits-at-all", "confirm_new_password": "no-digits-at-all"}, "letter and one digit"),
        ({"new_username": "has space"}, "username must be"),
        ({"new_password": "Admin@1234567", "confirm_new_password": "Admin@1234567", "new_username": "x.y.z"}, None),
    ],
)
async def test_invalid_new_credentials_are_refused(
    recovery_client, forgotten: str, owner_query, overrides, message
) -> None:
    before = account(owner_query)
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    response = await recovery_client.post(RECOVER, headers=bearer(emergency), json=recover_body(**overrides))
    if message is None:  # a valid password that is not the recovery password is accepted
        assert response.status_code == 200
        return
    assert response.status_code == 422 and message in response.json()["detail"]
    assert account(owner_query) == before  # nothing changed, and no failure was counted


async def test_new_password_must_differ_from_the_emergency_password(recovery_client, forgotten: str) -> None:
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    # A policy-compliant value equal to the recovery password is refused (test server uses a short one,
    # so check the rule at the use-case level in unit tests too).
    response = await recovery_client.post(
        RECOVER,
        headers=bearer(emergency),
        json=recover_body(new_password="admin@123", confirm_new_password="admin@123"),
    )
    assert response.status_code == 422


# 6. Duplicate username


async def test_duplicate_username_is_refused(recovery_client, forgotten: str, owner_query) -> None:
    owner_query(
        "INSERT INTO platform_admins (id, username, password_hash) "
        "VALUES (gen_random_uuid(), 'taken.name', '$argon2id$placeholder') RETURNING 1"
    )
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    response = await recovery_client.post(
        RECOVER, headers=bearer(emergency), json=recover_body(new_username="Taken.Name")
    )
    assert response.status_code == 409
    assert owner_query("SELECT username FROM platform_admins WHERE is_primary") == [(FORGOTTEN_USERNAME,)]
    assert (await recovery_client.get(ME, headers=bearer(emergency))).status_code == 200  # still signed in


# 10. Earlier normal and emergency tokens are invalidated


async def test_earlier_tokens_stop_working(recovery_client, forgotten: str) -> None:
    normal_before = await token(recovery_client, FORGOTTEN_USERNAME, FORGOTTEN_PASSWORD)
    emergency_before = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    other_emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    response = await recovery_client.post(RECOVER, headers=bearer(emergency_before), json=recover_body())
    fresh = response.json()["access_token"]
    for old in (forgotten, normal_before, emergency_before, other_emergency):
        stale = await recovery_client.get(ME, headers=bearer(old))
        assert stale.status_code == 401 and stale.json() == {"detail": "Session expired. Please log in again."}
    assert (await recovery_client.get(ME, headers=bearer(fresh))).status_code == 200
    # Replaying the old emergency token cannot run a second recovery.
    assert (
        await recovery_client.post(RECOVER, headers=bearer(emergency_before), json=recover_body())
    ).status_code == 401


# 11. Audit log without sensitive information


async def test_recovery_is_audited_without_secrets(
    recovery_client, forgotten: str, owner_query, dev_super_admin: UUID, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    bodies = [
        (
            await recovery_client.post(
                RECOVER, headers=bearer(emergency), json=recover_body(recovery_password="Wrong-guess-111")
            )
        ).text
    ]
    bodies.append((await recovery_client.post(RECOVER, headers=bearer(emergency), json=recover_body())).text)

    rows = owner_query(
        "SELECT outcome, metadata FROM platform_audit_logs "
        "WHERE action = 'CREDENTIALS_RECOVERED' AND actor_platform_admin_id = %(id)s ORDER BY occurred_at",
        {"id": dev_super_admin},
    )
    assert rows == [
        ("FAILURE", {"reason": "RECOVERY_CREDENTIALS_INCORRECT"}),
        ("SUCCESS", {"login_method": "RECOVERY", "username_changed": True}),
    ]
    stored = " ".join(r[0] for r in owner_query("SELECT row_to_json(l)::text FROM platform_audit_logs l"))
    everything = stored + " ".join(bodies) + caplog.text
    for secret in SECRETS:
        assert secret not in everything
    assert emergency not in everything  # no token in logs or audit rows


# 12. Rate limiting of failed recovery verification


async def test_failed_reentries_lock_the_account_for_both_methods(recovery_client, forgotten: str, owner_query) -> None:
    emergency = await token(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)
    for _ in range(5):
        response = await recovery_client.post(
            RECOVER, headers=bearer(emergency), json=recover_body(recovery_password="Wrong-guess-111")
        )
        assert response.status_code == 403
    assert (await recovery_client.post(RECOVER, headers=bearer(emergency), json=recover_body())).status_code == 429
    assert (await login(recovery_client, RECOVERY_USERNAME, RECOVERY_PASSWORD)).status_code == 429
    assert (await login(recovery_client, FORGOTTEN_USERNAME, FORGOTTEN_PASSWORD)).status_code == 429
    assert account(owner_query)[0] == FORGOTTEN_USERNAME  # nothing changed
