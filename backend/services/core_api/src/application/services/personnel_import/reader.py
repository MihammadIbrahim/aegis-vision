"""Reading uploaded personnel spreadsheets as a stream (decision D-016).

* **.xlsx:** openpyxl in read-only mode, one row at a time; nothing is evaluated. A formula cell is
  reported as a formula (``RawCell.is_formula``), never calculated, and an Excel error value such as
  ``#N/A`` as an error. Macro-enabled workbooks are refused, and so is an archive whose unpacked
  size is over the configured limit (an .xlsx is a zip file).
* **.csv:** UTF-8 (with or without BOM); comma, semicolon or tab separated. Values stay text.
* Old ``.xls`` and anything else is refused with a clear message.

Rows that are completely empty are skipped (they hold no data); every other row is returned, so
nothing is silently dropped. Row numbers are the ones the user sees in Excel.
"""

import csv
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook  # type: ignore[import-untyped]

XLSX = "XLSX"
CSV = "CSV"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls


class SpreadsheetError(Exception):
    """The file cannot be used. ``code`` is stable; the message is safe to show and to log."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RawCell:
    value: Any
    is_formula: bool = False
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class SheetRow:
    number: int  # 1-based row number in the sheet (CSV: record number)
    cells: tuple[RawCell, ...]


def detect_format(path: Path, filename: str, *, max_uncompressed: int) -> str:
    """XLSX or CSV, checked against the content (not only the name)."""
    suffix = Path(filename).suffix.lower()
    with path.open("rb") as handle:
        head = handle.read(8192)
    if suffix in (".xlsm", ".xltm", ".xlsb"):
        raise SpreadsheetError("MACRO_WORKBOOK", "Macro-enabled and binary workbooks are not accepted. Save as .xlsx.")
    if head.startswith(_OLE_MAGIC) or suffix == ".xls":
        raise SpreadsheetError("LEGACY_XLS", "Old .xls files are not accepted. Save the file as .xlsx or .csv.")
    if suffix == ".xlsx":
        if not head.startswith(_ZIP_MAGIC):
            raise SpreadsheetError("NOT_A_WORKBOOK", "The file is not a valid .xlsx workbook.")
        _check_workbook_archive(path, max_uncompressed)
        return XLSX
    if suffix in (".csv", ".txt"):
        if b"\x00" in head or head.startswith(_ZIP_MAGIC):
            raise SpreadsheetError("NOT_A_CSV", "The file is not a text CSV file.")
        return CSV
    raise SpreadsheetError("UNSUPPORTED_FILE_TYPE", "Upload an Excel workbook (.xlsx) or a CSV file (.csv).")


def _check_workbook_archive(path: Path, max_uncompressed: int) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as error:
        raise SpreadsheetError("NOT_A_WORKBOOK", "The file is not a valid .xlsx workbook.") from error
    names = {e.filename for e in entries}
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise SpreadsheetError("NOT_A_WORKBOOK", "The file is not a valid .xlsx workbook.")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise SpreadsheetError("MACRO_WORKBOOK", "Workbooks containing macros are not accepted. Save as .xlsx.")
    if sum(e.file_size for e in entries) > max_uncompressed:
        raise SpreadsheetError(
            "WORKBOOK_TOO_LARGE_UNPACKED",
            "The workbook is larger than this server accepts when unpacked "
            "(PERSONNEL_IMPORT_MAX_UNCOMPRESSED_BYTES). Split it or save it as .csv.",
        )


class SheetReader:
    """Opens one sheet; ``headers()`` and ``rows()`` can be called repeatedly (each call re-reads
    the file from the start, so memory use does not grow with the number of rows)."""

    def __init__(self, path: Path, file_format: str, *, sheet_name: str | None = None, header_row: int = 1) -> None:
        if header_row < 1:
            raise SpreadsheetError("INVALID_HEADER_ROW", "The header row must be 1 or more.")
        self.path = path
        self.file_format = file_format
        self.header_row = header_row
        self.sheet_name = sheet_name
        if file_format == XLSX:
            names = self.sheet_names()
            if sheet_name is None:
                self.sheet_name = names[0] if names else None
            elif sheet_name not in names:
                raise SpreadsheetError("SHEET_NOT_FOUND", "The selected sheet does not exist in the workbook.")
            if self.sheet_name is None:
                raise SpreadsheetError("EMPTY_WORKBOOK", "The workbook has no sheets.")

    def sheet_names(self) -> list[str]:
        if self.file_format != XLSX:
            return []
        workbook = self._open()
        try:
            return list(workbook.sheetnames)
        finally:
            workbook.close()

    def _open(self):  # type: ignore[no-untyped-def]
        try:
            return load_workbook(self.path, read_only=True, data_only=False)
        except Exception as error:  # openpyxl raises many types for broken files
            raise SpreadsheetError("UNREADABLE_WORKBOOK", "The workbook could not be read. Is it damaged?") from error

    def all_rows(self) -> Iterator[SheetRow]:
        """Every non-empty row of the sheet, including those above and at the header row."""
        if self.file_format == XLSX:
            yield from self._xlsx_rows()
        else:
            yield from self._csv_rows()

    def headers(self) -> list[str]:
        for row in self.all_rows():
            if row.number == self.header_row:
                return _header_names(row.cells)
            if row.number > self.header_row:
                break
        raise SpreadsheetError("NO_HEADER_ROW", "The header row is empty. Choose the row that holds the column names.")

    def rows(self) -> Iterator[SheetRow]:
        """The data rows: every non-empty row below the header row."""
        for row in self.all_rows():
            if row.number > self.header_row:
                yield row

    def _xlsx_rows(self) -> Iterator[SheetRow]:
        workbook = self._open()
        try:
            sheet = workbook[self.sheet_name]
            try:
                for number, row in enumerate(sheet.iter_rows(), start=1):
                    cells = tuple(
                        RawCell(
                            cell.value,
                            is_formula=getattr(cell, "data_type", None) == "f",
                            is_error=getattr(cell, "data_type", None) == "e",
                        )
                        for cell in row
                    )
                    if any(not _empty(c.value) for c in cells):
                        yield SheetRow(number, _trim(cells))
            except SpreadsheetError:
                raise
            except Exception as error:
                raise SpreadsheetError("UNREADABLE_WORKBOOK", "The workbook could not be read to the end.") from error
        finally:
            workbook.close()

    def _csv_rows(self) -> Iterator[SheetRow]:
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(65536)
                handle.seek(0)
                try:
                    dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample else csv.excel
                except csv.Error:
                    dialect = csv.excel
                reader = csv.reader(handle, dialect)
                for number, record in enumerate(reader, start=1):
                    cells = tuple(RawCell(v) for v in record)
                    if any(not _empty(c.value) for c in cells):
                        yield SheetRow(number, _trim(cells))
        except UnicodeDecodeError as error:
            raise SpreadsheetError("CSV_NOT_UTF8", "The CSV file is not UTF-8 text. Save it as 'CSV UTF-8'.") from error
        except csv.Error as error:
            raise SpreadsheetError(
                "CSV_MALFORMED", "The CSV file is malformed (for example an unclosed quote)."
            ) from error


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _trim(cells: tuple[RawCell, ...]) -> tuple[RawCell, ...]:
    end = len(cells)
    while end and _empty(cells[end - 1].value):
        end -= 1
    return cells[:end]


def _header_names(cells: tuple[RawCell, ...]) -> list[str]:
    names = []
    for index, cell in enumerate(cells):
        text = "" if cell.value is None else str(cell.value).strip()
        names.append(text or f"(Column {column_letter(index)})")
    return names


def column_letter(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
