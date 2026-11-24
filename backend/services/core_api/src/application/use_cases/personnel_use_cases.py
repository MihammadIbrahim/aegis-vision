"""Organization Personnel Management (decision D-016).

* **Organization Admins** register, edit, deactivate and reactivate personnel of their own campus,
  manage the photo, categories and custom fields, and view each person's history.
* **Supervisors** search the directory and view basic details only. They cannot see the photo:
  a photo is shown to Supervisors only inside an authorized identity or incident review, which
  does not exist yet.
* **Guards** and the Platform Super Admin have no access.

Each check is made here, again by the router dependencies, and again by the database (Row-Level
Security and the functions of migration 0017). Another campus's record answers 404, exactly like
a record that does not exist. Manual registration uses the same ``PersonnelRules`` as the import.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.application.services.personnel_import.mapping import LOCATION_TARGETS
from src.application.use_cases.location_use_cases import LocationResolver
from src.domain.entities.location import LocationSelection, LocationTarget
from src.domain.entities.personnel import (
    CANONICAL_FIELDS,
    REQUIRABLE_FIELDS,
    AuditEntry,
    CreatedVia,
    CustomField,
    FieldType,
    Personnel,
    PersonnelCategory,
    PersonnelFilter,
    PersonnelSummary,
    PersonnelValues,
    generate_personnel_code,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_location_repo import ILocationRepository
from src.domain.repositories.i_personnel_repo import (
    IAreaAccessRepository,
    IPersonnelRepository,
    NewPersonnel,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import (
    ensure_personnel_manager,
    ensure_personnel_viewer,
)
from src.domain.services.personnel_rules import FieldIssue, PersonnelRules, Severity
from src.domain.value_objects.enums import UserRole

_PHOTO_TYPES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}
DEACTIVATED_GRANT_REASON = "Personnel record deactivated."


def missing() -> NotFound:
    # The same answer whether the record does not exist or belongs to another campus.
    return NotFound("Personnel not found.", code="PERSONNEL_NOT_FOUND")


def raise_if_errors(issues: Sequence[FieldIssue]) -> None:
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        raise ValidationFailed(
            errors[0].message if len(errors) == 1 else f"{len(errors)} fields need attention.",
            code="INVALID_PERSONNEL",
            errors=[{"field": i.field, "code": i.code, "message": i.message} for i in errors],
        )


async def campus_rules(personnel: IPersonnelRepository) -> PersonnelRules:
    return PersonnelRules(await personnel.categories(), await personnel.custom_fields())


@dataclass(frozen=True, slots=True)
class PersonnelInput:
    """What the form sends: canonical values by key (``category`` may be the category ID),
    ``custom`` values by custom-field key, and an optional location selection."""

    values: Mapping[str, Any]
    custom: Mapping[str, Any]
    location: LocationSelection | None = None

    def raw(self) -> dict[str, Any]:
        return {**self.values, **{f"custom:{k}": v for k, v in self.custom.items()}}


class ViewPersonnel:
    def __init__(self, personnel: IPersonnelRepository, storage: ICampusMediaStorage | None = None) -> None:
        self._personnel = personnel
        self._storage = storage

    async def directory(self, actor: User, where: PersonnelFilter) -> tuple[list[PersonnelSummary], int]:
        ensure_personnel_viewer(actor)
        return await self._personnel.directory(where)

    async def summary(self, actor: User, personnel_id: UUID) -> PersonnelSummary:
        ensure_personnel_viewer(actor)
        found = await self._personnel.summary(personnel_id)
        if found is None:
            raise missing()
        return found

    async def details(self, actor: User, personnel_id: UUID) -> Personnel:
        """The full record: Admins only."""
        ensure_personnel_manager(actor)
        found = await self._personnel.get(personnel_id)
        if found is None:
            raise missing()
        return found

    async def history(self, actor: User, personnel_id: UUID) -> list[AuditEntry]:
        await self.details(actor, personnel_id)
        return await self._personnel.audit_trail("PERSONNEL", personnel_id)

    async def photo(self, actor: User, personnel_id: UUID) -> tuple[bytes, str]:
        if actor.role is UserRole.SUPERVISOR:
            raise PermissionDenied(
                "A personnel photo is shown to Supervisors only within an authorized identity review.",
                code="PHOTO_REVIEW_ONLY",
            )
        person = await self.details(actor, personnel_id)
        if not person.photo_path or self._storage is None:
            raise NotFound("This person has no photo.", code="PHOTO_NOT_FOUND")
        data = self._storage.read_file(person.organization_id, person.campus_id, person.photo_path)
        if data is None:
            raise NotFound("This person has no photo.", code="PHOTO_NOT_FOUND")
        return data, person.photo_content_type or "application/octet-stream"


def changed_fields(before: Personnel, values: PersonnelValues, location: LocationTarget | None) -> list[str]:
    """Names (never values) of what an edit changed, for the audit log."""
    changed = [
        f.key
        for f in CANONICAL_FIELDS
        if f.key not in ("category", "assigned_location") and getattr(before.values, f.key) != getattr(values, f.key)
    ]
    if before.values.category_id != values.category_id:
        changed.append("category")
    old = before.location
    old_target = None if old is None else (old.area_id or old.floor_id or old.building_id)
    if old_target != (None if location is None else location.id):
        changed.append("assigned_location")
    keys = set(before.values.custom_attributes) | set(values.custom_attributes)
    changed += [
        f"custom:{k}" for k in sorted(keys) if before.values.custom_attributes.get(k) != values.custom_attributes.get(k)
    ]
    return changed


class _PersonnelWriter:
    def __init__(
        self,
        personnel: IPersonnelRepository,
        locations: ILocationRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._personnel = personnel
        self._locations = LocationResolver(locations, transaction)
        self._audit = audit
        self._transaction = transaction
        self._clock = clock

    async def _clean(self, data: PersonnelInput) -> tuple[PersonnelValues, LocationTarget | None, list[FieldIssue]]:
        location = None if data.location is None else await self._locations.resolve(data.location)
        rules = await campus_rules(self._personnel)
        values, issues = rules.validate(data.raw(), today=self._clock().date(), location_given=location is not None)
        raise_if_errors(issues)
        assert values is not None
        return values, location, [i for i in issues if i.severity is Severity.WARNING]

    async def _read_back(self, personnel_id: UUID) -> Personnel:
        person = await self._personnel.get(personnel_id)
        if person is None:
            raise missing()
        return person


class RegisterPersonnel(_PersonnelWriter):
    async def execute(self, actor: User, data: PersonnelInput) -> tuple[Personnel, list[FieldIssue]]:
        ensure_personnel_manager(actor)
        values, location, warnings = await self._clean(data)
        person = NewPersonnel(uuid4(), generate_personnel_code(), values, location, CreatedVia.MANUAL.value)
        await self._personnel.add(person, actor.id)
        filled = [
            f.key for f in CANONICAL_FIELDS if f.key not in ("category", "assigned_location") and getattr(values, f.key)
        ]
        await self._audit.record(
            actor,
            "PERSONNEL_CREATED",
            "PERSONNEL",
            person.id,
            {
                "created_via": "MANUAL",
                "category_id": str(values.category_id),
                "fields": filled,
                "custom_fields": sorted(values.custom_attributes),
                "has_location": location is not None,
            },
        )
        created = await self._read_back(person.id)
        await self._transaction.commit()
        return created, warnings


class UpdatePersonnel(_PersonnelWriter):
    async def execute(
        self, actor: User, personnel_id: UUID, data: PersonnelInput
    ) -> tuple[Personnel, list[FieldIssue]]:
        ensure_personnel_manager(actor)
        before = await self._read_back(personnel_id)
        values, location, warnings = await self._clean(data)
        changes = changed_fields(before, values, location)
        if changes:
            await self._personnel.update(personnel_id, values, location, actor.id)
            await self._audit.record(actor, "PERSONNEL_UPDATED", "PERSONNEL", personnel_id, {"changed_fields": changes})
        updated = await self._read_back(personnel_id)
        await self._transaction.commit()
        return updated, warnings


class ChangePersonnelStatus:
    """Deactivation keeps the record (history, alerts and audit stay valid) and revokes the
    person's active restricted-area permissions. Reactivation does not restore them."""

    def __init__(
        self,
        personnel: IPersonnelRepository,
        access: IAreaAccessRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
    ) -> None:
        self._personnel = personnel
        self._access = access
        self._audit = audit
        self._transaction = transaction

    async def deactivate(self, actor: User, personnel_id: UUID, reason: str | None) -> Personnel:
        ensure_personnel_manager(actor)
        if await self._personnel.get(personnel_id) is None:
            raise missing()
        note = (reason or "").strip() or None
        if note is not None and len(note) > 500:
            raise ValidationFailed("The reason must be at most 500 characters.", code="INVALID_REASON")
        if not await self._personnel.set_status(personnel_id, False, actor.id, note):
            raise Conflict("This person is already inactive.", code="PERSONNEL_ALREADY_INACTIVE")
        revoked = await self._access.revoke_all_for([personnel_id], DEACTIVATED_GRANT_REASON, actor.id)
        await self._audit.record(
            actor,
            "PERSONNEL_DEACTIVATED",
            "PERSONNEL",
            personnel_id,
            {"reason_given": note is not None, "access_revoked": revoked},
        )
        person = await self._personnel.get(personnel_id)
        await self._transaction.commit()
        assert person is not None
        return person

    async def reactivate(self, actor: User, personnel_id: UUID) -> Personnel:
        ensure_personnel_manager(actor)
        if await self._personnel.get(personnel_id) is None:
            raise missing()
        if not await self._personnel.set_status(personnel_id, True, actor.id, None):
            raise Conflict("This person is already active.", code="PERSONNEL_ALREADY_ACTIVE")
        await self._audit.record(actor, "PERSONNEL_REACTIVATED", "PERSONNEL", personnel_id, {})
        person = await self._personnel.get(personnel_id)
        await self._transaction.commit()
        assert person is not None
        return person


class ManagePersonnelPhoto:
    """An ordinary profile photo (JPEG or PNG) under the campus folder. It is **not** a biometric
    template and is never used to identify anyone (face recognition is a separate future module)."""

    def __init__(
        self,
        personnel: IPersonnelRepository,
        storage: ICampusMediaStorage,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        max_bytes: int,
    ) -> None:
        self._personnel = personnel
        self._storage = storage
        self._audit = audit
        self._transaction = transaction
        self._max = max_bytes

    async def upload(self, actor: User, personnel_id: UUID, data: bytes) -> Personnel:
        ensure_personnel_manager(actor)
        person = await self._personnel.get(personnel_id)
        if person is None:
            raise missing()
        if not data or len(data) > self._max:
            raise ValidationFailed(f"The photo must be at most {self._max} bytes.", code="PHOTO_TOO_LARGE")
        kind = next((v for sig, v in _PHOTO_TYPES.items() if data.startswith(sig)), None)
        if kind is None:
            raise ValidationFailed("The photo must be a JPEG or PNG image.", code="INVALID_PHOTO")
        path = f"personnel/{personnel_id}/photo-{uuid4().hex}.{kind[1]}"
        self._storage.write_file(person.organization_id, person.campus_id, path, data)
        try:
            await self._personnel.set_photo(personnel_id, path, kind[0], actor.id)
            await self._audit.record(actor, "PERSONNEL_PHOTO_UPDATED", "PERSONNEL", personnel_id, {})
            updated = await self._personnel.get(personnel_id)
            await self._transaction.commit()
        except BaseException:
            self._storage.remove_file(person.organization_id, person.campus_id, path)
            raise
        if person.photo_path:
            self._storage.remove_file(person.organization_id, person.campus_id, person.photo_path)
        assert updated is not None
        return updated

    async def remove(self, actor: User, personnel_id: UUID) -> None:
        ensure_personnel_manager(actor)
        person = await self._personnel.get(personnel_id)
        if person is None:
            raise missing()
        if not person.photo_path:
            raise NotFound("This person has no photo.", code="PHOTO_NOT_FOUND")
        await self._personnel.set_photo(personnel_id, None, None, actor.id)
        await self._audit.record(actor, "PERSONNEL_PHOTO_REMOVED", "PERSONNEL", personnel_id, {})
        await self._transaction.commit()
        self._storage.remove_file(person.organization_id, person.campus_id, person.photo_path)


# --- Categories and custom fields ----------------------------------------------------------------------

_CATEGORY_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
_FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_RESERVED_KEYS = (
    {f.key for f in CANONICAL_FIELDS}
    | set(LOCATION_TARGETS)
    | {
        "personnel_id",
        "personnel_code",
        "organization_id",
        "campus_id",
        "status",
        "id",
    }
)


def _label(value: str, what: str, longest: int = 60) -> str:
    text = value.strip()
    if not text or len(text) > longest or any(ord(c) < 32 for c in text):
        raise ValidationFailed(f"{what} must be 1 to {longest} characters.", code="INVALID_NAME")
    return text


def _optional_text(value: str | None, longest: int, code: str) -> str | None:
    text = (value or "").strip() or None
    if text is not None and len(text) > longest:
        raise ValidationFailed(f"The text must be at most {longest} characters.", code=code)
    return text


def _required_fields(fields: Sequence[str]) -> tuple[str, ...]:
    unknown = [f for f in fields if f not in REQUIRABLE_FIELDS]
    if unknown:
        raise ValidationFailed(
            f"These fields cannot be made required: {', '.join(unknown)}. Allowed: {', '.join(REQUIRABLE_FIELDS)}.",
            code="INVALID_REQUIRED_FIELDS",
        )
    return tuple(dict.fromkeys(fields))


@dataclass(frozen=True, slots=True)
class CategoryDetails:
    label: str
    description: str | None = None
    required_fields: tuple[str, ...] = ()
    is_active: bool = True
    sort_order: int = 100


@dataclass(frozen=True, slots=True)
class CustomFieldDetails:
    label: str
    choices: tuple[str, ...] = ()
    is_required: bool = False
    category_ids: tuple[UUID, ...] = ()
    max_length: int = 255
    help_text: str | None = None
    is_active: bool = True


class ManagePersonnelConfiguration:
    def __init__(
        self, personnel: IPersonnelRepository, audit: IOrganizationAuditLog, transaction: ITransaction
    ) -> None:
        self._personnel = personnel
        self._audit = audit
        self._transaction = transaction

    async def categories(self, actor: User) -> list[PersonnelCategory]:
        ensure_personnel_viewer(actor)
        return await self._personnel.categories()

    async def _category(self, category_id: UUID) -> PersonnelCategory:
        found = next((c for c in await self._personnel.categories() if c.id == category_id), None)
        if found is None:
            raise NotFound("Category not found.", code="CATEGORY_NOT_FOUND")
        return found

    async def create_category(self, actor: User, code: str, details: CategoryDetails) -> PersonnelCategory:
        ensure_personnel_manager(actor)
        clean_code = code.strip().upper()
        if not _CATEGORY_CODE.match(clean_code):
            raise ValidationFailed(
                "The code must be 2 to 40 capital letters, digits or _.", code="INVALID_CATEGORY_CODE"
            )
        category = PersonnelCategory(
            id=uuid4(),
            code=clean_code,
            label=_label(details.label, "The category name"),
            required_fields=_required_fields(details.required_fields),
            description=_optional_text(details.description, 500, "INVALID_DESCRIPTION"),
            is_active=details.is_active,
            sort_order=details.sort_order,
        )
        await self._personnel.add_category(category)
        await self._audit.record(
            actor, "PERSONNEL_CATEGORY_CREATED", "PERSONNEL_CATEGORY", category.id, {"code": clean_code}
        )
        created = await self._category(category.id)
        await self._transaction.commit()
        return created

    async def update_category(self, actor: User, category_id: UUID, details: CategoryDetails) -> PersonnelCategory:
        """The code stays (records and imports refer to it); everything else can change."""
        ensure_personnel_manager(actor)
        current = await self._category(category_id)
        updated = replace(
            current,
            label=_label(details.label, "The category name"),
            description=_optional_text(details.description, 500, "INVALID_DESCRIPTION"),
            required_fields=_required_fields(details.required_fields),
            is_active=details.is_active,
            sort_order=details.sort_order,
        )
        await self._personnel.update_category(updated)
        await self._audit.record(
            actor,
            "PERSONNEL_CATEGORY_UPDATED",
            "PERSONNEL_CATEGORY",
            category_id,
            {"code": current.code, "is_active": updated.is_active},
        )
        changed = await self._category(category_id)
        await self._transaction.commit()
        return changed

    async def custom_fields(self, actor: User) -> list[CustomField]:
        ensure_personnel_manager(actor)
        return await self._personnel.custom_fields()

    async def _checked(self, field_type: FieldType, details: CustomFieldDetails) -> CustomFieldDetails:
        choices = tuple(dict.fromkeys(c.strip() for c in details.choices if c.strip()))
        if field_type is FieldType.CHOICE:
            if not choices or len(choices) > 100 or any(len(c) > 100 for c in choices):
                raise ValidationFailed(
                    "A choice field needs 1 to 100 choices of at most 100 characters.", code="INVALID_CHOICES"
                )
        elif choices:
            raise ValidationFailed("Only choice fields have choices.", code="INVALID_CHOICES")
        if not 1 <= details.max_length <= 2000:
            raise ValidationFailed("The maximum length must be 1 to 2000.", code="INVALID_MAX_LENGTH")
        known = {c.id for c in await self._personnel.categories()}
        if any(c not in known for c in details.category_ids):
            raise NotFound("Category not found.", code="CATEGORY_NOT_FOUND")
        return replace(
            details,
            label=_label(details.label, "The field name"),
            choices=choices,
            category_ids=tuple(dict.fromkeys(details.category_ids)),
            help_text=_optional_text(details.help_text, 300, "INVALID_HELP_TEXT"),
        )

    async def create_custom_field(
        self, actor: User, key: str, field_type: FieldType, details: CustomFieldDetails
    ) -> CustomField:
        ensure_personnel_manager(actor)
        clean_key = key.strip().lower()
        if not _FIELD_KEY.match(clean_key) or clean_key in _RESERVED_KEYS:
            raise ValidationFailed(
                "The key must be 2 to 40 lower-case letters, digits or _, and not a built-in field name.",
                code="INVALID_FIELD_KEY",
            )
        checked = await self._checked(field_type, details)
        field = CustomField(
            id=uuid4(),
            key=clean_key,
            label=checked.label,
            field_type=field_type,
            choices=checked.choices,
            is_required=checked.is_required,
            category_ids=checked.category_ids,
            max_length=checked.max_length,
            help_text=checked.help_text,
            is_active=checked.is_active,
        )
        await self._personnel.add_custom_field(field, actor.id)
        await self._audit.record(
            actor, "PERSONNEL_FIELD_CREATED", "PERSONNEL_FIELD", field.id, {"key": clean_key, "type": field_type.value}
        )
        created = next(f for f in await self._personnel.custom_fields() if f.id == field.id)
        await self._transaction.commit()
        return created

    async def update_custom_field(self, actor: User, field_id: UUID, details: CustomFieldDetails) -> CustomField:
        """Key and type stay (stored values depend on them)."""
        ensure_personnel_manager(actor)
        current = next((f for f in await self._personnel.custom_fields() if f.id == field_id), None)
        if current is None:
            raise NotFound("Custom field not found.", code="CUSTOM_FIELD_NOT_FOUND")
        checked = await self._checked(current.field_type, details)
        updated = replace(
            current,
            label=checked.label,
            choices=checked.choices,
            is_required=checked.is_required,
            category_ids=checked.category_ids,
            max_length=checked.max_length,
            help_text=checked.help_text,
            is_active=checked.is_active,
        )
        await self._personnel.update_custom_field(updated)
        await self._audit.record(
            actor,
            "PERSONNEL_FIELD_UPDATED",
            "PERSONNEL_FIELD",
            field_id,
            {"key": current.key, "is_active": updated.is_active},
        )
        changed = next(f for f in await self._personnel.custom_fields() if f.id == field_id)
        await self._transaction.commit()
        return changed
