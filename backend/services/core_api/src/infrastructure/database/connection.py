from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: database errors are logged without the values sent with the query
    # (emails, password hashes, code hashes), only with the SQL text.
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def bind_actor(session: AsyncSession, user_id: UUID) -> None:
    """Tell PostgreSQL who is making this request, for Row-Level Security.

    Only the user ID is passed. The database looks up that user's organization,
    campus and First Admin status itself, so application code cannot widen its
    own scope. The setting is transaction-local (`true`), so it never leaks to
    another request that reuses the pooled connection.
    """
    await session.execute(text("SELECT set_config('app.user_id', :user_id, true)"), {"user_id": str(user_id)})


async def actor_sessions_revoked_at(session: AsyncSession) -> datetime | None:
    """When the bound actor's sessions were last revoked without a password change (a Guard's
    assignment changed, migration 0019); None if never."""
    revoked: datetime | None = await session.scalar(text("SELECT app_actor_sessions_revoked_at()"))
    return revoked


async def actor_password_changed_at(session: AsyncSession) -> datetime | None:
    """When the bound actor's password last changed (None if the account has no password)."""
    changed: datetime | None = await session.scalar(text("SELECT app_actor_password_changed_at()"))
    return changed
