"""The annotated error workbook: a **separate copy** of the uploaded sheet with every problem marked
(decision D-016). The uploaded file itself is never changed.

* Cells with a blocking error: red fill; with a warning only: amber fill. Each has a comment with
  the severity, the code, what is wrong and how to fix it.
* A "Validation result" column at the end of the data lists every problem of the row.
* Header cells say which field each column was mapped to (or that it was ignored).
* A "Validation Report" sheet lists the summary and every issue (row, column, field, severity).

Written in openpyxl's write-only mode, row by row, so large files do not need large memory. Values
are copied as values: text that starts with "=" (including a formula found in the upload) is
written as plain text, so opening the copy never runs anything. Cell formatting of the original is
not copied.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.cell import WriteOnlyCell  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]

from src.application.services.personnel_import.mapping import IGNORE
from src.application.services.personnel_import.reader import SheetReader
from src.application.services.personnel_import.validator import ImportIssue
from src.domain.services.personnel_rules import Severity

ERROR_FILL = PatternFill("solid", fgColor="FFC7CE")  # red
WARNING_FILL = PatternFill("solid", fgColor="FFEB9C")  # amber
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
BOLD = Font(bold=True)
_AUTHOR = "Personnel import validator"

FIX_HINTS: dict[str, str] = {
    "REQUIRED": "Fill in this cell.",
    "REQUIRED_FOR_CATEGORY": "Fill in this cell, or check that the category is right.",
    "INVALID_EMAIL": "Enter an address like name@example.com.",
    "INVALID_PHONE": "Use 7 to 15 digits, e.g. 03001234567 or +923001234567. Format the column as Text in Excel.",
    "PHONE_STORED_AS_NUMBER": "Format the phone column as Text in Excel so leading zeros are kept, then check it.",
    "INVALID_DATE": "Use YYYY-MM-DD (e.g. 2026-09-01) or a real Excel date.",
    "END_BEFORE_START": "Correct the start or end date.",
    "END_DATE_PAST": "Check the end date; the record will still be imported.",
    "UNKNOWN_CATEGORY": "Use a category listed in the template's Instructions sheet.",
    "CATEGORY_INACTIVE": "Use an active category, or ask an Admin to reactivate this one.",
    "DUPLICATE_IN_FILE": "Each person may appear only once. Remove or correct the repeated row.",
    "DUPLICATE_ROW": "Remove the repeated row if it is the same person.",
    "ALREADY_EXISTS": "This person is already registered. Remove the row, or edit the existing record instead.",
    "FORMULA_NOT_ALLOWED": "Replace the formula with its value (Copy, then Paste Special > Values).",
    "CELL_ERROR": "The cell shows an Excel error such as #N/A. Enter a real value.",
    "UNHEADED_VALUE": "Give this column a header and map it, or clear the cell.",
    "TOO_LONG": "Shorten the text.",
    "INVALID_CHARACTERS": "Remove hidden control characters (retype the value).",
    "INVALID_CUSTOM_VALUE": "Enter a value of the field's type (see the Instructions sheet).",
    "UNKNOWN_CUSTOM_FIELD": "Map the column to an existing custom field, or ignore it.",
    "CUSTOM_FIELD_NOT_FOR_CATEGORY": "Check the value; it is kept even though the field is not used for this category.",
    "LOCATION_NOT_FOUND": "Use the exact name of a building, floor or area of this campus.",
    "AMBIGUOUS_AREA": "Also fill in the Assigned Floor column.",
    "BUILDING_REQUIRED": "Fill in the Assigned Building column too.",
    "NO_CONTACT": "Add an email address or phone number if you have one.",
    "NO_DATA_ROWS": "Add the personnel rows below the header row.",
}


def hint(code: str) -> str:
    return FIX_HINTS.get(code, "Correct the value and upload the file again.")


def _value_cell(sheet: Any, value: Any) -> WriteOnlyCell:
    if isinstance(value, str):
        cell = WriteOnlyCell(sheet, value=value)
        cell.data_type = "s"  # never a formula, even if the text starts with "="
        return cell
    return WriteOnlyCell(sheet, value=value)


def _text_cell(sheet: Any, text: str, *, bold: bool = False, fill: PatternFill | None = None) -> WriteOnlyCell:
    cell = _value_cell(sheet, text)
    if bold:
        cell.font = BOLD
    if fill is not None:
        cell.fill = fill
    return cell


def write_annotated_workbook(
    reader: SheetReader,
    headers: Sequence[str],
    targets: Sequence[str] | None,
    target_labels: Mapping[str, str],
    issues: Sequence[ImportIssue],
    summary: Mapping[str, Any],
    destination: Path,
) -> None:
    by_row: dict[int, list[ImportIssue]] = defaultdict(list)
    for issue in issues:
        if issue.row_number is not None:
            by_row[issue.row_number].append(issue)

    workbook = Workbook(write_only=True)
    data = workbook.create_sheet("Annotated Data")
    report = workbook.create_sheet("Validation Report")
    result_column = len(headers)

    written = 0
    for row in reader.all_rows():
        while written < row.number - 1:  # keep the original row numbers (empty rows were skipped)
            data.append([])
            written += 1
        cells: list[Any] = []
        if row.number == reader.header_row:
            for index, header in enumerate(headers):
                cell = _text_cell(data, header, bold=True, fill=HEADER_FILL)
                if targets is not None and index < len(targets):
                    target = targets[index]
                    note = (
                        "Ignored (not imported)"
                        if target == IGNORE
                        else f"Mapped to: {target_labels.get(target, target)}"
                    )
                    cell.comment = Comment(note, _AUTHOR)
                cells.append(cell)
            cells.append(_text_cell(data, "Validation result", bold=True, fill=HEADER_FILL))
        else:
            row_issues = by_row.get(row.number, [])
            per_column: dict[int, list[ImportIssue]] = defaultdict(list)
            for issue in row_issues:
                if issue.column_index is not None:
                    per_column[issue.column_index].append(issue)
            width = max(len(row.cells), result_column if row.number > reader.header_row else 0)
            for index in range(width):
                raw = row.cells[index] if index < len(row.cells) else None
                value = None if raw is None else raw.value
                cell = _value_cell(data, value)
                marked = per_column.get(index)
                if marked:
                    error = any(i.severity is Severity.ERROR for i in marked)
                    cell.fill = ERROR_FILL if error else WARNING_FILL
                    text = "\n".join(
                        f"{i.severity.value} ({i.code}): {i.message}\nHow to fix: {hint(i.code)}" for i in marked
                    )
                    cell.comment = Comment(text, _AUTHOR, width=320, height=40 + 60 * len(marked))
                cells.append(cell)
            if row.number > reader.header_row:
                if row_issues:
                    error = any(i.severity is Severity.ERROR for i in row_issues)
                    text = "; ".join(f"{i.severity.value}: {i.message}" for i in row_issues)
                    cells.append(_text_cell(data, text, fill=ERROR_FILL if error else WARNING_FILL))
                else:
                    cells.append(_text_cell(data, "OK"))
        data.append(cells)
        written = row.number

    _write_report(report, summary, issues)
    workbook.save(destination)


def _write_report(report: Any, summary: Mapping[str, Any], issues: Sequence[ImportIssue]) -> None:
    report.append([_text_cell(report, "Personnel import validation report", bold=True)])
    for label, key in (
        ("File", "filename"),
        ("Rows checked", "total_rows"),
        ("Valid rows", "valid_rows"),
        ("Blocking errors", "error_count"),
        ("Warnings", "warning_count"),
    ):
        report.append([_text_cell(report, label, bold=True), _value_cell(report, summary.get(key))])
    blocked = int(summary.get("error_count") or 0) > 0
    report.append(
        [
            _text_cell(report, "Result", bold=True),
            _text_cell(
                report,
                "BLOCKED: nothing will be imported. Fix every error and upload the corrected file again."
                if blocked
                else "No blocking errors. Review the warnings, then confirm the import.",
                fill=ERROR_FILL if blocked else None,
            ),
        ]
    )
    report.append(
        [
            _text_cell(report, "Legend", bold=True),
            _text_cell(report, "Blocking error", fill=ERROR_FILL),
            _text_cell(report, "Warning", fill=WARNING_FILL),
        ]
    )
    report.append([])
    report.append(
        [
            _text_cell(report, h, bold=True, fill=HEADER_FILL)
            for h in ("Row", "Column", "Field", "Severity", "Code", "Problem", "How to fix")
        ]
    )
    for issue in issues:
        fill = ERROR_FILL if issue.severity is Severity.ERROR else WARNING_FILL
        report.append(
            [
                _value_cell(report, issue.row_number if issue.row_number is not None else "File"),
                _value_cell(report, issue.column_name or ""),
                _value_cell(report, issue.field or ""),
                _text_cell(report, issue.severity.value, fill=fill),
                _value_cell(report, issue.code),
                _value_cell(report, issue.message),
                _value_cell(report, hint(issue.code)),
            ]
        )
