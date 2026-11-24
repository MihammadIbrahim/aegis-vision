"""Organization Personnel Management, Excel/CSV personnel import and restricted-area access
(decision D-016).

Personnel are organizational records (students, faculty, employees, contractors, visitors, ...),
not login accounts. Every table is scoped to one campus by composite foreign keys and Row-Level
Security; nothing can point into another campus.

Tables:

* ``personnel_categories``: the campus's person types. Eight built-in categories are created for
  every campus (existing ones here, new ones by a trigger on ``campuses``); Admins rename,
  deactivate or add more. ``required_fields`` lists the optional canonical fields this category
  makes required (for example the institutional ID for students).
* ``personnel_custom_fields``: controlled extra attributes (type, choices, required, which
  categories). Values live in ``personnel.custom_attributes``; no column is ever created from user
  input.
* ``personnel``: the canonical record. ``personnel_code`` is the system-generated Personnel ID
  (``PSN-`` + 10 characters, unique). The institutional ID and email are unique per campus,
  ignoring capitalization. Optional assigned location (one building, floor or area of the campus).
  Records are deactivated, never deleted.
* ``personnel_import_batches`` / ``personnel_import_issues`` / ``personnel_import_mapping_profiles``:
  import history, the validation findings (codes and messages, no cell values) and reusable
  column mappings.
* ``restricted_areas``: areas the Admin has marked as restricted.
* ``personnel_area_access``: a person's permission for one restricted area (validity, optional
  weekly schedule, revocation). Revoked rows are kept as history; one ACTIVE grant per person and
  area. A trigger refuses an active grant for an area that is not restricted.

Row-Level Security: **every table above is Admin-only** for direct reads and writes, except
``personnel_categories`` and ``restricted_areas`` (Admins and Supervisors read; Admins write).
Supervisors read personnel and grants only through these functions, which return basic columns:

* ``app_personnel_directory(...)``: search and details (email and phone only for Admins).
* ``app_area_access_list(...)``: grants with basic personnel columns (revoked history and notes
  only for Admins).
* ``app_personnel_audit_trail(target_type, target_id)``: Admin only; this module's audit entries.

Also replaced (from 0015): campus deletion erases all of the above; the campus export includes it.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
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


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"
_ADMIN = f"{_CAMPUS} AND app_actor_role() = 'ADMIN'"


def _text(column: str, longest: int, *, minimum: int = 1) -> str:
    return f"CHECK (length({column}) BETWEEN {minimum} AND {longest} AND {column} = btrim({column}))"


# Canonical optional fields a category may make required (kept in step with the domain code).
REQUIRABLE_FIELDS = (
    "external_id",
    "email",
    "phone",
    "department",
    "designation",
    "joining_date",
    "start_date",
    "end_date",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
    "assigned_location",
)
_REQUIRABLE = "ARRAY[" + ", ".join(f"'{f}'" for f in REQUIRABLE_FIELDS) + "]::text[]"

# Built-in categories: (code, label, sort order, required optional fields).
DEFAULT_CATEGORIES = (
    ("STUDENT", "Student", 10, ("external_id",)),
    ("FACULTY", "Faculty", 20, ("external_id",)),
    ("EMPLOYEE", "Employee", 30, ("external_id",)),
    ("STAFF", "Staff", 40, ("external_id",)),
    ("MANAGEMENT", "Management", 50, ("external_id",)),
    ("CONTRACTOR", "Contractor", 60, ("end_date",)),
    ("VISITOR", "Visitor", 70, ("end_date",)),
    ("OTHER", "Other", 80, ()),
)


def _defaults_values(org: str, campus: str) -> str:
    rows = []
    for code, label, order, required in DEFAULT_CATEGORIES:
        fields = "ARRAY[" + ", ".join(f"'{f}'" for f in required) + "]::text[]" if required else "'{}'::text[]"
        rows.append(f"(gen_random_uuid(), {org}, {campus}, '{code}', '{label}', {fields}, true, {order})")
    return ",\n                      ".join(rows)


_PHONE = r"^\+?[0-9]{7,15}$"
_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

_SCHEMA = [
    f"""CREATE TABLE personnel_categories (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           code             text NOT NULL
                                    CONSTRAINT ck_personnel_categories_code CHECK (code ~ '^[A-Z][A-Z0-9_]{{1,39}}$'),
           label            text NOT NULL CONSTRAINT ck_personnel_categories_label {_text("label", 60)},
           description      text CONSTRAINT ck_personnel_categories_description CHECK (length(description) <= 500),
           required_fields  text[] NOT NULL DEFAULT '{{}}'
                                CONSTRAINT ck_personnel_categories_required CHECK (required_fields <@ {_REQUIRABLE}),
           is_system        boolean NOT NULL DEFAULT false,
           is_active        boolean NOT NULL DEFAULT true,
           sort_order       integer NOT NULL DEFAULT 100
                                CONSTRAINT ck_personnel_categories_sort CHECK (sort_order BETWEEN 0 AND 10000),
           created_at       timestamptz NOT NULL DEFAULT now(),
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_personnel_categories_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           CONSTRAINT uq_personnel_categories_scope UNIQUE (organization_id, campus_id, id)
       )""",
    "CREATE UNIQUE INDEX uq_personnel_categories_code ON personnel_categories (organization_id, campus_id, code)",
    """CREATE UNIQUE INDEX uq_personnel_categories_label
           ON personnel_categories (organization_id, campus_id, lower(label))""",
    f"""CREATE TABLE personnel_custom_fields (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           field_key        text NOT NULL CONSTRAINT ck_personnel_custom_fields_key
                                CHECK (field_key ~ '^[a-z][a-z0-9_]{{1,39}}$'),
           label            text NOT NULL CONSTRAINT ck_personnel_custom_fields_label {_text("label", 60)},
           field_type       text NOT NULL CONSTRAINT ck_personnel_custom_fields_type
                                CHECK (field_type IN ('TEXT', 'NUMBER', 'DATE', 'BOOLEAN', 'CHOICE', 'EMAIL', 'PHONE')),
           choices          text[] NOT NULL DEFAULT '{{}}',
           is_required      boolean NOT NULL DEFAULT false,
           category_ids     uuid[] NOT NULL DEFAULT '{{}}',
           max_length       integer NOT NULL DEFAULT 255
                                CONSTRAINT ck_personnel_custom_fields_length CHECK (max_length BETWEEN 1 AND 2000),
           help_text        text CONSTRAINT ck_personnel_custom_fields_help CHECK (length(help_text) <= 300),
           is_active        boolean NOT NULL DEFAULT true,
           created_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           created_at       timestamptz NOT NULL DEFAULT now(),
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_personnel_custom_fields_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           -- A choice field needs its choices; other types take none.
           CONSTRAINT ck_personnel_custom_fields_choices
               CHECK ((field_type = 'CHOICE') = (cardinality(choices) > 0) AND cardinality(choices) <= 100)
       )""",
    """CREATE UNIQUE INDEX uq_personnel_custom_fields_key
           ON personnel_custom_fields (organization_id, campus_id, field_key)""",
    """CREATE UNIQUE INDEX uq_personnel_custom_fields_label
           ON personnel_custom_fields (organization_id, campus_id, lower(label))""",
    """CREATE TABLE personnel_import_batches (
           id                      uuid PRIMARY KEY,
           organization_id         uuid NOT NULL,
           campus_id               uuid NOT NULL,
           original_filename       text NOT NULL CONSTRAINT ck_personnel_imports_filename
                                       CHECK (length(original_filename) BETWEEN 1 AND 255),
           file_format             text NOT NULL
                                    CONSTRAINT ck_personnel_imports_format CHECK (file_format IN ('XLSX', 'CSV')),
           file_size               bigint NOT NULL CONSTRAINT ck_personnel_imports_size CHECK (file_size > 0),
           file_sha256             text NOT NULL
                                    CONSTRAINT ck_personnel_imports_sha CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
           sheet_name              text CONSTRAINT ck_personnel_imports_sheet CHECK (length(sheet_name) <= 64),
           header_row              integer NOT NULL DEFAULT 1
                                    CONSTRAINT ck_personnel_imports_header CHECK (header_row >= 1),
           headers                 jsonb NOT NULL DEFAULT '[]'::jsonb,
           mapping                 jsonb,
           status                  text NOT NULL DEFAULT 'UPLOADED' CONSTRAINT ck_personnel_imports_status
                                       CHECK (status IN ('UPLOADED', 'INVALID', 'VALIDATED', 'IMPORTED', 'FAILED',
                                                         'ROLLED_BACK', 'DISCARDED')),
           total_rows              integer CONSTRAINT ck_personnel_imports_total CHECK (total_rows >= 0),
           valid_rows              integer CONSTRAINT ck_personnel_imports_valid CHECK (valid_rows >= 0),
           warning_count           integer NOT NULL DEFAULT 0,
           error_count             integer NOT NULL DEFAULT 0,
           imported_count          integer NOT NULL DEFAULT 0,
           rolled_back_count       integer NOT NULL DEFAULT 0,
           failure_reason          text CONSTRAINT ck_personnel_imports_failure CHECK (length(failure_reason) <= 500),
           has_annotated_workbook  boolean NOT NULL DEFAULT false,
           files_purged_at         timestamptz,
           uploaded_by             uuid REFERENCES users (id) ON DELETE SET NULL,
           uploaded_at             timestamptz NOT NULL DEFAULT now(),
           validated_by            uuid REFERENCES users (id) ON DELETE SET NULL,
           validated_at            timestamptz,
           confirmed_by            uuid REFERENCES users (id) ON DELETE SET NULL,
           confirmed_at            timestamptz,
           rolled_back_by          uuid REFERENCES users (id) ON DELETE SET NULL,
           rolled_back_at          timestamptz,
           updated_at              timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_personnel_imports_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           CONSTRAINT uq_personnel_imports_scope UNIQUE (organization_id, campus_id, id)
       )""",
    """CREATE INDEX ix_personnel_imports_campus
           ON personnel_import_batches (organization_id, campus_id, uploaded_at DESC)""",
    """CREATE TABLE personnel_import_issues (
           batch_id      uuid NOT NULL REFERENCES personnel_import_batches (id) ON DELETE CASCADE,
           seq           integer NOT NULL,
           row_number    integer CONSTRAINT ck_personnel_import_issues_row CHECK (row_number >= 1),
           column_index  integer,
           column_name   text CONSTRAINT ck_personnel_import_issues_column CHECK (length(column_name) <= 255),
           field         text CONSTRAINT ck_personnel_import_issues_field CHECK (length(field) <= 80),
           severity      text NOT NULL
                                    CONSTRAINT ck_personnel_import_issues_severity
                                        CHECK (severity IN ('ERROR', 'WARNING')),
           code          text NOT NULL CONSTRAINT ck_personnel_import_issues_code CHECK (code ~ '^[A-Z_]{3,40}$'),
           message       text NOT NULL CONSTRAINT ck_personnel_import_issues_message CHECK (length(message) <= 500),
           PRIMARY KEY (batch_id, seq)
       )""",
    """CREATE INDEX ix_personnel_import_issues_severity ON personnel_import_issues (batch_id, severity, seq)""",
    f"""CREATE TABLE personnel_import_mapping_profiles (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           name             text NOT NULL CONSTRAINT ck_personnel_mapping_name {_text("name", 80)},
           mapping          jsonb NOT NULL
                                    CONSTRAINT ck_personnel_mapping_object CHECK (jsonb_typeof(mapping) = 'object'),
           created_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           created_at       timestamptz NOT NULL DEFAULT now(),
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_personnel_mapping_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id)
       )""",
    """CREATE UNIQUE INDEX uq_personnel_mapping_name
           ON personnel_import_mapping_profiles (organization_id, campus_id, lower(name))""",
    f"""CREATE TABLE personnel (
           id                          uuid PRIMARY KEY,
           organization_id             uuid NOT NULL,
           campus_id                   uuid NOT NULL,
           personnel_code              text NOT NULL
                                    CONSTRAINT ck_personnel_code CHECK (personnel_code ~ '^PSN-[A-Z0-9]{{10}}$'),
           external_id                 text CONSTRAINT ck_personnel_external_id {_text("external_id", 64)},
           full_name                   text NOT NULL CONSTRAINT ck_personnel_full_name {_text("full_name", 200)},
           category_id                 uuid NOT NULL,
           email                       text CONSTRAINT ck_personnel_email
                                           CHECK (length(email) <= 320 AND email = lower(email) AND email ~ '{_EMAIL}'),
           phone                       text CONSTRAINT ck_personnel_phone CHECK (phone ~ '{_PHONE}'),
           department                  text CONSTRAINT ck_personnel_department {_text("department", 120)},
           designation                 text CONSTRAINT ck_personnel_designation {_text("designation", 120)},
           joining_date                date,
           start_date                  date,
           end_date                    date,
           status                      text NOT NULL DEFAULT 'ACTIVE'
                                           CONSTRAINT ck_personnel_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
           address                     text CONSTRAINT ck_personnel_address {_text("address", 500)},
           emergency_contact_name      text CONSTRAINT ck_personnel_ec_name {_text("emergency_contact_name", 200)},
           emergency_contact_phone     text
                                    CONSTRAINT ck_personnel_ec_phone CHECK (emergency_contact_phone ~ '{_PHONE}'),
           emergency_contact_relation  text
                                    CONSTRAINT ck_personnel_ec_relation {_text("emergency_contact_relation", 60)},
           notes                       text CONSTRAINT ck_personnel_notes CHECK (length(notes) <= 2000),
           custom_attributes           jsonb NOT NULL DEFAULT '{{}}'::jsonb
                                           CONSTRAINT ck_personnel_custom
                                               CHECK (jsonb_typeof(custom_attributes) = 'object'),
           photo_path                  text CONSTRAINT ck_personnel_photo_path CHECK (length(photo_path) <= 300),
           photo_content_type          text CONSTRAINT ck_personnel_photo_type
                                           CHECK (photo_content_type IN ('image/jpeg', 'image/png')),
           created_via                 text NOT NULL
                                    CONSTRAINT ck_personnel_created_via CHECK (created_via IN ('MANUAL', 'IMPORT')),
           import_batch_id             uuid,
           created_by                  uuid REFERENCES users (id) ON DELETE SET NULL,
           created_at                  timestamptz NOT NULL DEFAULT now(),
           updated_by                  uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_at                  timestamptz NOT NULL DEFAULT now(),
           deactivated_by              uuid REFERENCES users (id) ON DELETE SET NULL,
           deactivated_at              timestamptz,
           deactivation_reason         text
                                    CONSTRAINT ck_personnel_deactivation_reason
                                        CHECK (length(deactivation_reason) <= 500),
           CONSTRAINT fk_personnel_category FOREIGN KEY (organization_id, campus_id, category_id)
               REFERENCES personnel_categories (organization_id, campus_id, id),
           CONSTRAINT fk_personnel_import FOREIGN KEY (organization_id, campus_id, import_batch_id)
               REFERENCES personnel_import_batches (organization_id, campus_id, id),
           CONSTRAINT ck_personnel_import_source CHECK ((created_via = 'IMPORT') = (import_batch_id IS NOT NULL)),
           CONSTRAINT ck_personnel_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
           CONSTRAINT ck_personnel_deactivated CHECK ((status = 'INACTIVE') = (deactivated_at IS NOT NULL)),
           CONSTRAINT ck_personnel_photo CHECK ((photo_path IS NULL) = (photo_content_type IS NULL)),
           CONSTRAINT uq_personnel_code UNIQUE (personnel_code),
           CONSTRAINT uq_personnel_scope UNIQUE (organization_id, campus_id, id)
       )""",
    """CREATE UNIQUE INDEX uq_personnel_external_id ON personnel (organization_id, campus_id, lower(external_id))
           WHERE external_id IS NOT NULL""",
    "CREATE UNIQUE INDEX uq_personnel_email ON personnel (organization_id, campus_id, email) WHERE email IS NOT NULL",
    "CREATE INDEX ix_personnel_name ON personnel (organization_id, campus_id, lower(full_name))",
    "CREATE INDEX ix_personnel_category ON personnel (category_id)",
    "CREATE INDEX ix_personnel_import ON personnel (import_batch_id) WHERE import_batch_id IS NOT NULL",
    """CREATE TABLE restricted_areas (
           area_id          uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           access_note      text CONSTRAINT ck_restricted_areas_note CHECK (length(access_note) <= 500),
           marked_by        uuid REFERENCES users (id) ON DELETE SET NULL,
           marked_at        timestamptz NOT NULL DEFAULT now(),
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_restricted_areas_area FOREIGN KEY (organization_id, campus_id, area_id)
               REFERENCES areas (organization_id, campus_id, id)
       )""",
    """CREATE TABLE personnel_area_access (
           id                 uuid PRIMARY KEY,
           organization_id    uuid NOT NULL,
           campus_id          uuid NOT NULL,
           personnel_id       uuid NOT NULL,
           area_id            uuid NOT NULL,
           status             text NOT NULL DEFAULT 'ACTIVE'
                                  CONSTRAINT ck_area_access_status CHECK (status IN ('ACTIVE', 'REVOKED')),
           valid_from         timestamptz NOT NULL,
           valid_until        timestamptz,
           schedule_days      smallint[],
           schedule_start     time,
           schedule_end       time,
           schedule_timezone  text
                                    CONSTRAINT ck_area_access_timezone
                                        CHECK (length(schedule_timezone) BETWEEN 1 AND 64),
           note               text CONSTRAINT ck_area_access_note CHECK (length(note) <= 500),
           granted_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           granted_at         timestamptz NOT NULL DEFAULT now(),
           updated_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_at         timestamptz NOT NULL DEFAULT now(),
           revoked_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           revoked_at         timestamptz,
           revoke_reason      text CONSTRAINT ck_area_access_revoke_reason CHECK (length(revoke_reason) <= 500),
           CONSTRAINT fk_area_access_personnel FOREIGN KEY (organization_id, campus_id, personnel_id)
               REFERENCES personnel (organization_id, campus_id, id),
           CONSTRAINT fk_personnel_area_access_area FOREIGN KEY (organization_id, campus_id, area_id)
               REFERENCES areas (organization_id, campus_id, id),
           CONSTRAINT ck_area_access_validity CHECK (valid_until IS NULL OR valid_until > valid_from),
           CONSTRAINT ck_area_access_revoked CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL)),
           -- A schedule is all or nothing: days (ISO 1 = Monday .. 7), a same-day window, a time zone.
           CONSTRAINT ck_area_access_schedule CHECK (
               (schedule_days IS NULL AND schedule_start IS NULL AND schedule_end IS NULL AND schedule_timezone IS NULL)
               OR (cardinality(schedule_days) BETWEEN 1 AND 7
                   AND schedule_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]
                   AND schedule_start IS NOT NULL AND schedule_end IS NOT NULL AND schedule_start < schedule_end
                   AND schedule_timezone IS NOT NULL))
       )""",
    """CREATE UNIQUE INDEX uq_area_access_active ON personnel_area_access (personnel_id, area_id)
           WHERE status = 'ACTIVE'""",
    "CREATE INDEX ix_area_access_area ON personnel_area_access (area_id, status)",
    "CREATE INDEX ix_area_access_personnel ON personnel_area_access (personnel_id, status)",
]

_TABLES = [
    "personnel_categories",
    "personnel_custom_fields",
    "personnel_import_batches",
    "personnel_import_issues",
    "personnel_import_mapping_profiles",
    "personnel",
    "restricted_areas",
    "personnel_area_access",
]

_ISSUE_SCOPE = (
    "EXISTS (SELECT 1 FROM personnel_import_batches b WHERE b.id = batch_id "
    "AND b.organization_id = app_actor_org_id() AND b.campus_id = app_actor_campus_id()) "
    "AND app_actor_role() = 'ADMIN'"
)

_POLICIES = [
    *(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY" for t in _TABLES),
    # Categories: everyone on the staff reads them (directory filters); only Admins change them.
    f"CREATE POLICY personnel_categories_select ON personnel_categories FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY personnel_categories_insert ON personnel_categories FOR INSERT WITH CHECK ({_ADMIN})",
    f"""CREATE POLICY personnel_categories_update ON personnel_categories FOR UPDATE
        USING ({_ADMIN}) WITH CHECK ({_ADMIN})""",
    # Everything holding personal data, and every configuration write: Admins only.
    *(
        f"CREATE POLICY {t}_admin ON {t} FOR ALL USING ({_ADMIN}) WITH CHECK ({_ADMIN})"
        for t in (
            "personnel_custom_fields",
            "personnel_import_batches",
            "personnel_import_mapping_profiles",
            "personnel",
            "personnel_area_access",
        )
    ),
    f"""CREATE POLICY personnel_import_issues_admin ON personnel_import_issues FOR ALL
        USING ({_ISSUE_SCOPE}) WITH CHECK ({_ISSUE_SCOPE})""",
    f"CREATE POLICY restricted_areas_select ON restricted_areas FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY restricted_areas_insert ON restricted_areas FOR INSERT WITH CHECK ({_ADMIN})",
    f"CREATE POLICY restricted_areas_update ON restricted_areas FOR UPDATE USING ({_ADMIN}) WITH CHECK ({_ADMIN})",
    f"CREATE POLICY restricted_areas_delete ON restricted_areas FOR DELETE USING ({_ADMIN})",
]

_GRANTS = {
    "personnel_categories": "SELECT, INSERT, UPDATE",
    "personnel_custom_fields": "SELECT, INSERT, UPDATE",
    "personnel_import_batches": "SELECT, INSERT, UPDATE",
    "personnel_import_issues": "SELECT, INSERT, DELETE",
    "personnel_import_mapping_profiles": "SELECT, INSERT, UPDATE, DELETE",
    "personnel": "SELECT, INSERT, UPDATE",
    "restricted_areas": "SELECT, INSERT, UPDATE, DELETE",
    "personnel_area_access": "SELECT, INSERT, UPDATE",
}

_FUNCTIONS = [
    # --- Built-in categories for every new campus ---------------------------------------------------
    f"""CREATE FUNCTION app_seed_personnel_categories() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           INSERT INTO personnel_categories (id, organization_id, campus_id, code, label, required_fields,
                                             is_system, sort_order)
           VALUES {_defaults_values("NEW.organization_id", "NEW.id")}
           ON CONFLICT DO NOTHING;
           RETURN NEW;
       END
       $$""",
    """CREATE TRIGGER trg_campuses_personnel_categories AFTER INSERT ON campuses
       FOR EACH ROW EXECUTE FUNCTION app_seed_personnel_categories()""",
    # --- An active grant needs a restricted area ------------------------------------------------------
    """CREATE FUNCTION app_area_access_requires_restricted() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF NEW.status = 'ACTIVE' AND NOT EXISTS (
               SELECT 1 FROM restricted_areas r
                WHERE r.area_id = NEW.area_id AND r.organization_id = NEW.organization_id
                  AND r.campus_id = NEW.campus_id) THEN
               RAISE EXCEPTION 'area is not restricted' USING ERRCODE = 'check_violation',
                   CONSTRAINT = 'ck_area_access_restricted_area';
           END IF;
           RETURN NEW;
       END
       $$""",
    """CREATE TRIGGER trg_area_access_restricted BEFORE INSERT OR UPDATE ON personnel_area_access
       FOR EACH ROW EXECUTE FUNCTION app_area_access_requires_restricted()""",
    # --- Directory: basic columns for Admins and Supervisors; contact details for Admins only ----------
    r"""CREATE FUNCTION app_personnel_directory(
           p_personnel_id uuid, p_query text, p_category_id uuid, p_status text, p_department text,
           p_designation text, p_area_id uuid, p_limit integer, p_offset integer)
       RETURNS TABLE (id uuid, personnel_code text, external_id text, full_name text, category_id uuid,
                      category_code text, category_label text, department text, designation text, status text,
                      email text, phone text, start_date date, end_date date, building_name text,
                      floor_name text, area_name text, total_count bigint)
       LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_role text := app_actor_role();
           v_admin boolean := v_role = 'ADMIN';
           v_pattern text;
       BEGIN
           IF v_role IS NULL OR v_role NOT IN ('ADMIN', 'SUPERVISOR') THEN
               RETURN;
           END IF;
           IF p_query IS NOT NULL AND btrim(p_query) <> '' THEN
               v_pattern := '%' || replace(replace(replace(lower(btrim(p_query)), '\', '\\'), '%', '\%'), '_', '\_')
                            || '%';
           END IF;
           RETURN QUERY
           SELECT p.id, p.personnel_code, p.external_id, p.full_name, c.id, c.code, c.label, p.department,
                  p.designation, p.status,
                  CASE WHEN v_admin THEN p.email END, CASE WHEN v_admin THEN p.phone END,
                  p.start_date, p.end_date, b.name, f.name, a.name,
                  count(*) OVER ()
             FROM personnel p
             JOIN personnel_categories c ON c.id = p.category_id
             LEFT JOIN areas a ON a.id = p.area_id
             LEFT JOIN floors f ON f.id = COALESCE(p.floor_id, a.floor_id)
             LEFT JOIN buildings b ON b.id = COALESCE(p.building_id, f.building_id, a.building_id)
            WHERE p.organization_id = app_actor_org_id() AND p.campus_id = app_actor_campus_id()
              AND (p_personnel_id IS NULL OR p.id = p_personnel_id)
              AND (v_pattern IS NULL
                   OR lower(p.full_name) LIKE v_pattern
                   OR lower(p.personnel_code) LIKE v_pattern
                   OR lower(COALESCE(p.external_id, '')) LIKE v_pattern
                   OR (v_admin AND COALESCE(p.email, '') LIKE v_pattern))
              AND (p_category_id IS NULL OR p.category_id = p_category_id)
              AND (p_status IS NULL OR p.status = p_status)
              AND (p_department IS NULL OR lower(p.department) = lower(btrim(p_department)))
              AND (p_designation IS NULL OR lower(p.designation) = lower(btrim(p_designation)))
              AND (p_area_id IS NULL OR EXISTS (
                     SELECT 1 FROM personnel_area_access g
                      WHERE g.personnel_id = p.id AND g.area_id = p_area_id AND g.status = 'ACTIVE'))
            ORDER BY lower(p.full_name), p.personnel_code
            LIMIT GREATEST(1, LEAST(p_limit, 500)) OFFSET GREATEST(0, p_offset);
       END
       $$""",
    # --- Grants with basic personnel columns; revoked history and notes for Admins only ----------------
    """CREATE FUNCTION app_area_access_list(p_area_id uuid, p_personnel_id uuid, p_grant_id uuid,
                                            p_include_revoked boolean)
       RETURNS TABLE (id uuid, personnel_id uuid, personnel_code text, personnel_name text, personnel_status text,
                      category_label text, department text, area_id uuid, area_name text, floor_name text,
                      building_name text, status text, valid_from timestamptz, valid_until timestamptz,
                      schedule_days smallint[], schedule_start time, schedule_end time, schedule_timezone text,
                      note text, granted_at timestamptz, granted_by_name text, updated_at timestamptz,
                      revoked_at timestamptz, revoked_by_name text, revoke_reason text)
       LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_role text := app_actor_role();
           v_admin boolean := v_role = 'ADMIN';
       BEGIN
           IF v_role IS NULL OR v_role NOT IN ('ADMIN', 'SUPERVISOR') THEN
               RETURN;
           END IF;
           RETURN QUERY
           SELECT g.id, p.id, p.personnel_code, p.full_name, p.status, c.label, p.department,
                  a.id, a.name, f.name, b.name, g.status, g.valid_from, g.valid_until,
                  g.schedule_days, g.schedule_start, g.schedule_end, g.schedule_timezone,
                  CASE WHEN v_admin THEN g.note END, g.granted_at, gu.full_name, g.updated_at,
                  g.revoked_at, CASE WHEN v_admin THEN ru.full_name END, CASE WHEN v_admin THEN g.revoke_reason END
             FROM personnel_area_access g
             JOIN personnel p ON p.id = g.personnel_id
             JOIN personnel_categories c ON c.id = p.category_id
             JOIN areas a ON a.id = g.area_id
             LEFT JOIN floors f ON f.id = a.floor_id
             JOIN buildings b ON b.id = a.building_id
             LEFT JOIN users gu ON gu.id = g.granted_by
             LEFT JOIN users ru ON ru.id = g.revoked_by
            WHERE g.organization_id = app_actor_org_id() AND g.campus_id = app_actor_campus_id()
              AND (p_area_id IS NULL OR g.area_id = p_area_id)
              AND (p_personnel_id IS NULL OR g.personnel_id = p_personnel_id)
              AND (p_grant_id IS NULL OR g.id = p_grant_id)
              AND (g.status = 'ACTIVE' OR (v_admin AND p_include_revoked))
            ORDER BY g.status, lower(p.full_name), a.name, g.granted_at DESC;
       END
       $$""",
    # --- This module's audit entries, for the Admin's history views -------------------------------------
    """CREATE FUNCTION app_personnel_audit_trail(p_target_type text, p_target_id uuid)
       RETURNS TABLE (occurred_at timestamptz, action text, target_type text, target_id uuid, actor_role text,
                      actor_display_id text, actor_name text, metadata jsonb)
       LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_batch uuid;
       BEGIN
           IF app_actor_role() IS DISTINCT FROM 'ADMIN' OR p_target_type NOT IN
                  ('PERSONNEL', 'AREA_ACCESS', 'RESTRICTED_AREA', 'PERSONNEL_IMPORT') THEN
               RETURN;
           END IF;
           IF p_target_type = 'PERSONNEL' THEN
               SELECT p.import_batch_id INTO v_batch FROM personnel p
                WHERE p.id = p_target_id AND p.organization_id = app_actor_org_id()
                  AND p.campus_id = app_actor_campus_id();
           END IF;
           RETURN QUERY
           SELECT l.occurred_at, l.action, l.target_type, l.target_id, l.actor_role, u.display_id, u.full_name,
                  l.metadata
             FROM organization_audit_logs l
             LEFT JOIN users u ON u.id = l.actor_user_id
            WHERE l.organization_id = app_actor_org_id() AND l.campus_id = app_actor_campus_id()
              AND ((l.target_type = p_target_type AND l.target_id = p_target_id)
                   -- A person's history includes the import that created (or rolled back) them.
                   OR (v_batch IS NOT NULL AND l.target_type = 'PERSONNEL_IMPORT' AND l.target_id = v_batch
                       AND l.action IN ('PERSONNEL_IMPORT_CONFIRMED', 'PERSONNEL_IMPORT_ROLLED_BACK')))
            ORDER BY l.occurred_at, l.id;
       END
       $$""",
]
_FUNCTION_SIGNATURES = [
    "app_personnel_directory(uuid, text, uuid, text, text, text, uuid, integer, integer)",
    "app_area_access_list(uuid, uuid, uuid, boolean)",
    "app_personnel_audit_trail(text, uuid)",
]

# --- Campus deletion and export (replacing the 0015 versions) -------------------------------------------

_PERSONNEL_OF_CAMPUS = """               -- Personnel data, before the locations and users it points at (0017).
               DELETE FROM personnel_area_access
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM restricted_areas WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM personnel WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM personnel_import_batches
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM personnel_import_mapping_profiles
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM personnel_custom_fields
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM personnel_categories WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
"""
_PERSONNEL_OF_ORG = """               DELETE FROM personnel_area_access WHERE organization_id = me.organization_id;
               DELETE FROM restricted_areas WHERE organization_id = me.organization_id;
               DELETE FROM personnel WHERE organization_id = me.organization_id;
               DELETE FROM personnel_import_batches WHERE organization_id = me.organization_id;
               DELETE FROM personnel_import_mapping_profiles WHERE organization_id = me.organization_id;
               DELETE FROM personnel_custom_fields WHERE organization_id = me.organization_id;
               DELETE FROM personnel_categories WHERE organization_id = me.organization_id;
"""


def _deletion_function_0015() -> str:
    return _load("0015_alert_management.py")._deletion_function()


def _deletion_function() -> str:
    m15 = _load("0015_alert_management.py")
    old = _deletion_function_0015()
    campus, org = m15._ALERTS_OF_CAMPUS, m15._ALERTS_OF_ORG
    assert old.count(campus) == 1 and old.count(org) == 1
    return old.replace(campus, _PERSONNEL_OF_CAMPUS + campus).replace(org, _PERSONNEL_OF_ORG + org)


def _export_function_0015() -> str:
    return _load("0015_alert_management.py")._export_function()


_EXPORT_ANCHOR = "               'pending_account_creations', "
_EXPORT_PERSONNEL = """               'personnel_categories', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'code', pc.code, 'label', pc.label, 'description', pc.description,
                       'required_fields', to_jsonb(pc.required_fields), 'is_active', pc.is_active)
                       ORDER BY pc.sort_order, pc.code)
                     FROM personnel_categories pc
                    WHERE pc.organization_id = me.organization_id AND pc.campus_id = me.campus_id), '[]'::jsonb),
               'personnel_custom_fields', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'key', cf.field_key, 'label', cf.label, 'type', cf.field_type,
                       'choices', to_jsonb(cf.choices), 'required', cf.is_required, 'is_active', cf.is_active)
                       ORDER BY cf.field_key)
                     FROM personnel_custom_fields cf
                    WHERE cf.organization_id = me.organization_id AND cf.campus_id = me.campus_id), '[]'::jsonb),
               'personnel', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'personnel_id', p.personnel_code, 'external_id', p.external_id, 'full_name', p.full_name,
                       'category', pc.code, 'email', p.email, 'phone', p.phone, 'department', p.department,
                       'designation', p.designation, 'joining_date', p.joining_date, 'start_date', p.start_date,
                       'end_date', p.end_date, 'status', p.status, 'address', p.address,
                       'emergency_contact_name', p.emergency_contact_name,
                       'emergency_contact_phone', p.emergency_contact_phone,
                       'emergency_contact_relation', p.emergency_contact_relation, 'notes', p.notes,
                       'custom_attributes', p.custom_attributes, 'has_photo', p.photo_path IS NOT NULL,
                       'created_via', p.created_via, 'created_at', p.created_at,
                       'deactivated_at', p.deactivated_at, 'deactivation_reason', p.deactivation_reason)
                       ORDER BY p.personnel_code)
                     FROM personnel p JOIN personnel_categories pc ON pc.id = p.category_id
                    WHERE p.organization_id = me.organization_id AND p.campus_id = me.campus_id), '[]'::jsonb),
               'restricted_areas', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'area', ar.name, 'building', bl.name, 'access_note', ra.access_note,
                       'marked_at', ra.marked_at) ORDER BY bl.name, ar.name)
                     FROM restricted_areas ra JOIN areas ar ON ar.id = ra.area_id
                     JOIN buildings bl ON bl.id = ar.building_id
                    WHERE ra.organization_id = me.organization_id AND ra.campus_id = me.campus_id), '[]'::jsonb),
               'area_access', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'personnel_id', p.personnel_code, 'area', ar.name, 'building', bl.name,
                       'status', g.status, 'valid_from', g.valid_from, 'valid_until', g.valid_until,
                       'schedule_days', to_jsonb(g.schedule_days), 'schedule_start', g.schedule_start,
                       'schedule_end', g.schedule_end, 'schedule_timezone', g.schedule_timezone,
                       'granted_at', g.granted_at, 'revoked_at', g.revoked_at, 'revoke_reason', g.revoke_reason)
                       ORDER BY p.personnel_code, ar.name, g.granted_at)
                     FROM personnel_area_access g JOIN personnel p ON p.id = g.personnel_id
                     JOIN areas ar ON ar.id = g.area_id JOIN buildings bl ON bl.id = ar.building_id
                    WHERE g.organization_id = me.organization_id AND g.campus_id = me.campus_id), '[]'::jsonb),
               'personnel_imports', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'original_filename', ib.original_filename, 'status', ib.status, 'total_rows', ib.total_rows,
                       'imported_count', ib.imported_count, 'error_count', ib.error_count,
                       'warning_count', ib.warning_count, 'uploaded_at', ib.uploaded_at,
                       'confirmed_at', ib.confirmed_at, 'rolled_back_at', ib.rolled_back_at)
                       ORDER BY ib.uploaded_at)
                     FROM personnel_import_batches ib
                    WHERE ib.organization_id = me.organization_id AND ib.campus_id = me.campus_id), '[]'::jsonb),
"""


def _export_function() -> str:
    old = _export_function_0015()
    assert old.count(_EXPORT_ANCHOR) == 1
    return old.replace(_EXPORT_ANCHOR, _EXPORT_PERSONNEL + _EXPORT_ANCHOR)


def _location_link() -> list[str]:
    """personnel.building_id / floor_id / area_id (at most one), the same pattern as cameras."""
    return _load("0013_buildings_floors_areas.py")._location_link("personnel", nullable_all=True)


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _location_link() + _POLICIES + _FUNCTIONS:
        op.execute(statement)
    # Built-in categories for the campuses that already exist.
    op.execute(
        f"""INSERT INTO personnel_categories (id, organization_id, campus_id, code, label, required_fields,
                                              is_system, sort_order)
            SELECT v.* FROM campuses c CROSS JOIN LATERAL (VALUES {_defaults_values("c.organization_id", "c.id")})
                 AS v(id, organization_id, campus_id, code, label, required_fields, is_system, sort_order)"""
    )
    for table, rights in _GRANTS.items():
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT {rights} ON {table} TO {app}")
    for name in ("app_seed_personnel_categories()", "app_area_access_requires_restricted()"):
        op.execute(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
    op.execute(_deletion_function())
    op.execute(_export_function())


def downgrade() -> None:
    """Back to 0016. All personnel, import history and restricted-area data are dropped."""
    op.execute(_export_function_0015())
    op.execute(_deletion_function_0015())
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TRIGGER IF EXISTS trg_campuses_personnel_categories ON campuses")
    op.execute("DROP TABLE IF EXISTS " + ", ".join(reversed(_TABLES)))
    op.execute("DROP FUNCTION IF EXISTS app_seed_personnel_categories()")
    op.execute("DROP FUNCTION IF EXISTS app_area_access_requires_restricted()")
