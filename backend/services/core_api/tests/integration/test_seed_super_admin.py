"""The seed command is the only way to create or recover the Super Admin."""

import psycopg

from src.entrypoints.seed_super_admin import seed_super_admin
from src.infrastructure.security.password import Argon2PasswordHasher
from tests.conftest import DatabaseUrls


def test_seed_creates_then_leaves_alone_then_resets(migrated_database: DatabaseUrls) -> None:
    hasher = Argon2PasswordHasher()
    with psycopg.connect(migrated_database.owner_test) as conn:
        conn.execute("TRUNCATE platform_admins")
        assert seed_super_admin(conn, username="ops.admin", password="first-password-123", hasher=hasher) == "created"
        [(first_hash,)] = conn.execute("SELECT password_hash FROM platform_admins").fetchall()

        # A second run without --reset-password changes nothing.
        assert seed_super_admin(conn, username="OPS.ADMIN", password="other-password-456", hasher=hasher) == "exists"
        assert conn.execute("SELECT password_hash FROM platform_admins").fetchall() == [(first_hash,)]

        conn.execute("UPDATE platform_admins SET failed_login_attempts = 3, locked_until = now() + interval '1 hour'")
        outcome = seed_super_admin(
            conn, username="ops.admin", password="other-password-456", hasher=hasher, reset_password=True
        )
        assert outcome == "password_reset"
        [(new_hash, attempts, locked_until)] = conn.execute(
            "SELECT password_hash, failed_login_attempts, locked_until FROM platform_admins"
        ).fetchall()
    assert new_hash != first_hash and hasher.verify(new_hash, "other-password-456")
    assert attempts == 0 and locked_until is None
