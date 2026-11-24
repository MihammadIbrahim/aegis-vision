"""User Records & Rejoining (Org Admin page 13), ID regeneration (pages 11 and 24) and permanent deletion
(pages 11–13); decision D-024, migration 0029.

* Records: Admins see the Supervisors and Guards of their campus, with status history and activity, and
  download them as CSV (audited).
* Rejoining (Admins only, D-017): find the former account by its ID, update permitted details, optionally set
  a new password and (Guards) a location, confirm with the Admin's password. The same account comes back,
  so its history stays linked; no duplicate is created.
* ID regeneration with the actor's password, no code: an Admin for their own Admin ID; an Admin or
  Supervisor for a Guard's ID (the Guard and, when a Supervisor acted, the Admins are notified).
  Supervisor IDs are permanent (migration 0008).
* Permanent deletion (Admins only, former staff only): password plus the typed word DELETE; erases the
  personal data and keeps the anonymous account so alerts, cases and audit records stay linked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.domain.entities.location import LocationSelection
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import check_full_name, check_password, check_phone, check_username
from src.domain.value_objects.enums import UserRole
from src.domain.value_objects.system_id import generate_guard_id, generate_staff_id

DELETE_CONFIRMATION = "DELETE"
_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class UserRecordFilter:
    role: str | None = None
    status: str | None = None
    text: str | None = None
    include_deleted: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: UUID
    display_id: str
    role: str
    status: str
    full_name: str
    username: str | None
    email: str | None
    phone: str | None
    created_at: datetime
    deleted_at: datetime | None
    location: str | None
    status_changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StatusChange:
    from_status: str | None
    to_status: str
    event: str
    changed_at: datetime
    changed_by_name: str | None


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    occurred_at: datetime
    action: str
    category: str
    outcome: str
    target_type: str
    by_user: bool


@dataclass(frozen=True, slots=True)
class RecordDetail:
    record: UserRecord
    history: list[StatusChange]
    activity: list[ActivityEntry]


@dataclass(frozen=True, slots=True)
class RejoinForm:
    full_name: str
    username: str | None
    phone: str | None
    new_password: str | None
    location: LocationSelection | None
    current_password: str


class IPasswordConfirmation(Protocol):
    async def confirm(self, current_password: str) -> None: ...


class IGuardPlacement(Protocol):
    async def place(self, actor: User, display_id: str, selection: LocationSelection) -> None: ...


def _admin(actor: User) -> None:
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied("Only an Organization Admin can do this.", code="ADMIN_ONLY")


def _missing() -> NotFound:
    return NotFound("User record not found.", code="STAFF_NOT_FOUND")


class UserRecords:
    def __init__(
        self,
        repository: Any,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        confirmation: IPasswordConfirmation,
        hash_password: Callable[[str], str],
        placement: IGuardPlacement | None = None,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._transaction = transaction
        self._confirmation = confirmation
        self._hash = hash_password
        self._placement = placement

    async def _record(self, user_id: UUID) -> UserRecord:
        found = await self._repo.record(user_id)
        if found is None:
            raise _missing()
        return found

    # --- Records ---------------------------------------------------------------------------------------

    async def search(self, actor: User, where: UserRecordFilter) -> tuple[list[UserRecord], int]:
        _admin(actor)
        result: tuple[list[UserRecord], int] = await self._repo.records(where)
        return result

    async def detail(self, actor: User, user_id: UUID) -> RecordDetail:
        _admin(actor)
        record = await self._record(user_id)
        return RecordDetail(record, await self._repo.history(user_id), await self._repo.activity(user_id))

    async def export(self, actor: User, where: UserRecordFilter) -> list[UserRecord]:
        _admin(actor)
        rows, _ = await self._repo.records(where)
        await self._audit.record(
            actor, "USER_RECORDS_EXPORTED", "USER", None,
            {"rows": len(rows), "role": where.role, "status": where.status},
        )  # fmt: skip
        await self._transaction.commit()
        return list(rows)

    async def lookup(self, actor: User, display_id: str) -> UserRecord:
        """Rejoining starts from the existing ID (no duplicate profile)."""
        _admin(actor)
        found = await self._repo.by_display_id(display_id.strip())
        if found is None:
            raise NotFound("No Supervisor or Guard of this campus has this ID.", code="STAFF_NOT_FOUND")
        return found

    # --- Rejoining ---------------------------------------------------------------------------------------

    async def rejoin(self, actor: User, user_id: UUID, form: RejoinForm) -> UserRecord:
        _admin(actor)
        record = await self._record(user_id)
        if record.deleted_at is not None:
            raise Conflict("This person's data was permanently deleted; add them as new staff.", code="DELETED")
        if record.status == "ACTIVE":
            raise Conflict("This account is already active.", code="STAFF_ALREADY_ACTIVE")
        full_name = check_full_name(form.full_name)
        username = check_username(form.username) if form.username else record.username
        phone = check_phone(form.phone) if form.phone else None
        if form.new_password is not None:
            check_password(form.new_password, username=username or "\u0000", email=record.email or "")
        if form.location is not None and record.role != "GUARD":
            raise ValidationFailed("Only Guards have a location.", code="INVALID_LOCATION")
        await self._confirmation.confirm(form.current_password)
        if not await self._repo.rejoin(user_id, full_name, username, phone):
            raise Conflict("This account cannot rejoin now.", code="STAFF_ALREADY_ACTIVE")
        if form.new_password is not None:
            if await self._repo.set_password(user_id, self._hash(form.new_password)) != "UPDATED":
                raise _missing()
        if form.location is not None:
            assert self._placement is not None
            await self._placement.place(actor, record.display_id, form.location)
        await self._audit.record(
            actor, "STAFF_REJOINED", "USER", user_id,
            {"role": record.role, "password_set": form.new_password is not None,
             "location_set": form.location is not None},
        )  # fmt: skip
        updated = await self._record(user_id)
        await self._transaction.commit()
        return updated

    # --- ID regeneration ----------------------------------------------------------------------------------

    async def _regenerate(self, user_id: UUID, factory: Callable[[], str]) -> str:
        for _ in range(_ATTEMPTS):
            new_id = factory()
            outcome = await self._repo.regenerate_display_id(user_id, new_id)
            if outcome == "REGENERATED":
                return new_id
            if outcome != "DISPLAY_ID_TAKEN":
                raise PermissionDenied("This ID cannot be regenerated by you.", code="NOT_ALLOWED")
        raise Conflict("A new ID could not be issued; try again.", code="DISPLAY_ID_TAKEN")

    async def regenerate_own_admin_id(self, actor: User, current_password: str) -> str:
        _admin(actor)
        await self._confirmation.confirm(current_password)
        new_id = await self._regenerate(actor.id, generate_staff_id)
        await self._audit.record(actor, "PROFILE_ID_REGENERATED", "USER", actor.id,
                                 {"old_id": actor.display_id, "new_id": new_id})  # fmt: skip
        await self._transaction.commit()
        return new_id

    async def regenerate_guard_id(self, actor: User, user_id: UUID, current_password: str) -> str:
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            raise PermissionDenied("Only Admins and Supervisors can regenerate Guard IDs.", code="STAFF_ONLY")
        record = await self._record(user_id)
        if record.role != "GUARD":
            raise PermissionDenied("Supervisor IDs are permanent.", code="SUPERVISOR_ID_PERMANENT")
        if record.status != "ACTIVE":
            raise Conflict("Only an active Guard's ID can be regenerated.", code="STAFF_INACTIVE")
        await self._confirmation.confirm(current_password)
        new_id = await self._regenerate(user_id, generate_guard_id)
        await self._audit.record(actor, "GUARD_ID_REGENERATED", "USER", user_id,
                                 {"old_id": record.display_id, "new_id": new_id})  # fmt: skip
        await self._repo.notify([user_id], "GUARD_ID_REGENERATED", user_id, f"Your Guard ID is now {new_id}")
        if actor.role is UserRole.SUPERVISOR:
            admins = await self._repo.active_admins()
            title = f"Supervisor {actor.full_name} regenerated the ID of Guard {record.full_name}: {new_id}"
            await self._repo.notify(admins, "GUARD_ID_REGENERATED", user_id, title)
        await self._transaction.commit()
        return new_id

    # --- Permanent deletion -----------------------------------------------------------------------------

    async def permanently_delete(self, actor: User, user_id: UUID, current_password: str, confirmation: str) -> None:
        _admin(actor)
        if confirmation.strip() != DELETE_CONFIRMATION:
            raise ValidationFailed("Type DELETE to confirm the permanent deletion.", code="CONFIRMATION_REQUIRED")
        record = await self._record(user_id)
        if record.deleted_at is not None:
            raise Conflict("This person's data was already deleted.", code="ALREADY_DELETED")
        if record.status != "LEFT":
            raise Conflict("Deactivate the account before deleting its data.", code="STILL_ACTIVE")
        await self._confirmation.confirm(current_password)
        outcome = await self._repo.permanently_delete(user_id)
        if outcome != "DELETED":
            raise Conflict("The data could not be deleted.", code=outcome)
        # The record keeps only the IDs and the role; no personal data goes into the audit entry.
        await self._audit.record(actor, "USER_PERMANENTLY_DELETED", "USER", user_id,
                                 {"role": record.role, "display_id": record.display_id})  # fmt: skip
        await self._transaction.commit()
