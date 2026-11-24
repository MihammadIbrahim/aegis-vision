"""New Organization ID format: 6 capital letters + 11 digits (e.g. ``ABCDEF12345678901``).

* New Organization IDs use the new format: the API generates them in Python with
  ``secrets``, and the database default (used only for rows inserted without one) is
  replaced to match.
* IDs already issued in the ``ORG-`` + 11 digits format (migration 0004) are **not**
  rewritten and stay valid. This includes IDs reserved by registrations that are still
  waiting for their email code: they keep that ID when they complete, because a
  registration's reserved ID must never change.
* Uniqueness is unchanged: the unique indexes on ``organizations.organization_code`` and
  ``organization_registrations.organization_code`` still apply, and the two formats can
  never be equal to each other.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_OR_LEGACY = "'^([A-Z]{6}[0-9]{11}|ORG-[0-9]{11})$'"
_LEGACY_ONLY = "'^ORG-[0-9]{11}$'"
_CONSTRAINTS = [
    ("organizations", "ck_organizations_code_format"),
    ("organization_registrations", "ck_org_registrations_code_format"),
]

# 60 random bits from gen_random_uuid() (hex digits 1-12 and 14-16, skipping the fixed
# version digit), one UUID for the letters and one for the digits.
_NEW_DEFAULT = """CREATE OR REPLACE FUNCTION app_new_organization_code() RETURNS text
   LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public AS $$
   DECLARE
       h text := translate(gen_random_uuid()::text, '-', '');
       n bigint := ('x' || substr(h, 1, 12) || substr(h, 14, 3))::bit(60)::bigint;
       letters text := '';
   BEGIN
       FOR i IN 1..6 LOOP
           letters := letters || chr(65 + (n % 26)::integer);
           n := n / 26;
       END LOOP;
       h := translate(gen_random_uuid()::text, '-', '');
       n := ('x' || substr(h, 1, 12) || substr(h, 14, 3))::bit(60)::bigint;
       RETURN letters || lpad((n % 100000000000)::text, 11, '0');
   END $$"""

# The 0004 version, restored on downgrade.
_OLD_DEFAULT = """CREATE OR REPLACE FUNCTION app_new_organization_code() RETURNS text
   LANGUAGE sql VOLATILE SET search_path = pg_catalog, public AS $$
       SELECT 'ORG-' || lpad(
           ((('x' || substr(translate(gen_random_uuid()::text, '-', ''), 1, 12)
                  || substr(translate(gen_random_uuid()::text, '-', ''), 1, 3))::bit(60)::bigint)
             % 100000000000)::text,
           11, '0')
   $$"""


def upgrade() -> None:
    for table, constraint in _CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK (organization_code ~ {_NEW_OR_LEGACY})")
    # Same name and signature, so the column default and the (owner-only) privileges stay.
    op.execute(_NEW_DEFAULT)


def downgrade() -> None:
    op.execute(_OLD_DEFAULT)
    for table, constraint in _CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
        # The original rule, for new rows. IDs issued in the newer format (if any) stay in place.
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK (organization_code ~ {_LEGACY_ONLY}) NOT VALID"
        )
        op.execute(
            f"""DO $$ BEGIN
                   IF NOT EXISTS (SELECT 1 FROM {table} WHERE organization_code !~ {_LEGACY_ONLY}) THEN
                       ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};
                   END IF;
               END $$"""
        )
