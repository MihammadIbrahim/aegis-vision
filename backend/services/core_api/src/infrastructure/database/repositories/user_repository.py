from uuid import UUID

from sqlalchemy import Uuid, bindparam, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.user import AdminDirectoryEntry, User
from src.domain.exceptions.domain_errors import Conflict
from src.domain.value_objects.enums import AccountStatus, UserRole
from src.infrastructure.database.models import UserModel

# app_admin_directory() is a SECURITY DEFINER function: it is the only way to read
# Admins outside the caller's campus, and it returns names and the flag only.
_ADMIN_DIRECTORY_SQL = text("SELECT full_name, is_first_admin FROM app_admin_directory(:organization_id)").bindparams(
    bindparam("organization_id", type_=Uuid)
)


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_actor(self, user_id: UUID) -> User | None:
        row = (await self._session.execute(select(UserModel).where(UserModel.id == user_id))).scalar_one_or_none()
        return None if row is None else _to_entity(row)

    async def list_admin_directory(self, organization_id: UUID) -> list[AdminDirectoryEntry]:
        result = await self._session.execute(_ADMIN_DIRECTORY_SQL, {"organization_id": organization_id})
        return [AdminDirectoryEntry(full_name=row.full_name, is_first_admin=row.is_first_admin) for row in result]

    async def add(self, user: User) -> None:
        # No RETURNING: the new user may be in a campus the caller cannot read.
        statement = insert(UserModel).values(
            id=user.id,
            organization_id=user.organization_id,
            campus_id=user.campus_id,
            role=user.role.value,
            status=user.status.value,
            full_name=user.full_name,
            email=user.email,
            display_id=user.display_id,
            is_first_admin=user.is_first_admin,
        )
        try:
            await self._session.execute(statement)
        except IntegrityError as exc:
            raise Conflict("A user with this email or ID already exists.") from exc


def _to_entity(row: UserModel) -> User:
    return User(
        id=row.id,
        organization_id=row.organization_id,
        campus_id=row.campus_id,
        role=UserRole(row.role),
        status=AccountStatus(row.status),
        full_name=row.full_name,
        email=row.email,
        display_id=row.display_id,
        is_first_admin=row.is_first_admin,
    )
