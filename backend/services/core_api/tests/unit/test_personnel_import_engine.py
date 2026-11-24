"""The personnel import engine without a database (decision D-016): reading files safely, column
mapping suggestions, streaming validation, the annotated workbook and the template."""

import csv
import hashlib
import io
import zipfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook

from src.application.services.personnel_import.annotator import (
    ERROR_FILL,
    WARNING_FILL,
    write_annotated_workbook,
)
from src.application.services.personnel_import.mapping import (
    IGNORE,
    ColumnMapping,
    Confidence,
    available_targets,
    confirm_mapping,
    suggest,
)
from src.application.services.personnel_import.reader import (
    CSV,
    XLSX,
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
from src.domain.entities.location import (
    Area,
    Boundary,
    Building,
    BuildingKind,
    Floor,
    LocationLevel,
)
from src.domain.entities.personnel import CustomField, FieldType, PersonnelCategory
from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.personnel_rules import PersonnelRules, Severity

TODAY = date(2026, 11, 1)
STUDENT = PersonnelCategory(uuid4(), "STUDENT", "Student", ("external_id",))
EMPLOYEE = PersonnelCategory(uuid4(), "EMPLOYEE", "Employee", ("external_id",))
PROGRAM = CustomField(uuid4(), "program", "Academic Program", FieldType.TEXT)
RULES = PersonnelRules([STUDENT, EMPLOYEE], [PROGRAM])
ORG, CAMPUS = uuid4(), uuid4()
BLOCK = Building(uuid4(), ORG, CAMPUS, BuildingKind.INDOOR, "IT Block")
GROUND = Floor(uuid4(), BLOCK.id, "Ground", 0)
SERVER = Area(uuid4(), BLOCK.id, GROUND.id, "Server Room", Boundary())
LOCATIONS = LocationIndex([BLOCK], [GROUND], [SERVER])


def xlsx(path: Path, rows: Sequence[Sequence[object]], *, formula_cell: str | None = None) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(list(row))
    if formula_cell:
        sheet[formula_cell] = "=1+2"
    workbook.save(path)
    return path


def csv_file(path: Path, rows: Sequence[Sequence[object]], delimiter: str = ",") -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle, delimiter=delimiter).writerows(rows)
    return path


class Lookup:
    def __init__(self, ids: Sequence[str] = (), emails: Sequence[str] = ()) -> None:
        self.ids, self.emails = set(ids), set(emails)
        self.calls = 0

    async def existing_identifiers(self, external_ids, emails):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.ids & set(external_ids), self.emails & set(emails)


HEADERS = ["Student Name", "Type", "Registration No", "Email", "Program"]
TARGETS = ("full_name", "category", "external_id", "email", "custom:program")


def validation(lookup: Lookup | None = None, **kwargs) -> ImportValidation:  # type: ignore[no-untyped-def]
    return ImportValidation(RULES, ColumnMapping(TARGETS), HEADERS, LOCATIONS, lookup or Lookup(), TODAY, **kwargs)


# --- Reading -------------------------------------------------------------------------------------------------


def test_xlsx_and_csv_are_read_as_streams_with_original_row_numbers(tmp_path: Path) -> None:
    rows = [HEADERS, ["Ali", "Student", "S1", "ali@example.com", "BSSE"], [], ["Sara", "Student", "S2", "", ""]]
    for path, fmt in ((xlsx(tmp_path / "a.xlsx", rows), XLSX), (csv_file(tmp_path / "a.csv", rows, ";"), CSV)):
        assert detect_format(path, path.name, max_uncompressed=10**8) == fmt
        reader = SheetReader(path, fmt)
        assert reader.headers() == HEADERS
        data = list(reader.rows())
        assert [r.number for r in data] == [2, 4]  # the empty row 3 is skipped, numbering is kept
        assert data[0].cells[0].value == "Ali"


def test_unsafe_or_unsupported_files_are_refused(tmp_path: Path) -> None:
    def refused(path: Path, name: str, limit: int = 10**8) -> str:
        with pytest.raises(SpreadsheetError) as caught:
            detect_format(path, name, max_uncompressed=limit)
        return caught.value.code

    book = xlsx(tmp_path / "ok.xlsx", [["Name"]])
    assert refused(book, "macro.xlsm") == "MACRO_WORKBOOK"
    legacy = tmp_path / "old.xls"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    assert refused(legacy, "old.xls") == "LEGACY_XLS"
    fake = tmp_path / "fake.xlsx"
    fake.write_text("Name,Email\n")
    assert refused(fake, "fake.xlsx") == "NOT_A_WORKBOOK"
    assert refused(book, "ok.xlsx", limit=100) == "WORKBOOK_TOO_LARGE_UNPACKED"  # zip-bomb guard
    macro = tmp_path / "hidden.xlsx"
    with zipfile.ZipFile(book) as source, zipfile.ZipFile(macro, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr("xl/vbaProject.bin", b"macro")
    assert refused(macro, "hidden.xlsx") == "MACRO_WORKBOOK"
    binary = tmp_path / "data.csv"
    binary.write_bytes(b"Name\x00\x01")
    assert refused(binary, "data.csv") == "NOT_A_CSV"
    assert refused(book, "notes.pdf") == "UNSUPPORTED_FILE_TYPE"
    latin = tmp_path / "latin.csv"
    latin.write_bytes("Name\nJos\xe9\n".encode("latin-1"))
    with pytest.raises(SpreadsheetError) as caught:
        list(SheetReader(latin, CSV).rows())
    assert caught.value.code == "CSV_NOT_UTF8"


def test_formulas_and_error_values_are_detected_not_evaluated(tmp_path: Path) -> None:
    path = xlsx(tmp_path / "f.xlsx", [HEADERS, ["Ali", "Student", None, "ali@example.com", "#N/A"]], formula_cell="C2")
    cells = next(SheetReader(path, XLSX).rows()).cells
    assert cells[2].is_formula and cells[2].value == "=1+2"
    assert cells[4].is_error


# --- Mapping -------------------------------------------------------------------------------------------------


def test_suggestions_are_exact_synonym_fuzzy_or_none_and_never_organization_ids() -> None:
    headers = [
        "Full Name *",
        "Registration Number",
        "E-mail",
        "Departmnt",
        "Academic Program",
        "Organization ID",
        "Favourite Colour",
        "Name",
    ]
    got = suggest(headers, [PROGRAM])
    assert [(s.target, s.confidence) for s in got] == [
        ("full_name", Confidence.EXACT),
        ("external_id", Confidence.SYNONYM),
        ("email", Confidence.SYNONYM),
        ("department", Confidence.FUZZY),
        ("custom:program", Confidence.EXACT),
        (None, Confidence.NONE),  # organization and campus come from the session, never from the file
        (None, Confidence.NONE),
        (None, Confidence.NONE),  # full_name is already suggested for the first column
    ]
    profile = suggest(["Favourite Colour"], [PROGRAM], {"favourite colour": IGNORE})
    assert (profile[0].target, profile[0].confidence) == (IGNORE, Confidence.PROFILE)


def test_every_column_needs_a_decision_and_required_fields_must_be_mapped() -> None:
    headers = ["Name", "Type", "Hobby"]
    with pytest.raises(ValidationFailed) as caught:
        confirm_mapping(headers, {0: "full_name", 1: "category"}, [])
    assert caught.value.code == "MAPPING_INCOMPLETE"
    assert [e["code"] for e in caught.value.errors or []] == ["UNMAPPED_COLUMN"]  # not silently dropped
    with pytest.raises(ValidationFailed) as caught:
        confirm_mapping(headers, {0: "full_name", 1: "full_name", 2: "custom:missing"}, [])
    assert {e["code"] for e in caught.value.errors or []} == {
        "DUPLICATE_TARGET",
        "UNKNOWN_TARGET",
        "REQUIRED_FIELD_UNMAPPED",
    }
    mapping = confirm_mapping(headers, {0: "full_name", 1: "category", 2: IGNORE}, [])
    assert mapping.targets == ("full_name", "category", IGNORE)
    assert "organization_id" not in available_targets([]) and "campus_id" not in available_targets([])


# --- Validation ----------------------------------------------------------------------------------------------


async def test_in_file_and_existing_duplicates_formulas_and_unheaded_values(tmp_path: Path) -> None:
    path = xlsx(
        tmp_path / "v.xlsx",
        [
            HEADERS,
            ["Ali", "Student", "S1", "ali@example.com", "BSSE"],
            ["Ali Again", "Student", "s1", "other@example.com", ""],  # same ID, other case
            ["Sara", "Student", "S2", "ALI@example.com", ""],  # same email
            ["Taken", "Student", "S9", "t@example.com", ""],  # already in the campus
            ["Formula", "Student", None, "f@example.com", ""],
            ["Stray", "Employee", "E1", "s@example.com", "", "value without a header"],
            ["Ali", "Student", "S1", "ali@example.com", "BSSE"],
        ],
        formula_cell="C6",
    )
    lookup = Lookup(ids=["s9"])
    check = validation(lookup, chunk_rows=2)
    valid = [row async for chunk in check.chunks(SheetReader(path, XLSX)) for row in chunk]
    assert [r.row_number for r in valid] == [2]
    found = sorted((i.row_number, i.field, i.code) for i in check.issues if i.severity is Severity.ERROR)
    assert found == [
        (3, "external_id", "DUPLICATE_IN_FILE"),
        (4, "email", "DUPLICATE_IN_FILE"),
        (5, "external_id", "ALREADY_EXISTS"),
        (6, "external_id", "FORMULA_NOT_ALLOWED"),  # no extra "required" error for the same cell
        (7, None, "UNHEADED_VALUE"),
        (8, "email", "DUPLICATE_IN_FILE"),
        (8, "external_id", "DUPLICATE_IN_FILE"),
    ]
    assert (8, "DUPLICATE_ROW") in {(i.row_number, i.code) for i in check.issues if i.severity is Severity.WARNING}
    assert check.total_rows == 7 and check.valid_rows == 1 and lookup.calls >= 1
    stray = next(i for i in check.issues if i.code == "UNHEADED_VALUE")
    assert stray.column_index == 5 and stray.column_name == "(Column F)"


async def test_locations_are_resolved_by_name_within_the_campus(tmp_path: Path) -> None:
    headers = ["Name", "Type", "ID", "Building", "Floor", "Room"]
    targets = ("full_name", "category", "external_id", "assigned_building", "assigned_floor", "assigned_area")
    path = csv_file(
        tmp_path / "l.csv",
        [
            headers,
            ["A", "Employee", "E1", "it block", "", "server room"],
            ["B", "Employee", "E2", "Nowhere", "", ""],
            ["C", "Employee", "E3", "", "Ground", ""],
            ["D", "Employee", "E4", "IT Block", "Ground", ""],
        ],
    )
    check = ImportValidation(RULES, ColumnMapping(targets), headers, LOCATIONS, Lookup(), TODAY)
    valid = [row async for chunk in check.chunks(SheetReader(path, CSV)) for row in chunk]
    assert [(r.row_number, r.location.level, r.location.id) for r in valid if r.location] == [
        (2, LocationLevel.AREA, SERVER.id),
        (5, LocationLevel.FLOOR, GROUND.id),
    ]
    assert sorted((i.row_number, i.code) for i in check.issues if i.severity is Severity.ERROR) == [
        (3, "LOCATION_NOT_FOUND"),
        (4, "BUILDING_REQUIRED"),
    ]


async def test_an_empty_sheet_is_a_blocking_file_error_and_confirm_mode_stops_early(tmp_path: Path) -> None:
    check = validation()
    await check.run(SheetReader(xlsx(tmp_path / "e.xlsx", [HEADERS]), XLSX))
    assert [(i.row_number, i.code) for i in check.issues] == [(None, "NO_DATA_ROWS")]
    path = xlsx(tmp_path / "s.xlsx", [HEADERS, ["Ok", "Student", "S1", "a@example.com", ""], ["", "Student"]])
    with pytest.raises(ImportBlocked):
        await validation(stop_at_first_error=True).run(SheetReader(path, XLSX))


async def test_large_files_have_no_row_limit_and_nothing_is_truncated(tmp_path: Path) -> None:
    rows = [HEADERS] + [[f"Person {n}", "Student", f"S{n}", f"p{n}@example.com", ""] for n in range(30_000)]
    path = csv_file(tmp_path / "big.csv", rows)
    lookup = Lookup()
    check = validation(lookup, chunk_rows=1000)
    sizes = [len(chunk) async for chunk in check.chunks(SheetReader(path, CSV))]
    assert sum(sizes) == 30_000 and max(sizes) == 1000 and len(sizes) == 30
    assert check.total_rows == check.valid_rows == 30_000 and check.error_count == 0
    assert lookup.calls == 30  # one database round trip per chunk, not per row


# --- Annotated workbook and template --------------------------------------------------------------------------


async def test_the_annotated_copy_marks_problems_and_leaves_the_original_untouched(tmp_path: Path) -> None:
    path = xlsx(
        tmp_path / "upload.xlsx",
        [
            ["Personnel list"],
            HEADERS,
            ["Ali", "Student", "S1", "bad-email", '=HYPERLINK("x")'],
            ["Sara", "Student", "S2", "s@example.com", ""],
            ["Zed", "Student", "S3", "", ""],
        ],
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = SheetReader(path, XLSX, header_row=2)
    check = validation()
    await check.run(reader)
    out = tmp_path / "annotated.xlsx"
    summary = {
        "filename": "upload.xlsx",
        "total_rows": check.total_rows,
        "valid_rows": check.valid_rows,
        "error_count": check.error_count,
        "warning_count": check.warning_count,
    }
    labels = available_targets([PROGRAM])
    write_annotated_workbook(reader, HEADERS, TARGETS, labels, check.issues, summary, out)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # the upload is never modified

    book = load_workbook(out)
    data, report = book["Annotated Data"], book["Validation Report"]
    assert data["A1"].value == "Personnel list"  # rows above the header are kept, numbering unchanged
    assert data["D3"].fill.fgColor.rgb.endswith(ERROR_FILL.fgColor.rgb[-6:])
    assert "INVALID_EMAIL" in data["D3"].comment.text and "How to fix" in data["D3"].comment.text
    assert data["A2"].comment.text == "Mapped to: Full Name"
    assert data["E3"].data_type == "s" and data["E3"].value.startswith("=HYPERLINK")  # stays plain text
    assert data["F2"].value == "Validation result" and data["F3"].value.startswith("ERROR") and data["F4"].value == "OK"
    assert data["F5"].value.startswith("WARNING") and data["F5"].fill.fgColor.rgb.endswith(
        WARNING_FILL.fgColor.rgb[-6:]
    )
    assert data["D4"].fill.fgColor.rgb in ("00000000", None)
    assert data["A4"].value == "Sara"
    texts = [c.value for row in report.iter_rows() for c in row if isinstance(c.value, str)]
    assert any(t.startswith("BLOCKED") for t in texts) and "INVALID_EMAIL" in texts
    warning_rows = [row for row in report.iter_rows(min_row=1) if row[3].value == "WARNING"]
    assert warning_rows and warning_rows[0][3].fill.fgColor.rgb.endswith(WARNING_FILL.fgColor.rgb[-6:])


def test_the_template_matches_the_campus_and_maps_back_exactly() -> None:
    data = build_template([STUDENT, EMPLOYEE], [PROGRAM])
    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames[0] == "Personnel" and "Instructions" in book.sheetnames and "Examples" in book.sheetnames
    sheet = book["Personnel"]
    headers = [c.value for c in sheet[1] if c.value]
    assert headers[:2] == ["Full Name *", "Personnel Category *"] and "Academic Program" in headers
    assert sheet.max_row == 1  # no example rows that could be imported by mistake
    assert sheet.column_dimensions["C"].number_format == "@"  # IDs and phones keep leading zeros
    assert not any("organization" in h.lower() or "campus" in h.lower() for h in headers)
    assert "Required for: Student, Employee" in sheet["C1"].comment.text
    assert all(s.confidence is Confidence.EXACT for s in suggest(headers, [PROGRAM]))
    assert any(dv.formula1.startswith("=Lists!") for dv in sheet.data_validations.dataValidation)
