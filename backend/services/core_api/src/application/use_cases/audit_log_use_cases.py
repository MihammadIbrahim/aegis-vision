"""The audit-log viewer (requirements Section 18; Org Admin page 21, Supervisor page 17; decision D-024).

Admins see every record of their campus; Supervisors see the operational categories and their own
records (Row-Level Security does the filtering, so a forged filter cannot widen it). Guards and the
Super Admin have no access. Records cannot be changed or deleted by anyone. Exporting is itself audited.
"""

from __future__ import annotations

from dataclasses import replace

from src.domain.entities.audit_log import OPERATIONAL_CATEGORIES, AuditCategory, AuditPage, AuditRecord
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import PermissionDenied, ValidationFailed
from src.domain.repositories.i_audit_log_repo import AuditFilter, IAuditLogReader, IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

AUDIT_LOGS_EXPORTED = "AUDIT_LOGS_EXPORTED"
MAX_EXPORT_ROWS = 50_000


def visible_categories(actor: User) -> list[AuditCategory]:
    if actor.role is UserRole.ADMIN:
        return list(AuditCategory)
    if actor.role is UserRole.SUPERVISOR:
        # AI_ASSISTANT (decision D-027): a Supervisor filters their own chatbot records; RLS shows no one else's.
        return [c for c in AuditCategory if c in OPERATIONAL_CATEGORIES or c is AuditCategory.AI_ASSISTANT]
    return []


class ViewAuditLogs:
    def __init__(self, reader: IAuditLogReader, audit: IOrganizationAuditLog, transaction: ITransaction) -> None:
        self._reader = reader
        self._audit = audit
        self._transaction = transaction

    @staticmethod
    def _check(actor: User, where: AuditFilter) -> AuditFilter:
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            raise PermissionDenied("Only Admins and Supervisors can view audit logs.", code="STAFF_ONLY")
        if where.since is not None and where.until is not None and where.since > where.until:
            raise ValidationFailed("The start of the period must be before its end.", code="INVALID_PERIOD")
        return where

    async def page(self, actor: User, where: AuditFilter) -> AuditPage:
        return await self._reader.page(self._check(actor, where))

    async def export(self, actor: User, where: AuditFilter) -> list[AuditRecord]:
        checked = replace(self._check(actor, where), offset=0)
        rows = await self._reader.export_rows(checked, MAX_EXPORT_ROWS)
        await self._audit.record(
            actor,
            AUDIT_LOGS_EXPORTED,
            "AUDIT_LOG",
            None,
            {
                "rows": len(rows),
                "categories": list(checked.categories or []),
                "since": checked.since.isoformat() if checked.since else None,
                "until": checked.until.isoformat() if checked.until else None,
            },
        )
        await self._transaction.commit()
        return rows
