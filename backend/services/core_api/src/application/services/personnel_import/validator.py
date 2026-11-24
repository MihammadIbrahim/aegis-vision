"""Validation of a whole personnel import, as a stream (decision D-016).

Rows are read and checked in chunks (``chunk_rows`` at a time), in a worker thread so the API
stays responsive. Memory grows only with the identifiers seen (for duplicate detection) and the
issues found, never with the whole file.

Checks per row (the same ``PersonnelRules`` as the manual form, plus import-only checks):
* formulas and Excel error values are refused (nothing is ever evaluated);
* values in columns without a header are refused (they would otherwise be lost);
* assigned building / floor / area names must exist in this campus;
* duplicate Institutional IDs and emails **within the file** (error) and identical repeated rows
  (warning);
* Institutional IDs and emails that **already exist in this campus** (error).
"""

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol
from uuid import UUID

from src.application.services.personnel_import.mapping import (
    IGNORE,
    LOCATION_TARGETS,
    ColumnMapping,
)
from src.application.services.personnel_import.reader import (
    SheetReader,
    SheetRow,
    SpreadsheetError,
    column_letter,
)
from src.domain.entities.location import (
    Area,
    Building,
    Floor,
    LocationLevel,
    LocationTarget,
)
from src.domain.entities.personnel import PersonnelValues
from src.domain.services.personnel_rules import (
    FieldIssue,
    PersonnelRules,
    Severity,
    is_blank,
    label_of,
)


@dataclass(frozen=True, slots=True)
class ImportIssue:
    row_number: int | None  # None: about the whole file
    column_index: int | None
    column_name: str | None
    field: str | None
    severity: Severity
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidRow:
    row_number: int
    values: PersonnelValues
    location: LocationTarget | None


class IdentifierLookup(Protocol):
    async def existing_identifiers(
        self, external_ids: Sequence[str], emails: Sequence[str]
    ) -> tuple[set[str], set[str]]:
        """Which of these (lower-case) Institutional IDs and emails already exist in the campus."""
        ...


class ImportBlocked(Exception):
    """Raised in stop-at-first-error mode (the confirm step) when a blocking error appears."""


class LocationIndex:
    """The campus's buildings, floors and areas by name (ignoring capitalization)."""

    def __init__(self, buildings: Sequence[Building], floors: Sequence[Floor], areas: Sequence[Area]) -> None:
        self._buildings = {b.name.lower(): b for b in buildings}
        self._floors = {(f.building_id, f.name.lower()): f for f in floors}
        self._areas: dict[tuple[UUID, str], list[Area]] = {}
        for area in areas:
            self._areas.setdefault((area.building_id, area.name.lower()), []).append(area)

    def resolve(self, building: Any, floor: Any, area: Any) -> tuple[LocationTarget | None, FieldIssue | None]:
        if is_blank(building) and is_blank(floor) and is_blank(area):
            return None, None
        if is_blank(building):
            key = "assigned_floor" if not is_blank(floor) else "assigned_area"
            return None, FieldIssue(
                key,
                "BUILDING_REQUIRED",
                Severity.ERROR,
                "A floor or area needs its building in the Assigned Building column.",
            )
        found = self._buildings.get(str(building).strip().lower())
        if found is None:
            return None, FieldIssue(
                "assigned_building",
                "LOCATION_NOT_FOUND",
                Severity.ERROR,
                "Assigned Building is not a building or perimeter of this campus.",
            )
        floor_found = None
        if not is_blank(floor):
            floor_found = self._floors.get((found.id, str(floor).strip().lower()))
            if floor_found is None:
                return None, FieldIssue(
                    "assigned_floor",
                    "LOCATION_NOT_FOUND",
                    Severity.ERROR,
                    "Assigned Floor is not a floor of that building.",
                )
        if not is_blank(area):
            matches = self._areas.get((found.id, str(area).strip().lower()), [])
            if floor_found is not None:
                matches = [a for a in matches if a.floor_id == floor_found.id]
            if not matches:
                return None, FieldIssue(
                    "assigned_area",
                    "LOCATION_NOT_FOUND",
                    Severity.ERROR,
                    "Assigned Area is not an area of that building or floor.",
                )
            if len(matches) > 1:
                return None, FieldIssue(
                    "assigned_area",
                    "AMBIGUOUS_AREA",
                    Severity.ERROR,
                    "More than one area has this name in the building; give the floor too.",
                )
            return LocationTarget(LocationLevel.AREA, matches[0].id), None
        if floor_found is not None:
            return LocationTarget(LocationLevel.FLOOR, floor_found.id), None
        return LocationTarget(LocationLevel.BUILDING, found.id), None


@dataclass
class ImportValidation:
    rules: PersonnelRules
    mapping: ColumnMapping
    headers: Sequence[str]
    locations: LocationIndex
    lookup: IdentifierLookup
    today: date
    chunk_rows: int = 1000
    stop_at_first_error: bool = False

    issues: list[ImportIssue] = field(default_factory=list)
    total_rows: int = 0
    valid_rows: int = 0
    error_count: int = 0
    warning_count: int = 0
    _external_ids: dict[str, int] = field(default_factory=dict)
    _emails: dict[str, int] = field(default_factory=dict)
    _fingerprints: dict[int, int] = field(default_factory=dict)

    def _add(self, issue: ImportIssue) -> None:
        self.issues.append(issue)
        if issue.severity is Severity.ERROR:
            self.error_count += 1
            if self.stop_at_first_error:
                raise ImportBlocked(issue.message)
        else:
            self.warning_count += 1

    def _issue(self, row: int | None, field_key: str | None, code: str, severity: Severity, message: str) -> None:
        column = None if field_key is None else self.mapping.column_of(field_key)
        if column is None and field_key == "assigned_location":
            column = next(
                (self.mapping.column_of(t) for t in LOCATION_TARGETS if self.mapping.column_of(t) is not None), None
            )
        name = None if column is None else self.headers[column]
        self._add(ImportIssue(row, column, name, field_key, severity, code, message))

    def file_issue(self, code: str, message: str) -> None:
        self._add(ImportIssue(None, None, None, None, Severity.ERROR, code, message))

    async def chunks(self, reader: SheetReader) -> AsyncIterator[list[ValidRow]]:
        """Yields the valid rows chunk by chunk. File problems become file-level errors."""
        iterator = reader.rows()
        try:
            while True:
                valid = await asyncio.to_thread(self._read_chunk, iterator)
                if valid is None:
                    break
                yield await self._check_existing(valid)
        except SpreadsheetError as error:
            self.file_issue(error.code, str(error))
        if self.total_rows == 0 and not any(i.row_number is None for i in self.issues):
            self.file_issue("NO_DATA_ROWS", "The file has no data rows below the header row.")

    async def run(self, reader: SheetReader) -> None:
        async for _ in self.chunks(reader):
            pass

    def _read_chunk(self, iterator: Iterator[SheetRow]) -> list[ValidRow] | None:
        valid: list[ValidRow] = []
        read = 0
        for row in iterator:
            read += 1
            checked = self._check_row(row)
            if checked is not None:
                valid.append(checked)
            if read >= self.chunk_rows:
                break
        return valid if read else None

    def _check_row(self, row: SheetRow) -> ValidRow | None:
        self.total_rows += 1
        errors_before = self.error_count
        raw: dict[str, Any] = {}
        unusable: set[str] = set()
        for index, cell in enumerate(row.cells):
            if index >= len(self.headers):
                if not is_blank(cell.value):
                    self._add(
                        ImportIssue(
                            row.number,
                            index,
                            f"(Column {column_letter(index)})",
                            None,
                            Severity.ERROR,
                            "UNHEADED_VALUE",
                            "This cell has a value but its column has no header.",
                        )
                    )
                continue
            target = self.mapping.targets[index]
            if target == IGNORE:
                continue
            if cell.is_formula:
                self._issue(
                    row.number,
                    target,
                    "FORMULA_NOT_ALLOWED",
                    Severity.ERROR,
                    f"{label_of(target, self.rules.custom_fields)} contains a formula; enter the value itself.",
                )
                unusable.add(target)
            elif cell.is_error:
                self._issue(
                    row.number,
                    target,
                    "CELL_ERROR",
                    Severity.ERROR,
                    f"{label_of(target, self.rules.custom_fields)} shows an Excel error value.",
                )
                unusable.add(target)
            else:
                raw[target] = cell.value

        location, problem = self.locations.resolve(
            raw.get("assigned_building"), raw.get("assigned_floor"), raw.get("assigned_area")
        )
        if problem is not None:
            self._issue(row.number, problem.field, problem.code, problem.severity, problem.message)
            unusable.add("assigned_location")
        values, issues = self.rules.validate(
            {k: v for k, v in raw.items() if k not in LOCATION_TARGETS},
            today=self.today,
            location_given=location is not None or "assigned_location" in unusable,
        )
        for issue in issues:
            if issue.field not in unusable:
                self._issue(row.number, issue.field, issue.code, issue.severity, issue.message)
        if values is None or self.error_count > errors_before:
            return None
        return self._check_duplicates(row.number, values, location)

    def _check_duplicates(
        self, number: int, values: PersonnelValues, location: LocationTarget | None
    ) -> ValidRow | None:
        errors_before = self.error_count
        if values.external_id:
            first = self._external_ids.setdefault(values.external_id.lower(), number)
            if first != number:
                self._issue(
                    number,
                    "external_id",
                    "DUPLICATE_IN_FILE",
                    Severity.ERROR,
                    f"Institutional ID is duplicated in this file (also in row {first}).",
                )
        if values.email:
            first = self._emails.setdefault(values.email, number)
            if first != number:
                self._issue(
                    number,
                    "email",
                    "DUPLICATE_IN_FILE",
                    Severity.ERROR,
                    f"Email Address is duplicated in this file (also in row {first}).",
                )
        fingerprint = hash((repr(values), location))
        first = self._fingerprints.setdefault(fingerprint, number)
        if first != number:
            self._issue(number, None, "DUPLICATE_ROW", Severity.WARNING, f"This row repeats row {first} exactly.")
        if self.error_count > errors_before:
            return None
        return ValidRow(number, values, location)

    async def _check_existing(self, rows: list[ValidRow]) -> list[ValidRow]:
        if not rows:
            return rows
        ids = [r.values.external_id.lower() for r in rows if r.values.external_id]
        emails = [r.values.email for r in rows if r.values.email]
        taken_ids, taken_emails = await self.lookup.existing_identifiers(ids, emails)
        kept = []
        for row in rows:
            bad = False
            if row.values.external_id and row.values.external_id.lower() in taken_ids:
                self._issue(
                    row.row_number,
                    "external_id",
                    "ALREADY_EXISTS",
                    Severity.ERROR,
                    "Institutional ID already exists in this campus.",
                )
                bad = True
            if row.values.email and row.values.email in taken_emails:
                self._issue(
                    row.row_number,
                    "email",
                    "ALREADY_EXISTS",
                    Severity.ERROR,
                    "Email Address already exists in this campus.",
                )
                bad = True
            if not bad:
                kept.append(row)
        self.valid_rows += len(kept)
        return kept
