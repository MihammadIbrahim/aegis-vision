"""Organization Admin login: credentials, then an email OTP, then a token.

What this adds
--------------
* ``organizations.status`` (ACTIVE / INACTIVE / SUSPENDED, default ACTIVE). Only ACTIVE
  organizations can sign in. Nothing changes the status yet; Super Admin organization
  controls come later.
* Failed-login tracking on ``user_credentials`` (counter, lock time, last login).
* ``admin_login_sessions``: a sign-in that passed the password check and is waiting for its
  email code. It expires quickly and never grants access by itself.
* ``admin_login_otp_challenges``: the codes, stored only as keyed hashes.

How the API reaches these tables
--------------------------------
As in migration 0004: the app role gets **no** rights on the new tables or on
``user_credentials``. It may only call five narrow functions (look up, record a failure,
start, issue a code, verify a code). The look-up returns a password hash only when the
exact organization name, Organization ID and Admin email all match one account.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


# An Admin may sign in only if the account, its campus and its organization are all active.
_ELIGIBLE = "u.role = 'ADMIN' AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE'"

_SCHEMA = [
    """ALTER TABLE organizations ADD COLUMN status text NOT NULL DEFAULT 'ACTIVE'
           CONSTRAINT ck_organizations_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED'))""",
    """ALTER TABLE user_credentials
           ADD COLUMN failed_login_attempts integer NOT NULL DEFAULT 0
               CONSTRAINT ck_user_credentials_attempts CHECK (failed_login_attempts >= 0),
           ADD COLUMN last_failed_login_at timestamptz,
           ADD COLUMN locked_until timestamptz,
           ADD COLUMN last_login_at timestamptz""",
    """CREATE TABLE admin_login_sessions (
           id            uuid PRIMARY KEY,
           user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           created_at    timestamptz NOT NULL DEFAULT now(),
           expires_at    timestamptz NOT NULL,
           completed_at  timestamptz,
           CONSTRAINT ck_admin_login_sessions_expiry CHECK (expires_at > created_at)
       )""",
    "CREATE INDEX ix_admin_login_sessions_created ON admin_login_sessions (created_at)",
    """CREATE TABLE admin_login_otp_challenges (
           id                uuid PRIMARY KEY,
           login_session_id  uuid NOT NULL REFERENCES admin_login_sessions (id) ON DELETE CASCADE,
           user_id           uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           code_hash         text NOT NULL CONSTRAINT ck_admin_login_otp_hash CHECK (code_hash ~ '^[0-9a-f]{64}$'),
           issued_at         timestamptz NOT NULL DEFAULT now(),
           expires_at        timestamptz NOT NULL,
           max_attempts      integer NOT NULL,
           failed_attempts   integer NOT NULL DEFAULT 0,
           consumed_at       timestamptz,
           superseded_at     timestamptz,
           CONSTRAINT ck_admin_login_otp_expiry CHECK (expires_at > issued_at),
           CONSTRAINT ck_admin_login_otp_attempts CHECK (max_attempts BETWEEN 1 AND 20
                                                         AND failed_attempts BETWEEN 0 AND max_attempts),
           CONSTRAINT ck_admin_login_otp_one_ending CHECK (consumed_at IS NULL OR superseded_at IS NULL)
       )""",
    """CREATE UNIQUE INDEX uq_admin_login_otp_one_open_per_session ON admin_login_otp_challenges (login_session_id)
       WHERE consumed_at IS NULL AND superseded_at IS NULL""",
    "CREATE INDEX ix_admin_login_otp_user_recent ON admin_login_otp_challenges (user_id, issued_at)",
]

_FUNCTIONS = [
    # Step 1: find the one Admin account matching the exact organization name, Organization ID
    # and email. Returns nothing otherwise. Locks the credentials row so that failure counting
    # is exact under concurrent attempts. The password itself is checked by the API (Argon2id).
    f"""CREATE FUNCTION app_admin_login_lookup(p_organization_name text, p_organization_code text, p_email text)
       RETURNS TABLE (user_id uuid, password_hash text, eligible boolean, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           RETURN QUERY
           SELECT u.id, c.password_hash, ({_ELIGIBLE}), c.locked_until
             FROM organizations o
             JOIN users u ON u.organization_id = o.id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
             JOIN user_credentials c ON c.user_id = u.id
            WHERE o.name = p_organization_name
              AND o.organization_code = p_organization_code
              AND lower(u.email) = lower(p_email)
              AND u.role = 'ADMIN'
              FOR UPDATE OF c;
       END $$""",
    # A wrong password: count it. Reaching the limit within the window locks the account for
    # one window. Returns the lock time, or NULL if not locked.
    """CREATE FUNCTION app_admin_login_record_failure(p_user_id uuid, p_max_attempts integer, p_window_seconds integer)
       RETURNS timestamptz
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           c user_credentials%ROWTYPE;
           v_attempts integer;
           v_locked timestamptz;
       BEGIN
           IF p_max_attempts NOT BETWEEN 1 AND 20 OR p_window_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'login lockout settings out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO c FROM user_credentials uc WHERE uc.user_id = p_user_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN NULL;
           END IF;
           IF c.last_failed_login_at IS NOT NULL
              AND c.last_failed_login_at > now() - make_interval(secs => p_window_seconds) THEN
               v_attempts := c.failed_login_attempts + 1;
           ELSE
               v_attempts := 1;
           END IF;
           IF v_attempts >= p_max_attempts THEN
               v_locked := now() + make_interval(secs => p_window_seconds);
               v_attempts := 0;
           END IF;
           UPDATE user_credentials uc
              SET failed_login_attempts = v_attempts, last_failed_login_at = now(),
                  locked_until = COALESCE(v_locked, uc.locked_until)
            WHERE uc.user_id = p_user_id;
           RETURN v_locked;
       END $$""",
    # The password was right: clear the failure count and open a short-lived sign-in that still
    # needs the email code. Refuses accounts that may not sign in.
    f"""CREATE FUNCTION app_admin_login_start(p_user_id uuid, p_login_id uuid, p_ttl_seconds integer)
       RETURNS void
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'sign-in lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT EXISTS (
               SELECT 1 FROM users u
                 JOIN organizations o ON o.id = u.organization_id
                 JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
                 JOIN user_credentials c ON c.user_id = u.id
                WHERE u.id = p_user_id AND {_ELIGIBLE}) THEN
               RAISE EXCEPTION 'account may not sign in' USING ERRCODE = 'insufficient_privilege';
           END IF;
           UPDATE user_credentials uc
              SET failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = p_user_id;
           -- Old sign-ins (and their codes) are kept a day at most.
           DELETE FROM admin_login_sessions s WHERE s.created_at < now() - interval '1 day';
           INSERT INTO admin_login_sessions (id, user_id, created_at, expires_at)
           VALUES (p_login_id, p_user_id, now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
    # Step 2: issue a code (also "resend"): cooldown per sign-in, hourly limit per account.
    f"""CREATE FUNCTION app_admin_login_issue_otp(
           p_login_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, login_expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s admin_login_sessions%ROWTYPE;
           v_email text;
           v_last timestamptz;
           v_count integer;
           v_oldest timestamptz;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 30 AND 3600 OR p_max_attempts NOT BETWEEN 1 AND 20
              OR p_cooldown_seconds NOT BETWEEN 0 AND 3600 OR p_hourly_limit NOT BETWEEN 1 AND 100 THEN
               RAISE EXCEPTION 'OTP settings out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO s FROM admin_login_sessions ls WHERE ls.id = p_login_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'LOGIN_NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.completed_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.expires_at <= now() THEN
               RETURN QUERY SELECT 'LOGIN_EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT u.email INTO v_email
             FROM users u
             JOIN organizations o ON o.id = u.organization_id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
            WHERE u.id = s.user_id AND {_ELIGIBLE};
           IF v_email IS NULL THEN
               RETURN QUERY SELECT 'NOT_ELIGIBLE'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           PERFORM pg_advisory_xact_lock(hashtextextended('admin_login_otp:' || s.user_id::text, 0));
           SELECT max(ch.issued_at) INTO v_last FROM admin_login_otp_challenges ch WHERE ch.login_session_id = s.id;
           IF v_last IS NOT NULL AND v_last + make_interval(secs => p_cooldown_seconds) > now() THEN
               RETURN QUERY SELECT 'COOLDOWN'::text,
                   greatest(1, ceil(extract(epoch FROM
                       v_last + make_interval(secs => p_cooldown_seconds) - now())))::integer,
                   NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT count(*), min(ch.issued_at) INTO v_count, v_oldest
             FROM admin_login_otp_challenges ch
            WHERE ch.user_id = s.user_id AND ch.issued_at > now() - interval '1 hour';
           IF v_count >= p_hourly_limit THEN
               RETURN QUERY SELECT 'HOURLY_LIMIT'::text,
                   greatest(1, ceil(extract(epoch FROM v_oldest + interval '1 hour' - now())))::integer,
                   NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           UPDATE admin_login_otp_challenges ch SET superseded_at = now()
            WHERE ch.login_session_id = s.id AND ch.consumed_at IS NULL AND ch.superseded_at IS NULL;
           INSERT INTO admin_login_otp_challenges (
               id, login_session_id, user_id, code_hash, issued_at, expires_at, max_attempts)
           VALUES (p_challenge_id, s.id, s.user_id, p_code_hash, now(),
                   now() + make_interval(secs => p_ttl_seconds), p_max_attempts);
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, v_email, s.expires_at;
       END $$""",
    # Step 3: check a code. A wrong code counts as an attempt (the caller commits that). A right
    # code is used up, completes the sign-in, and returns the account to issue a token for.
    f"""CREATE FUNCTION app_admin_login_verify_otp(p_login_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer, user_id uuid)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s admin_login_sessions%ROWTYPE;
           c admin_login_otp_challenges%ROWTYPE;
       BEGIN
           SELECT * INTO s FROM admin_login_sessions ls WHERE ls.id = p_login_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'LOGIN_NOT_FOUND'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           IF s.completed_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           IF s.expires_at <= now() THEN
               RETURN QUERY SELECT 'LOGIN_EXPIRED'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           SELECT * INTO c FROM admin_login_otp_challenges ch
            WHERE ch.login_session_id = s.id AND ch.consumed_at IS NULL AND ch.superseded_at IS NULL
              FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NO_ACTIVE_CODE'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           IF c.failed_attempts >= c.max_attempts THEN
               RETURN QUERY SELECT 'ATTEMPTS_EXCEEDED'::text, 0, NULL::uuid;
               RETURN;
           END IF;
           IF c.expires_at <= now() THEN
               RETURN QUERY SELECT 'CODE_EXPIRED'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           IF c.code_hash <> p_code_hash THEN
               UPDATE admin_login_otp_challenges ch SET failed_attempts = ch.failed_attempts + 1 WHERE ch.id = c.id;
               IF c.failed_attempts + 1 >= c.max_attempts THEN
                   RETURN QUERY SELECT 'ATTEMPTS_EXCEEDED'::text, 0, NULL::uuid;
               ELSE
                   RETURN QUERY SELECT 'INVALID_CODE'::text, c.max_attempts - c.failed_attempts - 1, NULL::uuid;
               END IF;
               RETURN;
           END IF;
           -- The account may have been deactivated since the password check.
           IF NOT EXISTS (
               SELECT 1 FROM users u
                 JOIN organizations o ON o.id = u.organization_id
                 JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
                WHERE u.id = s.user_id AND {_ELIGIBLE}) THEN
               RETURN QUERY SELECT 'NOT_ELIGIBLE'::text, NULL::integer, NULL::uuid;
               RETURN;
           END IF;
           UPDATE admin_login_otp_challenges ch SET consumed_at = now() WHERE ch.id = c.id;
           UPDATE admin_login_sessions ls SET completed_at = now() WHERE ls.id = s.id;
           UPDATE user_credentials uc SET last_login_at = now() WHERE uc.user_id = s.user_id;
           RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer, s.user_id;
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_admin_login_lookup(text, text, text)",
    "app_admin_login_record_failure(uuid, integer, integer)",
    "app_admin_login_start(uuid, uuid, integer)",
    "app_admin_login_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_admin_login_verify_otp(uuid, text)",
]
_NEW_TABLES = ["admin_login_sessions", "admin_login_otp_challenges"]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _FUNCTIONS:
        op.execute(statement)
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for signature in reversed(_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS admin_login_otp_challenges, admin_login_sessions")
    op.execute(
        "ALTER TABLE user_credentials DROP COLUMN IF EXISTS last_login_at, DROP COLUMN IF EXISTS locked_until, "
        "DROP COLUMN IF EXISTS last_failed_login_at, DROP COLUMN IF EXISTS failed_login_attempts"
    )
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS status")
