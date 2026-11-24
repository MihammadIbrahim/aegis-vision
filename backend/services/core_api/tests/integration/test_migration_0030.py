"""Migration 0030 (decision D-025) upgrades, downgrades to 0029 and upgrades again on its own scratch database: its
tables, functions, triggers and the new notification column appear and disappear, and the platform (Super Admin) role
has no privilege on the new tables."""

from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls

SCRATCH = "cctv_migration_0030_roundtrip"
TABLES = {"call_sessions", "call_participants", "device_push_tokens"}
FUNCTIONS = {
    "app_comm_target_visible",
    "app_alert_responders",
    "app_call_invitees",
    "app_device_token_register",
    "app_device_token_forget",
    "app_push_claim",
    "app_calls_end_with_target",
    "app_rt_alert_recipients",
    "app_rt_alerts",
    "app_rt_alert_messages",
    "app_rt_incident_messages",
    "app_rt_incident_participants",
    "app_rt_incidents",
    "app_rt_notifications",
    "app_rt_call_sessions",
    "app_rt_call_participants",
    "app_rt_credentials",
    "app_rt_users",
    "app_rt_organizations",
}


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


def _state(dsn: str) -> tuple[set[str], set[str], bool, int]:
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        functions = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'app_%'")}
        column = bool(
            conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'user_notifications' AND column_name = 'push_claimed_at'"
            ).fetchone()
        )
        triggers = conn.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_rt_%' OR tgname LIKE 'trg_%_end_calls'"
        ).fetchone()
    assert triggers is not None
    return tables, functions, column, int(triggers[0])


def test_migration_0030_round_trips(scratch: tuple[Config, str], database_urls: DatabaseUrls) -> None:
    config, dsn = scratch
    command.upgrade(config, "0030")
    tables, functions, column, triggers = _state(dsn)
    assert TABLES <= tables and FUNCTIONS <= functions and column and triggers == 14
    with psycopg.connect(dsn) as conn:
        for table in TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                allowed = conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s)", (database_urls.platform_user, table, privilege)
                ).fetchone()
                assert allowed == (False,), (table, privilege)
        # The app role may not call the trigger-only functions.
        callable_ = conn.execute(
            "SELECT has_function_privilege(%s, 'app_rt_users()', 'EXECUTE')", (database_urls.app_user,)
        ).fetchone()
        assert callable_ == (False,)
    command.downgrade(config, "0029")
    tables, functions, column, triggers = _state(dsn)
    assert not (TABLES & tables) and not (FUNCTIONS & functions) and not column and triggers == 0
    command.upgrade(config, "0030")
    tables, functions, column, triggers = _state(dsn)
    assert TABLES <= tables and FUNCTIONS <= functions and column and triggers == 14
