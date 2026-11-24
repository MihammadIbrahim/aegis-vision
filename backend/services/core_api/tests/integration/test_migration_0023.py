"""Migration 0023 upgrades, downgrades to 0022 and upgrades again on its own scratch database, and
the downgrade restores the notification constraints of 0018."""

from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls

SCRATCH = "cctv_migration_0023_roundtrip"
TABLES = ("camera_status", "campus_retention_settings", "recording_segments", "evidence_items", "storage_status")


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


def _state(dsn: str) -> tuple[set[str], str, bool]:
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        kinds = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_user_notifications_kind'"
        ).fetchone()[0]
        camera_column = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'user_notifications' AND column_name = 'camera_id'"
        ).fetchone()
    return tables, kinds, camera_column is not None


def test_upgrade_downgrade_upgrade(scratch: tuple[Config, str], database_urls: DatabaseUrls) -> None:
    config, dsn = scratch
    command.upgrade(config, "head")
    tables, kinds, camera_column = _state(dsn)
    assert set(TABLES) <= tables and "CAMERA_OFFLINE" in kinds and camera_column
    with psycopg.connect(dsn) as conn:
        functions = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'app_%'")}
        assert {"app_camera_status_report", "app_recording_segments_expired", "app_storage_report"} <= functions
        # The platform (Super Admin) role has no privilege on any new table.
        for table in TABLES:
            assert not conn.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT')", (database_urls.platform_user, table)
            ).fetchone()[0]

    command.downgrade(config, "0022")
    tables, kinds, camera_column = _state(dsn)
    assert not set(TABLES) & tables and "CAMERA_OFFLINE" not in kinds and "GUARD_UPDATED" in kinds
    assert not camera_column

    command.upgrade(config, "head")
    tables, kinds, camera_column = _state(dsn)
    assert set(TABLES) <= tables and "STORAGE_WARNING" in kinds and camera_column
