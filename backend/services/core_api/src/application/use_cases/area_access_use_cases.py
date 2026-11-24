"""Restricted-Area Access Management (decision D-016).

* **Organization Admins** mark areas of their campus as restricted, grant people access to one or
  more restricted areas (validity, optional weekly schedule), change and revoke grants, and see the
  permission history.
* **Supervisors** see the restricted areas, who is currently authorized for an area (basic details)
  and can check a person's access; they cannot change anything, nor see revoked history or notes.
* **Guards** have no access.

An access check needs a known person. It never treats "not found in the personnel list" as an
unauthorized entry by someone (see ``access_policy``).
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.domain.entities.area_access import (
    AccessGrant,
    GrantStatus,
    GrantTerms,
    RestrictedArea,
)
from src.domain.entities.personnel import AuditEntry, PersonnelStatus
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_personnel_repo import (
    IAreaAccessRepository,
    IPersonnelRepository,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.access_policy import AccessResult, evaluate_access
from src.domain.services.permission_policy import (
    ensure_personnel_manager,
    ensure_personnel_viewer,
)
from src.domain.value_objects.enums import UserRole

MAX_AREAS_PER_GRANT = 100


def _area_missing() -> NotFound:
    return NotFound("Area not found.", code="AREA_NOT_FOUND")


def _person_missing() -> NotFound:
    return NotFound("Personnel not found.", code="PERSONNEL_NOT_FOUND")


def _grant_missing() -> NotFound:
    return NotFound("Access permission not found.", code="ACCESS_GRANT_NOT_FOUND")


def _note(value: str | None, longest: int = 500) -> str | None:
    text = (value or "").strip() or None
    if text is not None and len(text) > longest:
        raise ValidationFailed(f"The note must be at most {longest} characters.", code="INVALID_NOTE")
    return text


def _validity(terms: GrantTerms, now: datetime) -> datetime:
    for value in (terms.valid_from, terms.valid_until):
        if value is not None and value.tzinfo is None:
            raise ValidationFailed("Times must include a time zone.", code="INVALID_VALIDITY")
    valid_from = terms.valid_from or now
    if terms.valid_until is not None and terms.valid_until <= valid_from:
        raise ValidationFailed("'Valid until' must be after 'valid from'.", code="INVALID_VALIDITY")
    return valid_from


class ViewRestrictedAccess:
    def __init__(
        self,
        access: IAreaAccessRepository,
        personnel: IPersonnelRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._access = access
        self._personnel = personnel
        self._clock = clock

    async def areas(self, actor: User, *, include_unrestricted: bool) -> list[RestrictedArea]:
        """Restricted areas; with ``include_unrestricted`` (Admins), every area of the campus with
        its restricted flag, for choosing which to restrict."""
        ensure_personnel_viewer(actor)
        if include_unrestricted:
            ensure_personnel_manager(actor)
        found = await self._access.areas(restricted_only=not include_unrestricted)
        if actor.role is UserRole.ADMIN:
            return found
        # The area's access note is written by an Admin: an Admin note, never shown to Supervisors
        # (D-016 item 16). The response for Supervisors also has no such field.
        return [replace(a, access_note=None) for a in found]

    async def _area(self, area_id: UUID) -> RestrictedArea:
        area = await self._access.area(area_id)
        if area is None:
            raise _area_missing()
        return area

    async def area_grants(self, actor: User, area_id: UUID, *, include_revoked: bool) -> list[AccessGrant]:
        ensure_personnel_viewer(actor)
        await self._area(area_id)
        return await self._access.grants(area_id=area_id, include_revoked=self._revoked(actor, include_revoked))

    async def personnel_grants(self, actor: User, personnel_id: UUID, *, include_revoked: bool) -> list[AccessGrant]:
        ensure_personnel_viewer(actor)
        if await self._personnel.summary(personnel_id) is None:
            raise _person_missing()
        return await self._access.grants(
            personnel_id=personnel_id, include_revoked=self._revoked(actor, include_revoked)
        )

    @staticmethod
    def _revoked(actor: User, wanted: bool) -> bool:
        if wanted and actor.role is not UserRole.ADMIN:
            raise PermissionDenied(
                "Only an Organization Admin can view the permission history.", code="PERSONNEL_ADMIN_ONLY"
            )
        return wanted

    async def check(self, actor: User, personnel_id: UUID, area_id: UUID, at: datetime | None) -> AccessResult:
        """Is this (already identified) person allowed in this area at this time?"""
        ensure_personnel_viewer(actor)
        person = await self._personnel.summary(personnel_id)
        if person is None:
            raise _person_missing()
        area = await self._area(area_id)
        when = at or self._clock()
        if when.tzinfo is None:
            raise ValidationFailed("The time must include a time zone.", code="INVALID_TIME")
        grants = await self._access.grants(area_id=area_id, personnel_id=personnel_id)
        return evaluate_access(
            personnel_status=person.status.value, area_restricted=area.is_restricted, grants=grants, at=when
        )

    async def grant_history(self, actor: User, grant_id: UUID) -> tuple[AccessGrant, list[AuditEntry]]:
        ensure_personnel_manager(actor)
        found = await self._access.grants(grant_id=grant_id, include_revoked=True)
        if not found:
            raise _grant_missing()
        return found[0], await self._personnel.audit_trail("AREA_ACCESS", grant_id)


class ManageRestrictedAreas:
    def __init__(self, access: IAreaAccessRepository, audit: IOrganizationAuditLog, transaction: ITransaction) -> None:
        self._access = access
        self._audit = audit
        self._transaction = transaction

    async def mark(self, actor: User, area_id: UUID, access_note: str | None) -> RestrictedArea:
        ensure_personnel_manager(actor)
        if await self._access.area(area_id) is None:
            raise _area_missing()
        note = _note(access_note)
        created = await self._access.mark_restricted(area_id, note, actor.id)
        action = "RESTRICTED_AREA_MARKED" if created else "RESTRICTED_AREA_UPDATED"
        await self._audit.record(actor, action, "RESTRICTED_AREA", area_id, {"note_given": note is not None})
        area = await self._access.area(area_id)
        await self._transaction.commit()
        assert area is not None
        return area

    async def clear(self, actor: User, area_id: UUID) -> None:
        ensure_personnel_manager(actor)
        area = await self._access.area(area_id)
        if area is None:
            raise _area_missing()
        if not area.is_restricted:
            raise Conflict("This area is not restricted.", code="AREA_NOT_RESTRICTED")
        if area.active_grants:
            raise Conflict(
                f"{area.active_grants} person(s) still have access to this area. Revoke their access first.",
                code="AREA_HAS_ACTIVE_ACCESS",
            )
        await self._access.clear_restricted(area_id)
        await self._audit.record(actor, "RESTRICTED_AREA_CLEARED", "RESTRICTED_AREA", area_id, {})
        await self._transaction.commit()


class ManageAccessGrants:
    def __init__(
        self,
        access: IAreaAccessRepository,
        personnel: IPersonnelRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._access = access
        self._personnel = personnel
        self._audit = audit
        self._transaction = transaction
        self._clock = clock

    async def grant(
        self, actor: User, personnel_id: UUID, area_ids: Sequence[UUID], terms: GrantTerms
    ) -> list[AccessGrant]:
        """Grants access to every listed area at once (all or none)."""
        ensure_personnel_manager(actor)
        unique = list(dict.fromkeys(area_ids))
        if not 1 <= len(unique) <= MAX_AREAS_PER_GRANT:
            raise ValidationFailed(f"Choose 1 to {MAX_AREAS_PER_GRANT} areas.", code="INVALID_AREAS")
        person = await self._personnel.get(personnel_id)
        if person is None:
            raise _person_missing()
        if person.status is not PersonnelStatus.ACTIVE:
            raise Conflict("Access can only be granted to active personnel.", code="PERSONNEL_INACTIVE")
        valid_from = _validity(terms, self._clock())
        clean = GrantTerms(valid_from, terms.valid_until, terms.schedule, _note(terms.note))
        current = {g.area_id for g in await self._access.grants(personnel_id=personnel_id)}
        created: list[UUID] = []
        for area_id in unique:
            area = await self._access.area(area_id)
            if area is None:
                raise _area_missing()
            if not area.is_restricted:
                raise Conflict(f"'{area.area_name}' is not a restricted area.", code="AREA_NOT_RESTRICTED")
            if area_id in current:
                raise Conflict(
                    f"This person already has active access to '{area.area_name}'.", code="ACCESS_ALREADY_GRANTED"
                )
            grant_id = uuid4()
            await self._access.add_grant(grant_id, personnel_id, area_id, clean, valid_from, actor.id)
            await self._audit.record(
                actor,
                "AREA_ACCESS_GRANTED",
                "AREA_ACCESS",
                grant_id,
                {
                    "personnel_id": str(personnel_id),
                    "area_id": str(area_id),
                    "valid_from": valid_from.isoformat(),
                    "valid_until": None if clean.valid_until is None else clean.valid_until.isoformat(),
                    "has_schedule": clean.schedule is not None,
                },
            )
            created.append(grant_id)
        grants = [g for g in await self._access.grants(personnel_id=personnel_id) if g.id in created]
        await self._transaction.commit()
        return grants

    async def _active(self, grant_id: UUID) -> AccessGrant:
        found = await self._access.grants(grant_id=grant_id, include_revoked=True)
        if not found:
            raise _grant_missing()
        if found[0].status is not GrantStatus.ACTIVE:
            raise Conflict("This access permission has been revoked.", code="ACCESS_GRANT_REVOKED")
        return found[0]

    async def update(self, actor: User, grant_id: UUID, terms: GrantTerms) -> AccessGrant:
        ensure_personnel_manager(actor)
        before = await self._active(grant_id)
        valid_from = _validity(GrantTerms(terms.valid_from or before.valid_from, terms.valid_until), self._clock())
        clean = GrantTerms(valid_from, terms.valid_until, terms.schedule, _note(terms.note))
        changed = [
            name
            for name, old, new in (
                ("valid_from", before.valid_from, valid_from),
                ("valid_until", before.valid_until, clean.valid_until),
                ("schedule", before.schedule, clean.schedule),
                ("note", before.note, clean.note),
            )
            if old != new
        ]
        if not await self._access.update_grant(grant_id, clean, valid_from, actor.id):
            raise _grant_missing()
        await self._audit.record(actor, "AREA_ACCESS_UPDATED", "AREA_ACCESS", grant_id, {"changed": changed})
        updated = (await self._access.grants(grant_id=grant_id, include_revoked=True))[0]
        await self._transaction.commit()
        return updated

    async def revoke(self, actor: User, grant_id: UUID, reason: str | None) -> AccessGrant:
        ensure_personnel_manager(actor)
        await self._active(grant_id)
        note = _note(reason)
        if not await self._access.revoke_grant(grant_id, note, actor.id):
            raise _grant_missing()
        await self._audit.record(
            actor, "AREA_ACCESS_REVOKED", "AREA_ACCESS", grant_id, {"reason_given": note is not None}
        )
        revoked = (await self._access.grants(grant_id=grant_id, include_revoked=True))[0]
        await self._transaction.commit()
        return revoked
