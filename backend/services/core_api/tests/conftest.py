"""Shared fixtures.

Unit tests need nothing. Database tests need a real PostgreSQL 16 server, and are
skipped unless these variables are set:

  TEST_POSTGRES_OWNER_URL          owner/superuser URL, e.g. postgresql://cctv_owner:pw@127.0.0.1:5432/postgres
  TEST_POSTGRES_APP_USER           tenant role created by docker/postgres/init/02-create-roles.sh
  TEST_POSTGRES_APP_PASSWORD
  TEST_POSTGRES_PLATFORM_USER      platform (Super Admin) role created by the same script
  TEST_POSTGRES_PLATFORM_PASSWORD

A throwaway database named `cctv_core_api_test` is created, migrated, and dropped.
"""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import Settings
from src.entrypoints.seed_super_admin import seed_super_admin
from src.infrastructure.security.jwt import create_access_token, create_platform_token
from src.infrastructure.security.password import Argon2PasswordHasher
from src.main import create_app
from tests.fakes import FakeEmailSender
from tests.seed import Seed, insert_seed

CORE_API_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE = "cctv_core_api_test"
JWT_SECRET = "test-only-signing-key-0123456789abcdef"
JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class DatabaseUrls:
    owner_server: str  # psycopg URL to the maintenance database
    owner_test: str  # psycopg URL to the test database, as owner
    migration: str  # SQLAlchemy URL for Alembic, as owner
    app: str  # SQLAlchemy async URL, as the restricted app role
    app_user: str
    platform: str  # SQLAlchemy async URL, as the platform (Super Admin) role
    platform_user: str


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not set; skipping tests that need PostgreSQL.")
    return value


@pytest.fixture(scope="session")
def database_urls() -> DatabaseUrls:
    owner = make_url(_required_env("TEST_POSTGRES_OWNER_URL"))
    app_user = _required_env("TEST_POSTGRES_APP_USER")
    app_password = _required_env("TEST_POSTGRES_APP_PASSWORD")
    platform_user = _required_env("TEST_POSTGRES_PLATFORM_USER")
    platform_password = _required_env("TEST_POSTGRES_PLATFORM_PASSWORD")
    test_owner = owner.set(database=TEST_DATABASE)

    def render(url: Any) -> str:
        return url.render_as_string(hide_password=False)

    return DatabaseUrls(
        owner_server=render(owner.set(drivername="postgresql")),
        owner_test=render(test_owner.set(drivername="postgresql")),
        migration=render(test_owner.set(drivername="postgresql+psycopg")),
        app=render(test_owner.set(drivername="postgresql+asyncpg", username=app_user, password=app_password)),
        app_user=app_user,
        platform=render(
            test_owner.set(drivername="postgresql+asyncpg", username=platform_user, password=platform_password)
        ),
        platform_user=platform_user,
    )


@pytest.fixture(scope="session")
def migrated_database(database_urls: DatabaseUrls) -> Iterator[DatabaseUrls]:
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DATABASE}"')

    config = Config(str(CORE_API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_urls.migration.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APP_DB_USER", database_urls.app_user)
        mp.setenv("PLATFORM_DB_USER", database_urls.platform_user)
        command.upgrade(config, "head")

    yield database_urls

    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')


@pytest.fixture
def seed(migrated_database: DatabaseUrls) -> Seed:
    """Fresh data for every test (inserted as the owner, so RLS does not apply)."""
    data = Seed()
    with psycopg.connect(migrated_database.owner_test) as conn:
        conn.execute("TRUNCATE organizations, campuses, users, campus_registration_requests, cameras CASCADE")
        insert_seed(conn, data)
    return data


@pytest.fixture
def owner_query(migrated_database: DatabaseUrls):
    """Read the database as the owner, bypassing RLS, to check what really happened."""

    def run(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        with psycopg.connect(migrated_database.owner_test) as conn:
            return conn.execute(sql, params or {}).fetchall()

    return run


@pytest.fixture
async def client(migrated_database: DatabaseUrls) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        database_url=migrated_database.app,
        platform_database_url=migrated_database.platform,
        migration_database_url=migrated_database.migration,
        jwt_secret_key=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        _env_file=None,
    )
    engine = create_async_engine(migrated_database.app, poolclass=NullPool)
    platform_engine = create_async_engine(migrated_database.platform, poolclass=NullPool)
    app = create_app(settings, engine=engine, platform_engine=platform_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()
    await platform_engine.dispose()


@pytest.fixture
async def app_role_engine(migrated_database: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """A direct connection as the restricted app role, to test RLS without the API."""
    engine = create_async_engine(migrated_database.app, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def run_as(engine: AsyncEngine, user_id: UUID | None, sql: str, params: dict[str, Any] | None = None):
    """Run one statement as the app role, with `user_id` bound as the RLS actor.

    Returns (rows, rowcount). Rows is empty for statements that return nothing.
    """
    async with engine.begin() as conn:
        if user_id is not None:
            await conn.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)})
        result = await conn.execute(text(sql), params or {})
        rows = result.fetchall() if result.returns_rows else []
        return rows, result.rowcount


def auth_header(user_id: UUID, *, secret: str = JWT_SECRET, expires_minutes: int = 5) -> dict[str, str]:
    token = create_access_token(user_id, secret=secret, algorithm=JWT_ALGORITHM, expires_minutes=expires_minutes)
    return {"Authorization": f"Bearer {token}"}


# --- Platform Super Admin fixtures ---

SUPER_ADMIN_USERNAME = "platform.root"
SUPER_ADMIN_PASSWORD = "test-only-super-admin-password-42"


@pytest.fixture
def super_admin(migrated_database: DatabaseUrls) -> UUID:
    """A fresh Super Admin for every test, created by the real seed function."""
    with psycopg.connect(migrated_database.owner_test) as conn:
        conn.execute("TRUNCATE platform_admins")
        seed_super_admin(
            conn, username=SUPER_ADMIN_USERNAME, password=SUPER_ADMIN_PASSWORD, hasher=Argon2PasswordHasher()
        )
        row = conn.execute("SELECT id FROM platform_admins").fetchone()
    assert row is not None
    return row[0]


@pytest.fixture
async def platform_role_engine(migrated_database: DatabaseUrls) -> AsyncIterator[AsyncEngine]:
    """A direct connection as the platform role, to test its grants without the API."""
    engine = create_async_engine(migrated_database.platform, poolclass=NullPool)
    yield engine
    await engine.dispose()


def platform_auth_header(
    admin_id: UUID, *, expires_minutes: int = 5, version: int = 1, login_method: str = "NORMAL"
) -> dict[str, str]:
    token = create_platform_token(
        admin_id,
        version,
        secret=JWT_SECRET,
        algorithm=JWT_ALGORITHM,
        expires_minutes=expires_minutes,
        login_method=login_method,
    )
    return {"Authorization": f"Bearer {token}"}


# --- Emergency recovery fixtures (the development defaults the project uses) ---

RECOVERY_USERNAME = "admin"
RECOVERY_PASSWORD = "admin@123"


@asynccontextmanager
async def _client_for(
    urls: DatabaseUrls,
    *,
    email_sender: Any = None,
    media_storage: Any = None,
    assistant_engine: Any = None,
    **extra_settings: Any,
) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        database_url=urls.app,
        platform_database_url=urls.platform,
        migration_database_url=urls.migration,
        jwt_secret_key=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        _env_file=None,
        **extra_settings,
    )
    engine = create_async_engine(urls.app, poolclass=NullPool)
    platform_engine = create_async_engine(urls.platform, poolclass=NullPool)
    app = create_app(
        settings,
        engine=engine,
        platform_engine=platform_engine,
        email_sender=email_sender,
        media_storage=media_storage,
        assistant_engine=assistant_engine,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()
    await platform_engine.dispose()


@pytest.fixture
async def recovery_client(migrated_database: DatabaseUrls) -> AsyncIterator[AsyncClient]:
    """A client whose server has SUPER_ADMIN_RECOVERY_USERNAME/PASSWORD = admin / admin@123."""
    async with _client_for(
        migrated_database,
        super_admin_recovery_username=RECOVERY_USERNAME,
        super_admin_recovery_password=RECOVERY_PASSWORD,
    ) as http:
        yield http


@pytest.fixture
def dev_super_admin(migrated_database: DatabaseUrls) -> UUID:
    """The primary Super Admin created by the seed command from SUPER_ADMIN_USERNAME=admin and
    SUPER_ADMIN_PASSWORD=admin@123 (the project's initial development values)."""
    with psycopg.connect(migrated_database.owner_test) as conn:
        conn.execute("TRUNCATE platform_admins")
        seed_super_admin(conn, username="admin", password="admin@123", hasher=Argon2PasswordHasher())
        row = conn.execute("SELECT id FROM platform_admins").fetchone()
    assert row is not None
    return row[0]


# --- First Organization Admin registration fixtures ---


@pytest.fixture
def outbox() -> FakeEmailSender:
    """Captures the emails the API sends (nothing is really sent)."""
    return FakeEmailSender()


DETECTION_TOKEN = "test-only-detection-intake-token"  # the AI workers' shared secret in tests


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """CCTV_STORAGE_ROOT for this test's clients (campus files)."""
    return tmp_path / "cctv_storage"


@pytest.fixture
async def registration_client(seed: Seed, outbox: FakeEmailSender, migrated_database: DatabaseUrls, storage_root: Path):
    """A client whose emails go to `outbox`. Depends on `seed`, which also empties the
    registration, code and credential tables (they cascade from organizations and users).
    The shared code table of migration 0007 and the deleted-organization names of 0011
    reference no other table, so they are emptied here."""
    with psycopg.connect(migrated_database.owner_test) as conn:
        conn.execute("TRUNCATE auth_otp_challenges, deleted_organization_names")
    async with _client_for(
        migrated_database,
        email_sender=outbox,
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
    ) as http:
        yield http


@pytest.fixture
def owner_execute(migrated_database: DatabaseUrls):
    """Run a statement as the owner (bypassing RLS), for setting up test conditions."""

    def run(sql: str, params: dict[str, Any] | None = None) -> None:
        with psycopg.connect(migrated_database.owner_test) as conn:
            conn.execute(sql, params or {})

    return run
