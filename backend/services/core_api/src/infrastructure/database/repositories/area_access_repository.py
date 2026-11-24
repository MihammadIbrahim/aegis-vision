"""Restricted areas and personnel access grants of one campus (migration 0017).

Areas and their restricted marking are readable by Admins and Supervisors. Grants are read through
``app_area_access_list`` (basic personnel columns; revoked history and notes for Admins only) and
written by Admins only (Row-Level Security)."""

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.area_access import (
    AccessGrant,
    AccessSchedule,
    GrantStatus,
    GrantTerms,
    RestrictedArea,
)
from src.domain.exceptions.domain_errors import Conflict, ValidationFailed

_UUIDS = ("id", "org", "campus", "area", "pid", "actor", "grant")
_SCOPE = "x.organization_id = :org AND x.campus_id = :campus"
_AREAS = """SELECT x.id AS area_id, x.name AS area_name, b.id AS building_id, b.name AS building_name,
                   f.id AS floor_id, f.name AS floor_name, r.area_id IS NOT NULL AS is_restricted,
                   r.access_note, r.marked_at,
                   (SELECT count(*) FROM app_area_access_list(x.id, NULL, NULL, false)) AS active_grants
              FROM areas x
              JOIN buildings b ON b.id = x.building_id
              LEFT JOIN floors f ON f.id = x.floor_id
              LEFT JOIN restricted_areas r ON r.area_id = x.id"""
_CONFLICTS = {
    "uq_area_access_active": ("ACCESS_ALREADY_GRANTED", "This person already has active access to this area."),
    "ck_area_access_restricted_area": ("AREA_NOT_RESTRICTED", "Mark the area as restricted before granting access."),
    "fk_area_access_personnel": ("PERSONNEL_NOT_FOUND", "Personnel not found."),
    "fk_personnel_area_access_area": ("AREA_NOT_FOUND", "Area not found."),
    "fk_restricted_areas_area": ("AREA_NOT_FOUND", "Area not found."),
}


def _translate(error: IntegrityError) -> Exception:
    cause = getattr(error.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None) or ""
    if name in _CONFLICTS:
        code, message = _CONFLICTS[name]
        return Conflict(message, code=code)
    return ValidationFailed("The access permission is not valid.", code="INVALID_ACCESS_GRANT")


def _schedule_params(terms: GrantTerms) -> dict[str, Any]:
    schedule = terms.schedule
    return {
        "days": None if schedule is None else list(schedule.days),
        "start": None if schedule is None else schedule.start,
        "end": None if schedule is None else schedule.end,
        "tz": None if schedule is None else schedule.timezone,
    }


def _grant(row: Any) -> AccessGrant:
    schedule = None
    if row.schedule_days:
        schedule = AccessSchedule(tuple(row.schedule_days), row.schedule_start, row.schedule_end, row.schedule_timezone)
    return AccessGrant(
        id=row.id,
        personnel_id=row.personnel_id,
        personnel_code=row.personnel_code,
        personnel_name=row.personnel_name,
        personnel_status=row.personnel_status,
        category_label=row.category_label,
        department=row.department,
        area_id=row.area_id,
        area_name=row.area_name,
        floor_name=row.floor_name,
        building_name=row.building_name,
        status=GrantStatus(row.status),
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        schedule=schedule,
        note=row.note,
        granted_at=row.granted_at,
        granted_by_name=row.granted_by_name,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
        revoked_by_name=row.revoked_by_name,
        revoke_reason=row.revoke_reason,
    )


class SqlAreaAccessRepository:
    def __init__(self, session: AsyncSession, organization_id: UUID, campus_id: UUID) -> None:
        self._session = session
        self._scope = {"org": organization_id, "campus": campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        merged = {k: v for k, v in {**self._scope, **(params or {})}.items() if re.search(rf":{k}\b", statement)}
        sql = text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in merged))
        try:
            return await self._session.execute(sql, merged)
        except IntegrityError as error:
            raise _translate(error) from error

    async def areas(self, *, restricted_only: bool) -> list[RestrictedArea]:
        where = f"WHERE {_SCOPE}" + (" AND r.area_id IS NOT NULL" if restricted_only else "")
        rows = await self._run(f"{_AREAS} {where} ORDER BY lower(b.name), lower(f.name) NULLS FIRST, lower(x.name)")
        return [RestrictedArea(**r._mapping) for r in rows]

    async def area(self, area_id: UUID) -> RestrictedArea | None:
        row = (await self._run(f"{_AREAS} WHERE {_SCOPE} AND x.id = :area", {"area": area_id})).one_or_none()
        return None if row is None else RestrictedArea(**row._mapping)

    async def mark_restricted(self, area_id: UUID, note: str | None, actor_id: UUID) -> bool:
        """True if newly marked, False if only the note changed."""
        row = (
            await self._run(
                "INSERT INTO restricted_areas (area_id, organization_id, campus_id, access_note, marked_by) "
                "VALUES (:area, :org, :campus, :note, :actor) ON CONFLICT (area_id) DO UPDATE "
                "SET access_note = EXCLUDED.access_note, updated_at = now() RETURNING (xmax = 0) AS inserted",
                {"area": area_id, "note": note, "actor": actor_id},
            )
        ).one()
        return bool(row.inserted)

    async def clear_restricted(self, area_id: UUID) -> bool:
        result = await self._run(
            f"DELETE FROM restricted_areas x WHERE {_SCOPE} AND x.area_id = :area RETURNING x.area_id",
            {"area": area_id},
        )
        return result.first() is not None

    async def grants(
        self,
        *,
        area_id: UUID | None = None,
        personnel_id: UUID | None = None,
        grant_id: UUID | None = None,
        include_revoked: bool = False,
    ) -> list[AccessGrant]:
        rows = await self._run(
            "SELECT * FROM app_area_access_list(:area, :pid, :grant, :revoked)",
            {"area": area_id, "pid": personnel_id, "grant": grant_id, "revoked": include_revoked},
        )
        return [_grant(r) for r in rows]

    async def add_grant(
        self, grant_id: UUID, personnel_id: UUID, area_id: UUID, terms: GrantTerms, valid_from: datetime, actor_id: UUID
    ) -> None:
        await self._run(
            "INSERT INTO personnel_area_access (id, organization_id, campus_id, personnel_id, area_id, valid_from, "
            "valid_until, schedule_days, schedule_start, schedule_end, schedule_timezone, note, granted_by, "
            "updated_by) VALUES (:grant, :org, :campus, :pid, :area, :from, :until, CAST(:days AS smallint[]), "
            ":start, :end, :tz, :note, :actor, :actor)",
            {
                "grant": grant_id,
                "pid": personnel_id,
                "area": area_id,
                "from": valid_from,
                "until": terms.valid_until,
                "note": terms.note,
                "actor": actor_id,
                **_schedule_params(terms),
            },
        )

    async def update_grant(self, grant_id: UUID, terms: GrantTerms, valid_from: datetime, actor_id: UUID) -> bool:
        result = await self._run(
            "UPDATE personnel_area_access x SET valid_from = :from, valid_until = :until, "
            "schedule_days = CAST(:days AS smallint[]), schedule_start = :start, schedule_end = :end, "
            "schedule_timezone = :tz, note = :note, updated_by = :actor, updated_at = now() "
            f"WHERE {_SCOPE} AND x.id = :grant AND x.status = 'ACTIVE' RETURNING x.id",
            {
                "grant": grant_id,
                "from": valid_from,
                "until": terms.valid_until,
                "note": terms.note,
                "actor": actor_id,
                **_schedule_params(terms),
            },
        )
        return result.first() is not None

    async def revoke_grant(self, grant_id: UUID, reason: str | None, actor_id: UUID) -> bool:
        result = await self._run(
            "UPDATE personnel_area_access x SET status = 'REVOKED', revoked_at = now(), revoked_by = :actor, "
            "revoke_reason = :reason, updated_by = :actor, updated_at = now() "
            f"WHERE {_SCOPE} AND x.id = :grant AND x.status = 'ACTIVE' RETURNING x.id",
            {"grant": grant_id, "reason": reason, "actor": actor_id},
        )
        return result.first() is not None

    async def revoke_all_for(self, personnel_ids: Sequence[UUID], reason: str, actor_id: UUID) -> int:
        if not personnel_ids:
            return 0
        result = await self._session.execute(
            text(
                "UPDATE personnel_area_access x SET status = 'REVOKED', revoked_at = now(), revoked_by = :actor, "
                "revoke_reason = :reason, updated_by = :actor, updated_at = now() "
                f"WHERE {_SCOPE} AND x.status = 'ACTIVE' AND x.personnel_id = ANY(:ids) RETURNING x.id"
            ).bindparams(*(bindparam(n, type_=Uuid) for n in ("org", "campus", "actor"))),
            {**self._scope, "actor": actor_id, "reason": reason, "ids": list(personnel_ids)},
        )
        return len(result.all())
