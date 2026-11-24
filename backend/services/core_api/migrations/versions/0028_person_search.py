"""Person Search and cross-camera Re-ID results (Org Admin page 7, Supervisor page 7; requirements Section 16;
decision D-024).

* ``reid_observations``: a person track seen on a camera, as a 512-number appearance vector (pgvector) with
  a small crop, sent by a vision worker (``app_reid_observation_record``, service token). Only cameras that
  may run Re-ID (platform AND campus AND camera, migration 0027) are accepted. Observations are deleted
  with the campus's recording retention (``app_reid_observations_purge``); never an identity.
* ``person_searches``: a search by reference photo or by an observation, over a period and cameras, with
  the limitations found (offline cameras, Re-ID switched off, no observations) kept with it.
* ``person_search_candidates``: ranked results with similarity and a band (LIKELY_MATCH or UNCERTAIN; weaker
  results are not shown). Staff review each one; only a LIKELY_MATCH can be marked a VERIFIED_MATCH, and
  even that is a reviewed resemblance, not an identity. A result can be linked to a case and saved as evidence;
  its crop is copied when the search runs, so it outlives the observation.
* Evidence can come from a person search (``evidence_items.source`` PERSON_SEARCH).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-18
"""

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSION = 512


def _app_role() -> str:
    role = os.environ.get("APP_DB_USER", "cctv_app")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", role):
        raise ValueError(f"Invalid APP_DB_USER role name: {role!r}")
    return role


_CAMPUS = "organization_id = app_actor_org_id() AND campus_id = app_actor_campus_id()"
_STAFF = f"{_CAMPUS} AND app_actor_role() IN ('ADMIN', 'SUPERVISOR')"

_SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""CREATE TABLE reid_observations (
           id               uuid PRIMARY KEY,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           camera_id        uuid NOT NULL,
           camera_name      text NOT NULL,
           building_name    text,
           floor_name       text,
           area_name        text,
           track_id         integer NOT NULL CONSTRAINT ck_reid_observations_track CHECK (track_id >= 0),
           observed_at      timestamptz NOT NULL,
           embedding        vector({DIMENSION}) NOT NULL,
           model_version    text NOT NULL CONSTRAINT ck_reid_observations_model CHECK (length(model_version) <= 100),
           quality          numeric(5, 4) CONSTRAINT ck_reid_observations_quality CHECK (quality BETWEEN 0 AND 1),
           crop_path        text CONSTRAINT ck_reid_observations_crop CHECK (length(crop_path) <= 300),
           created_at       timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT fk_reid_observations_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE CASCADE
       )""",
    "CREATE INDEX ix_reid_observations_campus ON reid_observations (organization_id, campus_id, observed_at DESC)",
    "CREATE INDEX ix_reid_observations_camera ON reid_observations (camera_id, observed_at DESC)",
    """CREATE TABLE person_searches (
           id                       uuid PRIMARY KEY,
           organization_id          uuid NOT NULL,
           campus_id                uuid NOT NULL,
           kind                     text NOT NULL CONSTRAINT ck_person_searches_kind
                                        CHECK (kind IN ('PHOTO', 'OBSERVATION')),
           reference_observation_id uuid,
           reference_path           text CONSTRAINT ck_person_searches_reference CHECK (length(reference_path) <= 300),
           period_from              timestamptz NOT NULL,
           period_to                timestamptz NOT NULL,
           camera_ids               uuid[] NOT NULL DEFAULT '{}',
           model_version            text,
           candidate_count          integer NOT NULL DEFAULT 0,
           searched_observations    integer NOT NULL DEFAULT 0,
           limitations              jsonb NOT NULL DEFAULT '[]'::jsonb,
           note                     text CONSTRAINT ck_person_searches_note CHECK (length(note) <= 1000),
           incident_id              uuid,
           created_by               uuid REFERENCES users (id) ON DELETE SET NULL,
           created_by_name          text NOT NULL,
           created_at               timestamptz NOT NULL DEFAULT now(),
           CONSTRAINT ck_person_searches_period CHECK (period_to >= period_from),
           CONSTRAINT fk_person_searches_campus FOREIGN KEY (organization_id, campus_id)
               REFERENCES campuses (organization_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_person_searches_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE SET NULL (incident_id),
           CONSTRAINT uq_person_searches_scope UNIQUE (organization_id, campus_id, id)
       )""",
    "CREATE INDEX ix_person_searches_campus ON person_searches (organization_id, campus_id, created_at DESC)",
    """CREATE TABLE person_search_candidates (
           id               uuid PRIMARY KEY,
           search_id        uuid NOT NULL,
           organization_id  uuid NOT NULL,
           campus_id        uuid NOT NULL,
           rank             integer NOT NULL CONSTRAINT ck_candidates_rank CHECK (rank >= 1),
           observation_id   uuid,
           camera_id        uuid,
           camera_name      text NOT NULL,
           location_label   text,
           track_id         integer,
           observed_at      timestamptz NOT NULL,
           similarity       numeric(5, 4) NOT NULL CONSTRAINT ck_candidates_similarity
                                CHECK (similarity BETWEEN -1 AND 1),
           band             text NOT NULL CONSTRAINT ck_candidates_band CHECK (band IN ('LIKELY_MATCH', 'UNCERTAIN')),
           crop_path        text CONSTRAINT ck_candidates_crop CHECK (length(crop_path) <= 300),
           review           text NOT NULL DEFAULT 'PENDING' CONSTRAINT ck_candidates_review
                                CHECK (review IN ('PENDING', 'VERIFIED_MATCH', 'NOT_A_MATCH')),
           reviewed_by      uuid REFERENCES users (id) ON DELETE SET NULL,
           reviewed_by_name text,
           reviewed_at      timestamptz,
           review_note      text CONSTRAINT ck_candidates_note CHECK (length(review_note) <= 1000),
           incident_id      uuid,
           evidence_id      uuid REFERENCES evidence_items (id) ON DELETE SET NULL,
           -- Requirements 16 / page 7 item 17: an uncertain result can never be marked a verified match.
           CONSTRAINT ck_candidates_verified_needs_likely CHECK (review <> 'VERIFIED_MATCH' OR band = 'LIKELY_MATCH'),
           CONSTRAINT ck_candidates_reviewed CHECK ((review = 'PENDING') = (reviewed_at IS NULL)),
           CONSTRAINT fk_candidates_search FOREIGN KEY (organization_id, campus_id, search_id)
               REFERENCES person_searches (organization_id, campus_id, id) ON DELETE CASCADE,
           CONSTRAINT fk_candidates_camera FOREIGN KEY (organization_id, campus_id, camera_id)
               REFERENCES cameras (organization_id, campus_id, id) ON DELETE SET NULL (camera_id),
           CONSTRAINT fk_candidates_incident FOREIGN KEY (organization_id, campus_id, incident_id)
               REFERENCES incidents (organization_id, campus_id, id) ON DELETE SET NULL (incident_id)
       )""",
    "CREATE INDEX ix_candidates_search ON person_search_candidates (search_id, rank)",
    "CREATE INDEX ix_candidates_incident ON person_search_candidates (incident_id) WHERE incident_id IS NOT NULL",
    "ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_source",
    """ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_source
       CHECK (source IN ('ALERT', 'PLAYBACK', 'PERSON_SEARCH'))""",
]

_TABLES = ["reid_observations", "person_searches", "person_search_candidates"]

_POLICIES = [
    f"CREATE POLICY reid_observations_select ON reid_observations FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY person_searches_select ON person_searches FOR SELECT USING ({_STAFF})",
    f"""CREATE POLICY person_searches_insert ON person_searches FOR INSERT
        WITH CHECK ({_STAFF} AND created_by = app_current_user_id())""",
    f"CREATE POLICY person_searches_update ON person_searches FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
    f"CREATE POLICY candidates_select ON person_search_candidates FOR SELECT USING ({_STAFF})",
    f"CREATE POLICY candidates_insert ON person_search_candidates FOR INSERT WITH CHECK ({_STAFF})",
    f"CREATE POLICY candidates_update ON person_search_candidates FOR UPDATE USING ({_STAFF}) WITH CHECK ({_STAFF})",
]

_GRANTS = {
    "reid_observations": "SELECT",
    "person_searches": "SELECT, INSERT, UPDATE",
    "person_search_candidates": "SELECT, INSERT, UPDATE",
}

_FUNCTIONS = [
    # A vision worker records one observation (service token, no signed-in user). Refused when the camera
    # may not run Re-ID; the camera's name and location are copied.
    f"""CREATE FUNCTION app_reid_observation_record(p_id uuid, p_camera_id uuid, p_track_id integer,
                                                    p_observed_at timestamptz, p_embedding text,
                                                    p_model_version text, p_quality numeric, p_crop_path text)
       RETURNS TABLE (outcome text, organization_id uuid, campus_id uuid)
       LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
       #variable_conflict use_column
       DECLARE
           c record;
           v_allowed boolean;
       BEGIN
           SELECT x.id, x.organization_id, x.campus_id, x.display_name, b.name AS building_name,
                  f.name AS floor_name, a.name AS area_name
             INTO c
             FROM cameras x
             LEFT JOIN areas a ON a.id = x.area_id
             LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, a.floor_id)
             LEFT JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, a.building_id)
            WHERE x.id = p_camera_id;
           IF NOT FOUND THEN
               RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::uuid, NULL::uuid;
               RETURN;
           END IF;
           SELECT cfg.person_reid INTO v_allowed FROM app_camera_ai_config(p_camera_id) cfg;
           IF NOT COALESCE(v_allowed, false) THEN
               RETURN QUERY SELECT 'REID_DISABLED'::text, c.organization_id, c.campus_id;
               RETURN;
           END IF;
           INSERT INTO reid_observations (id, organization_id, campus_id, camera_id, camera_name, building_name,
                                          floor_name, area_name, track_id, observed_at, embedding, model_version,
                                          quality, crop_path)
           VALUES (p_id, c.organization_id, c.campus_id, c.id, c.display_name, c.building_name, c.floor_name,
                   c.area_name, p_track_id, p_observed_at, CAST(p_embedding AS vector({DIMENSION})),
                   p_model_version, p_quality, p_crop_path);
           RETURN QUERY SELECT 'RECORDED'::text, c.organization_id, c.campus_id;
       END $$""",
    # Observations older than their campus's recording retention go; the crops to delete are returned.
    """CREATE FUNCTION app_reid_observations_purge(p_default_seconds integer, p_limit integer)
       RETURNS TABLE (organization_id uuid, campus_id uuid, crop_path text)
       LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
           WITH expired AS (
               SELECT o.id FROM reid_observations o
                 LEFT JOIN campus_retention_settings r
                        ON r.organization_id = o.organization_id AND r.campus_id = o.campus_id
                WHERE o.observed_at < now() - make_interval(secs => COALESCE(
                          r.retention_value * CASE r.retention_unit WHEN 'MINUTES' THEN 60 WHEN 'HOURS' THEN 3600
                                                                    ELSE 86400 END,
                          p_default_seconds))
                ORDER BY o.observed_at
                LIMIT greatest(1, least(p_limit, 5000))
           )
           DELETE FROM reid_observations o USING expired e WHERE o.id = e.id
           RETURNING o.organization_id, o.campus_id, o.crop_path
       $$""",
]

_FUNCTION_SIGNATURES = [
    "app_reid_observation_record(uuid, uuid, integer, timestamptz, text, text, numeric, text)",
    "app_reid_observations_purge(integer, integer)",
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
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DELETE FROM evidence_items WHERE source = 'PERSON_SEARCH'")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_source")
    op.execute("ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_source CHECK (source IN ('ALERT', 'PLAYBACK'))")
    # The vector extension stays installed: other objects may use it.
