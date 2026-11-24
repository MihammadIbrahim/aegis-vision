"""Storage for Staff Management: Supervisors and Guards of the signed-in user's campus. The database
enforces the campus again (Row-Level Security); passwords change only through a narrow function."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.domain.entities.staff import GuardCategory, StaffMember
from src.domain.entities.user import User
from src.domain.repositories.i_staff_account_repo import StepUpAccount
from src.domain.value_objects.enums import AccountStatus, UserRole


@dataclass(frozen=True, slots=True)
class StaffFilter:
    role: UserRole
    status: AccountStatus | None = None
    category: GuardCategory | None = None
    q: str | None = None
    limit: int = 50
    offset: int = 0


class IStaffRepository(Protocol):
    async def list_staff(self, where: StaffFilter) -> list[StaffMember]: ...
    async def get_staff(self, user_id: UUID) -> StaffMember | None: ...
    async def update_guard_details(self, user_id: UUID, full_name: str, username: str, phone: str) -> bool: ...
    async def set_guard_password(self, user_id: UUID, password_hash: str) -> bool: ...
    async def set_status(self, user_id: UUID, status: AccountStatus) -> bool: ...
    async def clear_guard_location(self, user_id: UUID) -> bool: ...
    async def actor_account(self) -> StepUpAccount | None: ...
    async def record_password_failure(self, user_id: UUID, *, max_attempts: int, window_seconds: int) -> Any: ...
    async def audit(self, actor: User, action: str, target_id: UUID, metadata: dict[str, Any]) -> None: ...
    async def notify_admins(self, kind: str, subject_user_id: UUID, title: str) -> None: ...
