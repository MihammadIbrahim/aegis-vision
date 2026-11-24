"""Complete campus erasure on Admin deletion, optional export, and deleted organization names.

Replaces the 0010 deletion so nothing of a deleted campus stays in the operational database:

* **Campus deletion** (no other active Admin remains on the campus): every user of the campus
  and their credentials and requests, one-time codes, pending creations, cameras, the campus
  registration request that created the campus, the campus row itself, and links from
  organization records to the deleted users.
* **Last campus:** the organization is deleted too (not marked INACTIVE as in 0010), with its
  registration records, campus requests, ownership transfers and the platform audit entries
  that name its Organization ID. Only its name is written to ``deleted_organization_names``,
  which the Super Admin can read and nothing else can.
* **Shared campus:** only the deleting Admin's own account and requests are deleted.
* **Organization contacts:** the official email and phone, copied from the first Admin at
  registration, are cleared when they belong to a deleted user.
* **Ownership history:** a completed (ACCEPTED) transfer stays when one party is deleted. The
  deleted party's link becomes NULL. Unfinished requests of deleted users are deleted.
* ``app_admin_deletion_export``: the caller's own campus records for an open deletion request,
  without credentials, hashes, codes or internal keys. It is built in the database and returned
  directly; nothing is stored.

No table holds recordings, alerts, incidents, reports, notifications, buildings, floors or
training data yet. Campus media files are handled by the API (``ICampusMediaStorage``).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role(variable: str, default: str) -> str:
    role = os.environ.get(variable, default)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid {variable} role name: {role!r}")
    return role


# Same as 0010: the signed-in caller as an active Admin of an active organization and campus.
_ACTOR_ADMIN = """(SELECT u.id, u.organization_id, u.campus_id, u.is_first_admin, u.email
      FROM users u
      JOIN organizations o ON o.id = u.organization_id
      JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
     WHERE u.id = app_current_user_id() AND u.role = 'ADMIN'
       AND u.status = 'ACTIVE' AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""

# No other active Admin on the caller's campus: deleting the caller deletes the campus.
_CAMPUS_ALONE = """NOT EXISTS (
               SELECT 1 FROM users u WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id
                  AND u.id <> me.id AND u.role = 'ADMIN' AND u.status = 'ACTIVE')"""
_NO_OTHER_CAMPUS = """NOT EXISTS (SELECT 1 FROM campuses ca
                       WHERE ca.organization_id = me.organization_id AND ca.id <> me.campus_id)"""

_SCHEMA = [
    # Keep finished ownership history when a party is deleted.
    "ALTER TABLE ownership_transfers DROP CONSTRAINT ownership_transfers_from_user_id_fkey",
    "ALTER TABLE ownership_transfers DROP CONSTRAINT ownership_transfers_to_user_id_fkey",
    "ALTER TABLE ownership_transfers ALTER COLUMN from_user_id DROP NOT NULL",
    "ALTER TABLE ownership_transfers ALTER COLUMN to_user_id DROP NOT NULL",
    """ALTER TABLE ownership_transfers ADD CONSTRAINT ownership_transfers_from_user_id_fkey
       FOREIGN KEY (from_user_id) REFERENCES users (id) ON DELETE SET NULL""",
    """ALTER TABLE ownership_transfers ADD CONSTRAINT ownership_transfers_to_user_id_fkey
       FOREIGN KEY (to_user_id) REFERENCES users (id) ON DELETE SET NULL""",
    # Only a completed transfer may outlive a party.
    """ALTER TABLE ownership_transfers ADD CONSTRAINT ck_ownership_transfers_open_has_parties
       CHECK (status = 'ACCEPTED' OR (from_user_id IS NOT NULL AND to_user_id IS NOT NULL))""",
    # The name of a deleted organization, and nothing else.
    """CREATE TABLE deleted_organization_names (
           id                 uuid PRIMARY KEY,
           organization_name  text NOT NULL CONSTRAINT ck_deleted_org_name_length
                                  CHECK (length(organization_name) BETWEEN 1 AND 200)
       )""",
]

_FUNCTIONS = [
    f"""CREATE FUNCTION app_admin_deletion_overview()
       RETURNS TABLE (is_main_admin boolean, blocker text, campus_will_be_deleted boolean,
                      organization_will_be_deleted boolean, transfer_id uuid, transfer_status text,
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
           v_campus_alone := {_CAMPUS_ALONE};
           SELECT tr.id, tr.status, u.full_name, tr.expires_at INTO t
             FROM ownership_transfers tr JOIN users u ON u.id = tr.to_user_id
            WHERE tr.from_user_id = me.id ORDER BY tr.created_at DESC LIMIT 1;
           RETURN QUERY SELECT me.is_first_admin, app_admin_deletion_blocker(me.id), v_campus_alone,
               v_campus_alone AND {_NO_OTHER_CAMPUS},
               t.id, t.status, t.full_name, t.expires_at;
       END $$""",
    # The caller's own campus, for an open deletion request. Built here and returned; not stored.
    f"""CREATE FUNCTION app_admin_deletion_export(p_request_id uuid) RETURNS jsonb
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           d admin_account_deletions%ROWTYPE;
       BEGIN
           SELECT * INTO me FROM {_ACTOR_ADMIN} x;
           SELECT * INTO d FROM admin_account_deletions ad WHERE ad.id = p_request_id AND ad.user_id = me.id;
           IF me.id IS NULL OR NOT FOUND OR d.status <> 'AWAITING_VERIFICATION' THEN
               RETURN jsonb_build_object('outcome', 'NOT_FOUND');
           END IF;
           IF d.expires_at <= now() THEN
               RETURN jsonb_build_object('outcome', 'EXPIRED');
           END IF;
           IF app_admin_deletion_blocker(me.id) IS NOT NULL THEN
               RETURN jsonb_build_object('outcome', app_admin_deletion_blocker(me.id));
           END IF;
           RETURN jsonb_build_object(
               'outcome', 'EXPORTED',
               'organization', (SELECT jsonb_build_object('name', o.name)
                                  FROM organizations o WHERE o.id = me.organization_id),
               'campus', (SELECT jsonb_build_object('name', ca.name, 'status', ca.status, 'created_at', ca.created_at)
                            FROM campuses ca WHERE ca.organization_id = me.organization_id AND ca.id = me.campus_id),
               'users', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'user_id', u.display_id, 'role', u.role, 'status', u.status, 'full_name', u.full_name,
                       'username', u.username, 'email', u.email, 'phone', u.phone,
                       'is_main_admin', u.is_first_admin, 'created_at', u.created_at)
                       ORDER BY u.role, u.created_at)
                     FROM users u WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id),
                   '[]'::jsonb),
               'cameras', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'display_name', c.display_name, 'created_at', c.created_at) ORDER BY c.created_at)
                     FROM cameras c WHERE c.organization_id = me.organization_id AND c.campus_id = me.campus_id),
                   '[]'::jsonb),
               'pending_account_creations', COALESCE((SELECT jsonb_agg(p ORDER BY p->>'created_at') FROM (
                       SELECT jsonb_build_object('role', 'SUPERVISOR', 'status', s.status, 'full_name', s.full_name,
                              'username', s.username, 'email', s.email, 'phone', s.phone,
                              'created_at', s.created_at, 'expires_at', s.expires_at) AS p
                         FROM supervisor_creations s
                        WHERE s.organization_id = me.organization_id AND s.campus_id = me.campus_id
                          AND s.status = 'PENDING'
                       UNION ALL
                       SELECT jsonb_build_object('role', 'GUARD', 'status', g.status, 'full_name', g.full_name,
                              'username', g.username, 'email', g.email, 'phone', g.phone,
                              'created_at', g.created_at, 'expires_at', g.expires_at)
                         FROM guard_creations g
                        WHERE g.organization_id = me.organization_id AND g.campus_id = me.campus_id
                          AND g.status = 'PENDING') q),
                   '[]'::jsonb));
       END $$""",
    # Check the code and the ownership rules, then erase, all in the caller's transaction.
    f"""CREATE FUNCTION app_admin_deletion_complete(p_request_id uuid, p_code_hash text)
       RETURNS TABLE (outcome text, attempts_remaining integer, campus_deleted boolean,
                      organization_deleted boolean)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           d admin_account_deletions%ROWTYPE;
           r record;
           v_blocker text;
           v_code text;
           v_name text;
           v_campus_alone boolean;
           v_last_campus boolean := false;
           v_users uuid[];
           v_subjects uuid[];
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

           SELECT o.organization_code INTO v_code FROM organizations o WHERE o.id = me.organization_id FOR UPDATE;
           v_campus_alone := {_CAMPUS_ALONE};
           v_last_campus := v_campus_alone AND {_NO_OTHER_CAMPUS};
           IF v_last_campus THEN
               SELECT array_agg(u.id) INTO v_users FROM users u WHERE u.organization_id = me.organization_id;
           ELSIF v_campus_alone THEN
               SELECT array_agg(u.id) INTO v_users FROM users u
                WHERE u.organization_id = me.organization_id AND u.campus_id = me.campus_id;
           ELSE
               v_users := ARRAY[me.id];
           END IF;

           -- One-time codes have no foreign keys: remove those of these users' requests.
           SELECT array_agg(s) INTO v_subjects FROM (
               SELECT id FROM admin_step_up_grants WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM guard_step_up_grants WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM supervisor_profile_changes WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM guard_profile_changes WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM password_reset_requests WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM ownership_transfers
                          WHERE from_user_id = ANY (v_users) OR to_user_id = ANY (v_users)
               UNION ALL SELECT id FROM admin_account_deletions WHERE user_id = ANY (v_users)
               UNION ALL SELECT id FROM supervisor_creations
                          WHERE created_by = ANY (v_users) OR supervisor_user_id = ANY (v_users)
                             OR (v_campus_alone AND organization_id = me.organization_id AND campus_id = me.campus_id)
               UNION ALL SELECT id FROM guard_creations
                          WHERE created_by = ANY (v_users) OR guard_user_id = ANY (v_users)
                             OR (v_campus_alone AND organization_id = me.organization_id AND campus_id = me.campus_id)
           ) q(s);
           DELETE FROM auth_otp_challenges ch
            WHERE ch.subject_id = ANY (v_subjects)
               OR split_part(ch.limit_key, ':', 2) IN (SELECT x::text FROM unnest(v_users) x);

           -- Ownership history: keep a completed transfer while one party remains; drop the rest.
           DELETE FROM ownership_transfers tr
            WHERE tr.organization_id = me.organization_id
              AND (tr.from_user_id = ANY (v_users) OR tr.to_user_id = ANY (v_users))
              AND (tr.status <> 'ACCEPTED'
                   OR (COALESCE(tr.from_user_id = ANY (v_users), true)
                       AND COALESCE(tr.to_user_id = ANY (v_users), true)));

           UPDATE campus_registration_requests SET reviewed_by = NULL
            WHERE organization_id = me.organization_id AND reviewed_by = ANY (v_users);
           -- The organization's contact details were copied from its first Admin: drop them if
           -- they belong to a deleted user.
           UPDATE organizations o
              SET official_email = CASE WHEN lower(o.official_email) IN (
                                       SELECT lower(u.email) FROM users u WHERE u.id = ANY (v_users))
                                   THEN NULL ELSE o.official_email END,
                  official_phone = CASE WHEN o.official_phone IN (
                                       SELECT u.phone FROM users u WHERE u.id = ANY (v_users) AND u.phone IS NOT NULL)
                                   THEN NULL ELSE o.official_phone END
            WHERE o.id = me.organization_id;
           IF v_campus_alone THEN
               DELETE FROM campus_registration_requests
                WHERE organization_id = me.organization_id AND approved_campus_id = me.campus_id;
               DELETE FROM supervisor_creations WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM guard_creations WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM cameras WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
           END IF;
           -- Credentials, sign-ins, grants, profile changes, resets and deletion requests go with
           -- their users (ON DELETE CASCADE on user_id columns).
           DELETE FROM users u WHERE u.organization_id = me.organization_id AND u.id = ANY (v_users);

           IF v_last_campus THEN
               DELETE FROM ownership_transfers WHERE organization_id = me.organization_id;
               DELETE FROM campus_registration_requests WHERE organization_id = me.organization_id;
               DELETE FROM organization_registrations WHERE organization_id = me.organization_id;  -- codes cascade
               DELETE FROM cameras WHERE organization_id = me.organization_id;
               DELETE FROM supervisor_creations WHERE organization_id = me.organization_id;
               DELETE FROM guard_creations WHERE organization_id = me.organization_id;
               DELETE FROM campuses WHERE organization_id = me.organization_id;
               DELETE FROM platform_audit_logs WHERE metadata ->> 'organization_code' = v_code;
               DELETE FROM organizations o WHERE o.id = me.organization_id RETURNING o.name INTO v_name;
               -- Exactly one entry: the organization row was locked above and is now gone.
               INSERT INTO deleted_organization_names (id, organization_name) VALUES (gen_random_uuid(), v_name);
               INSERT INTO platform_audit_logs (id, occurred_at, action, outcome, metadata)
               VALUES (gen_random_uuid(), now(), 'ADMIN_ACCOUNT_DELETED', 'SUCCESS',
                       jsonb_build_object('campus_deleted', true, 'organization_deleted', true));
           ELSE
               IF v_campus_alone THEN
                   DELETE FROM campuses ca WHERE ca.organization_id = me.organization_id AND ca.id = me.campus_id;
               END IF;
               INSERT INTO platform_audit_logs (id, occurred_at, action, outcome, metadata)
               VALUES (gen_random_uuid(), now(), 'ADMIN_ACCOUNT_DELETED', 'SUCCESS',
                       jsonb_build_object('organization_code', v_code, 'campus_deleted', v_campus_alone,
                                          'accounts_deleted', cardinality(v_users), 'organization_deleted', false));
           END IF;
           RETURN QUERY SELECT 'DELETED'::text, NULL::integer, v_campus_alone, v_last_campus;
       END $$""",
]
_REPLACED = ["app_admin_deletion_overview()", "app_admin_deletion_complete(uuid, text)"]
_FUNCTION_SIGNATURES = [*_REPLACED, "app_admin_deletion_export(uuid)"]


def upgrade() -> None:
    app = _role("APP_DB_USER", "cctv_app")
    platform = _role("PLATFORM_DB_USER", "cctv_platform")
    for signature in _REPLACED:
        op.execute(f"DROP FUNCTION {signature}")
    for statement in _SCHEMA + _FUNCTIONS:
        op.execute(statement)
    op.execute("REVOKE ALL ON deleted_organization_names FROM PUBLIC")
    op.execute(f"GRANT SELECT ON deleted_organization_names TO {platform}")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    """Back to 0010's functions. Deleted organizations and campuses are not restored."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("0010_ownership_transfer_and_account_deletion.py")
    spec = importlib.util.spec_from_file_location("_revision_0010", path)
    assert spec is not None and spec.loader is not None
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    app = _role("APP_DB_USER", "cctv_app")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS deleted_organization_names")
    op.execute("DELETE FROM ownership_transfers WHERE from_user_id IS NULL OR to_user_id IS NULL")
    op.execute("ALTER TABLE ownership_transfers DROP CONSTRAINT ck_ownership_transfers_open_has_parties")
    for column in ("from_user_id", "to_user_id"):
        op.execute(f"ALTER TABLE ownership_transfers DROP CONSTRAINT ownership_transfers_{column}_fkey")
        op.execute(f"ALTER TABLE ownership_transfers ALTER COLUMN {column} SET NOT NULL")
        op.execute(
            f"""ALTER TABLE ownership_transfers ADD CONSTRAINT ownership_transfers_{column}_fkey
                FOREIGN KEY ({column}) REFERENCES users (id) ON DELETE CASCADE"""
        )
    for statement in previous._FUNCTIONS:
        if "app_admin_deletion_overview()" in statement or "app_admin_deletion_complete(" in statement[:80]:
            op.execute(statement)
    for signature in _REPLACED:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
