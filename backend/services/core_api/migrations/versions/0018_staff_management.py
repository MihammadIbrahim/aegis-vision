"""Staff accounts: Guard creation by Admins only, staff-set Guard passwords, and Admin notifications
about Supervisors' Guard changes (web Phase 8; decision D-017).

* **Guard creation is Admin-only.** The eight Guard step-up and creation functions of 0009 accepted an
  active Admin *or Supervisor* as the acting user; they are re-created with ``u.role = 'ADMIN'``. A
  Supervisor's unfinished confirmation or form can no longer be used.
* ``app_staff_actor_lookup()``: the signed-in, active Admin's or Supervisor's own password hash and
  lock (the shape of ``app_step_up_lookup``), so a staff action can be confirmed with the current
  password. Wrong passwords count toward the usual sign-in lock.
* ``app_staff_set_guard_password(guard_user_id, password_hash)``: an active Admin or Supervisor sets
  the password of an active Guard of their own campus. ``password_changed_at`` moves, so the Guard's
  earlier sessions stop working; the lock is cleared and pending Guard profile changes are superseded.
* ``user_notifications``: new kinds ``GUARD_UPDATED``, ``GUARD_PASSWORD_SET`` and
  ``GUARD_LOCATION_CHANGED`` (sent to the campus's active Admins when a Supervisor acts on a Guard)
  and a third possible target, ``subject_user_id`` (the Guard, same campus).

Everything else (staff list, Guard details, deactivation, audit entries) uses the tables the app role
can already reach under Row-Level Security (``users``, ``guard_location_assignments``,
``organization_audit_logs``, ``user_notifications``).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-11
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _migration(file_name: str) -> ModuleType:
    """Earlier migrations never change once applied, so reading their SQL text is stable."""
    spec = importlib.util.spec_from_file_location(file_name, Path(__file__).with_name(file_name))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Guard creation: Admins only -----------------------------------------------------------------

_STAFF_ACTOR = "u.role IN ('ADMIN', 'SUPERVISOR')"
_ADMIN_ACTOR = "u.role = 'ADMIN'"


def _guard_creation_functions(actor_rule: str) -> list[str]:
    _, functions = _migration("0009_guards.py")._guard_creation_functions()
    assert sum(_STAFF_ACTOR in f for f in functions) >= 1
    return [
        f.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(_STAFF_ACTOR, actor_rule)
        for f in functions
    ]


# --- Staff actions ----------------------------------------------------------------------------------

_ACTOR_IS_ACTIVE_STAFF = """EXISTS (
    SELECT 1 FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role IN ('ADMIN', 'SUPERVISOR')
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""

_FUNCTIONS = [
    f"""CREATE FUNCTION app_staff_actor_lookup()
       RETURNS TABLE (user_id uuid, password_hash text, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           IF NOT {_ACTOR_IS_ACTIVE_STAFF} THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT c.user_id, c.password_hash, c.locked_until
             FROM user_credentials c WHERE c.user_id = app_current_user_id() FOR UPDATE;
       END $$""",
    f"""CREATE FUNCTION app_staff_set_guard_password(p_guard_user_id uuid, p_password_hash text)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF NOT {_ACTOR_IS_ACTIVE_STAFF} THEN
               RETURN 'NOT_AUTHORIZED';
           END IF;
           IF p_password_hash IS NULL OR p_password_hash NOT LIKE '$argon2id$%' THEN
               RAISE EXCEPTION 'not an argon2id hash' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT EXISTS (
               SELECT 1 FROM users g
                WHERE g.id = p_guard_user_id AND g.role = 'GUARD' AND g.status = 'ACTIVE'
                  AND g.organization_id = app_actor_org_id() AND g.campus_id = app_actor_campus_id()) THEN
               RETURN 'NOT_FOUND';
           END IF;
           UPDATE user_credentials c
              SET password_hash = p_password_hash, password_changed_at = now(),
                  failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE c.user_id = p_guard_user_id;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           UPDATE guard_profile_changes gpc
              SET status = 'SUPERSEDED', email = NULL, phone = NULL, password_hash = NULL, new_email_digest = NULL
            WHERE gpc.user_id = p_guard_user_id AND gpc.status = 'PENDING';
           RETURN 'UPDATED';
       END $$""",
]
_FUNCTION_SIGNATURES = ["app_staff_actor_lookup()", "app_staff_set_guard_password(uuid, text)"]

# --- Notifications about a Guard ----------------------------------------------------------------

_KINDS_0015 = (
    "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT', "
    "'ALERT_ASSISTANCE_REQUESTED', 'ALERT_RESOLVED', 'ALERT_DISMISSED', 'MANUAL_REPORT_SUBMITTED'"
)
_STAFF_KINDS = "'GUARD_UPDATED', 'GUARD_PASSWORD_SET', 'GUARD_LOCATION_CHANGED'"

_NOTIFICATIONS = [
    "ALTER TABLE user_notifications ADD COLUMN subject_user_id uuid",
    """ALTER TABLE user_notifications ADD CONSTRAINT fk_user_notifications_subject
       FOREIGN KEY (organization_id, campus_id, subject_user_id)
       REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target",
    """ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target
       CHECK (num_nonnulls(alert_id, manual_report_id, subject_user_id) = 1)""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind
       CHECK (kind IN ({_KINDS_0015}, {_STAFF_KINDS}))""",
    """CREATE INDEX ix_user_notifications_subject ON user_notifications (subject_user_id)
       WHERE subject_user_id IS NOT NULL""",
]


def upgrade() -> None:
    app = _app_role()
    for statement in _guard_creation_functions(_ADMIN_ACTOR) + _FUNCTIONS + _NOTIFICATIONS:
        op.execute(statement)
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    """Back to 0017: Supervisors may create Guards again; staff notifications are removed."""
    op.execute(f"DELETE FROM user_notifications WHERE kind IN ({_STAFF_KINDS})")
    op.execute("DROP INDEX IF EXISTS ix_user_notifications_subject")
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind")
    op.execute(
        f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({_KINDS_0015}))"
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target")
    op.execute(
        "ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target "
        "CHECK (num_nonnulls(alert_id, manual_report_id) = 1)"
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT fk_user_notifications_subject")
    op.execute("ALTER TABLE user_notifications DROP COLUMN subject_user_id")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for statement in _guard_creation_functions(_STAFF_ACTOR):
        op.execute(statement)
