from src.domain.entities.user import AdminDirectoryEntry, User
from src.domain.repositories.i_user_repo import IUserRepository
from src.domain.services.permission_policy import ensure_admin


class GetAdminDirectory:
    """Names of all active Admins in the caller's organization, with the First Admin marked.

    This is the only cross-campus visibility exception. The organization always
    comes from the caller, never from the request.
    """

    def __init__(self, users: IUserRepository) -> None:
        self._users = users

    async def execute(self, actor: User) -> list[AdminDirectoryEntry]:
        ensure_admin(actor)
        return await self._users.list_admin_directory(actor.organization_id)
