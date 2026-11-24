"""Platform Super Admin management (decision D-019): overview, organizations, Main Admin contact,
organization status, platform AI settings and the platform audit log.

Sensitive changes (Main Admin contact, organization status, AI settings) need the Super Admin's current
database password; a wrong one counts toward the sign-in lock and is audited. Every change and every
export is written to ``platform_audit_logs`` without passwords, codes or tokens. Nothing here reads
footage, stream addresses, alerts, chat, reports or personnel: the platform role cannot.
"""

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import replace
from typing import Any
from uuid import UUID

from src.application.use_cases.platform_admin_use_cases import (
    RequestContext,
    _CredentialChange,
)
from src.domain.entities.ai_configuration import CORE_MODULES, FEATURE_NAMES, MODULE_LABELS, AiModule, module_key
from src.domain.entities.platform_management import (
    AI_MODULES,
    AI_PARAMETERS,
    AiSettings,
    AuditLogEntry,
    AuditLogQuery,
    CampusBreakdown,
    OrganizationAiPermission,
    OrganizationListQuery,
    OrganizationStatus,
    OrganizationSummary,
    PlatformCounts,
)
from src.domain.exceptions.domain_errors import Conflict, NotFound
from src.domain.services.registration_policy import (
    check_email,
    check_full_name,
    check_phone,
)

EXPORT_LIMIT = 10_000

# Audit actions written by this module.
ORGANIZATION_ADMIN_UPDATED = "ORGANIZATION_ADMIN_UPDATED"
ORGANIZATION_STATUS_CHANGED = "ORGANIZATION_STATUS_CHANGED"
ORGANIZATION_AI_PERMISSIONS_UPDATED = "ORGANIZATION_AI_PERMISSIONS_UPDATED"
AI_SETTINGS_UPDATED = "AI_SETTINGS_UPDATED"
ORGANIZATIONS_EXPORTED = "ORGANIZATIONS_EXPORTED"
AUDIT_LOGS_EXPORTED = "AUDIT_LOGS_EXPORTED"


def _missing() -> NotFound:
    return NotFound("Organization not found.", code="ORGANIZATION_NOT_FOUND")


def _cell(value: Any) -> str:
    """CSV cell safe to open in a spreadsheet: formulas are neutralised."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return out.getvalue()


class _Sensitive(_CredentialChange):
    """A platform change confirmed with the Super Admin's current password (lock and audit included)."""

    def __init__(self, repository: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._repo = repository


# --- Read-only ----------------------------------------------------------------------------------------


class GetPlatformCounts:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self) -> PlatformCounts:
        counts: PlatformCounts = await self._repo.counts()
        return counts


class ListOrganizations:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self, query: OrganizationListQuery) -> tuple[list[OrganizationSummary], int]:
        result: tuple[list[OrganizationSummary], int] = await self._repo.list_organizations(query)
        return result


class GetOrganizationDetails:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self, code: str) -> tuple[OrganizationSummary, list[CampusBreakdown]]:
        organization = await self._repo.organization(code.strip())
        if organization is None:
            raise _missing()
        return organization, await self._repo.campuses(organization.organization_code)


class ExportOrganizations(_Sensitive):
    """CSV of the organization directory (same filters as the list, up to 10 000 rows). Audited."""

    async def run(self, admin_id: UUID, query: OrganizationListQuery, context: RequestContext) -> str:
        items, total = await self._repo.list_organizations(replace(query, limit=EXPORT_LIMIT, offset=0))
        await self._audit(
            ORGANIZATIONS_EXPORTED,
            "SUCCESS",
            context,
            admin_id,
            {"rows": len(items), "total": total, "filters": {"q": query.q, "status": query.status}},
        )
        header = [
            "organization_id", "name", "official_email", "official_phone", "status", "created_at",
            "main_admin_full_name", "main_admin_username", "main_admin_email", "main_admin_phone_number",
            "campus_count", "camera_count",
        ]  # fmt: skip
        return to_csv(
            header,
            (
                [
                    o.organization_code,
                    o.name,
                    o.official_email,
                    o.official_phone,
                    o.status.value,
                    o.created_at.isoformat(),
                    *(
                        [o.main_admin.full_name, o.main_admin.username, o.main_admin.email, o.main_admin.phone]
                        if o.main_admin
                        else [None] * 4
                    ),
                    o.campus_count,
                    o.camera_count,
                ]  # fmt: skip
                for o in items
            ),
        )


# --- Changes confirmed with the current password ------------------------------------------------------


class UpdateMainAdminContact(_Sensitive):
    """The Main Admin's full name, email and phone. The Admin's password is never read or shown."""

    async def run(
        self,
        admin_id: UUID,
        code: str,
        *,
        full_name: str,
        email: str,
        phone: str,
        current_password: str,
        context: RequestContext,
    ) -> OrganizationSummary:
        name, address, number = check_full_name(full_name), check_email(email), check_phone(phone)
        organization = await self._repo.organization(code.strip())
        if organization is None:
            raise _missing()
        if organization.main_admin is None:
            raise NotFound("This organization has no Main Admin.", code="MAIN_ADMIN_NOT_FOUND")
        await self._verify_current_password(admin_id, current_password, ORGANIZATION_ADMIN_UPDATED, context)
        before = organization.main_admin
        changed = [
            f
            for f, old, new in (
                ("full_name", before.full_name, name),
                ("email", before.email, address),
                ("phone_number", before.phone, number),
            )
            if old != new
        ]
        if changed:
            outcome = await self._repo.update_main_admin(organization.organization_code, name, address, number)
            if outcome == "EMAIL_TAKEN":
                raise Conflict("This email address is already registered.", code="EMAIL_ALREADY_REGISTERED")
            if outcome != "UPDATED":
                raise NotFound("This organization has no Main Admin.", code="MAIN_ADMIN_NOT_FOUND")
        await self._audit(
            ORGANIZATION_ADMIN_UPDATED,
            "SUCCESS",
            context,
            admin_id,
            {
                "organization_code": organization.organization_code,
                "organization_name": organization.name,
                "fields": changed,
            },
        )
        updated = await self._repo.organization(organization.organization_code)
        assert updated is not None
        return updated


class ChangeOrganizationStatus(_Sensitive):
    """ACTIVE, INACTIVE or SUSPENDED. A non-active organization's users cannot sign in, and their open
    sessions are refused on the next request (403 ORGANIZATION_INACTIVE)."""

    async def run(
        self,
        admin_id: UUID,
        code: str,
        *,
        status: OrganizationStatus,
        reason: str | None,
        current_password: str,
        context: RequestContext,
    ) -> OrganizationSummary:
        organization = await self._repo.organization(code.strip())
        if organization is None:
            raise _missing()
        await self._verify_current_password(admin_id, current_password, ORGANIZATION_STATUS_CHANGED, context)
        outcome, previous, name = await self._repo.set_status(organization.organization_code, status)
        if outcome == "NOT_FOUND":
            raise _missing()
        if outcome == "UNCHANGED":
            raise Conflict(f"The organization is already {status.value}.", code="STATUS_UNCHANGED")
        await self._audit(
            ORGANIZATION_STATUS_CHANGED,
            "SUCCESS",
            context,
            admin_id,
            {
                "organization_code": organization.organization_code,
                "organization_name": name,
                "from_status": previous,
                "to_status": status.value,
                "reason": (reason or "").strip() or None,
            },
        )
        updated = await self._repo.organization(organization.organization_code)
        assert updated is not None
        return updated


# --- Per-organization AI feature access (decision D-028) ---------------------------------------------------


async def _ai_permissions(repo: Any, code: str) -> list[OrganizationAiPermission] | None:
    blocks = await repo.organization_ai_blocks(code)
    if blocks is None:
        return None
    platform = (await repo.ai_settings()).values()
    return [
        OrganizationAiPermission(
            module_key=m.value,
            feature=FEATURE_NAMES[m],
            label=MODULE_LABELS[m],
            core=m in CORE_MODULES,
            blocked=blocks[m.value],
            platform_enabled=bool(platform[f"{FEATURE_NAMES[m]}_enabled"]),
        )
        for m in AiModule
    ]


class GetOrganizationAiPermissions:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self, code: str) -> list[OrganizationAiPermission]:
        permissions = await _ai_permissions(self._repo, code.strip())
        if permissions is None:
            raise _missing()
        return permissions


class ChangeOrganizationAiPermission(_Sensitive):
    """Blocks or unblocks one vision module for one organization, with the Super Admin's password (SA-SEC-4).

    A blocked module stops on every camera of the organization at once and its Admins and Supervisors cannot switch
    it on; unblocking never switches a camera back on by itself. Audited as ORGANIZATION_AI_PERMISSIONS_UPDATED."""

    async def run(
        self,
        admin_id: UUID,
        code: str,
        *,
        module: str,
        blocked: bool,
        current_password: str,
        context: RequestContext,
    ) -> list[OrganizationAiPermission]:
        key = module_key(module.strip())
        if key is None:
            raise Conflict(f"Unknown AI module: {module}.", code="UNKNOWN_AI_MODULE")
        organization = await self._repo.organization(code.strip())
        if organization is None:
            raise _missing()
        await self._verify_current_password(admin_id, current_password, ORGANIZATION_AI_PERMISSIONS_UPDATED, context)
        code = organization.organization_code
        outcome = await self._repo.set_organization_ai_block(code, key.value, blocked, admin_id)
        if outcome == "NOT_FOUND":
            raise _missing()
        if outcome == "UPDATED":
            await self._audit(
                ORGANIZATION_AI_PERMISSIONS_UPDATED,
                "SUCCESS",
                context,
                admin_id,
                {
                    "organization_code": organization.organization_code,
                    "organization_name": organization.name,
                    "module": key.value,
                    "feature": FEATURE_NAMES[key],
                    "blocked": blocked,
                },
            )
        permissions = await _ai_permissions(self._repo, organization.organization_code)
        assert permissions is not None
        return permissions


class GetAiSettings:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self) -> AiSettings:
        settings: AiSettings = await self._repo.ai_settings()
        return settings


class UpdateAiSettings(_Sensitive):
    """Replaces every AI module switch and default parameter. The audit row lists what changed, with the
    old and new values (they are settings, not secrets)."""

    async def run(
        self, admin_id: UUID, values: dict[str, Any], *, current_password: str, context: RequestContext
    ) -> AiSettings:
        await self._verify_current_password(admin_id, current_password, AI_SETTINGS_UPDATED, context)
        before = (await self._repo.ai_settings()).values()
        changes = [
            {"field": name, "from": _plain(before[name]), "to": _plain(values[name])}
            for name in (*AI_MODULES, *AI_PARAMETERS)
            if _plain(before[name]) != _plain(values[name])
        ]
        if changes:
            await self._repo.save_ai_settings(values, admin_id)
        await self._audit(AI_SETTINGS_UPDATED, "SUCCESS", context, admin_id, {"changes": changes})
        settings: AiSettings = await self._repo.ai_settings()
        return settings


def _plain(value: Any) -> Any:
    return float(value) if not isinstance(value, bool) and not isinstance(value, int) else value


# --- Platform audit log ---------------------------------------------------------------------------------


class ListAuditLogs:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self, query: AuditLogQuery) -> tuple[list[AuditLogEntry], int]:
        result: tuple[list[AuditLogEntry], int] = await self._repo.audit_logs(query)
        return result


class ListAuditActions:
    def __init__(self, repository: Any) -> None:
        self._repo = repository

    async def execute(self) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = await self._repo.audit_actions()
        return result


class ExportAuditLogs(_Sensitive):
    async def run(self, admin_id: UUID, query: AuditLogQuery, context: RequestContext) -> str:
        entries, total = await self._repo.audit_logs(replace(query, limit=EXPORT_LIMIT, offset=0))
        await self._audit(
            AUDIT_LOGS_EXPORTED,
            "SUCCESS",
            context,
            admin_id,
            {
                "rows": len(entries),
                "total": total,
                "filters": {
                    "organization": query.organization,
                    "action": query.action,
                    "outcome": query.outcome,
                    "date_from": None if query.date_from is None else query.date_from.date().isoformat(),
                    "date_to": None if query.date_to is None else query.date_to.date().isoformat(),
                },
            },
        )
        header = [
            "occurred_at", "actor_type", "actor_username", "action", "outcome", "organization_id",
            "organization_name", "ip_address", "user_agent", "details",
        ]  # fmt: skip
        return to_csv(
            header,
            (
                [
                    e.occurred_at.isoformat(),
                    e.actor_type,
                    e.actor_username,
                    e.action,
                    e.outcome,
                    e.organization_code,
                    e.organization_name,
                    e.ip_address,
                    e.user_agent,
                    _details_text(e.details),
                ]  # fmt: skip
                for e in entries
            ),
        )


def _details_text(details: dict[str, Any]) -> str:
    return json.dumps(details, sort_keys=True, default=str)
