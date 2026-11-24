"""Camera Management: camera details, safer writes, and the export.

Requirements: Section 13; Admin dashboard *3. Camera Management*; Supervisor dashboard
*3. Camera Monitoring & Status*. Decision D-012.

* ``cameras`` gains:
  - ``connection_type`` (RTSP or ONVIF);
  - an optional ``stream_url`` (the "IP Address / Stream URL" field), checked against the type:
    ``rtsp://`` / ``rtsps://`` for RTSP, ``http://`` / ``https://`` for ONVIF;
  - ``updated_at``.

  The camera's ID stays the system-generated ``id``, and its location stays the 0013 link (one
  building, floor or area).
* **No credentials** are stored: a stream URL with a user name or password (``user:pass@host``)
  is refused by the database. No secure storage for camera credentials exists yet.
* **Unique per campus**, ignoring capitalization: the display name, and the stream URL when given.
* **Row-Level Security:** reading stays as before (same campus). Adding, changing and removing
  cameras is now limited to the campus's Admins and Supervisors; before, any active user of the
  campus could, at the database level.
* **Export** (``app_admin_deletion_export``): each camera now also lists its connection type and
  stream URL.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"_revision_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"

# scheme://host[:port][/path...]: the authority part may not contain "@", so no user:password.
_URL = r"^(rtsps?|https?)://[^/?#@\s]+([/?#]\S*)?$"

_UPGRADE = [
    f"""ALTER TABLE cameras
           ADD COLUMN connection_type text NOT NULL DEFAULT 'RTSP'
               CONSTRAINT ck_cameras_connection_type CHECK (connection_type IN ('RTSP', 'ONVIF')),
           ADD COLUMN stream_url text
               CONSTRAINT ck_cameras_stream_url CHECK (length(stream_url) <= 2048 AND stream_url ~* '{_URL}'),
           ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
           ADD CONSTRAINT ck_cameras_stream_matches_type CHECK (
               stream_url IS NULL
               OR (connection_type = 'RTSP' AND stream_url ~* '^rtsps?://')
               OR (connection_type = 'ONVIF' AND stream_url ~* '^https?://')),
           ADD CONSTRAINT ck_cameras_display_name
               CHECK (length(display_name) BETWEEN 1 AND 120 AND display_name = btrim(display_name))""",
    "CREATE UNIQUE INDEX uq_cameras_name ON cameras (organization_id, campus_id, lower(display_name))",
    """CREATE UNIQUE INDEX uq_cameras_stream ON cameras (organization_id, campus_id, lower(stream_url))
       WHERE stream_url IS NOT NULL""",
    "DROP POLICY cameras_same_campus ON cameras",
    f"CREATE POLICY cameras_select ON cameras FOR SELECT USING ({_CAMPUS})",
    f"CREATE POLICY cameras_insert ON cameras FOR INSERT WITH CHECK ({_STAFF})",
    f"CREATE POLICY cameras_update ON cameras FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY cameras_delete ON cameras FOR DELETE USING ({_STAFF})",
]

_EXPORT_CAMERA_0013 = "'display_name', c.display_name, 'created_at', c.created_at)"
_EXPORT_CAMERA = (
    "'display_name', c.display_name, 'connection_type', c.connection_type, "
    "'stream_url', c.stream_url, 'created_at', c.created_at)"
)


def _export_function_0013() -> str:
    return _load("0013_buildings_floors_areas.py")._export_function()


def _export_function() -> str:
    old = _export_function_0013()
    assert old.count(_EXPORT_CAMERA_0013) == 1
    return old.replace(_EXPORT_CAMERA_0013, _EXPORT_CAMERA)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)
    op.execute(_export_function())


def downgrade() -> None:
    """Back to 0013: camera details are dropped; the earlier campus-wide policy returns."""
    op.execute(_export_function_0013())
    for policy in ("cameras_select", "cameras_insert", "cameras_update", "cameras_delete"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON cameras")
    op.execute(f"CREATE POLICY cameras_same_campus ON cameras FOR ALL USING ({_CAMPUS}) WITH CHECK ({_CAMPUS})")
    op.execute("DROP INDEX IF EXISTS uq_cameras_stream")
    op.execute("DROP INDEX IF EXISTS uq_cameras_name")
    op.execute(
        "ALTER TABLE cameras DROP CONSTRAINT IF EXISTS ck_cameras_display_name, "
        "DROP CONSTRAINT IF EXISTS ck_cameras_stream_matches_type, "
        "DROP COLUMN IF EXISTS updated_at, DROP COLUMN IF EXISTS stream_url, DROP COLUMN IF EXISTS connection_type"
    )
