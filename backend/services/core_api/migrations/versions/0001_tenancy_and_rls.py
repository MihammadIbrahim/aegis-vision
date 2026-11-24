"""Organizations, campuses, users, campus requests, cameras, and Row-Level Security.

How isolation works
-------------------
* The API connects as a restricted role (APP_DB_USER, default ``cctv_app``):
  not a superuser, not the table owner, cannot bypass RLS.
* Per request, the API sets only ``app.user_id``. The ``app_actor_*`` functions
  look up that user's organization, campus, role and First Admin status, and only
  for ACTIVE accounts. Nothing else the application says is trusted.
* Operational tables are visible only within the caller's own campus.
* Exception 1: ``app_admin_directory()`` returns Admin names and the First Admin
  flag for the caller's organization. Nothing else crosses campuses.
* Exception 2: the First Admin can read and decide campus registration requests,
  and can create the approved campus and its Admin. They still cannot read that
  campus's data.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_TABLES = """
CREATE TABLE organizations (
    id          uuid PRIMARY KEY,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE campuses (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES organizations (id),
    name             text NOT NULL,
    status           text NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_campuses_org_id UNIQUE (organization_id, id),
    CONSTRAINT uq_campuses_org_name UNIQUE (organization_id, name)
);

CREATE TABLE users (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    campus_id        uuid NOT NULL,
    role             text NOT NULL CHECK (role IN ('ADMIN', 'SUPERVISOR', 'GUARD')),
    status           text NOT NULL CHECK (status IN ('ACTIVE', 'LEFT')),
    full_name        text NOT NULL,
    email            text NOT NULL,
    display_id       text NOT NULL,
    is_first_admin   boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_users_campus FOREIGN KEY (organization_id, campus_id)
        REFERENCES campuses (organization_id, id),
    CONSTRAINT ck_users_first_admin_is_admin CHECK (NOT is_first_admin OR role = 'ADMIN'),
    CONSTRAINT ck_users_display_id_format CHECK (
        display_id ~ '^(ADM|SUP|GRD)-[0-9]{11}$'
        AND left(display_id, 3) = CASE role WHEN 'ADMIN' THEN 'ADM' WHEN 'SUPERVISOR' THEN 'SUP' ELSE 'GRD' END
    )
);
CREATE UNIQUE INDEX uq_users_email ON users (lower(email));
CREATE UNIQUE INDEX uq_users_display_id ON users (display_id);
CREATE UNIQUE INDEX uq_users_one_first_admin_per_org ON users (organization_id) WHERE is_first_admin;
CREATE INDEX ix_users_org_campus ON users (organization_id, campus_id);
CREATE INDEX ix_users_org_role ON users (organization_id, role);

CREATE TABLE campus_registration_requests (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL REFERENCES organizations (id),
    proposed_campus_name  text NOT NULL,
    applicant_name        text NOT NULL,
    applicant_email       text NOT NULL,
    email_verified        boolean NOT NULL DEFAULT false,
    status                text NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    submitted_at          timestamptz NOT NULL DEFAULT now(),
    reviewed_by           uuid REFERENCES users (id),
    reviewed_at           timestamptz,
    approved_campus_id    uuid REFERENCES campuses (id)
);
CREATE INDEX ix_campus_requests_org_status ON campus_registration_requests (organization_id, status);

CREATE TABLE cameras (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    campus_id        uuid NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_cameras_campus FOREIGN KEY (organization_id, campus_id)
        REFERENCES campuses (organization_id, id)
);
CREATE INDEX ix_cameras_org_campus ON cameras (organization_id, campus_id);
"""

# SECURITY DEFINER functions run as the owner, so they can read `users` without
# recursing into the users RLS policy. search_path is pinned to stop hijacking.
_FUNCTIONS = [
    """
    CREATE FUNCTION app_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
        SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
    $$
    """,
    """
    CREATE FUNCTION app_actor_org_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        SELECT organization_id FROM users WHERE id = app_current_user_id() AND status = 'ACTIVE'
    $$
    """,
    """
    CREATE FUNCTION app_actor_campus_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        SELECT campus_id FROM users WHERE id = app_current_user_id() AND status = 'ACTIVE'
    $$
    """,
    """
    CREATE FUNCTION app_actor_role() RETURNS text
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        SELECT role FROM users WHERE id = app_current_user_id() AND status = 'ACTIVE'
    $$
    """,
    """
    CREATE FUNCTION app_actor_is_first_admin() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        SELECT COALESCE(
            (SELECT is_first_admin FROM users
              WHERE id = app_current_user_id() AND status = 'ACTIVE' AND role = 'ADMIN'),
            false)
    $$
    """,
    # The same-organization Admin directory: names and First Admin flag only,
    # only for an active Admin caller, only for the caller's own organization.
    """
    CREATE FUNCTION app_admin_directory(p_organization_id uuid)
    RETURNS TABLE (full_name text, is_first_admin boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        SELECT u.full_name, u.is_first_admin
          FROM users u
         WHERE app_actor_role() = 'ADMIN'
           AND p_organization_id = app_actor_org_id()
           AND u.organization_id = p_organization_id
           AND u.role = 'ADMIN'
           AND u.status = 'ACTIVE'
         ORDER BY u.is_first_admin DESC, u.full_name
    $$
    """,
]
_FUNCTION_SIGNATURES = [
    "app_current_user_id()",
    "app_actor_org_id()",
    "app_actor_campus_id()",
    "app_actor_role()",
    "app_actor_is_first_admin()",
    "app_admin_directory(uuid)",
]

_SAME_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"

_POLICIES = [
    # organizations: only the caller's own organization.
    "ALTER TABLE organizations ENABLE ROW LEVEL SECURITY",
    "CREATE POLICY organizations_select ON organizations FOR SELECT USING (id = app_actor_org_id())",
    # campuses: read only your own campus; only the First Admin may create one (on approval).
    "ALTER TABLE campuses ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY campuses_select ON campuses FOR SELECT
       USING (organization_id = app_actor_org_id() AND id = app_actor_campus_id())""",
    """CREATE POLICY campuses_insert_first_admin ON campuses FOR INSERT
       WITH CHECK (organization_id = app_actor_org_id() AND app_actor_is_first_admin())""",
    # users: your own row, plus users of your own campus. Nobody can create a First Admin.
    "ALTER TABLE users ENABLE ROW LEVEL SECURITY",
    f"""CREATE POLICY users_select ON users FOR SELECT
        USING (id = app_current_user_id() OR ({_SAME_CAMPUS}))""",
    """CREATE POLICY users_insert ON users FOR INSERT
        WITH CHECK (
            NOT is_first_admin
            AND organization_id = app_actor_org_id()
            AND (campus_id = app_actor_campus_id() OR (app_actor_is_first_admin() AND role = 'ADMIN'))
        )""",
    f"CREATE POLICY users_update ON users FOR UPDATE USING ({_SAME_CAMPUS}) WITH CHECK ({_SAME_CAMPUS})",
    # campus requests: only the First Admin of that organization.
    "ALTER TABLE campus_registration_requests ENABLE ROW LEVEL SECURITY",
    """CREATE POLICY campus_requests_select ON campus_registration_requests FOR SELECT
       USING (organization_id = app_actor_org_id() AND app_actor_is_first_admin())""",
    """CREATE POLICY campus_requests_update ON campus_registration_requests FOR UPDATE
       USING (organization_id = app_actor_org_id() AND app_actor_is_first_admin())
       WITH CHECK (organization_id = app_actor_org_id() AND app_actor_is_first_admin())""",
    # cameras: the pattern every campus-scoped operational table must follow.
    "ALTER TABLE cameras ENABLE ROW LEVEL SECURITY",
    f"CREATE POLICY cameras_same_campus ON cameras FOR ALL USING ({_SAME_CAMPUS}) WITH CHECK ({_SAME_CAMPUS})",
]


def _grants(role: str) -> list[str]:
    statements = [
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT SELECT ON organizations TO {role}",
        f"GRANT SELECT, INSERT ON campuses TO {role}",
        f"GRANT SELECT, INSERT, UPDATE ON users TO {role}",
        f"GRANT SELECT, UPDATE ON campus_registration_requests TO {role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON cameras TO {role}",
    ]
    for signature in _FUNCTION_SIGNATURES:
        statements.append(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        statements.append(f"GRANT EXECUTE ON FUNCTION {signature} TO {role}")
    return statements


def upgrade() -> None:
    op.execute(_TABLES)
    for statement in _FUNCTIONS + _POLICIES + _grants(_app_role()):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cameras, campus_registration_requests, users, campuses, organizations CASCADE")
    for signature in reversed(_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
