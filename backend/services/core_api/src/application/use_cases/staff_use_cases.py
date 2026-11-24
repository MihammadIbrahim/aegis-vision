"""Staff Management for the signed-in Admin or Supervisor's own campus (web Phase 8; decision D-017).

- **Admins** list and view Guards and Supervisors, update Guards, set a Guard's password, and
  deactivate (status LEFT) or reactivate Guards and Supervisors. Only Admins create staff accounts
  (the creation flows in ``supervisor_use_cases``).
- **Supervisors** list and view Guards only, update Guard details, set a Guard's password and change
  Guard locations. They cannot create, deactivate or reactivate anyone, nor see Supervisors.

Changes need the acting user's current password (wrong passwords count toward the sign-in lock).
Every change is written to the organization audit log with field names only, never values. When a
Supervisor changes a Guard, the campus's active Admins get an in-app notification.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.otp_flow import TIMING_DUMMY_HASH
from src.domain.entities.staff import StaffMember
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    Conflict,
    CurrentPasswordIncorrect,
    NotFound,
    PermissionDenied,
)
from src.domain.repositories.i_staff_repo import IStaffRepository, StaffFilter
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import (
    ensure_staff_admin,
    ensure_staff_manager,
)
from src.domain.services.registration_policy import (
    check_full_name,
    check_password,
    check_phone,
    check_username,
)
from src.domain.value_objects.enums import AccountStatus, UserRole

# Audit actions (organization_audit_logs.action) and notification kinds (migration 0018).
GUARD_DETAILS_UPDATED = "GUARD_DETAILS_UPDATED"
GUARD_PASSWORD_SET = "GUARD_PASSWORD_SET"
GUARD_LOCATION_ASSIGNED = "GUARD_LOCATION_ASSIGNED"
GUARD_LOCATION_CLEARED = "GUARD_LOCATION_CLEARED"
STAFF_DEACTIVATED = "STAFF_DEACTIVATED"
STAFF_REACTIVATED = "STAFF_REACTIVATED"
NOTIFY_GUARD_UPDATED = "GUARD_UPDATED"
NOTIFY_GUARD_PASSWORD_SET = "GUARD_PASSWORD_SET"
NOTIFY_GUARD_LOCATION_CHANGED = "GUARD_LOCATION_CHANGED"


def _missing() -> NotFound:
    return NotFound("Staff member not found.", code="STAFF_NOT_FOUND")


def _who(user: User | StaffMember) -> str:
    return f"{user.full_name} ({user.display_id})"


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    max_failed_attempts: int
    lockout_seconds: int


class _StaffBase:
    def __init__(
        self,
        repository: IStaffRepository,
        transaction: ITransaction,
        hasher: IPasswordHasher | None = None,
        policy: PasswordPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._transaction = transaction
        self._hasher = hasher
        self._policy = policy
        self._clock = clock

    async def _visible(self, actor: User, user_id: UUID) -> StaffMember:
        """A Guard (Admins and Supervisors) or a Supervisor (Admins only) of the actor's campus."""
        member = await self._repo.get_staff(user_id)
        if member is None:
            raise _missing()
        if member.role is UserRole.SUPERVISOR and actor.role is not UserRole.ADMIN:
            raise PermissionDenied("Only an Organization Admin can view Supervisors.", code="ADMIN_ONLY")
        return member

    async def _active_guard(self, actor: User, user_id: UUID) -> StaffMember:
        member = await self._visible(actor, user_id)
        if member.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards can be changed here.", code="GUARD_ONLY")
        if member.status is not AccountStatus.ACTIVE:
            raise PermissionDenied("Reactivate this Guard before changing it.", code="STAFF_INACTIVE")
        return member

    async def _confirm_password(self, current_password: str) -> None:
        assert self._hasher is not None and self._policy is not None
        account = await self._repo.actor_account()
        if account is None:
            self._hasher.verify(TIMING_DUMMY_HASH, current_password)
            raise CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, current_password):
            locked_until = await self._repo.record_password_failure(
                account.user_id,
                max_attempts=self._policy.max_failed_attempts,
                window_seconds=self._policy.lockout_seconds,
            )
            await self._transaction.commit()  # wrong passwords count toward the sign-in lock
            if locked_until is not None:
                raise _locked(self._policy.lockout_seconds)
            raise CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")

    async def _record(
        self, actor: User, guard: StaffMember, action: str, metadata: dict[str, Any], kind: str | None, verb: str
    ) -> None:
        await self._repo.audit(actor, action, guard.user_id, {"role": guard.role.value, **metadata})
        if kind is not None and actor.role is UserRole.SUPERVISOR:
            await self._repo.notify_admins(kind, guard.user_id, f"Supervisor {_who(actor)} {verb} Guard {_who(guard)}")


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many incorrect passwords. Please try again later.", code="ACCOUNT_LOCKED", retry_after=max(1, retry_after)
    )


class ListStaff(_StaffBase):
    async def execute(self, actor: User, where: StaffFilter) -> list[StaffMember]:
        ensure_staff_manager(actor)
        if where.role is UserRole.SUPERVISOR:
            ensure_staff_admin(actor)
        elif where.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards and Supervisors are listed here.", code="INVALID_ROLE")
        if where.role is UserRole.SUPERVISOR and where.category is not None:
            where = StaffFilter(where.role, where.status, None, where.q, where.limit, where.offset)
        return await self._repo.list_staff(where)


class GetStaffMember(_StaffBase):
    async def execute(self, actor: User, user_id: UUID) -> StaffMember:
        ensure_staff_manager(actor)
        return await self._visible(actor, user_id)


class UpdateGuardDetails(_StaffBase):
    """Full name, username and phone number. Email changes stay with the Guard's own verified flow."""

    async def execute(
        self, actor: User, user_id: UUID, *, full_name: str, username: str, phone: str, current_password: str
    ) -> StaffMember:
        ensure_staff_manager(actor)
        name, user, number = check_full_name(full_name), check_username(username), check_phone(phone)
        guard = await self._active_guard(actor, user_id)
        await self._confirm_password(current_password)
        changed = [
            field
            for field, before, after in (
                ("full_name", guard.full_name, name),
                ("username", guard.username, user),
                ("phone", guard.phone, number),
            )
            if before != after
        ]
        if changed:
            if not await self._repo.update_guard_details(user_id, name, user, number):
                raise _missing()
            await self._record(
                actor, guard, GUARD_DETAILS_UPDATED, {"fields": changed}, NOTIFY_GUARD_UPDATED, "updated"
            )
        updated = await self._repo.get_staff(user_id)  # read back before commit: the RLS actor is per transaction
        if updated is None:
            raise _missing()
        await self._transaction.commit()
        return updated


class SetGuardPassword(_StaffBase):
    """The Guard's earlier sessions stop working; the Guard is told the new password in person."""

    async def execute(self, actor: User, user_id: UUID, *, new_password: str, current_password: str) -> None:
        ensure_staff_manager(actor)
        guard = await self._active_guard(actor, user_id)
        check_password(new_password, username=guard.username or guard.display_id, email=guard.email)
        await self._confirm_password(current_password)
        assert self._hasher is not None
        if not await self._repo.set_guard_password(user_id, self._hasher.hash(new_password)):
            raise _missing()
        await self._record(
            actor,
            guard,
            GUARD_PASSWORD_SET,
            {"fields": ["password"]},
            NOTIFY_GUARD_PASSWORD_SET,
            "set a new password for",
        )
        await self._transaction.commit()


class ChangeStaffStatus(_StaffBase):
    """Admins deactivate (status LEFT: no sign-in, earlier sessions refused at once) or reactivate a
    Guard or Supervisor. Records are never deleted, so history stays linked. A deactivated Guard loses
    its location assignment (it becomes Standby when reactivated)."""

    async def execute(self, actor: User, user_id: UUID, *, active: bool, current_password: str) -> StaffMember:
        ensure_staff_admin(actor)
        member = await self._visible(actor, user_id)
        target = AccountStatus.ACTIVE if active else AccountStatus.LEFT
        if member.status is target:
            raise Conflict(
                "This account is already active." if active else "This account is already deactivated.",
                code="STAFF_ALREADY_ACTIVE" if active else "STAFF_ALREADY_INACTIVE",
            )
        await self._confirm_password(current_password)
        if not await self._repo.set_status(user_id, target):
            raise _missing()
        metadata: dict[str, Any] = {"status": target.value}
        if not active and member.role is UserRole.GUARD:
            metadata["location_cleared"] = await self._repo.clear_guard_location(user_id)
        await self._record(actor, member, STAFF_REACTIVATED if active else STAFF_DEACTIVATED, metadata, None, "")
        changed = await self._repo.get_staff(user_id)  # read back before commit: the RLS actor is per transaction
        if changed is None:
            raise _missing()
        await self._transaction.commit()
        return changed


class GuardLocationActivity:
    """Audit entry (and Admin notification, for Supervisors) when a Guard's location is set or removed
    through ``/locations/guard-assignments``. Runs inside that request's transaction."""

    def __init__(self, repository: IStaffRepository) -> None:
        self._repo = repository

    async def record(self, actor: User, guard_user_id: UUID, level: str | None) -> None:
        guard = await self._repo.get_staff(guard_user_id)
        if guard is None:
            return
        action = GUARD_LOCATION_ASSIGNED if level else GUARD_LOCATION_CLEARED
        await self._repo.audit(actor, action, guard.user_id, {"role": "GUARD", "level": level})
        if actor.role is UserRole.SUPERVISOR:
            verb = "assigned a location to" if level else "removed the location of"
            await self._repo.notify_admins(
                NOTIFY_GUARD_LOCATION_CHANGED, guard.user_id, f"Supervisor {_who(actor)} {verb} Guard {_who(guard)}"
            )
