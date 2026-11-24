"""Organization audit records (migration 0015, readable since 0024). The database accepts a new row only for
the signed-in user's own campus, role and account; nobody can change or delete a row. Admins read their
campus's records, Supervisors the operational ones and their own (Row-Level Security)."""

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.audit_log import AuditCategory, AuditOutcome, AuditPage, AuditRecord
from src.domain.entities.user import User
from src.domain.repositories.i_audit_log_repo import AuditFilter

_INSERT = text(
    "INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action, "
    "target_type, target_id, metadata, outcome) VALUES (:id, :org, :campus, :actor, :role, :action, :target_type, "
    ":target, CAST(:metadata AS jsonb), :outcome)"
).bindparams(*(bindparam(n, type_=Uuid) for n in ("id", "org", "campus", "actor", "target")))


_AUTH_EVENT = text(
    "SELECT app_org_audit_auth_event(:user_id, :action, :outcome, CAST(:metadata AS jsonb))"
).bindparams(bindparam("user_id", type_=Uuid))
_RESET_COMPLETED = text("SELECT app_org_audit_reset_completed(:request_id)").bindparams(
    bindparam("request_id", type_=Uuid)
)


class SqlAuthEventLog:
    """Sign-in and password-reset events, written before anyone is signed in (migration 0024)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def auth_event(self, user_id: UUID, action: str, outcome: str, metadata: dict[str, object]) -> None:
        params = {"user_id": user_id, "action": action, "outcome": outcome, "metadata": json.dumps(metadata)}
        await self._session.execute(_AUTH_EVENT, params)

    async def reset_completed(self, request_id: UUID) -> None:
        await self._session.execute(_RESET_COMPLETED, {"request_id": request_id})


class SqlOrganizationAuditLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        actor: User,
        action: str,
        target_type: str,
        target_id: UUID | None,
        metadata: dict[str, object],
        *,
        outcome: str = "SUCCESS",
    ) -> None:
        await self._session.execute(
            _INSERT,
            {
                "id": uuid4(),
                "org": actor.organization_id,
                "campus": actor.campus_id,
                "actor": actor.id,
                "role": actor.role.value,
                "action": action,
                "target_type": target_type,
                "target": target_id,
                "metadata": json.dumps(metadata, default=str),
                "outcome": outcome,
            },
        )


# --- Reading (migration 0024) ------------------------------------------------------------------


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_READ_COLUMNS = """l.id, l.occurred_at, l.category, l.action, l.outcome, l.actor_user_id, l.actor_role,
       l.target_type, l.target_id, l.metadata,
       au.display_id AS actor_display_id, au.full_name AS actor_name,
       CASE l.target_type
           WHEN 'USER' THEN (SELECT tu.display_id || ' · ' || tu.full_name FROM users tu WHERE tu.id = l.target_id)
           WHEN 'CAMERA' THEN (SELECT c.display_name FROM cameras c WHERE c.id = l.target_id)
           ELSE NULL END AS target_label"""
_READ_FROM = "FROM organization_audit_logs l LEFT JOIN users au ON au.id = l.actor_user_id"


def _where(where: AuditFilter) -> tuple[str, dict[str, Any]]:
    clauses = ["true"]
    params: dict[str, Any] = {}
    if where.categories:
        clauses.append("l.category = ANY (:categories)")
        params["categories"] = list(where.categories)
    if where.actor_role:
        clauses.append("l.actor_role = :actor_role")
        params["actor_role"] = where.actor_role
    if where.actor:
        clauses.append("(au.display_id = :actor OR l.actor_user_id::text = :actor)")
        params["actor"] = where.actor.strip()
    if where.action:
        clauses.append("l.action = :action")
        params["action"] = where.action
    if where.outcome:
        clauses.append("l.outcome = :outcome")
        params["outcome"] = where.outcome
    if where.since is not None:
        clauses.append("l.occurred_at >= :since")
        params["since"] = where.since
    if where.until is not None:
        clauses.append("l.occurred_at <= :until")
        params["until"] = where.until
    if where.text:
        clauses.append(
            "(l.action ILIKE :q ESCAPE '\\' OR l.target_id::text = :exact OR au.display_id = :exact "
            "OR au.full_name ILIKE :q ESCAPE '\\' OR l.metadata::text ILIKE :q ESCAPE '\\')"
        )
        params["q"] = _like(where.text.strip())
        params["exact"] = where.text.strip()
    return " AND ".join(clauses), params


def _record(r: Any) -> AuditRecord:
    return AuditRecord(
        id=r.id,
        occurred_at=r.occurred_at,
        category=AuditCategory(r.category) if r.category in AuditCategory.__members__ else AuditCategory.OTHER,
        action=r.action,
        outcome=AuditOutcome(r.outcome),
        actor_user_id=r.actor_user_id,
        actor_display_id=r.actor_display_id,
        actor_name=r.actor_name,
        actor_role=r.actor_role,
        target_type=r.target_type,
        target_id=r.target_id,
        target_label=r.target_label,
        metadata=dict(r.metadata or {}),
    )


class SqlAuditLogReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, where: AuditFilter) -> AuditPage:
        clause, params = _where(where)
        total = (await self._session.execute(text(f"SELECT count(*) {_READ_FROM} WHERE {clause}"), params)).scalar_one()
        rows = await self._session.execute(
            text(
                f"SELECT {_READ_COLUMNS} {_READ_FROM} WHERE {clause} "
                "ORDER BY l.occurred_at DESC, l.id LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": where.limit, "offset": where.offset},
        )
        return AuditPage([_record(r) for r in rows], int(total))

    async def export_rows(self, where: AuditFilter, max_rows: int) -> list[AuditRecord]:
        clause, params = _where(where)
        rows = await self._session.execute(
            text(f"SELECT {_READ_COLUMNS} {_READ_FROM} WHERE {clause} ORDER BY l.occurred_at DESC, l.id LIMIT :limit"),
            {**params, "limit": max_rows},
        )
        return [_record(r) for r in rows]
