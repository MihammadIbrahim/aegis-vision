"""Personnel, categories and custom fields of one campus (migration 0017, decision D-016).

Admins read and write the tables directly; Row-Level Security refuses everyone else. The
directory and the audit trail go through the narrow functions of migration 0017, which return
only what the caller's role may see (Supervisors get basic columns)."""

import json
import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.location import LocationTarget
from src.domain.entities.personnel import (
    AuditEntry,
    CreatedVia,
    CustomField,
    FieldType,
    Personnel,
    PersonnelCategory,
    PersonnelFilter,
    PersonnelStatus,
    PersonnelSummary,
    PersonnelValues,
)
from src.domain.exceptions.domain_errors import Conflict, ValidationFailed
from src.domain.repositories.i_personnel_repo import NewPersonnel
from src.domain.value_objects.location_path import LocationPath

_UUIDS = ("id", "org", "campus", "category", "building_id", "floor_id", "area_id", "batch", "actor", "pid", "area")
_SCOPE = "x.organization_id = :org AND x.campus_id = :campus"

_CONFLICTS: dict[str, tuple[str, str]] = {
    "uq_personnel_external_id": (
        "EXTERNAL_ID_TAKEN",
        "Another person in this campus already has this Institutional ID.",
    ),
    "uq_personnel_email": ("EMAIL_TAKEN", "Another person in this campus already has this email address."),
    "uq_personnel_code": ("PERSONNEL_ID_COLLISION", "A Personnel ID collided. Please try again."),
    "uq_personnel_categories_code": ("CATEGORY_TAKEN", "A category with this code already exists in this campus."),
    "uq_personnel_categories_label": ("CATEGORY_TAKEN", "A category with this name already exists in this campus."),
    "uq_personnel_custom_fields_key": ("CUSTOM_FIELD_TAKEN", "A custom field with this key already exists."),
    "uq_personnel_custom_fields_label": ("CUSTOM_FIELD_TAKEN", "A custom field with this name already exists."),
    "fk_personnel_category": ("INVALID_CATEGORY", "The category does not belong to this campus."),
    "fk_personnel_building": ("INVALID_LOCATION", "The assigned location is not part of this campus."),
    "fk_personnel_floor": ("INVALID_LOCATION", "The assigned location is not part of this campus."),
    "fk_personnel_area": ("INVALID_LOCATION", "The assigned location is not part of this campus."),
}

_PERSON_COLUMNS = """x.id, x.organization_id, x.campus_id, x.personnel_code, x.external_id, x.full_name, x.category_id,
       x.email, x.phone, x.department, x.designation, x.joining_date, x.start_date, x.end_date, x.status, x.address,
       x.emergency_contact_name, x.emergency_contact_phone, x.emergency_contact_relation, x.notes,
       x.custom_attributes, x.photo_path, x.photo_content_type, x.created_via, x.import_batch_id, x.created_at,
       x.updated_at, x.deactivated_at, x.deactivation_reason,
       cu.full_name AS created_by_name, uu.full_name AS updated_by_name, du.full_name AS deactivated_by_name,
       c.code AS c_code, c.label AS c_label, c.required_fields AS c_required, c.description AS c_description,
       c.is_system AS c_system, c.is_active AS c_active, c.sort_order AS c_sort,
       b.id AS building_id, b.name AS building_name, b.kind AS building_kind,
       f.id AS floor_id, f.name AS floor_name, a.id AS area_id, a.name AS area_name"""
_PERSON_JOINS = """JOIN personnel_categories c ON c.id = x.category_id
       LEFT JOIN users cu ON cu.id = x.created_by
       LEFT JOIN users uu ON uu.id = x.updated_by
       LEFT JOIN users du ON du.id = x.deactivated_by
       LEFT JOIN areas a ON a.id = x.area_id
       LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, a.floor_id)
       LEFT JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, a.building_id)"""

_VALUE_COLUMNS = (
    "external_id",
    "full_name",
    "email",
    "phone",
    "department",
    "designation",
    "joining_date",
    "start_date",
    "end_date",
    "address",
    "emergency_contact_name",
    "emergency_contact_phone",
    "emergency_contact_relation",
    "notes",
)

_INSERT = (
    "INSERT INTO personnel (id, organization_id, campus_id, personnel_code, category_id, "
    + ", ".join(_VALUE_COLUMNS)
    + ", custom_attributes, building_id, floor_id, area_id, created_via, import_batch_id, created_by, updated_by) "
    "VALUES (:id, :org, :campus, :code, :category, "
    + ", ".join(f":{c}" for c in _VALUE_COLUMNS)
    + ", CAST(:custom AS jsonb), :building_id, :floor_id, :area_id, :via, :batch, :actor, :actor)"
)


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params))


def _constraint(error: IntegrityError) -> str | None:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None)


def translate(error: IntegrityError) -> Exception:
    name = _constraint(error) or ""
    if name in _CONFLICTS:
        code, message = _CONFLICTS[name]
        return Conflict(message, code=code)
    return ValidationFailed("The personnel details are not valid.", code="INVALID_PERSONNEL")


def _link(location: LocationTarget | None) -> dict[str, UUID | None]:
    ids: dict[str, UUID | None] = {"building_id": None, "floor_id": None, "area_id": None}
    if location is not None:
        ids[f"{location.level.value.lower()}_id"] = location.id
    return ids


def _value_params(values: PersonnelValues) -> dict[str, Any]:
    params: dict[str, Any] = {c: getattr(values, c) for c in _VALUE_COLUMNS}
    params["category"] = values.category_id
    params["custom"] = json.dumps(values.custom_attributes, default=str)
    return params


def category_from(row: Any, prefix: str = "") -> PersonnelCategory:
    get = (lambda n: getattr(row, prefix + n)) if prefix else (lambda n: getattr(row, n))
    return PersonnelCategory(
        id=row.category_id if prefix else row.id,
        code=get("code"),
        label=get("label"),
        required_fields=tuple(get("required") if prefix else row.required_fields),
        description=get("description"),
        is_system=get("system") if prefix else row.is_system,
        is_active=get("active") if prefix else row.is_active,
        sort_order=get("sort") if prefix else row.sort_order,
    )


def _person(row: Any) -> Personnel:
    values = PersonnelValues(
        category_id=row.category_id,
        custom_attributes=dict(row.custom_attributes or {}),
        **{c: getattr(row, c) for c in _VALUE_COLUMNS},
    )
    location = None
    if row.building_id is not None:
        location = LocationPath(
            row.building_id,
            row.building_name,
            row.building_kind,
            row.floor_id,
            row.floor_name,
            row.area_id,
            row.area_name,
        )
    return Personnel(
        id=row.id,
        organization_id=row.organization_id,
        campus_id=row.campus_id,
        personnel_code=row.personnel_code,
        values=values,
        category=category_from(row, "c_"),
        status=PersonnelStatus(row.status),
        created_via=CreatedVia(row.created_via),
        location=location,
        import_batch_id=row.import_batch_id,
        has_photo=row.photo_path is not None,
        photo_path=row.photo_path,
        photo_content_type=row.photo_content_type,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
        updated_by_name=row.updated_by_name,
        updated_at=row.updated_at,
        deactivated_by_name=row.deactivated_by_name,
        deactivated_at=row.deactivated_at,
        deactivation_reason=row.deactivation_reason,
    )


def _custom_field(row: Any) -> CustomField:
    return CustomField(
        id=row.id,
        key=row.field_key,
        label=row.label,
        field_type=FieldType(row.field_type),
        choices=tuple(row.choices or ()),
        is_required=row.is_required,
        category_ids=tuple(row.category_ids or ()),
        max_length=row.max_length,
        help_text=row.help_text,
        is_active=row.is_active,
    )


class SqlPersonnelRepository:
    def __init__(self, session: AsyncSession, organization_id: UUID, campus_id: UUID) -> None:
        self._session = session
        self._scope = {"org": organization_id, "campus": campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        merged = {k: v for k, v in {**self._scope, **(params or {})}.items() if re.search(rf":{k}\b", statement)}
        try:
            return await self._session.execute(_sql(statement, merged), merged)
        except IntegrityError as error:
            raise translate(error) from error

    # --- Categories and custom fields -----------------------------------------------------------------

    async def categories(self) -> list[PersonnelCategory]:
        rows = await self._run(
            "SELECT id, code, label, required_fields, description, is_system, is_active, sort_order "
            f"FROM personnel_categories x WHERE {_SCOPE} ORDER BY sort_order, lower(label)"
        )
        return [category_from(r) for r in rows]

    async def add_category(self, category: PersonnelCategory) -> None:
        await self._run(
            "INSERT INTO personnel_categories (id, organization_id, campus_id, code, label, description, "
            "required_fields, is_system, is_active, sort_order) VALUES (:id, :org, :campus, :code, :label, "
            ":description, :required, false, :active, :sort)",
            {
                "id": category.id,
                "code": category.code,
                "label": category.label,
                "description": category.description,
                "required": list(category.required_fields),
                "active": category.is_active,
                "sort": category.sort_order,
            },
        )

    async def update_category(self, category: PersonnelCategory) -> None:
        await self._run(
            "UPDATE personnel_categories x SET label = :label, description = :description, "
            "required_fields = :required, is_active = :active, sort_order = :sort, updated_at = now() "
            f"WHERE {_SCOPE} AND x.id = :id",
            {
                "id": category.id,
                "label": category.label,
                "description": category.description,
                "required": list(category.required_fields),
                "active": category.is_active,
                "sort": category.sort_order,
            },
        )

    async def custom_fields(self) -> list[CustomField]:
        rows = await self._run(
            "SELECT id, field_key, label, field_type, choices, is_required, category_ids, max_length, help_text, "
            f"is_active FROM personnel_custom_fields x WHERE {_SCOPE} ORDER BY lower(label)"
        )
        return [_custom_field(r) for r in rows]

    async def add_custom_field(self, field: CustomField, created_by: UUID) -> None:
        await self._run(
            "INSERT INTO personnel_custom_fields (id, organization_id, campus_id, field_key, label, field_type, "
            "choices, is_required, category_ids, max_length, help_text, is_active, created_by) VALUES (:id, :org, "
            ":campus, :key, :label, :type, :choices, :required, :categories, :max_length, :help, :active, :actor)",
            {**self._field_params(field), "actor": created_by},
        )

    async def update_custom_field(self, field: CustomField) -> None:
        await self._run(
            "UPDATE personnel_custom_fields x SET label = :label, choices = :choices, is_required = :required, "
            "category_ids = :categories, max_length = :max_length, help_text = :help, is_active = :active, "
            f"updated_at = now() WHERE {_SCOPE} AND x.id = :id",
            self._field_params(field),
        )

    @staticmethod
    def _field_params(field: CustomField) -> dict[str, Any]:
        return {
            "id": field.id,
            "key": field.key,
            "label": field.label,
            "type": field.field_type.value,
            "choices": list(field.choices),
            "required": field.is_required,
            "categories": list(field.category_ids),
            "max_length": field.max_length,
            "help": field.help_text,
            "active": field.is_active,
        }

    # --- Personnel ----------------------------------------------------------------------------------------

    async def directory(self, where: PersonnelFilter) -> tuple[list[PersonnelSummary], int]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM app_personnel_directory(:pid, :q, :category, :status, :department, :designation, "
                    ":area, :limit, :offset)"
                ).bindparams(*(bindparam(n, type_=Uuid) for n in ("pid", "category", "area"))),
                {
                    "pid": None,
                    "q": where.text,
                    "category": where.category_id,
                    "status": None if where.status is None else where.status.value,
                    "department": where.department,
                    "designation": where.designation,
                    "area": where.area_id,
                    "limit": where.limit,
                    "offset": where.offset,
                },
            )
        ).all()
        total = rows[0].total_count if rows else 0
        if not rows and where.offset:
            # Past the last page: count without paging.
            counted = await self.directory(replace(where, limit=1, offset=0))
            total = counted[1]
        return [_summary(r) for r in rows], int(total)

    async def summary(self, personnel_id: UUID) -> PersonnelSummary | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM app_personnel_directory(:pid, NULL, NULL, NULL, NULL, NULL, NULL, 1, 0)"
                ).bindparams(bindparam("pid", type_=Uuid)),
                {"pid": personnel_id},
            )
        ).one_or_none()
        return None if row is None else _summary(row)

    async def get(self, personnel_id: UUID) -> Personnel | None:
        row = (
            await self._run(
                f"SELECT {_PERSON_COLUMNS} FROM personnel x {_PERSON_JOINS} WHERE {_SCOPE} AND x.id = :id",
                {"id": personnel_id},
            )
        ).one_or_none()
        return None if row is None else _person(row)

    def _insert_params(self, person: NewPersonnel, actor_id: UUID) -> dict[str, Any]:
        return {
            **self._scope,
            "id": person.id,
            "code": person.personnel_code,
            **_value_params(person.values),
            **_link(person.location),
            "via": person.created_via,
            "batch": person.import_batch_id,
            "actor": actor_id,
        }

    async def add(self, person: NewPersonnel, actor_id: UUID) -> None:
        await self._run(_INSERT, self._insert_params(person, actor_id))

    async def add_many(self, people: Sequence[NewPersonnel], actor_id: UUID) -> None:
        if not people:
            return
        params = [self._insert_params(p, actor_id) for p in people]
        try:
            await self._session.execute(_sql(_INSERT, params[0]), params)
        except IntegrityError as error:
            raise translate(error) from error

    async def update(
        self, personnel_id: UUID, values: PersonnelValues, location: LocationTarget | None, actor_id: UUID
    ) -> None:
        sets = ", ".join(f"{c} = :{c}" for c in _VALUE_COLUMNS)
        await self._run(
            f"UPDATE personnel x SET {sets}, category_id = :category, custom_attributes = CAST(:custom AS jsonb), "
            "building_id = :building_id, floor_id = :floor_id, area_id = :area_id, updated_by = :actor, "
            f"updated_at = now() WHERE {_SCOPE} AND x.id = :id",
            {"id": personnel_id, **_value_params(values), **_link(location), "actor": actor_id},
        )

    async def set_status(self, personnel_id: UUID, active: bool, actor_id: UUID, reason: str | None) -> bool:
        if active:
            statement = (
                "UPDATE personnel x SET status = 'ACTIVE', deactivated_at = NULL, deactivated_by = NULL, "
                "deactivation_reason = NULL, updated_by = :actor, updated_at = now() "
                f"WHERE {_SCOPE} AND x.id = :id AND x.status = 'INACTIVE' RETURNING x.id"
            )
        else:
            statement = (
                "UPDATE personnel x SET status = 'INACTIVE', deactivated_at = now(), deactivated_by = :actor, "
                "deactivation_reason = :reason, updated_by = :actor, updated_at = now() "
                f"WHERE {_SCOPE} AND x.id = :id AND x.status = 'ACTIVE' RETURNING x.id"
            )
        result = await self._run(statement, {"id": personnel_id, "actor": actor_id, "reason": reason})
        return result.first() is not None

    async def set_photo(self, personnel_id: UUID, path: str | None, content_type: str | None, actor_id: UUID) -> None:
        await self._run(
            "UPDATE personnel x SET photo_path = :path, photo_content_type = :type, updated_by = :actor, "
            f"updated_at = now() WHERE {_SCOPE} AND x.id = :id",
            {"id": personnel_id, "path": path, "type": content_type, "actor": actor_id},
        )

    async def existing_identifiers(
        self, external_ids: Sequence[str], emails: Sequence[str]
    ) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        found_emails: set[str] = set()
        if external_ids:
            rows = await self._run(
                f"SELECT lower(x.external_id) FROM personnel x WHERE {_SCOPE} AND lower(x.external_id) = ANY(:ids)",
                {"ids": list(external_ids)},
            )
            ids = {r[0] for r in rows}
        if emails:
            rows = await self._run(
                f"SELECT x.email FROM personnel x WHERE {_SCOPE} AND x.email = ANY(:emails)", {"emails": list(emails)}
            )
            found_emails = {r[0] for r in rows}
        return ids, found_emails

    async def deactivate_import(self, batch_id: UUID, actor_id: UUID, reason: str) -> list[UUID]:
        rows = await self._run(
            "UPDATE personnel x SET status = 'INACTIVE', deactivated_at = now(), deactivated_by = :actor, "
            "deactivation_reason = :reason, updated_by = :actor, updated_at = now() "
            f"WHERE {_SCOPE} AND x.import_batch_id = :batch AND x.status = 'ACTIVE' RETURNING x.id",
            {"batch": batch_id, "actor": actor_id, "reason": reason},
        )
        return [r[0] for r in rows]

    async def audit_trail(self, target_type: str, target_id: UUID) -> list[AuditEntry]:
        rows = await self._session.execute(
            text("SELECT * FROM app_personnel_audit_trail(:type, :target)").bindparams(bindparam("target", type_=Uuid)),
            {"type": target_type, "target": target_id},
        )
        return [
            AuditEntry(
                r.occurred_at,
                r.action,
                r.target_type,
                r.target_id,
                r.actor_role,
                r.actor_display_id,
                r.actor_name,
                dict(r.metadata or {}),
            )
            for r in rows
        ]


def _summary(row: Any) -> PersonnelSummary:
    return PersonnelSummary(
        id=row.id,
        personnel_code=row.personnel_code,
        external_id=row.external_id,
        full_name=row.full_name,
        category_id=row.category_id,
        category_code=row.category_code,
        category_label=row.category_label,
        department=row.department,
        designation=row.designation,
        status=PersonnelStatus(row.status),
        email=row.email,
        phone=row.phone,
        start_date=row.start_date,
        end_date=row.end_date,
        building_name=row.building_name,
        floor_name=row.floor_name,
        area_name=row.area_name,
    )
