"""Super Admin credential changes, emergency recovery support, and the platform audit log.

* ``is_primary`` marks the one Super Admin account that the seed command creates and
  that the emergency recovery credentials (from the server's environment) log in to.
* ``token_version`` is copied into every Super Admin token. Changing the username or
  password increases it, so all earlier tokens stop working.
* ``platform_audit_logs`` records Super Admin logins and credential changes. The
  platform role may only insert and read rows: it cannot edit or delete history.
  Rows never contain usernames typed at login, passwords, or tokens.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_role() -> str:
    role = os.environ.get("PLATFORM_DB_USER", "cctv_platform")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid PLATFORM_DB_USER role name: {role!r}")
    return role


def upgrade() -> None:
    platform = _platform_role()
    for statement in [
        "ALTER TABLE platform_admins ADD COLUMN is_primary boolean NOT NULL DEFAULT false",
        "ALTER TABLE platform_admins ADD COLUMN token_version integer NOT NULL DEFAULT 1 "
        "CONSTRAINT ck_platform_admins_token_version CHECK (token_version > 0)",
        # Existing installations: the oldest Super Admin becomes the primary one.
        """UPDATE platform_admins SET is_primary = true
            WHERE id = (SELECT id FROM platform_admins ORDER BY created_at, id LIMIT 1)""",
        "CREATE UNIQUE INDEX uq_platform_admins_one_primary ON platform_admins (is_primary) WHERE is_primary",
        # No direct UPDATE of username/password_hash for the platform role: credentials change only
        # through this function, which always signs out older tokens and refuses stale changes.
        """CREATE FUNCTION platform_set_admin_credentials(
               p_admin_id uuid, p_username text, p_password_hash text, p_expected_version integer)
           RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           DECLARE new_version integer;
           BEGIN
               UPDATE platform_admins
                  SET username = p_username,
                      password_hash = p_password_hash,
                      password_changed_at = CASE WHEN password_hash <> p_password_hash
                                                 THEN now() ELSE password_changed_at END,
                      token_version = token_version + 1
                WHERE id = p_admin_id AND token_version = p_expected_version AND is_active
            RETURNING token_version INTO new_version;
               IF new_version IS NULL THEN
                   RAISE EXCEPTION 'credentials were changed concurrently or the account is inactive'
                       USING ERRCODE = '40001';
               END IF;
               RETURN new_version;
           END $$""",
        "REVOKE ALL ON FUNCTION platform_set_admin_credentials(uuid, text, text, integer) FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION platform_set_admin_credentials(uuid, text, text, integer) TO {platform}",
        """CREATE TABLE platform_audit_logs (
               id                      uuid PRIMARY KEY,
               occurred_at             timestamptz NOT NULL DEFAULT now(),
               actor_platform_admin_id uuid,
               action                  text NOT NULL CONSTRAINT ck_platform_audit_action
                                            CHECK (action ~ '^[A-Z_]{3,64}$'),
               outcome                 text NOT NULL CONSTRAINT ck_platform_audit_outcome
                                            CHECK (outcome IN ('SUCCESS', 'FAILURE')),
               metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
               ip_address              inet,
               user_agent              text CONSTRAINT ck_platform_audit_user_agent CHECK (length(user_agent) <= 512)
           )""",
        "CREATE INDEX ix_platform_audit_logs_occurred_at ON platform_audit_logs (occurred_at DESC)",
        "REVOKE ALL ON platform_audit_logs FROM PUBLIC",
        f"GRANT SELECT, INSERT ON platform_audit_logs TO {platform}",
    ]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS platform_set_admin_credentials(uuid, text, text, integer)")
    op.execute("DROP INDEX IF EXISTS uq_platform_admins_one_primary")
    op.execute("ALTER TABLE platform_admins DROP COLUMN IF EXISTS token_version")
    op.execute("ALTER TABLE platform_admins DROP COLUMN IF EXISTS is_primary")
