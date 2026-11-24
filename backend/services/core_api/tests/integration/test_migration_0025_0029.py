"""Migrations 0025–0029 (decision D-024) upgrade, downgrade one by one to 0024 and upgrade again on their own
scratch database: their tables, functions and constraints appear and disappear, the 0023 evidence keys are
restored exactly, and the platform (Super Admin) role has no privilege on any new table."""

from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls

SCRATCH = "cctv_migration_0025_0029_roundtrip"
TABLES = {
    "0025": {"incidents", "incident_alerts", "incident_events", "incident_participants", "incident_messages"},
    "0026": {"organization_settings", "camera_status_events"},
    "0027": {"campus_ai_settings", "camera_ai_settings", "camera_ai_status"},
    "0028": {"reid_observations", "person_searches", "person_search_candidates"},
    "0029": {"admin_profile_changes", "user_status_history"},
}
FUNCTIONS = {
    "0025": {"app_incident_participant"},
    "0026": {"app_organization_settings_save", "app_alerts_escalate_overdue", "app_alert_assistance_broadcast"},
    "0027": {"app_platform_ai_modules", "app_camera_ai_config", "app_camera_ai_status_report"},
    "0028": {"app_reid_observation_record", "app_reid_observations_purge"},
    "0029": {"app_admin_profile_change_submit", "app_display_id_regenerate", "app_user_permanently_delete"},
}
ORDER = ["0025", "0026", "0027", "0028", "0029"]


@pytest.fixture
def scratch(database_urls: DatabaseUrls) -> Iterator[tuple[Config, str]]:
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{SCRATCH}"')
    owner = make_url(database_urls.owner_server).set(database=SCRATCH)
    config = Config(str(CORE_API_DIR / "alembic.ini"))
    url = owner.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APP_DB_USER", database_urls.app_user)
        mp.setenv("PLATFORM_DB_USER", database_urls.platform_user)
        yield config, owner.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


def _state(dsn: str) -> tuple[set[str], set[str], dict[str, str]]:
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        functions = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'app_%'")}
        keys = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname IN ('fk_evidence_alert', 'fk_evidence_camera')"
            )
        }
    return tables, functions, keys


def test_each_migration_round_trips(scratch: tuple[Config, str], database_urls: DatabaseUrls) -> None:
    config, dsn = scratch
    command.upgrade(config, "0024")
    _, _, keys_0024 = _state(dsn)
    command.upgrade(config, "head")
    tables, functions, keys = _state(dsn)
    for revision in ORDER:
        assert TABLES[revision] <= tables, revision
        assert FUNCTIONS[revision] <= functions, revision
    assert all("SET NULL (" in definition for definition in keys.values())  # the 0025 fix
    with psycopg.connect(dsn) as conn:
        for table in set().union(*TABLES.values()):
            assert not conn.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT')", (database_urls.platform_user, table)
            ).fetchone()[0], table

    for index in range(len(ORDER) - 1, -1, -1):
        revision = ORDER[index]
        command.downgrade(config, ORDER[index - 1] if index else "0024")
        tables, functions, _ = _state(dsn)
        assert not TABLES[revision] & tables, revision
        assert not FUNCTIONS[revision] & functions, revision
    _, _, keys = _state(dsn)
    assert keys == keys_0024  # the 0023 definitions, exactly

    command.upgrade(config, "head")
    tables, functions, _ = _state(dsn)
    assert set().union(*TABLES.values()) <= tables
