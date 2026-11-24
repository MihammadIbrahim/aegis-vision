"""Ownership-transfer history that survives deletion of the previous owner.

* ``ownership_transfers`` gets a snapshot of the previous owner: full name, username and email.
  It is filled by a trigger at the moment a transfer becomes ACCEPTED, and never otherwise. No
  password, hash, token, code, phone or other profile field is copied. The table has no grants to
  the application role, and no sign-in function reads it, so the snapshot cannot be used to sign in.
* ``app_ownership_history()``: the accepted transfers to the signed-in Admin, only while that Admin
  is the organization's Main Admin. Not granted to the platform role.
* ``app_admin_deletion_complete`` (from 0011) changes in two places:
  - An accepted transfer is kept only while its recipient exists. A record whose recipient is
    deleted could never be shown again, so it is deleted with the recipient.
  - Deleting the organization writes no audit entry. Only its name remains, in
    ``deleted_organization_names``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _revision_0011() -> ModuleType:
    path = Path(__file__).with_name("0011_complete_campus_erasure_and_deleted_org_history.py")
    spec = importlib.util.spec_from_file_location("_revision_0011", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _deletion_function_0011() -> str:
    return next(f for f in _revision_0011()._FUNCTIONS if "FUNCTION app_admin_deletion_complete(" in f)


# The exact 0011 text being replaced, and its replacement.
_KEEP_RULE_0011 = """              AND (tr.status <> 'ACCEPTED'
                   OR (COALESCE(tr.from_user_id = ANY (v_users), true)
                       AND COALESCE(tr.to_user_id = ANY (v_users), true)));"""
_KEEP_RULE = (
    """              AND (tr.status <> 'ACCEPTED' OR tr.to_user_id IS NULL OR tr.to_user_id = ANY (v_users));"""
)
_ORG_AUDIT_0011 = """               INSERT INTO platform_audit_logs (id, occurred_at, action, outcome, metadata)
               VALUES (gen_random_uuid(), now(), 'ADMIN_ACCOUNT_DELETED', 'SUCCESS',
                       jsonb_build_object('campus_deleted', true, 'organization_deleted', true));
"""


def _deletion_function() -> str:
    old = _deletion_function_0011()
    assert old.count(_KEEP_RULE_0011) == 1 and old.count(_ORG_AUDIT_0011) == 1
    new = old.replace(_KEEP_RULE_0011, _KEEP_RULE).replace(_ORG_AUDIT_0011, "")
    new = new.replace(
        "-- Ownership history: keep a completed transfer while one party remains; drop the rest.",
        "-- Ownership history: keep a completed transfer while its recipient remains; drop the rest.",
    )
    return new.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


_SCHEMA = [
    """ALTER TABLE ownership_transfers
           ADD COLUMN from_full_name text,
           ADD COLUMN from_username  text,
           ADD COLUMN from_email     text""",
    # Accepted transfers made before this migration: copy from the sender while they still exist.
    """UPDATE ownership_transfers tr
          SET from_full_name = u.full_name, from_username = u.username, from_email = u.email
         FROM users u
        WHERE u.id = tr.from_user_id AND tr.status = 'ACCEPTED'""",
    # Only an accepted transfer carries a snapshot. NOT VALID: older accepted rows whose sender was
    # already deleted cannot be filled in, but every new or changed row is checked.
    """ALTER TABLE ownership_transfers ADD CONSTRAINT ck_ownership_transfers_snapshot CHECK (
           CASE WHEN status = 'ACCEPTED' THEN from_full_name IS NOT NULL AND from_email IS NOT NULL
                ELSE from_full_name IS NULL AND from_username IS NULL AND from_email IS NULL END
       ) NOT VALID""",
    """CREATE FUNCTION app_ownership_snapshot() RETURNS trigger
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           IF NEW.status = 'ACCEPTED' AND (TG_OP = 'INSERT' OR OLD.status <> 'ACCEPTED') THEN
               SELECT u.full_name, u.username, u.email INTO NEW.from_full_name, NEW.from_username, NEW.from_email
                 FROM users u WHERE u.id = NEW.from_user_id;
           ELSIF NEW.status <> 'ACCEPTED' THEN
               NEW.from_full_name := NULL;
               NEW.from_username := NULL;
               NEW.from_email := NULL;
           ELSE
               -- Once taken, the snapshot does not change (also when a party's link is emptied).
               NEW.from_full_name := OLD.from_full_name;
               NEW.from_username := OLD.from_username;
               NEW.from_email := OLD.from_email;
           END IF;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_ownership_snapshot BEFORE INSERT OR UPDATE ON ownership_transfers
       FOR EACH ROW EXECUTE FUNCTION app_ownership_snapshot()""",
    "CREATE INDEX ix_ownership_transfers_accepted_to ON ownership_transfers (to_user_id) WHERE status = 'ACCEPTED'",
]

_HISTORY = """CREATE FUNCTION app_ownership_history()
       RETURNS TABLE (transfer_id uuid, previous_owner_name text, previous_owner_username text,
                      previous_owner_email text, status text, offered_at timestamptz, accepted_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
       BEGIN
           SELECT u.id, u.organization_id INTO me
             FROM users u
             JOIN organizations o ON o.id = u.organization_id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
            WHERE u.id = app_current_user_id() AND u.role = 'ADMIN' AND u.is_first_admin
              AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE';
           IF NOT FOUND THEN
               RETURN;  -- only the current Main Admin
           END IF;
           RETURN QUERY
           SELECT tr.id, tr.from_full_name, tr.from_username, tr.from_email, tr.status, tr.created_at, tr.decided_at
             FROM ownership_transfers tr
            WHERE tr.organization_id = me.organization_id AND tr.to_user_id = me.id AND tr.status = 'ACCEPTED'
              AND tr.from_full_name IS NOT NULL
            ORDER BY tr.decided_at DESC;
       END $$"""


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA:
        op.execute(statement)
    op.execute(_deletion_function())
    op.execute(_HISTORY)
    op.execute("REVOKE ALL ON FUNCTION app_ownership_snapshot() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION app_ownership_history() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_ownership_history() TO {app}")


def downgrade() -> None:
    """Back to 0011. The snapshots are dropped."""
    op.execute("DROP FUNCTION IF EXISTS app_ownership_history()")
    op.execute(_deletion_function_0011().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute("DROP TRIGGER IF EXISTS trg_ownership_snapshot ON ownership_transfers")
    op.execute("DROP FUNCTION IF EXISTS app_ownership_snapshot()")
    op.execute("DROP INDEX IF EXISTS ix_ownership_transfers_accepted_to")
    op.execute("ALTER TABLE ownership_transfers DROP CONSTRAINT IF EXISTS ck_ownership_transfers_snapshot")
    op.execute(
        "ALTER TABLE ownership_transfers DROP COLUMN from_full_name, DROP COLUMN from_username, DROP COLUMN from_email"
    )
