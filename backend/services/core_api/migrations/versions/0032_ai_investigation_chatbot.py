"""Read-only AI Investigation & Security Analytics Chatbot: chat sessions and messages (decision D-027).

* ``ai_chat_sessions``: a user's conversation on their own campus, optionally about one case
  (``context_incident_id``, set to NULL when the case row is deleted). ``last_active_at`` drives the 30-day purge.
* ``ai_chat_messages``: the user's questions and the grounded answers: ``citations`` (records the answer used),
  ``tool_calls`` (read-only tools with sanitised arguments, outcome, row count and latency), ``blocks`` (tables,
  metrics, bars, timelines and notices for the web app), ``context`` (period, place, case of the conversation for
  follow-up questions), ``insufficient_evidence``, ``provider`` and ``model_name``. No row data is copied beyond what
  the answer shows.
* Row-Level Security: Admins and Supervisors read, write and delete **only their own** sessions and messages on their
  own organization and campus. Guards and other users see nothing; the platform role has no privilege.
* ``app_ai_chat_purge(days, limit)``: deletes sessions inactive for longer than ``AI_CHAT_RETENTION_DAYS``; called by
  the storage retention daemon through ``POST /internal/assistant/purge-expired``.
* Erasure: the tables cascade from ``campuses`` (campus deletion, D-008 / D-011) and from ``users``; a user's chats are
  also deleted when their personal data is permanently deleted (``users.deleted_at`` set, D-024).
* ``app_audit_category``: actions ``ASSISTANT_*`` get their own category ``AI_ASSISTANT``. It is not an operational
  category, so a Supervisor sees only their own chatbot audit records and never an Admin's questions.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-19
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = "app_actor_role() IN ('ADMIN', 'SUPERVISOR')"


def _role(variable: str, default: str) -> str:
    role = os.environ.get(variable, default)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid {variable} role name: {role!r}")
    return role


# The 0024 category function (unchanged there); 0032 adds the ASSISTANT_ line at the top.
_CATEGORY_CASES = """
               WHEN p_action IN ('ALERT_MESSAGE_POSTED', 'INCIDENT_MESSAGE_POSTED') THEN 'INCIDENT_CHAT'
               WHEN p_actor_role = 'GUARD' AND p_action LIKE 'ALERT\\_%' THEN 'GUARD_RESPONSE'
               WHEN p_action LIKE 'ALERT\\_%' THEN 'ALERTS'
               WHEN p_action LIKE 'INCIDENT\\_%' THEN 'INCIDENTS'
               WHEN p_action LIKE 'EVIDENCE\\_%' THEN 'EVIDENCE'
               WHEN p_action IN ('RECORDING_RETENTION_CHANGED') OR p_action LIKE 'STORAGE\\_%' THEN 'RETENTION'
               WHEN p_action LIKE 'LIVE\\_%' OR p_action LIKE 'RECORDING\\_%' THEN 'VIDEO'
               WHEN p_action LIKE 'AI\\_%' OR p_action LIKE 'CAMERA\\_AI\\_%' THEN 'AI_SETTINGS'
               WHEN p_action LIKE 'CAMERA\\_%' OR p_action LIKE 'LOCATION\\_%' OR p_action LIKE 'BUILDING\\_%'
                    OR p_action LIKE 'FLOOR\\_%' OR p_action LIKE 'AREA\\_%' AND p_action NOT LIKE 'AREA\\_ACCESS%'
                    THEN 'CAMERA_LOCATION'
               WHEN p_action LIKE 'PERSON\\_SEARCH\\_%' OR p_action LIKE 'REID\\_%' THEN 'PERSON_SEARCH'
               WHEN p_action LIKE 'MANUAL\\_REPORT\\_%' THEN 'MANUAL_REPORTS'
               WHEN p_action LIKE 'PERSONNEL\\_%' OR p_action LIKE 'RESTRICTED\\_%' OR p_action LIKE 'AREA\\_ACCESS%'
                    OR p_action LIKE 'ACCESS\\_%' THEN 'PERSONNEL'
               WHEN p_action LIKE 'AUTH\\_%' OR p_action LIKE 'LOGIN\\_%' OR p_action LIKE 'PASSWORD\\_RESET%'
                    THEN 'AUTHENTICATION'
               WHEN p_action LIKE 'STAFF\\_%' OR p_action LIKE 'GUARD\\_%' OR p_action LIKE 'SUPERVISOR\\_%'
                    OR p_action LIKE 'USER\\_%' OR p_action LIKE 'PROFILE\\_%' OR p_action LIKE 'ACCOUNT\\_%'
                    OR p_action LIKE 'OWNERSHIP\\_%' THEN 'USER_MANAGEMENT'
               WHEN p_action LIKE 'ORGANIZATION\\_%' OR p_action LIKE 'CAMPUS\\_%' OR p_action LIKE 'SETTINGS\\_%'
                    OR p_action LIKE 'AUDIT\\_%' OR p_action LIKE 'REPORT\\_%' THEN 'ORGANIZATION'
               ELSE 'OTHER'"""


def _category_function(extra: str) -> str:
    return f"""CREATE OR REPLACE FUNCTION app_audit_category(p_action text, p_actor_role text) RETURNS text
       LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
           SELECT CASE{extra}{_CATEGORY_CASES}
           END
       $$"""


_ASSISTANT_CASE = "\n               WHEN p_action LIKE 'ASSISTANT\\_%' THEN 'AI_ASSISTANT'"

_TABLES = [
    """CREATE TABLE ai_chat_sessions (
           id                   uuid PRIMARY KEY,
           organization_id      uuid NOT NULL,
           campus_id            uuid NOT NULL,
           user_id              uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
           title                text NOT NULL CONSTRAINT ck_ai_chat_sessions_title
                                    CHECK (length(btrim(title)) BETWEEN 1 AND 120),
           context_incident_id  uuid,
           created_at           timestamptz NOT NULL DEFAULT now(),
           updated_at           timestamptz NOT NULL DEFAULT now(),
           last_active_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT uq_ai_chat_sessions_scope UNIQUE (organization_id, campus_id, id),
           CONSTRAINT fk_ai_chat_sessions_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_ai_chat_sessions_incident FOREIGN KEY (organization_id, campus_id, context_incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE SET NULL (context_incident_id)
       )""",
    "CREATE INDEX ix_ai_chat_sessions_user ON ai_chat_sessions (user_id, last_active_at DESC)",
    "CREATE INDEX ix_ai_chat_sessions_last_active ON ai_chat_sessions (last_active_at)",
    """CREATE TABLE ai_chat_messages (
           id                     uuid PRIMARY KEY,
           session_id             uuid NOT NULL,
           organization_id        uuid NOT NULL,
           campus_id              uuid NOT NULL,
           role                   text NOT NULL
                                      CONSTRAINT ck_ai_chat_messages_role CHECK (role IN ('USER', 'ASSISTANT')),
           content                text NOT NULL CONSTRAINT ck_ai_chat_messages_content
                                      CHECK (length(content) BETWEEN 1 AND 20000),
           citations              jsonb NOT NULL DEFAULT '[]'
                                      CONSTRAINT ck_ai_chat_messages_citations
                                      CHECK (jsonb_typeof(citations) = 'array'),
           tool_calls             jsonb NOT NULL DEFAULT '[]'
                                      CONSTRAINT ck_ai_chat_messages_tools CHECK (jsonb_typeof(tool_calls) = 'array'),
           blocks                 jsonb NOT NULL DEFAULT '[]'
                                      CONSTRAINT ck_ai_chat_messages_blocks CHECK (jsonb_typeof(blocks) = 'array'),
           context                jsonb NOT NULL DEFAULT '{}'
                                      CONSTRAINT ck_ai_chat_messages_context CHECK (jsonb_typeof(context) = 'object'),
           insufficient_evidence  boolean NOT NULL DEFAULT false,
           provider               text CONSTRAINT ck_ai_chat_messages_provider
                                      CHECK (provider IN ('deterministic', 'ollama', 'openai_compatible')),
           model_name             text CONSTRAINT ck_ai_chat_messages_model CHECK (length(model_name) <= 120),
           created_at             timestamptz NOT NULL DEFAULT clock_timestamp(),
           CONSTRAINT ck_ai_chat_messages_answer CHECK ((role = 'ASSISTANT') = (provider IS NOT NULL)),
           CONSTRAINT fk_ai_chat_messages_session FOREIGN KEY (organization_id, campus_id, session_id)
               REFERENCES ai_chat_sessions (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_ai_chat_messages_session ON ai_chat_messages (session_id, created_at, id)",
]

_OWN_SESSION = "EXISTS (SELECT 1 FROM ai_chat_sessions s WHERE s.id = session_id AND s.user_id = app_current_user_id())"
_POLICIES = [
    "ALTER TABLE ai_chat_sessions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ai_chat_messages ENABLE ROW LEVEL SECURITY",
    f"""CREATE POLICY ai_chat_sessions_own ON ai_chat_sessions FOR ALL
        USING ({_CAMPUS} AND {_STAFF} AND user_id = app_current_user_id())
        WITH CHECK ({_CAMPUS} AND {_STAFF} AND user_id = app_current_user_id())""",
    f"""CREATE POLICY ai_chat_messages_own ON ai_chat_messages FOR ALL
        USING ({_CAMPUS} AND {_STAFF} AND {_OWN_SESSION})
        WITH CHECK ({_CAMPUS} AND {_STAFF} AND {_OWN_SESSION})""",
]

_FUNCTIONS = [
    """CREATE FUNCTION app_ai_chat_purge(p_days integer, p_limit integer) RETURNS integer
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       DECLARE
           v_deleted integer;
       BEGIN
           IF p_days IS NULL OR p_days < 1 THEN
               RAISE EXCEPTION 'chat retention must be at least one day' USING ERRCODE = '22023';
           END IF;
           WITH old AS (
               SELECT id FROM ai_chat_sessions
                WHERE last_active_at < now() - make_interval(days => p_days)
                ORDER BY last_active_at
                LIMIT LEAST(GREATEST(COALESCE(p_limit, 1000), 1), 10000)
                FOR UPDATE SKIP LOCKED)
           DELETE FROM ai_chat_sessions s USING old WHERE s.id = old.id;
           GET DIAGNOSTICS v_deleted = ROW_COUNT;
           RETURN v_deleted;
       END
       $$""",
    """CREATE FUNCTION app_ai_chat_forget_user() RETURNS trigger
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       BEGIN
           DELETE FROM ai_chat_sessions WHERE user_id = NEW.id;
           RETURN NEW;
       END
       $$""",
    """CREATE TRIGGER trg_ai_chat_forget_user AFTER UPDATE OF deleted_at ON users
       FOR EACH ROW WHEN (NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL)
       EXECUTE FUNCTION app_ai_chat_forget_user()""",
]


def upgrade() -> None:
    app, platform = _role("APP_DB_USER", "cctv_app"), _role("PLATFORM_DB_USER", "cctv_platform")
    for statement in [*_TABLES, *_POLICIES, *_FUNCTIONS]:
        op.execute(statement)
    op.execute(_category_function(_ASSISTANT_CASE))
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ai_chat_sessions TO {app}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON ai_chat_messages TO {app}")
    op.execute(f"REVOKE ALL ON ai_chat_sessions, ai_chat_messages FROM PUBLIC, {platform}")
    op.execute("REVOKE ALL ON FUNCTION app_ai_chat_purge(integer, integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_ai_chat_purge(integer, integer) TO {app}")
    op.execute("REVOKE ALL ON FUNCTION app_ai_chat_forget_user() FROM PUBLIC")


def downgrade() -> None:
    op.execute(_category_function(""))
    op.execute("DROP TRIGGER IF EXISTS trg_ai_chat_forget_user ON users")
    op.execute("DROP TABLE IF EXISTS ai_chat_messages")
    op.execute("DROP TABLE IF EXISTS ai_chat_sessions")
    op.execute("DROP FUNCTION IF EXISTS app_ai_chat_forget_user()")
    op.execute("DROP FUNCTION IF EXISTS app_ai_chat_purge(integer, integer)")
