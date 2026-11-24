"""First Organization Admin registration with email OTP verification.

What this adds
--------------
* ``organizations.organization_code``: the Organization ID shown to people
  (``ORG-`` + 11 digits). It is separate from the primary key, unique, and
  permanent: a trigger refuses any change. Organizations created before this
  migration get a random code.
* ``organizations.official_email`` / ``official_phone``: the organization's own
  contact details, stored apart from any Admin's login email and phone.
* Organization names become unique ignoring capitalization; the name itself is
  stored exactly as entered.
* ``users.username`` and ``users.phone``. The Admin/Supervisor display ID may now
  also be 6 capital letters + 11 digits. Old ``ADM-``/``SUP-``/``GRD-`` IDs stay valid.
* ``user_credentials``: password hashes for organization users, in their own table so
  that no tenant query can read them. The app role has no access to it at all.
* ``organization_registrations``: a registration waiting for its email OTP. Nothing
  else (organization, campus, Admin) exists until the code is verified. Applicant
  details are erased from the row when it completes or expires; the reserved
  Organization ID stays, so it is never handed out again.
* ``email_otp_challenges``: one-time codes, stored only as keyed hashes.

How the API reaches these tables
--------------------------------
Registration happens before anyone is signed in, so the RLS policies (which need a
signed-in user) cannot apply. The app role gets **no** rights on the new tables.
Instead it may call four narrow functions, each doing one step and nothing else:
submit, issue a code, verify a code, complete. RLS is unchanged for everything else.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_PHONE = r"'^\+?[0-9]{7,15}$'"

_ORGANIZATIONS = [
    # Default for rows inserted without a code (organizations from before this migration, and
    # test data). 60 random bits from gen_random_uuid(), a cryptographically strong source,
    # reduced to 11 digits. The API itself generates codes in Python with `secrets`.
    """CREATE FUNCTION app_new_organization_code() RETURNS text
       LANGUAGE sql VOLATILE SET search_path = pg_catalog, public AS $$
           SELECT 'ORG-' || lpad(
               ((('x' || substr(translate(gen_random_uuid()::text, '-', ''), 1, 12)
                      || substr(translate(gen_random_uuid()::text, '-', ''), 1, 3))::bit(60)::bigint)
                 % 100000000000)::text,
               11, '0')
       $$""",
    "REVOKE ALL ON FUNCTION app_new_organization_code() FROM PUBLIC",
    f"""ALTER TABLE organizations
        ADD COLUMN organization_code text NOT NULL DEFAULT app_new_organization_code()
            CONSTRAINT ck_organizations_code_format CHECK (organization_code ~ '^ORG-[0-9]{{11}}$'),
        ADD COLUMN official_email text
            CONSTRAINT ck_organizations_official_email CHECK (length(official_email) BETWEEN 3 AND 254),
        ADD COLUMN official_phone text
            CONSTRAINT ck_organizations_official_phone CHECK (official_phone ~ {_PHONE})""",
    "CREATE UNIQUE INDEX uq_organizations_code ON organizations (organization_code)",
    "CREATE UNIQUE INDEX uq_organizations_name_ci ON organizations (lower(name))",
    # The Organization ID is permanent.
    """CREATE FUNCTION app_keep_organization_code() RETURNS trigger
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           IF NEW.organization_code IS DISTINCT FROM OLD.organization_code THEN
               RAISE EXCEPTION 'The Organization ID is permanent and cannot be changed.'
                   USING ERRCODE = 'integrity_constraint_violation';
           END IF;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_organizations_code_is_permanent
       BEFORE UPDATE OF organization_code ON organizations
       FOR EACH ROW EXECUTE FUNCTION app_keep_organization_code()""",
]

_USERS = [
    f"""ALTER TABLE users
        ADD COLUMN username text CONSTRAINT ck_users_username_format CHECK (username ~ '^[A-Za-z0-9_.-]{{3,64}}$'),
        ADD COLUMN phone text CONSTRAINT ck_users_phone_format CHECK (phone ~ {_PHONE})""",
    "CREATE UNIQUE INDEX uq_users_username_ci ON users (lower(username)) WHERE username IS NOT NULL",
    # Admin and Supervisor IDs: the new 6 letters + 11 digits, or the original prefix format
    # (kept so existing IDs and history stay valid). Guard IDs are unchanged.
    "ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format",
    """ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format CHECK (
           CASE role
               WHEN 'ADMIN' THEN display_id ~ '^(ADM-[0-9]{11}|[A-Z]{6}[0-9]{11})$'
               WHEN 'SUPERVISOR' THEN display_id ~ '^(SUP-[0-9]{11}|[A-Z]{6}[0-9]{11})$'
               WHEN 'GUARD' THEN display_id ~ '^GRD-[0-9]{11}$'
               ELSE false
           END)""",
    """CREATE TABLE user_credentials (
           user_id              uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
           password_hash        text NOT NULL
                                CONSTRAINT ck_user_credentials_argon2 CHECK (password_hash LIKE '$argon2id$%'),
           password_changed_at  timestamptz NOT NULL DEFAULT now(),
           created_at           timestamptz NOT NULL DEFAULT now()
       )""",
]

_REGISTRATIONS = [
    f"""CREATE TABLE organization_registrations (
           id                 uuid PRIMARY KEY,
           organization_code  text NOT NULL CONSTRAINT ck_org_registrations_code_format
                                  CHECK (organization_code ~ '^ORG-[0-9]{{11}}$'),
           status             text NOT NULL CONSTRAINT ck_org_registrations_status
                                  CHECK (status IN ('PENDING', 'COMPLETED', 'EXPIRED')),
           organization_name  text CONSTRAINT ck_org_registrations_name
                                  CHECK (length(organization_name) BETWEEN 2 AND 150),
           admin_full_name    text CONSTRAINT ck_org_registrations_full_name
                                  CHECK (length(admin_full_name) BETWEEN 2 AND 120),
           username           text CONSTRAINT ck_org_registrations_username
                                  CHECK (username ~ '^[A-Za-z0-9_.-]{{3,64}}$'),
           email              text CONSTRAINT ck_org_registrations_email CHECK (length(email) BETWEEN 3 AND 254),
           phone              text CONSTRAINT ck_org_registrations_phone CHECK (phone ~ {_PHONE}),
           password_hash      text CONSTRAINT ck_org_registrations_argon2 CHECK (password_hash LIKE '$argon2id$%'),
           recipient_digest   text CONSTRAINT ck_org_registrations_digest CHECK (recipient_digest ~ '^[0-9a-f]{{64}}$'),
           created_at         timestamptz NOT NULL DEFAULT now(),
           expires_at         timestamptz NOT NULL,
           email_verified_at  timestamptz,
           completed_at       timestamptz,
           organization_id    uuid REFERENCES organizations (id),
           -- Applicant details exist only while the registration is pending.
           CONSTRAINT ck_org_registrations_details_only_while_pending CHECK (
               CASE WHEN status = 'PENDING'
                    THEN num_nonnulls(organization_name, admin_full_name, username, email, phone,
                                      password_hash, recipient_digest) = 7
                    ELSE num_nonnulls(organization_name, admin_full_name, username, email, phone,
                                      password_hash, recipient_digest) = 0
               END),
           CONSTRAINT ck_org_registrations_completed CHECK (
               (status = 'COMPLETED')
               = (organization_id IS NOT NULL AND completed_at IS NOT NULL AND email_verified_at IS NOT NULL))
       )""",
    "CREATE UNIQUE INDEX uq_org_registrations_code ON organization_registrations (organization_code)",
    """CREATE UNIQUE INDEX uq_org_registrations_organization ON organization_registrations (organization_id)
       WHERE organization_id IS NOT NULL""",
    """CREATE INDEX ix_org_registrations_pending_expiry ON organization_registrations (expires_at)
       WHERE status = 'PENDING'""",
    """CREATE TABLE email_otp_challenges (
           id                uuid PRIMARY KEY,
           purpose           text NOT NULL
                             CONSTRAINT ck_email_otp_purpose CHECK (purpose IN ('ORGANIZATION_REGISTRATION')),
           registration_id   uuid NOT NULL REFERENCES organization_registrations (id) ON DELETE CASCADE,
           recipient_digest  text NOT NULL CONSTRAINT ck_email_otp_digest CHECK (recipient_digest ~ '^[0-9a-f]{64}$'),
           code_hash         text NOT NULL CONSTRAINT ck_email_otp_code_hash CHECK (code_hash ~ '^[0-9a-f]{64}$'),
           issued_at         timestamptz NOT NULL DEFAULT now(),
           expires_at        timestamptz NOT NULL,
           max_attempts      integer NOT NULL,
           failed_attempts   integer NOT NULL DEFAULT 0,
           consumed_at       timestamptz,
           superseded_at     timestamptz,
           CONSTRAINT ck_email_otp_expiry CHECK (expires_at > issued_at),
           CONSTRAINT ck_email_otp_attempts CHECK (max_attempts BETWEEN 1 AND 20
                                                   AND failed_attempts BETWEEN 0 AND max_attempts),
           CONSTRAINT ck_email_otp_one_ending CHECK (consumed_at IS NULL OR superseded_at IS NULL)
       )""",
    # At most one usable code per registration: issuing a new one retires the old one.
    """CREATE UNIQUE INDEX uq_email_otp_one_open_per_registration ON email_otp_challenges (registration_id)
       WHERE consumed_at IS NULL AND superseded_at IS NULL""",
    "CREATE INDEX ix_email_otp_recipient_recent ON email_otp_challenges (recipient_digest, issued_at)",
    "CREATE INDEX ix_email_otp_registration ON email_otp_challenges (registration_id, issued_at)",
]

# Internal helpers: called only from the functions below (which run as the owner).
_HELPERS = [
    """CREATE FUNCTION app_org_registration_expire(p_registration_id uuid) RETURNS void
       LANGUAGE sql SET search_path = pg_catalog, public AS $$
           UPDATE organization_registrations
              SET status = 'EXPIRED', organization_name = NULL, admin_full_name = NULL, username = NULL,
                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL
            WHERE id = p_registration_id AND status = 'PENDING'
       $$""",
    # Erase applicant details of pending registrations that ran out of time. Rows another
    # request is using are skipped and handled by a later call.
    """CREATE FUNCTION app_org_registration_expire_stale() RETURNS void
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           UPDATE organization_registrations
              SET status = 'EXPIRED', organization_name = NULL, admin_full_name = NULL, username = NULL,
                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL
            WHERE id IN (SELECT id FROM organization_registrations
                          WHERE status = 'PENDING' AND expires_at <= now()
                          ORDER BY expires_at
                          LIMIT 200
                          FOR UPDATE SKIP LOCKED);
           -- Codes are needed for an hour at most (the hourly limit); keep them a day.
           DELETE FROM email_otp_challenges WHERE issued_at < now() - interval '1 day';
       END $$""",
]
_HELPER_SIGNATURES = ["app_org_registration_expire(uuid)", "app_org_registration_expire_stale()"]

_FUNCTIONS = [
    # Step 1: store a pending registration. Name and username are checked here, and again
    # (race-safely, by unique indexes) on completion. The email is checked only on completion,
    # after the OTP proves the caller owns it, so this step cannot reveal registered emails.
    """CREATE FUNCTION app_org_registration_submit(
           p_registration_id uuid, p_organization_code text, p_organization_name text,
           p_admin_full_name text, p_username text, p_email text, p_phone text,
           p_password_hash text, p_recipient_digest text, p_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_constraint text;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'registration lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           PERFORM app_org_registration_expire_stale();
           IF EXISTS (SELECT 1 FROM organizations o WHERE lower(o.name) = lower(p_organization_name)) THEN
               RETURN 'ORGANIZATION_NAME_TAKEN';
           END IF;
           IF EXISTS (SELECT 1 FROM users u
                       WHERE u.username IS NOT NULL AND lower(u.username) = lower(p_username)) THEN
               RETURN 'USERNAME_TAKEN';
           END IF;
           IF EXISTS (SELECT 1 FROM organizations o WHERE o.organization_code = p_organization_code) THEN
               RETURN 'ORGANIZATION_CODE_TAKEN';
           END IF;
           BEGIN
               INSERT INTO organization_registrations (
                   id, organization_code, status, organization_name, admin_full_name, username,
                   email, phone, password_hash, recipient_digest, created_at, expires_at)
               VALUES (
                   p_registration_id, p_organization_code, 'PENDING', p_organization_name, p_admin_full_name,
                   p_username, p_email, p_phone, p_password_hash, p_recipient_digest, now(),
                   now() + make_interval(secs => p_ttl_seconds));
           EXCEPTION WHEN unique_violation THEN
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               IF v_constraint = 'uq_org_registrations_code' THEN
                   RETURN 'ORGANIZATION_CODE_TAKEN';
               END IF;
               RAISE;
           END;
           RETURN 'CREATED';
       END $$""",
    # Step 2: issue a code (also used for "resend"). Enforces the resend cooldown for this
    # registration and the hourly limit for the recipient address across all registrations.
    """CREATE FUNCTION app_org_registration_issue_otp(
           p_registration_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text,
                      registration_expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           r organization_registrations%ROWTYPE;
           v_last timestamptz;
           v_count integer;
           v_oldest timestamptz;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 30 AND 3600 OR p_max_attempts NOT BETWEEN 1 AND 20
              OR p_cooldown_seconds NOT BETWEEN 0 AND 3600 OR p_hourly_limit NOT BETWEEN 1 AND 100 THEN
               RAISE EXCEPTION 'OTP settings out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO r FROM organization_registrations WHERE id = p_registration_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'REGISTRATION_NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF r.status = 'COMPLETED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF r.status <> 'PENDING' OR r.expires_at <= now() THEN
               PERFORM app_org_registration_expire(r.id);
               RETURN QUERY SELECT 'REGISTRATION_EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           -- One issuer at a time per recipient, so the hourly limit holds under concurrency.
           PERFORM pg_advisory_xact_lock(hashtextextended('email_otp:' || r.recipient_digest, 0));
           SELECT max(c.issued_at) INTO v_last FROM email_otp_challenges c WHERE c.registration_id = r.id;
           IF v_last IS NOT NULL AND v_last + make_interval(secs => p_cooldown_seconds) > now() THEN
               RETURN QUERY SELECT 'COOLDOWN'::text,
                   greatest(1, ceil(extract(epoch FROM
                       v_last + make_interval(secs => p_cooldown_seconds) - now())))::integer,
                   NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT count(*), min(c.issued_at) INTO v_count, v_oldest
             FROM email_otp_challenges c
            WHERE c.recipient_digest = r.recipient_digest AND c.issued_at > now() - interval '1 hour';
           IF v_count >= p_hourly_limit THEN
               RETURN QUERY SELECT 'HOURLY_LIMIT'::text,
                   greatest(1, ceil(extract(epoch FROM v_oldest + interval '1 hour' - now())))::integer,
                   NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           UPDATE email_otp_challenges c SET superseded_at = now()
            WHERE c.registration_id = r.id AND c.consumed_at IS NULL AND c.superseded_at IS NULL;
           INSERT INTO email_otp_challenges (
               id, purpose, registration_id, recipient_digest, code_hash, issued_at, expires_at, max_attempts)
           VALUES (
               p_challenge_id, 'ORGANIZATION_REGISTRATION', r.id, r.recipient_digest, p_code_hash, now(),
               now() + make_interval(secs => p_ttl_seconds), p_max_attempts);
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, r.email, r.expires_at;
       END $$""",
    # Step 3: check a code. A wrong code counts as an attempt; the caller commits that even
    # though the request fails. A right code is used up and marks the email verified.
    """CREATE FUNCTION app_org_registration_verify_otp(p_registration_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           r organization_registrations%ROWTYPE;
           c email_otp_challenges%ROWTYPE;
       BEGIN
           SELECT * INTO r FROM organization_registrations WHERE id = p_registration_id FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'REGISTRATION_NOT_FOUND'::text, NULL::integer;
               RETURN;
           END IF;
           IF r.status = 'COMPLETED' THEN
               RETURN QUERY SELECT 'ALREADY_COMPLETED'::text, NULL::integer;
               RETURN;
           END IF;
           IF r.status <> 'PENDING' OR r.expires_at <= now() THEN
               PERFORM app_org_registration_expire(r.id);
               RETURN QUERY SELECT 'REGISTRATION_EXPIRED'::text, NULL::integer;
               RETURN;
           END IF;
           SELECT * INTO c FROM email_otp_challenges ch
            WHERE ch.registration_id = r.id AND ch.consumed_at IS NULL AND ch.superseded_at IS NULL
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
               UPDATE email_otp_challenges ch SET failed_attempts = ch.failed_attempts + 1 WHERE ch.id = c.id;
               IF c.failed_attempts + 1 >= c.max_attempts THEN
                   RETURN QUERY SELECT 'ATTEMPTS_EXCEEDED'::text, 0;
               ELSE
                   RETURN QUERY SELECT 'INVALID_CODE'::text, c.max_attempts - c.failed_attempts - 1;
               END IF;
               RETURN;
           END IF;
           UPDATE email_otp_challenges ch SET consumed_at = now() WHERE ch.id = c.id;
           UPDATE organization_registrations reg SET email_verified_at = now() WHERE reg.id = r.id;
           RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer;
       END $$""",
    # Step 4: create the organization, its Main Campus and the Main Admin, all or nothing.
    # Requires a verified email in this same transaction. Any conflict undoes every insert.
    """CREATE FUNCTION app_org_registration_complete(
           p_registration_id uuid, p_organization_id uuid, p_campus_id uuid, p_user_id uuid, p_display_id text)
       RETURNS TABLE (outcome text, organization_code text, organization_name text, campus_name text,
                      admin_full_name text, username text, email text, phone text, completed_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           r organization_registrations%ROWTYPE;
           v_constraint text;
           v_outcome text;
       BEGIN
           SELECT * INTO r FROM organization_registrations WHERE id = p_registration_id FOR UPDATE;
           IF NOT FOUND THEN
               v_outcome := 'REGISTRATION_NOT_FOUND';
           ELSIF r.status = 'COMPLETED' THEN
               v_outcome := 'ALREADY_COMPLETED';
           ELSIF r.status <> 'PENDING' OR r.expires_at <= now() THEN
               v_outcome := 'REGISTRATION_EXPIRED';
           ELSIF r.email_verified_at IS NULL THEN
               v_outcome := 'EMAIL_NOT_VERIFIED';
           ELSIF EXISTS (SELECT 1 FROM users u WHERE lower(u.email) = lower(r.email)) THEN
               v_outcome := 'EMAIL_ALREADY_REGISTERED';
           ELSIF EXISTS (SELECT 1 FROM organizations o WHERE lower(o.name) = lower(r.organization_name)) THEN
               v_outcome := 'ORGANIZATION_NAME_TAKEN';
           ELSIF EXISTS (SELECT 1 FROM users u
                          WHERE u.username IS NOT NULL AND lower(u.username) = lower(r.username)) THEN
               v_outcome := 'USERNAME_TAKEN';
           END IF;
           IF v_outcome IS NOT NULL THEN
               RETURN QUERY SELECT v_outcome, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                                   NULL::text, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           BEGIN
               INSERT INTO organizations (id, name, organization_code, official_email, official_phone)
               VALUES (p_organization_id, r.organization_name, r.organization_code, r.email, r.phone);
               INSERT INTO campuses (id, organization_id, name, status)
               VALUES (p_campus_id, p_organization_id, 'Main Campus', 'ACTIVE');
               INSERT INTO users (id, organization_id, campus_id, role, status, full_name, email,
                                  display_id, is_first_admin, username, phone)
               VALUES (p_user_id, p_organization_id, p_campus_id, 'ADMIN', 'ACTIVE', r.admin_full_name, r.email,
                       p_display_id, true, r.username, r.phone);
               INSERT INTO user_credentials (user_id, password_hash) VALUES (p_user_id, r.password_hash);
               UPDATE organization_registrations reg
                  SET status = 'COMPLETED', organization_id = p_organization_id, completed_at = now(),
                      organization_name = NULL, admin_full_name = NULL, username = NULL, email = NULL,
                      phone = NULL, password_hash = NULL, recipient_digest = NULL
                WHERE reg.id = r.id;
           EXCEPTION WHEN unique_violation THEN
               -- A concurrent registration won the name, email or username, or the display ID collided.
               GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
               RETURN QUERY SELECT
                   CASE v_constraint
                       WHEN 'uq_organizations_name_ci' THEN 'ORGANIZATION_NAME_TAKEN'
                       WHEN 'uq_users_email' THEN 'EMAIL_ALREADY_REGISTERED'
                       WHEN 'uq_users_username_ci' THEN 'USERNAME_TAKEN'
                       WHEN 'uq_users_display_id' THEN 'DISPLAY_ID_TAKEN'
                       ELSE 'CONFLICT'
                   END,
                   NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                   NULL::timestamptz;
               RETURN;
           END;
           RETURN QUERY SELECT 'COMPLETED'::text, r.organization_code, r.organization_name, 'Main Campus'::text,
                               r.admin_full_name, r.username, r.email, r.phone, now();
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_org_registration_submit(uuid, text, text, text, text, text, text, text, text, integer)",
    "app_org_registration_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_org_registration_verify_otp(uuid, text)",
    "app_org_registration_complete(uuid, uuid, uuid, uuid, text)",
]

_NEW_TABLES = ["user_credentials", "organization_registrations", "email_otp_challenges"]


def _ensure_names_are_unique_ignoring_case() -> None:
    clashes = op.get_bind().execute(
        text("SELECT count(*) FROM (SELECT 1 FROM organizations GROUP BY lower(name) HAVING count(*) > 1) d")
    )
    if clashes.scalar_one():
        raise RuntimeError(
            "Cannot make organization names unique ignoring capitalization: some existing organizations "
            "have the same name apart from capitalization. Rename them, then run the migration again."
        )


def upgrade() -> None:
    app = _app_role()
    _ensure_names_are_unique_ignoring_case()
    for statement in _ORGANIZATIONS + _USERS + _REGISTRATIONS + _HELPERS + _FUNCTIONS:
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
    for signature in reversed(_FUNCTION_SIGNATURES + _HELPER_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS email_otp_challenges, organization_registrations, user_credentials")
    op.execute("DROP INDEX IF EXISTS uq_users_username_ci")
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_display_id_format")
    # The original rule, for new rows. Rows with the newer ID format (if any) are left in place.
    op.execute(
        """ALTER TABLE users ADD CONSTRAINT ck_users_display_id_format CHECK (
               display_id ~ '^(ADM|SUP|GRD)-[0-9]{11}$'
               AND left(display_id, 3) = CASE role WHEN 'ADMIN' THEN 'ADM' WHEN 'SUPERVISOR' THEN 'SUP' ELSE 'GRD' END
           ) NOT VALID"""
    )
    op.execute(
        """DO $$ BEGIN
               IF NOT EXISTS (SELECT 1 FROM users
                               WHERE NOT (display_id ~ '^(ADM|SUP|GRD)-[0-9]{11}$'
                                          AND left(display_id, 3) = CASE role WHEN 'ADMIN' THEN 'ADM'
                                                                    WHEN 'SUPERVISOR' THEN 'SUP' ELSE 'GRD' END)) THEN
                   ALTER TABLE users VALIDATE CONSTRAINT ck_users_display_id_format;
               END IF;
           END $$"""
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone, DROP COLUMN IF EXISTS username")
    op.execute("DROP TRIGGER IF EXISTS trg_organizations_code_is_permanent ON organizations")
    op.execute("DROP FUNCTION IF EXISTS app_keep_organization_code()")
    op.execute("DROP INDEX IF EXISTS uq_organizations_name_ci")
    op.execute("DROP INDEX IF EXISTS uq_organizations_code")
    op.execute(
        "ALTER TABLE organizations DROP COLUMN IF EXISTS official_phone, "
        "DROP COLUMN IF EXISTS official_email, DROP COLUMN IF EXISTS organization_code"
    )
    op.execute("DROP FUNCTION IF EXISTS app_new_organization_code()")
