"""Supervisor creation, Supervisor sign-in, and password reset for Admins and Supervisors.

What this adds
--------------
* Supervisor display IDs may be 6 capital letters + 21 digits (27 characters), the format
  new Supervisors get. Earlier formats stay valid; Admin and Guard rules are unchanged.
* ``auth_otp_challenges``: one table for the one-time codes of three purposes. Codes are
  stored only as keyed hashes.
  - ``ADMIN_STEP_UP``: the Admin re-confirms password + email code before the
    Supervisor form opens.
  - ``SUPERVISOR_EMAIL``: the new Supervisor's email is verified.
  - ``PASSWORD_RESET``: forgot password.
* ``admin_step_up_grants``: a short-lived permission, for one Admin, to submit one Supervisor form.
* ``supervisor_creations``: a Supervisor form waiting for the email code. Details are erased
  when it completes or expires.
* ``password_reset_requests``: a reset request. A request is stored even when no account
  matches (``user_id`` empty, no email sent), so the API's answers never reveal whether
  an account exists.
* Sign-in (migration 0006) now also accepts active Supervisors.
* ``app_actor_password_changed_at()``: lets the API refuse tokens issued before the
  account's last password change, so a password reset signs out every earlier session.

As before, the app role gets no rights on the new tables; it calls narrow functions. The
Admin-side functions act only for the signed-in caller (``app_current_user_id()``), so an
Admin can never use another Admin's grant or creation, nor create a Supervisor outside
their own organization and campus.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _migration_0006() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0006", Path(__file__).with_name("0006_organization_admin_login.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ADMIN_ONLY = "u.role = 'ADMIN'"
_ADMIN_OR_SUPERVISOR = "u.role IN ('ADMIN', 'SUPERVISOR')"


def _login_functions(*, supervisors: bool) -> list[str]:
    """Migration 0006's sign-in functions, with the role rule widened (or restored).
    Migrations never change once applied, so reading 0006's text is stable."""
    statements = []
    for function in _migration_0006()._FUNCTIONS:
        if _ADMIN_ONLY not in function:
            continue
        body = function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
        statements.append(body.replace(_ADMIN_ONLY, _ADMIN_OR_SUPERVISOR) if supervisors else body)
    assert len(statements) == 4, statements  # lookup, start, issue, verify
    return statements


_SUPERVISOR_IDS = "^(SUP-[0-9]{11}|[A-Z]{6}[0-9]{11}|[A-Z]{6}[0-9]{21})$"
_DISPLAY_ID_RULE = f"""CHECK (
    CASE role
        WHEN 'ADMIN' THEN display_id ~ '^(ADM-[0-9]{{11}}|[A-Z]{{6}}[0-9]{{11}})$'
        WHEN 'SUPERVISOR' THEN display_id ~ '{_SUPERVISOR_IDS}'
        WHEN 'GUARD' THEN display_id ~ '^GRD-[0-9]{{11}}$'
        ELSE false
    END)"""
_DISPLAY_ID_RULE_0004 = """CHECK (
    CASE role
        WHEN 'ADMIN' THEN display_id ~ '^(ADM-[0-9]{11}|[A-Z]{6}[0-9]{11})$'
        WHEN 'SUPERVISOR' THEN display_id ~ '^(SUP-[0-9]{11}|[A-Z]{6}[0-9]{11})$'
        WHEN 'GUARD' THEN display_id ~ '^GRD-[0-9]{11}$'
        ELSE false
    END)"""

# The caller (bound by the API for Row-Level Security) is an active Admin of an active
# organization and campus.
_ACTOR_IS_ACTIVE_ADMIN = """EXISTS (
    SELECT 1 FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role = 'ADMIN'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""
_STAFF_ACTIVE = "u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE'"

_SCHEMA = [
    "ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format",
    f"ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format {_DISPLAY_ID_RULE}",
    """CREATE TABLE auth_otp_challenges (
           id               uuid PRIMARY KEY,
           purpose          text NOT NULL CONSTRAINT ck_auth_otp_purpose
                                CHECK (purpose IN ('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET')),
           subject_id       uuid NOT NULL,
           limit_key        text NOT NULL CONSTRAINT ck_auth_otp_limit_key CHECK (length(limit_key) <= 200),
           code_hash        text NOT NULL CONSTRAINT ck_auth_otp_hash CHECK (code_hash ~ '^[0-9a-f]{64}$'),
           issued_at        timestamptz NOT NULL DEFAULT now(),
           expires_at       timestamptz NOT NULL,
           max_attempts     integer NOT NULL,
           failed_attempts  integer NOT NULL DEFAULT 0,
           consumed_at      timestamptz,
           superseded_at    timestamptz,
           CONSTRAINT ck_auth_otp_expiry CHECK (expires_at > issued_at),
           CONSTRAINT ck_auth_otp_attempts CHECK (max_attempts BETWEEN 1 AND 20
                                                  AND failed_attempts BETWEEN 0 AND max_attempts),
           CONSTRAINT ck_auth_otp_one_ending CHECK (consumed_at IS NULL OR superseded_at IS NULL)
       )""",
    """CREATE UNIQUE INDEX uq_auth_otp_one_open_per_subject ON auth_otp_challenges (purpose, subject_id)
       WHERE consumed_at IS NULL AND superseded_at IS NULL""",
    "CREATE INDEX ix_auth_otp_limit_recent ON auth_otp_challenges (limit_key, issued_at)",
    """CREATE TABLE admin_step_up_grants (
           id           uuid PRIMARY KEY,
           user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           created_at   timestamptz NOT NULL DEFAULT now(),
           expires_at   timestamptz NOT NULL,
           verified_at  timestamptz,
           used_at      timestamptz,
           CONSTRAINT ck_admin_step_up_expiry CHECK (expires_at > created_at)
       )""",
    """CREATE TABLE supervisor_creations (
           id                  uuid PRIMARY KEY,
           organization_id     uuid NOT NULL,
           campus_id           uuid NOT NULL,
           created_by          uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status              text NOT NULL CONSTRAINT ck_supervisor_creations_status
                                   CHECK (status IN ('PENDING', 'COMPLETED', 'EXPIRED')),
           full_name           text CONSTRAINT ck_supervisor_creations_full_name
                                   CHECK (length(full_name) BETWEEN 2 AND 120),
           username            text CONSTRAINT ck_supervisor_creations_username
                                   CHECK (username ~ '^[A-Za-z0-9_.-]{3,64}$'),
           email               text CONSTRAINT ck_supervisor_creations_email CHECK (length(email) BETWEEN 3 AND 254),
           phone               text CONSTRAINT ck_supervisor_creations_phone CHECK (phone ~ '^\\+?[0-9]{7,15}$'),
           password_hash       text CONSTRAINT ck_supervisor_creations_argon2 CHECK (password_hash LIKE '$argon2id$%'),
           recipient_digest    text CONSTRAINT ck_supervisor_creations_digest
                                   CHECK (recipient_digest ~ '^[0-9a-f]{64}$'),
           created_at          timestamptz NOT NULL DEFAULT now(),
           expires_at          timestamptz NOT NULL,
           email_verified_at   timestamptz,
           completed_at        timestamptz,
           supervisor_user_id  uuid REFERENCES users (id) ON DELETE SET NULL,
           CONSTRAINT fk_supervisor_creations_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           CONSTRAINT ck_supervisor_creations_details_only_while_pending CHECK (
               CASE WHEN status = 'PENDING'
                    THEN num_nonnulls(full_name, username, email, phone, password_hash, recipient_digest) = 6
                    ELSE num_nonnulls(full_name, username, email, phone, password_hash, recipient_digest) = 0
               END)
       )""",
    "CREATE INDEX ix_supervisor_creations_pending ON supervisor_creations (expires_at) WHERE status = 'PENDING'",
    """CREATE TABLE password_reset_requests (
           id                      uuid PRIMARY KEY,
           user_id                 uuid REFERENCES users (id) ON DELETE CASCADE,
           subject_digest          text NOT NULL CONSTRAINT ck_password_reset_digest
                                       CHECK (subject_digest ~ '^[0-9a-f]{64}$'),
           created_at              timestamptz NOT NULL DEFAULT now(),
           expires_at              timestamptz NOT NULL,
           verified_at             timestamptz,
           reset_token_hash        text CONSTRAINT ck_password_reset_token CHECK (reset_token_hash ~ '^[0-9a-f]{64}$'),
           reset_token_expires_at  timestamptz,
           completed_at            timestamptz,
           CONSTRAINT ck_password_reset_expiry CHECK (expires_at > created_at)
       )""",
    "CREATE INDEX ix_password_reset_requests_created ON password_reset_requests (created_at)",
]

_HELPERS = [
    # Issue a code for (purpose, subject): cooldown per subject, hourly limit per limit_key
    # (serialized with an advisory lock), the previous code retired.
    """CREATE FUNCTION app_otp_issue(
           p_purpose text, p_subject_id uuid, p_limit_key text, p_challenge_id uuid, p_code_hash text,
           p_ttl_seconds integer, p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer)
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           v_last timestamptz;
           v_count integer;
           v_oldest timestamptz;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 30 AND 3600 OR p_max_attempts NOT BETWEEN 1 AND 20
              OR p_cooldown_seconds NOT BETWEEN 0 AND 3600 OR p_hourly_limit NOT BETWEEN 1 AND 100 THEN
               RAISE EXCEPTION 'OTP settings out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           PERFORM pg_advisory_xact_lock(hashtextextended('auth_otp:' || p_limit_key, 0));
           SELECT max(ch.issued_at) INTO v_last FROM auth_otp_challenges ch
            WHERE ch.purpose = p_purpose AND ch.subject_id = p_subject_id;
           IF v_last IS NOT NULL AND v_last + make_interval(secs => p_cooldown_seconds) > now() THEN
               RETURN QUERY SELECT 'COOLDOWN'::text, greatest(1, ceil(extract(epoch FROM
                   v_last + make_interval(secs => p_cooldown_seconds) - now())))::integer;
               RETURN;
           END IF;
           SELECT count(*), min(ch.issued_at) INTO v_count, v_oldest FROM auth_otp_challenges ch
            WHERE ch.limit_key = p_limit_key AND ch.issued_at > now() - interval '1 hour';
           IF v_count >= p_hourly_limit THEN
               RETURN QUERY SELECT 'HOURLY_LIMIT'::text,
                   greatest(1, ceil(extract(epoch FROM v_oldest + interval '1 hour' - now())))::integer;
               RETURN;
           END IF;
           UPDATE auth_otp_challenges ch SET superseded_at = now()
            WHERE ch.purpose = p_purpose AND ch.subject_id = p_subject_id
              AND ch.consumed_at IS NULL AND ch.superseded_at IS NULL;
           INSERT INTO auth_otp_challenges (id, purpose, subject_id, limit_key, code_hash, issued_at, expires_at,
                                            max_attempts)
           VALUES (p_challenge_id, p_purpose, p_subject_id, p_limit_key, p_code_hash, now(),
                   now() + make_interval(secs => p_ttl_seconds), p_max_attempts);
           DELETE FROM auth_otp_challenges ch WHERE ch.issued_at < now() - interval '1 day';
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer;
       END $$""",
    # Check a code for (purpose, subject): wrong codes are counted; a right code is used up.
    """CREATE FUNCTION app_otp_verify(p_purpose text, p_subject_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           c auth_otp_challenges%ROWTYPE;
       BEGIN
           SELECT * INTO c FROM auth_otp_challenges ch
            WHERE ch.purpose = p_purpose AND ch.subject_id = p_subject_id
              AND ch.consumed_at IS NULL AND ch.superseded_at IS NULL
              FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NO_ACTIVE_CODE'::text, NULL::integer;
               RETURN;
           END IF;
           IF c.failed_attempts >= c.max_attempts THEN
               RETURN QUERY SELECT 'ATTEMPTS_EXCEEDED'::text, 0;
               RETURN;
           END IF;
           IF c.expires_at <= now() THEN
               RETURN QUERY SELECT 'CODE_EXPIRED'::text, NULL::integer;
               RETURN;
           END IF;
           IF c.code_hash <> p_code_hash THEN
               UPDATE auth_otp_challenges ch SET failed_attempts = ch.failed_attempts + 1 WHERE ch.id = c.id;
               IF c.failed_attempts + 1 >= c.max_attempts THEN
                   RETURN QUERY SELECT 'ATTEMPTS_EXCEEDED'::text, 0;
               ELSE
                   RETURN QUERY SELECT 'INVALID_CODE'::text, c.max_attempts - c.failed_attempts - 1;
               END IF;
               RETURN;
           END IF;
           UPDATE auth_otp_challenges ch SET consumed_at = now() WHERE ch.id = c.id;
           RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer;
       END $$""",
    """CREATE FUNCTION app_supervisor_creation_expire(p_creation_id uuid) RETURNS void
       LANGUAGE sql SET search_path = pg_catalog, public AS $$
           UPDATE supervisor_creations
              SET status = 'EXPIRED', full_name = NULL, username = NULL, email = NULL, phone = NULL,
                  password_hash = NULL, recipient_digest = NULL
            WHERE id = p_creation_id AND status = 'PENDING'
       $$""",
]
_HELPER_SIGNATURES = [
    "app_otp_issue(text, uuid, text, uuid, text, integer, integer, integer, integer)",
    "app_otp_verify(text, uuid, text)",
    "app_supervisor_creation_expire(uuid)",
]

_FUNCTIONS = [
    # --- Session cut-off after a password change ------------------------------------------
    """CREATE FUNCTION app_actor_password_changed_at() RETURNS timestamptz
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT password_changed_at FROM user_credentials WHERE user_id = app_current_user_id()
       $$""",
    # --- Admin step-up: password, then email code, before the Supervisor form ---------------
    f"""CREATE FUNCTION app_step_up_lookup()
       RETURNS TABLE (user_id uuid, password_hash text, locked_until timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       BEGIN
           IF NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT c.user_id, c.password_hash, c.locked_until
             FROM user_credentials c WHERE c.user_id = app_current_user_id() FOR UPDATE;
       END $$""",
    f"""CREATE FUNCTION app_step_up_start(p_grant_id uuid, p_ttl_seconds integer) RETURNS void
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'step-up lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RAISE EXCEPTION 'only an active Admin may do this' USING ERRCODE = 'insufficient_privilege';
           END IF;
           UPDATE user_credentials uc SET failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = app_current_user_id();
           DELETE FROM admin_step_up_grants g WHERE g.created_at < now() - interval '1 day';
           INSERT INTO admin_step_up_grants (id, user_id, created_at, expires_at)
           VALUES (p_grant_id, app_current_user_id(), now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
    f"""CREATE FUNCTION app_step_up_issue_otp(
           p_grant_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           g admin_step_up_grants%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO g FROM admin_step_up_grants sg
            WHERE sg.id = p_grant_id AND sg.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF g.verified_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF g.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('ADMIN_STEP_UP', g.id, 'ADMIN_STEP_UP:' || g.user_id::text,
               p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, u.email, g.expires_at
             FROM users u WHERE u.id = g.user_id;
       END $$""",
    f"""CREATE FUNCTION app_step_up_verify_otp(p_grant_id uuid, p_code_hash text, p_form_ttl_seconds integer)
       RETURNS TABLE (outcome text, attempts_remaining integer, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           g admin_step_up_grants%ROWTYPE;
           r record;
       BEGIN
           IF p_form_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'form lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO g FROM admin_step_up_grants sg
            WHERE sg.id = p_grant_id AND sg.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           IF g.verified_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           IF g.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('ADMIN_STEP_UP', g.id, p_code_hash);
           IF r.outcome = 'VERIFIED' THEN
               UPDATE admin_step_up_grants sg
                  SET verified_at = now(), expires_at = now() + make_interval(secs => p_form_ttl_seconds)
                WHERE sg.id = g.id;
               RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer, now() + make_interval(secs => p_form_ttl_seconds);
               RETURN;
           END IF;
           RETURN QUERY SELECT r.outcome, r.attempts_remaining, NULL::timestamptz;
       END $$""",
    # --- Supervisor creation -----------------------------------------------------------------
    # Uses up the caller's verified grant and stores the form for the caller's own
    # organization and campus. The username is checked now; the email after its code.
    f"""CREATE FUNCTION app_supervisor_creation_submit(
           p_creation_id uuid, p_grant_id uuid, p_full_name text, p_username text, p_email text, p_phone text,
           p_password_hash text, p_recipient_digest text, p_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           g admin_step_up_grants%ROWTYPE;
           v_org uuid;
           v_campus uuid;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'creation lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN 'NOT_AUTHORIZED';
           END IF;
           SELECT * INTO g FROM admin_step_up_grants sg
            WHERE sg.id = p_grant_id AND sg.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR g.verified_at IS NULL OR g.used_at IS NOT NULL OR g.expires_at <= now() THEN
               RETURN 'AUTHORIZATION_REQUIRED';
           END IF;
           IF EXISTS (SELECT 1 FROM users u WHERE u.username IS NOT NULL AND lower(u.username) = lower(p_username)) THEN
               RETURN 'USERNAME_TAKEN';
           END IF;
           UPDATE admin_step_up_grants sg SET used_at = now() WHERE sg.id = g.id;
           SELECT u.organization_id, u.campus_id INTO v_org, v_campus FROM users u WHERE u.id = app_current_user_id();
           UPDATE supervisor_creations s
              SET status = 'EXPIRED', full_name = NULL, username = NULL, email = NULL, phone = NULL,
                  password_hash = NULL, recipient_digest = NULL
            WHERE s.id IN (SELECT id FROM supervisor_creations WHERE status = 'PENDING' AND expires_at <= now()
                            LIMIT 200 FOR UPDATE SKIP LOCKED);
           INSERT INTO supervisor_creations (id, organization_id, campus_id, created_by, status, full_name, username,
                                             email, phone, password_hash, recipient_digest, created_at, expires_at)
           VALUES (p_creation_id, v_org, v_campus, app_current_user_id(), 'PENDING', p_full_name, p_username,
                   p_email, p_phone, p_password_hash, p_recipient_digest, now(),
                   now() + make_interval(secs => p_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    f"""CREATE FUNCTION app_supervisor_creation_issue_otp(
           p_creation_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s supervisor_creations%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO s FROM supervisor_creations sc
            WHERE sc.id = p_creation_id AND sc.created_by = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.status = 'COMPLETED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.status <> 'PENDING' OR s.expires_at <= now() THEN
               PERFORM app_supervisor_creation_expire(s.id);
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('SUPERVISOR_EMAIL', s.id, 'SUPERVISOR_EMAIL:' || s.recipient_digest,
               p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, s.email, s.expires_at;
       END $$""",
    f"""CREATE FUNCTION app_supervisor_creation_verify_otp(p_creation_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s supervisor_creations%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO s FROM supervisor_creations sc
            WHERE sc.id = p_creation_id AND sc.created_by = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer;
               RETURN;
           END IF;
           IF s.status = 'COMPLETED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer;
               RETURN;
           END IF;
           IF s.status <> 'PENDING' OR s.expires_at <= now() THEN
               PERFORM app_supervisor_creation_expire(s.id);
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('SUPERVISOR_EMAIL', s.id, p_code_hash);
           IF r.outcome = 'VERIFIED' THEN
               UPDATE supervisor_creations sc SET email_verified_at = now() WHERE sc.id = s.id;
           END IF;
           RETURN QUERY SELECT r.outcome, r.attempts_remaining;
       END $$""",
    # Creates the Supervisor in the creator's organization and campus, all or nothing.
    f"""CREATE FUNCTION app_supervisor_creation_complete(p_creation_id uuid, p_user_id uuid, p_display_id text)
       RETURNS TABLE (outcome text, full_name text, username text, email text, phone text, campus_name text,
                      organization_code text, completed_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s supervisor_creations%ROWTYPE;
           v_outcome text;
           v_constraint text;
       BEGIN
           SELECT * INTO s FROM supervisor_creations sc
            WHERE sc.id = p_creation_id AND sc.created_by = app_current_user_id() FOR UPDATE;
           IF NOT FOUND OR NOT {_ACTOR_IS_ACTIVE_ADMIN} THEN
               v_outcome := 'NOT_FOUND';
           ELSIF s.status = 'COMPLETED' THEN
               v_outcome := 'ALREADY_COMPLETED';
           ELSIF s.status <> 'PENDING' OR s.expires_at <= now() THEN
               v_outcome := 'EXPIRED';
           ELSIF s.email_verified_at IS NULL THEN
               v_outcome := 'EMAIL_NOT_VERIFIED';
           ELSIF EXISTS (SELECT 1 FROM users u WHERE lower(u.email) = lower(s.email)) THEN
               v_outcome := 'EMAIL_ALREADY_REGISTERED';
           ELSIF EXISTS (SELECT 1 FROM users u
                          WHERE u.username IS NOT NULL AND lower(u.username) = lower(s.username)) THEN
               v_outcome := 'USERNAME_TAKEN';
           END IF;
           IF v_outcome IS NOT NULL THEN
               RETURN QUERY SELECT v_outcome, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                                   NULL::timestamptz;
               RETURN;
           END IF;
           BEGIN
               INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id,
                                  is_first_admin, username, phone)
               VALUES (p_user_id, s.organization_id, s.campus_id, 'SUPERVISOR', 'ACTIVE', s.full_name, s.email,
                       p_display_id, false, s.username, s.phone);
               INSERT INTO user_credentials (user_id, password_hash) VALUES (p_user_id, s.password_hash);
               UPDATE supervisor_creations sc
                  SET status = 'COMPLETED', completed_at = now(), supervisor_user_id = p_user_id,
                      full_name = NULL, username = NULL, email = NULL, phone = NULL, password_hash = NULL,
                      recipient_digest = NULL
                WHERE sc.id = s.id;
           EXCEPTION WHEN unique_violation THEN
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               RETURN QUERY SELECT
                   CASE v_constraint
                       WHEN 'uq_users_email' THEN 'EMAIL_ALREADY_REGISTERED'
                       WHEN 'uq_users_username_ci' THEN 'USERNAME_TAKEN'
                       WHEN 'uq_users_display_id' THEN 'DISPLAY_ID_TAKEN'
                       ELSE 'CONFLICT'
                   END, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::timestamptz;
               RETURN;
           END;
           RETURN QUERY SELECT 'COMPLETED'::text, s.full_name, s.username, s.email, s.phone, ca.name,
                               o.organization_code, now()
             FROM campuses ca JOIN organizations o ON o.id = ca.organization_id
            WHERE ca.id = s.campus_id AND ca.organization_id = s.organization_id;
       END $$""",
    # --- Password reset (Admins and Supervisors) ---------------------------------------------
    # Always stores a request, matched or not, so the API answers the same either way.
    f"""CREATE FUNCTION app_password_reset_request(
           p_request_id uuid, p_organization_code text, p_email text, p_subject_digest text, p_ttl_seconds integer)
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
            WHERE o.organization_code = p_organization_code AND lower(u.email) = lower(p_email)
              AND u.role IN ('ADMIN', 'SUPERVISOR') AND {_STAFF_ACTIVE};
           DELETE FROM password_reset_requests pr WHERE pr.created_at < now() - interval '1 day';
           INSERT INTO password_reset_requests (id, user_id, subject_digest, created_at, expires_at)
           VALUES (p_request_id, v_user, p_subject_digest, now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
    # Issues a code either way; returns an email address only for a real, active account.
    f"""CREATE FUNCTION app_password_reset_issue_otp(
           p_request_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           q password_reset_requests%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO q FROM password_reset_requests pr WHERE pr.id = p_request_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF q.verified_at IS NOT NULL OR q.completed_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF q.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('PASSWORD_RESET', q.id, 'PASSWORD_RESET:' || q.subject_digest,
               p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer,
               (SELECT u.email FROM users u
                  JOIN organizations o ON o.id = u.organization_id
                  JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
                 WHERE u.id = q.user_id AND {_STAFF_ACTIVE}),
               q.expires_at;
       END $$""",
    # A right code (only possible for a real account) stores a one-time reset token hash.
    """CREATE FUNCTION app_password_reset_verify_otp(
           p_request_id uuid, p_code_hash text, p_token_hash text, p_token_ttl_seconds integer)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           q password_reset_requests%ROWTYPE;
           r record;
       BEGIN
           IF p_token_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'reset token lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO q FROM password_reset_requests pr WHERE pr.id = p_request_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer;
               RETURN;
           END IF;
           IF q.verified_at IS NOT NULL OR q.completed_at IS NOT NULL THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer;
               RETURN;
           END IF;
           IF q.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('PASSWORD_RESET', q.id, p_code_hash);
           IF r.outcome = 'VERIFIED' THEN
               IF q.user_id IS NULL THEN  -- unreachable: no code was ever sent for such a request
                   RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer;
                   RETURN;
               END IF;
               UPDATE password_reset_requests pr
                  SET verified_at = now(), reset_token_hash = p_token_hash,
                      reset_token_expires_at = now() + make_interval(secs => p_token_ttl_seconds)
                WHERE pr.id = q.id;
           END IF;
           RETURN QUERY SELECT r.outcome, r.attempts_remaining;
       END $$""",
    # The account behind a valid reset token (for the password rules that need the username).
    f"""CREATE FUNCTION app_password_reset_identity(p_request_id uuid, p_token_hash text)
       RETURNS TABLE (outcome text, username text, email text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           q password_reset_requests%ROWTYPE;
       BEGIN
           SELECT * INTO q FROM password_reset_requests pr WHERE pr.id = p_request_id FOR UPDATE;
           IF NOT FOUND OR q.user_id IS NULL OR q.verified_at IS NULL OR q.completed_at IS NOT NULL
              OR q.reset_token_hash IS DISTINCT FROM p_token_hash OR q.reset_token_expires_at <= now() THEN
               RETURN QUERY SELECT 'INVALID_TOKEN'::text, NULL::text, NULL::text;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'OK'::text, u.username, u.email
             FROM users u
             JOIN organizations o ON o.id = u.organization_id
             JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
            WHERE u.id = q.user_id AND {_STAFF_ACTIVE};
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'INVALID_TOKEN'::text, NULL::text, NULL::text;
           END IF;
       END $$""",
    # Sets the new password once, clears any lock, ends pending sign-ins. password_changed_at
    # moves forward, so every token issued before now is refused.
    """CREATE FUNCTION app_password_reset_complete(p_request_id uuid, p_token_hash text, p_password_hash text)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           q password_reset_requests%ROWTYPE;
       BEGIN
           SELECT * INTO q FROM password_reset_requests pr WHERE pr.id = p_request_id FOR UPDATE;
           IF NOT FOUND OR q.user_id IS NULL OR q.verified_at IS NULL OR q.completed_at IS NOT NULL
              OR q.reset_token_hash IS DISTINCT FROM p_token_hash OR q.reset_token_expires_at <= now() THEN
               RETURN 'INVALID_TOKEN';
           END IF;
           UPDATE user_credentials uc
              SET password_hash = p_password_hash, password_changed_at = now(),
                  failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = q.user_id;
           DELETE FROM admin_login_sessions ls WHERE ls.user_id = q.user_id AND ls.completed_at IS NULL;
           UPDATE password_reset_requests pr SET completed_at = now(), reset_token_hash = NULL WHERE pr.id = q.id;
           RETURN 'COMPLETED';
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_actor_password_changed_at()",
    "app_step_up_lookup()",
    "app_step_up_start(uuid, integer)",
    "app_step_up_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_step_up_verify_otp(uuid, text, integer)",
    "app_supervisor_creation_submit(uuid, uuid, text, text, text, text, text, text, integer)",
    "app_supervisor_creation_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_supervisor_creation_verify_otp(uuid, text)",
    "app_supervisor_creation_complete(uuid, uuid, text)",
    "app_password_reset_request(uuid, text, text, text, integer)",
    "app_password_reset_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_password_reset_verify_otp(uuid, text, text, integer)",
    "app_password_reset_identity(uuid, text)",
    "app_password_reset_complete(uuid, text, text)",
]
_NEW_TABLES = ["auth_otp_challenges", "admin_step_up_grants", "supervisor_creations", "password_reset_requests"]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _HELPERS + _FUNCTIONS + _login_functions(supervisors=True):
        op.execute(statement)
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for signature in _HELPER_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for statement in _login_functions(supervisors=False):
        op.execute(statement)
    for signature in reversed(_FUNCTION_SIGNATURES + _HELPER_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(
        "DROP TABLE IF EXISTS password_reset_requests, supervisor_creations, admin_step_up_grants, auth_otp_challenges"
    )
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format")
    # The 0004 rule, for new rows. Supervisors with 27-character IDs (if any) stay in place.
    op.execute(f"ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format {_DISPLAY_ID_RULE_0004} NOT VALID")
