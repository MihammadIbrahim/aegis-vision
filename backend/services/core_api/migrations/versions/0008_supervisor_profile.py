"""Supervisor profile changes, Supervisor IDs made permanent, and password reset by Supervisor ID.

* A Supervisor's display ID can never change: a trigger refuses any update of it.
* ``supervisor_profile_changes``: a requested change (name, username, email, password) that is
  applied only after the code sent to the registered email is verified. A new email must be
  verified with its own code too. Until then, and if verification fails, expires or is
  cancelled, the profile stays as it was. Requested values are erased once the change ends.
* Two more code purposes: ``PROFILE_CHANGE`` (registered email) and ``PROFILE_NEW_EMAIL``.
* ``app_password_reset_request_by_supervisor_id``: forgot-password with Supervisor ID + email.

The profile functions act only for the signed-in caller, and only for an active Supervisor
of an active organization and campus. The app role gets no rights on the new table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_ACTOR_IS_ACTIVE_SUPERVISOR = """EXISTS (
    SELECT 1 FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role = 'SUPERVISOR'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""
_PURPOSES_0007 = "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET')"
_PURPOSES = "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET', 'PROFILE_CHANGE', 'PROFILE_NEW_EMAIL')"

_SCHEMA = [
    "ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose",
    f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES})",
    """CREATE FUNCTION app_keep_supervisor_id() RETURNS trigger
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           IF OLD.role = 'SUPERVISOR' AND NEW.display_id IS DISTINCT FROM OLD.display_id THEN
               RAISE EXCEPTION 'A Supervisor ID is permanent and cannot be changed.'
                   USING ERRCODE = 'integrity_constraint_violation';
           END IF;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_users_supervisor_id_is_permanent BEFORE UPDATE OF display_id ON users
       FOR EACH ROW EXECUTE FUNCTION app_keep_supervisor_id()""",
    """CREATE TABLE supervisor_profile_changes (
           id                      uuid PRIMARY KEY,
           user_id                 uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status                  text NOT NULL CONSTRAINT ck_profile_changes_status
                                       CHECK (status IN ('PENDING', 'APPLIED', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')),
           full_name               text CONSTRAINT ck_profile_changes_full_name
                                   CHECK (length(full_name) BETWEEN 2 AND 120),
           username                text CONSTRAINT ck_profile_changes_username
                                   CHECK (username ~ '^[A-Za-z0-9_.-]{3,64}$'),
           email                   text CONSTRAINT ck_profile_changes_email CHECK (length(email) BETWEEN 3 AND 254),
           password_hash           text CONSTRAINT ck_profile_changes_argon2 CHECK (password_hash LIKE '$argon2id$%'),
           new_email_digest        text CONSTRAINT ck_profile_changes_digest
                                   CHECK (new_email_digest ~ '^[0-9a-f]{64}$'),
           created_at              timestamptz NOT NULL DEFAULT now(),
           expires_at              timestamptz NOT NULL,
           current_email_verified_at timestamptz,
           new_email_verified_at   timestamptz,
           applied_at              timestamptz,
           CONSTRAINT ck_profile_changes_expiry CHECK (expires_at > created_at),
           CONSTRAINT ck_profile_changes_new_email CHECK ((email IS NULL) = (new_email_digest IS NULL)),
           -- Requested values exist only while the change is pending.
           CONSTRAINT ck_profile_changes_values_only_while_pending CHECK (
               status = 'PENDING' OR num_nonnulls(full_name, username, email, password_hash, new_email_digest) = 0)
       )""",
    "CREATE INDEX ix_profile_changes_user ON supervisor_profile_changes (user_id, status)",
]

_END = """UPDATE supervisor_profile_changes spc
             SET status = {status}, full_name = NULL, username = NULL, email = NULL, password_hash = NULL,
                 new_email_digest = NULL
           WHERE {where}"""

_SUPERSEDE_PENDING = _END.format(
    status="'SUPERSEDED'", where="spc.user_id = app_current_user_id() AND spc.status = 'PENDING'"
)
_MARK_APPLIED = _END.format(status="'APPLIED'", where="spc.id = p_change_id")
_CANCEL_PENDING = _END.format(
    status="'CANCELLED'",
    where="spc.id = p_change_id AND spc.user_id = app_current_user_id() AND spc.status = 'PENDING'",
)

_FUNCTIONS = [
    # The caller's own password hash, for re-confirming it (Supervisors only).
    f"""CREATE FUNCTION app_profile_lookup()
       RETURNS TABLE (user_id uuid, password_hash text, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           IF NOT {_ACTOR_IS_ACTIVE_SUPERVISOR} THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT c.user_id, c.password_hash, c.locked_until
             FROM user_credentials c WHERE c.user_id = app_current_user_id() FOR UPDATE;
       END $$""",
    # Store a requested change for the caller. Any earlier pending change is superseded.
    f"""CREATE FUNCTION app_profile_change_submit(
           p_change_id uuid, p_full_name text, p_username text, p_email text, p_password_hash text,
           p_new_email_digest text, p_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'change lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT {_ACTOR_IS_ACTIVE_SUPERVISOR} THEN
               RETURN 'NOT_AUTHORIZED';
           END IF;
           IF p_username IS NOT NULL AND EXISTS (
               SELECT 1 FROM users u WHERE u.id <> app_current_user_id() AND u.username IS NOT NULL
                  AND lower(u.username) = lower(p_username)) THEN
               RETURN 'USERNAME_TAKEN';
           END IF;
           UPDATE user_credentials uc SET failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = app_current_user_id();
           {_SUPERSEDE_PENDING};
           DELETE FROM supervisor_profile_changes old
            WHERE old.created_at < now() - interval '1 day' AND old.status <> 'PENDING';
           INSERT INTO supervisor_profile_changes (id, user_id, status, full_name, username, email, password_hash,
                                                   new_email_digest, created_at, expires_at)
           VALUES (p_change_id, app_current_user_id(), 'PENDING', p_full_name, p_username, p_email, p_password_hash,
                   p_new_email_digest, now(), now() + make_interval(secs => p_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    # Issue the code for the registered email (p_new_email = false) or for the new email.
    f"""CREATE FUNCTION app_profile_change_issue_otp(
           p_change_id uuid, p_new_email boolean, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc supervisor_profile_changes%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO pc FROM supervisor_profile_changes c
            WHERE c.id = p_change_id AND c.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_SUPERVISOR} OR (p_new_email AND pc.email IS NULL) THEN
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
    # Check one of the two codes. A code already verified for this change counts as VERIFIED.
    f"""CREATE FUNCTION app_profile_change_verify_otp(p_change_id uuid, p_new_email boolean, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc supervisor_profile_changes%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO pc FROM supervisor_profile_changes c
            WHERE c.id = p_change_id AND c.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_SUPERVISOR} OR (p_new_email AND pc.email IS NULL) THEN
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
                   UPDATE supervisor_profile_changes c SET new_email_verified_at = now() WHERE c.id = pc.id;
               ELSE
                   UPDATE supervisor_profile_changes c SET current_email_verified_at = now() WHERE c.id = pc.id;
               END IF;
           END IF;
           RETURN QUERY SELECT r.outcome, r.attempts_remaining;
       END $$""",
    # Apply the change, all at once, only when every required code is verified. A new password
    # moves password_changed_at forward, which signs out every earlier session.
    f"""CREATE FUNCTION app_profile_change_apply(p_change_id uuid)
       RETURNS TABLE (outcome text, password_changed boolean)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           pc supervisor_profile_changes%ROWTYPE;
           v_constraint text;
       BEGIN
           SELECT * INTO pc FROM supervisor_profile_changes c
            WHERE c.id = p_change_id AND c.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_SUPERVISOR} THEN
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
               UPDATE users u
                  SET full_name = COALESCE(pc.full_name, u.full_name),
                      username = COALESCE(pc.username, u.username),
                      email = COALESCE(pc.email, u.email)
                WHERE u.id = pc.user_id;
               IF pc.password_hash IS NOT NULL THEN
                   UPDATE user_credentials uc SET password_hash = pc.password_hash, password_changed_at = now()
                    WHERE uc.user_id = pc.user_id;
                   DELETE FROM admin_login_sessions ls WHERE ls.user_id = pc.user_id AND ls.completed_at IS NULL;
               END IF;
           EXCEPTION WHEN unique_violation THEN
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               RETURN QUERY SELECT CASE v_constraint
                                       WHEN 'uq_users_email' THEN 'EMAIL_ALREADY_REGISTERED'
                                       WHEN 'uq_users_username_ci' THEN 'USERNAME_TAKEN'
                                       ELSE 'CONFLICT' END, NULL::boolean;
               RETURN;
           END;
           {_MARK_APPLIED};
           UPDATE supervisor_profile_changes c SET applied_at = now() WHERE c.id = p_change_id;
           RETURN QUERY SELECT 'APPLIED'::text, pc.password_hash IS NOT NULL;
       END $$""",
    f"""CREATE FUNCTION app_profile_change_cancel(p_change_id uuid) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           {_CANCEL_PENDING};
           RETURN CASE WHEN FOUND THEN 'CANCELLED' ELSE 'NOT_FOUND' END;
       END $$""",
    # Forgot password with Supervisor ID + registered email. Like app_password_reset_request,
    # a request is stored whether or not an account matches.
    """CREATE FUNCTION app_password_reset_request_by_supervisor_id(
           p_request_id uuid, p_supervisor_id text, p_email text, p_subject_digest text, p_ttl_seconds integer)
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
            WHERE u.display_id = p_supervisor_id AND lower(u.email) = lower(p_email) AND u.role = 'SUPERVISOR'
              AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE';
           DELETE FROM password_reset_requests pr WHERE pr.created_at < now() - interval '1 day';
           INSERT INTO password_reset_requests (id, user_id, subject_digest, created_at, expires_at)
           VALUES (p_request_id, v_user, p_subject_digest, now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_profile_lookup()",
    "app_profile_change_submit(uuid, text, text, text, text, text, integer)",
    "app_profile_change_issue_otp(uuid, boolean, uuid, text, integer, integer, integer, integer)",
    "app_profile_change_verify_otp(uuid, boolean, text)",
    "app_profile_change_apply(uuid)",
    "app_profile_change_cancel(uuid)",
    "app_password_reset_request_by_supervisor_id(uuid, text, text, text, integer)",
]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _FUNCTIONS:
        op.execute(statement)
    op.execute("ALTER TABLE supervisor_profile_changes ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON supervisor_profile_changes FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for signature in reversed(_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS supervisor_profile_changes")
    op.execute("DROP TRIGGER IF EXISTS trg_users_supervisor_id_is_permanent ON users")
    op.execute("DROP FUNCTION IF EXISTS app_keep_supervisor_id()")
    op.execute("DELETE FROM auth_otp_challenges WHERE purpose IN ('PROFILE_CHANGE', 'PROFILE_NEW_EMAIL')")
    op.execute("ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose")
    op.execute(
        f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES_0007})"
    )
