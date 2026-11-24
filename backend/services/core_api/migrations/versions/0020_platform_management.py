"""Platform Super Admin management: overview, organizations, Main Admin contact, organization status,
platform AI settings (decision D-019).

The platform role (``PLATFORM_DB_USER``) still has **no** rights on any tenant table. Everything it may
know about organizations comes through these narrow ``SECURITY DEFINER`` functions, granted to the
platform role only (never to the app role):

* ``platform_overview()``: counts only (organizations by status, campuses, registered cameras, active
  staff accounts by role).
* ``platform_organization_list(q, status, sort, descending, limit, offset)``: organization metadata, the
  Main Admin's contact details, campus and camera counts, and the total for paging.
* ``platform_organization_detail(code)`` and ``platform_organization_campuses(code)``: one organization
  and per-campus counts (buildings, perimeters, floors, areas, cameras, staff).
* ``platform_organization_names(codes)``: names for audit-log display.
* ``platform_update_main_admin(code, full_name, email, phone)``: the Main Admin's name, email and phone.
  Never reads or returns a password.
* ``platform_set_organization_status(code, status)``: ACTIVE, INACTIVE or SUSPENDED. Tenant sign-in
  already requires ACTIVE (0006); the API now also refuses every tenant request of a non-active
  organization.

No function returns camera stream addresses, recordings, alerts, chat messages, reports or personnel.

Trigger ``trg_platform_audit_organization_registered``: every new organization writes an
``ORGANIZATION_REGISTERED`` row (code and name) to ``platform_audit_logs``.

``platform_ai_settings`` (one row): platform-wide AI module availability and default parameters. It is
platform configuration, not tenant data, so the platform role reads and updates it directly.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-12
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_role() -> str:
    role = os.environ.get("PLATFORM_DB_USER", "cctv_platform")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid PLATFORM_DB_USER role name: {role!r}")
    return role


_HEAD = "SECURITY DEFINER SET search_path = pg_catalog, public"
_ORG_COLUMNS = """organization_code text, name text, official_email text, official_phone text, status text,
                  created_at timestamptz, main_admin_full_name text, main_admin_username text,
                  main_admin_email text, main_admin_phone text, campus_count bigint, camera_count bigint"""
_ORG_SELECT = """SELECT o.organization_code, o.name, o.official_email, o.official_phone, o.status, o.created_at,
                        a.full_name AS admin_full_name, a.username AS admin_username, a.email AS admin_email,
                        a.phone AS admin_phone,
                        (SELECT count(*) FROM campuses c WHERE c.organization_id = o.id) AS n_campuses,
                        (SELECT count(*) FROM cameras k WHERE k.organization_id = o.id) AS n_cameras
                   FROM organizations o
                   LEFT JOIN users a ON a.organization_id = o.id AND a.is_first_admin"""

_FUNCTIONS = [
    f"""CREATE FUNCTION platform_overview()
       RETURNS TABLE (organizations_total bigint, organizations_active bigint, organizations_inactive bigint,
                      organizations_suspended bigint, campuses_total bigint, campuses_active bigint,
                      cameras_total bigint, admins_active bigint, supervisors_active bigint, guards_active bigint)
       LANGUAGE sql STABLE {_HEAD} AS $$
           SELECT (SELECT count(*) FROM organizations),
                  (SELECT count(*) FROM organizations WHERE status = 'ACTIVE'),
                  (SELECT count(*) FROM organizations WHERE status = 'INACTIVE'),
                  (SELECT count(*) FROM organizations WHERE status = 'SUSPENDED'),
                  (SELECT count(*) FROM campuses),
                  (SELECT count(*) FROM campuses WHERE status = 'ACTIVE'),
                  (SELECT count(*) FROM cameras),
                  (SELECT count(*) FROM users WHERE role = 'ADMIN' AND status = 'ACTIVE'),
                  (SELECT count(*) FROM users WHERE role = 'SUPERVISOR' AND status = 'ACTIVE'),
                  (SELECT count(*) FROM users WHERE role = 'GUARD' AND status = 'ACTIVE')
       $$""",
    f"""CREATE FUNCTION platform_organization_list(
           p_q text, p_status text, p_sort text, p_descending boolean, p_limit integer, p_offset integer)
       RETURNS TABLE ({_ORG_COLUMNS}, total_count bigint)
       LANGUAGE plpgsql STABLE {_HEAD} AS $$
       #variable_conflict use_column
       BEGIN
           IF p_limit NOT BETWEEN 1 AND 10000 OR p_offset < 0 THEN
               RAISE EXCEPTION 'page out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           IF p_sort NOT IN ('name', 'created_at', 'camera_count') THEN
               RAISE EXCEPTION 'unknown sort' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           RETURN QUERY
           WITH base AS ({_ORG_SELECT}
                 WHERE (p_status IS NULL OR o.status = p_status)
                   AND (p_q IS NULL
                        OR strpos(lower(o.name), lower(p_q)) > 0
                        OR strpos(lower(coalesce(o.official_email, '')), lower(p_q)) > 0
                        OR strpos(lower(o.organization_code), lower(p_q)) > 0))
           SELECT b.*, count(*) OVER () FROM base b
            ORDER BY
               CASE WHEN p_sort = 'name' AND NOT p_descending THEN lower(b.name) END ASC,
               CASE WHEN p_sort = 'name' AND p_descending THEN lower(b.name) END DESC,
               CASE WHEN p_sort = 'created_at' AND NOT p_descending THEN b.created_at END ASC,
               CASE WHEN p_sort = 'created_at' AND p_descending THEN b.created_at END DESC,
               CASE WHEN p_sort = 'camera_count' AND NOT p_descending THEN b.n_cameras END ASC,
               CASE WHEN p_sort = 'camera_count' AND p_descending THEN b.n_cameras END DESC,
               b.organization_code
            LIMIT p_limit OFFSET p_offset;
       END $$""",
    f"""CREATE FUNCTION platform_organization_detail(p_code text)
       RETURNS TABLE ({_ORG_COLUMNS})
       LANGUAGE sql STABLE {_HEAD} AS $$
           {_ORG_SELECT} WHERE o.organization_code = p_code
       $$""",
    f"""CREATE FUNCTION platform_organization_campuses(p_code text)
       RETURNS TABLE (campus_name text, status text, created_at timestamptz, building_count bigint,
                      perimeter_count bigint, floor_count bigint, area_count bigint, camera_count bigint,
                      admin_count bigint, supervisor_count bigint, guard_count bigint)
       LANGUAGE sql STABLE {_HEAD} AS $$
           SELECT c.name, c.status, c.created_at,
                  (SELECT count(*) FROM buildings b WHERE b.campus_id = c.id AND b.kind = 'INDOOR'),
                  (SELECT count(*) FROM buildings b WHERE b.campus_id = c.id AND b.kind = 'OUTDOOR_PERIMETER'),
                  (SELECT count(*) FROM floors f JOIN buildings b ON b.id = f.building_id WHERE b.campus_id = c.id),
                  (SELECT count(*) FROM areas ar JOIN buildings b ON b.id = ar.building_id WHERE b.campus_id = c.id),
                  (SELECT count(*) FROM cameras k WHERE k.campus_id = c.id),
                  (SELECT count(*) FROM users u WHERE u.campus_id = c.id AND u.role = 'ADMIN' AND u.status = 'ACTIVE'),
                  (SELECT count(*) FROM users u WHERE u.campus_id = c.id AND u.role = 'SUPERVISOR'
                     AND u.status = 'ACTIVE'),
                  (SELECT count(*) FROM users u WHERE u.campus_id = c.id AND u.role = 'GUARD' AND u.status = 'ACTIVE')
             FROM campuses c JOIN organizations o ON o.id = c.organization_id
            WHERE o.organization_code = p_code
            ORDER BY c.created_at, c.name
       $$""",
    f"""CREATE FUNCTION platform_organization_names(p_codes text[])
       RETURNS TABLE (organization_code text, name text)
       LANGUAGE sql STABLE {_HEAD} AS $$
           SELECT o.organization_code, o.name FROM organizations o WHERE o.organization_code = ANY (p_codes)
       $$""",
    f"""CREATE FUNCTION platform_update_main_admin(p_code text, p_full_name text, p_email text, p_phone text)
       RETURNS text
       LANGUAGE plpgsql {_HEAD} AS $$
       DECLARE
           v_user uuid;
       BEGIN
           SELECT u.id INTO v_user FROM users u JOIN organizations o ON o.id = u.organization_id
            WHERE o.organization_code = p_code AND u.is_first_admin FOR UPDATE OF u;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           IF EXISTS (SELECT 1 FROM users x WHERE lower(x.email) = lower(p_email) AND x.id <> v_user) THEN
               RETURN 'EMAIL_TAKEN';
           END IF;
           BEGIN
               UPDATE users u SET full_name = p_full_name, email = p_email, phone = p_phone WHERE u.id = v_user;
           EXCEPTION WHEN unique_violation THEN
               RETURN 'EMAIL_TAKEN';
           END;
           RETURN 'UPDATED';
       END $$""",
    f"""CREATE FUNCTION platform_set_organization_status(p_code text, p_status text)
       RETURNS TABLE (outcome text, previous_status text, organization_name text)
       LANGUAGE plpgsql {_HEAD} AS $$
       DECLARE
           v_status text;
           v_name text;
       BEGIN
           IF p_status NOT IN ('ACTIVE', 'INACTIVE', 'SUSPENDED') THEN
               RAISE EXCEPTION 'unknown status' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT o.status, o.name INTO v_status, v_name FROM organizations o
            WHERE o.organization_code = p_code FOR UPDATE;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::text, NULL::text;
               RETURN;
           END IF;
           IF v_status = p_status THEN
               RETURN QUERY SELECT 'UNCHANGED'::text, v_status, v_name;
               RETURN;
           END IF;
           UPDATE organizations o SET status = p_status WHERE o.organization_code = p_code;
           RETURN QUERY SELECT 'UPDATED'::text, v_status, v_name;
       END $$""",
]
_REGISTERED_AUDIT = [
    # Every new organization appears in the platform audit log (requirements: "organization onboarded").
    f"""CREATE FUNCTION platform_audit_organization_registered() RETURNS trigger
       LANGUAGE plpgsql {_HEAD} AS $$
       BEGIN
           INSERT INTO platform_audit_logs (id, occurred_at, action, outcome, metadata)
           VALUES (gen_random_uuid(), now(), 'ORGANIZATION_REGISTERED', 'SUCCESS',
                   jsonb_build_object('organization_code', NEW.organization_code, 'organization_name', NEW.name));
           RETURN NULL;
       END $$""",
    """CREATE TRIGGER trg_platform_audit_organization_registered AFTER INSERT ON organizations
       FOR EACH ROW EXECUTE FUNCTION platform_audit_organization_registered()""",
    "REVOKE ALL ON FUNCTION platform_audit_organization_registered() FROM PUBLIC",
]
_SIGNATURES = [
    "platform_overview()",
    "platform_organization_list(text, text, text, boolean, integer, integer)",
    "platform_organization_detail(text)",
    "platform_organization_campuses(text)",
    "platform_organization_names(text[])",
    "platform_update_main_admin(text, text, text, text)",
    "platform_set_organization_status(text, text)",
]

_AI_SETTINGS = [
    """CREATE TABLE platform_ai_settings (
           id                                        smallint PRIMARY KEY DEFAULT 1
               CONSTRAINT ck_ai_settings_single CHECK (id = 1),
           human_detection_tracking_enabled          boolean NOT NULL DEFAULT true,
           person_reid_enabled                       boolean NOT NULL DEFAULT true,
           weapon_detection_enabled                  boolean NOT NULL DEFAULT true,
           fight_violence_detection_enabled          boolean NOT NULL DEFAULT true,
           restricted_area_face_recognition_enabled  boolean NOT NULL DEFAULT false,
           rag_assistant_enabled                     boolean NOT NULL DEFAULT false,
           agentic_investigation_enabled             boolean NOT NULL DEFAULT false,
           default_confidence_threshold              numeric(3, 2) NOT NULL DEFAULT 0.50
               CONSTRAINT ck_ai_settings_confidence CHECK (default_confidence_threshold BETWEEN 0.05 AND 0.99),
           frame_sample_fps                          smallint NOT NULL DEFAULT 5
               CONSTRAINT ck_ai_settings_fps CHECK (frame_sample_fps BETWEEN 1 AND 30),
           detection_cooldown_seconds                integer NOT NULL DEFAULT 60
               CONSTRAINT ck_ai_settings_cooldown CHECK (detection_cooldown_seconds BETWEEN 0 AND 3600),
           max_task_retries                          smallint NOT NULL DEFAULT 3
               CONSTRAINT ck_ai_settings_retries CHECK (max_task_retries BETWEEN 0 AND 10),
           task_timeout_seconds                      integer NOT NULL DEFAULT 30
               CONSTRAINT ck_ai_settings_timeout CHECK (task_timeout_seconds BETWEEN 5 AND 600),
           updated_at                                timestamptz NOT NULL DEFAULT now(),
           updated_by_platform_admin_id              uuid  -- no foreign key, like platform_audit_logs
       )""",
    "INSERT INTO platform_ai_settings DEFAULT VALUES",
    "CREATE INDEX ix_platform_audit_logs_action ON platform_audit_logs (action, occurred_at DESC)",
    """CREATE INDEX ix_platform_audit_logs_org ON platform_audit_logs ((metadata ->> 'organization_code'),
       occurred_at DESC)""",
]


def upgrade() -> None:
    platform = _platform_role()
    for statement in _FUNCTIONS + _AI_SETTINGS + _REGISTERED_AUDIT:
        op.execute(statement)
    for signature in _SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {platform}")
    op.execute("REVOKE ALL ON platform_ai_settings FROM PUBLIC")
    op.execute(f"GRANT SELECT, UPDATE ON platform_ai_settings TO {platform}")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_platform_audit_organization_registered ON organizations")
    op.execute("DROP FUNCTION IF EXISTS platform_audit_organization_registered()")
    op.execute("DROP INDEX IF EXISTS ix_platform_audit_logs_org")
    op.execute("DROP INDEX IF EXISTS ix_platform_audit_logs_action")
    op.execute("DROP TABLE IF EXISTS platform_ai_settings")
    for signature in reversed(_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
