"""Real-time signals, in-app voice calls, device push tokens and the Guards' responder roster (decision D-025).

Real-time signals (WebSocket ``/api/v1/realtime/ws``)
* Triggers call ``pg_notify('cctv_realtime', <json>)`` after changes a connected client should see at once: alert
  recipients and status, alert and case chat messages, case invitations and status, in-app notifications, calls and
  their participants, a revoked session (password change, Guard assignment change, deactivation) and an
  organization's status. The JSON holds only an event name and IDs (``e``, ``org``, ``campus``, ``alert``,
  ``incident``, ``call``, ``user``, ``state``, ``kind``): never message text, names, tokens or addresses. The API
  forwards a signal only to connections allowed to see its target; clients then read the content through the REST
  routes, where Row-Level Security applies as before.
* ``app_comm_target_visible(alert, incident)``: may the bound actor see this alert or case? Staff of its campus, a
  Guard recipient of the alert, or a Guard taking part in the case (``app_incident_participant``).

In-app voice calls (LiveKit; no media passes through the API or the database)
* ``call_sessions``: one audio call about an alert or a case, ACTIVE or ENDED; at most one ACTIVE call per alert and
  per case; ``room_name`` is the LiveKit room. A call ends by itself when its alert or case is resolved or dismissed.
* ``call_participants``: who was invited, joined, declined, left or missed the call. Names are not copied; they are
  read from ``users`` (so permanent deletion's "Deleted user" applies to calls too).

Device push tokens
* ``device_push_tokens``: the FCM (Android) or APNs (iOS) token of a signed-in user's phone, so a locked phone can be
  woken for an alert when ``PUSH_DELIVERY_MODE=fcm``. A token belongs to one user at a time
  (``app_device_token_register`` moves it); tokens of an account that stops being ACTIVE are deleted.
* ``user_notifications.push_claimed_at``: ``app_push_claim`` lets exactly one API process claim a notification for
  push delivery.

Responder roster (Guard app screen 6 item 6, requirements 15.4)
* ``app_alert_responders(alert)``: for staff of the campus and for the alert's Guard recipients, the Guards who
  acknowledged or are responding (name, state, assessment, times), without their Guard IDs.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-19
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL = "cctv_realtime"
PUSH_KINDS = "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_AUTO_ESCALATED', 'ALERT_ASSISTANCE_BROADCAST', 'INCIDENT_INVITED'"
MAX_TOKENS_PER_USER = 10


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_DEFINER = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public"


def _signal(**fields: str) -> str:
    """A pg_notify call with a JSON object of the given SQL expressions (nulls dropped)."""
    pairs = ", ".join(f"'{key}', {value}" for key, value in fields.items())
    return f"PERFORM pg_notify('{CHANNEL}', jsonb_strip_nulls(jsonb_build_object({pairs}))::text)"


_SCHEMA = [
    """CREATE TABLE call_sessions (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           scope            text NOT NULL CONSTRAINT ck_call_sessions_scope CHECK (scope IN ('ALERT', 'INCIDENT')),
           alert_id         uuid,
           incident_id      uuid,
           room_name        text NOT NULL CONSTRAINT uq_call_sessions_room UNIQUE
                                CONSTRAINT ck_call_sessions_room CHECK (room_name ~ '^cctv-call-[0-9a-f-]{36}$'),
           media            text NOT NULL DEFAULT 'AUDIO' CONSTRAINT ck_call_sessions_media CHECK (media = 'AUDIO'),
           status           text NOT NULL DEFAULT 'ACTIVE' CONSTRAINT ck_call_sessions_status
                                CHECK (status IN ('ACTIVE', 'ENDED')),
           started_by       uuid REFERENCES users (id) ON DELETE SET NULL,
           started_by_role  text NOT NULL CONSTRAINT ck_call_sessions_role
                                CHECK (started_by_role IN ('ADMIN', 'SUPERVISOR', 'GUARD')),
           started_at       timestamptz NOT NULL DEFAULT now(),
           ended_at         timestamptz,
           ended_by         uuid REFERENCES users (id) ON DELETE SET NULL,
           end_reason       text CONSTRAINT ck_call_sessions_end_reason
                                CHECK (end_reason IN ('ENDED_BY_USER', 'EVERYONE_LEFT', 'TARGET_CLOSED')),
           CONSTRAINT ck_call_sessions_target CHECK (
               (scope = 'ALERT' AND alert_id IS NOT NULL AND incident_id IS NULL)
               OR (scope = 'INCIDENT' AND incident_id IS NOT NULL AND alert_id IS NULL)),
           CONSTRAINT ck_call_sessions_ended CHECK (
               ((status = 'ENDED') = (ended_at IS NOT NULL)) AND ((status = 'ENDED') = (end_reason IS NOT NULL))),
           CONSTRAINT fk_call_sessions_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_call_sessions_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_call_sessions_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT uq_call_sessions_scope UNIQUE (organization_id, campus_id, id)
       )""",
    """CREATE UNIQUE INDEX uq_call_sessions_active_alert ON call_sessions (alert_id)
       WHERE status = 'ACTIVE' AND alert_id IS NOT NULL""",
    """CREATE UNIQUE INDEX uq_call_sessions_active_incident ON call_sessions (incident_id)
       WHERE status = 'ACTIVE' AND incident_id IS NOT NULL""",
    "CREATE INDEX ix_call_sessions_campus ON call_sessions (organization_id, campus_id, status, started_at DESC)",
    """CREATE TABLE call_participants (
           call_id          uuid NOT NULL,
           user_id          uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           state            text NOT NULL CONSTRAINT ck_call_participants_state
                                CHECK (state IN ('INVITED', 'JOINED', 'DECLINED', 'LEFT', 'MISSED')),
           invited_at       timestamptz NOT NULL DEFAULT now(),
           joined_at        timestamptz,
           left_at          timestamptz,
           updated_at       timestamptz NOT NULL DEFAULT now(),
           PRIMARY KEY (call_id, user_id),
           CONSTRAINT fk_call_participants_call FOREIGN KEY (organization_id, campus_id, call_id)
               REFERENCES call_sessions (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_call_participants_user FOREIGN KEY (organization_id, campus_id, user_id)
               REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_call_participants_user ON call_participants (user_id, state)",
    """CREATE TABLE device_push_tokens (
           id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
           organization_id     uuid NOT NULL,
           campus_id           uuid NOT NULL,
           user_id             uuid NOT NULL,
           token               text NOT NULL CONSTRAINT uq_device_push_tokens_token UNIQUE
                                   CONSTRAINT ck_device_push_tokens_token CHECK (length(token) BETWEEN 16 AND 4096),
           platform            text NOT NULL CONSTRAINT ck_device_push_tokens_platform
                                   CHECK (platform IN ('ANDROID', 'IOS')),
           provider            text NOT NULL CONSTRAINT ck_device_push_tokens_provider
                                   CHECK (provider IN ('FCM', 'APNS')),
           app_version         text CONSTRAINT ck_device_push_tokens_version CHECK (length(app_version) <= 40),
           created_at          timestamptz NOT NULL DEFAULT now(),
           last_registered_at  timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_device_push_tokens_user FOREIGN KEY (organization_id, campus_id, user_id)
               REFERENCES users (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_device_push_tokens_user ON device_push_tokens (user_id)",
    "ALTER TABLE user_notifications ADD COLUMN push_claimed_at timestamptz",
    """CREATE INDEX ix_user_notifications_push ON user_notifications (created_at)
       WHERE push_claimed_at IS NULL AND read_at IS NULL""",
]

_FUNCTIONS = [
    # --- Who may see an alert or a case (staff of its campus, a Guard recipient, a Guard in the case chat) --------
    """CREATE FUNCTION app_comm_target_visible(p_alert uuid, p_incident uuid) RETURNS boolean
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT CASE
               WHEN p_alert IS NOT NULL THEN EXISTS (
                   SELECT 1 FROM alerts a
                    WHERE a.id = p_alert
                      AND a.organization_id = app_actor_org_id() AND a.campus_id = app_actor_campus_id()
                      AND (app_actor_role() IN ('ADMIN', 'SUPERVISOR')
                           OR (app_actor_role() = 'GUARD'
                               AND EXISTS (SELECT 1 FROM alert_recipients r
                                            WHERE r.alert_id = a.id AND r.guard_user_id = app_current_user_id()))))
               WHEN p_incident IS NOT NULL THEN EXISTS (
                   SELECT 1 FROM incidents i
                    WHERE i.id = p_incident
                      AND i.organization_id = app_actor_org_id() AND i.campus_id = app_actor_campus_id()
                      AND (app_actor_role() IN ('ADMIN', 'SUPERVISOR')
                           OR (app_actor_role() = 'GUARD' AND app_incident_participant(i.id))))
               ELSE false
           END
       $$""",
    # --- The responder roster: Guards who acknowledged or are responding, without their Guard IDs -----------------
    """CREATE FUNCTION app_alert_responders(p_alert uuid)
       RETURNS TABLE (full_name text, state text, assessment text, reason text, acknowledged_at timestamptz,
                      responding_at timestamptz, is_me boolean)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT u.full_name, r.state, r.assessment, r.reason, r.acknowledged_at, r.responding_at,
                  r.guard_user_id = app_current_user_id()
             FROM alert_recipients r
             JOIN users u ON u.id = r.guard_user_id
            WHERE r.alert_id = p_alert AND r.state <> 'NOTIFIED' AND app_comm_target_visible(p_alert, NULL)
            ORDER BY CASE r.state WHEN 'RESPONDING' THEN 0 ELSE 1 END,
                     COALESCE(r.responding_at, r.acknowledged_at), u.full_name
       $$""",
    # --- Who a new call rings: the active staff of the campus and the target's Guards (not the caller) ------------
    """CREATE FUNCTION app_call_invitees(p_alert uuid, p_incident uuid) RETURNS SETOF uuid
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT u.id FROM users u
            WHERE app_comm_target_visible(p_alert, p_incident)
              AND u.organization_id = app_actor_org_id() AND u.campus_id = app_actor_campus_id()
              AND u.status = 'ACTIVE' AND u.id <> app_current_user_id()
              AND (u.role IN ('ADMIN', 'SUPERVISOR')
                   OR (u.role = 'GUARD' AND p_alert IS NOT NULL
                       AND EXISTS (SELECT 1 FROM alert_recipients r
                                    WHERE r.alert_id = p_alert AND r.guard_user_id = u.id))
                   OR (u.role = 'GUARD' AND p_incident IS NOT NULL
                       AND (EXISTS (SELECT 1 FROM incident_participants ip
                                     WHERE ip.incident_id = p_incident AND ip.guard_user_id = u.id)
                            OR EXISTS (SELECT 1 FROM incident_alerts ia
                                         JOIN alert_recipients r ON r.alert_id = ia.alert_id
                                        WHERE ia.incident_id = p_incident AND r.guard_user_id = u.id))))
       $$""",
    # --- Device push tokens ---------------------------------------------------------------------------------------
    f"""CREATE FUNCTION app_device_token_register(p_token text, p_platform text, p_provider text, p_app_version text)
       RETURNS uuid {_DEFINER} AS $$
       DECLARE
           me users%ROWTYPE;
           v_id uuid;
       BEGIN
           SELECT * INTO me FROM users u WHERE u.id = app_current_user_id() AND u.status = 'ACTIVE';
           IF me.id IS NULL THEN
               RAISE EXCEPTION 'no active signed-in user' USING ERRCODE = '42501';
           END IF;
           -- A phone serves one account at a time: the token leaves its earlier owner.
           DELETE FROM device_push_tokens d WHERE d.token = p_token AND d.user_id <> me.id;
           INSERT INTO device_push_tokens (organization_id, campus_id, user_id, token, platform, provider, app_version)
           VALUES (me.organization_id, me.campus_id, me.id, p_token, p_platform, p_provider, p_app_version)
           ON CONFLICT (token) DO UPDATE
              SET platform = EXCLUDED.platform, provider = EXCLUDED.provider, app_version = EXCLUDED.app_version,
                  last_registered_at = now()
           RETURNING id INTO v_id;
           DELETE FROM device_push_tokens d
            WHERE d.user_id = me.id
              AND d.id NOT IN (SELECT k.id FROM device_push_tokens k WHERE k.user_id = me.id
                                ORDER BY k.last_registered_at DESC LIMIT {MAX_TOKENS_PER_USER});
           RETURN v_id;
       END $$""",
    # A token the push provider reported as no longer valid (run by the API in the background, no signed-in user).
    f"""CREATE FUNCTION app_device_token_forget(p_token text) RETURNS boolean {_DEFINER} AS $$
       BEGIN
           DELETE FROM device_push_tokens d WHERE d.token = p_token;
           RETURN FOUND;
       END $$""",
    # Claims unsent alert notifications for push delivery; each is claimed by exactly one caller.
    f"""CREATE FUNCTION app_push_claim(p_limit integer, p_max_age_seconds integer)
       RETURNS TABLE (notification_id uuid, user_id uuid, kind text, alert_id uuid, incident_id uuid,
                      created_at timestamptz, tokens text[])
       {_DEFINER} AS $$
       BEGIN
           RETURN QUERY
           WITH picked AS (
               SELECT x.id FROM user_notifications x
                 JOIN users u ON u.id = x.user_id AND u.status = 'ACTIVE'
                 JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
                WHERE x.push_claimed_at IS NULL AND x.read_at IS NULL
                  AND x.kind IN ({PUSH_KINDS})
                  AND x.created_at > now() - make_interval(secs => greatest(10, p_max_age_seconds))
                  AND EXISTS (SELECT 1 FROM device_push_tokens d WHERE d.user_id = x.user_id AND d.provider = 'FCM')
                ORDER BY x.created_at
                LIMIT greatest(1, least(p_limit, 500))
                FOR UPDATE OF x SKIP LOCKED
           ), claimed AS (
               UPDATE user_notifications n SET push_claimed_at = now()
                 FROM picked WHERE n.id = picked.id
               RETURNING n.id, n.user_id, n.kind, n.alert_id, n.incident_id, n.created_at
           )
           SELECT c.id, c.user_id, c.kind, c.alert_id, c.incident_id, c.created_at,
                  ARRAY(SELECT d.token FROM device_push_tokens d
                         WHERE d.user_id = c.user_id AND d.provider = 'FCM' ORDER BY d.last_registered_at DESC)
             FROM claimed c;
       END $$""",
    # --- Calls end with their alert or case -----------------------------------------------------------------------
    f"""CREATE FUNCTION app_calls_end_with_target() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF NEW.status NOT IN ('RESOLVED', 'DISMISSED') OR OLD.status IS NOT DISTINCT FROM NEW.status THEN
               RETURN NULL;
           END IF;
           UPDATE call_participants p
              SET state = CASE p.state WHEN 'INVITED' THEN 'MISSED' ELSE 'LEFT' END,
                  left_at = CASE WHEN p.state = 'JOINED' THEN now() ELSE p.left_at END,
                  updated_at = now()
             FROM call_sessions s
            WHERE s.id = p.call_id AND s.status = 'ACTIVE' AND p.state IN ('INVITED', 'JOINED')
              AND ((TG_TABLE_NAME = 'alerts' AND s.alert_id = NEW.id)
                   OR (TG_TABLE_NAME = 'incidents' AND s.incident_id = NEW.id));
           UPDATE call_sessions s SET status = 'ENDED', ended_at = now(), end_reason = 'TARGET_CLOSED'
            WHERE s.status = 'ACTIVE'
              AND ((TG_TABLE_NAME = 'alerts' AND s.alert_id = NEW.id)
                   OR (TG_TABLE_NAME = 'incidents' AND s.incident_id = NEW.id));
           RETURN NULL;
       END $$""",
    # --- Real-time signals (IDs only) -----------------------------------------------------------------------------
    f"""CREATE FUNCTION app_rt_alert_recipients() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF TG_OP = 'INSERT' THEN
               {
        _signal(
            e="'alert.assigned'", org="NEW.organization_id", campus="NEW.campus_id", alert="NEW.alert_id",
            user="NEW.guard_user_id",
        )
    };
           ELSIF NEW.state IS DISTINCT FROM OLD.state OR NEW.assessment IS DISTINCT FROM OLD.assessment THEN
               {_signal(e="'alert.updated'", org="NEW.organization_id", campus="NEW.campus_id", alert="NEW.alert_id")};
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_alerts() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status
              OR NEW.handled_by IS DISTINCT FROM OLD.handled_by
              OR NEW.detection_count IS DISTINCT FROM OLD.detection_count THEN
               {
        _signal(
            e="'alert.updated'", org="NEW.organization_id", campus="NEW.campus_id", alert="NEW.id", state="NEW.status"
        )
    };
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_alert_messages() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           {_signal(e="'alert.message'", org="NEW.organization_id", campus="NEW.campus_id", alert="NEW.alert_id")};
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_incident_messages() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           {
        _signal(e="'incident.message'", org="NEW.organization_id", campus="NEW.campus_id", incident="NEW.incident_id")
    };
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_incident_participants() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF TG_OP = 'INSERT' THEN
               {
        _signal(
            e="'incident.invited'", org="NEW.organization_id", campus="NEW.campus_id", incident="NEW.incident_id",
            user="NEW.guard_user_id",
        )
    };
           ELSE
               {
        _signal(
            e="'incident.removed'", org="OLD.organization_id", campus="OLD.campus_id", incident="OLD.incident_id",
            user="OLD.guard_user_id",
        )
    };
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_incidents() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF NEW.status IS DISTINCT FROM OLD.status THEN
               {
        _signal(
            e="'incident.updated'", org="NEW.organization_id", campus="NEW.campus_id", incident="NEW.id",
            state="NEW.status",
        )
    };
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_notifications() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           {
        _signal(
            e="'notification'", org="NEW.organization_id", campus="NEW.campus_id", user="NEW.user_id", kind="NEW.kind",
            alert="NEW.alert_id", incident="NEW.incident_id",
        )
    };
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_call_sessions() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
               {
        _signal(
            e="'call.updated'", org="NEW.organization_id", campus="NEW.campus_id", call="NEW.id", alert="NEW.alert_id",
            incident="NEW.incident_id", state="NEW.status",
        )
    };
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_call_participants() RETURNS trigger {_DEFINER} AS $$
       DECLARE
           s call_sessions%ROWTYPE;
       BEGIN
           SELECT * INTO s FROM call_sessions x WHERE x.id = NEW.call_id;
           IF TG_OP = 'INSERT' AND NEW.state = 'INVITED' THEN
               {
        _signal(
            e="'call.invited'", org="NEW.organization_id", campus="NEW.campus_id", call="NEW.call_id",
            alert="s.alert_id", incident="s.incident_id", user="NEW.user_id",
        )
    };
           END IF;
           IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
               {
        _signal(
            e="'call.updated'", org="NEW.organization_id", campus="NEW.campus_id", call="NEW.call_id",
            alert="s.alert_id", incident="s.incident_id", state="s.status",
        )
    };
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_credentials() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF NEW.sessions_revoked_at IS DISTINCT FROM OLD.sessions_revoked_at
              OR NEW.password_changed_at IS DISTINCT FROM OLD.password_changed_at THEN
               {_signal(e="'session.revoked'", user="NEW.user_id")};
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_users() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF NEW.status IS DISTINCT FROM OLD.status THEN
               IF NEW.status <> 'ACTIVE' THEN
                   DELETE FROM device_push_tokens d WHERE d.user_id = NEW.id;
               END IF;
               {_signal(e="'session.revoked'", user="NEW.id")};
           END IF;
           RETURN NULL;
       END $$""",
    f"""CREATE FUNCTION app_rt_organizations() RETURNS trigger {_DEFINER} AS $$
       BEGIN
           IF NEW.status IS DISTINCT FROM OLD.status THEN
               {_signal(e="'organization.status'", org="NEW.id", state="NEW.status")};
           END IF;
           RETURN NULL;
       END $$""",
]

_TRIGGERS = [
    ("trg_alerts_end_calls", "alerts", "AFTER UPDATE OF status ON alerts FOR EACH ROW", "app_calls_end_with_target"),
    (
        "trg_incidents_end_calls",
        "incidents",
        "AFTER UPDATE OF status ON incidents FOR EACH ROW",
        "app_calls_end_with_target",
    ),
    (
        "trg_rt_alert_recipients",
        "alert_recipients",
        "AFTER INSERT OR UPDATE ON alert_recipients FOR EACH ROW",
        "app_rt_alert_recipients",
    ),
    ("trg_rt_alerts", "alerts", "AFTER INSERT OR UPDATE ON alerts FOR EACH ROW", "app_rt_alerts"),
    ("trg_rt_alert_messages", "alert_messages", "AFTER INSERT ON alert_messages FOR EACH ROW", "app_rt_alert_messages"),
    (
        "trg_rt_incident_messages",
        "incident_messages",
        "AFTER INSERT ON incident_messages FOR EACH ROW",
        "app_rt_incident_messages",
    ),
    (
        "trg_rt_incident_participants",
        "incident_participants",
        "AFTER INSERT OR DELETE ON incident_participants FOR EACH ROW",
        "app_rt_incident_participants",
    ),
    ("trg_rt_incidents", "incidents", "AFTER UPDATE OF status ON incidents FOR EACH ROW", "app_rt_incidents"),
    (
        "trg_rt_notifications",
        "user_notifications",
        "AFTER INSERT ON user_notifications FOR EACH ROW",
        "app_rt_notifications",
    ),
    (
        "trg_rt_call_sessions",
        "call_sessions",
        "AFTER INSERT OR UPDATE ON call_sessions FOR EACH ROW",
        "app_rt_call_sessions",
    ),
    (
        "trg_rt_call_participants",
        "call_participants",
        "AFTER INSERT OR UPDATE ON call_participants FOR EACH ROW",
        "app_rt_call_participants",
    ),
    (
        "trg_rt_credentials",
        "user_credentials",
        "AFTER UPDATE OF sessions_revoked_at, password_changed_at ON user_credentials FOR EACH ROW",
        "app_rt_credentials",
    ),
    ("trg_rt_users", "users", "AFTER UPDATE OF status ON users FOR EACH ROW", "app_rt_users"),
    (
        "trg_rt_organizations",
        "organizations",
        "AFTER UPDATE OF status ON organizations FOR EACH ROW",
        "app_rt_organizations",
    ),
]

# Called by the API (granted to the app role).
_GRANTED = [
    "app_comm_target_visible(uuid, uuid)",
    "app_alert_responders(uuid)",
    "app_call_invitees(uuid, uuid)",
    "app_device_token_register(text, text, text, text)",
    "app_device_token_forget(text)",
    "app_push_claim(integer, integer)",
]
# Run only by triggers.
_TRIGGER_FUNCTIONS = [
    "app_calls_end_with_target()",
    "app_rt_alert_recipients()",
    "app_rt_alerts()",
    "app_rt_alert_messages()",
    "app_rt_incident_messages()",
    "app_rt_incident_participants()",
    "app_rt_incidents()",
    "app_rt_notifications()",
    "app_rt_call_sessions()",
    "app_rt_call_participants()",
    "app_rt_credentials()",
    "app_rt_users()",
    "app_rt_organizations()",
]

_VISIBLE = "app_comm_target_visible(alert_id, incident_id)"
_CALL_VISIBLE = "EXISTS (SELECT 1 FROM call_sessions s WHERE s.id = call_id)"
_POLICIES = [
    f"CREATE POLICY call_sessions_select ON call_sessions FOR SELECT USING ({_CAMPUS} AND {_VISIBLE})",
    f"""CREATE POLICY call_sessions_insert ON call_sessions FOR INSERT
        WITH CHECK ({_CAMPUS} AND {_VISIBLE} AND started_by = app_current_user_id()
                    AND started_by_role = app_actor_role())""",
    f"""CREATE POLICY call_sessions_update ON call_sessions FOR UPDATE
        USING ({_CAMPUS} AND {_VISIBLE}) WITH CHECK ({_CAMPUS} AND {_VISIBLE})""",
    f"CREATE POLICY call_participants_select ON call_participants FOR SELECT USING ({_CAMPUS} AND {_CALL_VISIBLE})",
    f"""CREATE POLICY call_participants_insert ON call_participants FOR INSERT
        WITH CHECK ({_CAMPUS} AND {_CALL_VISIBLE})""",
    f"""CREATE POLICY call_participants_update ON call_participants FOR UPDATE
        USING ({_CAMPUS} AND {_CALL_VISIBLE}) WITH CHECK ({_CAMPUS} AND {_CALL_VISIBLE})""",
    f"""CREATE POLICY device_push_tokens_own ON device_push_tokens FOR SELECT
        USING ({_CAMPUS} AND user_id = app_current_user_id())""",
    f"""CREATE POLICY device_push_tokens_delete ON device_push_tokens FOR DELETE
        USING ({_CAMPUS} AND user_id = app_current_user_id())""",
]
_GRANTS = {
    "call_sessions": "SELECT, INSERT, UPDATE",
    "call_participants": "SELECT, INSERT, UPDATE",
    "device_push_tokens": "SELECT, DELETE",
}


def upgrade() -> None:
    app = _app_role()
    for statement in _SCHEMA:
        op.execute(statement)
    for statement in _FUNCTIONS:
        op.execute(statement)
    for signature in _GRANTED:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {app}")
    for signature in _TRIGGER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for name, _table, timing, function in _TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {timing} EXECUTE FUNCTION {function}()")
    for table in _GRANTS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for statement in _POLICIES:
        op.execute(statement)
    for table, rights in _GRANTS.items():
        op.execute(f"GRANT {rights} ON {table} TO {app}")


def downgrade() -> None:
    for name, table, _timing, _function in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    # Tables first: their policies use app_comm_target_visible.
    op.execute("DROP TABLE IF EXISTS device_push_tokens")
    op.execute("DROP TABLE IF EXISTS call_participants")
    op.execute("DROP TABLE IF EXISTS call_sessions")
    op.execute("DROP INDEX IF EXISTS ix_user_notifications_push")
    op.execute("ALTER TABLE user_notifications DROP COLUMN IF EXISTS push_claimed_at")
    for signature in _TRIGGER_FUNCTIONS + _GRANTED:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
