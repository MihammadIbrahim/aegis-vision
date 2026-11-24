"""Incidents & Investigations (Org Admin page 6, Supervisor page 6; requirements Sections 15.5 and 16;
decision D-024), and links from evidence and manual reports to incidents.

Incidents
* ``incidents``: a case per campus with a campus-wide number (INC-000001), title, description, status
  (OPEN → UNDER_INVESTIGATION → RESPONDING → RESOLVED / DISMISSED; a closed case can be reopened),
  priority, when it happened, an optional camera and location (names copied, so the record survives
  their removal), the assigned Admin or Supervisor and who opened it (names copied as well).
* ``incident_alerts``: AI alerts linked to a case. ``manual_reports.incident_id`` and
  ``evidence_items.incident_id`` link Guards' reports and saved evidence (one case each).
* ``incident_events``: the investigation history (every change, link and note); insert-only.
* ``incident_participants``: Guards invited to the case chat. Guards who received a linked alert take
  part as well (requirements 15.5).
* ``incident_messages``: the case chat; insert-only.
Admins and Supervisors of the campus manage cases; nothing is ever deleted (requirements 16: "removing a
user account will not automatically delete linked investigation history"). Guards read only the cases
they take part in, and only their chat. The Super Admin (platform role) has no access.

Evidence
* ``tags`` and the camera's location at the time of saving (names copied) for search and filters.
* Fix of migration 0023: ``fk_evidence_alert`` / ``fk_evidence_camera`` set only the alert / camera to
  NULL when it is deleted (``SET NULL (column)``). Before, PostgreSQL also tried to empty the
  organization and campus, so deleting a camera with saved evidence, or erasing a campus whose alerts
  had evidence, failed.

Notifications: INCIDENT_ASSIGNED, INCIDENT_STATUS_CHANGED and INCIDENT_INVITED, targeting the case.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-18
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"
_GUARD = f"{_CAMPUS} AND app_actor_role() = 'GUARD'"

STATUSES = ("OPEN", "UNDER_INVESTIGATION", "RESPONDING", "RESOLVED", "DISMISSED")
EVENT_KINDS = (
    "CREATED",
    "DETAILS_UPDATED",
    "STATUS_CHANGED",
    "ASSIGNED",
    "UNASSIGNED",
    "NOTE_ADDED",
    "ALERT_LINKED",
    "ALERT_UNLINKED",
    "REPORT_LINKED",
    "REPORT_UNLINKED",
    "EVIDENCE_LINKED",
    "EVIDENCE_UNLINKED",
    "PERSON_SEARCH_LINKED",
    "PERSON_SEARCH_REVIEWED",
    "PARTICIPANT_ADDED",
    "PARTICIPANT_REMOVED",
)
_STATUSES = ", ".join(f"'{s}'" for s in STATUSES)
_EVENT_KINDS = ", ".join(f"'{k}'" for k in EVENT_KINDS)

_KINDS_0023 = (
    "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT', 'ALERT_ASSISTANCE_REQUESTED', "
    "'ALERT_RESOLVED', 'ALERT_DISMISSED', 'MANUAL_REPORT_SUBMITTED', "
    "'GUARD_UPDATED', 'GUARD_PASSWORD_SET', 'GUARD_LOCATION_CHANGED', "
    "'CAMERA_OFFLINE', 'CAMERA_ONLINE', 'STORAGE_WARNING', 'STORAGE_CRITICAL'"
)
_NEW_KINDS = "'INCIDENT_ASSIGNED', 'INCIDENT_STATUS_CHANGED', 'INCIDENT_INVITED'"
_STORAGE_KINDS = "('STORAGE_WARNING', 'STORAGE_CRITICAL')"
_TARGETS_0023 = "alert_id, manual_report_id, subject_user_id, camera_id"
_TARGETS = "alert_id, manual_report_id, subject_user_id, camera_id, incident_id"

_LOC_FK = """CONSTRAINT fk_{t}_building FOREIGN KEY (building_id) REFERENCES buildings (id) ON DELETE SET NULL,
           CONSTRAINT fk_{t}_floor FOREIGN KEY (floor_id) REFERENCES floors (id) ON DELETE SET NULL,
           CONSTRAINT fk_{t}_area FOREIGN KEY (area_id) REFERENCES areas (id) ON DELETE SET NULL"""

_SCHEMA = [
    # --- The 0023 foreign keys: only the referencing column becomes NULL ------------------------------------
    "ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_alert",
    """ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_alert FOREIGN KEY (organization_id, campus_id, alert_id)
       REFERENCES alerts (organization_id, campus_id, id) ON DELETE SET NULL (alert_id)""",
    "ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_camera",
    """ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_camera FOREIGN KEY (organization_id, campus_id, camera_id)
       REFERENCES cameras (organization_id, campus_id, id) ON DELETE SET NULL (camera_id)""",
    # --- Incidents ------------------------------------------------------------------------------------------
    f"""CREATE TABLE incidents (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           number           integer NOT NULL CONSTRAINT ck_incidents_number CHECK (number >= 1),
           title            text NOT NULL CONSTRAINT ck_incidents_title CHECK (length(btrim(title)) BETWEEN 3 AND 200),
           description      text NOT NULL DEFAULT '' CONSTRAINT ck_incidents_description
                                CHECK (length(description) <= 5000),
           status           text NOT NULL DEFAULT 'OPEN' CONSTRAINT ck_incidents_status CHECK (status IN ({_STATUSES})),
           priority         text NOT NULL DEFAULT 'STANDARD' CONSTRAINT ck_incidents_priority
                                CHECK (priority IN ('HIGH', 'STANDARD')),
           occurred_at      timestamptz NOT NULL,
           camera_id        uuid,
           camera_name      text CONSTRAINT ck_incidents_camera_name CHECK (length(camera_name) <= 200),
           building_id      uuid,
           floor_id         uuid,
           area_id          uuid,
           building_name    text,
           floor_name       text,
           area_name        text,
           assigned_to      uuid REFERENCES users (id) ON DELETE SET NULL,
           assigned_name    text,
           created_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           created_by_name  text NOT NULL,
           resolution_note  text CONSTRAINT ck_incidents_resolution CHECK (length(resolution_note) <= 2000),
           closed_at        timestamptz,
           created_at       timestamptz NOT NULL DEFAULT now(),
           updated_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_incidents_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_incidents_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE SET NULL (camera_id),
           {_LOC_FK.format(t="incidents")},
           CONSTRAINT uq_incidents_number UNIQUE (organization_id, campus_id, number),
           CONSTRAINT uq_incidents_scope UNIQUE (organization_id, campus_id, id),
           CONSTRAINT ck_incidents_closed CHECK ((status IN ('RESOLVED', 'DISMISSED')) = (closed_at IS NOT NULL)),
           CONSTRAINT ck_incidents_assignee CHECK (assigned_to IS NULL OR assigned_name IS NOT NULL)
       )""",
    "CREATE INDEX ix_incidents_campus ON incidents (organization_id, campus_id, status, updated_at DESC)",
    """CREATE TABLE incident_alerts (
           incident_id      uuid NOT NULL,
           alert_id         uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           linked_by        uuid REFERENCES users (id) ON DELETE SET NULL,
           linked_by_name   text NOT NULL,
           linked_at        timestamptz NOT NULL DEFAULT now(),
           PRIMARY KEY (incident_id, alert_id),
           CONSTRAINT fk_incident_alerts_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_incident_alerts_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_incident_alerts_alert ON incident_alerts (alert_id)",
    f"""CREATE TABLE incident_events (
           id               uuid PRIMARY KEY,
           incident_id      uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           kind             text NOT NULL CONSTRAINT ck_incident_events_kind CHECK (kind IN ({_EVENT_KINDS})),
           actor_user_id    uuid REFERENCES users (id) ON DELETE SET NULL,
           actor_name       text NOT NULL,
           actor_role       text NOT NULL CONSTRAINT ck_incident_events_role
                                CHECK (actor_role IN ('ADMIN', 'SUPERVISOR', 'GUARD', 'SYSTEM')),
           from_status      text,
           to_status        text,
           note             text CONSTRAINT ck_incident_events_note CHECK (length(note) <= 4000),
           details          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
           -- The real moment and order of each step, also within one transaction.
           occurred_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
           seq              bigint GENERATED ALWAYS AS IDENTITY,
           CONSTRAINT fk_incident_events_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_incident_events_incident ON incident_events (incident_id, seq)",
    """CREATE TABLE incident_participants (
           incident_id      uuid NOT NULL,
           guard_user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           added_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           added_at         timestamptz NOT NULL DEFAULT now(),
           PRIMARY KEY (incident_id, guard_user_id),
           CONSTRAINT fk_incident_participants_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_incident_participants_guard FOREIGN KEY (organization_id, campus_id, guard_user_id)
               REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_incident_participants_guard ON incident_participants (guard_user_id)",
    """CREATE TABLE incident_messages (
           id               uuid PRIMARY KEY,
           incident_id      uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           sender_user_id   uuid REFERENCES users (id) ON DELETE SET NULL,
           sender_name      text NOT NULL,
           sender_role      text NOT NULL CONSTRAINT ck_incident_messages_role
                                CHECK (sender_role IN ('ADMIN', 'SUPERVISOR', 'GUARD')),
           body             text NOT NULL CONSTRAINT ck_incident_messages_body
                                CHECK (length(btrim(body)) BETWEEN 1 AND 2000),
           created_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
           CONSTRAINT fk_incident_messages_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_incident_messages_incident ON incident_messages (incident_id, created_at)",
    # --- Links from manual reports and evidence ---------------------------------------------------------------
    "ALTER TABLE manual_reports ADD COLUMN incident_id uuid",
    """ALTER TABLE manual_reports ADD CONSTRAINT fk_manual_reports_incident
       FOREIGN KEY (organization_id, campus_id, incident_id)
       REFERENCES incidents (organization_id, campus_id, id) ON DELETE SET NULL (incident_id)""",
    "CREATE INDEX ix_manual_reports_incident ON manual_reports (incident_id) WHERE incident_id IS NOT NULL",
    "ALTER TABLE evidence_items ADD COLUMN incident_id uuid",
    """ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_incident
       FOREIGN KEY (organization_id, campus_id, incident_id)
       REFERENCES incidents (organization_id, campus_id, id) ON DELETE SET NULL (incident_id)""",
    "CREATE INDEX ix_evidence_incident ON evidence_items (incident_id) WHERE incident_id IS NOT NULL",
    """ALTER TABLE evidence_items ADD COLUMN tags text[] NOT NULL DEFAULT '{}'
       CONSTRAINT ck_evidence_tags CHECK (cardinality(tags) <= 10)""",
    "ALTER TABLE evidence_items ADD COLUMN building_id uuid",
    "ALTER TABLE evidence_items ADD COLUMN floor_id uuid",
    "ALTER TABLE evidence_items ADD COLUMN area_id uuid",
    "ALTER TABLE evidence_items ADD COLUMN building_name text",
    "ALTER TABLE evidence_items ADD COLUMN floor_name text",
    "ALTER TABLE evidence_items ADD COLUMN area_name text",
    "ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_building FOREIGN KEY (building_id) "
    "REFERENCES buildings (id) ON DELETE SET NULL",
    "ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_floor FOREIGN KEY (floor_id) "
    "REFERENCES floors (id) ON DELETE SET NULL",
    "ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_area FOREIGN KEY (area_id) "
    "REFERENCES areas (id) ON DELETE SET NULL",
    "CREATE INDEX ix_evidence_tags ON evidence_items USING gin (tags)",
    # Existing evidence gets its camera's current location (the best that is known).
    """UPDATE evidence_items e
          SET building_id = b.id, floor_id = f.id, area_id = a.id,
              building_name = b.name, floor_name = f.name, area_name = a.name
         FROM cameras c
         LEFT JOIN areas a ON a.id = c.area_id
         LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, a.floor_id)
         LEFT JOIN buildings b ON b.id = COALESCE(c.building_id, f.building_id, a.building_id)
        WHERE c.id = e.camera_id AND b.id IS NOT NULL""",
    # --- Notifications about cases ------------------------------------------------------------------------
    "ALTER TABLE user_notifications ADD COLUMN incident_id uuid",
    """ALTER TABLE user_notifications ADD CONSTRAINT fk_user_notifications_incident
       FOREIGN KEY (organization_id, campus_id, incident_id)
       REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target
       CHECK (CASE WHEN kind IN {_STORAGE_KINDS}
                   THEN num_nonnulls({_TARGETS}) = 0
                   ELSE num_nonnulls({_TARGETS}) = 1 END)""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind
       CHECK (kind IN ({_KINDS_0023}, {_NEW_KINDS}))""",
    "CREATE INDEX ix_user_notifications_incident ON user_notifications (incident_id) WHERE incident_id IS NOT NULL",
]

_TABLES = ["incidents", "incident_alerts", "incident_events", "incident_participants", "incident_messages"]

# Whether the signed-in Guard takes part in a case: invited, or a recipient of one of its alerts.
_PARTICIPANT = "app_incident_participant({col})"

_POLICIES = [
    f"""CREATE POLICY incidents_select ON incidents FOR SELECT
        USING (({_STAFF}) OR ({_GUARD} AND {_PARTICIPANT.format(col="id")}))""",
    f"""CREATE POLICY incidents_insert ON incidents FOR INSERT
        WITH CHECK ({_STAFF} AND created_by = app_current_user_id())""",
    f"CREATE POLICY incidents_update ON incidents FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY incident_alerts_select ON incident_alerts FOR SELECT USING ({_STAFF})",
    f"""CREATE POLICY incident_alerts_insert ON incident_alerts FOR INSERT
        WITH CHECK ({_STAFF} AND linked_by = app_current_user_id())""",
    f"CREATE POLICY incident_alerts_delete ON incident_alerts FOR DELETE USING ({_STAFF})",
    f"CREATE POLICY incident_events_select ON incident_events FOR SELECT USING ({_STAFF})",
    f"""CREATE POLICY incident_events_insert ON incident_events FOR INSERT
        WITH CHECK ({_STAFF} AND actor_user_id = app_current_user_id() AND actor_role = app_actor_role())""",
    f"""CREATE POLICY incident_participants_select ON incident_participants FOR SELECT
        USING (({_STAFF}) OR ({_GUARD} AND guard_user_id = app_current_user_id()))""",
    f"""CREATE POLICY incident_participants_insert ON incident_participants FOR INSERT
        WITH CHECK ({_STAFF} AND added_by = app_current_user_id())""",
    f"CREATE POLICY incident_participants_delete ON incident_participants FOR DELETE USING ({_STAFF})",
    f"""CREATE POLICY incident_messages_select ON incident_messages FOR SELECT
        USING (({_STAFF}) OR ({_GUARD} AND {_PARTICIPANT.format(col="incident_id")}))""",
    f"""CREATE POLICY incident_messages_insert ON incident_messages FOR INSERT
        WITH CHECK ((({_STAFF}) OR ({_GUARD} AND {_PARTICIPANT.format(col="incident_id")}))
                    AND sender_user_id = app_current_user_id() AND sender_role = app_actor_role())""",
]

_GRANTS = {
    "incidents": "SELECT, INSERT, UPDATE",
    "incident_alerts": "SELECT, INSERT, DELETE",
    "incident_events": "SELECT, INSERT",
    "incident_participants": "SELECT, INSERT, DELETE",
    "incident_messages": "SELECT, INSERT",
}

_FUNCTIONS = [
    # Runs as the owner so the policies above can ask it without recursing into each other's rules.
    """CREATE FUNCTION app_incident_participant(p_incident uuid) RETURNS boolean
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT EXISTS (SELECT 1 FROM incident_participants p
                           WHERE p.incident_id = p_incident AND p.guard_user_id = app_current_user_id())
               OR EXISTS (SELECT 1 FROM incident_alerts ia
                            JOIN alert_recipients r ON r.alert_id = ia.alert_id
                           WHERE ia.incident_id = p_incident AND r.guard_user_id = app_current_user_id())
       $$""",
]

_FUNCTION_SIGNATURES = ["app_incident_participant(uuid)"]


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA:
        op.execute(statement)
    for statement in _FUNCTIONS:
        op.execute(statement)
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for statement in _POLICIES:
        op.execute(statement)
    for table, rights in _GRANTS.items():
        op.execute(f"GRANT {rights} ON {table} TO {app}")


def downgrade() -> None:
    op.execute(f"DELETE FROM user_notifications WHERE kind IN ({_NEW_KINDS}) OR incident_id IS NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_user_notifications_incident")
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind")
    op.execute(
        f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({_KINDS_0023}))"
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target")
    op.execute(
        f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target
            CHECK (CASE WHEN kind IN {_STORAGE_KINDS}
                        THEN num_nonnulls({_TARGETS_0023}) = 0
                        ELSE num_nonnulls({_TARGETS_0023}) = 1 END)"""
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT fk_user_notifications_incident")
    op.execute("ALTER TABLE user_notifications DROP COLUMN incident_id")
    op.execute("DROP INDEX IF EXISTS ix_evidence_tags")
    for column in ("area_name", "floor_name", "building_name", "area_id", "floor_id", "building_id", "tags"):
        op.execute(f"ALTER TABLE evidence_items DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT IF EXISTS fk_evidence_incident")
    op.execute("ALTER TABLE evidence_items DROP COLUMN incident_id")
    op.execute("ALTER TABLE manual_reports DROP CONSTRAINT IF EXISTS fk_manual_reports_incident")
    op.execute("ALTER TABLE manual_reports DROP COLUMN incident_id")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    # The 0023 definitions, exactly.
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_alert")
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_alert "
        "FOREIGN KEY (organization_id, campus_id, alert_id) "
        "REFERENCES alerts (organization_id, campus_id, id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT fk_evidence_camera")
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT fk_evidence_camera "
        "FOREIGN KEY (organization_id, campus_id, camera_id) "
        "REFERENCES cameras (organization_id, campus_id, id) ON DELETE SET NULL"
    )
