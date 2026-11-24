"""AI Configuration (Org Admin page 17, Supervisor page 14; Live Monitoring items 16–22; decision D-024).

* ``app_platform_ai_modules()``: which of the five vision modules the Super Admin permits on the platform
  (``platform_ai_settings``), readable by signed-in Admins and Supervisors; the platform table itself
  stays closed to tenants.
* ``campus_ai_settings``: the modules a campus permits (its Admins decide, with their password). Without
  a row: human detection and tracking, Re-ID, weapon and fight/violence detection on; restricted-area
  face recognition off.
* ``camera_ai_settings``: the modules switched on per camera (Admins and Supervisors, with their
  password). Without a row a camera runs every module its campus permits.
* A camera runs a module only when the platform, its campus and the camera all allow it
  (``app_camera_ai_config``, read by the vision workers with the service token).
* ``camera_ai_status``: what a vision worker last reported running on a camera (modules, frame rate,
  error), so the pages show real status instead of the configuration alone.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-18
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"
_ADMIN = f"{_CAMPUS} AND app_actor_role() = 'ADMIN'"

# Module columns: (campus / camera column, platform_ai_settings column, default for a campus without a row).
MODULES = (
    ("human_detection", "human_detection_tracking_enabled", "true"),
    ("person_reid", "person_reid_enabled", "true"),
    ("weapon_detection", "weapon_detection_enabled", "true"),
    ("fight_detection", "fight_violence_detection_enabled", "true"),
    ("face_recognition", "restricted_area_face_recognition_enabled", "false"),
)

_MODULE_COLUMNS = ",\n".join(
    f"           {name:<18} boolean NOT NULL DEFAULT {default}" for name, _, default in MODULES
)
_CAMERA_COLUMNS = ",\n".join(f"           {name:<18} boolean NOT NULL DEFAULT true" for name, _, _ in MODULES)

_SCHEMA = [
    f"""CREATE TABLE campus_ai_settings (
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
{_MODULE_COLUMNS},
           updated_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_by_name  text NOT NULL,
           updated_at       timestamptz NOT NULL DEFAULT now(),
           PRIMARY KEY (organization_id, campus_id),
           CONSTRAINT fk_campus_ai_settings_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE
       )""",
    f"""CREATE TABLE camera_ai_settings (
           camera_id        uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
{_CAMERA_COLUMNS},
           updated_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_by_name  text NOT NULL,
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_camera_ai_settings_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    """CREATE TABLE camera_ai_status (
           camera_id        uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           modules          text[] NOT NULL DEFAULT '{}',
           frames_per_second numeric(6, 2) CONSTRAINT ck_camera_ai_status_fps CHECK (frames_per_second >= 0),
           last_error       text CONSTRAINT ck_camera_ai_status_error CHECK (length(last_error) <= 500),
           worker           text CONSTRAINT ck_camera_ai_status_worker CHECK (length(worker) <= 100),
           reported_at      timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_camera_ai_status_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT ck_camera_ai_status_modules CHECK (modules <@ ARRAY['human_detection', 'person_reid',
               'weapon_detection', 'fight_detection', 'face_recognition']::text[])
       )""",
]

_TABLES = ["campus_ai_settings", "camera_ai_settings", "camera_ai_status"]

_POLICIES = [
    f"CREATE POLICY campus_ai_select ON campus_ai_settings FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY campus_ai_insert ON campus_ai_settings FOR INSERT WITH CHECK ({_ADMIN})",
    f"CREATE POLICY campus_ai_update ON campus_ai_settings FOR UPDATE USING ({_ADMIN}) WITH CHECK ({_ADMIN})",
    f"CREATE POLICY camera_ai_select ON camera_ai_settings FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY camera_ai_insert ON camera_ai_settings FOR INSERT WITH CHECK ({_STAFF})",
    f"CREATE POLICY camera_ai_update ON camera_ai_settings FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY camera_ai_status_select ON camera_ai_status FOR SELECT USING ({_STAFF})",
]

_GRANTS = {
    "campus_ai_settings": "SELECT, INSERT, UPDATE",
    "camera_ai_settings": "SELECT, INSERT, UPDATE",
    "camera_ai_status": "SELECT",
}

_PLATFORM_COLUMNS = ", ".join(f"p.{platform}" for _, platform, _ in MODULES)
_EFFECTIVE = ",\n".join(
    f"""                  COALESCE(p.{platform}, false) AND COALESCE(cs.{name}, {default})
                      AND COALESCE(ca.{name}, true) AS {name}"""
    for name, platform, default in MODULES
)

_FUNCTIONS = [
    # The Super Admin's module switches, for the AI Configuration pages (no other platform data).
    f"""CREATE FUNCTION app_platform_ai_modules()
       RETURNS TABLE (human_detection boolean, person_reid boolean, weapon_detection boolean,
                      fight_detection boolean, face_recognition boolean)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT {_PLATFORM_COLUMNS}
             FROM platform_ai_settings p
            WHERE app_actor_role() IN ('ADMIN', 'SUPERVISOR')
            ORDER BY p.id LIMIT 1
       $$""",
    # What a vision worker should run on a camera: platform AND campus AND camera, plus the platform's
    # detection parameters. Called with the service token (no signed-in user).
    f"""CREATE FUNCTION app_camera_ai_config(p_camera_id uuid)
       RETURNS TABLE (camera_id uuid, organization_id uuid, campus_id uuid, human_detection boolean,
                      person_reid boolean, weapon_detection boolean, fight_detection boolean,
                      face_recognition boolean, confidence_threshold numeric, frame_sample_fps smallint,
                      detection_cooldown_seconds integer)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT c.id, c.organization_id, c.campus_id,
{_EFFECTIVE},
                  p.default_confidence_threshold, p.frame_sample_fps, p.detection_cooldown_seconds
             FROM cameras c
             JOIN organizations o ON o.id = c.organization_id AND o.status = 'ACTIVE'
             LEFT JOIN platform_ai_settings p ON true
             LEFT JOIN campus_ai_settings cs ON cs.organization_id = c.organization_id AND cs.campus_id = c.campus_id
             LEFT JOIN camera_ai_settings ca ON ca.camera_id = c.id
            WHERE c.id = p_camera_id
            ORDER BY p.id LIMIT 1
       $$""",
    # A vision worker reports what it runs on a camera (service token).
    """CREATE FUNCTION app_camera_ai_status_report(p_camera_id uuid, p_modules text[], p_fps numeric,
                                                   p_error text, p_worker text)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           c cameras%ROWTYPE;
       BEGIN
           SELECT * INTO c FROM cameras x WHERE x.id = p_camera_id;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           INSERT INTO camera_ai_status AS s (camera_id, organization_id, campus_id, modules, frames_per_second,
                                              last_error, worker, reported_at)
           VALUES (c.id, c.organization_id, c.campus_id, COALESCE(p_modules, '{}'), p_fps, left(p_error, 500),
                   left(p_worker, 100), now())
           ON CONFLICT (camera_id) DO UPDATE
              SET modules = EXCLUDED.modules, frames_per_second = EXCLUDED.frames_per_second,
                  last_error = EXCLUDED.last_error, worker = EXCLUDED.worker, reported_at = now();
           RETURN 'RECORDED';
       END $$""",
]

_FUNCTION_SIGNATURES = [
    "app_platform_ai_modules()",
    "app_camera_ai_config(uuid)",
    "app_camera_ai_status_report(uuid, text[], numeric, text, text)",
]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA:
        op.execute(statement)
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for statement in _POLICIES:
        op.execute(statement)
    for table, rights in _GRANTS.items():
        op.execute(f"GRANT {rights} ON {table} TO {app}")
    for statement in _FUNCTIONS:
        op.execute(statement)
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
