"""The first campus is named by the registering Main Admin (decision D-021, amending decision E).

What changes
------------
* ``organization_registrations.campus_name``: required while a registration is pending, erased with the
  other applicant details when it completes or expires. Pending registrations from before this
  migration get "Main Campus", the name they were promised.
* ``app_org_registration_submit`` takes the campus name; ``app_org_registration_complete`` creates the
  first campus with it (instead of always "Main Campus"). The two expiry helpers also erase it.

Everything else in the registration flow of migration 0004 is unchanged.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-13
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


def _migration_0004() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0004", Path(__file__).with_name("0004_organization_registration.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DETAILS_0004 = "organization_name, admin_full_name, username, email, phone, password_hash, recipient_digest"
_DETAILS = "organization_name, campus_name, admin_full_name, username, email, phone, password_hash, recipient_digest"
_ERASE_0004 = (
    "SET status = 'EXPIRED', organization_name = NULL, admin_full_name = NULL, username = NULL,\n"
    "                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL"
)
_ERASE = (
    "SET status = 'EXPIRED', organization_name = NULL, campus_name = NULL, admin_full_name = NULL, username = NULL,\n"
    "                  email = NULL, phone = NULL, password_hash = NULL, recipient_digest = NULL"
)

_OLD_SUBMIT = "app_org_registration_submit(uuid, text, text, text, text, text, text, text, text, integer)"
_NEW_SUBMIT = "app_org_registration_submit(uuid, text, text, text, text, text, text, text, text, text, integer)"
_COMPLETE = "app_org_registration_complete(uuid, uuid, uuid, uuid, text)"

_TABLE = [
    """ALTER TABLE organization_registrations ADD COLUMN campus_name text
           CONSTRAINT ck_org_registrations_campus_name CHECK (length(campus_name) BETWEEN 2 AND 100)""",
    "UPDATE organization_registrations SET campus_name = 'Main Campus' WHERE status = 'PENDING'",
    "ALTER TABLE organization_registrations DROP CONSTRAINT ck_org_registrations_details_only_while_pending",
    f"""ALTER TABLE organization_registrations ADD CONSTRAINT ck_org_registrations_details_only_while_pending CHECK (
           CASE WHEN status = 'PENDING' THEN num_nonnulls({_DETAILS}) = 8
                ELSE num_nonnulls({_DETAILS}) = 0
           END)""",
]


def _functions() -> tuple[list[str], list[str]]:
    """(helpers, public functions) of migration 0004, rewritten for the campus name."""
    m = _migration_0004()
    helpers = []
    for helper in m._HELPERS:
        assert _ERASE_0004 in helper, "migration 0004's erase statement changed"
        helpers.append(helper.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(_ERASE_0004, _ERASE))
    submit, _issue, _verify, complete = m._FUNCTIONS
    for old, new in [
        ("p_admin_full_name text, p_username text,", "p_campus_name text, p_admin_full_name text, p_username text,"),
        (
            "id, organization_code, status, organization_name, admin_full_name, username,",
            "id, organization_code, status, organization_name, campus_name, admin_full_name, username,",
        ),
        ("p_organization_name, p_admin_full_name,", "p_organization_name, p_campus_name, p_admin_full_name,"),
    ]:
        assert old in submit, old
        submit = submit.replace(old, new, 1)
    for old, new in [
        (
            "VALUES (p_campus_id, p_organization_id, 'Main Campus', 'ACTIVE');",
            "VALUES (p_campus_id, p_organization_id, r.campus_name, 'ACTIVE');",
        ),
        (
            "organization_name = NULL, admin_full_name = NULL,",
            "organization_name = NULL, campus_name = NULL, admin_full_name = NULL,",
        ),
        (
            "r.organization_code, r.organization_name, 'Main Campus'::text,",
            "r.organization_code, r.organization_name, r.campus_name,",
        ),
    ]:
        assert old in complete, old
        complete = complete.replace(old, new, 1)
    complete = complete.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    return helpers, [submit, complete]


def upgrade() -> None:
    app = _app_role()
    helpers, (submit, complete) = _functions()
    for statement in _TABLE + helpers:
        op.execute(statement)
    op.execute(f"DROP FUNCTION {_OLD_SUBMIT}")
    op.execute(submit)
    op.execute(complete)
    op.execute(f"REVOKE ALL ON FUNCTION {_NEW_SUBMIT} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_NEW_SUBMIT} TO {app}")


def downgrade() -> None:
    app = _app_role()
    m = _migration_0004()
    op.execute(f"DROP FUNCTION {_NEW_SUBMIT}")
    for helper in m._HELPERS:
        op.execute(helper.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    submit, _issue, _verify, complete = m._FUNCTIONS
    op.execute(submit)
    op.execute(complete.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(f"REVOKE ALL ON FUNCTION {_OLD_SUBMIT} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_OLD_SUBMIT} TO {app}")
    op.execute(
        "ALTER TABLE organization_registrations DROP CONSTRAINT ck_org_registrations_details_only_while_pending"
    )
    op.execute("UPDATE organization_registrations SET campus_name = NULL")
    op.execute(
        f"""ALTER TABLE organization_registrations
               ADD CONSTRAINT ck_org_registrations_details_only_while_pending CHECK (
               CASE WHEN status = 'PENDING' THEN num_nonnulls({_DETAILS_0004}) = 7
                    ELSE num_nonnulls({_DETAILS_0004}) = 0
               END)"""
    )
    op.execute("ALTER TABLE organization_registrations DROP COLUMN campus_name")
