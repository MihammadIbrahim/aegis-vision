"""Alert Management: AI alerts, location routing, Guard actions, escalation, alert chat, manual
reports, in-app notifications and the organization audit log (decision D-013).

Tables (all scoped to one campus by composite foreign keys and Row-Level Security):

* ``alerts``: one per AI detection event, created only by ``app_alert_ingest`` (the detection
  intake). Type WEAPON / FIGHT_VIOLENCE (HIGH priority, enforced) or RESTRICTED_AREA (STANDARD).
  Status NEW → ACKNOWLEDGED → RESPONDING → RESOLVED, plus ESCALATED and DISMISSED; a trigger
  refuses any other change, and RESOLVED / DISMISSED are final. The camera's location at detection
  time is copied (IDs and names), so history survives later location changes.
* ``alert_recipients``: the Guards an alert was sent to. Each has their own NOTIFIED /
  ACKNOWLEDGED / RESPONDING state and optional assessment (UNDER_CONTROL /
  ADDITIONAL_GUARDS_REQUIRED). Reason LOCATION (routing), ESCALATION.
* ``alert_escalations``: who widened the recipients, to what (CAMPUS / LOCATION / GUARDS).
* ``alert_events``: the response history. A per-actor ``client_action_id`` makes queued offline
  actions safe to resend.
* ``alert_messages``: the alert's chat (staff of the campus and the alert's Guards).
* ``manual_reports``: Guard reports, separate from alerts; idempotent by ``client_report_id``.
* ``user_notifications``: in-app notifications (no push).
* ``organization_audit_logs``: insert-only for the app role; no secrets, no message text.

Functions (SECURITY DEFINER, pinned search_path):
* ``app_alert_ingest``: detection → alert (deduplicated), routing, notifications, audit.
* ``app_alert_guard_action``: a recipient Guard acknowledges, responds or reports an assessment.
* ``app_manual_report_submit``: a Guard's report (location checked), notifications, audit.
* ``app_guard_campus_locations``: the location names a Guard picks from (dropdowns only).

Cameras with alert history can no longer be deleted (foreign key). Campus deletion and the export
are extended (from 0014).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-31
"""

import importlib.util
import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
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


def _recipient(column: str) -> str:
    return (
        f"({_CAMPUS} AND EXISTS (SELECT 1 FROM alert_recipients r "
        f"WHERE r.alert_id = {column} AND r.guard_user_id = app_current_user_id()))"
    )


_OPEN = "('NEW', 'ACKNOWLEDGED', 'RESPONDING', 'ESCALATED')"
_LOC_FK = """CONSTRAINT fk_{t}_building FOREIGN KEY (building_id) REFERENCES buildings (id) ON DELETE SET NULL,
           CONSTRAINT fk_{t}_floor FOREIGN KEY (floor_id) REFERENCES floors (id) ON DELETE SET NULL,
           CONSTRAINT fk_{t}_area FOREIGN KEY (area_id) REFERENCES areas (id) ON DELETE SET NULL"""

_SCHEMA = [
    "ALTER TABLE cameras ADD CONSTRAINT uq_cameras_scope UNIQUE (organization_id, campus_id, id)",
    "CREATE UNIQUE INDEX uq_users_scope ON users (organization_id, campus_id, id)",
    f"""CREATE TABLE alerts (
           id                uuid PRIMARY KEY,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           camera_id         uuid NOT NULL,
           alert_type        text NOT NULL CONSTRAINT ck_alerts_type
                                 CHECK (alert_type IN ('WEAPON', 'FIGHT_VIOLENCE', 'RESTRICTED_AREA')),
           priority          text NOT NULL CONSTRAINT ck_alerts_priority CHECK (priority IN ('HIGH', 'STANDARD')),
           status            text NOT NULL DEFAULT 'NEW' CONSTRAINT ck_alerts_status
                                 CHECK (status IN ('NEW', 'ACKNOWLEDGED', 'RESPONDING', 'ESCALATED', 'RESOLVED',
                                                   'DISMISSED')),
           confidence        numeric(5, 4) CONSTRAINT ck_alerts_confidence CHECK (confidence BETWEEN 0 AND 1),
           detected_at       timestamptz NOT NULL,
           last_detected_at  timestamptz NOT NULL,
           detection_count   integer NOT NULL DEFAULT 1 CONSTRAINT ck_alerts_count CHECK (detection_count >= 1),
           detection_id      text CONSTRAINT ck_alerts_detection_id CHECK (length(detection_id) BETWEEN 1 AND 128),
           snapshot_ref      text CONSTRAINT ck_alerts_snapshot_ref CHECK (length(snapshot_ref) <= 1024),
           camera_name       text NOT NULL,
           building_id       uuid,
           floor_id          uuid,
           area_id           uuid,
           building_name     text,
           building_kind     text,
           floor_name        text,
           area_name         text,
           closed_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           closed_at         timestamptz,
           close_note        text CONSTRAINT ck_alerts_close_note CHECK (length(close_note) <= 1000),
           created_at        timestamptz NOT NULL DEFAULT now(),
           updated_at        timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_alerts_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id),
           {_LOC_FK.format(t="alerts")},
           -- Weapon and fight/violence alerts are always high priority (requirements 14.3, 14.4, 15.1).
           CONSTRAINT ck_alerts_high_priority
               CHECK ((alert_type IN ('WEAPON', 'FIGHT_VIOLENCE')) = (priority = 'HIGH')),
           CONSTRAINT ck_alerts_closed CHECK ((status IN ('RESOLVED', 'DISMISSED')) = (closed_at IS NOT NULL)),
           CONSTRAINT uq_alerts_scope UNIQUE (organization_id, campus_id, id)
       )""",
    "CREATE UNIQUE INDEX uq_alerts_detection ON alerts (camera_id, detection_id) WHERE detection_id IS NOT NULL",
    "CREATE INDEX ix_alerts_campus_status ON alerts (organization_id, campus_id, status, created_at DESC)",
    f"CREATE INDEX ix_alerts_open ON alerts (camera_id, alert_type, last_detected_at) WHERE status IN {_OPEN}",
    # Only the documented lifecycle; closed alerts stay closed.
    """CREATE FUNCTION app_alert_status_guard() RETURNS trigger
       LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
       BEGIN
           IF NEW.status = OLD.status THEN
               RETURN NEW;
           END IF;
           IF NOT (
               (OLD.status = 'NEW'
                AND NEW.status IN ('ACKNOWLEDGED', 'RESPONDING', 'ESCALATED', 'RESOLVED', 'DISMISSED'))
               OR (OLD.status = 'ACKNOWLEDGED' AND NEW.status IN ('RESPONDING', 'ESCALATED', 'RESOLVED', 'DISMISSED'))
               OR (OLD.status = 'RESPONDING' AND NEW.status IN ('ESCALATED', 'RESOLVED', 'DISMISSED'))
               OR (OLD.status = 'ESCALATED' AND NEW.status IN ('RESOLVED', 'DISMISSED'))
           ) THEN
               RAISE EXCEPTION 'alert status cannot change from % to %', OLD.status, NEW.status
                   USING ERRCODE = '23514', CONSTRAINT = 'ck_alerts_transition';
           END IF;
           NEW.updated_at := now();
           RETURN NEW;
       END $$""",
    """CREATE TRIGGER trg_alert_status_guard BEFORE UPDATE OF status ON alerts
       FOR EACH ROW EXECUTE FUNCTION app_alert_status_guard()""",
    """CREATE TABLE alert_escalations (
           id               uuid PRIMARY KEY,
           alert_id         uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           escalated_by     uuid REFERENCES users (id) ON DELETE SET NULL,
           scope            text NOT NULL CONSTRAINT ck_alert_escalations_scope
                                CHECK (scope IN ('CAMPUS', 'LOCATION', 'GUARDS')),
           building_id      uuid,
           floor_id         uuid,
           area_id          uuid,
           note             text CONSTRAINT ck_alert_escalations_note CHECK (length(note) <= 1000),
           added_count      integer NOT NULL CONSTRAINT ck_alert_escalations_added CHECK (added_count >= 1),
           created_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_alert_escalations_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_alert_escalations_building FOREIGN KEY (building_id) REFERENCES buildings (id)
               ON DELETE SET NULL,
           CONSTRAINT fk_alert_escalations_floor FOREIGN KEY (floor_id) REFERENCES floors (id) ON DELETE SET NULL,
           CONSTRAINT fk_alert_escalations_area FOREIGN KEY (area_id) REFERENCES areas (id) ON DELETE SET NULL
       )""",
    """CREATE TABLE alert_recipients (
           alert_id         uuid NOT NULL,
           guard_user_id    uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           guard_role       text NOT NULL DEFAULT 'GUARD'
                                CONSTRAINT ck_alert_recipients_role CHECK (guard_role = 'GUARD'),
           reason           text NOT NULL
                                CONSTRAINT ck_alert_recipients_reason CHECK (reason IN ('LOCATION', 'ESCALATION')),
           escalation_id    uuid REFERENCES alert_escalations (id) ON DELETE SET NULL,
           state            text NOT NULL DEFAULT 'NOTIFIED' CONSTRAINT ck_alert_recipients_state
                                CHECK (state IN ('NOTIFIED', 'ACKNOWLEDGED', 'RESPONDING')),
           assessment       text CONSTRAINT ck_alert_recipients_assessment
                                CHECK (assessment IN ('UNDER_CONTROL', 'ADDITIONAL_GUARDS_REQUIRED')),
           added_at         timestamptz NOT NULL DEFAULT now(),
           acknowledged_at  timestamptz,
           responding_at    timestamptz,
           PRIMARY KEY (alert_id, guard_user_id),
           CONSTRAINT fk_alert_recipients_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_alert_recipients_guard FOREIGN KEY (organization_id, campus_id, guard_user_id, guard_role)
               REFERENCES users (organization_id, campus_id, id, role) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_alert_recipients_guard ON alert_recipients (guard_user_id)",
    """CREATE TABLE alert_events (
           id                uuid PRIMARY KEY,
           alert_id          uuid NOT NULL,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           actor_user_id     uuid REFERENCES users (id) ON DELETE SET NULL,
           actor_role        text NOT NULL CONSTRAINT ck_alert_events_role
                                 CHECK (actor_role IN ('ADMIN', 'SUPERVISOR', 'GUARD', 'SYSTEM')),
           event_type        text NOT NULL CONSTRAINT ck_alert_events_type CHECK (event_type IN (
                                 'CREATED', 'DETECTION_REPEATED', 'ROUTED', 'ACKNOWLEDGED', 'RESPONDING',
                                 'ASSESSMENT', 'ESCALATED', 'RESOLVED', 'DISMISSED')),
           from_status       text,
           to_status         text,
           details           jsonb NOT NULL DEFAULT '{}'::jsonb,
           client_action_id  uuid,
           -- clock_timestamp: several events of one transaction keep their order.
           created_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
           CONSTRAINT fk_alert_events_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    """CREATE UNIQUE INDEX uq_alert_events_client_action ON alert_events (actor_user_id, client_action_id)
       WHERE client_action_id IS NOT NULL""",
    "CREATE INDEX ix_alert_events_alert ON alert_events (alert_id, created_at)",
    """CREATE TABLE alert_messages (
           id                 uuid PRIMARY KEY,
           alert_id           uuid NOT NULL,
           organization_id    uuid NOT NULL,
           campus_id          uuid NOT NULL,
           sender_user_id     uuid REFERENCES users (id) ON DELETE SET NULL,
           sender_role        text NOT NULL CONSTRAINT ck_alert_messages_role
                                  CHECK (sender_role IN ('ADMIN', 'SUPERVISOR', 'GUARD')),
           sender_name        text NOT NULL,
           body               text NOT NULL
                                  CONSTRAINT ck_alert_messages_body CHECK (length(btrim(body)) BETWEEN 1 AND 2000),
           client_message_id  uuid,
           created_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
           CONSTRAINT fk_alert_messages_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    """CREATE UNIQUE INDEX uq_alert_messages_client ON alert_messages (sender_user_id, client_message_id)
       WHERE client_message_id IS NOT NULL""",
    "CREATE INDEX ix_alert_messages_alert ON alert_messages (alert_id, created_at)",
    f"""CREATE TABLE manual_reports (
           id                  uuid PRIMARY KEY,
           organization_id     uuid NOT NULL,
           campus_id           uuid NOT NULL,
           reporter_user_id    uuid REFERENCES users (id) ON DELETE SET NULL,
           reporter_name       text NOT NULL,
           reporter_guard_id   text NOT NULL,
           client_report_id    uuid NOT NULL,
           report_type         text NOT NULL CONSTRAINT ck_manual_reports_type
                                   CHECK (length(btrim(report_type)) BETWEEN 2 AND 60),
           description         text NOT NULL CONSTRAINT ck_manual_reports_description
                                   CHECK (length(btrim(description)) BETWEEN 1 AND 4000),
           building_id         uuid,
           floor_id            uuid,
           area_id             uuid,
           building_name       text NOT NULL,
           floor_name          text,
           area_name           text,
           photo_path          text,
           photo_content_type  text CONSTRAINT ck_manual_reports_photo_type
                                   CHECK (photo_content_type IN ('image/jpeg', 'image/png')),
           occurred_at         timestamptz,
           status              text NOT NULL DEFAULT 'SUBMITTED' CONSTRAINT ck_manual_reports_status
                                   CHECK (status IN ('SUBMITTED', 'REVIEWED')),
           reviewed_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           reviewed_at         timestamptz,
           review_note         text CONSTRAINT ck_manual_reports_review_note CHECK (length(review_note) <= 1000),
           created_at          timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_manual_reports_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id),
           {_LOC_FK.format(t="manual_reports")},
           CONSTRAINT ck_manual_reports_photo CHECK ((photo_path IS NULL) = (photo_content_type IS NULL)),
           CONSTRAINT ck_manual_reports_reviewed CHECK ((status = 'REVIEWED') = (reviewed_at IS NOT NULL)),
           CONSTRAINT uq_manual_reports_scope UNIQUE (organization_id, campus_id, id)
       )""",
    """CREATE UNIQUE INDEX uq_manual_reports_client ON manual_reports (reporter_user_id, client_report_id)""",
    "CREATE INDEX ix_manual_reports_campus ON manual_reports (organization_id, campus_id, created_at DESC)",
    """CREATE TABLE user_notifications (
           id                uuid PRIMARY KEY,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           user_id           uuid NOT NULL,
           kind              text NOT NULL CONSTRAINT ck_user_notifications_kind CHECK (kind IN (
                                 'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT',
                                 'ALERT_ASSISTANCE_REQUESTED', 'ALERT_RESOLVED', 'ALERT_DISMISSED',
                                 'MANUAL_REPORT_SUBMITTED')),
           alert_id          uuid,
           manual_report_id  uuid,
           title             text NOT NULL CONSTRAINT ck_user_notifications_title CHECK (length(title) <= 200),
           created_at        timestamptz NOT NULL DEFAULT now(),
           read_at           timestamptz,
           CONSTRAINT fk_user_notifications_user FOREIGN KEY (organization_id, campus_id, user_id)
               REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_user_notifications_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_user_notifications_report FOREIGN KEY (organization_id, campus_id, manual_report_id)
               REFERENCES manual_reports (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT ck_user_notifications_target CHECK (num_nonnulls(alert_id, manual_report_id) = 1)
       )""",
    "CREATE INDEX ix_user_notifications_user ON user_notifications (user_id, created_at DESC)",
    """CREATE TABLE organization_audit_logs (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           actor_user_id    uuid REFERENCES users (id) ON DELETE SET NULL,
           actor_role       text NOT NULL CONSTRAINT ck_org_audit_role
                                CHECK (actor_role IN ('ADMIN', 'SUPERVISOR', 'GUARD', 'SYSTEM')),
           action           text NOT NULL CONSTRAINT ck_org_audit_action CHECK (action ~ '^[A-Z_]{3,64}$'),
           target_type      text NOT NULL CONSTRAINT ck_org_audit_target CHECK (target_type ~ '^[A-Z_]{3,32}$'),
           target_id        uuid,
           metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
           occurred_at      timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_org_audit_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id)
       )""",
    "CREATE INDEX ix_org_audit_campus ON organization_audit_logs (organization_id, campus_id, occurred_at DESC)",
]

_TABLES = [
    "alerts",
    "alert_escalations",
    "alert_recipients",
    "alert_events",
    "alert_messages",
    "manual_reports",
    "user_notifications",
    "organization_audit_logs",
]

_POLICIES = [
    *(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY" for t in _TABLES),
    f"CREATE POLICY alerts_select ON alerts FOR SELECT USING (({_STAFF}) OR {_recipient('id')})",
    f"CREATE POLICY alerts_update ON alerts FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY alert_escalations_select ON alert_escalations FOR SELECT USING ({_STAFF})",
    f"""CREATE POLICY alert_escalations_insert ON alert_escalations FOR INSERT
        WITH CHECK ({_STAFF} AND escalated_by = app_current_user_id())""",
    f"""CREATE POLICY alert_recipients_select ON alert_recipients FOR SELECT
        USING (({_STAFF}) OR ({_CAMPUS} AND guard_user_id = app_current_user_id()))""",
    f"CREATE POLICY alert_recipients_insert ON alert_recipients FOR INSERT WITH CHECK ({_STAFF})",
    f"CREATE POLICY alert_events_select ON alert_events FOR SELECT USING (({_STAFF}) OR {_recipient('alert_id')})",
    f"""CREATE POLICY alert_events_insert ON alert_events FOR INSERT
        WITH CHECK ({_STAFF} AND actor_user_id = app_current_user_id())""",
    f"""CREATE POLICY alert_messages_select ON alert_messages FOR SELECT
        USING (({_STAFF}) OR {_recipient("alert_id")})""",
    f"""CREATE POLICY alert_messages_insert ON alert_messages FOR INSERT
        WITH CHECK (sender_user_id = app_current_user_id() AND (({_STAFF}) OR {_recipient("alert_id")}))""",
    f"""CREATE POLICY manual_reports_select ON manual_reports FOR SELECT
        USING (({_STAFF}) OR ({_CAMPUS} AND reporter_user_id = app_current_user_id()))""",
    f"CREATE POLICY manual_reports_update ON manual_reports FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"""CREATE POLICY user_notifications_own ON user_notifications FOR SELECT
        USING ({_CAMPUS} AND user_id = app_current_user_id())""",
    f"""CREATE POLICY user_notifications_read ON user_notifications FOR UPDATE
        USING ({_CAMPUS} AND user_id = app_current_user_id())
        WITH CHECK ({_CAMPUS} AND user_id = app_current_user_id())""",
    f"CREATE POLICY user_notifications_insert ON user_notifications FOR INSERT WITH CHECK ({_STAFF})",
    f"""CREATE POLICY org_audit_insert ON organization_audit_logs FOR INSERT
        WITH CHECK ({_CAMPUS} AND actor_user_id = app_current_user_id() AND actor_role = app_actor_role())""",
]

_GRANTS = {
    "alerts": "SELECT, UPDATE",
    "alert_escalations": "SELECT, INSERT",
    "alert_recipients": "SELECT, INSERT",
    "alert_events": "SELECT, INSERT",
    "alert_messages": "SELECT, INSERT",
    "manual_reports": "SELECT, UPDATE",
    "user_notifications": "SELECT, INSERT, UPDATE",
    "organization_audit_logs": "INSERT",
}

# Staff of a campus get an in-app notification (inside a function, as the owner).
_NOTIFY_STAFF = """INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, {target}, title)
               SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id, {kind}, {target_value}, {title}
                 FROM users u
                WHERE u.organization_id = {org} AND u.campus_id = {campus}
                  AND u.role IN ('ADMIN', 'SUPERVISOR') AND u.status = 'ACTIVE'"""

_FUNCTIONS = [
    # --- Detection intake ------------------------------------------------------------------------
    f"""CREATE FUNCTION app_alert_ingest(p_camera_id uuid, p_type text, p_confidence numeric,
                                         p_detected_at timestamptz, p_detection_id text, p_snapshot_ref text,
                                         p_dedup_seconds integer)
       RETURNS TABLE (alert_id uuid, outcome text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           c record;
           v_alert uuid;
           v_priority text;
           v_building uuid;
           v_floor uuid;
           v_area uuid;
           v_count integer;
           v_title text;
       BEGIN
           IF p_type NOT IN ('WEAPON', 'FIGHT_VIOLENCE', 'RESTRICTED_AREA') THEN
               RETURN QUERY SELECT NULL::uuid, 'INVALID_TYPE'::text;
               RETURN;
           END IF;
           SELECT x.id, x.organization_id, x.campus_id, x.display_name, x.building_id, x.floor_id, x.area_id INTO c
             FROM cameras x
             JOIN campuses ca ON ca.organization_id = x.organization_id AND ca.id = x.campus_id AND ca.status = 'ACTIVE'
             JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
            WHERE x.id = p_camera_id;
           IF NOT FOUND THEN
               RETURN QUERY SELECT NULL::uuid, 'CAMERA_NOT_FOUND'::text;
               RETURN;
           END IF;
           -- The same detection sent twice.
           IF p_detection_id IS NOT NULL THEN
               SELECT a.id INTO v_alert FROM alerts a WHERE a.camera_id = c.id AND a.detection_id = p_detection_id;
               IF FOUND THEN
                   RETURN QUERY SELECT v_alert, 'DUPLICATE'::text;
                   RETURN;
               END IF;
           END IF;
           -- A repeat of an open alert of the same camera and type within the window: no new alert.
           SELECT a.id INTO v_alert FROM alerts a
            WHERE a.camera_id = c.id AND a.alert_type = p_type AND a.status IN {_OPEN}
              AND a.last_detected_at >= p_detected_at - make_interval(secs => p_dedup_seconds)
            ORDER BY a.last_detected_at DESC LIMIT 1 FOR UPDATE;
           IF FOUND THEN
               UPDATE alerts a SET detection_count = a.detection_count + 1,
                                   last_detected_at = GREATEST(a.last_detected_at, p_detected_at),
                                   confidence = GREATEST(a.confidence, p_confidence), updated_at = now()
                WHERE a.id = v_alert;
               INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_role, event_type, details)
               VALUES (gen_random_uuid(), v_alert, c.organization_id, c.campus_id, 'SYSTEM', 'DETECTION_REPEATED',
                       jsonb_build_object('confidence', p_confidence));
               RETURN QUERY SELECT v_alert, 'REPEATED'::text;
               RETURN;
           END IF;

           v_priority := CASE WHEN p_type IN ('WEAPON', 'FIGHT_VIOLENCE') THEN 'HIGH' ELSE 'STANDARD' END;
           -- The camera's location, written out from the building down.
           v_area := c.area_id;
           v_floor := COALESCE(c.floor_id, (SELECT ar.floor_id FROM areas ar WHERE ar.id = c.area_id));
           v_building := COALESCE(c.building_id, (SELECT fl.building_id FROM floors fl WHERE fl.id = v_floor),
                                  (SELECT ar.building_id FROM areas ar WHERE ar.id = c.area_id));
           v_alert := gen_random_uuid();
           INSERT INTO alerts (id, organization_id, campus_id, camera_id, alert_type, priority, confidence,
                               detected_at, last_detected_at, detection_id, snapshot_ref, camera_name,
                               building_id, floor_id, area_id, building_name, building_kind, floor_name, area_name)
           SELECT v_alert, c.organization_id, c.campus_id, c.id, p_type, v_priority, p_confidence,
                  p_detected_at, p_detected_at, p_detection_id, p_snapshot_ref, c.display_name,
                  v_building, v_floor, v_area,
                  (SELECT b.name FROM buildings b WHERE b.id = v_building),
                  (SELECT b.kind FROM buildings b WHERE b.id = v_building),
                  (SELECT f.name FROM floors f WHERE f.id = v_floor),
                  (SELECT ar.name FROM areas ar WHERE ar.id = v_area);
           INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_role, event_type, to_status,
                                     details)
           VALUES (gen_random_uuid(), v_alert, c.organization_id, c.campus_id, 'SYSTEM', 'CREATED', 'NEW',
                   jsonb_build_object('alert_type', p_type, 'priority', v_priority, 'confidence', p_confidence));

           -- Routing: active Guards of this campus whose assignment contains the camera's location.
           INSERT INTO alert_recipients (alert_id, guard_user_id, organization_id, campus_id, reason)
           SELECT v_alert, g.guard_user_id, c.organization_id, c.campus_id, 'LOCATION'
             FROM guard_location_assignments g
             JOIN users u ON u.id = g.guard_user_id AND u.role = 'GUARD' AND u.status = 'ACTIVE'
            WHERE g.organization_id = c.organization_id AND g.campus_id = c.campus_id
              AND ((g.building_id IS NOT NULL AND g.building_id = v_building)
                   OR (g.floor_id IS NOT NULL AND g.floor_id = v_floor)
                   OR (g.area_id IS NOT NULL AND g.area_id = v_area));
           GET DIAGNOSTICS v_count = ROW_COUNT;
           INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_role, event_type, details)
           VALUES (gen_random_uuid(), v_alert, c.organization_id, c.campus_id, 'SYSTEM', 'ROUTED',
                   jsonb_build_object('guards', v_count, 'camera_has_location', v_building IS NOT NULL));

           v_title := initcap(replace(lower(p_type), '_', ' ')) || ' alert at ' || c.display_name;
           INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, alert_id, title)
           SELECT gen_random_uuid(), c.organization_id, c.campus_id, r.guard_user_id, 'ALERT_NEW', v_alert, v_title
             FROM alert_recipients r WHERE r.alert_id = v_alert;
           {
        _NOTIFY_STAFF.format(
            target="alert_id",
            kind="'ALERT_NEW'",
            target_value="v_alert",
            title="v_title",
            org="c.organization_id",
            campus="c.campus_id",
        )
    };
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_role, action, target_type,
                                                target_id, metadata)
           VALUES (gen_random_uuid(), c.organization_id, c.campus_id, 'SYSTEM', 'ALERT_CREATED', 'ALERT', v_alert,
                   jsonb_build_object('alert_type', p_type, 'priority', v_priority, 'routed_guards', v_count));
           RETURN QUERY SELECT v_alert, 'CREATED'::text;
       END $$""",
    # --- A recipient Guard's action ------------------------------------------------------------------
    f"""CREATE FUNCTION app_alert_guard_action(p_alert_id uuid, p_action text, p_client_action_id uuid)
       RETURNS TABLE (outcome text, status text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           a alerts%ROWTYPE;
           r alert_recipients%ROWTYPE;
           v_new_status text;
           v_event text;
           v_kind text;
       BEGIN
           SELECT u.id, u.organization_id, u.campus_id INTO me FROM users u
            WHERE u.id = app_current_user_id() AND u.role = 'GUARD' AND u.status = 'ACTIVE';
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::text;
               RETURN;
           END IF;
           SELECT * INTO a FROM alerts x
            WHERE x.id = p_alert_id AND x.organization_id = me.organization_id AND x.campus_id = me.campus_id
            FOR UPDATE;
           SELECT * INTO r FROM alert_recipients x WHERE x.alert_id = p_alert_id AND x.guard_user_id = me.id
            FOR UPDATE;
           IF a.id IS NULL OR r.alert_id IS NULL THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::text;  -- the same answer for any other alert
               RETURN;
           END IF;
           -- A queued action sent again: answer as before, change nothing.
           IF p_client_action_id IS NOT NULL AND EXISTS (
                   SELECT 1 FROM alert_events e
                    WHERE e.actor_user_id = me.id AND e.client_action_id = p_client_action_id) THEN
               RETURN QUERY SELECT 'REPLAYED'::text, a.status;
               RETURN;
           END IF;
           IF a.status IN ('RESOLVED', 'DISMISSED') THEN
               RETURN QUERY SELECT 'ALERT_CLOSED'::text, a.status;
               RETURN;
           END IF;
           IF p_action = 'ACKNOWLEDGE' THEN
               IF r.state <> 'NOTIFIED' THEN
                   RETURN QUERY SELECT 'UNCHANGED'::text, a.status;
                   RETURN;
               END IF;
               UPDATE alert_recipients x SET state = 'ACKNOWLEDGED', acknowledged_at = now()
                WHERE x.alert_id = a.id AND x.guard_user_id = me.id;
               v_new_status := CASE WHEN a.status = 'NEW' THEN 'ACKNOWLEDGED' ELSE a.status END;
               v_event := 'ACKNOWLEDGED';
           ELSIF p_action = 'RESPONDING' THEN
               IF r.state = 'RESPONDING' THEN
                   RETURN QUERY SELECT 'UNCHANGED'::text, a.status;
                   RETURN;
               END IF;
               UPDATE alert_recipients x SET state = 'RESPONDING', responding_at = now(),
                                             acknowledged_at = COALESCE(x.acknowledged_at, now())
                WHERE x.alert_id = a.id AND x.guard_user_id = me.id;
               v_new_status := CASE WHEN a.status IN ('NEW', 'ACKNOWLEDGED') THEN 'RESPONDING' ELSE a.status END;
               v_event := 'RESPONDING';
               v_kind := 'ALERT_RESPONDING';
           ELSIF p_action IN ('UNDER_CONTROL', 'ADDITIONAL_GUARDS_REQUIRED') THEN
               -- An assessment follows acknowledgement (15.3); it never resolves the alert.
               IF r.state = 'NOTIFIED' THEN
                   RETURN QUERY SELECT 'ACKNOWLEDGE_FIRST'::text, a.status;
                   RETURN;
               END IF;
               UPDATE alert_recipients x SET assessment = p_action
                WHERE x.alert_id = a.id AND x.guard_user_id = me.id;
               v_new_status := a.status;
               v_event := 'ASSESSMENT';
               v_kind := CASE WHEN p_action = 'ADDITIONAL_GUARDS_REQUIRED' THEN 'ALERT_ASSISTANCE_REQUESTED'
                              ELSE 'ALERT_ASSESSMENT' END;
           ELSE
               RETURN QUERY SELECT 'INVALID_ACTION'::text, a.status;
               RETURN;
           END IF;
           IF v_new_status <> a.status THEN
               UPDATE alerts x SET status = v_new_status WHERE x.id = a.id;
           END IF;
           INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_user_id, actor_role, event_type,
                                     from_status, to_status, details, client_action_id)
           VALUES (gen_random_uuid(), a.id, me.organization_id, me.campus_id, me.id, 'GUARD', v_event,
                   a.status, v_new_status, jsonb_build_object('action', p_action), p_client_action_id);
           IF v_kind IS NOT NULL THEN
               {
        _NOTIFY_STAFF.format(
            target="alert_id",
            kind="v_kind",
            target_value="a.id",
            title="replace(initcap(lower(p_action)), '_', ' ') || ': ' || a.camera_name",
            org="me.organization_id",
            campus="me.campus_id",
        )
    };
           END IF;
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                target_type, target_id, metadata)
           VALUES (gen_random_uuid(), me.organization_id, me.campus_id, me.id, 'GUARD', 'ALERT_' || p_action,
                   'ALERT', a.id, jsonb_build_object('from_status', a.status, 'to_status', v_new_status));
           RETURN QUERY SELECT 'DONE'::text, v_new_status;
       END $$""",
    # --- Guard manual report ---------------------------------------------------------------------------
    f"""CREATE FUNCTION app_manual_report_submit(p_report_id uuid, p_client_report_id uuid, p_type text,
                                                 p_description text, p_building_id uuid, p_floor_id uuid,
                                                 p_area_id uuid, p_photo_path text, p_photo_type text,
                                                 p_occurred_at timestamptz)
       RETURNS TABLE (report_id uuid, outcome text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           me record;
           v_existing uuid;
           b record;
           v_floor_name text;
           v_area_name text;
       BEGIN
           SELECT u.id, u.organization_id, u.campus_id, u.full_name, u.display_id INTO me FROM users u
            WHERE u.id = app_current_user_id() AND u.role = 'GUARD' AND u.status = 'ACTIVE';
           IF NOT FOUND THEN
               RETURN QUERY SELECT NULL::uuid, 'NOT_ALLOWED'::text;
               RETURN;
           END IF;
           SELECT m.id INTO v_existing FROM manual_reports m
            WHERE m.reporter_user_id = me.id AND m.client_report_id = p_client_report_id;
           IF FOUND THEN
               RETURN QUERY SELECT v_existing, 'REPLAYED'::text;
               RETURN;
           END IF;
           -- Building (or perimeter), then optionally its floor, then optionally an area on it.
           SELECT x.id, x.name INTO b FROM buildings x
            WHERE x.id = p_building_id AND x.organization_id = me.organization_id AND x.campus_id = me.campus_id;
           IF NOT FOUND THEN
               RETURN QUERY SELECT NULL::uuid, 'LOCATION_NOT_FOUND'::text;
               RETURN;
           END IF;
           IF p_floor_id IS NOT NULL THEN
               SELECT x.name INTO v_floor_name FROM floors x WHERE x.id = p_floor_id AND x.building_id = b.id;
               IF NOT FOUND THEN
                   RETURN QUERY SELECT NULL::uuid, 'INVALID_LOCATION'::text;
                   RETURN;
               END IF;
           END IF;
           IF p_area_id IS NOT NULL THEN
               SELECT x.name INTO v_area_name FROM areas x
                WHERE x.id = p_area_id AND x.building_id = b.id AND x.floor_id IS NOT DISTINCT FROM p_floor_id;
               IF NOT FOUND THEN
                   RETURN QUERY SELECT NULL::uuid, 'INVALID_LOCATION'::text;
                   RETURN;
               END IF;
           END IF;
           INSERT INTO manual_reports (id, organization_id, campus_id, reporter_user_id, reporter_name,
                                       reporter_guard_id, client_report_id, report_type, description, building_id,
                                       floor_id, area_id, building_name, floor_name, area_name, photo_path,
                                       photo_content_type, occurred_at)
           VALUES (p_report_id, me.organization_id, me.campus_id, me.id, me.full_name, me.display_id,
                   p_client_report_id, btrim(p_type), btrim(p_description), b.id, p_floor_id, p_area_id, b.name,
                   v_floor_name, v_area_name,
                   p_photo_path, p_photo_type, p_occurred_at);
           {
        _NOTIFY_STAFF.format(
            target="manual_report_id",
            kind="'MANUAL_REPORT_SUBMITTED'",
            target_value="p_report_id",
            title="'Manual report: ' || btrim(p_type)",
            org="me.organization_id",
            campus="me.campus_id",
        )
    };
           INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                                                target_type, target_id, metadata)
           VALUES (gen_random_uuid(), me.organization_id, me.campus_id, me.id, 'GUARD', 'MANUAL_REPORT_SUBMITTED',
                   'MANUAL_REPORT', p_report_id, jsonb_build_object('has_photo', p_photo_path IS NOT NULL));
           RETURN QUERY SELECT p_report_id, 'CREATED'::text;
       END $$""",
    # --- Location names a Guard may pick from (manual-report dropdowns) -------------------------------------
    """CREATE FUNCTION app_guard_campus_locations()
       RETURNS TABLE (building_id uuid, building_name text, building_kind text, floor_id uuid, floor_name text,
                      floor_level integer, area_id uuid, area_name text)
       LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           SELECT b.id, b.name, b.kind, f.id, f.name, f.level, a.id, a.name
             FROM users u
             JOIN buildings b ON b.organization_id = u.organization_id AND b.campus_id = u.campus_id
             LEFT JOIN floors f ON f.building_id = b.id
             LEFT JOIN areas a ON a.building_id = b.id AND a.floor_id IS NOT DISTINCT FROM f.id
            WHERE u.id = app_current_user_id() AND u.role = 'GUARD' AND u.status = 'ACTIVE'
           UNION ALL
           -- Areas directly under a building that also has floors.
           SELECT b.id, b.name, b.kind, NULL, NULL, NULL, a.id, a.name
             FROM users u
             JOIN buildings b ON b.organization_id = u.organization_id AND b.campus_id = u.campus_id
             JOIN areas a ON a.building_id = b.id AND a.floor_id IS NULL
            WHERE u.id = app_current_user_id() AND u.role = 'GUARD' AND u.status = 'ACTIVE'
              AND EXISTS (SELECT 1 FROM floors f WHERE f.building_id = b.id)
       $$""",
]
_FUNCTION_SIGNATURES = [
    "app_alert_ingest(uuid, text, numeric, timestamptz, text, text, integer)",
    "app_alert_guard_action(uuid, text, uuid)",
    "app_manual_report_submit(uuid, uuid, text, text, uuid, uuid, uuid, text, text, timestamptz)",
    "app_guard_campus_locations()",
]

# --- Campus deletion and export (replacing the 0013 / 0014 versions) ----------------------------------------

_ALERTS_OF_CAMPUS = """               -- Alert data, before the cameras it points at (0015).
               DELETE FROM user_notifications WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM alerts WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM manual_reports WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
               DELETE FROM organization_audit_logs
                WHERE organization_id = me.organization_id AND campus_id = me.campus_id;
"""
_ALERTS_OF_ORG = """               DELETE FROM user_notifications WHERE organization_id = me.organization_id;
               DELETE FROM alerts WHERE organization_id = me.organization_id;
               DELETE FROM manual_reports WHERE organization_id = me.organization_id;
               DELETE FROM organization_audit_logs WHERE organization_id = me.organization_id;
"""


def _deletion_function_0013() -> str:
    return _load("0013_buildings_floors_areas.py")._deletion_function()


def _deletion_function() -> str:
    m13 = _load("0013_buildings_floors_areas.py")
    old = _deletion_function_0013()
    campus, org = m13._CAMERAS_OF_CAMPUS, m13._CAMERAS_OF_ORG
    assert old.count(campus) == 1 and old.count(org) == 1
    return old.replace(campus, _ALERTS_OF_CAMPUS + campus).replace(org, _ALERTS_OF_ORG + org)


def _export_function_0014() -> str:
    return _load("0014_camera_management.py")._export_function()


_EXPORT_ANCHOR = "               'pending_account_creations', "
_EXPORT_ALERTS = """               'alerts', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'alert_type', al.alert_type, 'priority', al.priority, 'status', al.status,
                       'camera', al.camera_name, 'building', al.building_name, 'floor', al.floor_name,
                       'area', al.area_name, 'detected_at', al.detected_at, 'detection_count', al.detection_count,
                       'closed_at', al.closed_at) ORDER BY al.detected_at)
                     FROM alerts al WHERE al.organization_id = me.organization_id AND al.campus_id = me.campus_id),
                   '[]'::jsonb),
               'manual_reports', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                       'guard_id', mr.reporter_guard_id, 'report_type', mr.report_type,
                       'description', mr.description, 'building', mr.building_name, 'floor', mr.floor_name,
                       'area', mr.area_name, 'has_photo', mr.photo_path IS NOT NULL, 'status', mr.status,
                       'created_at', mr.created_at) ORDER BY mr.created_at)
                     FROM manual_reports mr
                    WHERE mr.organization_id = me.organization_id AND mr.campus_id = me.campus_id),
                   '[]'::jsonb),
"""


def _export_function() -> str:
    old = _export_function_0014()
    assert old.count(_EXPORT_ANCHOR) == 1
    return old.replace(_EXPORT_ANCHOR, _EXPORT_ALERTS + _EXPORT_ANCHOR)


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA + _POLICIES + _FUNCTIONS:
        op.execute(statement)
    for table, rights in _GRANTS.items():
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT {rights} ON {table} TO {app}")
    op.execute("REVOKE ALL ON FUNCTION app_alert_status_guard() FROM PUBLIC")
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
    op.execute(_deletion_function())
    op.execute(_export_function())


def downgrade() -> None:
    """Back to 0014. Alerts, reports, notifications and organization audit entries are dropped."""
    op.execute(_export_function_0014())
    op.execute(_deletion_function_0013())
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP TABLE IF EXISTS " + ", ".join(reversed(_TABLES)))
    op.execute("DROP FUNCTION IF EXISTS app_alert_status_guard()")
    op.execute("DROP INDEX IF EXISTS uq_users_scope")
    op.execute("ALTER TABLE cameras DROP CONSTRAINT IF EXISTS uq_cameras_scope")
