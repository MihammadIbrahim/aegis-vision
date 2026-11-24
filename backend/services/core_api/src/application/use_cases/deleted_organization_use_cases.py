"""Super Admin: the organizations that have used the service and were permanently deleted."""

from src.domain.repositories.i_deleted_organization_repo import (
    IDeletedOrganizationHistory,
)


class ListDeletedOrganizationNames:
    def __init__(self, history: IDeletedOrganizationHistory) -> None:
        self._history = history

    async def execute(self) -> list[str]:
        return await self._history.list_names()
