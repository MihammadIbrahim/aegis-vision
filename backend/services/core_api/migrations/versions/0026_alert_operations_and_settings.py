"""Alert operations, organization settings and camera uptime history (decision D-024).

Organization settings (Org Admin page 25)
* ``organization_settings``: one row per organization: address, time zone, date and time formats, and
  the alert defaults below. Every user of the organization reads it; only the Main Admin changes it,
  through ``app_organization_settings_save`` (which also changes the official phone number).

Alerts (Org Admin page 4, Supervisor page 4)
* ``alerts.handled_by``: the Admin or Supervisor responsible for an alert (page 4 item 12).
* Automatic escalation: an alert that no Guard acknowledged within the organization's time (HIGH 120 s
  and STANDARD 300 s by default) reaches every active Guard of its campus (``app_alerts_escalate_overdue``,
  run by the API every few seconds). The Admins and Supervisors are told even when no Guard is left to add.
* Assistance broadcast: when a Guard answers "Additional Guards Required", the alert also reaches the
  Standby Guards of the campus and the Stationed Guards of the same building (a trigger), unless the
  organization switched it off.
* New escalation scopes and recipient reasons AUTOMATIC and ASSISTANCE; new alert events AUTO_ESCALATED,
  ASSISTANCE_BROADCAST and HANDLER_ASSIGNED; notifications ALERT_AUTO_ESCALATED,
  ALERT_ASSISTANCE_BROADCAST and ALERT_HANDLER_ASSIGNED.

Cameras
* ``camera_status_events``: every change of a camera's ONLINE / OFFLINE / UNKNOWN state (a trigger on
  ``camera_status``), for uptime in Reports & Analytics.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-18
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"

DEFAULT_HIGH_SECONDS = 120
DEFAULT_STANDARD_SECONDS = 300

_KINDS_0025 = (
    "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT', 'ALERT_ASSISTANCE_REQUESTED', "
    "'ALERT_RESOLVED', 'ALERT_DISMISSED', 'MANUAL_REPORT_SUBMITTED', "
    "'GUARD_UPDATED', 'GUARD_PASSWORD_SET', 'GUARD_LOCATION_CHANGED', "
    "'CAMERA_OFFLINE', 'CAMERA_ONLINE', 'STORAGE_WARNING', 'STORAGE_CRITICAL', "
    "'INCIDENT_ASSIGNED', 'INCIDENT_STATUS_CHANGED', 'INCIDENT_INVITED'"
)
_NEW_KINDS = "'ALERT_AUTO_ESCALATED', 'ALERT_ASSISTANCE_BROADCAST', 'ALERT_HANDLER_ASSIGNED'"
_EVENTS_0015 = (
    "'CREATED', 'DETECTION_REPEATED', 'ROUTED', 'ACKNOWLEDGED', 'RESPONDING', 'ASSESSMENT', 'ESCALATED', "
    "'RESOLVED', 'DISMISSED'"
)
_NEW_EVENTS = "'AUTO_ESCALATED', 'ASSISTANCE_BROADCAST', 'HANDLER_ASSIGNED'"

_SCHEMA = [
    # --- Organization settings ------------------------------------------------------------------------------
    f"""CREATE TABLE organization_settings (
           organization_id               uuid PRIMARY KEY REFERENCES organizations (id) ON DELETE CASCADE,
           address_line                  text CONSTRAINT ck_org_settings_address CHECK (length(address_line) <= 300),
           city                          text CONSTRAINT ck_org_settings_city CHECK (length(city) <= 100),
           country                       text CONSTRAINT ck_org_settings_country CHECK (length(country) <= 100),
           time_zone                     text NOT NULL DEFAULT 'Asia/Karachi' CONSTRAINT ck_org_settings_tz
                                             CHECK (time_zone ~ '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$'
                                                    AND length(time_zone) <= 64),
           date_format                   text NOT NULL DEFAULT 'DD/MM/YYYY' CONSTRAINT ck_org_settings_date
                                             CHECK (date_format IN ('DD/MM/YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD')),
           time_format                   text NOT NULL DEFAULT '24H' CONSTRAINT ck_org_settings_time
                                             CHECK (time_format IN ('24H', '12H')),
           auto_escalation_enabled       boolean NOT NULL DEFAULT true,
           escalation_high_seconds       integer NOT NULL DEFAULT {DEFAULT_HIGH_SECONDS}
                                             CONSTRAINT ck_org_settings_high CHECK (escalation_high_seconds
                                                                                     BETWEEN 30 AND 3600),
           escalation_standard_seconds   integer NOT NULL DEFAULT {DEFAULT_STANDARD_SECONDS}
                                             CONSTRAINT ck_org_settings_standard CHECK (escalation_standard_seconds
                                                                                         BETWEEN 30 AND 86400),
           assistance_broadcast_enabled  boolean NOT NULL DEFAULT true,
           updated_by                    uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_at                    timestamptz NOT NULL DEFAULT now()
       )""",
    # --- Alerts ---------------------------------------------------------------------------------------------
    "ALTER TABLE alerts ADD COLUMN handled_by uuid REFERENCES users (id) ON DELETE SET NULL",
    "ALTER TABLE alerts ADD COLUMN handled_by_name text",
    "ALTER TABLE alerts ADD COLUMN handled_at timestamptz",
    """ALTER TABLE alerts ADD CONSTRAINT ck_alerts_handler
       CHECK ((handled_by IS NULL OR handled_by_name IS NOT NULL)
              AND (handled_by_name IS NULL) = (handled_at IS NULL))""",
    "CREATE INDEX ix_alerts_handler ON alerts (handled_by) WHERE handled_by IS NOT NULL",
    "ALTER TABLE alert_escalations DROP CONSTRAINT ck_alert_escalations_scope",
    """ALTER TABLE alert_escalations ADD CONSTRAINT ck_alert_escalations_scope
       CHECK (scope IN ('CAMPUS', 'LOCATION', 'GUARDS', 'AUTOMATIC', 'ASSISTANCE'))""",
    "ALTER TABLE alert_recipients DROP CONSTRAINT ck_alert_recipients_reason",
    """ALTER TABLE alert_recipients ADD CONSTRAINT ck_alert_recipients_reason
       CHECK (reason IN ('LOCATION', 'ESCALATION', 'AUTOMATIC', 'ASSISTANCE'))""",
    "ALTER TABLE alert_events DROP CONSTRAINT ck_alert_events_type",
    f"""ALTER TABLE alert_events ADD CONSTRAINT ck_alert_events_type
        CHECK (event_type IN ({_EVENTS_0015}, {_NEW_EVENTS}))""",
    "CREATE INDEX ix_alerts_overdue ON alerts (status, created_at) WHERE status = 'NEW'",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind
        CHECK (kind IN ({_KINDS_0025}, {_NEW_KINDS}))""",
    # --- Camera uptime history ----------------------------------------------------------------------------------
    """CREATE TABLE camera_status_events (
           id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
           camera_id        uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           state            text NOT NULL CONSTRAINT ck_camera_status_events_state
                                CHECK (state IN ('ONLINE', 'OFFLINE', 'UNKNOWN')),
           changed_at       timestamptz NOT NULL,
           CONSTRAINT fk_camera_status_events_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_camera_status_events ON camera_status_events (camera_id, changed_at)",
    """INSERT INTO camera_status_events (camera_id, organization_id, campus_id, state, changed_at)
       SELECT camera_id, organization_id, campus_id, state, state_changed_at FROM camera_status""",
]

_TABLES = ["organization_settings", "camera_status_events"]

_POLICIES = [
    """CREATE POLICY org_settings_select ON organization_settings FOR SELECT
       USING (organization_id = app_actor_org_id())""",
    f"CREATE POLICY camera_status_events_select ON camera_status_events FOR SELECT USING ({_STAFF})",
]

_GRANTS = {"organization_settings": "SELECT", "camera_status_events": "SELECT"}

# The organization's alert defaults, also when it never saved any settings.
_SETTING = "COALESCE((SELECT s.{col} FROM organization_settings s WHERE s.organization_id = {org}), {default})"

# Active Guards of the alert's campus that do not have it yet.
_NOT_RECIPIENT = """u.organization_id = a.organization_id AND u.campus_id = a.campus_id
                 AND u.role = 'GUARD' AND u.status = 'ACTIVE'
                 AND NOT EXISTS (SELECT 1 FROM alert_recipients r WHERE r.alert_id = a.id AND r.guard_user_id = u.id)"""

# Standby Guards (no location) of the campus, and Stationed Guards of the alert's building.
_ASSIST_FROM = """users u
             LEFT JOIN guard_location_assignments g ON g.guard_user_id = u.id
             LEFT JOIN floors f ON f.id = g.floor_id
             LEFT JOIN areas ar ON ar.id = g.area_id"""
_ASSIST_WHERE = f"""{_NOT_RECIPIENT}
              AND (g.guard_user_id IS NULL
                   OR (a.building_id IS NOT NULL
                       AND COALESCE(g.building_id, f.building_id, ar.building_id) = a.building_id))"""

_NOTIFY = """INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, alert_id, title)
               SELECT gen_random_uuid(), a.organization_id, a.campus_id, u.id, {kind}, a.id, {title}
                 FROM users u WHERE {where}"""

_FUNCTIONS = [
    # The Main Admin saves the organization's settings (and its official phone number); the caller's
    # password is checked by the API first. Returns NOT_MAIN_ADMIN for anyone else.
    """CREATE FUNCTION app_organization_settings_save(
           p_official_phone text, p_address_line text, p_city text, p_country text, p_time_zone text,
           p_date_format text, p_time_format text, p_auto_escalation boolean, p_high_seconds integer,
           p_standard_seconds integer, p_assistance_broadcast boolean)
       RETURNS text
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_org uuid := app_actor_org_id();
       BEGIN
           IF v_org IS NULL OR NOT app_actor_is_first_admin() THEN
               RETURN 'NOT_MAIN_ADMIN';
           END IF;
           UPDATE organizations o SET official_phone = COALESCE(p_official_phone, o.official_phone) WHERE o.id = v_org;
           INSERT INTO organization_settings AS s (organization_id, address_line, city, country, time_zone, date_format,
                                                   time_format, auto_escalation_enabled, escalation_high_seconds,
                                                   escalation_standard_seconds, assistance_broadcast_enabled,
                                                   updated_by, updated_at)
           VALUES (v_org, p_address_line, p_city, p_country, p_time_zone, p_date_format, p_time_format,
                   p_auto_escalation, p_high_seconds, p_standard_seconds, p_assistance_broadcast,
                   app_current_user_id(), now())
           ON CONFLICT (organization_id) DO UPDATE
              SET address_line = EXCLUDED.address_line, city = EXCLUDED.city, country = EXCLUDED.country,
                  time_zone = EXCLUDED.time_zone, date_format = EXCLUDED.date_format,
                  time_format = EXCLUDED.time_format, auto_escalation_enabled = EXCLUDED.auto_escalation_enabled,
                  escalation_high_seconds = EXCLUDED.escalation_high_seconds,
                  escalation_standard_seconds = EXCLUDED.escalation_standard_seconds,
                  assistance_broadcast_enabled = EXCLUDED.assistance_broadcast_enabled,
                  updated_by = EXCLUDED.updated_by, updated_at = now();
           RETURN 'SAVED';
       END $$""",
    # Run by the API in the background (no signed-in user): alerts nobody acknowledged in time reach every
    # active Guard of their campus. Each alert is escalated automatically once. Returns how many it handled.
    f"""CREATE FUNCTION app_alerts_escalate_overdue(p_limit integer) RETURNS integer
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           a alerts%ROWTYPE;
           v_done integer := 0;
           v_added integer;
           v_escalation uuid;
           v_seconds integer;
       BEGIN
           FOR a IN
               SELECT x.* FROM alerts x
                JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
                WHERE x.status = 'NEW'
                  AND {_SETTING.format(col="auto_escalation_enabled", org="x.organization_id", default="true")}
                  AND x.created_at <= now() - make_interval(secs => CASE x.priority
                      WHEN 'HIGH' THEN {
        _SETTING.format(col="escalation_high_seconds", org="x.organization_id", default=DEFAULT_HIGH_SECONDS)
    }
                      ELSE {
        _SETTING.format(col="escalation_standard_seconds", org="x.organization_id", default=DEFAULT_STANDARD_SECONDS)
    } END)
                  AND NOT EXISTS (SELECT 1 FROM alert_recipients r
                                   WHERE r.alert_id = x.id AND r.state <> 'NOTIFIED')
                  AND NOT EXISTS (SELECT 1 FROM alert_events e
                                   WHERE e.alert_id = x.id AND e.event_type = 'AUTO_ESCALATED')
                ORDER BY x.created_at
                LIMIT greatest(1, least(p_limit, 500))
                FOR UPDATE OF x SKIP LOCKED
           LOOP
               v_seconds := floor(EXTRACT(EPOCH FROM (now() - a.created_at)))::integer;
               SELECT count(*) INTO v_added FROM users u WHERE {_NOT_RECIPIENT};
               v_escalation := NULL;
               IF v_added > 0 THEN
                   v_escalation := gen_random_uuid();
                   INSERT INTO alert_escalations (id, alert_id, organization_id, campus_id, escalated_by, scope,
                                                  note, added_count)
                   VALUES (v_escalation, a.id, a.organization_id, a.campus_id, NULL, 'AUTOMATIC',
                           'No Guard acknowledged within ' || v_seconds || ' seconds.', v_added);
                   {
        _NOTIFY.format(
            kind="'ALERT_AUTO_ESCALATED'",
            title="'Escalated, no Guard acknowledged: ' || a.camera_name",
            where=_NOT_RECIPIENT,
        )
    };
                   INSERT INTO alert_recipients (alert_id, guard_user_id, organization_id, campus_id, reason,
                                                 escalation_id)
                   SELECT a.id, u.id, a.organization_id, a.campus_id, 'AUTOMATIC', v_escalation
                     FROM users u WHERE {_NOT_RECIPIENT};
                   UPDATE alerts x SET status = 'ESCALATED', updated_at = now() WHERE x.id = a.id;
               END IF;
               INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_user_id, actor_role,
                                         event_type, from_status, to_status, details)
               VALUES (gen_random_uuid(), a.id, a.organization_id, a.campus_id, NULL, 'SYSTEM', 'AUTO_ESCALATED',
                       a.status, CASE WHEN v_added > 0 THEN 'ESCALATED' ELSE a.status END,
                       jsonb_build_object('added_guards', v_added, 'after_seconds', v_seconds));
               {
        _NOTIFY.format(
            kind="'ALERT_AUTO_ESCALATED'",
            title="CASE WHEN v_added > 0 THEN 'No Guard acknowledged; sent to all Guards: '"
            " ELSE 'No Guard acknowledged and no other Guard is available: ' END || a.camera_name",
            where="u.organization_id = a.organization_id AND u.campus_id = a.campus_id "
            "AND u.role IN ('ADMIN', 'SUPERVISOR') AND u.status = 'ACTIVE'",
        )
    };
               INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                    target_type, target_id, metadata)
               VALUES (gen_random_uuid(), a.organization_id, a.campus_id, NULL, 'SYSTEM', 'ALERT_AUTO_ESCALATED',
                       'ALERT', a.id, jsonb_build_object('added_guards', v_added, 'after_seconds', v_seconds));
               v_done := v_done + 1;
           END LOOP;
           RETURN v_done;
       END $$""",
    # "Additional Guards Required" from a Guard: the alert also reaches the Standby Guards of the campus and
    # the Stationed Guards of the same building (decision D-024), unless the organization switched it off.
    f"""CREATE FUNCTION app_alert_assistance_broadcast() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           a alerts%ROWTYPE;
           v_added integer;
           v_escalation uuid := gen_random_uuid();
       BEGIN
           SELECT * INTO a FROM alerts x WHERE x.id = NEW.alert_id;
           IF a.id IS NULL OR a.status IN ('RESOLVED', 'DISMISSED')
              OR NOT {_SETTING.format(col="assistance_broadcast_enabled", org="a.organization_id", default="true")}
           THEN
               RETURN NEW;
           END IF;
           SELECT count(*) INTO v_added FROM {_ASSIST_FROM} WHERE {_ASSIST_WHERE};
           IF v_added = 0 THEN
               RETURN NEW;
           END IF;
           INSERT INTO alert_escalations (id, alert_id, organization_id, campus_id, escalated_by, scope, note,
                                          added_count)
           VALUES (v_escalation, a.id, a.organization_id, a.campus_id, NEW.guard_user_id, 'ASSISTANCE',
                   'Additional Guards Required', v_added);
           -- Notified first: once they are recipients they no longer match.
           INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, alert_id, title)
           SELECT gen_random_uuid(), a.organization_id, a.campus_id, u.id, 'ALERT_ASSISTANCE_BROADCAST', a.id,
                  'Assistance requested: ' || a.camera_name
             FROM {_ASSIST_FROM} WHERE {_ASSIST_WHERE};
           INSERT INTO alert_recipients (alert_id, guard_user_id, organization_id, campus_id, reason, escalation_id)
           SELECT a.id, u.id, a.organization_id, a.campus_id, 'ASSISTANCE', v_escalation
             FROM {_ASSIST_FROM} WHERE {_ASSIST_WHERE};
           INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_user_id, actor_role, event_type,
                                     from_status, to_status, details)
           VALUES (gen_random_uuid(), a.id, a.organization_id, a.campus_id, NULL, 'SYSTEM', 'ASSISTANCE_BROADCAST',
                   a.status, a.status,
                   jsonb_build_object('added_guards', v_added, 'requested_by', NEW.guard_user_id));
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                target_type, target_id, metadata)
           VALUES (gen_random_uuid(), a.organization_id, a.campus_id, NULL, 'SYSTEM', 'ALERT_ASSISTANCE_BROADCAST',
                   'ALERT', a.id, jsonb_build_object('added_guards', v_added));
           RETURN NEW;
       END $$""",
    # Deferred to the end of the Guard's transaction, so the broadcast follows the Guard's own assessment
    # in the alert history.
    """CREATE CONSTRAINT TRIGGER trg_alert_recipients_assistance AFTER UPDATE OF assessment ON alert_recipients
       DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW WHEN (NEW.assessment = 'ADDITIONAL_GUARDS_REQUIRED'
                          AND OLD.assessment IS DISTINCT FROM NEW.assessment)
       EXECUTE FUNCTION app_alert_assistance_broadcast()""",
    # Every state change of a camera is kept for uptime figures.
    """CREATE FUNCTION app_camera_status_history() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           IF TG_OP = 'INSERT' OR OLD.state IS DISTINCT FROM NEW.state THEN
               INSERT INTO camera_status_events (camera_id, organization_id, campus_id, state, changed_at)
               VALUES (NEW.camera_id, NEW.organization_id, NEW.campus_id, NEW.state,
                       COALESCE(NEW.state_changed_at, now()));
           END IF;
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_camera_status_history AFTER INSERT OR UPDATE OF state ON camera_status
       FOR EACH ROW EXECUTE FUNCTION app_camera_status_history()""",
]

_FUNCTION_SIGNATURES = [
    "app_organization_settings_save(text, text, text, text, text, text, text, boolean, integer, integer, boolean)",
    "app_alerts_escalate_overdue(integer)",
]
_TRIGGER_FUNCTIONS = ["app_alert_assistance_broadcast()", "app_camera_status_history()"]


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
    for signature in _TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_camera_status_history ON camera_status")
    op.execute("DROP TRIGGER IF EXISTS trg_alert_recipients_assistance ON alert_recipients")
    for signature in _FUNCTION_SIGNATURES + _TRIGGER_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(f"DELETE FROM user_notifications WHERE kind IN ({_NEW_KINDS})")
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind")
    op.execute(
        f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({_KINDS_0025}))"
    )
    # What 0015 cannot express goes: automatic and assistance recipients, escalations and events.
    op.execute("DELETE FROM alert_recipients WHERE reason IN ('AUTOMATIC', 'ASSISTANCE')")
    op.execute("DELETE FROM alert_escalations WHERE scope IN ('AUTOMATIC', 'ASSISTANCE')")
    op.execute(f"DELETE FROM alert_events WHERE event_type IN ({_NEW_EVENTS})")
    op.execute("DROP INDEX IF EXISTS ix_alerts_overdue")
    op.execute("ALTER TABLE alert_events DROP CONSTRAINT ck_alert_events_type")
    op.execute(f"ALTER TABLE alert_events ADD CONSTRAINT ck_alert_events_type CHECK (event_type IN ({_EVENTS_0015}))")
    op.execute("ALTER TABLE alert_recipients DROP CONSTRAINT ck_alert_recipients_reason")
    op.execute(
        "ALTER TABLE alert_recipients ADD CONSTRAINT ck_alert_recipients_reason "
        "CHECK (reason IN ('LOCATION', 'ESCALATION'))"
    )
    op.execute("ALTER TABLE alert_escalations DROP CONSTRAINT ck_alert_escalations_scope")
    op.execute(
        "ALTER TABLE alert_escalations ADD CONSTRAINT ck_alert_escalations_scope "
        "CHECK (scope IN ('CAMPUS', 'LOCATION', 'GUARDS'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_alerts_handler")
    op.execute("ALTER TABLE alerts DROP CONSTRAINT IF EXISTS ck_alerts_handler")
    for column in ("handled_at", "handled_by_name", "handled_by"):
        op.execute(f"ALTER TABLE alerts DROP COLUMN IF EXISTS {column}")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
