"""AI feature controls: per-organization blocks, Supervisor visibility, explicit per-camera opt-in (decision D-028).

A camera runs a vision module only when all four levels allow it:

    effective(m) = platform_enabled(m) AND NOT org_blocked(m) AND campus_enabled(m) AND camera_enabled(m)

* ``platform_organization_ai_blocks`` (one row per organization, no row = nothing blocked): the Super Admin blocks
  or unblocks a module for one organization. Tenants have no privilege on the table. The platform role reads and
  changes it only through ``platform_organization_ai_permissions_get(code)`` and
  ``platform_organization_ai_permissions_set(code, module, blocked, admin_id)`` (the API confirms the Super Admin's
  password, SA-SEC-4, and writes ``ORGANIZATION_AI_PERMISSIONS_UPDATED`` to ``platform_audit_logs``). Blocking a
  module also switches it off on every camera of the organization, so unblocking it later never switches a camera
  back on by itself. The rows cascade from ``organizations`` (organization erasure).
* ``app_organization_ai_blocks()``: the signed-in Admin's or Supervisor's own organization's blocks, nothing else.
* ``campus_ai_settings.supervisor_can_manage_*``: per module, whether the campus's Supervisors see and change it
  (default true). Only Admins write ``campus_ai_settings`` (0027 policies, unchanged). Hiding a module from
  Supervisors does not switch it off on any camera.
* ``camera_ai_settings``: new cameras run **no** module until one is selected. A camera without a row now runs
  nothing (``app_camera_ai_config``), and the column defaults are false. Cameras that existed before this migration
  get a row with every module selected, so they keep running exactly what they ran before.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-20
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _role(variable: str, default: str) -> str:
    role = os.environ.get(variable, default)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid {variable} role name: {role!r}")
    return role


_HEAD = "SECURITY DEFINER SET search_path = pg_catalog, public"

# (campus / camera column, long feature name, campus default without a row). platform_ai_settings uses
# "<long>_enabled"; platform_organization_ai_blocks "<long>_blocked"; campus_ai_settings "supervisor_can_manage_<long>".
MODULES = (
    ("human_detection", "human_detection_tracking", "true"),
    ("person_reid", "person_reid", "true"),
    ("weapon_detection", "weapon_detection", "true"),
    ("fight_detection", "fight_violence_detection", "true"),
    ("face_recognition", "restricted_area_face_recognition", "false"),
)

_BLOCK_COLUMNS = ",\n".join(f"           {long}_blocked boolean NOT NULL DEFAULT false" for _, long, _ in MODULES)
_BLOCKS_SELECT = ", ".join(f"COALESCE(b.{long}_blocked, false)" for _, long, _ in MODULES)
_BLOCKS_RETURN = ", ".join(f"{short} boolean" for short, _, _ in MODULES)
_MODULE_CASE = "\n".join(f"               WHEN '{short}' THEN '{long}'" for short, long, _ in MODULES)


def _effective(levels: int) -> str:
    """The per-module effective expression of ``app_camera_ai_config`` (4 levels now; 3 levels before, 0027)."""
    rows = []
    for short, long, default in MODULES:
        if levels == 4:
            rows.append(
                f"""                  COALESCE(p.{long}_enabled, false) AND NOT COALESCE(b.{long}_blocked, false)
                      AND COALESCE(cs.{short}, {default}) AND COALESCE(ca.{short}, false) AS {short}"""
            )
        else:
            rows.append(
                f"""                  COALESCE(p.{long}_enabled, false) AND COALESCE(cs.{short}, {default})
                      AND COALESCE(ca.{short}, true) AS {short}"""
            )
    return ",\n".join(rows)


def _camera_config(levels: int) -> str:
    blocks = (
        "\n             LEFT JOIN platform_organization_ai_blocks b ON b.organization_id = c.organization_id"
        if levels == 4
        else ""
    )
    return f"""CREATE OR REPLACE FUNCTION app_camera_ai_config(p_camera_id uuid)
       RETURNS TABLE (camera_id uuid, organization_id uuid, campus_id uuid, human_detection boolean,
                      person_reid boolean, weapon_detection boolean, fight_detection boolean,
                      face_recognition boolean, confidence_threshold numeric, frame_sample_fps smallint,
                      detection_cooldown_seconds integer)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT c.id, c.organization_id, c.campus_id,
{_effective(levels)},
                  p.default_confidence_threshold, p.frame_sample_fps, p.detection_cooldown_seconds
             FROM cameras c
             JOIN organizations o ON o.id = c.organization_id AND o.status = 'ACTIVE'
             LEFT JOIN platform_ai_settings p ON true{blocks}
             LEFT JOIN campus_ai_settings cs ON cs.organization_id = c.organization_id AND cs.campus_id = c.campus_id
             LEFT JOIN camera_ai_settings ca ON ca.camera_id = c.id
            WHERE c.id = p_camera_id
            ORDER BY p.id LIMIT 1
       $$"""


_SCHEMA = [
    f"""CREATE TABLE platform_organization_ai_blocks (
           organization_id  uuid PRIMARY KEY REFERENCES organizations (id) ON DELETE CASCADE,
{_BLOCK_COLUMNS},
           updated_at       timestamptz NOT NULL DEFAULT now(),
           updated_by_admin_id uuid  -- the Super Admin; no foreign key, like platform_audit_logs
       )""",
    # Defence in depth: no policy, so a role without the table owner's rights sees no row even if granted.
    "ALTER TABLE platform_organization_ai_blocks ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON platform_organization_ai_blocks FROM PUBLIC",
    *(
        f"ALTER TABLE campus_ai_settings ADD COLUMN supervisor_can_manage_{long} boolean NOT NULL DEFAULT true"
        for _, long, _ in MODULES
    ),
    *(f"ALTER TABLE camera_ai_settings ALTER COLUMN {short} SET DEFAULT false" for short, _, _ in MODULES),
]

# Existing cameras keep exactly what they ran before (0027: a camera without a row ran every permitted module).
_BACKFILL = [
    "ALTER TABLE camera_ai_settings NO FORCE ROW LEVEL SECURITY",
    f"""INSERT INTO camera_ai_settings (camera_id, organization_id, campus_id,
                                       {", ".join(short for short, _, _ in MODULES)}, updated_by_name)
        SELECT c.id, c.organization_id, c.campus_id, {", ".join("true" for _ in MODULES)},
               'System (kept the modules this camera ran before D-028)'
          FROM cameras c
         WHERE NOT EXISTS (SELECT 1 FROM camera_ai_settings s WHERE s.camera_id = c.id)""",
    "ALTER TABLE camera_ai_settings FORCE ROW LEVEL SECURITY",
]

_FUNCTIONS = [
    # The Super Admin's view of one organization's blocks (no row = nothing blocked). Empty for an unknown code.
    f"""CREATE FUNCTION platform_organization_ai_permissions_get(p_code text)
       RETURNS TABLE ({_BLOCKS_RETURN}, updated_at timestamptz, updated_by_admin_id uuid)
       LANGUAGE sql STABLE {_HEAD} AS $$
           SELECT {_BLOCKS_SELECT}, b.updated_at, b.updated_by_admin_id
             FROM organizations o
             LEFT JOIN platform_organization_ai_blocks b ON b.organization_id = o.id
            WHERE o.organization_code = p_code
       $$""",
    # Block or unblock one module for one organization. Blocking also switches the module off on every camera
    # of the organization, so a later unblock never switches a camera on by itself.
    f"""CREATE FUNCTION platform_organization_ai_permissions_set(p_code text, p_module text, p_blocked boolean,
                                                                 p_admin_id uuid)
       RETURNS text
       LANGUAGE plpgsql {_HEAD} AS $$
       DECLARE
           v_org uuid;
           v_long text;
           v_before boolean;
       BEGIN
           v_long := CASE p_module
{_MODULE_CASE}
           END;
           IF v_long IS NULL OR p_blocked IS NULL THEN
               RETURN 'INVALID';
           END IF;
           SELECT o.id INTO v_org FROM organizations o WHERE o.organization_code = p_code;
           IF NOT FOUND THEN
               RETURN 'NOT_FOUND';
           END IF;
           INSERT INTO platform_organization_ai_blocks (organization_id) VALUES (v_org)
           ON CONFLICT (organization_id) DO NOTHING;
           EXECUTE format('SELECT %I FROM platform_organization_ai_blocks WHERE organization_id = $1 FOR UPDATE',
                          v_long || '_blocked')
              INTO v_before USING v_org;
           IF v_before = p_blocked THEN
               RETURN 'UNCHANGED';
           END IF;
           EXECUTE format('UPDATE platform_organization_ai_blocks SET %I = $1, updated_at = now(),
                                  updated_by_admin_id = $2 WHERE organization_id = $3', v_long || '_blocked')
              USING p_blocked, p_admin_id, v_org;
           IF p_blocked THEN
               EXECUTE format('UPDATE camera_ai_settings SET %I = false, updated_at = now(), updated_by = NULL,
                                      updated_by_name = ''Platform (feature blocked for this organization)''
                                WHERE organization_id = $1 AND %I', p_module, p_module)
                  USING v_org;
           END IF;
           RETURN 'UPDATED';
       END $$""",
    # A signed-in Admin's or Supervisor's own organization's blocks (one row; all false without a block row).
    f"""CREATE FUNCTION app_organization_ai_blocks()
       RETURNS TABLE ({_BLOCKS_RETURN})
       LANGUAGE sql STABLE {_HEAD} AS $$
           SELECT {_BLOCKS_SELECT}
             FROM organizations o
             LEFT JOIN platform_organization_ai_blocks b ON b.organization_id = o.id
            WHERE o.id = app_actor_org_id() AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')
       $$""",
]

_PLATFORM_FUNCTIONS = [
    "platform_organization_ai_permissions_get(text)",
    "platform_organization_ai_permissions_set(text, text, boolean, uuid)",
]
_APP_FUNCTIONS = ["app_organization_ai_blocks()"]


def upgrade() -> None:
    app = _role("APP_DB_USER", "cctv_app")
    platform = _role("PLATFORM_DB_USER", "cctv_platform")
    for statement in _SCHEMA + _BACKFILL + _FUNCTIONS:
        op.execute(statement)
    op.execute(_camera_config(4))
    for signature in _PLATFORM_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {platform}")
    for signature in _APP_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    op.execute(_camera_config(3))  # CREATE OR REPLACE keeps 0027's grants
    for signature in _PLATFORM_FUNCTIONS + _APP_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for short, long, _ in MODULES:
        op.execute(f"ALTER TABLE camera_ai_settings ALTER COLUMN {short} SET DEFAULT true")
        op.execute(f"ALTER TABLE campus_ai_settings DROP COLUMN IF EXISTS supervisor_can_manage_{long}")
    op.execute("DROP TABLE IF EXISTS platform_organization_ai_blocks")
    # The rows 0033 added for existing cameras select every module, which is what 0027 ran without a row.
