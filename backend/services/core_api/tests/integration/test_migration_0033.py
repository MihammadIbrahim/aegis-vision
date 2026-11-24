"""Migration 0033 (decision D-028) upgrades, downgrades to 0032 and upgrades again on its own scratch database: the
block table, the Supervisor-visibility columns, the camera defaults and the functions appear and disappear; cameras
that existed before keep running exactly what they ran (a row with every module selected), new cameras run nothing;
and only the platform role may call the platform functions."""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DatabaseUrls

SCRATCH = "cctv_migration_0033_roundtrip"
FUNCTIONS = {
    "platform_organization_ai_permissions_get",
    "platform_organization_ai_permissions_set",
    "app_organization_ai_blocks",
}
VISIBLE = {
    "supervisor_can_manage_human_detection_tracking",
    "supervisor_can_manage_person_reid",
    "supervisor_can_manage_weapon_detection",
    "supervisor_can_manage_fight_violence_detection",
    "supervisor_can_manage_restricted_area_face_recognition",
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


def _state(dsn: str) -> tuple[bool, set[str], set[str], dict[str, str]]:
    with psycopg.connect(dsn) as conn:
        table = conn.execute("SELECT to_regclass('platform_organization_ai_blocks') IS NOT NULL").fetchone()[0]
        functions = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE proname = ANY (%s)",
                                                (list(FUNCTIONS),))}  # fmt: skip
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'campus_ai_settings'"
            )
        }
        defaults = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name = 'camera_ai_settings' AND data_type = 'boolean'"
            )
        }
    return table, functions, columns & VISIBLE, defaults


def _camera_config(dsn: str, camera: object) -> tuple[bool, ...]:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT human_detection, person_reid, weapon_detection, fight_detection, face_recognition "
            "FROM app_camera_ai_config(%s)",
            (camera,),
        ).fetchone()
    assert row is not None
    return tuple(row)


def test_0033_round_trips_and_keeps_existing_cameras_running(
    scratch: tuple[Config, str], database_urls: DatabaseUrls
) -> None:
    config, dsn = scratch
    command.upgrade(config, "0032")
    org, campus, old_camera, new_camera = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO organizations (id, name) VALUES (%s, 'Round Trip')", (org,))
        conn.execute("INSERT INTO campuses (id, organization_id, name, status) VALUES (%s, %s, 'Main', 'ACTIVE')",
                     (campus, org))  # fmt: skip
        conn.execute("INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (%s, %s, %s, 'Old')",
                     (old_camera, org, campus))  # fmt: skip
    before = _camera_config(dsn, old_camera)
    assert before == (True, True, True, True, False)  # 0027: no row = every permitted module

    command.upgrade(config, "head")
    table, functions, columns, defaults = _state(dsn)
    assert table and functions == FUNCTIONS and columns == VISIBLE
    assert set(defaults.values()) == {"false"}
    assert _camera_config(dsn, old_camera) == before  # backfilled: still runs what it ran
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (%s, %s, %s, 'New')",
                     (new_camera, org, campus))  # fmt: skip
        privileges = {
            (role, function): conn.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                                           (role, function)).fetchone()[0]  # fmt: skip
            for role in (database_urls.app_user, database_urls.platform_user)
            for function in ("platform_organization_ai_permissions_set(text, text, boolean, uuid)",
                             "app_organization_ai_blocks()")  # fmt: skip
        }
        table_rights = [
            conn.execute("SELECT has_table_privilege(%s, 'platform_organization_ai_blocks', 'SELECT')",
                         (role,)).fetchone()[0]  # fmt: skip
            for role in (database_urls.app_user, database_urls.platform_user)
        ]
    assert _camera_config(dsn, new_camera) == (False, False, False, False, False)
    assert privileges == {
        (database_urls.app_user, "platform_organization_ai_permissions_set(text, text, boolean, uuid)"): False,
        (database_urls.app_user, "app_organization_ai_blocks()"): True,
        (database_urls.platform_user, "platform_organization_ai_permissions_set(text, text, boolean, uuid)"): True,
        (database_urls.platform_user, "app_organization_ai_blocks()"): False,
    }
    assert table_rights == [False, False]

    command.downgrade(config, "0032")
    table, functions, columns, defaults = _state(dsn)
    assert not table and not functions and not columns and set(defaults.values()) == {"true"}
    assert _camera_config(dsn, old_camera) == before
    assert _camera_config(dsn, new_camera) == before  # 0027 semantics again: no row = every permitted module

    command.upgrade(config, "head")
    table, functions, columns, defaults = _state(dsn)
    assert table and functions == FUNCTIONS and columns == VISIBLE and set(defaults.values()) == {"false"}
