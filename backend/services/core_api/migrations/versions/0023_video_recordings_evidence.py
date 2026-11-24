"""Live monitoring, recordings, storage retention and the evidence vault (requirements Sections 13,
17, 18, 21; decision D-023).

Tables (all campus-scoped; Row-Level Security; the platform role gets nothing)
------------------------------------------------------------------------------
* ``camera_status``: one row per camera: ONLINE / OFFLINE / UNKNOWN, last seen, the last short error
  text (never a URL or credential), who reported it (the ingestion service or a staff health check).
* ``campus_retention_settings``: how long routine recordings of a campus are kept (value + unit in
  MINUTES, HOURS or DAYS; 1 minute to 365 days), set by an Admin or a Supervisor of the campus. A
  campus without a row uses the server default (a deployment setting, not a fixed rule).
* ``recording_segments``: the index of Tier 1 routine recording files written by the ingestion
  service (``recordings/<org>/<campus>/<camera>/<date>/<file>.mp4`` under the storage root).
  Expiry is computed from the campus retention at purge time, never stored.
* ``evidence_items``: Tier 2 clips and snapshots in the evidence vault
  (``evidence/<org>/<campus>/<evidence id>/...``). Never expire. Admins delete them, with their
  password, in the API; the retention daemon has no access to this tier at all.
* ``storage_status``: the last disk report of the storage node (one row), for low-storage warnings.
* ``user_notifications``: new kinds CAMERA_OFFLINE, CAMERA_ONLINE (target ``camera_id``) and
  STORAGE_WARNING, STORAGE_CRITICAL (no target; sent to the Admins and Supervisors of every active campus,
  Admin spec page 23 and Supervisor spec page 5 item 7).

Functions (SECURITY DEFINER, EXECUTE for the app role only)
-----------------------------------------------------------
Called by the internal service endpoints (ingestion, retention daemon), which have no signed-in
actor, exactly like ``app_alert_ingest``:
``app_service_cameras``, ``app_camera_status_report``, ``app_recording_segment_register``,
``app_recording_segments_expired``, ``app_recording_segments_purge``, ``app_storage_report``;
and ``app_storage_status`` for signed-in Admins and Supervisors.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-16
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"
_ADMIN = f"{_CAMPUS} AND app_actor_role() = 'ADMIN'"

# A relative path inside a storage tier: no leading slash, no empty or ".." components, printable.
_PATH_CHECK = (
    r"relative_path ~ '^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$'"
    r" AND relative_path !~ '(^|/)\.\.(/|$)'"
)

_KINDS_0018 = (
    "'ALERT_NEW', 'ALERT_ESCALATED', 'ALERT_RESPONDING', 'ALERT_ASSESSMENT', 'ALERT_ASSISTANCE_REQUESTED', "
    "'ALERT_RESOLVED', 'ALERT_DISMISSED', 'MANUAL_REPORT_SUBMITTED', "
    "'GUARD_UPDATED', 'GUARD_PASSWORD_SET', 'GUARD_LOCATION_CHANGED'"
)
_NEW_KINDS = "'CAMERA_OFFLINE', 'CAMERA_ONLINE', 'STORAGE_WARNING', 'STORAGE_CRITICAL'"
_STORAGE_KINDS = "('STORAGE_WARNING', 'STORAGE_CRITICAL')"

_SCHEMA = [
    """CREATE TABLE camera_status (
           camera_id         uuid PRIMARY KEY,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           state             text NOT NULL DEFAULT 'UNKNOWN'
                                 CONSTRAINT ck_camera_status_state CHECK (state IN ('ONLINE', 'OFFLINE', 'UNKNOWN')),
           last_seen_at      timestamptz,
           last_error        text CONSTRAINT ck_camera_status_error CHECK (length(last_error) <= 300),
           source            text NOT NULL CONSTRAINT ck_camera_status_source
                                 CHECK (source IN ('INGESTION', 'HEALTH_CHECK')),
           state_changed_at  timestamptz NOT NULL DEFAULT now(),
           reported_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_camera_status_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_camera_status_campus ON camera_status (organization_id, campus_id, state)",
    """CREATE TABLE campus_retention_settings (
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           retention_value   integer NOT NULL CONSTRAINT ck_retention_value CHECK (retention_value >= 1),
           retention_unit    text NOT NULL CONSTRAINT ck_retention_unit
                                 CHECK (retention_unit IN ('MINUTES', 'HOURS', 'DAYS')),
           retention_seconds integer GENERATED ALWAYS AS (
                                 retention_value * CASE retention_unit WHEN 'MINUTES' THEN 60
                                                                       WHEN 'HOURS' THEN 3600
                                                                       ELSE 86400 END) STORED,
           updated_by        uuid REFERENCES users (id) ON DELETE SET NULL,
           updated_at        timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT pk_campus_retention_settings PRIMARY KEY (organization_id, campus_id),
           CONSTRAINT fk_campus_retention_settings_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           -- 1 minute to 365 days.
           CONSTRAINT ck_retention_range CHECK (
               retention_value * CASE retention_unit WHEN 'MINUTES' THEN 60 WHEN 'HOURS' THEN 3600 ELSE 86400 END
               BETWEEN 60 AND 31536000)
       )""",
    f"""CREATE TABLE recording_segments (
           id                uuid PRIMARY KEY,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           camera_id         uuid NOT NULL,
           started_at        timestamptz NOT NULL,
           ended_at          timestamptz NOT NULL,
           relative_path     text NOT NULL CONSTRAINT ck_recording_segments_path
                                 CHECK (length(relative_path) <= 512 AND {_PATH_CHECK}),
           size_bytes        bigint NOT NULL CONSTRAINT ck_recording_segments_size CHECK (size_bytes >= 0),
           created_at        timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT uq_recording_segments_path UNIQUE (relative_path),
           CONSTRAINT ck_recording_segments_span CHECK (ended_at > started_at),
           CONSTRAINT fk_recording_segments_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    """CREATE INDEX ix_recording_segments_camera_time
           ON recording_segments (organization_id, campus_id, camera_id, started_at DESC)""",
    "CREATE INDEX ix_recording_segments_started ON recording_segments (started_at)",
    f"""CREATE TABLE evidence_items (
           id                uuid PRIMARY KEY,
           organization_id   uuid NOT NULL,
           campus_id         uuid NOT NULL,
           kind              text NOT NULL CONSTRAINT ck_evidence_kind CHECK (kind IN ('CLIP', 'SNAPSHOT')),
           title             text NOT NULL CONSTRAINT ck_evidence_title CHECK (length(title) BETWEEN 1 AND 200),
           notes             text NOT NULL DEFAULT '' CONSTRAINT ck_evidence_notes CHECK (length(notes) <= 2000),
           investigation_ref text CONSTRAINT ck_evidence_investigation
                                 CHECK (length(investigation_ref) BETWEEN 1 AND 100),
           source            text NOT NULL CONSTRAINT ck_evidence_source CHECK (source IN ('ALERT', 'PLAYBACK')),
           alert_id          uuid,
           camera_id         uuid,
           camera_name       text NOT NULL,
           covers_from       timestamptz NOT NULL,
           covers_to         timestamptz NOT NULL,
           relative_path     text NOT NULL CONSTRAINT ck_evidence_path
                                 CHECK (length(relative_path) <= 512 AND {_PATH_CHECK}),
           media_type        text NOT NULL CONSTRAINT ck_evidence_media_type
                                 CHECK (media_type IN ('video/mp4', 'image/jpeg')),
           size_bytes        bigint NOT NULL CONSTRAINT ck_evidence_size CHECK (size_bytes >= 0),
           sha256            text NOT NULL CONSTRAINT ck_evidence_sha256 CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
           saved_by          uuid REFERENCES users (id) ON DELETE SET NULL,
           saved_by_name     text NOT NULL,
           saved_at          timestamptz NOT NULL DEFAULT now(),
           updated_at        timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT uq_evidence_path UNIQUE (relative_path),
           CONSTRAINT ck_evidence_span CHECK (covers_to >= covers_from),
           CONSTRAINT fk_evidence_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_evidence_alert FOREIGN KEY (organization_id, campus_id, alert_id)
               REFERENCES alerts (organization_id, campus_id, id) ON DELETE SET NULL,
           CONSTRAINT fk_evidence_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE SET NULL
       )""",
    "CREATE INDEX ix_evidence_campus ON evidence_items (organization_id, campus_id, saved_at DESC)",
    "CREATE INDEX ix_evidence_alert ON evidence_items (alert_id) WHERE alert_id IS NOT NULL",
    """CREATE TABLE storage_status (
           node              text PRIMARY KEY,
           total_bytes       bigint NOT NULL CONSTRAINT ck_storage_total CHECK (total_bytes >= 0),
           used_bytes        bigint NOT NULL CONSTRAINT ck_storage_used CHECK (used_bytes >= 0),
           free_bytes        bigint NOT NULL CONSTRAINT ck_storage_free CHECK (free_bytes >= 0),
           percent_used      numeric(5, 2) NOT NULL
                                 CONSTRAINT ck_storage_percent CHECK (percent_used BETWEEN 0 AND 100),
           level             text NOT NULL CONSTRAINT ck_storage_level CHECK (level IN ('OK', 'WARNING', 'CRITICAL')),
           reported_at       timestamptz NOT NULL DEFAULT now(),
           last_warned_level text CONSTRAINT ck_storage_warned CHECK (last_warned_level IN ('WARNING', 'CRITICAL')),
           last_warned_at    timestamptz
       )""",
    # Notifications: camera targets and target-less storage warnings.
    "ALTER TABLE user_notifications ADD COLUMN camera_id uuid",
    """ALTER TABLE user_notifications ADD CONSTRAINT fk_user_notifications_camera
       FOREIGN KEY (organization_id, campus_id, camera_id)
       REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target
       CHECK (CASE WHEN kind IN {_STORAGE_KINDS}
                   THEN num_nonnulls(alert_id, manual_report_id, subject_user_id, camera_id) = 0
                   ELSE num_nonnulls(alert_id, manual_report_id, subject_user_id, camera_id) = 1 END)""",
    "ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind",
    f"""ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind
       CHECK (kind IN ({_KINDS_0018}, {_NEW_KINDS}))""",
    """CREATE INDEX ix_user_notifications_camera ON user_notifications (camera_id)
       WHERE camera_id IS NOT NULL""",
]

_TABLES = ["camera_status", "campus_retention_settings", "recording_segments", "evidence_items", "storage_status"]

_POLICIES = [
    f"CREATE POLICY camera_status_select ON camera_status FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY retention_select ON campus_retention_settings FOR SELECT USING ({_STAFF})",
    # Admins and Supervisors set the retention (requirements Section 17), each with their own password.
    f"""CREATE POLICY retention_insert ON campus_retention_settings FOR INSERT
        WITH CHECK ({_STAFF} AND updated_by = app_current_user_id())""",
    f"""CREATE POLICY retention_update ON campus_retention_settings FOR UPDATE
        USING ({_STAFF}) WITH CHECK ({_STAFF} AND updated_by = app_current_user_id())""",
    f"CREATE POLICY recording_segments_select ON recording_segments FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY evidence_select ON evidence_items FOR SELECT USING ({_STAFF})",
    f"""CREATE POLICY evidence_insert ON evidence_items FOR INSERT
        WITH CHECK ({_STAFF} AND saved_by = app_current_user_id())""",
    f"CREATE POLICY evidence_update ON evidence_items FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY evidence_delete ON evidence_items FOR DELETE USING ({_ADMIN})",
    # storage_status: no policy for the app role; it is read through app_storage_status().
]

_GRANTS = {
    "camera_status": "SELECT",
    "campus_retention_settings": "SELECT, INSERT, UPDATE",
    "recording_segments": "SELECT",
    "evidence_items": "SELECT, INSERT, UPDATE, DELETE",
}

_NOTIFY_CAMERA = """INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, camera_id, title)
               SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id, {kind}, c.id, {title}
                 FROM users u
                WHERE u.organization_id = c.organization_id AND u.campus_id = c.campus_id
                  AND u.role IN ('ADMIN', 'SUPERVISOR') AND u.status = 'ACTIVE';"""

_OFFLINE_TITLE = "left('Camera offline: ' || c.display_name, 200)"
_ONLINE_TITLE = "left('Camera back online: ' || c.display_name, 200)"

_FUNCTIONS = [
    # Cameras of every active campus, for the ingestion service (no actor is bound).
    """CREATE FUNCTION app_service_cameras()
       RETURNS TABLE (camera_id uuid, organization_id uuid, campus_id uuid, display_name text,
                      connection_type text, stream_url text)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT x.id, x.organization_id, x.campus_id, x.display_name, x.connection_type, x.stream_url
             FROM cameras x
             JOIN campuses ca ON ca.organization_id = x.organization_id AND ca.id = x.campus_id AND ca.status = 'ACTIVE'
             JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
            ORDER BY x.organization_id, x.campus_id, x.display_name
       $$""",
    f"""CREATE FUNCTION app_camera_status_report(p_camera_id uuid, p_state text, p_error text,
                                                 p_seen_at timestamptz, p_source text)
       RETURNS TABLE (outcome text, previous_state text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           c record;
           v_previous text;
       BEGIN
           IF p_state NOT IN ('ONLINE', 'OFFLINE', 'UNKNOWN') OR p_source NOT IN ('INGESTION', 'HEALTH_CHECK') THEN
               RETURN QUERY SELECT 'INVALID'::text, NULL::text;
               RETURN;
           END IF;
           SELECT x.id, x.organization_id, x.campus_id, x.display_name INTO c
             FROM cameras x
             JOIN campuses ca ON ca.organization_id = x.organization_id AND ca.id = x.campus_id AND ca.status = 'ACTIVE'
             JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
            WHERE x.id = p_camera_id;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'CAMERA_NOT_FOUND'::text, NULL::text;
               RETURN;
           END IF;
           SELECT s.state INTO v_previous FROM camera_status s WHERE s.camera_id = c.id FOR UPDATE;
           IF NOT FOUND THEN
               v_previous := 'UNKNOWN';
               INSERT INTO camera_status (camera_id, organization_id, campus_id, state, last_seen_at, last_error,
                                          source, state_changed_at, reported_at)
               VALUES (c.id, c.organization_id, c.campus_id, p_state,
                       CASE WHEN p_state = 'ONLINE' THEN COALESCE(p_seen_at, now()) ELSE p_seen_at END,
                       left(p_error, 300), p_source, now(), now());
           ELSE
               UPDATE camera_status s
                  SET state = p_state,
                      last_seen_at = CASE WHEN p_state = 'ONLINE' THEN COALESCE(p_seen_at, now())
                                          ELSE COALESCE(p_seen_at, s.last_seen_at) END,
                      last_error = CASE WHEN p_state = 'ONLINE' THEN NULL ELSE left(p_error, 300) END,
                      source = p_source,
                      state_changed_at = CASE WHEN s.state = p_state THEN s.state_changed_at ELSE now() END,
                      reported_at = now()
                WHERE s.camera_id = c.id;
           END IF;
           -- Staff of the campus are told when a camera goes offline and when it comes back.
           IF v_previous <> p_state AND p_state = 'OFFLINE' THEN
               {_NOTIFY_CAMERA.format(kind="'CAMERA_OFFLINE'", title=_OFFLINE_TITLE)}
           ELSIF v_previous = 'OFFLINE' AND p_state = 'ONLINE' THEN
               {_NOTIFY_CAMERA.format(kind="'CAMERA_ONLINE'", title=_ONLINE_TITLE)}
           END IF;
           RETURN QUERY SELECT CASE WHEN v_previous = p_state THEN 'UNCHANGED' ELSE 'CHANGED' END::text, v_previous;
       END;
       $$""",
    """CREATE FUNCTION app_recording_segment_register(p_camera_id uuid, p_started_at timestamptz,
                                                      p_ended_at timestamptz, p_relative_path text, p_size_bytes bigint)
       RETURNS TABLE (segment_id uuid, outcome text)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           c record;
           v_id uuid;
       BEGIN
           SELECT x.id, x.organization_id, x.campus_id INTO c
             FROM cameras x
             JOIN campuses ca ON ca.organization_id = x.organization_id AND ca.id = x.campus_id AND ca.status = 'ACTIVE'
             JOIN organizations o ON o.id = x.organization_id AND o.status = 'ACTIVE'
            WHERE x.id = p_camera_id;
           IF NOT FOUND THEN
               RETURN QUERY SELECT NULL::uuid, 'CAMERA_NOT_FOUND'::text;
               RETURN;
           END IF;
           -- The file must live in this camera's own folder of the recordings tier.
           IF p_relative_path NOT LIKE
                  c.organization_id::text || '/' || c.campus_id::text || '/' || c.id::text || '/%' THEN
               RETURN QUERY SELECT NULL::uuid, 'PATH_OUTSIDE_CAMERA'::text;
               RETURN;
           END IF;
           SELECT s.id INTO v_id FROM recording_segments s WHERE s.relative_path = p_relative_path;
           IF FOUND THEN
               RETURN QUERY SELECT v_id, 'DUPLICATE'::text;
               RETURN;
           END IF;
           v_id := gen_random_uuid();
           INSERT INTO recording_segments (id, organization_id, campus_id, camera_id, started_at, ended_at,
                                           relative_path, size_bytes)
           VALUES (v_id, c.organization_id, c.campus_id, c.id, p_started_at, p_ended_at, p_relative_path, p_size_bytes);
           RETURN QUERY SELECT v_id, 'REGISTERED'::text;
       END;
       $$""",
    # Routine recordings past their campus's retention (or the server default), oldest first.
    """CREATE FUNCTION app_recording_segments_expired(p_default_seconds integer, p_limit integer)
       RETURNS TABLE (segment_id uuid, organization_id uuid, campus_id uuid, camera_id uuid,
                      relative_path text, started_at timestamptz, retention_seconds integer)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT s.id, s.organization_id, s.campus_id, s.camera_id, s.relative_path, s.started_at,
                  COALESCE(r.retention_seconds, p_default_seconds)
             FROM recording_segments s
             LEFT JOIN campus_retention_settings r
                    ON r.organization_id = s.organization_id AND r.campus_id = s.campus_id
            WHERE s.ended_at < now() - make_interval(secs => COALESCE(r.retention_seconds, p_default_seconds))
            ORDER BY s.started_at
            LIMIT GREATEST(1, LEAST(p_limit, 5000))
       $$""",
    """CREATE FUNCTION app_recording_segments_purge(p_segment_ids uuid[])
       RETURNS integer
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_count integer;
       BEGIN
           DELETE FROM recording_segments WHERE id = ANY (p_segment_ids);
           GET DIAGNOSTICS v_count = ROW_COUNT;
           RETURN v_count;
       END;
       $$""",
    # The storage node's disk report. Admins and Supervisors of every active campus are warned once per level
    # change (and again after 24 hours while the level stays WARNING or CRITICAL).
    """CREATE FUNCTION app_storage_report(p_node text, p_total bigint, p_used bigint, p_free bigint,
                                          p_warning_percent integer, p_critical_percent integer)
       RETURNS TABLE (level text, notified boolean)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           v_percent numeric(5, 2);
           v_level text;
           v_prev record;
           v_notify boolean := false;
       BEGIN
           v_percent := CASE WHEN p_total <= 0 THEN 0 ELSE round(100.0 * p_used / p_total, 2) END;
           v_level := CASE WHEN v_percent >= p_critical_percent THEN 'CRITICAL'
                           WHEN v_percent >= p_warning_percent THEN 'WARNING'
                           ELSE 'OK' END;
           SELECT s.level, s.last_warned_level, s.last_warned_at INTO v_prev FROM storage_status s
            WHERE s.node = p_node FOR UPDATE;
           IF v_level <> 'OK' THEN
               v_notify := NOT FOUND OR v_prev.last_warned_level IS DISTINCT FROM v_level
                           OR v_prev.last_warned_at IS NULL OR v_prev.last_warned_at < now() - interval '24 hours';
           END IF;
           INSERT INTO storage_status (node, total_bytes, used_bytes, free_bytes, percent_used, level, reported_at,
                                       last_warned_level, last_warned_at)
           VALUES (p_node, p_total, p_used, p_free, v_percent, v_level, now(),
                   CASE WHEN v_notify THEN v_level ELSE NULL END, CASE WHEN v_notify THEN now() ELSE NULL END)
           ON CONFLICT (node) DO UPDATE
              SET total_bytes = EXCLUDED.total_bytes, used_bytes = EXCLUDED.used_bytes,
                  free_bytes = EXCLUDED.free_bytes, percent_used = EXCLUDED.percent_used,
                  level = EXCLUDED.level, reported_at = now(),
                  last_warned_level = CASE WHEN v_notify THEN v_level
                                           WHEN v_level = 'OK' THEN NULL
                                           ELSE storage_status.last_warned_level END,
                  last_warned_at = CASE WHEN v_notify THEN now()
                                        WHEN v_level = 'OK' THEN NULL
                                        ELSE storage_status.last_warned_at END;
           IF v_notify THEN
               INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, title)
               SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id,
                      CASE WHEN v_level = 'CRITICAL' THEN 'STORAGE_CRITICAL' ELSE 'STORAGE_WARNING' END,
                      left('Recording storage ' || lower(v_level) || ': ' || v_percent::text || '% used', 200)
                 FROM users u
                 JOIN campuses ca ON ca.organization_id = u.organization_id AND ca.id = u.campus_id
                                 AND ca.status = 'ACTIVE'
                 JOIN organizations o ON o.id = u.organization_id AND o.status = 'ACTIVE'
                WHERE u.role IN ('ADMIN', 'SUPERVISOR') AND u.status = 'ACTIVE';
           END IF;
           RETURN QUERY SELECT v_level, v_notify;
       END;
       $$""",
    # Signed-in Admins and Supervisors see the storage node's last report (no campus data in it).
    """CREATE FUNCTION app_storage_status()
       RETURNS TABLE (node text, total_bytes bigint, used_bytes bigint, free_bytes bigint, percent_used numeric,
                      level text, reported_at timestamptz)
       LANGUAGE sql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
           SELECT s.node, s.total_bytes, s.used_bytes, s.free_bytes, s.percent_used, s.level, s.reported_at
             FROM storage_status s
            WHERE app_actor_role() IN ('ADMIN', 'SUPERVISOR')
            ORDER BY s.node
       $$""",
]

_FUNCTION_SIGNATURES = [
    "app_service_cameras()",
    "app_camera_status_report(uuid, text, text, timestamptz, text)",
    "app_recording_segment_register(uuid, timestamptz, timestamptz, text, bigint)",
    "app_recording_segments_expired(integer, integer)",
    "app_recording_segments_purge(uuid[])",
    "app_storage_report(text, bigint, bigint, bigint, integer, integer)",
    "app_storage_status()",
]


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


def downgrade() -> None:
    for signature in _FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute(f"DELETE FROM user_notifications WHERE kind IN ({_NEW_KINDS})")
    op.execute("DROP INDEX IF EXISTS ix_user_notifications_camera")
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_kind")
    op.execute(
        f"ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_kind CHECK (kind IN ({_KINDS_0018}))"
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT ck_user_notifications_target")
    op.execute(
        "ALTER TABLE user_notifications ADD CONSTRAINT ck_user_notifications_target "
        "CHECK (num_nonnulls(alert_id, manual_report_id, subject_user_id) = 1)"
    )
    op.execute("ALTER TABLE user_notifications DROP CONSTRAINT fk_user_notifications_camera")
    op.execute("ALTER TABLE user_notifications DROP COLUMN camera_id")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
