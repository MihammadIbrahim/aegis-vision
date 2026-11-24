"""Personnel import batches, their validation issues and mapping profiles (migration 0017).
Admins of the campus only (Row-Level Security)."""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.exceptions.domain_errors import Conflict
from src.domain.repositories.i_personnel_repo import ImportBatch, StoredIssue

_UUIDS = ("id", "org", "campus", "actor")
_SCOPE = "x.organization_id = :org AND x.campus_id = :campus"
_JSON = {"headers", "mapping"}
_BATCH = """SELECT x.*, uu.full_name AS uploaded_by_name, cu.full_name AS confirmed_by_name,
                   ru.full_name AS rolled_back_by_name
              FROM personnel_import_batches x
              LEFT JOIN users uu ON uu.id = x.uploaded_by
              LEFT JOIN users cu ON cu.id = x.confirmed_by
              LEFT JOIN users ru ON ru.id = x.rolled_back_by"""
# Columns the use cases may set (anything else is refused, so column names never come from input).
_UPDATABLE = {
    "status",
    "mapping",
    "total_rows",
    "valid_rows",
    "warning_count",
    "error_count",
    "imported_count",
    "rolled_back_count",
    "failure_reason",
    "has_annotated_workbook",
    "files_purged_at",
    "validated_by",
    "validated_at",
    "confirmed_by",
    "confirmed_at",
    "rolled_back_by",
    "rolled_back_at",
}
_USER_COLUMNS = {"validated_by", "confirmed_by", "rolled_back_by"}


def _batch(row: Any) -> ImportBatch:
    return ImportBatch(
        id=row.id,
        original_filename=row.original_filename,
        file_format=row.file_format,
        file_size=row.file_size,
        file_sha256=row.file_sha256,
        sheet_name=row.sheet_name,
        header_row=row.header_row,
        headers=list(row.headers or []),
        mapping=None if row.mapping is None else list(row.mapping),
        status=row.status,
        total_rows=row.total_rows,
        valid_rows=row.valid_rows,
        warning_count=row.warning_count,
        error_count=row.error_count,
        imported_count=row.imported_count,
        rolled_back_count=row.rolled_back_count,
        failure_reason=row.failure_reason,
        has_annotated_workbook=row.has_annotated_workbook,
        files_purged_at=row.files_purged_at,
        uploaded_by_name=row.uploaded_by_name,
        uploaded_at=row.uploaded_at,
        validated_at=row.validated_at,
        confirmed_by_name=row.confirmed_by_name,
        confirmed_at=row.confirmed_at,
        rolled_back_by_name=row.rolled_back_by_name,
        rolled_back_at=row.rolled_back_at,
    )


class SqlPersonnelImportRepository:
    def __init__(self, session: AsyncSession, organization_id: UUID, campus_id: UUID) -> None:
        self._session = session
        self._scope = {"org": organization_id, "campus": campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None, uuids: Sequence[str] = ()) -> Any:
        merged = {k: v for k, v in {**self._scope, **(params or {})}.items() if re.search(rf":{k}\b", statement)}
        names = [n for n in (*_UUIDS, *uuids) if n in merged]
        return await self._session.execute(
            text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in names)), merged
        )

    async def add_batch(self, batch_id: UUID, values: dict[str, Any], actor_id: UUID) -> None:
        await self._run(
            "INSERT INTO personnel_import_batches (id, organization_id, campus_id, original_filename, file_format, "
            "file_size, file_sha256, sheet_name, header_row, headers, uploaded_by) VALUES (:id, :org, :campus, "
            ":filename, :format, :size, :sha, :sheet, :header_row, CAST(:headers AS jsonb), :actor)",
            {
                "id": batch_id,
                "filename": values["original_filename"],
                "format": values["file_format"],
                "size": values["file_size"],
                "sha": values["file_sha256"],
                "sheet": values["sheet_name"],
                "header_row": values["header_row"],
                "headers": json.dumps(values["headers"]),
                "actor": actor_id,
            },
        )

    async def get_batch(self, batch_id: UUID, *, lock: bool = False) -> ImportBatch | None:
        if lock:
            await self._run(
                f"SELECT 1 FROM personnel_import_batches x WHERE {_SCOPE} AND x.id = :id FOR UPDATE", {"id": batch_id}
            )
        row = (await self._run(f"{_BATCH} WHERE {_SCOPE} AND x.id = :id", {"id": batch_id})).one_or_none()
        return None if row is None else _batch(row)

    async def list_batches(self, limit: int, offset: int) -> list[ImportBatch]:
        rows = await self._run(
            f"{_BATCH} WHERE {_SCOPE} ORDER BY x.uploaded_at DESC LIMIT :limit OFFSET :offset",
            {"limit": limit, "offset": offset},
        )
        return [_batch(r) for r in rows]

    async def update_batch(self, batch_id: UUID, values: dict[str, Any]) -> None:
        unknown = set(values) - _UPDATABLE
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        sets = []
        params: dict[str, Any] = {"id": batch_id}
        for column, value in values.items():
            if column in _JSON:
                sets.append(f"{column} = CAST(:{column} AS jsonb)")
                params[column] = None if value is None else json.dumps(value)
            else:
                sets.append(f"{column} = :{column}")
                params[column] = value
        await self._run(
            f"UPDATE personnel_import_batches x SET {', '.join(sets)}, updated_at = now() "
            f"WHERE {_SCOPE} AND x.id = :id",
            params,
            uuids=tuple(c for c in values if c in _USER_COLUMNS),
        )

    async def replace_issues(self, batch_id: UUID, issues: Sequence[StoredIssue]) -> None:
        await self._run("DELETE FROM personnel_import_issues WHERE batch_id = :id", {"id": batch_id})
        if not issues:
            return
        statement = text(
            "INSERT INTO personnel_import_issues (batch_id, seq, row_number, column_index, column_name, field, "
            "severity, code, message) VALUES (:id, :seq, :row, :column, :name, :field, :severity, :code, :message)"
        ).bindparams(bindparam("id", type_=Uuid))
        rows = [
            {
                "id": batch_id,
                "seq": seq,
                "row": i.row_number,
                "column": i.column_index,
                "name": None if i.column_name is None else i.column_name[:255],
                "field": i.field,
                "severity": i.severity,
                "code": i.code,
                "message": i.message[:500],
            }
            for seq, i in enumerate(issues, start=1)
        ]
        for start in range(0, len(rows), 5000):
            await self._session.execute(statement, rows[start : start + 5000])

    async def issues(self, batch_id: UUID, severity: str | None, limit: int, offset: int) -> list[StoredIssue]:
        rows = await self._run(
            "SELECT row_number, column_index, column_name, field, severity, code, message "
            "FROM personnel_import_issues WHERE batch_id = :id "
            "AND (CAST(:severity AS text) IS NULL OR severity = :severity) "
            "ORDER BY seq LIMIT :limit OFFSET :offset",
            {"id": batch_id, "severity": severity, "limit": limit, "offset": offset},
        )
        return [StoredIssue(*r) for r in rows]

    async def expired_batches(self, older_than: datetime) -> list[ImportBatch]:
        rows = await self._run(
            f"{_BATCH} WHERE {_SCOPE} AND x.files_purged_at IS NULL AND x.uploaded_at < :cutoff",
            {"cutoff": older_than},
        )
        return [_batch(r) for r in rows]

    async def lock_campus_imports(self) -> None:
        """One import confirmation at a time per campus (released at commit or rollback)."""
        await self._run(
            "SELECT pg_advisory_xact_lock(hashtextextended('personnel_import:' || CAST(:campus AS text), 0))"
        )

    async def mapping_profiles(self) -> list[tuple[UUID, str, dict[str, str]]]:
        rows = await self._run(
            f"SELECT x.id, x.name, x.mapping FROM personnel_import_mapping_profiles x WHERE {_SCOPE} "
            "ORDER BY lower(x.name)"
        )
        return [(r.id, r.name, dict(r.mapping)) for r in rows]

    async def save_mapping_profile(self, name: str, mapping: dict[str, str], actor_id: UUID) -> UUID:
        """Creates the profile, or replaces the mapping of the profile with this name."""
        try:
            row = (
                await self._run(
                    "INSERT INTO personnel_import_mapping_profiles (id, organization_id, campus_id, name, mapping, "
                    "created_by) VALUES (:id, :org, :campus, :name, CAST(:mapping AS jsonb), :actor) "
                    "ON CONFLICT (organization_id, campus_id, lower(name)) DO UPDATE SET mapping = EXCLUDED.mapping, "
                    "updated_at = now() RETURNING id",
                    {"id": uuid4(), "name": name, "mapping": json.dumps(mapping), "actor": actor_id},
                )
            ).one()
        except IntegrityError as error:
            raise Conflict("The mapping profile could not be saved.", code="MAPPING_PROFILE_INVALID") from error
        return UUID(str(row.id))

    async def delete_mapping_profile(self, profile_id: UUID) -> bool:
        result = await self._run(
            f"DELETE FROM personnel_import_mapping_profiles x WHERE {_SCOPE} AND x.id = :id RETURNING x.id",
            {"id": profile_id},
        )
        return result.first() is not None
