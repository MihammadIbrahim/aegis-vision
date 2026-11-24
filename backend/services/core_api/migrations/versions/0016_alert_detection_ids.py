"""Idempotent detection intake for every detection, not only the first of an alert.

0015 remembered only the detection ID that *created* an alert (``alerts.detection_id``). A detection
merged into an open alert (REPEATED) left no trace of its ID, so sending it again merged it again and
inflated ``detection_count``. Found by the cv_inference end-to-end test, which re-runs a video.

* ``alert_detection_ids``: every detection ID accepted for a camera, and the alert it went to. Only
  the intake function writes it; the app role has no access. Rows go with their alert.
* ``app_alert_ingest`` checks this table for duplicates and records each created or merged detection.
  Existing ``alerts.detection_id`` values are copied in.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-31
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"_revision_{filename[:4]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ingest_0015() -> str:
    functions = _load("0015_alert_management.py")._FUNCTIONS
    return next(f for f in functions if "FUNCTION app_alert_ingest(" in f)


_DUPLICATE_0015 = "SELECT a.id INTO v_alert FROM alerts a WHERE a.camera_id = c.id AND a.detection_id = p_detection_id;"
_DUPLICATE = (
    "SELECT d.alert_id INTO v_alert FROM alert_detection_ids d "
    "WHERE d.camera_id = c.id AND d.detection_id = p_detection_id;"
)
_RECORD = """IF p_detection_id IS NOT NULL THEN
                   INSERT INTO alert_detection_ids (camera_id, detection_id, alert_id)
                   VALUES (c.id, p_detection_id, v_alert) ON CONFLICT DO NOTHING;
               END IF;
               """
_REPEATED = "RETURN QUERY SELECT v_alert, 'REPEATED'::text;"
_CREATED = "RETURN QUERY SELECT v_alert, 'CREATED'::text;"


def _ingest() -> str:
    old = _ingest_0015()
    for anchor in (_DUPLICATE_0015, _REPEATED, _CREATED):
        assert old.count(anchor) == 1, anchor
    new = (
        old.replace(_DUPLICATE_0015, _DUPLICATE)
        .replace(_REPEATED, _RECORD + _REPEATED)
        .replace(_CREATED, _RECORD.replace("               ", "           ") + _CREATED)
    )
    return new.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def upgrade() -> None:
    op.execute(
        """CREATE TABLE alert_detection_ids (
               camera_id     uuid NOT NULL,
               detection_id  text NOT NULL CONSTRAINT ck_alert_detection_ids_length
                                 CHECK (length(detection_id) BETWEEN 1 AND 128),
               alert_id      uuid NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
               received_at   timestamptz NOT NULL DEFAULT now(),
               PRIMARY KEY (camera_id, detection_id)
           )"""
    )
    op.execute("CREATE INDEX ix_alert_detection_ids_alert ON alert_detection_ids (alert_id)")
    op.execute("ALTER TABLE alert_detection_ids ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON alert_detection_ids FROM PUBLIC")
    op.execute(
        """INSERT INTO alert_detection_ids (camera_id, detection_id, alert_id)
           SELECT camera_id, detection_id, id FROM alerts WHERE detection_id IS NOT NULL"""
    )
    op.execute(_ingest())


def downgrade() -> None:
    op.execute(_ingest_0015().replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute("DROP TABLE IF EXISTS alert_detection_ids")
