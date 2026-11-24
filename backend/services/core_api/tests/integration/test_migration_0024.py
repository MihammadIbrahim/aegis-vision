"""Migration 0024 upgrades, downgrades to 0023 and upgrades again on its own scratch database: audit
categories and outcomes, the read policies, the campus-request erasure columns and the new functions."""

from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls

SCRATCH = "cctv_migration_0024_roundtrip"
FUNCTIONS = {"app_audit_category", "app_org_audit_auth_event", "app_org_audit_reset_completed", "app_step_up_consume"}


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


def _columns(conn: psycopg.Connection, table: str) -> set[str]:
    rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {r[0] for r in rows}


def _state(dsn: str, app_user: str) -> dict[str, object]:
    with psycopg.connect(dsn) as conn:
        functions = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE proname LIKE 'app_%'")}
        policies = {
            r[0] for r in conn.execute("SELECT policyname FROM pg_policies WHERE tablename = 'organization_audit_logs'")
        }
        nullable = conn.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'campus_registration_requests' AND column_name = 'applicant_name'"
        ).fetchone()[0]
        can_read = conn.execute(
            "SELECT has_table_privilege(%s, 'organization_audit_logs', 'SELECT')", (app_user,)
        ).fetchone()[0]
        return {
            "audit_columns": _columns(conn, "organization_audit_logs"),
            "request_columns": _columns(conn, "campus_registration_requests"),
            "functions": functions,
            "policies": policies,
            "nullable": nullable,
            "app_can_read": can_read,
        }


def test_upgrade_downgrade_upgrade(scratch: tuple[Config, str], database_urls: DatabaseUrls) -> None:
    config, dsn = scratch
    command.upgrade(config, "0024")
    state = _state(dsn, database_urls.app_user)
    assert {"category", "outcome"} <= state["audit_columns"]  # type: ignore[operator]
    assert "details_erased_at" in state["request_columns"]  # type: ignore[operator]
    assert FUNCTIONS <= state["functions"]  # type: ignore[operator]
    assert {"org_audit_select_admin", "org_audit_select_supervisor"} <= state["policies"]  # type: ignore[operator]
    assert state["nullable"] == "YES" and state["app_can_read"] is True
    with psycopg.connect(dsn) as conn:
        categories = {
            action: conn.execute("SELECT app_audit_category(%s, %s)", (action, role)).fetchone()[0]
            for action, role in (
                ("ALERT_ACKNOWLEDGE", "GUARD"),
                ("ALERT_CLOSED", "SUPERVISOR"),
                ("ALERT_MESSAGE_POSTED", "SUPERVISOR"),
                ("CAMERA_CREATED", "ADMIN"),
                ("AREA_ACCESS_GRANTED", "ADMIN"),
                ("AREA_CREATED", "ADMIN"),
                ("AUTH_SIGN_IN_FAILED", "ADMIN"),
                ("CAMPUS_REQUEST_APPROVED", "ADMIN"),
                ("RECORDING_RETENTION_CHANGED", "SUPERVISOR"),
                ("SOMETHING_NEW", "ADMIN"),
            )
        }
        assert categories == {
            "ALERT_ACKNOWLEDGE": "GUARD_RESPONSE",
            "ALERT_CLOSED": "ALERTS",
            "ALERT_MESSAGE_POSTED": "INCIDENT_CHAT",
            "CAMERA_CREATED": "CAMERA_LOCATION",
            "AREA_ACCESS_GRANTED": "PERSONNEL",
            "AREA_CREATED": "CAMERA_LOCATION",
            "AUTH_SIGN_IN_FAILED": "AUTHENTICATION",
            "CAMPUS_REQUEST_APPROVED": "ORGANIZATION",
            "RECORDING_RETENTION_CHANGED": "RETENTION",
            "SOMETHING_NEW": "OTHER",
        }
        # The platform (Super Admin) role cannot run the new functions.
        for name in FUNCTIONS - {"app_audit_category"}:
            assert not conn.execute(
                "SELECT bool_or(has_function_privilege(%s, p.oid, 'EXECUTE')) FROM pg_proc p WHERE proname = %s",
                (database_urls.platform_user, name),
            ).fetchone()[0], name

    command.downgrade(config, "0023")
    state = _state(dsn, database_urls.app_user)
    assert not {"category", "outcome"} & state["audit_columns"]  # type: ignore[operator]
    assert "details_erased_at" not in state["request_columns"]  # type: ignore[operator]
    assert not FUNCTIONS & state["functions"]  # type: ignore[operator]
    assert not {"org_audit_select_admin", "org_audit_select_supervisor"} & state["policies"]  # type: ignore[operator]
    assert state["nullable"] == "NO" and state["app_can_read"] is False

    command.upgrade(config, "0024")
    assert FUNCTIONS <= _state(dsn, database_urls.app_user)["functions"]  # type: ignore[operator]
