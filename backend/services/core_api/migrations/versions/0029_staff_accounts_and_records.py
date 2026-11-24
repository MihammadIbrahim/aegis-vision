"""Admin profile, ID regeneration, rejoining, permanent deletion and user-record history (Org Admin pages 11–13
and 24; decision D-024).

* ``admin_profile_changes`` and ``app_admin_profile_*``: an Admin changes their own name, username, email,
  phone or password with their current password and a code sent to their email (and one to a new email).
  The same flow as the Supervisor profile of migration 0008, generated from it, plus the phone number.
* ``app_display_id_regenerate``: an Admin gives themselves a new Admin ID; an Admin or Supervisor gives a
  Guard of their campus a new Guard ID. The internal account ID never changes, so history stays linked.
  Supervisor IDs stay permanent (the 0008 trigger).
* ``app_staff_rejoin_password``: an Admin sets the password of a returning Supervisor or Guard.
* ``app_user_permanently_delete``: an Admin erases a former (LEFT) Supervisor's or Guard's personal data:
  name, username, email, phone, credentials, location, notifications and pending changes; the copies of
  the name kept in reports, chats, cases and evidence become "Deleted user". The account row stays with
  its IDs so alerts, cases and audit records remain linked (requirements Section 12).
* ``user_status_history``: every status change (Active / Left), who made it and when.
* Notification GUARD_ID_REGENERATED.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-18
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DELETED_NAME = "Deleted user"


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _load(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"_revision_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _swap(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"0008 text not found: {old[:60]!r}")
    return text.replace(old, new)


def _admin_profile_functions() -> list[str]:
    """The 0008 Supervisor profile functions, for Admins, on their own table, with the phone number."""
    m08 = _load("0008_supervisor_profile.py")
    gate = m08._ACTOR_IS_ACTIVE_SUPERVISOR
    admin_gate = _swap(gate, "u.role = 'SUPERVISOR'", "u.role = 'ADMIN'")
    result = []
    for function in m08._FUNCTIONS:
        if "FUNCTION app_profile_" not in function:
            continue
        text = function.replace(gate, admin_gate)
        text = text.replace("supervisor_profile_changes", "admin_profile_changes")
        text = text.replace("FUNCTION app_profile_", "FUNCTION app_admin_profile_")
        text = text.replace("new_email_digest = NULL", "new_email_digest = NULL, phone = NULL")
        text = text.replace("(Supervisors only)", "(Admins only)")
        if "app_admin_profile_change_submit" in text:
            text = _swap(text, "p_new_email_digest text, p_ttl_seconds integer)",
                         "p_new_email_digest text, p_ttl_seconds integer, p_phone text)")  # fmt: skip
            columns = "password_hash,\n" + " " * 51 + "new_email_digest,"
            text = _swap(text, columns, columns + " phone,")
            text = _swap(text, "p_password_hash,\n                   p_new_email_digest,",
                         "p_password_hash,\n                   p_new_email_digest, p_phone,")  # fmt: skip
        if "app_admin_profile_change_apply" in text:
            email = "email = COALESCE(pc.email, u.email)"
            text = _swap(text, email, email + ",\n" + " " * 22 + "phone = COALESCE(pc.phone, u.phone)")
        result.append(text)
    return result


_ADMIN_PROFILE_SIGNATURES = [
    "app_admin_profile_lookup()",
    "app_admin_profile_change_submit(uuid, text, text, text, text, text, integer, text)",
    "app_admin_profile_change_issue_otp(uuid, boolean, uuid, text, integer, integer, integer, integer)",
    "app_admin_profile_change_verify_otp(uuid, boolean, text)",
    "app_admin_profile_change_apply(uuid)",
    "app_admin_profile_change_cancel(uuid)",
]

_KINDS_0026 = (
    "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT', 'ALERT_ASSISTANCE_REQUESTED', "
    "'ALERT_RESOLVED', 'ALERT_DISMISSED', 'MANUAL_REPORT_SUBMITTED', "
    "'GUARD_UPDATED', 'GUARD_PASSWORD_SET', 'GUARD_LOCATION_CHANGED', "
    "'CAMERA_OFFLINE', 'CAMERA_ONLINE', 'STORAGE_WARNING', 'STORAGE_CRITICAL', "
    "'INCIDENT_ASSIGNED', 'INCIDENT_STATUS_CHANGED', 'INCIDENT_INVITED', "
    "'ALERT_AUTO_ESCALATED', 'ALERT_ASSISTANCE_BROADCAST', 'ALERT_HANDLER_ASSIGNED'"
)
_NEW_KINDS = "'GUARD_ID_REGENERATED'"

_SCHEMA = [
    """CREATE TABLE admin_profile_changes (
           id                        uuid PRIMARY KEY,
           user_id                   uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status                    text NOT NULL CONSTRAINT ck_admin_profile_changes_status
                                         CHECK (status IN ('PENDING', 'APPLIED', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')),
           full_name                 text CONSTRAINT ck_admin_profile_changes_full_name
                                         CHECK (length(full_name) BETWEEN 2 AND 120),
           username                  text CONSTRAINT ck_admin_profile_changes_username
                                         CHECK (username ~ '^[A-Za-z0-9_.-]{3,64}$'),
           email                     text CONSTRAINT ck_admin_profile_changes_email
                                         CHECK (length(email) BETWEEN 3 AND 254),
           phone                     text CONSTRAINT ck_admin_profile_changes_phone
                                         CHECK (phone ~ '^\\+?[0-9]{7,15}$'),
           password_hash             text CONSTRAINT ck_admin_profile_changes_argon2
                                         CHECK (password_hash LIKE '$argon2id$%'),
           new_email_digest          text CONSTRAINT ck_admin_profile_changes_digest
                                         CHECK (new_email_digest ~ '^[0-9a-f]{64}$'),
           created_at                timestamptz NOT NULL DEFAULT now(),
           expires_at                timestamptz NOT NULL,
           current_email_verified_at timestamptz,
           new_email_verified_at     timestamptz,
           applied_at                timestamptz,
           CONSTRAINT ck_admin_profile_changes_expiry CHECK (expires_at > created_at),
           CONSTRAINT ck_admin_profile_changes_new_email CHECK ((email IS NULL) = (new_email_digest IS NULL)),
           CONSTRAINT ck_admin_profile_changes_values_only_while_pending CHECK (
               status = 'PENDING'
               OR num_nonnulls(full_name, username, email, phone, password_hash, new_email_digest) = 0)
       )""",
    "CREATE INDEX ix_admin_profile_changes_user ON admin_profile_changes (user_id, status)",
    "ALTER TABLE admin_profile_changes ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON admin_profile_changes FROM PUBLIC",
    "ALTER TABLE users ADD COLUMN deleted_at timestamptz",
    "ALTER TABLE users ADD CONSTRAINT ck_users_deleted_is_left CHECK (deleted_at IS NULL OR status = 'LEFT')",
    """CREATE TABLE user_status_history (
           id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
           user_id          uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           from_status      text,
           to_status        text NOT NULL CONSTRAINT ck_user_status_history_to CHECK (to_status IN ('ACTIVE', 'LEFT')),
           event            text NOT NULL CONSTRAINT ck_user_status_history_event
                                CHECK (event IN ('CREATED', 'DEACTIVATED', 'REACTIVATED', 'PERMANENTLY_DELETED')),
           changed_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           changed_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
           CONSTRAINT fk_user_status_history_user FOREIGN KEY (organization_id, campus_id, user_id)
               REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_user_status_history_user ON user_status_history (user_id, changed_at)",
    """INSERT INTO user_status_history (user_id, organization_id, campus_id, from_status, to_status, event, changed_at)
       SELECT id, organization_id, campus_id, NULL, 'ACTIVE', 'CREATED', created_at FROM users""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind
        CHECK (kind IN ({_KINDS_0026}, {_NEW_KINDS}))""",
]

_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"

_POLICIES = [
    "ALTER TABLE user_status_history ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE user_status_history FORCE ROW LEVEL SECURITY",
    f"""CREATE POLICY user_status_history_select ON user_status_history FOR SELECT
        USING ({_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR'))""",
]

# The acting user, active, of an active organization and campus, with this role.
_ACTOR = """SELECT u.id, u.organization_id, u.campus_id, u.role INTO me FROM users u
              JOIN organizations o ON o.id = u.organization_id AND o.status = 'ACTIVE'
              JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
                              AND ca.status = 'ACTIVE'
             WHERE u.id = app_current_user_id() AND u.status = 'ACTIVE'"""

_SCRUB = [
    ("manual_reports", "reporter_name", "reporter_user_id"),
    ("alert_messages", "sender_name", "sender_user_id"),
    ("evidence_items", "saved_by_name", "saved_by"),
    ("incidents", "created_by_name", "created_by"),
    ("incidents", "assigned_name", "assigned_to"),
    ("incident_events", "actor_name", "actor_user_id"),
    ("incident_messages", "sender_name", "sender_user_id"),
    ("incident_alerts", "linked_by_name", "linked_by"),
    ("alerts", "handled_by_name", "handled_by"),
    ("person_searches", "created_by_name", "created_by"),
    ("person_search_candidates", "reviewed_by_name", "reviewed_by"),
    ("campus_ai_settings", "updated_by_name", "updated_by"),
    ("camera_ai_settings", "updated_by_name", "updated_by"),
]
_SCRUB_SQL = "\n".join(
    f"           UPDATE {table} SET {column} = '{DELETED_NAME}' WHERE {owner} = p_user_id;"
    for table, column, owner in _SCRUB
)

_FUNCTIONS = [
    # Status history, written whenever a status changes (by whom: the signed-in user, if any).
    """CREATE FUNCTION app_user_status_history() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF OLD.status IS DISTINCT FROM NEW.status THEN
               INSERT INTO user_status_history (user_id, organization_id, campus_id, from_status, to_status, event,
                                                changed_by)
               VALUES (NEW.id, NEW.organization_id, NEW.campus_id, OLD.status, NEW.status,
                       CASE NEW.status WHEN 'LEFT' THEN 'DEACTIVATED' ELSE 'REACTIVATED' END,
                       app_current_user_id());
           END IF;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_users_status_history AFTER UPDATE OF status ON users
       FOR EACH ROW EXECUTE FUNCTION app_user_status_history()""",
    """CREATE FUNCTION app_user_created_history() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           INSERT INTO user_status_history (user_id, organization_id, campus_id, from_status, to_status, event,
                                            changed_by)
           VALUES (NEW.id, NEW.organization_id, NEW.campus_id, NULL, NEW.status, 'CREATED', app_current_user_id());
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_users_created_history AFTER INSERT ON users
       FOR EACH ROW EXECUTE FUNCTION app_user_created_history()""",
    # A new displayed ID: an Admin for themselves; an Admin or Supervisor for an active Guard of their campus.
    f"""CREATE FUNCTION app_display_id_regenerate(p_user_id uuid, p_new_id text) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
           t users%ROWTYPE;
       BEGIN
           {_ACTOR};
           IF NOT FOUND THEN
               RETURN 'NOT_ALLOWED';
           END IF;
           SELECT * INTO t FROM users x
            WHERE x.id = p_user_id AND x.organization_id = me.organization_id AND x.campus_id = me.campus_id
            FOR UPDATE;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF NOT ((t.id = me.id AND me.role = 'ADMIN')
                   OR (t.role = 'GUARD' AND t.status = 'ACTIVE' AND me.role IN ('ADMIN', 'SUPERVISOR'))) THEN
               RETURN 'NOT_ALLOWED';
           END IF;
           BEGIN
               UPDATE users x SET display_id = p_new_id WHERE x.id = t.id;
           EXCEPTION WHEN unique_violation THEN
               RETURN 'DISPLAY_ID_TAKEN';
           END;
           RETURN 'REGENERATED';
       END $$""",
    # The password of a returning Supervisor or Guard (rejoining), set by an Admin of the same campus.
    f"""CREATE FUNCTION app_staff_rejoin_password(p_user_id uuid, p_password_hash text) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
       BEGIN
           {_ACTOR} AND u.role = 'ADMIN';
           IF NOT FOUND THEN
               RETURN 'NOT_ALLOWED';
           END IF;
           IF p_password_hash NOT LIKE '$argon2id$%' THEN
               RAISE EXCEPTION 'not an Argon2id hash' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT EXISTS (SELECT 1 FROM users x WHERE x.id = p_user_id AND x.organization_id = me.organization_id
                             AND x.campus_id = me.campus_id AND x.role IN ('SUPERVISOR', 'GUARD')
                             AND x.deleted_at IS NULL) THEN
               RETURN 'NOT_FOUND';
           END IF;
           INSERT INTO user_credentials AS c (user_id, password_hash, password_changed_at)
           VALUES (p_user_id, p_password_hash, now())
           ON CONFLICT (user_id) DO UPDATE
              SET password_hash = EXCLUDED.password_hash, password_changed_at = now(),
                  failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL;
           RETURN 'UPDATED';
       END $$""",
    # Permanent deletion of a former Supervisor's or Guard's personal data (Admins; the API checks the
    # password and the typed confirmation first).
    f"""CREATE FUNCTION app_user_permanently_delete(p_user_id uuid) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
           t users%ROWTYPE;
       BEGIN
           {_ACTOR} AND u.role = 'ADMIN';
           IF NOT FOUND THEN
               RETURN 'NOT_ALLOWED';
           END IF;
           SELECT * INTO t FROM users x
            WHERE x.id = p_user_id AND x.organization_id = me.organization_id AND x.campus_id = me.campus_id
              AND x.role IN ('SUPERVISOR', 'GUARD')
            FOR UPDATE;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF t.deleted_at IS NOT NULL THEN
               RETURN 'ALREADY_DELETED';
           END IF;
           IF t.status <> 'LEFT' THEN
               RETURN 'STILL_ACTIVE';
           END IF;
           DELETE FROM user_credentials WHERE user_id = p_user_id;
           DELETE FROM guard_location_assignments WHERE guard_user_id = p_user_id;
           DELETE FROM user_notifications WHERE user_id = p_user_id;
           DELETE FROM admin_login_sessions WHERE user_id = p_user_id;
           DELETE FROM password_reset_requests WHERE user_id = p_user_id;
           DELETE FROM supervisor_profile_changes WHERE user_id = p_user_id;
           DELETE FROM guard_profile_changes WHERE user_id = p_user_id;
           DELETE FROM incident_participants WHERE guard_user_id = p_user_id;
{_SCRUB_SQL}
           UPDATE users x SET full_name = '{DELETED_NAME}', username = NULL, phone = NULL,
                              email = 'deleted-' || x.id::text || '@deleted.invalid', deleted_at = now()
            WHERE x.id = p_user_id;
           INSERT INTO user_status_history (user_id, organization_id, campus_id, from_status, to_status, event,
                                            changed_by)
           VALUES (t.id, t.organization_id, t.campus_id, 'LEFT', 'LEFT', 'PERMANENTLY_DELETED', me.id);
           RETURN 'DELETED';
       END $$""",
]

_SIGNATURES = [
    "app_display_id_regenerate(uuid, text)",
    "app_staff_rejoin_password(uuid, text)",
    "app_user_permanently_delete(uuid)",
]
_TRIGGER_FUNCTIONS = ["app_user_status_history()", "app_user_created_history()"]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _POLICIES:
        op.execute(statement)
    op.execute(f"GRANT SELECT ON user_status_history TO {app}")
    for statement in _admin_profile_functions() + _FUNCTIONS:
        op.execute(statement)
    for signature in _ADMIN_PROFILE_SIGNATURES + _SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
    for signature in _TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_users_created_history ON users")
    op.execute("DROP TRIGGER IF EXISTS trg_users_status_history ON users")
    for signature in _SIGNATURES + _TRIGGER_FUNCTIONS + _ADMIN_PROFILE_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(f"DELETE FROM user_notifications WHERE kind IN ({_NEW_KINDS})")
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind")
    op.execute(
        f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({_KINDS_0026}))"
    )
    op.execute("DROP TABLE IF EXISTS user_status_history")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_deleted_is_left")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deleted_at")
    op.execute("DROP TABLE IF EXISTS admin_profile_changes")
