"""Staff Management storage (migration 0018). Every query is limited to the caller's organization and
campus, and Row-Level Security applies the same rule again. Passwords are never read or written
directly: ``app_staff_actor_lookup`` and ``app_staff_set_guard_password`` act for the signed-in caller."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.staff import GuardCategory, StaffMember
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict
from src.domain.repositories.i_staff_account_repo import StepUpAccount
from src.domain.repositories.i_staff_repo import StaffFilter
from src.domain.value_objects.enums import AccountStatus, UserRole
from src.domain.value_objects.location_path import LocationPath


def _sql(statement: str, *uuid_params: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(name, type_=Uuid) for name in uuid_params))


_SCOPE = "u.organization_id = :org AND u.campus_id = :campus"
_SELECT = """SELECT u.id, u.display_id, u.role, u.status, u.full_name, u.username, u.email, u.phone, u.created_at,
                    b.id AS building_id, b.name AS building_name, b.kind AS building_kind,
                    f.id AS floor_id, f.name AS floor_name, ar.id AS area_id, ar.name AS area_name
               FROM users u
               LEFT JOIN guard_location_assignments x ON x.guard_user_id = u.id
               LEFT JOIN areas ar ON ar.id = x.area_id
               LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, ar.floor_id)
               LEFT JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, ar.building_id)"""
_STAFF_ROLES = "u.role IN ('GUARD', 'SUPERVISOR')"

_ACTOR_LOOKUP = text("SELECT user_id, password_hash, locked_until FROM app_staff_actor_lookup()")
_RECORD_FAILURE = _sql("SELECT app_admin_login_record_failure(:user_id, :max_attempts, :window_seconds)", "user_id")
_SET_PASSWORD = _sql("SELECT app_staff_set_guard_password(:guard, :password_hash)", "guard")


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _member(row: Any) -> StaffMember:
    location = (
        None
        if row.building_id is None
        else LocationPath(
            row.building_id,
            row.building_name,
            row.building_kind,
            row.floor_id,
            row.floor_name,
            row.area_id,
            row.area_name,
        )
    )
    return StaffMember(
        user_id=row.id,
        display_id=row.display_id,
        role=UserRole(row.role),
        status=AccountStatus(row.status),
        full_name=row.full_name,
        username=row.username,
        email=row.email,
        phone=row.phone,
        created_at=row.created_at,
        location=location,
    )


class SqlStaffRepository:
    def __init__(self, session: AsyncSession, organization_id: UUID, campus_id: UUID) -> None:
        self._session = session
        self._scope = {"org": organization_id, "campus": campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None, *uuids: str) -> Any:
        return await self._session.execute(_sql(statement, "org", "campus", *uuids), {**self._scope, **(params or {})})

    # --- Reading -------------------------------------------------------------------------------

    async def list_staff(self, where: StaffFilter) -> list[StaffMember]:
        clauses = [_SCOPE, "u.role = :role"]
        params: dict[str, Any] = {"role": where.role.value, "limit": where.limit, "offset": where.offset}
        if where.status is not None:
            clauses.append("u.status = :status")
            params["status"] = where.status.value
        if where.category is GuardCategory.STANDBY:
            clauses.append("x.guard_user_id IS NULL")
        elif where.category is GuardCategory.STATIONED:
            clauses.append("x.guard_user_id IS NOT NULL")
        if where.q:
            clauses.append("(u.full_name ILIKE :q OR u.display_id ILIKE :q OR u.username ILIKE :q OR u.email ILIKE :q)")
            params["q"] = _like(where.q)
        rows = await self._run(
            f"{_SELECT} WHERE {' AND '.join(clauses)} "
            "ORDER BY (u.status = 'ACTIVE') DESC, lower(u.full_name), u.id LIMIT :limit OFFSET :offset",
            params,
        )
        return [_member(r) for r in rows]

    async def get_staff(self, user_id: UUID) -> StaffMember | None:
        row = (
            await self._run(f"{_SELECT} WHERE {_SCOPE} AND {_STAFF_ROLES} AND u.id = :id", {"id": user_id}, "id")
        ).first()
        return None if row is None else _member(row)

    # --- Changes -------------------------------------------------------------------------------

    async def update_guard_details(self, user_id: UUID, full_name: str, username: str, phone: str) -> bool:
        try:
            result = await self._run(
                "UPDATE users u SET full_name = :full_name, username = :username, phone = :phone "
                f"WHERE {_SCOPE} AND u.id = :id AND u.role = 'GUARD' AND u.status = 'ACTIVE' RETURNING u.id",
                {"id": user_id, "full_name": full_name, "username": username, "phone": phone},
                "id",
            )
        except IntegrityError as error:
            if "uq_users_username_ci" in str(error.orig):
                raise Conflict("This username is already taken.", code="USERNAME_TAKEN") from error
            raise
        return result.first() is not None

    async def set_guard_password(self, user_id: UUID, password_hash: str) -> bool:
        outcome = (
            await self._session.execute(_SET_PASSWORD, {"guard": user_id, "password_hash": password_hash})
        ).scalar_one()
        return outcome == "UPDATED"

    async def set_status(self, user_id: UUID, status: AccountStatus) -> bool:
        result = await self._run(
            f"UPDATE users u SET status = :status WHERE {_SCOPE} AND u.id = :id AND {_STAFF_ROLES} "
            "AND u.status <> :status RETURNING u.id",
            {"id": user_id, "status": status.value},
            "id",
        )
        return result.first() is not None

    async def clear_guard_location(self, user_id: UUID) -> bool:
        result = await self._run(
            "DELETE FROM guard_location_assignments WHERE organization_id = :org AND campus_id = :campus "
            "AND guard_user_id = :id RETURNING guard_user_id",
            {"id": user_id},
            "id",
        )
        return result.first() is not None

    # --- The acting user's password ---------------------------------------------------------------

    async def actor_account(self) -> StepUpAccount | None:
        rows = (await self._session.execute(_ACTOR_LOOKUP)).all()
        if len(rows) != 1:
            return None
        return StepUpAccount(
            user_id=rows[0].user_id, password_hash=rows[0].password_hash, locked_until=rows[0].locked_until
        )

    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None:
        params = {"user_id": user_id, "max_attempts": max_attempts, "window_seconds": window_seconds}
        locked: datetime | None = (await self._session.execute(_RECORD_FAILURE, params)).scalar_one()
        return locked

    # --- Audit and notifications --------------------------------------------------------------------

    async def audit(self, actor: User, action: str, target_id: UUID, metadata: dict[str, Any]) -> None:
        await self._run(
            "INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action, "
            "target_type, target_id, metadata) VALUES (gen_random_uuid(), :org, :campus, :actor, :role, :action, "
            "'USER', :target, CAST(:metadata AS jsonb))",
            {
                "actor": actor.id,
                "role": actor.role.value,
                "action": action,
                "target": target_id,
                "metadata": json.dumps(metadata),
            },
            "actor",
            "target",
        )

    async def notify_admins(self, kind: str, subject_user_id: UUID, title: str) -> None:
        await self._run(
            "INSERT INTO user_notifications (id, organization_id, campus_id, user_id, kind, subject_user_id, title) "
            "SELECT gen_random_uuid(), u.organization_id, u.campus_id, u.id, :kind, :subject, :title FROM users u "
            f"WHERE {_SCOPE} AND u.role = 'ADMIN' AND u.status = 'ACTIVE'",
            {"kind": kind, "subject": subject_user_id, "title": title[:200]},
            "subject",
        )
