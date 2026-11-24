"""Create the primary Platform Super Admin, or reset it, from environment variables.

There is no sign-up or email reset for the Super Admin (requirement). This command, run
by whoever operates the server, creates the account once from the INITIAL credentials:

    cd backend/services/core_api
    python -m src.entrypoints.seed_super_admin                   # create if missing; never overwrites
    python -m src.entrypoints.seed_super_admin --reset-password  # set username + password back to
                                                                 # the initial values, sign out, unlock

Reads SUPER_ADMIN_USERNAME, SUPER_ADMIN_PASSWORD and MIGRATION_DATABASE_URL (owner role)
from the environment or backend/.env. The emergency SUPER_ADMIN_RECOVERY_* values are not
used here: the API reads them directly. Passwords are never printed or logged.
"""

import argparse
import re
import sys
from uuid import uuid4

import psycopg
from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.config.settings import describe_validation_error, env_files
from src.infrastructure.security.password import Argon2PasswordHasher

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
MIN_PASSWORD_LENGTH = 8  # the development default 'admin@123' is allowed, with a warning
RECOMMENDED_PASSWORD_LENGTH = 12


class SeedSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=env_files(), extra="ignore")

    migration_database_url: str
    super_admin_username: str
    super_admin_password: SecretStr

    @field_validator("super_admin_username")
    @classmethod
    def _username_format(cls, value: str) -> str:
        if not USERNAME_PATTERN.match(value):
            raise ValueError("use 3-64 letters, digits, '.', '_' or '-'")
        return value

    @field_validator("super_admin_password")
    @classmethod
    def _password_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"must be at least {MIN_PASSWORD_LENGTH} characters long")
        return value


def seed_super_admin(
    conn: psycopg.Connection, *, username: str, password: str, hasher: IPasswordHasher, reset_password: bool = False
) -> str:
    """Returns "created", "exists" (nothing changed) or "password_reset".

    Works on the primary Super Admin, so a later username change made in the dashboard
    never leads to a second account being created here.
    """
    row = conn.execute("SELECT id FROM platform_admins WHERE is_primary FOR UPDATE").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO platform_admins (id, username, password_hash, is_primary) VALUES (%s, %s, %s, true)",
            (uuid4(), username, hasher.hash(password)),
        )
        return "created"
    if not reset_password:
        return "exists"
    conn.execute(
        """UPDATE platform_admins
              SET username = %s, password_hash = %s, password_changed_at = now(), is_active = true,
                  token_version = token_version + 1,
                  failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE id = %s""",
        (username, hasher.hash(password), row[0]),
    )
    return "password_reset"


_MESSAGES = {
    "created": "Super Admin '{u}' created.",
    "exists": "The primary Super Admin already exists; nothing changed. "
    "Use --reset-password to set its username and password back to the initial values.",
    "password_reset": "The primary Super Admin was reset to username '{u}' and the initial password; "
    "all its sessions were signed out and the account unlocked.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset-password", action="store_true", help="replace the password and unlock the account")
    args = parser.parse_args(argv)
    try:
        settings = SeedSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        print("Cannot seed the Super Admin:\n  - " + "\n  - ".join(describe_validation_error(exc)), file=sys.stderr)
        return 2
    dsn = make_url(settings.migration_database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn) as conn:
        outcome = seed_super_admin(
            conn,
            username=settings.super_admin_username,
            password=settings.super_admin_password.get_secret_value(),
            hasher=Argon2PasswordHasher(),
            reset_password=args.reset_password,
        )
    print(_MESSAGES[outcome].format(u=settings.super_admin_username))
    if outcome != "exists" and len(settings.super_admin_password.get_secret_value()) < RECOMMENDED_PASSWORD_LENGTH:
        print(
            "Warning: the initial password is short and suitable for local development only. "
            "Change it after the first login (POST /api/v1/platform/me/password).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
