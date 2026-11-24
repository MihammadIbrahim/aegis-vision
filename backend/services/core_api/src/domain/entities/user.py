from dataclasses import dataclass
from uuid import UUID

from src.domain.value_objects.enums import AccountStatus, UserRole


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    role: UserRole
    status: AccountStatus
    full_name: str
    email: str
    display_id: str
    is_first_admin: bool = False

    @property
    def is_active(self) -> bool:
        return self.status is AccountStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class AdminDirectoryEntry:
    """The only user information visible across campuses of the same organization."""

    full_name: str
    is_first_admin: bool
