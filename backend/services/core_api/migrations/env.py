import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from src.infrastructure.database.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL (owner role, postgresql+psycopg://...) to run migrations.")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
