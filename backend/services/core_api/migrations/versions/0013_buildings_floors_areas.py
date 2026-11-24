"""Buildings, floors and areas (the campus location hierarchy), Guard locations, camera locations.

Hierarchy (requirements Section 3)::

    Organization → Campus → Building or Outdoor Perimeter → optional Floor → Area / Zone → Camera

* ``buildings``: ``kind`` is INDOOR or OUTDOOR_PERIMETER.
* ``floors``: only under INDOOR buildings. The foreign key carries the building's kind, so a
  floor cannot point at a perimeter, and a building with floors cannot become a perimeter.
* ``areas``: under a building; under one of its floors or none. The floor must belong to the same
  building. Optional boundary: NORTH / SOUTH / EAST / WEST / CUSTOM (a custom name is then
  required), and an optional INSIDE / OUTSIDE label.
* ``guard_location_assignments``: optional, one per Guard. It points at exactly one building,
  floor or area; the rest of the path is derived from it.
* ``cameras``: the same optional link (at most one of building, floor, area).

Every link is a composite foreign key that includes ``(organization_id, campus_id)``, so nothing
can point into another campus. Links never cascade: a location in use cannot be deleted. Only
campus deletion (``app_admin_deletion_complete``) removes a campus's locations, and it does so
explicitly.

Row-Level Security (same pattern as ``cameras``, plus role checks):
- **buildings:** Admins and Supervisors of the campus read them; only Admins write them.
- **floors, areas, Guard assignments:** Admins and Supervisors of the campus.
- **Guards:** they see their own assignment only, through ``app_guard_location()``.

Also replaced, from 0012: the deletion function (erases the locations) and the export (includes
them).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-28
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _load(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"_revision_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NAME = "CHECK (length(btrim({c})) BETWEEN 1 AND 120 AND {c} = btrim({c}))"
_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"
_ADMIN = f"{_CAMPUS} AND app_actor_role() = 'ADMIN'"


def _location_link(table: str, *, nullable_all: bool) -> list[str]:
    """building_id / floor_id / area_id columns pointing into the same campus."""
    count = "<= 1" if nullable_all else "= 1"
    return [
        f"""ALTER TABLE {table}
               ADD COLUMN building_id uuid,
               ADD COLUMN floor_id    uuid,
               ADD COLUMN area_id     uuid,
               ADD CONSTRAINT fk_{table}_building FOREIGN KEY (organization_id, campus_id, building_id)
                   REFERENCES buildings (organization_id, campus_id, id),
               ADD CONSTRAINT fk_{table}_floor FOREIGN KEY (organization_id, campus_id, floor_id)
                   REFERENCES floors (organization_id, campus_id, id),
               ADD CONSTRAINT fk_{table}_area FOREIGN KEY (organization_id, campus_id, area_id)
                   REFERENCES areas (organization_id, campus_id, id),
               ADD CONSTRAINT ck_{table}_one_location CHECK (num_nonnulls(building_id, floor_id, area_id) {count})""",
        f"CREATE INDEX ix_{table}_building ON {table} (building_id) WHERE building_id IS NOT NULL",
        f"CREATE INDEX ix_{table}_floor ON {table} (floor_id) WHERE floor_id IS NOT NULL",
        f"CREATE INDEX ix_{table}_area ON {table} (area_id) WHERE area_id IS NOT NULL",
    ]


_SCHEMA = [
    f"""CREATE TABLE buildings (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           kind             text NOT NULL CONSTRAINT ck_buildings_kind CHECK (kind IN ('INDOOR', 'OUTDOOR_PERIMETER')),
           name             text NOT NULL CONSTRAINT ck_buildings_name {_NAME.format(c="name")},
           created_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_buildings_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           CONSTRAINT uq_buildings_scope UNIQUE (organization_id, campus_id, id),
           CONSTRAINT uq_buildings_scope_kind UNIQUE (organization_id, campus_id, id, kind)
       )""",
    "CREATE UNIQUE INDEX uq_buildings_name ON buildings (organization_id, campus_id, lower(name))",
    f"""CREATE TABLE floors (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           building_id      uuid NOT NULL,
           building_kind    text NOT NULL DEFAULT 'INDOOR' CONSTRAINT ck_floors_indoor CHECK (building_kind = 'INDOOR'),
           name             text NOT NULL CONSTRAINT ck_floors_name {_NAME.format(c="name")},
           level            integer CONSTRAINT ck_floors_level CHECK (level BETWEEN -20 AND 300),
           created_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_floors_building FOREIGN KEY (organization_id, campus_id, building_id, building_kind)
               REFERENCES buildings (organization_id, campus_id, id, kind),
           CONSTRAINT uq_floors_scope UNIQUE (organization_id, campus_id, id),
           CONSTRAINT uq_floors_building_scope UNIQUE (organization_id, campus_id, building_id, id)
       )""",
    "CREATE UNIQUE INDEX uq_floors_name ON floors (building_id, lower(name))",
    "CREATE UNIQUE INDEX uq_floors_level ON floors (building_id, level) WHERE level IS NOT NULL",
    f"""CREATE TABLE areas (
           id                  uuid PRIMARY KEY,
           organization_id     uuid NOT NULL,
           campus_id           uuid NOT NULL,
           building_id         uuid NOT NULL,
           floor_id            uuid,
           name                text NOT NULL CONSTRAINT ck_areas_name {_NAME.format(c="name")},
           boundary_direction  text CONSTRAINT ck_areas_boundary_direction
                                   CHECK (boundary_direction IN ('NORTH', 'SOUTH', 'EAST', 'WEST', 'CUSTOM')),
           boundary_name       text CONSTRAINT ck_areas_boundary_name_text {_NAME.format(c="boundary_name")},
           boundary_side       text CONSTRAINT ck_areas_boundary_side CHECK (boundary_side IN ('INSIDE', 'OUTSIDE')),
           created_at          timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_areas_building FOREIGN KEY (organization_id, campus_id, building_id)
               REFERENCES buildings (organization_id, campus_id, id),
           -- The floor, when given, must be a floor of the same building.
           CONSTRAINT fk_areas_floor FOREIGN KEY (organization_id, campus_id, building_id, floor_id)
               REFERENCES floors (organization_id, campus_id, building_id, id),
           -- A custom boundary needs its name; the fixed directions take none.
           CONSTRAINT ck_areas_boundary_name CHECK ((boundary_direction = 'CUSTOM') = (boundary_name IS NOT NULL)),
           CONSTRAINT uq_areas_scope UNIQUE (organization_id, campus_id, id)
       )""",
    """CREATE UNIQUE INDEX uq_areas_name ON areas
           (building_id, COALESCE(floor_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))""",
    "CREATE INDEX ix_areas_floor ON areas (floor_id) WHERE floor_id IS NOT NULL",
    # Lets an assignment's foreign key require a Guard of the same campus.
    "CREATE UNIQUE INDEX uq_users_scope_role ON users (organization_id, campus_id, id, role)",
    """CREATE TABLE guard_location_assignments (
           guard_user_id    uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           guard_role       text NOT NULL DEFAULT 'GUARD'
                                CONSTRAINT ck_guard_locations_role CHECK (guard_role = 'GUARD'),
           assigned_at      timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_guard_locations_guard FOREIGN KEY (organization_id, campus_id, guard_user_id, guard_role)
               REFERENCES users (organization_id, campus_id, id, role) ON DELETE CASCADE
       )""",
    *_location_link("guard_location_assignments", nullable_all=False),
    *_location_link("cameras", nullable_all=True),
]

_TABLES = ["buildings", "floors", "areas", "guard_location_assignments"]

_POLICIES = [
    *(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY" for t in _TABLES),
    f"CREATE POLICY buildings_select ON buildings FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY buildings_insert ON buildings FOR INSERT WITH CHECK ({_ADMIN})",
    f"CREATE POLICY buildings_update ON buildings FOR UPDATE USING ({_ADMIN}) WITH CHECK ({_ADMIN})",
    f"CREATE POLICY buildings_delete ON buildings FOR DELETE USING ({_ADMIN})",
    f"CREATE POLICY floors_staff ON floors FOR ALL USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY areas_staff ON areas FOR ALL USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"""CREATE POLICY guard_locations_staff ON guard_location_assignments FOR ALL
        USING ({_STAFF}) WITH CHECK ({_STAFF})""",
]

_GUARD_LOCATION = """CREATE FUNCTION app_guard_location()
       RETURNS TABLE (building_name text, building_kind text, floor_name text, floor_level integer,
                      area_name text, boundary_direction text, boundary_name text, boundary_side text)
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT b.name, b.kind, f.name, f.level, a.name, a.boundary_direction, a.boundary_name, a.boundary_side
             FROM guard_location_assignments g
             JOIN users u ON u.id = g.guard_user_id AND u.role = 'GUARD' AND u.status = 'ACTIVE'
             LEFT JOIN areas a ON a.id = g.area_id
             LEFT JOIN floors f ON f.id = COALESCE(g.floor_id, a.floor_id)
             JOIN buildings b ON b.id = COALESCE(g.building_id, f.building_id, a.building_id)
            WHERE g.guard_user_id = app_current_user_id()
       $$"""

# --- Campus deletion and export (replacing the 0012 / 0011 versions) ---------------------------

_CAMERAS_OF_CAMPUS = (
    "               DELETE FROM cameras WHERE organization_id = me.organization_id AND campus_id = me.campus_id;\n"
)
_CAMERAS_OF_ORG = "               DELETE FROM cameras WHERE organization_id = me.organization_id;\n"
_LOCATIONS_OF_CAMPUS = """               -- Locations, after what points at them (cameras, Guard assignments).
               DELETE FROM guard_location_assignments
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM areas WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM floors WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM buildings WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
"""
_LOCATIONS_OF_ORG = """               DELETE FROM guard_location_assignments WHERE organization_id = me.organization_id;
               DELETE FROM areas WHERE organization_id = me.organization_id;
               DELETE FROM floors WHERE organization_id = me.organization_id;
               DELETE FROM buildings WHERE organization_id = me.organization_id;
"""


def _deletion_function_0012() -> str:
    return _load("0012_ownership_history_snapshot.py")._deletion_function()


def _deletion_function() -> str:
    old = _deletion_function_0012()
    assert old.count(_CAMERAS_OF_CAMPUS) == 1 and old.count(_CAMERAS_OF_ORG) == 1
    return old.replace(_CAMERAS_OF_CAMPUS, _CAMERAS_OF_CAMPUS + _LOCATIONS_OF_CAMPUS).replace(
        _CAMERAS_OF_ORG, _CAMERAS_OF_ORG + _LOCATIONS_OF_ORG
    )


def _export_function_0011() -> str:
    functions = _load("0011_complete_campus_erasure_and_deleted_org_history.py")._FUNCTIONS
    return next(f for f in functions if "FUNCTION app_admin_deletion_export(" in f)


# Names only: no internal keys, as for the rest of the export.
_PATH = """jsonb_build_object('building', b.name, 'floor', f.name, 'area', a.name)"""
_PATH_JOINS = """LEFT JOIN areas a ON a.id = x.area_id
                     LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, a.floor_id)
                     LEFT JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, a.building_id)"""
_EXPORT_ANCHOR = "               'pending_account_creations', "
_EXPORT_LOCATIONS = f"""               'locations', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'name', bl.name, 'kind', bl.kind, 'created_at', bl.created_at,
                       'floors', COALESCE((SELECT jsonb_agg(jsonb_build_object('name', fl.name, 'level', fl.level)
                                                            ORDER BY fl.level NULLS LAST, fl.name)
                                             FROM floors fl WHERE fl.building_id = bl.id), '[]'::jsonb),
                       'areas', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                                              'name', ar.name,
                                              'floor', (SELECT fl.name FROM floors fl WHERE fl.id = ar.floor_id),
                                              'boundary_direction', ar.boundary_direction,
                                              'boundary_name', ar.boundary_name,
                                              'boundary_side', ar.boundary_side) ORDER BY ar.name)
                                            FROM areas ar WHERE ar.building_id = bl.id), '[]'::jsonb))
                       ORDER BY bl.name)
                     FROM buildings bl WHERE bl.organization_id = me.organization_id AND bl.campus_id = me.campus_id),
                   '[]'::jsonb),
               'guard_locations', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'user_id', u.display_id, 'location', {_PATH}) ORDER BY u.display_id)
                     FROM guard_location_assignments x
                     JOIN users u ON u.id = x.guard_user_id
                     {_PATH_JOINS}
                    WHERE x.organization_id = me.organization_id AND x.campus_id = me.campus_id),
                   '[]'::jsonb),
               'camera_locations', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'display_name', x.display_name, 'location', {_PATH}) ORDER BY x.display_name)
                     FROM cameras x
                     {_PATH_JOINS}
                    WHERE x.organization_id = me.organization_id AND x.campus_id = me.campus_id
                      AND num_nonnulls(x.building_id, x.floor_id, x.area_id) = 1),
                   '[]'::jsonb),
"""


def _export_function() -> str:
    old = _export_function_0011()
    assert old.count(_EXPORT_ANCHOR) == 1
    new = old.replace(_EXPORT_ANCHOR, _EXPORT_LOCATIONS + _EXPORT_ANCHOR)
    return new.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _POLICIES:
        op.execute(statement)
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app}")
    op.execute(_GUARD_LOCATION)
    op.execute("REVOKE ALL ON FUNCTION app_guard_location() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_guard_location() TO {app}")
    op.execute(_deletion_function())
    op.execute(_export_function())


def downgrade() -> None:
    """Back to 0012. All buildings, floors, areas and location links are dropped."""
    op.execute(_export_function_0011().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(_deletion_function_0012())
    op.execute("DROP FUNCTION IF EXISTS app_guard_location()")
    op.execute(
        "ALTER TABLE cameras DROP CONSTRAINT IF EXISTS ck_cameras_one_location, "
        "DROP COLUMN IF EXISTS building_id, DROP COLUMN IF EXISTS floor_id, DROP COLUMN IF EXISTS area_id"
    )
    op.execute("DROP TABLE IF EXISTS guard_location_assignments, areas, floors, buildings")
    op.execute("DROP INDEX IF EXISTS uq_users_scope_role")
