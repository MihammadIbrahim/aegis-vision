"""Campus / branch requests for an existing organization: the applicant's side (requirements
Section 8, "Already Registered Organization?"; decisions G, I and L of
docs/requirements/Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md).

What this adds
--------------
* ``campus_request_submissions``: an application waiting for its email code. It holds the
  applicant's details only while pending; they are erased when it completes or expires.
  Nothing appears in the Main Admin's queue before the email is verified.
* ``campus_request_credentials``: the applicant's password hash for a verified request, in its
  own table so that no tenant query can read it (like ``user_credentials``). It is deleted as
  soon as the request is decided (trigger), and with the request.
* ``campus_registration_requests`` gains ``applicant_username``, ``applicant_phone`` and
  ``email_verified_at``. A verified request must be decided within 72 hours of verification
  (decision I); after that it can never be approved or used, and it is deleted by the next
  submission's clean-up.
* One-time-code purpose ``CAMPUS_REQUEST_EMAIL``.

Public functions (no signed-in user; the applicant must give the organization's exact name
and its Organization ID, as at sign-in):
  ``app_campus_request_submit``, ``app_campus_request_issue_otp``, ``app_campus_request_verify_otp``,
  ``app_campus_request_login_lookup`` (sign-in answers "approval pending", decision L).
Main Admin function (acts only for the signed-in caller):
  ``app_campus_request_approve``: creates the campus and a working Admin account (username,
  phone, password) for the applicant, all or nothing. Approval no longer creates an Admin
  without a password.

As before, the app role gets no rights on the new tables.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-12
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_PHONE = r"'^\+?[0-9]{7,15}$'"
_PURPOSES_0010 = (
    "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET', 'PROFILE_CHANGE', 'PROFILE_NEW_EMAIL', "
    "'GUARD_STEP_UP', 'GUARD_EMAIL', 'OWNERSHIP_TRANSFER', 'ACCOUNT_DELETION')"
)
_PURPOSES = _PURPOSES_0010[:-1] + ", 'CAMPUS_REQUEST_EMAIL')"

# A verified request not decided within this time is expired (decision I).
_DECISION_WINDOW = "interval '72 hours'"
# A pending request the Main Admin may still decide.
_LIVE_REQUEST = f"""(q.status = 'PENDING' AND q.email_verified
                     AND (q.email_verified_at IS NULL OR q.email_verified_at + {_DECISION_WINDOW} > now()))"""

_TABLES = [
    f"""ALTER TABLE campus_registration_requests
        ADD COLUMN applicant_username text
            CONSTRAINT ck_campus_requests_username CHECK (applicant_username ~ '^[A-Za-z0-9_.-]{{3,64}}$'),
        ADD COLUMN applicant_phone text CONSTRAINT ck_campus_requests_phone CHECK (applicant_phone ~ {_PHONE}),
        ADD COLUMN email_verified_at timestamptz""",
    f"""CREATE TABLE campus_request_submissions (
           id                    uuid PRIMARY KEY,
           organization_id       uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
           status                text NOT NULL CONSTRAINT ck_campus_submissions_status
                                     CHECK (status IN ('PENDING', 'COMPLETED', 'EXPIRED')),
           proposed_campus_name  text CONSTRAINT ck_campus_submissions_campus
                                     CHECK (length(proposed_campus_name) BETWEEN 2 AND 100),
           full_name             text CONSTRAINT ck_campus_submissions_full_name
                                     CHECK (length(full_name) BETWEEN 2 AND 120),
           username              text CONSTRAINT ck_campus_submissions_username
                                     CHECK (username ~ '^[A-Za-z0-9_.-]{{3,64}}$'),
           email                 text CONSTRAINT ck_campus_submissions_email CHECK (length(email) BETWEEN 3 AND 254),
           phone                 text CONSTRAINT ck_campus_submissions_phone CHECK (phone ~ {_PHONE}),
           password_hash         text CONSTRAINT ck_campus_submissions_argon2
                                     CHECK (password_hash LIKE '$argon2id$%'),
           recipient_digest      text CONSTRAINT ck_campus_submissions_digest
                                     CHECK (recipient_digest ~ '^[0-9a-f]{{64}}$'),
           created_at            timestamptz NOT NULL DEFAULT now(),
           expires_at            timestamptz NOT NULL,
           completed_at          timestamptz,
           request_id            uuid REFERENCES campus_registration_requests (id) ON DELETE SET NULL,
           CONSTRAINT ck_campus_submissions_details_only_while_pending CHECK (
               CASE WHEN status = 'PENDING'
                    THEN num_nonnulls(proposed_campus_name, full_name, username, email, phone, password_hash,
                                      recipient_digest) = 7
                    ELSE num_nonnulls(proposed_campus_name, full_name, username, email, phone, password_hash,
                                      recipient_digest) = 0
               END)
       )""",
    """CREATE INDEX ix_campus_submissions_pending_expiry ON campus_request_submissions (expires_at)
       WHERE status = 'PENDING'""",
    """CREATE TABLE campus_request_credentials (
           request_id     uuid PRIMARY KEY REFERENCES campus_registration_requests (id) ON DELETE CASCADE,
           password_hash  text NOT NULL CONSTRAINT ck_campus_request_credentials_argon2
                              CHECK (password_hash LIKE '$argon2id$%'),
           created_at     timestamptz NOT NULL DEFAULT now()
       )""",
    "ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose",
    f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES})",
]

_HELPERS = [
    # Erase the details of applications whose code was never verified in time, and delete
    # verified requests nobody decided within the decision window (decision I).
    f"""CREATE FUNCTION app_campus_requests_expire_stale() RETURNS void
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           UPDATE campus_request_submissions
              SET status = 'EXPIRED', proposed_campus_name = NULL, full_name = NULL, username = NULL,
                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL
            WHERE id IN (SELECT id FROM campus_request_submissions
                          WHERE status = 'PENDING' AND expires_at <= now()
                          ORDER BY expires_at LIMIT 200 FOR UPDATE SKIP LOCKED);
           DELETE FROM campus_registration_requests
            WHERE id IN (SELECT id FROM campus_registration_requests
                          WHERE status = 'PENDING' AND email_verified_at IS NOT NULL
                            AND email_verified_at + {_DECISION_WINDOW} <= now()
                          LIMIT 200 FOR UPDATE SKIP LOCKED);
       END $$""",
    """CREATE FUNCTION app_campus_request_submission_expire(p_submission_id uuid) RETURNS void
       LANGUAGE sql SET search_path = pg_catalog, public AS $$
           UPDATE campus_request_submissions
              SET status = 'EXPIRED', proposed_campus_name = NULL, full_name = NULL, username = NULL,
                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL
            WHERE id = p_submission_id AND status = 'PENDING'
       $$""",
    # A decided request no longer needs the applicant's password hash.
    """CREATE FUNCTION app_campus_request_drop_credentials() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           DELETE FROM campus_request_credentials WHERE request_id = NEW.id;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_campus_requests_drop_credentials
       AFTER UPDATE OF status ON campus_registration_requests
       FOR EACH ROW WHEN (NEW.status <> 'PENDING') EXECUTE FUNCTION app_campus_request_drop_credentials()""",
]
_HELPER_SIGNATURES = [
    "app_campus_requests_expire_stale()",
    "app_campus_request_submission_expire(uuid)",
    "app_campus_request_drop_credentials()",
]

_FUNCTIONS = [
    # Step 1: store an application. The organization must match its exact name AND its
    # Organization ID and be active; the same answer otherwise, so nothing is revealed.
    # Campus name and username are checked now and again after the code.
    f"""CREATE FUNCTION app_campus_request_submit(
           p_submission_id uuid, p_organization_name text, p_organization_code text, p_campus_name text,
           p_full_name text, p_username text, p_email text, p_phone text, p_password_hash text,
           p_recipient_digest text, p_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_org uuid;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'application lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           PERFORM app_campus_requests_expire_stale();
           SELECT o.id INTO v_org FROM organizations o
            WHERE o.name = p_organization_name AND o.organization_code = p_organization_code
              AND o.status = 'ACTIVE';
           IF v_org IS NULL THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF EXISTS (SELECT 1 FROM campuses ca WHERE ca.organization_id = v_org
                                                  AND lower(ca.name) = lower(p_campus_name))
              OR EXISTS (SELECT 1 FROM campus_registration_requests q
                          WHERE q.organization_id = v_org AND {_LIVE_REQUEST}
                            AND lower(q.proposed_campus_name) = lower(p_campus_name)) THEN
               RETURN 'CAMPUS_NAME_TAKEN';
           END IF;
           IF EXISTS (SELECT 1 FROM users u WHERE u.username IS NOT NULL AND lower(u.username) = lower(p_username))
              OR EXISTS (SELECT 1 FROM campus_registration_requests q
                          WHERE {_LIVE_REQUEST} AND lower(q.applicant_username) = lower(p_username)) THEN
               RETURN 'USERNAME_TAKEN';
           END IF;
           INSERT INTO campus_request_submissions (id, organization_id, status, proposed_campus_name, full_name,
                                                  username, email, phone, password_hash, recipient_digest,
                                                  created_at, expires_at)
           VALUES (p_submission_id, v_org, 'PENDING', p_campus_name, p_full_name, p_username, p_email, p_phone,
                   p_password_hash, p_recipient_digest, now(), now() + make_interval(secs => p_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    # Step 2: issue (or re-issue) the email code, with the cooldown and the hourly limit.
    """CREATE FUNCTION app_campus_request_issue_otp(
           p_submission_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s campus_request_submissions%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO s FROM campus_request_submissions cs WHERE cs.id = p_submission_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.status = 'COMPLETED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF s.status <> 'PENDING' OR s.expires_at <= now() THEN
               PERFORM app_campus_request_submission_expire(s.id);
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('CAMPUS_REQUEST_EMAIL', s.id,
               'CAMPUS_REQUEST_EMAIL:' || s.recipient_digest, p_challenge_id, p_code_hash, p_ttl_seconds,
               p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, s.email, s.expires_at;
       END $$""",
    # Step 3: check the code. A right code turns the application into a verified request in the
    # Main Admin's queue, all or nothing. A conflict found now returns its outcome; the API then
    # rolls back, so the code stays usable and nothing is created.
    f"""CREATE FUNCTION app_campus_request_verify_otp(p_submission_id uuid, p_code_hash text, p_request_id uuid)
       RETURNS TABLE (outcome text, attempts_remaining integer, organization_name text, campus_name text,
                      email text, verified_at timestamptz, decision_deadline timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           s campus_request_submissions%ROWTYPE;
           r record;
           v_outcome text;
           v_org_name text;
       BEGIN
           SELECT * INTO s FROM campus_request_submissions cs WHERE cs.id = p_submission_id FOR UPDATE;
           IF NOT FOUND THEN
               v_outcome := 'NOT_FOUND';
           ELSIF s.status = 'COMPLETED' THEN
               v_outcome := 'ALREADY_COMPLETED';
           ELSIF s.status <> 'PENDING' OR s.expires_at <= now() THEN
               PERFORM app_campus_request_submission_expire(s.id);
               v_outcome := 'EXPIRED';
           END IF;
           IF v_outcome IS NOT NULL THEN
               RETURN QUERY SELECT v_outcome, NULL::integer, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                                   NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('CAMPUS_REQUEST_EMAIL', s.id, p_code_hash);
           IF r.outcome <> 'VERIFIED' THEN
               RETURN QUERY SELECT r.outcome, r.attempts_remaining, NULL::text, NULL::text, NULL::text,
                                   NULL::timestamptz, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT o.name INTO v_org_name FROM organizations o WHERE o.id = s.organization_id AND o.status = 'ACTIVE';
           IF v_org_name IS NULL THEN
               v_outcome := 'NOT_FOUND';
           ELSIF EXISTS (SELECT 1 FROM users u WHERE lower(u.email) = lower(s.email)) THEN
               v_outcome := 'EMAIL_ALREADY_REGISTERED';
           ELSIF EXISTS (SELECT 1 FROM campus_registration_requests q
                          WHERE {_LIVE_REQUEST} AND lower(q.applicant_email) = lower(s.email)) THEN
               v_outcome := 'EMAIL_ALREADY_PENDING';
           ELSIF EXISTS (SELECT 1 FROM users u WHERE u.username IS NOT NULL AND lower(u.username) = lower(s.username))
                 OR EXISTS (SELECT 1 FROM campus_registration_requests q
                             WHERE {_LIVE_REQUEST} AND lower(q.applicant_username) = lower(s.username)) THEN
               v_outcome := 'USERNAME_TAKEN';
           ELSIF EXISTS (SELECT 1 FROM campuses ca WHERE ca.organization_id = s.organization_id
                                                     AND lower(ca.name) = lower(s.proposed_campus_name))
                 OR EXISTS (SELECT 1 FROM campus_registration_requests q
                             WHERE q.organization_id = s.organization_id AND {_LIVE_REQUEST}
                               AND lower(q.proposed_campus_name) = lower(s.proposed_campus_name)) THEN
               v_outcome := 'CAMPUS_NAME_TAKEN';
           END IF;
           IF v_outcome IS NOT NULL THEN
               RETURN QUERY SELECT v_outcome, NULL::integer, NULL::text, NULL::text, NULL::text, NULL::timestamptz,
                                   NULL::timestamptz;
               RETURN;
           END IF;
           INSERT INTO campus_registration_requests (id, organization_id, proposed_campus_name, applicant_name,
                                                     applicant_email, email_verified, status, submitted_at,
                                                     applicant_username, applicant_phone, email_verified_at)
           VALUES (p_request_id, s.organization_id, s.proposed_campus_name, s.full_name, s.email, true, 'PENDING',
                   s.created_at, s.username, s.phone, now());
           INSERT INTO campus_request_credentials (request_id, password_hash) VALUES (p_request_id, s.password_hash);
           UPDATE campus_request_submissions cs
              SET status = 'COMPLETED', completed_at = now(), request_id = p_request_id,
                  proposed_campus_name = NULL, full_name = NULL, username = NULL, email = NULL, phone = NULL,
                  password_hash = NULL, recipient_digest = NULL
            WHERE cs.id = s.id;
           RETURN QUERY SELECT 'COMPLETED'::text, NULL::integer, v_org_name, s.proposed_campus_name, s.email, now(),
                               now() + {_DECISION_WINDOW};
       END $$""",
    # Sign-in: the password hash of a verified, still-pending request for this exact organization
    # and email, so the API can answer "approval pending" after a correct password (decision L).
    f"""CREATE FUNCTION app_campus_request_login_lookup(
           p_organization_name text, p_organization_code text, p_email text)
       RETURNS TABLE (password_hash text)
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT c.password_hash
             FROM organizations o
             JOIN campus_registration_requests q ON q.organization_id = o.id
             JOIN campus_request_credentials c ON c.request_id = q.id
            WHERE o.name = p_organization_name AND o.organization_code = p_organization_code
              AND lower(q.applicant_email) = lower(p_email) AND {_LIVE_REQUEST}
       $$""",
    # Main Admin approval: the new campus, and the applicant's Admin account with the username,
    # phone and password they gave, all or nothing. Acts only for the signed-in Main Admin of
    # the request's organization.
    f"""CREATE FUNCTION app_campus_request_approve(p_request_id uuid, p_campus_id uuid, p_user_id uuid,
                                                   p_display_id text)
       RETURNS TABLE (outcome text, reviewed_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           q campus_registration_requests%ROWTYPE;
           v_hash text;
           v_outcome text;
           v_constraint text;
       BEGIN
           IF NOT coalesce(app_actor_is_first_admin(), false) THEN
               RETURN QUERY SELECT 'NOT_AUTHORIZED'::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO q FROM campus_registration_requests r
            WHERE r.id = p_request_id AND r.organization_id = app_actor_org_id() FOR UPDATE;
           IF NOT FOUND THEN
               v_outcome := 'NOT_FOUND';
           ELSIF q.status <> 'PENDING' THEN
               v_outcome := 'ALREADY_COMPLETED';
           ELSIF NOT q.email_verified THEN
               v_outcome := 'EMAIL_NOT_VERIFIED';
           ELSIF q.email_verified_at IS NOT NULL AND q.email_verified_at + {_DECISION_WINDOW} <= now() THEN
               v_outcome := 'EXPIRED';
           END IF;
           IF v_outcome IS NOT NULL THEN
               RETURN QUERY SELECT v_outcome, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT c.password_hash INTO v_hash FROM campus_request_credentials c WHERE c.request_id = q.id;
           BEGIN
               INSERT INTO campuses (id, organization_id, name, status)
               VALUES (p_campus_id, q.organization_id, q.proposed_campus_name, 'ACTIVE');
               INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email, display_id,
                                  is_first_admin, username, phone)
               VALUES (p_user_id, q.organization_id, p_campus_id, 'ADMIN', 'ACTIVE', q.applicant_name,
                       q.applicant_email, p_display_id, false, q.applicant_username, q.applicant_phone);
               IF v_hash IS NOT NULL THEN
                   INSERT INTO user_credentials (user_id, password_hash) VALUES (p_user_id, v_hash);
               END IF;
               UPDATE campus_registration_requests r
                  SET status = 'APPROVED', reviewed_by = app_current_user_id(), reviewed_at = now(),
                      approved_campus_id = p_campus_id
                WHERE r.id = q.id;
           EXCEPTION WHEN unique_violation THEN
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               RETURN QUERY SELECT
                   CASE v_constraint
                       WHEN 'uq_campuses_org_name' THEN 'CAMPUS_NAME_TAKEN'
                       WHEN 'uq_users_email' THEN 'EMAIL_ALREADY_REGISTERED'
                       WHEN 'uq_users_username_ci' THEN 'USERNAME_TAKEN'
                       WHEN 'uq_users_display_id' THEN 'DISPLAY_ID_TAKEN'
                       ELSE 'CONFLICT'
                   END, NULL::timestamptz;
               RETURN;
           END;
           RETURN QUERY SELECT 'APPROVED'::text, now();
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_campus_request_submit(uuid, text, text, text, text, text, text, text, text, text, integer)",
    "app_campus_request_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_campus_request_verify_otp(uuid, text, uuid)",
    "app_campus_request_login_lookup(text, text, text)",
    "app_campus_request_approve(uuid, uuid, uuid, text)",
]
_NEW_TABLES = ["campus_request_submissions", "campus_request_credentials"]


def upgrade() -> None:
    app = _app_role()
    for statement in _TABLES + _HELPERS + _FUNCTIONS:
        op.execute(statement)
    for table in _NEW_TABLES:
        # RLS on with no policies: even a later accidental grant would show no rows.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    for signature in _HELPER_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for signature in reversed(_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TRIGGER IF EXISTS trg_campus_requests_drop_credentials ON campus_registration_requests")
    for signature in reversed(_HELPER_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS campus_request_credentials, campus_request_submissions")
    op.execute("DELETE FROM auth_otp_challenges WHERE purpose = 'CAMPUS_REQUEST_EMAIL'")
    op.execute("ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose")
    op.execute(
        f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES_0010})"
    )
    op.execute(
        "ALTER TABLE campus_registration_requests DROP COLUMN IF EXISTS email_verified_at, "
        "DROP COLUMN IF EXISTS applicant_phone, DROP COLUMN IF EXISTS applicant_username"
    )
