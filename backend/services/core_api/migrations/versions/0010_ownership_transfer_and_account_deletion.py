"""Main Admin ownership transfer, and campus-scoped Admin account deletion.

Ownership: the Main Admin is the existing ``users.is_first_admin`` flag (one per
organization, enforced by ``uq_users_one_first_admin_per_org``). A transfer moves that flag.
No second ownership system is added.

* ``ownership_transfers``: Main Admin A asks Admin C to take ownership.
  1. A's password and an emailed code are required.
  2. C must then accept.
  3. On acceptance, the flag moves from A to C in one statement pair: only one Main Admin at a
     time.
  Requests expire. A refused, expired, cancelled or stale request changes nothing.
* ``admin_account_deletions``: an Admin's own deletion request. It needs the Admin ID, password
  and a fresh emailed code of its own (``ACCOUNT_DELETION``); the transfer code cannot be
  reused.
  - **Who must transfer first:** a Main Admin while another eligible Admin exists.
  - **Campus deletion:** if no other active Admin remains on the Admin's campus, the campus and
    everything assigned to it is deleted. That is its Supervisors, Guards and other users, their
    credentials and pending requests, its cameras, and its pending creations. Otherwise only the
    Admin's own account is deleted.
  - **Last campus:** if it was the organization's last campus, the organization is marked
    INACTIVE and its official contacts (the Admin's copies) are cleared. The organization row and
    its permanent Organization ID stay.
  - **Scope:** deletes are explicit and filtered by organization and campus. No new cascading
    foreign key crosses a campus or organization boundary. There is no training-data table to
    touch.
* Ownership transfers and deletions are written to ``platform_audit_logs`` (the existing
  audit mechanism) without names, emails, passwords or codes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_PURPOSES_0009 = (
    "('ADMIN_STEP_UP', 'SUPERVISOR_EMAIL', 'PASSWORD_RESET', 'PROFILE_CHANGE', 'PROFILE_NEW_EMAIL', "
    "'GUARD_STEP_UP', 'GUARD_EMAIL')"
)
_PURPOSES = _PURPOSES_0009[:-1] + ", 'OWNERSHIP_TRANSFER', 'ACCOUNT_DELETION')"

# The signed-in caller as an active Admin of an active organization and campus.
_ACTOR_ADMIN = """(SELECT u.id, u.organization_id, u.campus_id, u.is_first_admin, u.email
      FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role = 'ADMIN'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""

# Admins who can take ownership: active, of an active campus of the same organization, with a
# password (so they can sign in and accept), and not the given user.
_ELIGIBLE = """SELECT u.id, u.display_id, u.full_name
      FROM users u
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
      JOIN user_credentials c ON c.user_id = u.id
     WHERE u.organization_id = {org} AND u.id <> {me} AND u.role = 'ADMIN'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE'"""


def _audit(action: str, metadata: str) -> str:
    return (
        "INSERT INTO platform_audit_logs (id, occurred_at, action, outcome, metadata) "
        f"VALUES (gen_random_uuid(), now(), '{action}', 'SUCCESS', {metadata})"
    )


_SCHEMA = [
    "ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose",
    f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES})",
    """CREATE TABLE ownership_transfers (
           id                 uuid PRIMARY KEY,
           organization_id    uuid NOT NULL REFERENCES organizations (id),
           from_user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           to_user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status             text NOT NULL CONSTRAINT ck_ownership_transfers_status CHECK (status IN (
                                  'AWAITING_VERIFICATION', 'PENDING_ACCEPTANCE', 'ACCEPTED', 'REJECTED',
                                  'CANCELLED', 'EXPIRED')),
           created_at         timestamptz NOT NULL DEFAULT now(),
           expires_at         timestamptz NOT NULL,
           verified_at        timestamptz,
           decided_at         timestamptz,
           CONSTRAINT ck_ownership_transfers_parties CHECK (from_user_id <> to_user_id),
           CONSTRAINT ck_ownership_transfers_expiry CHECK (expires_at > created_at)
       )""",
    # At most one open transfer per Main Admin.
    """CREATE UNIQUE INDEX uq_ownership_transfers_one_open ON ownership_transfers (from_user_id)
       WHERE status IN ('AWAITING_VERIFICATION', 'PENDING_ACCEPTANCE')""",
    "CREATE INDEX ix_ownership_transfers_to ON ownership_transfers (to_user_id, status)",
    """CREATE TABLE admin_account_deletions (
           id          uuid PRIMARY KEY,
           user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           status      text NOT NULL CONSTRAINT ck_admin_deletions_status
                           CHECK (status IN ('AWAITING_VERIFICATION', 'SUPERSEDED')),
           created_at  timestamptz NOT NULL DEFAULT now(),
           expires_at  timestamptz NOT NULL,
           CONSTRAINT ck_admin_deletions_expiry CHECK (expires_at > created_at)
       )""",
]

_HELPERS = [
    # Why the caller may not delete their account yet (NULL when they may).
    f"""CREATE FUNCTION app_admin_deletion_blocker(p_user_id uuid) RETURNS text
       LANGUAGE plpgsql STABLE SET search_path = pg_catalog, public AS $$
       DECLARE
           a record;
       BEGIN
           SELECT u.organization_id, u.campus_id, u.is_first_admin INTO a FROM users u WHERE u.id = p_user_id;
           IF NOT a.is_first_admin THEN
               RETURN NULL;  -- an additional Admin never needs a transfer
           END IF;
           IF EXISTS (SELECT 1 FROM ownership_transfers t
                       WHERE t.from_user_id = p_user_id AND t.status = 'PENDING_ACCEPTANCE'
                         AND t.expires_at > now()) THEN
               RETURN 'OWNERSHIP_TRANSFER_PENDING';
           END IF;
           IF EXISTS ({_ELIGIBLE.format(org="a.organization_id", me="p_user_id")}) THEN
               RETURN 'OWNERSHIP_TRANSFER_REQUIRED';
           END IF;
           IF EXISTS (SELECT 1 FROM campuses ca
                       WHERE ca.organization_id = a.organization_id AND ca.id <> a.campus_id) THEN
               RETURN 'OWNERSHIP_SUCCESSOR_REQUIRED';  -- other campuses, but nobody can take ownership
           END IF;
           RETURN NULL;
       END $$""",
    # Close transfers whose acceptance window has passed.
    """CREATE FUNCTION app_ownership_transfers_expire() RETURNS void
       LANGUAGE sql SET search_path = pg_catalog, public AS $$
           UPDATE ownership_transfers SET status = 'EXPIRED', decided_at = now()
            WHERE status IN ('AWAITING_VERIFICATION', 'PENDING_ACCEPTANCE') AND expires_at <= now()
       $$""",
]
_HELPER_SIGNATURES = ["app_admin_deletion_blocker(uuid)", "app_ownership_transfers_expire()"]

_FUNCTIONS = [
    # --- Overview for the Delete Account button ------------------------------------------------
    f"""CREATE FUNCTION app_admin_deletion_overview()
       RETURNS TABLE (is_main_admin boolean, blocker text, campus_will_be_deleted boolean,
                      organization_will_be_deactivated boolean, transfer_id uuid, transfer_status text,
                      transfer_to_name text, transfer_expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           t record;
           v_campus_alone boolean;
       BEGIN
           PERFORM app_ownership_transfers_expire();
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           IF NOT FOUND THEN
               RETURN;
           END IF;
           v_campus_alone := NOT EXISTS (
               SELECT 1 FROM users u WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id
                  AND u.id <> me.id AND u.role = 'ADMIN' AND u.status = 'ACTIVE');
           SELECT tr.id, tr.status, u.full_name, tr.expires_at INTO t
             FROM ownership_transfers tr JOIN users u ON u.id = tr.to_user_id
            WHERE tr.from_user_id = me.id ORDER BY tr.created_at DESC LIMIT 1;
           RETURN QUERY SELECT me.is_first_admin, app_admin_deletion_blocker(me.id), v_campus_alone,
               v_campus_alone AND NOT EXISTS (SELECT 1 FROM campuses ca
                                               WHERE ca.organization_id = me.organization_id AND ca.id <> me.campus_id),
               t.id, t.status, t.full_name, t.expires_at;
       END $$""",
    # Admins the Main Admin may hand ownership to (names and Admin IDs only).
    f"""CREATE FUNCTION app_ownership_candidates()
       RETURNS TABLE (admin_id text, full_name text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
       BEGIN
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           IF NOT FOUND OR NOT me.is_first_admin THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT e.display_id, e.full_name
             FROM ({_ELIGIBLE.format(org="me.organization_id", me="me.id")}) e ORDER BY e.full_name;
       END $$""",
    # --- Ownership transfer --------------------------------------------------------------------
    f"""CREATE FUNCTION app_ownership_transfer_start(p_transfer_id uuid, p_recipient_admin_id text,
                                                   p_verify_ttl_seconds integer)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
           v_to uuid;
       BEGIN
           IF p_verify_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'verification lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           PERFORM app_ownership_transfers_expire();
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           IF NOT FOUND OR NOT me.is_first_admin THEN
               RETURN 'NOT_MAIN_ADMIN';
           END IF;
           SELECT e.id INTO v_to FROM ({_ELIGIBLE.format(org="me.organization_id", me="me.id")}) e
            WHERE e.display_id = p_recipient_admin_id;
           IF v_to IS NULL THEN
               RETURN 'RECIPIENT_NOT_ELIGIBLE';
           END IF;
           -- An unverified earlier attempt is replaced; a request already waiting for acceptance is not.
           UPDATE ownership_transfers SET status = 'CANCELLED', decided_at = now()
            WHERE from_user_id = me.id AND status = 'AWAITING_VERIFICATION';
           IF EXISTS (SELECT 1 FROM ownership_transfers
                       WHERE from_user_id = me.id AND status = 'PENDING_ACCEPTANCE') THEN
               RETURN 'OWNERSHIP_TRANSFER_PENDING';
           END IF;
           INSERT INTO ownership_transfers (id, organization_id, from_user_id, to_user_id, status, created_at,
                                            expires_at)
           VALUES (p_transfer_id, me.organization_id, me.id, v_to, 'AWAITING_VERIFICATION', now(),
                   now() + make_interval(secs => p_verify_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    f"""CREATE FUNCTION app_ownership_transfer_issue_otp(
           p_transfer_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           t ownership_transfers%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO t FROM ownership_transfers tr
            WHERE tr.id = p_transfer_id AND tr.from_user_id = me.id FOR UPDATE;
           IF me.id IS NULL OR NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF t.status <> 'AWAITING_VERIFICATION' THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF t.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('OWNERSHIP_TRANSFER', t.id, 'OWNERSHIP_TRANSFER:' || me.id::text,
               p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, me.email, t.expires_at;
       END $$""",
    # A verified code sends the request to the recipient, who has p_accept_ttl_seconds to decide.
    f"""CREATE FUNCTION app_ownership_transfer_verify_otp(p_transfer_id uuid, p_code_hash text,
                                                        p_accept_ttl_seconds integer)
       RETURNS TABLE (outcome text, attempts_remaining integer, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           t ownership_transfers%ROWTYPE;
           r record;
       BEGIN
           IF p_accept_ttl_seconds NOT BETWEEN 300 AND 1209600 THEN
               RAISE EXCEPTION 'acceptance lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO t FROM ownership_transfers tr
            WHERE tr.id = p_transfer_id AND tr.from_user_id = me.id FOR UPDATE;
           IF me.id IS NULL OR NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           IF t.status <> 'AWAITING_VERIFICATION' THEN
               RETURN QUERY SELECT 'ALREADY_VERIFIED'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           IF t.expires_at <= now() OR NOT me.is_first_admin THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('OWNERSHIP_TRANSFER', t.id, p_code_hash);
           IF r.outcome <> 'VERIFIED' THEN
               RETURN QUERY SELECT r.outcome, r.attempts_remaining, NULL::timestamptz;
               RETURN;
           END IF;
           UPDATE ownership_transfers tr
              SET status = 'PENDING_ACCEPTANCE', verified_at = now(),
                  expires_at = now() + make_interval(secs => p_accept_ttl_seconds)
            WHERE tr.id = t.id;
           {
        _audit(
            "OWNERSHIP_TRANSFER_REQUESTED",
            "jsonb_build_object('organization_code', (SELECT o.organization_code FROM organizations o "
            "WHERE o.id = t.organization_id))",
        )
    };
           RETURN QUERY SELECT 'VERIFIED'::text, NULL::integer, now() + make_interval(secs => p_accept_ttl_seconds);
       END $$""",
    """CREATE FUNCTION app_ownership_transfer_cancel(p_transfer_id uuid) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           UPDATE ownership_transfers tr SET status = 'CANCELLED', decided_at = now()
            WHERE tr.id = p_transfer_id AND tr.from_user_id = app_current_user_id()
              AND tr.status IN ('AWAITING_VERIFICATION', 'PENDING_ACCEPTANCE');
           RETURN CASE WHEN FOUND THEN 'CANCELLED' ELSE 'NOT_FOUND' END;
       END $$""",
    # Requests waiting for the caller's decision.
    f"""CREATE FUNCTION app_ownership_transfers_incoming()
       RETURNS TABLE (transfer_id uuid, from_name text, created_at timestamptz, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
       BEGIN
           PERFORM app_ownership_transfers_expire();
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           IF NOT FOUND THEN
               RETURN;
           END IF;
           RETURN QUERY SELECT tr.id, u.full_name, tr.created_at, tr.expires_at
             FROM ownership_transfers tr JOIN users u ON u.id = tr.from_user_id
            WHERE tr.to_user_id = me.id AND tr.status = 'PENDING_ACCEPTANCE'
            ORDER BY tr.created_at;
       END $$""",
    # The recipient accepts or rejects. Acceptance moves the Main Admin flag, all or nothing.
    f"""CREATE FUNCTION app_ownership_transfer_decide(p_transfer_id uuid, p_accept boolean) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
           t ownership_transfers%ROWTYPE;
           v_code text;
       BEGIN
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO t FROM ownership_transfers tr WHERE tr.id = p_transfer_id AND tr.to_user_id = me.id FOR UPDATE;
           IF me.id IS NULL OR NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF t.status <> 'PENDING_ACCEPTANCE' THEN
               RETURN CASE WHEN t.status = 'ACCEPTED' THEN 'ALREADY_COMPLETED' ELSE 'EXPIRED' END;
           END IF;
           IF t.expires_at <= now() THEN
               UPDATE ownership_transfers tr SET status = 'EXPIRED', decided_at = now() WHERE tr.id = t.id;
               RETURN 'EXPIRED';
           END IF;
           SELECT o.organization_code INTO v_code FROM organizations o WHERE o.id = t.organization_id;
           IF NOT p_accept THEN
               UPDATE ownership_transfers tr SET status = 'REJECTED', decided_at = now() WHERE tr.id = t.id;
               {_audit("OWNERSHIP_TRANSFER_REJECTED", "jsonb_build_object('organization_code', v_code)")};
               RETURN 'REJECTED';
           END IF;
           -- Stale: the sender is no longer the active Main Admin of this organization.
           IF NOT EXISTS (SELECT 1 FROM users u WHERE u.id = t.from_user_id AND u.is_first_admin
                             AND u.status = 'ACTIVE' AND u.organization_id = t.organization_id)
              OR me.organization_id <> t.organization_id THEN
               UPDATE ownership_transfers tr SET status = 'EXPIRED', decided_at = now() WHERE tr.id = t.id;
               RETURN 'STALE';
           END IF;
           UPDATE users u SET is_first_admin = false WHERE u.id = t.from_user_id;
           UPDATE users u SET is_first_admin = true WHERE u.id = me.id;
           UPDATE ownership_transfers tr SET status = 'ACCEPTED', decided_at = now() WHERE tr.id = t.id;
           {_audit("OWNERSHIP_TRANSFER_ACCEPTED", "jsonb_build_object('organization_code', v_code)")};
           RETURN 'ACCEPTED';
       END $$""",
    # --- Account deletion ------------------------------------------------------------------------
    f"""CREATE FUNCTION app_admin_deletion_start(p_request_id uuid, p_ttl_seconds integer) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           me record;
           v_blocker text;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 3600 THEN
               RAISE EXCEPTION 'deletion lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           PERFORM app_ownership_transfers_expire();
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           IF NOT FOUND THEN
               RETURN 'NOT_AUTHORIZED';
           END IF;
           v_blocker := app_admin_deletion_blocker(me.id);
           IF v_blocker IS NOT NULL THEN
               RETURN v_blocker;
           END IF;
           UPDATE user_credentials uc SET failed_login_attempts = 0, last_failed_login_at = NULL, locked_until = NULL
            WHERE uc.user_id = me.id;
           UPDATE admin_account_deletions SET status = 'SUPERSEDED'
            WHERE user_id = me.id AND status = 'AWAITING_VERIFICATION';
           INSERT INTO admin_account_deletions (id, user_id, status, created_at, expires_at)
           VALUES (p_request_id, me.id, 'AWAITING_VERIFICATION', now(), now() + make_interval(secs => p_ttl_seconds));
           RETURN 'CREATED';
       END $$""",
    f"""CREATE FUNCTION app_admin_deletion_issue_otp(
           p_request_id uuid, p_challenge_id uuid, p_code_hash text, p_ttl_seconds integer,
           p_max_attempts integer, p_cooldown_seconds integer, p_hourly_limit integer)
       RETURNS TABLE (outcome text, retry_after_seconds integer, email text, expires_at timestamptz)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           d admin_account_deletions%ROWTYPE;
           r record;
       BEGIN
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO d FROM admin_account_deletions ad WHERE ad.id = p_request_id AND ad.user_id = me.id FOR UPDATE;
           IF me.id IS NULL OR NOT FOUND OR d.status <> 'AWAITING_VERIFICATION' THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           IF d.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_issue('ACCOUNT_DELETION', d.id, 'ACCOUNT_DELETION:' || me.id::text,
               p_challenge_id, p_code_hash, p_ttl_seconds, p_max_attempts, p_cooldown_seconds, p_hourly_limit);
           IF r.outcome <> 'ISSUED' THEN
               RETURN QUERY SELECT r.outcome, r.retry_after_seconds, NULL::text, NULL::timestamptz;
               RETURN;
           END IF;
           RETURN QUERY SELECT 'ISSUED'::text, NULL::integer, me.email, d.expires_at;
       END $$""",
    # Check the code, re-check the ownership rules, then delete, all in this transaction.
    f"""CREATE FUNCTION app_admin_deletion_complete(p_request_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer, campus_deleted boolean,
                      organization_deactivated boolean)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           d admin_account_deletions%ROWTYPE;
           r record;
           v_blocker text;
           v_code text;
           v_campus_alone boolean;
           v_last_campus boolean := false;
           v_users uuid[];
       BEGIN
           PERFORM app_ownership_transfers_expire();
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO d FROM admin_account_deletions ad WHERE ad.id = p_request_id AND ad.user_id = me.id FOR UPDATE;
           IF me.id IS NULL OR NOT FOUND OR d.status <> 'AWAITING_VERIFICATION' THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::integer, NULL::boolean, NULL::boolean;
               RETURN;
           END IF;
           IF d.expires_at <= now() THEN
               RETURN QUERY SELECT 'EXPIRED'::text, NULL::integer, NULL::boolean, NULL::boolean;
               RETURN;
           END IF;
           -- Lock the organization's Admin rows so a concurrent transfer cannot slip in between.
           PERFORM 1 FROM users u WHERE u.organization_id = me.organization_id AND u.role = 'ADMIN' FOR UPDATE;
           v_blocker := app_admin_deletion_blocker(me.id);
           IF v_blocker IS NOT NULL THEN
               RETURN QUERY SELECT v_blocker, NULL::integer, NULL::boolean, NULL::boolean;
               RETURN;
           END IF;
           SELECT * INTO r FROM app_otp_verify('ACCOUNT_DELETION', d.id, p_code_hash);
           IF r.outcome <> 'VERIFIED' THEN
               RETURN QUERY SELECT r.outcome, r.attempts_remaining, NULL::boolean, NULL::boolean;
               RETURN;
           END IF;

           SELECT o.organization_code INTO v_code FROM organizations o WHERE o.id = me.organization_id;
           v_campus_alone := NOT EXISTS (
               SELECT 1 FROM users u WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id
                  AND u.id <> me.id AND u.role = 'ADMIN' AND u.status = 'ACTIVE');
           IF v_campus_alone THEN
               -- Everyone assigned to this campus, and only this campus.
               SELECT array_agg(u.id) INTO v_users FROM users u
                WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id;
           ELSE
               v_users := ARRAY[me.id];
           END IF;

           -- One-time codes have no foreign keys; remove the ones belonging to these users' requests.
           DELETE FROM auth_otp_challenges ch WHERE ch.subject_id IN (
               SELECT id FROM admin_step_up_grants WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM guard_step_up_grants WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM supervisor_profile_changes WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM guard_profile_changes WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM password_reset_requests WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM ownership_transfers WHERE from_user_id = ANY (v_users)
               UNION ALL SELECT id FROM admin_account_deletions WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM supervisor_creations WHERE created_by = ANY (v_users)
               UNION ALL SELECT id FROM guard_creations WHERE created_by = ANY (v_users));
           -- Organization-level request history stays; only the links to deleted rows are cleared.
           UPDATE campus_registration_requests SET reviewed_by = NULL
            WHERE organization_id = me.organization_id AND reviewed_by = ANY (v_users);
           IF v_campus_alone THEN
               UPDATE campus_registration_requests SET approved_campus_id = NULL
                WHERE organization_id = me.organization_id AND approved_campus_id = me.campus_id;
               DELETE FROM supervisor_creations WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM guard_creations WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM cameras WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
           END IF;
           -- Credentials, sign-ins, grants, profile changes, resets, transfers and this request go with
           -- their users (existing ON DELETE CASCADE on user_id columns).
           DELETE FROM users u WHERE u.organization_id = me.organization_id AND u.id = ANY (v_users);
           IF v_campus_alone THEN
               DELETE FROM campuses ca WHERE ca.organization_id = me.organization_id AND ca.id = me.campus_id;
               IF NOT EXISTS (SELECT 1 FROM campuses ca WHERE ca.organization_id = me.organization_id) THEN
                   v_last_campus := true;
                   UPDATE organizations o SET status = 'INACTIVE', official_email = NULL, official_phone = NULL
                    WHERE o.id = me.organization_id;
               END IF;
           END IF;
           {
        _audit(
            "ADMIN_ACCOUNT_DELETED",
            "jsonb_build_object('organization_code', v_code, 'campus_deleted', v_campus_alone, "
            "'accounts_deleted', cardinality(v_users), 'organization_deactivated', v_last_campus)",
        )
    };
           RETURN QUERY SELECT 'DELETED'::text, NULL::integer, v_campus_alone, v_last_campus;
       END $$""",
]
_FUNCTION_SIGNATURES = [
    "app_admin_deletion_overview()",
    "app_ownership_candidates()",
    "app_ownership_transfer_start(uuid, text, integer)",
    "app_ownership_transfer_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_ownership_transfer_verify_otp(uuid, text, integer)",
    "app_ownership_transfer_cancel(uuid)",
    "app_ownership_transfers_incoming()",
    "app_ownership_transfer_decide(uuid, boolean)",
    "app_admin_deletion_start(uuid, integer)",
    "app_admin_deletion_issue_otp(uuid, uuid, text, integer, integer, integer, integer)",
    "app_admin_deletion_complete(uuid, text)",
]
_NEW_TABLES = ["ownership_transfers", "admin_account_deletions"]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _HELPERS + _FUNCTIONS:
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
    for signature in reversed(_FUNCTION_SIGNATURES + _HELPER_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS admin_account_deletions, ownership_transfers")
    op.execute("DELETE FROM auth_otp_challenges WHERE purpose IN ('OWNERSHIP_TRANSFER', 'ACCOUNT_DELETION')")
    op.execute("ALTER TABLE auth_otp_challenges DROP CONSTRAINT ck_auth_otp_purpose")
    op.execute(
        f"ALTER TABLE auth_otp_challenges ADD CONSTRAINT ck_auth_otp_purpose CHECK (purpose IN {_PURPOSES_0009})"
    )
