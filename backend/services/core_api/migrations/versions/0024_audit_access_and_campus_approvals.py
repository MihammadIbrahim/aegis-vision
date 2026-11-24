"""Audit-log viewing, sign-in audit events, and campus-request decisions with confirmation
(requirements Sections 8 and 18; Org Admin pages 15 and 21, Supervisor page 17; decisions H and I; D-024).

Audit logs (``organization_audit_logs``, insert-only since migration 0015)
-------------------------------------------------------------------------
* ``outcome`` (SUCCESS / FAILURE / DENIED, default SUCCESS) and ``category``, computed from the action
  and the actor's role by ``app_audit_category`` (a stored generated column, so filters use an index).
* Read access for the first time, still no UPDATE or DELETE for anyone (records are immutable):
  - Admins: every record of their own campus;
  - Supervisors: operational records of their campus (alerts, Guard responses, cameras and locations,
    AI settings, evidence, video, person search, manual reports, incidents and incident chat) and their
    own records — never Admin-only administrative records (user management, settings, sign-ins of others).
  - The platform (Super Admin) role still has no access.
* ``app_org_audit_auth_event``: sign-in and password-reset events of organization users, written by the
  API before any user is signed in (no secrets: never a password, code or token);
  ``app_org_audit_reset_completed`` records a completed password reset for the account it changed.

Campus requests
---------------
* ``app_step_up_consume``: uses up one verified step-up grant of the caller (password + emailed code,
  migration 0007). Approval of a campus request needs one (decision H).
* Decision I: a rejected request keeps no applicant details. The name, email, username, phone and
  proposed campus name become NULL when it is rejected; ``details_erased_at`` records when.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-18
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"

# Categories Supervisors may read (Supervisor page 17 item 4, plus incidents and video).
OPERATIONAL_CATEGORIES = (
    "ALERTS",
    "GUARD_RESPONSE",
    "CAMERA_LOCATION",
    "AI_SETTINGS",
    "EVIDENCE",
    "VIDEO",
    "PERSON_SEARCH",
    "MANUAL_REPORTS",
    "INCIDENTS",
    "INCIDENT_CHAT",
)
_OPERATIONAL = ", ".join(f"'{c}'" for c in OPERATIONAL_CATEGORIES)

_CATEGORY_FUNCTION = """CREATE FUNCTION app_audit_category(p_action text, p_actor_role text) RETURNS text
       LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
           SELECT CASE
               WHEN p_action IN ('ALERT_MESSAGE_POSTED', 'INCIDENT_MESSAGE_POSTED') THEN 'INCIDENT_CHAT'
               WHEN p_actor_role = 'GUARD' AND p_action LIKE 'ALERT\\_%' THEN 'GUARD_RESPONSE'
               WHEN p_action LIKE 'ALERT\\_%' THEN 'ALERTS'
               WHEN p_action LIKE 'INCIDENT\\_%' THEN 'INCIDENTS'
               WHEN p_action LIKE 'EVIDENCE\\_%' THEN 'EVIDENCE'
               WHEN p_action IN ('RECORDING_RETENTION_CHANGED') OR p_action LIKE 'STORAGE\\_%' THEN 'RETENTION'
               WHEN p_action LIKE 'LIVE\\_%' OR p_action LIKE 'RECORDING\\_%' THEN 'VIDEO'
               WHEN p_action LIKE 'AI\\_%' OR p_action LIKE 'CAMERA\\_AI\\_%' THEN 'AI_SETTINGS'
               WHEN p_action LIKE 'CAMERA\\_%' OR p_action LIKE 'LOCATION\\_%' OR p_action LIKE 'BUILDING\\_%'
                    OR p_action LIKE 'FLOOR\\_%' OR p_action LIKE 'AREA\\_%' AND p_action NOT LIKE 'AREA\\_ACCESS%'
                    THEN 'CAMERA_LOCATION'
               WHEN p_action LIKE 'PERSON\\_SEARCH\\_%' OR p_action LIKE 'REID\\_%' THEN 'PERSON_SEARCH'
               WHEN p_action LIKE 'MANUAL\\_REPORT\\_%' THEN 'MANUAL_REPORTS'
               WHEN p_action LIKE 'PERSONNEL\\_%' OR p_action LIKE 'RESTRICTED\\_%' OR p_action LIKE 'AREA\\_ACCESS%'
                    OR p_action LIKE 'ACCESS\\_%' THEN 'PERSONNEL'
               WHEN p_action LIKE 'AUTH\\_%' OR p_action LIKE 'LOGIN\\_%' OR p_action LIKE 'PASSWORD\\_RESET%'
                    THEN 'AUTHENTICATION'
               WHEN p_action LIKE 'STAFF\\_%' OR p_action LIKE 'GUARD\\_%' OR p_action LIKE 'SUPERVISOR\\_%'
                    OR p_action LIKE 'USER\\_%' OR p_action LIKE 'PROFILE\\_%' OR p_action LIKE 'ACCOUNT\\_%'
                    OR p_action LIKE 'OWNERSHIP\\_%' THEN 'USER_MANAGEMENT'
               WHEN p_action LIKE 'ORGANIZATION\\_%' OR p_action LIKE 'CAMPUS\\_%' OR p_action LIKE 'SETTINGS\\_%'
                    OR p_action LIKE 'AUDIT\\_%' OR p_action LIKE 'REPORT\\_%' THEN 'ORGANIZATION'
               ELSE 'OTHER'
           END
       $$"""

_SCHEMA = [
    _CATEGORY_FUNCTION,
    """ALTER TABLE organization_audit_logs ADD COLUMN outcome text NOT NULL DEFAULT 'SUCCESS'
       CONSTRAINT ck_org_audit_outcome CHECK (outcome IN ('SUCCESS', 'FAILURE', 'DENIED'))""",
    """ALTER TABLE organization_audit_logs ADD COLUMN category text
       GENERATED ALWAYS AS (app_audit_category(action, actor_role)) STORED""",
    """CREATE INDEX ix_org_audit_campus_time ON organization_audit_logs
       (organization_id, campus_id, occurred_at DESC, id)""",
    """CREATE INDEX ix_org_audit_campus_category ON organization_audit_logs
       (organization_id, campus_id, category, occurred_at DESC)""",
    "CREATE INDEX ix_org_audit_actor ON organization_audit_logs (actor_user_id, occurred_at DESC)",
    # Decision I: no applicant details after a rejection.
    "ALTER TABLE campus_registration_requests ALTER COLUMN proposed_campus_name DROP NOT NULL",
    "ALTER TABLE campus_registration_requests ALTER COLUMN applicant_name DROP NOT NULL",
    "ALTER TABLE campus_registration_requests ALTER COLUMN applicant_email DROP NOT NULL",
    "ALTER TABLE campus_registration_requests ADD COLUMN details_erased_at timestamptz",
    """ALTER TABLE campus_registration_requests ADD CONSTRAINT ck_campus_requests_details CHECK (
           CASE WHEN details_erased_at IS NOT NULL
                THEN status = 'REJECTED' AND num_nonnulls(proposed_campus_name, applicant_name, applicant_email,
                                                          applicant_username, applicant_phone) = 0
                ELSE num_nonnulls(proposed_campus_name, applicant_name, applicant_email) = 3
           END) NOT VALID""",
    "ALTER TABLE campus_registration_requests VALIDATE CONSTRAINT ck_campus_requests_details",
    """CREATE INDEX ix_campus_requests_org_submitted ON campus_registration_requests
       (organization_id, submitted_at DESC)""",
]

_POLICIES = [
    f"""CREATE POLICY org_audit_select_admin ON organization_audit_logs FOR SELECT
        USING ({_CAMPUS} AND app_actor_role() = 'ADMIN')""",
    f"""CREATE POLICY org_audit_select_supervisor ON organization_audit_logs FOR SELECT
        USING ({_CAMPUS} AND app_actor_role() = 'SUPERVISOR'
               AND (category IN ({_OPERATIONAL}) OR actor_user_id = app_current_user_id()))""",
]

_FUNCTIONS = [
    # Sign-in and reset events, written before anyone is signed in. Only AUTH_ actions, only for an
    # existing organization user; the organization, campus and role come from the account itself.
    """CREATE FUNCTION app_org_audit_auth_event(p_user_id uuid, p_action text, p_outcome text, p_metadata jsonb)
       RETURNS void
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF p_action !~ '^AUTH_[A-Z_]{2,58}$' OR p_outcome NOT IN ('SUCCESS', 'FAILURE', 'DENIED') THEN
               RAISE EXCEPTION 'invalid authentication audit event' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                target_type, target_id, metadata, outcome)
           SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id, u.role, p_action, 'USER', u.id,
                  COALESCE(p_metadata, '{}'::jsonb), p_outcome
             FROM users u WHERE u.id = p_user_id;
       END $$""",
    # A completed password reset, recorded for the account it changed (its user is known only here).
    """CREATE FUNCTION app_org_audit_reset_completed(p_request_id uuid) RETURNS void
       LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                target_type, target_id, metadata, outcome)
           SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id, u.role, 'AUTH_PASSWORD_RESET_COMPLETED',
                  'USER', u.id, '{}'::jsonb, 'SUCCESS'
             FROM password_reset_requests r JOIN users u ON u.id = r.user_id
            WHERE r.id = p_request_id AND r.completed_at IS NOT NULL
              AND r.completed_at > now() - interval '5 minutes'
       $$""",
    # One verified, unused, unexpired step-up grant of the caller is used up (decision H).
    """CREATE FUNCTION app_step_up_consume(p_grant_id uuid) RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           g admin_step_up_grants%ROWTYPE;
       BEGIN
           SELECT * INTO g FROM admin_step_up_grants sg
            WHERE sg.id = p_grant_id AND sg.user_id = app_current_user_id() FOR UPDATE;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF g.used_at IS NOT NULL THEN
               RETURN 'ALREADY_USED';
           END IF;
           IF g.verified_at IS NULL THEN
               RETURN 'NOT_VERIFIED';
           END IF;
           IF g.expires_at <= now() THEN
               RETURN 'EXPIRED';
           END IF;
           UPDATE admin_step_up_grants sg SET used_at = now() WHERE sg.id = g.id;
           RETURN 'CONSUMED';
       END $$""",
]

_FUNCTION_SIGNATURES = [
    "app_org_audit_auth_event(uuid, text, text, jsonb)",
    "app_org_audit_reset_completed(uuid)",
    "app_step_up_consume(uuid)",
]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA:
        op.execute(statement)
    for statement in _POLICIES:
        op.execute(statement)
    op.execute(f"GRANT SELECT ON organization_audit_logs TO {app}")
    for statement in _FUNCTIONS:
        op.execute(statement)
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    app = _app_role()
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(f"REVOKE SELECT ON organization_audit_logs FROM {app}")
    op.execute("DROP POLICY IF EXISTS org_audit_select_supervisor ON organization_audit_logs")
    op.execute("DROP POLICY IF EXISTS org_audit_select_admin ON organization_audit_logs")
    op.execute("DROP INDEX IF EXISTS ix_campus_requests_org_submitted")
    op.execute("ALTER TABLE campus_registration_requests DROP CONSTRAINT IF EXISTS ck_campus_requests_details")
    # Rejected requests whose details were erased cannot regain them; they are removed.
    op.execute("DELETE FROM campus_registration_requests WHERE details_erased_at IS NOT NULL")
    op.execute("ALTER TABLE campus_registration_requests DROP COLUMN details_erased_at")
    for column in ("proposed_campus_name", "applicant_name", "applicant_email"):
        op.execute(f"ALTER TABLE campus_registration_requests ALTER COLUMN {column} SET NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_org_audit_actor")
    op.execute("DROP INDEX IF EXISTS ix_org_audit_campus_category")
    op.execute("DROP INDEX IF EXISTS ix_org_audit_campus_time")
    op.execute("ALTER TABLE organization_audit_logs DROP COLUMN category")
    op.execute("ALTER TABLE organization_audit_logs DROP COLUMN outcome")
    op.execute("DROP FUNCTION IF EXISTS app_audit_category(text, text)")
