"""Read on the platform session: only the platform role may read this table."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_NAMES = text("SELECT organization_name FROM deleted_organization_names ORDER BY lower(organization_name), id")


class SqlDeletedOrganizationHistory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_names(self) -> list[str]:
        return list((await self._session.execute(_NAMES)).scalars().all())
