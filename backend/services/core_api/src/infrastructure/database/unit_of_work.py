from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import bind_actor


class SqlTransaction:
    """The request's database transaction (see ``ITransaction``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


class SqlActorBinding:
    """See ``IActorBinding``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rebind(self, user_id: UUID) -> None:
        await bind_actor(self._session, user_id)
