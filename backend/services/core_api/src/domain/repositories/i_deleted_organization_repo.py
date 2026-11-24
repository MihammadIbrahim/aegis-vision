"""Names of organizations that were permanently deleted (migration 0011). Nothing else about
them is kept, so these names cannot restore an organization or reach its former data."""

from typing import Protocol


class IDeletedOrganizationHistory(Protocol):
    async def list_names(self) -> list[str]: ...
