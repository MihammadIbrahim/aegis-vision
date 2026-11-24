"""Real-time signal for every change to a case (decision D-026).

Migration 0030 signalled ``incident.updated`` only when a case's status changed. The web case page also has to follow
notes, links (alerts, reports, evidence), assignment, detail edits and chat participants as they happen. Every one of
those writes an ``incident_events`` row, so the signal now comes from that table instead: one ``incident.updated``
per recorded change, with ``state`` = the new status for a status change and ``kind`` = the event kind. As before the
signal carries IDs only; clients read the case over REST.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public"

_EVENTS_FUNCTION = f"""CREATE FUNCTION app_rt_incident_events() RETURNS trigger {_DEFINER} AS $$
    BEGIN
        PERFORM pg_notify('cctv_realtime', jsonb_strip_nulls(jsonb_build_object(
            'e', 'incident.updated', 'org', NEW.organization_id, 'campus', NEW.campus_id,
            'incident', NEW.incident_id, 'state', NEW.to_status, 'kind', NEW.kind))::text);
        RETURN NULL;
    END $$"""

# Migration 0030's status-only trigger, restored on downgrade.
_STATUS_FUNCTION = f"""CREATE FUNCTION app_rt_incidents() RETURNS trigger {_DEFINER} AS $$
    BEGIN
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            PERFORM pg_notify('cctv_realtime', jsonb_strip_nulls(jsonb_build_object(
                'e', 'incident.updated', 'org', NEW.organization_id, 'campus', NEW.campus_id,
                'incident', NEW.id, 'state', NEW.status))::text);
        END IF;
        RETURN NULL;
    END $$"""


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_rt_incidents ON incidents")
    op.execute("DROP FUNCTION IF EXISTS app_rt_incidents()")
    op.execute(_EVENTS_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION app_rt_incident_events() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_rt_incident_events AFTER INSERT ON incident_events "
        "FOR EACH ROW EXECUTE FUNCTION app_rt_incident_events()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_rt_incident_events ON incident_events")
    op.execute("DROP FUNCTION IF EXISTS app_rt_incident_events()")
    op.execute(_STATUS_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION app_rt_incidents() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_rt_incidents AFTER UPDATE OF status ON incidents "
        "FOR EACH ROW EXECUTE FUNCTION app_rt_incidents()"
    )
