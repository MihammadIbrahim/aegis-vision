"""User Records & Rejoining, ID regeneration and permanent deletion (migration 0029). Runs for the signed-in
Admin or Supervisor under Row-Level Security (own campus only); the account changes that need more go
through the database functions of migration 0029."""

import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.user_records_use_cases import ActivityEntry, StatusChange, UserRecord, UserRecordFilter
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict

_UUIDS = ("id", "org", "campus", "user", "actor")
_PARAM = re.compile(r"(?<!:):([a-z_]+)\b")

_SELECT = """SELECT u.id, u.display_id, u.role, u.status, u.full_name, u.username, u.email, u.phone, u.created_at,
       u.deleted_at, b.name AS building_name, f.name AS floor_name, a.name AS area_name,
       (SELECT max(h.changed_at) FROM user_status_history h WHERE h.user_id = u.id) AS status_changed_at
  FROM users u
  LEFT JOIN guard_location_assignments g ON g.guard_user_id = u.id
  LEFT JOIN areas a ON a.id = g.area_id
  LEFT JOIN floors f ON f.id = COALESCE(g.floor_id, a.floor_id)
  LEFT JOIN buildings b ON b.id = COALESCE(g.building_id, f.building_id, a.building_id)"""
_SCOPE = "u.organization_id = :org AND u.campus_id = :campus AND u.role IN ('SUPERVISOR', 'GUARD')"


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _record(r: Any) -> UserRecord:
    location = " › ".join(p for p in (r.building_name, r.floor_name, r.area_name) if p) or None
    return UserRecord(
        user_id=r.id,
        display_id=r.display_id,
        role=r.role,
        status=r.status,
        full_name=r.full_name,
        username=r.username,
        email=None if r.deleted_at else r.email,
        phone=r.phone,
        created_at=r.created_at,
        deleted_at=r.deleted_at,
        location=location,
        status_changed_at=r.status_changed_at,
    )


class SqlUserRecordsRepository:
    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._session = session
        self._actor = actor
        self._scope = {"org": actor.organization_id, "campus": actor.campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        values = {**self._scope, **(params or {})}
        used = set(_PARAM.findall(statement))
        values = {k: v for k, v in values.items() if k in used}
        bound = text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in values))
        return await self._session.execute(bound, values)

    async def records(self, where: UserRecordFilter) -> tuple[list[UserRecord], int]:
        clauses = [_SCOPE]
        params: dict[str, Any] = {"limit": where.limit, "offset": where.offset}
        if where.role:
            clauses.append("u.role = :role")
            params["role"] = where.role
        if where.status:
            clauses.append("u.status = :status")
            params["status"] = where.status
        if not where.include_deleted:
            clauses.append("u.deleted_at IS NULL")
        if where.text and where.text.strip():
            clauses.append(
                "(u.display_id ILIKE :q ESCAPE '\\' OR u.full_name ILIKE :q ESCAPE '\\' "
                "OR u.username ILIKE :q ESCAPE '\\' OR u.email ILIKE :q ESCAPE '\\')"
            )
            params["q"] = _like(where.text.strip())
        where_sql = " AND ".join(clauses)
        total = (await self._run(f"SELECT count(*) FROM users u WHERE {where_sql}", params)).scalar_one()
        rows = await self._run(
            f"{_SELECT} WHERE {where_sql} ORDER BY (u.status = 'ACTIVE') DESC, lower(u.full_name), u.id "
            "LIMIT :limit OFFSET :offset",
            params,
        )
        return [_record(r) for r in rows], int(total)

    async def record(self, user_id: UUID) -> UserRecord | None:
        row = (await self._run(f"{_SELECT} WHERE {_SCOPE} AND u.id = :user", {"user": user_id})).first()
        return None if row is None else _record(row)

    async def by_display_id(self, display_id: str) -> UserRecord | None:
        row = (
            await self._run(
                f"{_SELECT} WHERE {_SCOPE} AND upper(u.display_id) = upper(:display)", {"display": display_id}
            )
        ).first()
        return None if row is None else _record(row)

    async def history(self, user_id: UUID) -> list[StatusChange]:
        rows = await self._run(
            """SELECT h.from_status, h.to_status, h.event, h.changed_at, c.full_name AS changed_by_name
                 FROM user_status_history h LEFT JOIN users c ON c.id = h.changed_by
                WHERE h.user_id = :user ORDER BY h.changed_at, h.id""",
            {"user": user_id},
        )
        return [StatusChange(r.from_status, r.to_status, r.event, r.changed_at, r.changed_by_name) for r in rows]

    async def activity(self, user_id: UUID, limit: int = 50) -> list[ActivityEntry]:
        rows = await self._run(
            """SELECT l.occurred_at, l.action, l.category, l.outcome, l.target_type,
                      l.actor_user_id = :user AS by_user
                 FROM organization_audit_logs l
                WHERE l.organization_id = :org AND l.campus_id = :campus
                  AND (l.actor_user_id = :user OR l.target_id = :user)
                ORDER BY l.occurred_at DESC LIMIT :limit""",
            {"user": user_id, "limit": limit},
        )
        return [ActivityEntry(r.occurred_at, r.action, r.category, r.outcome, r.target_type, bool(r.by_user))
                for r in rows]  # fmt: skip

    async def rejoin(self, user_id: UUID, full_name: str, username: str | None, phone: str | None) -> bool:
        try:
            rows = await self._run(
                f"""UPDATE users u SET full_name = :full_name, username = :username, phone = :phone, status = 'ACTIVE'
                     WHERE {_SCOPE} AND u.id = :user AND u.status = 'LEFT' AND u.deleted_at IS NULL RETURNING u.id""",
                {"user": user_id, "full_name": full_name, "username": username, "phone": phone},
            )
        except IntegrityError as error:
            if "uq_users_username_ci" in str(error.orig):
                raise Conflict("This username is already taken.", code="USERNAME_TAKEN") from error
            raise
        return rows.first() is not None

    async def set_password(self, user_id: UUID, password_hash: str) -> str:
        return str(
            (
                await self._run(
                    "SELECT app_staff_rejoin_password(:user, :hash)", {"user": user_id, "hash": password_hash}
                )
            ).scalar_one()
        )

    async def regenerate_display_id(self, user_id: UUID, new_id: str) -> str:
        return str(
            (
                await self._run("SELECT app_display_id_regenerate(:user, :new_id)", {"user": user_id, "new_id": new_id})
            ).scalar_one()
        )

    async def permanently_delete(self, user_id: UUID) -> str:
        return str((await self._run("SELECT app_user_permanently_delete(:user)", {"user": user_id})).scalar_one())

    async def notify(self, user_ids: list[UUID], kind: str, subject: UUID, title: str) -> None:
        for user_id in user_ids:
            await self._run(
                """INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, subject_user_id,
                       title)
                   VALUES (:id, :org, :campus, :user, :kind, :actor, :title)""",
                {"id": uuid4(), "user": user_id, "kind": kind, "actor": subject, "title": title[:200]},
            )

    async def active_admins(self) -> list[UUID]:
        rows = await self._run(
            "SELECT id FROM users WHERE organization_id = :org AND campus_id = :campus AND role = 'ADMIN' "
            "AND status = 'ACTIVE'"
        )
        return [r.id for r in rows]
