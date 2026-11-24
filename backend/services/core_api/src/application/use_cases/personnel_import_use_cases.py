"""Excel/CSV personnel import (decision D-016). Organization Admins only, inside their own campus.

Workflow (all or nothing):
1. **Upload** (``UploadPersonnelImport``): the file is streamed to the campus folder (never held in
   memory as a whole), checked (type, macros, unpacked size) and its header row read. Status UPLOADED.
2. **Map** columns and **validate** (``ValidatePersonnelImport``): the Admin confirms a decision for
   every column; every row is checked. Issues are stored and, if there are any, an annotated copy
   of the file is written. Status VALIDATED (no blocking errors) or INVALID.
3. If INVALID: nothing can be imported. The Admin downloads the annotated workbook, fixes the
   file and uploads it again (a new batch). The mapping can also be corrected and validated again.
4. **Confirm** (``ConfirmPersonnelImport``): only a VALIDATED batch, only with warnings acknowledged.
   The file is checked against its recorded SHA-256, validated again while inserting, and every
   record is written in **one database transaction**. One confirmation runs at a time per campus.
   Any blocking problem (for example a duplicate added since validation) rolls the whole
   transaction back; the batch is then validated again so the Admin sees what changed.
5. **Roll back** (``RollBackPersonnelImport``): an IMPORTED batch's records are deactivated (never
   deleted) and their restricted-area access revoked; history and audit stay.

There is no row limit. Operational limits (upload size, unpacked size, file retention) are
settings; an upload over a limit is refused as a whole with a clear error.
"""

import asyncio
import hashlib
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.application.services.personnel_import.annotator import write_annotated_workbook
from src.application.services.personnel_import.mapping import (
    ColumnMapping,
    Confidence,
    Suggestion,
    available_targets,
    confirm_mapping,
    suggest,
)
from src.application.services.personnel_import.reader import (
    SheetReader,
    SpreadsheetError,
    detect_format,
)
from src.application.services.personnel_import.template import build_template
from src.application.services.personnel_import.validator import (
    ImportBlocked,
    ImportValidation,
    LocationIndex,
)
from src.application.use_cases.personnel_use_cases import (
    DEACTIVATED_GRANT_REASON,
    campus_rules,
)
from src.domain.entities.personnel import (
    CreatedVia,
    PersonnelValues,
    generate_personnel_code,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    Gone,
    NotFound,
    OperationFailed,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_location_repo import ILocationRepository
from src.domain.repositories.i_personnel_repo import (
    IAreaAccessRepository,
    ImportBatch,
    IPersonnelImportRepository,
    IPersonnelRepository,
    NewPersonnel,
    StoredIssue,
)
from src.domain.repositories.i_unit_of_work import IActorBinding, ITransaction
from src.domain.services.permission_policy import ensure_personnel_manager
from src.domain.services.personnel_rules import PersonnelRules

UPLOADED, INVALID, VALIDATED = "UPLOADED", "INVALID", "VALIDATED"
IMPORTED, ROLLED_BACK, DISCARDED = "IMPORTED", "ROLLED_BACK", "DISCARDED"
_EDITABLE = {UPLOADED, INVALID, VALIDATED}
_UPLOAD_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class ImportLimits:
    max_upload_bytes: int
    max_uncompressed_bytes: int
    file_retention_hours: int
    chunk_rows: int


@dataclass(frozen=True, slots=True)
class ImportView:
    batch: ImportBatch
    sheets: list[str]
    suggestions: list[Suggestion]
    targets: dict[str, str]


@dataclass(frozen=True, slots=True)
class PreviewRow:
    row_number: int
    values: PersonnelValues
    category_label: str
    location: dict[str, str | None]


def _missing() -> NotFound:
    return NotFound("Import not found.", code="IMPORT_NOT_FOUND")


def _folder(batch_id: UUID) -> str:
    return f"personnel_imports/{batch_id}"


def original_path(batch: ImportBatch) -> str:
    return f"{_folder(batch.id)}/original.{batch.file_format.lower()}"


def annotated_path(batch_id: UUID) -> str:
    return f"{_folder(batch_id)}/annotated.xlsx"


def _clean_filename(name: str | None) -> str:
    base = Path(name or "").name.strip()
    base = "".join(c for c in base if ord(c) >= 32 and c not in '\\/:*?"<>|')
    return base[-255:] or "upload"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_UPLOAD_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


class _ImportBase:
    def __init__(
        self,
        imports: IPersonnelImportRepository,
        personnel: IPersonnelRepository,
        locations: ILocationRepository,
        storage: ICampusMediaStorage,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        limits: ImportLimits,
        *,
        access: IAreaAccessRepository | None = None,
        binding: IActorBinding | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._imports = imports
        self._personnel = personnel
        self._locations = locations
        self._storage = storage
        self._audit = audit
        self._transaction = transaction
        self._limits = limits
        self._access = access
        self._binding = binding
        self._clock = clock

    def _path(self, actor: User, relative: str) -> Path:
        return self._storage.local_path(actor.organization_id, actor.campus_id, relative)

    def _remove_files(self, actor: User, batch_id: UUID) -> None:
        shutil.rmtree(self._path(actor, f"{_folder(batch_id)}/original.upload").parent, ignore_errors=True)

    async def _batch(self, batch_id: UUID, *, lock: bool = False) -> ImportBatch:
        batch = await self._imports.get_batch(batch_id, lock=lock)
        if batch is None:
            raise _missing()
        return batch

    def _reader(self, actor: User, batch: ImportBatch) -> SheetReader:
        if batch.files_purged_at is not None:
            raise Gone("The uploaded file has expired. Upload it again.", code="IMPORT_FILE_EXPIRED")
        path = self._path(actor, original_path(batch))
        if not path.is_file():
            raise Gone("The uploaded file is no longer available. Upload it again.", code="IMPORT_FILE_EXPIRED")
        try:
            return SheetReader(path, batch.file_format, sheet_name=batch.sheet_name, header_row=batch.header_row)
        except SpreadsheetError as error:
            raise ValidationFailed(str(error), code=error.code) from error

    async def _view(self, batch: ImportBatch) -> ImportView:
        custom = await self._personnel.custom_fields()
        targets = available_targets(custom)
        if batch.mapping is not None:
            suggestions = [Suggestion(t, Confidence.PROFILE) for t in batch.mapping]
        else:
            profiles = await self._imports.mapping_profiles()
            merged: dict[str, str] = {}
            for _, _, mapping in profiles:
                merged.update(mapping)
            suggestions = suggest(batch.headers, custom, merged)
        sheets: list[str] = []
        return ImportView(batch, sheets, suggestions, targets)

    async def purge_expired(self, actor: User) -> int:
        """Erases uploaded and annotated files older than the retention period. Unconfirmed
        batches whose file is gone can no longer be imported, so they are marked DISCARDED.
        Batch history (counts, file name, who and when) is kept."""
        cutoff = self._clock() - timedelta(hours=self._limits.file_retention_hours)
        expired = await self._imports.expired_batches(cutoff)
        for batch in expired:
            self._remove_files(actor, batch.id)
            changes: dict[str, Any] = {"files_purged_at": self._clock(), "has_annotated_workbook": False}
            if batch.status in _EDITABLE:
                changes.update(status=DISCARDED, failure_reason="Expired: the file was not confirmed in time.")
            await self._imports.update_batch(batch.id, changes)
        return len(expired)

    async def _validate(
        self,
        actor: User,
        batch: ImportBatch,
        mapping: ColumnMapping,
        rules: PersonnelRules,
        failure_reason: str | None = None,
    ) -> ImportValidation:
        reader = self._reader(actor, batch)
        index = LocationIndex(
            await self._locations.buildings(), await self._locations.floors(), await self._locations.areas()
        )
        validation = ImportValidation(
            rules, mapping, batch.headers, index, self._personnel, self._clock().date(), self._limits.chunk_rows
        )
        await validation.run(reader)
        await self._imports.replace_issues(
            batch.id,
            [
                StoredIssue(i.row_number, i.column_index, i.column_name, i.field, i.severity.value, i.code, i.message)
                for i in validation.issues
            ],
        )
        annotated = self._path(actor, annotated_path(batch.id))
        if validation.issues:
            summary = {
                "filename": batch.original_filename,
                "total_rows": validation.total_rows,
                "valid_rows": validation.valid_rows,
                "error_count": validation.error_count,
                "warning_count": validation.warning_count,
            }
            labels = available_targets(list(rules.custom_fields.values()))
            partial = annotated.with_name("annotated.xlsx.part")
            await asyncio.to_thread(
                write_annotated_workbook,
                reader,
                batch.headers,
                mapping.targets,
                labels,
                validation.issues,
                summary,
                partial,
            )
            partial.replace(annotated)
        else:
            annotated.unlink(missing_ok=True)
        await self._imports.update_batch(
            batch.id,
            {
                "status": VALIDATED if validation.error_count == 0 else INVALID,
                "mapping": list(mapping.targets),
                "total_rows": validation.total_rows,
                "valid_rows": validation.valid_rows,
                "error_count": validation.error_count,
                "warning_count": validation.warning_count,
                "has_annotated_workbook": bool(validation.issues),
                "failure_reason": failure_reason,
                "validated_by": actor.id,
                "validated_at": self._clock(),
            },
        )
        return validation


class UploadPersonnelImport(_ImportBase):
    async def execute(
        self,
        actor: User,
        filename: str | None,
        chunks: AsyncIterator[bytes],
        *,
        sheet_name: str | None = None,
        header_row: int = 1,
    ) -> ImportView:
        ensure_personnel_manager(actor)
        await self.purge_expired(actor)
        batch_id = uuid4()
        name = _clean_filename(filename)
        upload = self._path(actor, f"{_folder(batch_id)}/original.upload")
        folder = upload.parent
        size = 0
        digest = hashlib.sha256()
        try:
            with upload.open("wb") as handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self._limits.max_upload_bytes:
                        raise ValidationFailed(
                            f"The file is larger than the {self._limits.max_upload_bytes} bytes this server accepts "
                            "(PERSONNEL_IMPORT_MAX_UPLOAD_BYTES). Nothing was imported; split the file or ask the "
                            "administrator to raise the limit.",
                            code="FILE_TOO_LARGE",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise ValidationFailed("The file is empty.", code="EMPTY_FILE")
            file_format = detect_format(upload, name, max_uncompressed=self._limits.max_uncompressed_bytes)
            stored = upload.with_name(f"original.{file_format.lower()}")
            upload.replace(stored)
            reader = SheetReader(stored, file_format, sheet_name=sheet_name, header_row=header_row)
            headers = await asyncio.to_thread(reader.headers)
            sheets = await asyncio.to_thread(reader.sheet_names)
            await self._imports.add_batch(
                batch_id,
                {
                    "original_filename": name,
                    "file_format": file_format,
                    "file_size": size,
                    "file_sha256": digest.hexdigest(),
                    "sheet_name": reader.sheet_name,
                    "header_row": header_row,
                    "headers": headers,
                },
                actor.id,
            )
            await self._audit.record(
                actor,
                "PERSONNEL_IMPORT_UPLOADED",
                "PERSONNEL_IMPORT",
                batch_id,
                {"format": file_format, "size_bytes": size, "columns": len(headers)},
            )
            batch = await self._batch(batch_id)
            view = await self._view(batch)
            await self._transaction.commit()
        except SpreadsheetError as error:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValidationFailed(f"{error} Nothing was imported.", code=error.code) from error
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return ImportView(view.batch, sheets, view.suggestions, view.targets)


class ValidatePersonnelImport(_ImportBase):
    async def execute(
        self, actor: User, batch_id: UUID, decisions: dict[int, str], *, save_profile_as: str | None = None
    ) -> ImportView:
        ensure_personnel_manager(actor)
        batch = await self._batch(batch_id, lock=True)
        if batch.status not in _EDITABLE:
            raise Conflict("This import can no longer be changed.", code="IMPORT_NOT_EDITABLE")
        rules = await campus_rules(self._personnel)
        mapping = confirm_mapping(batch.headers, decisions, list(rules.custom_fields.values()))
        validation = await self._validate(actor, batch, mapping, rules)
        if save_profile_as and save_profile_as.strip():
            profile = save_profile_as.strip()
            if len(profile) > 80:
                raise ValidationFailed("The profile name must be at most 80 characters.", code="INVALID_NAME")
            await self._imports.save_mapping_profile(profile, mapping.as_profile(batch.headers), actor.id)
        await self._audit.record(
            actor,
            "PERSONNEL_IMPORT_VALIDATED",
            "PERSONNEL_IMPORT",
            batch_id,
            {
                "total_rows": validation.total_rows,
                "error_count": validation.error_count,
                "warning_count": validation.warning_count,
            },
        )
        view = await self._view(await self._batch(batch_id))
        await self._transaction.commit()
        return view


class ConfirmPersonnelImport(_ImportBase):
    async def execute(self, actor: User, batch_id: UUID, *, acknowledge_warnings: bool) -> ImportView:
        ensure_personnel_manager(actor)
        assert self._binding is not None
        await self._imports.lock_campus_imports()
        batch = await self._batch(batch_id, lock=True)
        if batch.status == IMPORTED:
            raise Conflict("This import has already been confirmed.", code="IMPORT_ALREADY_CONFIRMED")
        if batch.status != VALIDATED or batch.mapping is None:
            raise Conflict(
                "Only a validated import without blocking errors can be confirmed.", code="IMPORT_NOT_VALIDATED"
            )
        if batch.warning_count and not acknowledge_warnings:
            raise ValidationFailed(
                f"Review the {batch.warning_count} warning(s) and confirm that you accept them.",
                code="WARNINGS_NOT_ACKNOWLEDGED",
            )
        reader = self._reader(actor, batch)
        if await asyncio.to_thread(_sha256, reader.path) != batch.file_sha256:
            raise Conflict(
                "The stored file changed since it was validated. Upload it again.", code="IMPORT_FILE_CHANGED"
            )

        rules = await campus_rules(self._personnel)
        imported = 0
        try:
            mapping = confirm_mapping(batch.headers, dict(enumerate(batch.mapping)), list(rules.custom_fields.values()))
            index = LocationIndex(
                await self._locations.buildings(), await self._locations.floors(), await self._locations.areas()
            )
            validation = ImportValidation(
                rules,
                mapping,
                batch.headers,
                index,
                self._personnel,
                self._clock().date(),
                self._limits.chunk_rows,
                stop_at_first_error=True,
            )
            async for chunk in validation.chunks(reader):
                people = [
                    NewPersonnel(
                        uuid4(), generate_personnel_code(), row.values, row.location, CreatedVia.IMPORT.value, batch.id
                    )
                    for row in chunk
                ]
                await self._personnel.add_many(people, actor.id)
                imported += len(people)
            if validation.error_count:
                raise ImportBlocked("blocking errors")
        except (ImportBlocked, Conflict, ValidationFailed) as problem:
            await self._recover(actor, batch_id, problem)

        await self._imports.update_batch(
            batch_id,
            {
                "status": IMPORTED,
                "imported_count": imported,
                "valid_rows": imported,
                "confirmed_by": actor.id,
                "confirmed_at": self._clock(),
                "failure_reason": None,
            },
        )
        await self._audit.record(
            actor,
            "PERSONNEL_IMPORT_CONFIRMED",
            "PERSONNEL_IMPORT",
            batch_id,
            {"imported_count": imported, "warning_count": batch.warning_count},
        )
        view = await self._view(await self._batch(batch_id))
        await self._transaction.commit()
        return view

    async def _recover(self, actor: User, batch_id: UUID, problem: Exception) -> NoReturn:
        """Nothing was imported (rolled back). Validate again so the batch shows what changed."""
        await self._transaction.rollback()
        assert self._binding is not None
        await self._binding.rebind(actor.id)
        batch = await self._batch(batch_id)
        rules = await campus_rules(self._personnel)
        reason = "Nothing was imported: the data no longer passed validation when the import was confirmed."
        try:
            mapping = confirm_mapping(
                batch.headers, dict(enumerate(batch.mapping or [])), list(rules.custom_fields.values())
            )
        except ValidationFailed:
            await self._imports.update_batch(
                batch_id,
                {
                    "status": INVALID,
                    "failure_reason": "Nothing was imported: the column mapping is no longer valid. Map again.",
                },
            )
            await self._audit.record(
                actor, "PERSONNEL_IMPORT_FAILED", "PERSONNEL_IMPORT", batch_id, {"reason": "MAPPING"}
            )
            await self._transaction.commit()
            raise Conflict(
                "Nothing was imported: a mapped custom field changed. Map the columns again.",
                code="IMPORT_REVALIDATION_FAILED",
            ) from problem
        validation = await self._validate(actor, batch, mapping, rules, failure_reason=reason)
        await self._audit.record(
            actor, "PERSONNEL_IMPORT_FAILED", "PERSONNEL_IMPORT", batch_id, {"error_count": validation.error_count}
        )
        await self._transaction.commit()
        if validation.error_count:
            raise Conflict(
                f"Nothing was imported: {validation.error_count} blocking error(s) appeared since validation (for "
                "example a person registered meanwhile). Download the new error report.",
                code="IMPORT_REVALIDATION_FAILED",
            ) from problem
        raise OperationFailed(
            "Nothing was imported because of a temporary problem. Please confirm again.", code="IMPORT_FAILED"
        ) from problem


class RollBackPersonnelImport(_ImportBase):
    async def execute(self, actor: User, batch_id: UUID, reason: str | None) -> ImportView:
        ensure_personnel_manager(actor)
        assert self._access is not None
        batch = await self._batch(batch_id, lock=True)
        if batch.status != IMPORTED:
            raise Conflict("Only a confirmed import can be rolled back.", code="IMPORT_NOT_IMPORTED")
        note = (reason or "").strip()
        if len(note) > 400:
            raise ValidationFailed("The reason must be at most 400 characters.", code="INVALID_REASON")
        text = "Import rolled back" + (f": {note}" if note else ".")
        people = await self._personnel.deactivate_import(batch_id, actor.id, text)
        revoked = await self._access.revoke_all_for(people, DEACTIVATED_GRANT_REASON, actor.id)
        await self._imports.update_batch(
            batch_id,
            {
                "status": ROLLED_BACK,
                "rolled_back_count": len(people),
                "rolled_back_by": actor.id,
                "rolled_back_at": self._clock(),
            },
        )
        await self._audit.record(
            actor,
            "PERSONNEL_IMPORT_ROLLED_BACK",
            "PERSONNEL_IMPORT",
            batch_id,
            {"deactivated": len(people), "access_revoked": revoked, "reason_given": bool(note)},
        )
        view = await self._view(await self._batch(batch_id))
        await self._transaction.commit()
        return view


class DiscardPersonnelImport(_ImportBase):
    async def execute(self, actor: User, batch_id: UUID) -> None:
        ensure_personnel_manager(actor)
        batch = await self._batch(batch_id, lock=True)
        if batch.status not in _EDITABLE:
            raise Conflict("Only an import that was not confirmed can be discarded.", code="IMPORT_NOT_EDITABLE")
        await self._imports.update_batch(
            batch_id, {"status": DISCARDED, "files_purged_at": self._clock(), "has_annotated_workbook": False}
        )
        await self._audit.record(actor, "PERSONNEL_IMPORT_DISCARDED", "PERSONNEL_IMPORT", batch_id, {})
        await self._transaction.commit()
        self._remove_files(actor, batch_id)


class ViewPersonnelImports(_ImportBase):
    async def history(self, actor: User, limit: int, offset: int) -> list[ImportBatch]:
        ensure_personnel_manager(actor)
        purged = await self.purge_expired(actor)
        batches = await self._imports.list_batches(limit, offset)
        if purged:
            await self._transaction.commit()
        return batches

    async def get(self, actor: User, batch_id: UUID) -> ImportView:
        ensure_personnel_manager(actor)
        return await self._view(await self._batch(batch_id))

    async def issues(
        self, actor: User, batch_id: UUID, severity: str | None, limit: int, offset: int
    ) -> list[StoredIssue]:
        ensure_personnel_manager(actor)
        await self._batch(batch_id)
        return await self._imports.issues(batch_id, severity, limit, offset)

    async def annotated_workbook(self, actor: User, batch_id: UUID) -> tuple[Path, str]:
        ensure_personnel_manager(actor)
        batch = await self._batch(batch_id)
        path = self._path(actor, annotated_path(batch_id))
        if not batch.has_annotated_workbook or batch.files_purged_at is not None or not path.is_file():
            raise NotFound("There is no annotated workbook for this import.", code="ANNOTATED_WORKBOOK_NOT_FOUND")
        stem = Path(batch.original_filename).stem or "personnel"
        return path, f"{stem}-validation.xlsx"

    async def preview(self, actor: User, batch_id: UUID, limit: int, offset: int) -> list[PreviewRow]:
        """The cleaned values the import would write (VALIDATED batches)."""
        ensure_personnel_manager(actor)
        batch = await self._batch(batch_id)
        if batch.status not in (VALIDATED, IMPORTED) or batch.mapping is None:
            raise Conflict(
                "A preview is available after a validation without blocking errors.", code="IMPORT_NOT_VALIDATED"
            )
        reader = self._reader(actor, batch)
        rules = await campus_rules(self._personnel)
        targets = list(batch.mapping)
        today = self._clock().date()
        labels = {c.id: c.label for c in rules.categories}

        def window() -> list[PreviewRow]:
            rows: list[PreviewRow] = []
            for position, row in enumerate(reader.rows()):
                if position < offset:
                    continue
                if len(rows) >= limit:
                    break
                raw = {t: row.cells[i].value for i, t in enumerate(targets) if t != "IGNORE" and i < len(row.cells)}
                location = {
                    k: (None if raw.get(k) is None else str(raw.get(k)))
                    for k in ("assigned_building", "assigned_floor", "assigned_area")
                }
                values, _ = rules.validate(
                    {k: v for k, v in raw.items() if k not in location},
                    today=today,
                    location_given=any(location.values()),
                )
                if values is not None:
                    rows.append(PreviewRow(row.number, values, labels.get(values.category_id, ""), location))
            return rows

        return await asyncio.to_thread(window)

    async def template(self, actor: User) -> bytes:
        ensure_personnel_manager(actor)
        return build_template(await self._personnel.categories(), await self._personnel.custom_fields())

    async def profiles(self, actor: User) -> list[tuple[UUID, str, dict[str, str]]]:
        ensure_personnel_manager(actor)
        return await self._imports.mapping_profiles()

    async def delete_profile(self, actor: User, profile_id: UUID) -> None:
        ensure_personnel_manager(actor)
        if not await self._imports.delete_mapping_profile(profile_id):
            raise NotFound("Mapping profile not found.", code="MAPPING_PROFILE_NOT_FOUND")
        await self._audit.record(actor, "PERSONNEL_IMPORT_PROFILE_DELETED", "IMPORT_PROFILE", profile_id, {})
        await self._transaction.commit()
