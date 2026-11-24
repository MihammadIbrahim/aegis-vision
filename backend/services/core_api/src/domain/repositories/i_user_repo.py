from typing import Protocol
from uuid import UUID

from src.domain.entities.user import AdminDirectoryEntry, User


class IUserRepository(Protocol):
    async def get_actor(self, user_id: UUID) -> User | None: ...

    async def list_admin_directory(self, organization_id: UUID) -> list[AdminDirectoryEntry]: ...

    async def add(self, user: User) -> None: ...
