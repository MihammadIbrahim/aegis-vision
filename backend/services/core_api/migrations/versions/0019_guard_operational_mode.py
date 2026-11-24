"""Guard operational mode (Standby vs Stationed / Standalone) and forced sign-out when a Guard's
assignment changes (decision D-018).

* ``user_credentials.sessions_revoked_at``: when set, the API refuses the account's access tokens
  issued before it (like ``password_changed_at``, but without a password change).
* Trigger ``trg_guard_assignment_revokes_sessions`` on ``guard_location_assignments``: every insert,
  delete, or update that changes the building, floor or area sets the Guard's ``sessions_revoked_at``
  to ``clock_timestamp()``. Assigning a post, moving it, clearing it ("Make Standby") and deactivation
  (which deletes the assignment) therefore all sign the Guard out at once, whichever code path does it.
* ``app_actor_sessions_revoked_at()``: the signed-in caller's own value, read on every request.
* ``app_guard_is_stationed(user_id)``: for sign-in (before a session exists): ``true`` if the account is
  an active Guard with a location, ``false`` if an active Guard without one, ``NULL`` otherwise.
* ``app_password_reset_request_by_guard_mode(...)``: forgot password with Guard ID + email + the chosen
  operational mode. A request is stored whether or not it matches (as in 0009), so the answer never
  reveals whether the account exists or how it is assigned; a mismatch simply sends no code.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-11
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_ACTIVE_GUARD = """u.role = 'GUARD' AND u.status = 'ACTIVE'
             AND EXISTS (SELECT 1 FROM campuses ca JOIN organizations o ON o.id = ca.organization_id
                          WHERE ca.organization_id = u.organization_id AND ca.id = u.campus_id
                            AND ca.status = 'ACTIVE' AND o.status = 'ACTIVE')"""

_STATEMENTS = [
    "ALTER TABLE user_credentials ADD COLUMN sessions_revoked_at timestamptz",
    """CREATE FUNCTION app_guard_assignment_revokes_sessions() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF TG_OP = 'UPDATE' AND NOT (NEW.building_id IS DISTINCT FROM OLD.building_id
                                        OR NEW.floor_id IS DISTINCT FROM OLD.floor_id
                                        OR NEW.area_id IS DISTINCT FROM OLD.area_id) THEN
               RETURN NULL;
           END IF;
           UPDATE user_credentials c SET sessions_revoked_at = clock_timestamp()
            WHERE c.user_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.guard_user_id ELSE NEW.guard_user_id END;
           RETURN NULL;
       END $$""",
    """CREATE TRIGGER trg_guard_assignment_revokes_sessions
       AFTER INSERT OR UPDATE OR DELETE ON guard_location_assignments
       FOR EACH ROW EXECUTE FUNCTION app_guard_assignment_revokes_sessions()""",
    """CREATE FUNCTION app_actor_sessions_revoked_at() RETURNS timestamptz
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT sessions_revoked_at FROM user_credentials WHERE user_id = app_current_user_id()
       $$""",
    f"""CREATE FUNCTION app_guard_is_stationed(p_user_id uuid) RETURNS boolean
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT EXISTS (SELECT 1 FROM guard_location_assignments g WHERE g.guard_user_id = u.id)
             FROM users u
            WHERE u.id = p_user_id AND {_ACTIVE_GUARD}
       $$""",
    f"""CREATE FUNCTION app_password_reset_request_by_guard_mode(
           p_request_id uuid, p_guard_id text, p_email text, p_stationed boolean, p_subject_digest text,
           p_ttl_seconds integer)
       RETURNS void
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_user uuid;
       BEGIN
           IF p_ttl_seconds NOT BETWEEN 60 AND 86400 THEN
               RAISE EXCEPTION 'reset lifetime out of range' USING ERRCODE = 'invalid_parameter_value';
           END IF;
           SELECT u.id INTO v_user
             FROM users u
             JOIN user_credentials c ON c.user_id = u.id
            WHERE u.display_id = p_guard_id AND lower(u.email) = lower(p_email) AND {_ACTIVE_GUARD}
              AND EXISTS (SELECT 1 FROM guard_location_assignments g WHERE g.guard_user_id = u.id) = p_stationed;
           DELETE FROM password_reset_requests pr WHERE pr.created_at < now() - interval '1 day';
           INSERT INTO password_reset_requests (id, user_id, subject_digest, created_at, expires_at)
           VALUES (p_request_id, v_user, p_subject_digest, now(), now() + make_interval(secs => p_ttl_seconds));
       END $$""",
]
_GRANTED = [
    "app_actor_sessions_revoked_at()",
    "app_guard_is_stationed(uuid)",
    "app_password_reset_request_by_guard_mode(uuid, text, text, boolean, text, integer)",
]


def upgrade() -> None:
    app = _app_role()
    for statement in _STATEMENTS:
        op.execute(statement)
    op.execute("REVOKE ALL ON FUNCTION app_guard_assignment_revokes_sessions() FROM PUBLIC")
    for signature in _GRANTED:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")


def downgrade() -> None:
    """Back to 0018: no operational-mode functions and no forced sign-out on assignment changes."""
    for signature in reversed(_GRANTED):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_assignment_revokes_sessions ON guard_location_assignments")
    op.execute("DROP FUNCTION IF EXISTS app_guard_assignment_revokes_sessions()")
    op.execute("ALTER TABLE user_credentials DROP COLUMN IF EXISTS sessions_revoked_at")
