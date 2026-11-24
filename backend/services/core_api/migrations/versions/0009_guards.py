"""Guards: creation by an Admin or Supervisor, sign-in, forgot password, profile changes.

* Guard display IDs may be 6 capital letters + 21 digits (27 characters), the format new
  Guards get. Earlier ``GRD-`` IDs stay valid.
* Guard creation follows the Supervisor-creation pattern of migration 0007. The acting user
  re-confirms password + email code first, then the Guard's email is verified by code.
  - ``guard_step_up_grants``: separate from the Admin's Supervisor grants, so neither can be
    used for the other.
  - ``guard_creations``: the pending Guard form.

  The acting user must be an active Admin **or Supervisor**. The Guard is created in the
  actor's own organization and campus.
* Guard sign-in: ``app_guard_login_lookup`` (Guard ID + email). The shared sign-in steps of
  migration 0006 (start, issue code, verify) now also accept active Guards; the Admin and
  Supervisor look-up is unchanged and never matches a Guard.
* Forgot password with Guard ID + email: ``app_password_reset_request_by_guard_id``. The other
  reset steps of 0007 already work for any active account with a password.
* ``guard_profile_changes``: a Guard may change only email, phone number and password. The
  rules are those of 0008: current password, code to the registered email (and to a new
  email), applied only after verification.

As before, the app role gets no rights on new tables, and the actor-side functions act only
for the signed-in caller.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
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


def _replace_all(text: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        text = text.replace(old, new)
    return text


# --- Shared sign-in steps (0006): also accept Guards -----------------------------------------

_ADMIN_ONLY = "u.role = 'ADMIN'"


def _shared_login_steps(roles: str) -> list[str]:
    statements = []
    for function in _migration("0006_organization_admin_login.py")._FUNCTIONS:
        if _ADMIN_ONLY not in function or "app_admin_login_lookup" in function:
            continue  # the Admin/Supervisor look-up stays as it is
        body = function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        statements.append(body.replace(_ADMIN_ONLY, f"u.role IN {roles}"))
    assert len(statements) == 3, statements  # start, issue, verify
    return statements


_STAFF = "('ADMIN', 'SUPERVISOR')"
_STAFF_AND_GUARDS = "('ADMIN', 'SUPERVISOR', 'GUARD')"

# --- Guard creation, derived from 0007's Supervisor creation -----------------------------------

_GUARD_CREATION_RENAMES = [
    ("app_step_up_", "app_guard_step_up_"),
    ("admin_step_up_grants", "guard_step_up_grants"),
    ("'ADMIN_STEP_UP'", "'GUARD_STEP_UP'"),
    ("'ADMIN_STEP_UP:'", "'GUARD_STEP_UP:'"),
    ("app_supervisor_creation_", "app_guard_creation_"),
    ("supervisor_creations", "guard_creations"),
    ("'SUPERVISOR_EMAIL'", "'GUARD_EMAIL'"),
    ("'SUPERVISOR_EMAIL:'", "'GUARD_EMAIL:'"),
    ("supervisor_user_id", "guard_user_id"),
    (
        "VALUES (p_user_id, s.organization_id, s.campus_id, 'SUPERVISOR', 'ACTIVE'",
        "VALUES (p_user_id, s.organization_id, s.campus_id, 'GUARD', 'ACTIVE'",
    ),
    # The acting user may be an Admin or a Supervisor.
    (_ADMIN_ONLY, f"u.role IN {_STAFF}"),
]


def _guard_creation_functions() -> tuple[list[str], list[str]]:
    m0007 = _migration("0007_supervisors_and_password_reset.py")
    helpers = [h for h in m0007._HELPERS if "app_supervisor_creation_expire" in h]
    prefixes = ("CREATE FUNCTION app_step_up_", "CREATE FUNCTION app_supervisor_creation_")
    functions = [f for f in m0007._FUNCTIONS if any(prefix in f for prefix in prefixes)]
    assert len(helpers) == 1 and len(functions) == 8
    derived_helpers = [_replace_all(h, _GUARD_CREATION_RENAMES) for h in helpers]
    derived = [_replace_all(f, _GUARD_CREATION_RENAMES) for f in functions]
    for text in derived:  # nothing Supervisor-specific may remain, except the actor rule
        leftover = text.lower().replace("u.role in ('admin', 'supervisor')", "")
        assert "supervisor" not in leftover, leftover
    return derived_helpers, derived


# --- Display IDs, code purposes ---------------------------------------------------------------

_DISPLAY_ID_RULE_0007 = """CHECK (
    CASE role
        WHEN 'ADMIN' THEN display_id ~ '^(ADM-[0-9]{11}|[A-Z]{6}[0-9]{11})$'
        WHEN 'SUPERVISOR' THEN display_id ~ '^(SUP-[0-9]{11}|[A-Z]{6}[0-9]{11}|[A-Z]{6}[0-9]{21})$'
        WHEN 'GUARD' THEN display_id ~ '^GRD-[0-9]{11}$'
        ELSE false
    END)"""
_DISPLAY_ID_RULE = _DISPLAY_ID_RULE_0007.replace("'^GRD-[0-9]{11}$'", "'^(GRD-[0-9]{11}|[A-Z]{6}[0-9]{21})$'")
_PURPOSES_0008 = "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET', 'PROFILE_CHANGE', 'PROFILE_NEW_EMAIL')"
_PURPOSES = (
    "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET', 'PROFILE_CHANGE', 'PROFILE_NEW_EMAIL', "
    "'GUARD_STEP_UP', 'GUARD_EMAIL')"
)

_SCHEMA = [
    "ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format",
    f"ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format {_DISPLAY_ID_RULE}",
    "ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose",
    f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES})",
    """CREATE TABLE guard_step_up_grants (
           id           uuid PRIMARY KEY,
           user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           created_at   timestamptz NOT NULL DEFAULT now(),
           expires_at   timestamptz NOT NULL,
           verified_at  timestamptz,
           used_at      timestamptz,
           CONSTRAINT ck_guard_step_up_expiry CHECK (expires_at > created_at)
       )""",
    """CREATE TABLE guard_creations (
           id                 uuid PRIMARY KEY,
           organization_id    uuid NOT NULL,
           campus_id          uuid NOT NULL,
           created_by         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status             text NOT NULL CONSTRAINT ck_guard_creations_status
                                  CHECK (status IN ('PENDING', 'COMPLETED', 'EXPIRED')),
           full_name          text CONSTRAINT ck_guard_creations_full_name CHECK (length(full_name) BETWEEN 2 AND 120),
           username           text CONSTRAINT ck_guard_creations_username CHECK (username ~ '^[A-Za-z0-9_.-]{3,64}$'),
           email              text CONSTRAINT ck_guard_creations_email CHECK (length(email) BETWEEN 3 AND 254),
           phone              text CONSTRAINT ck_guard_creations_phone CHECK (phone ~ '^\\+?[0-9]{7,15}$'),
           password_hash      text CONSTRAINT ck_guard_creations_argon2 CHECK (password_hash LIKE '$argon2id$%'),
           recipient_digest   text CONSTRAINT ck_guard_creations_digest CHECK (recipient_digest ~ '^[0-9a-f]{64}$'),
           created_at         timestamptz NOT NULL DEFAULT now(),
           expires_at         timestamptz NOT NULL,
           email_verified_at  timestamptz,
           completed_at       timestamptz,
           guard_user_id      uuid REFERENCES users (id) ON DELETE SET NULL,
           CONSTRAINT fk_guard_creations_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           CONSTRAINT ck_guard_creations_details_only_while_pending CHECK (
               CASE WHEN status = 'PENDING'
                    THEN num_nonnulls(full_name, username, email, phone, password_hash, recipient_digest) = 6
                    ELSE num_nonnulls(full_name, username, email, phone, password_hash, recipient_digest) = 0
               END)
       )""",
    "CREATE INDEX ix_guard_creations_pending ON guard_creations (expires_at) WHERE status = 'PENDING'",
    """CREATE TABLE guard_profile_changes (
           id                         uuid PRIMARY KEY,
           user_id                    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status                     text NOT NULL CONSTRAINT ck_guard_changes_status CHECK (
                                          status IN ('PENDING', 'APPLIED', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')),
           email                      text CONSTRAINT ck_guard_changes_email
                                          CHECK (length(email) BETWEEN 3 AND 254),
           phone                      text CONSTRAINT ck_guard_changes_phone CHECK (phone ~ '^\\+?[0-9]{7,15}$'),
           password_hash              text CONSTRAINT ck_guard_changes_argon2
                                          CHECK (password_hash LIKE '$argon2id$%'),
           new_email_digest           text CONSTRAINT ck_guard_changes_digest
                                          CHECK (new_email_digest ~ '^[0-9a-f]{64}$'),
           created_at                 timestamptz NOT NULL DEFAULT now(),
           expires_at                 timestamptz NOT NULL,
           current_email_verified_at  timestamptz,
           new_email_verified_at      timestamptz,
           applied_at                 timestamptz,
           CONSTRAINT ck_guard_changes_expiry CHECK (expires_at > created_at),
           CONSTRAINT ck_guard_changes_new_email CHECK ((email IS NULL) = (new_email_digest IS NULL)),
           CONSTRAINT ck_guard_changes_values_only_while_pending CHECK (
               status = 'PENDING' OR num_nonnulls(email, phone, password_hash, new_email_digest) = 0)
       )""",
    "CREATE INDEX ix_guard_changes_user ON guard_profile_changes (user_id, status)",
]

_ACTOR_IS_ACTIVE_GUARD = """EXISTS (
    SELECT 1 FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role = 'GUARD'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""
_END_CHANGE = """UPDATE guard_profile_changes gpc
             SET status = {status}, email = NULL, phone = NULL, password_hash = NULL, new_email_digest = NULL
           WHERE {where}"""
_SUPERSEDE_PENDING = _END_CHANGE.format(
    status="'SUPERSEDED'", where="gpc.user_id = app_current_user_id() AND gpc.status = 'PENDING'"
)
_MARK_APPLIED = _END_CHANGE.format(status="'APPLIED'", where="gpc.id = p_change_id")
_CANCEL_PENDING = _END_CHANGE.format(
    status="'CANCELLED'",
    where="gpc.id = p_change_id AND gpc.user_id = app_current_user_id() AND gpc.status = 'PENDING'",
)
_GUARD_CHANGE_ROW = f"""SELECT * INTO pc FROM guard_profile_changes c
            WHERE c.id = p_change_id AND c.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_GUARD}"""

_FUNCTIONS = [
    # Guard sign-in look-up: exact Guard ID + registered email; Guards only.
    """CREATE FUNCTION app_guard_login_lookup(p_guard_id text, p_email text)
       RETURNS TABLE (user_id uuid, password_hash text, eligible boolean, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           RETURN QUERY
           SELECT u.id, c.password_hash,
                  (u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE'), c.locked_until
             FROM users u
             JOIN organizations o ON o.id = u.organization_id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
             JOIN user_credentials c ON c.user_id = u.id
            WHERE u.display_id = p_guard_id AND lower(u.email) = lower(p_email) AND u.role = 'GUARD'
              FOR UPDATE OF c;
       END $$""",
    # Forgot password with Guard ID + email; a request is stored whether or not it matches.
    """CREATE FUNCTION app_password_reset_request_by_guard_id(
           p_request_id uuid, p_guard_id text, p_email text, p_subject_digest text, p_ttl_seconds integer)
       RETURNS void
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_user uuid;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'reset lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT u.id INTO v_user
             FROM users u
             JOIN organizations o ON o.id = u.organization_id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
             JOIN user_credentials c ON c.user_id = u.id
            WHERE u.display_id = p_guard_id AND lower(u.email) = lower(p_email) AND u.role = 'GUARD'
              AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE';
           DELETE FROM password_reset_requests pr WHERE pr.created_at < now() - interval '1 day';
           INSERT INTO password_reset_requests (id, user_id, subject_digest, created_at, expires_at)
           VALUES (p_request_id, v_user, p_subject_digest, now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
    # --- Guard profile (email, phone, password only) ---
    f"""CREATE FUNCTION app_guard_profile_lookup()
       RETURNS TABLE (user_id uuid, password_hash text, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           IF NOT {_ACTOR_IS_ACTIVE_GUARD} THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT c.user_id, c.password_hash, c.locked_until
             FROM user_credentials c WHERE c.user_id = app_current_user_id() FOR UPDATE;
       END $$""",
    f"""CREATE FUNCTION app_guard_profile_change_submit(
           p_change_id uuid, p_email text, p_phone text, p_password_hash text, p_new_email_digest text,
           p_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'change lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT {_ACTOR_IS_ACTIVE_GUARD} THEN
               RETURN 'NOT_AUTHORIZED';
           END IF;
           UPDATE user_credentials uc SET failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = app_current_user_id();
           {_SUPERSEDE_PENDING};
           DELETE FROM guard_profile_changes old
            WHERE old.created_at < now() - interval '1 day' AND old.status <> 'PENDING';
           INSERT INTO guard_profile_changes (id, user_id, status, email, phone, password_hash, new_email_digest,
                                              created_at, expires_at)
           VALUES (p_change_id, app_current_user_id(), 'PENDING', p_email, p_phone, p_password_hash,
                   p_new_email_digest, now(), now() + make_interval(secs => p_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    f"""CREATE FUNCTION app_guard_profile_change_issue_otp(
           p_change_id uuid, p_new_email boolean, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc guard_profile_changes%ROWTYPE;
           r record;
       BEGIN
           {_GUARD_CHANGE_ROW} OR (p_new_email AND pc.email IS NULL) THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF pc.status = 'APPLIED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF pc.status <> 'PENDING' OR pc.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF p_new_email THEN
               SELECT * INTO r FROM app_otp_issue(
                   'PROFILE_NEW_EMAIL', pc.id, 'PROFILE_NEW_EMAIL:' || pc.new_email_digest,
                   p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           ELSE
               SELECT * INTO r FROM app_otp_issue('PROFILE_CHANGE', pc.id, 'PROFILE_CHANGE:' || pc.user_id::text,
                   p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           END IF;
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer,
               CASE WHEN p_new_email THEN pc.email ELSE (SELECT u.email FROM users u WHERE u.id = pc.user_id) END,
               pc.expires_at;
       END $$""",
    f"""CREATE FUNCTION app_guard_profile_change_verify_otp(p_change_id uuid, p_new_email boolean, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc guard_profile_changes%ROWTYPE;
           r record;
       BEGIN
           {_GUARD_CHANGE_ROW} OR (p_new_email AND pc.email IS NULL) THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer;
               RETURN;
           END IF;
           IF pc.status = 'APPLIED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer;
               RETURN;
           END IF;
           IF pc.status <> 'PENDING' OR pc.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer;
               RETURN;
           END IF;
           IF (p_new_email AND pc.new_email_verified_at IS NOT NULL)
              OR (NOT p_new_email AND pc.current_email_verified_at IS NOT NULL) THEN
               RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify(CASE WHEN p_new_email THEN 'PROFILE_NEW_EMAIL' ELSE 'PROFILE_CHANGE' END,
                                               pc.id, p_code_hash);
           IF r.outcome = 'VERIFIED' THEN
               IF p_new_email THEN
                   UPDATE guard_profile_changes c SET new_email_verified_at = now() WHERE c.id = pc.id;
               ELSE
                   UPDATE guard_profile_changes c SET current_email_verified_at = now() WHERE c.id = pc.id;
               END IF;
           END IF;
           RETURN QUERY SELECT r.outcome, r.attempts_remaining;
       END $$""",
    f"""CREATE FUNCTION app_guard_profile_change_apply(p_change_id uuid)
       RETURNS TABLE (outcome text, password_changed boolean)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc guard_profile_changes%ROWTYPE;
           v_constraint text;
       BEGIN
           {_GUARD_CHANGE_ROW} THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::boolean;
               RETURN;
           END IF;
           IF pc.status = 'APPLIED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::boolean;
               RETURN;
           END IF;
           IF pc.status <> 'PENDING' OR pc.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::boolean;
               RETURN;
           END IF;
           IF pc.current_email_verified_at IS NULL OR (pc.email IS NOT NULL AND pc.new_email_verified_at IS NULL) THEN
               RETURN QUERY SELECT 'EMAIL_NOT_VERIFIED'::text, NULL::boolean;
               RETURN;
           END IF;
           IF pc.email IS NOT NULL AND EXISTS (
               SELECT 1 FROM users u WHERE u.id <> pc.user_id AND lower(u.email) = lower(pc.email)) THEN
               RETURN QUERY SELECT 'EMAIL_ALREADY_REGISTERED'::text, NULL::boolean;
               RETURN;
           END IF;
           BEGIN
               UPDATE users u SET email = COALESCE(pc.email, u.email), phone = COALESCE(pc.phone, u.phone)
                WHERE u.id = pc.user_id;
               IF pc.password_hash IS NOT NULL THEN
                   UPDATE user_credentials uc SET password_hash = pc.password_hash, password_changed_at = now()
                    WHERE uc.user_id = pc.user_id;
                   DELETE FROM admin_login_sessions ls WHERE ls.user_id = pc.user_id AND ls.completed_at IS NULL;
               END IF;
           EXCEPTION WHEN unique_violation THEN
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               RETURN QUERY SELECT CASE v_constraint WHEN 'uq_users_email' THEN 'EMAIL_ALREADY_REGISTERED'
                                                     ELSE 'CONFLICT' END, NULL::boolean;
               RETURN;
           END;
           {_MARK_APPLIED};
           UPDATE guard_profile_changes c SET applied_at = now() WHERE c.id = p_change_id;
           RETURN QUERY SELECT 'APPLIED'::text, pc.password_hash IS NOT NULL;
       END $$""",
    f"""CREATE FUNCTION app_guard_profile_change_cancel(p_change_id uuid) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           {_CANCEL_PENDING};
           RETURN CASE WHEN FOUND THEN 'CANCELLED' ELSE 'NOT_FOUND' END;
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_guard_login_lookup(text, text)",
    "app_password_reset_request_by_guard_id(uuid, text, text, text, integer)",
    "app_guard_profile_lookup()",
    "app_guard_profile_change_submit(uuid, text, text, text, text, integer)",
    "app_guard_profile_change_issue_otp(uuid, boolean, uuid, text, integer, integer, integer, integer)",
    "app_guard_profile_change_verify_otp(uuid, boolean, text)",
    "app_guard_profile_change_apply(uuid)",
    "app_guard_profile_change_cancel(uuid)",
]
_CREATION_SIGNATURES = [
    "app_guard_step_up_lookup()",
    "app_guard_step_up_start(uuid, integer)",
    "app_guard_step_up_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_guard_step_up_verify_otp(uuid, text, integer)",
    "app_guard_creation_submit(uuid, uuid, text, text, text, text, text, text, integer)",
    "app_guard_creation_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_guard_creation_verify_otp(uuid, text)",
    "app_guard_creation_complete(uuid, uuid, text)",
]
_HELPER_SIGNATURES = ["app_guard_creation_expire(uuid)"]
_NEW_TABLES = ["guard_step_up_grants", "guard_creations", "guard_profile_changes"]


def upgrade() -> None:
    app = _app_role()
    creation_helpers, creation_functions = _guard_creation_functions()
    for statement in (
        _SCHEMA + creation_helpers + creation_functions + _FUNCTIONS + _shared_login_steps(_STAFF_AND_GUARDS)
    ):
        op.execute(statement)
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for signature in _HELPER_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for signature in _CREATION_SIGNATURES + _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for statement in _shared_login_steps(_STAFF):  # back to the 0007 rule
        op.execute(statement)
    for signature in reversed(_CREATION_SIGNATURES + _FUNCTION_SIGNATURES + _HELPER_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS guard_profile_changes, guard_creations, guard_step_up_grants")
    op.execute("DELETE FROM auth_otp_challenges WHERE purpose IN ('GUARD_STEP_UP', 'GUARD_EMAIL')")
    op.execute("ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose")
    op.execute(
        f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES_0008})"
    )
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format")
    # The 0007 rule, for new rows. Guards with 27-character IDs (if any) stay in place.
    op.execute(f"ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format {_DISPLAY_ID_RULE_0007} NOT VALID")
