"""Platform Super Admin management storage (migration 0020). Runs on the **platform** session
(PLATFORM_DATABASE_URL). Tenant data is reached only through the narrow ``platform_*`` functions;
the platform role has no rights on tenant tables."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.platform_management import (
    AI_MODULES,
    AI_PARAMETERS,
    AiSettings,
    AuditLogEntry,
    AuditLogQuery,
    CampusBreakdown,
    MainAdminContact,
    OrganizationListQuery,
    OrganizationStatus,
    OrganizationSummary,
    PlatformCounts,
)

_ORGANIZATION_ADMIN_ACTIONS = ("OWNERSHIP_TRANSFER", "ADMIN_ACCOUNT_DELETED")


def _summary(row: Any) -> OrganizationSummary:
    admin = (
        None
        if row.main_admin_email is None
        else MainAdminContact(
            row.main_admin_full_name, row.main_admin_username, row.main_admin_email, row.main_admin_phone
        )
    )
    return OrganizationSummary(
        organization_code=row.organization_code,
        name=row.name,
        official_email=row.official_email,
        official_phone=row.official_phone,
        status=OrganizationStatus(row.status),
        created_at=row.created_at,
        main_admin=admin,
        campus_count=int(row.campus_count),
        camera_count=int(row.camera_count),
    )


def _actor_type(actor_id: UUID | None, action: str) -> str:
    if actor_id is not None:
        return "SUPER_ADMIN"
    if action.startswith(_ORGANIZATION_ADMIN_ACTIONS):
        return "ORGANIZATION_ADMIN"
    return "SYSTEM"


class SqlPlatformManagementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Overview and organizations -------------------------------------------------------------

    async def counts(self) -> PlatformCounts:
        row = (await self._session.execute(text("SELECT * FROM platform_overview()"))).one()
        deleted = (await self._session.execute(text("SELECT count(*) FROM deleted_organization_names"))).scalar_one()
        return PlatformCounts(**{k: int(v) for k, v in row._mapping.items()}, deleted_organizations_total=int(deleted))

    async def list_organizations(self, query: OrganizationListQuery) -> tuple[list[OrganizationSummary], int]:
        rows = (
            await self._session.execute(
                text("SELECT * FROM platform_organization_list(:q, :status, :sort, :descending, :limit, :offset)"),
                {
                    "q": query.q,
                    "status": None if query.status is None else query.status.value,
                    "sort": query.sort,
                    "descending": query.descending,
                    "limit": query.limit,
                    "offset": query.offset,
                },
            )
        ).all()
        total = int(rows[0].total_count) if rows else 0
        if not rows and query.offset > 0:  # past the end: still report the real total
            first = (
                await self._session.execute(
                    text("SELECT total_count FROM platform_organization_list(:q, :status, 'name', false, 1, 0)"),
                    {"q": query.q, "status": None if query.status is None else query.status.value},
                )
            ).first()
            total = 0 if first is None else int(first.total_count)
        return [_summary(r) for r in rows], total

    async def organization(self, code: str) -> OrganizationSummary | None:
        row = (
            await self._session.execute(text("SELECT * FROM platform_organization_detail(:code)"), {"code": code})
        ).first()
        return None if row is None else _summary(row)

    async def campuses(self, code: str) -> list[CampusBreakdown]:
        rows = await self._session.execute(text("SELECT * FROM platform_organization_campuses(:code)"), {"code": code})
        return [
            CampusBreakdown(
                r.campus_name,
                r.status,
                r.created_at,
                *(
                    int(getattr(r, c))
                    for c in (
                        "building_count",
                        "perimeter_count",
                        "floor_count",
                        "area_count",
                        "camera_count",
                        "admin_count",
                        "supervisor_count",
                        "guard_count",
                    )
                ),  # fmt: skip
            )
            for r in rows
        ]

    async def organization_names(self, codes: list[str]) -> dict[str, str]:
        if not codes:
            return {}
        rows = await self._session.execute(
            text("SELECT organization_code, name FROM platform_organization_names(CAST(:codes AS text[]))"),
            {"codes": codes},
        )
        return {r.organization_code: r.name for r in rows}

    async def update_main_admin(self, code: str, full_name: str, email: str, phone: str) -> str:
        outcome: str = (
            await self._session.execute(
                text("SELECT platform_update_main_admin(:code, :full_name, :email, :phone)"),
                {"code": code, "full_name": full_name, "email": email, "phone": phone},
            )
        ).scalar_one()
        return outcome

    async def set_status(self, code: str, status: OrganizationStatus) -> tuple[str, str | None, str | None]:
        row = (
            await self._session.execute(
                text("SELECT * FROM platform_set_organization_status(:code, :status)"),
                {"code": code, "status": status.value},
            )
        ).one()
        return row.outcome, row.previous_status, row.organization_name

    # --- AI settings ---------------------------------------------------------------------------------

    async def organization_ai_blocks(self, code: str) -> dict[str, bool] | None:
        """Module key → blocked for one organization (decision D-028); None for an unknown code."""
        row = (
            await self._session.execute(
                text("SELECT * FROM platform_organization_ai_permissions_get(:code)"), {"code": code}
            )
        ).first()
        if row is None:
            return None
        keys = ("human_detection", "person_reid", "weapon_detection", "fight_detection", "face_recognition")
        return {key: bool(getattr(row, key)) for key in keys}

    async def set_organization_ai_block(self, code: str, module: str, blocked: bool, admin_id: UUID) -> str:
        result = await self._session.execute(
            text("SELECT platform_organization_ai_permissions_set(:code, :module, :blocked, :admin)"),
            {"code": code, "module": module, "blocked": blocked, "admin": admin_id},
        )
        return str(result.scalar_one())

    async def ai_settings(self) -> AiSettings:
        row = (
            await self._session.execute(
                text(
                    "SELECT s.*, a.username AS updated_by_username FROM platform_ai_settings s "
                    "LEFT JOIN platform_admins a ON a.id = s.updated_by_platform_admin_id WHERE s.id = 1"
                )
            )
        ).one()
        values = {name: getattr(row, name) for name in (*AI_MODULES, *AI_PARAMETERS)}
        return AiSettings(**values, updated_at=row.updated_at, updated_by_username=row.updated_by_username)

    async def save_ai_settings(self, values: dict[str, Any], admin_id: UUID) -> None:
        columns = [name for name in (*AI_MODULES, *AI_PARAMETERS) if name in values]
        assignments = ", ".join(f"{name} = :{name}" for name in columns)
        statement = text(
            f"UPDATE platform_ai_settings SET {assignments}, updated_at = now(), "
            "updated_by_platform_admin_id = :admin WHERE id = 1"
        ).bindparams(bindparam("admin", type_=Uuid))
        params: dict[str, Any] = {name: values[name] for name in columns}
        for name in ("default_confidence_threshold",):
            if name in params:
                params[name] = Decimal(str(params[name]))
        await self._session.execute(statement, {**params, "admin": admin_id})

    # --- Audit log -------------------------------------------------------------------------------------

    async def _organization_codes_named(self, part: str) -> list[str]:
        rows = await self._session.execute(
            text("SELECT organization_code FROM platform_organization_list(:q, NULL, 'name', false, 10000, 0)"),
            {"q": part},
        )
        return [r.organization_code for r in rows]

    async def _where(self, query: AuditLogQuery) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if query.organization:
            params["org"] = query.organization
            params["org_like"] = f"%{query.organization.replace('%', '').replace('_', '')}%"
            params["codes"] = await self._organization_codes_named(query.organization)
            clauses.append(
                "(l.metadata ->> 'organization_code' = :org OR l.metadata ->> 'organization_name' ILIKE :org_like "
                "OR l.metadata ->> 'organization_code' = ANY (CAST(:codes AS text[])))"
            )
        if query.action:
            clauses.append("l.action = :action")
            params["action"] = query.action
        if query.outcome:
            clauses.append("l.outcome = :outcome")
            params["outcome"] = query.outcome
        if query.date_from:
            clauses.append("l.occurred_at >= :date_from")
            params["date_from"] = query.date_from
        if query.date_to:
            clauses.append("l.occurred_at < :date_to")
            params["date_to"] = query.date_to
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    async def audit_logs(self, query: AuditLogQuery) -> tuple[list[AuditLogEntry], int]:
        where, params = await self._where(query)
        total = (
            await self._session.execute(text(f"SELECT count(*) FROM platform_audit_logs l{where}"), params)
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    "SELECT l.id, l.occurred_at, l.actor_platform_admin_id, a.username, l.action, l.outcome, "
                    "l.metadata, host(l.ip_address) AS ip_address, l.user_agent FROM platform_audit_logs l "
                    f"LEFT JOIN platform_admins a ON a.id = l.actor_platform_admin_id{where} "
                    "ORDER BY l.occurred_at DESC, l.id LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": query.limit, "offset": query.offset},
            )
        ).all()
        codes = sorted({str(c) for r in rows if (c := (r.metadata or {}).get("organization_code"))})
        names = await self.organization_names(codes)
        entries = []
        for r in rows:
            meta: dict[str, Any] = r.metadata if isinstance(r.metadata, dict) else json.loads(r.metadata or "{}")
            code = meta.get("organization_code")
            code = None if code is None else str(code)
            entries.append(
                AuditLogEntry(
                    id=r.id,
                    occurred_at=r.occurred_at,
                    actor_type=_actor_type(r.actor_platform_admin_id, r.action),
                    actor_username=r.username,
                    action=r.action,
                    outcome=r.outcome,
                    organization_code=code,
                    organization_name=(names.get(code) if code else None) or meta.get("organization_name"),
                    ip_address=r.ip_address,
                    user_agent=r.user_agent,
                    details=meta,
                )
            )
        return entries, int(total)

    async def audit_actions(self) -> list[tuple[str, int]]:
        rows = await self._session.execute(
            text("SELECT action, count(*) AS n FROM platform_audit_logs GROUP BY action ORDER BY action")
        )
        return [(r.action, int(r.n)) for r in rows]

    # --- Health -----------------------------------------------------------------------------------------

    async def database_facts(self) -> dict[str, Any]:
        row = (
            await self._session.execute(
                text(
                    "SELECT current_setting('server_version') AS version, "
                    "pg_database_size(current_database()) AS size_bytes, "
                    "(SELECT array_agg(extname ORDER BY extname) FROM pg_extension) AS extensions"
                )
            )
        ).one()
        return {"version": row.version, "size_bytes": int(row.size_bytes), "extensions": list(row.extensions or [])}

    async def ping(self) -> None:
        await self._session.execute(text("SELECT 1"))
