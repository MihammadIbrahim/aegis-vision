"""Platform Super Admin accounts.

Super Admins are platform staff, not organization users, so they live in their own
table and are reached through their own database role:

* ``cctv_platform`` (PLATFORM_DB_USER) is the only role that can read this table.
  It may update only the login-tracking columns; it cannot create admins or change
  passwords (the seed script does that as the owner).
* ``cctv_app`` (the tenant role) gets no access at all, and ``cctv_platform`` gets
  no access to any tenant table. Both roles are created by
  ``docker/postgres/init/02-create-roles.sh``.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role(env_name: str, default: str) -> str:
    role = os.environ.get(env_name, default)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid {env_name} role name: {role!r}")
    return role


_TABLE = """
CREATE TABLE platform_admins (
    id                    uuid PRIMARY KEY,
    username              text NOT NULL,
    password_hash         text NOT NULL,
    is_active             boolean NOT NULL DEFAULT true,
    failed_login_attempts integer NOT NULL DEFAULT 0,
    last_failed_login_at  timestamptz,
    locked_until          timestamptz,
    last_login_at         timestamptz,
    password_changed_at   timestamptz NOT NULL DEFAULT now(),
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_platform_admins_username CHECK (username ~ '^[A-Za-z0-9_.-]{3,64}$'),
    CONSTRAINT ck_platform_admins_argon2 CHECK (password_hash LIKE '$argon2id$%'),
    CONSTRAINT ck_platform_admins_attempts CHECK (failed_login_attempts >= 0)
);
CREATE UNIQUE INDEX uq_platform_admins_username ON platform_admins (lower(username));
"""


def upgrade() -> None:
    platform = _role("PLATFORM_DB_USER", "cctv_platform")
    op.execute(_TABLE)
    for statement in [
        "REVOKE ALL ON platform_admins FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA public TO {platform}",
        f"GRANT SELECT ON platform_admins TO {platform}",
        f"GRANT UPDATE (failed_login_attempts, last_failed_login_at, locked_until, last_login_at) "
        f"ON platform_admins TO {platform}",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_admins")
