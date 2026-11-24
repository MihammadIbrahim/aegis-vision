"""The downloadable personnel import template, generated for the Admin's own campus (decision
D-016): its categories and custom fields are the ones the campus really uses.

Sheets:
* **Personnel**: only the header row. Required columns end with " *" and are shaded; every header
  has a comment explaining the column. The category column (and choice-type custom fields) offer a
  drop-down list. Phone and ID columns are formatted as text so leading zeros are kept.
* **Instructions**: how to fill the sheet, the categories and what each one requires, the custom
  fields, and the all-or-nothing rule.
* **Examples**: illustrative rows only. They are in a separate sheet so they are never imported.

There are no Organization ID or Campus ID columns: organization and campus always come from the
signed-in Admin.
"""

import io
from collections.abc import Sequence

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

from src.application.services.personnel_import.mapping import LOCATION_TARGETS
from src.domain.entities.personnel import (
    CANONICAL_FIELDS,
    FIELDS_BY_KEY,
    CustomField,
    PersonnelCategory,
)

_REQUIRED_FILL = PatternFill("solid", fgColor="FCE4D6")
_OPTIONAL_FILL = PatternFill("solid", fgColor="DDEBF7")
_BOLD = Font(bold=True)
_AUTHOR = "Personnel import template"
_TEXT_COLUMNS = {"external_id", "phone", "emergency_contact_phone"}

_EXAMPLES = (
    (
        "Example Student",
        "Student",
        "STU-001",
        "student@example.com",
        "03000000000",
        "Computer Science",
        "Student",
        "2026-09-01",
        "2026-09-01",
        "2030-06-30",
    ),
    (
        "Example Faculty",
        "Faculty",
        "FAC-001",
        "faculty@example.com",
        "03000000001",
        "Computer Science",
        "Lecturer",
        "2024-02-01",
        "2024-02-01",
        "",
    ),
    (
        "Example Employee",
        "Employee",
        "EMP-001",
        "employee@example.com",
        "03000000002",
        "Administration",
        "Manager",
        "2023-07-15",
        "2023-07-15",
        "",
    ),
    ("Example Visitor", "Visitor", "", "", "03000000003", "", "", "", "2026-11-01", "2026-11-02"),
)


def template_columns(custom_fields: Sequence[CustomField]) -> list[tuple[str, str, bool, str]]:
    """(target, header, required for every category, help) in template order."""
    columns: list[tuple[str, str, bool, str]] = []
    for definition in CANONICAL_FIELDS:
        if definition.key == "assigned_location":
            for target, label in LOCATION_TARGETS.items():
                columns.append((target, label, False, "Optional: exact name of a building, floor or area here."))
            continue
        columns.append((definition.key, definition.label, definition.always_required, definition.help))
    for field in custom_fields:
        if field.is_active:
            columns.append(
                (
                    f"custom:{field.key}",
                    field.label,
                    field.is_required and not field.category_ids,
                    field.help_text or f"Custom field ({field.field_type.value.lower()}).",
                )
            )
    return columns


def build_template(categories: Sequence[PersonnelCategory], custom_fields: Sequence[CustomField]) -> bytes:
    active = [c for c in categories if c.is_active]
    by_id = {c.id: c for c in categories}
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Personnel"
    columns = template_columns(custom_fields)

    lists = workbook.create_sheet("Lists")
    lists.sheet_state = "hidden"
    lists.cell(row=1, column=1, value="Categories").font = _BOLD
    for row, category in enumerate(active, start=2):
        lists.cell(row=row, column=1, value=category.label)

    for index, (target, header, required, help_text) in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=f"{header} *" if required else header)
        cell.font = _BOLD
        cell.fill = _REQUIRED_FILL if required else _OPTIONAL_FILL
        note = ("REQUIRED. " if required else "Optional. ") + help_text
        if target in FIELDS_BY_KEY and not required:
            needed_by = [c.label for c in active if target in c.required_fields]
            if needed_by:
                note += f" Required for: {', '.join(needed_by)}."
        cell.comment = Comment(note, _AUTHOR, width=300, height=110)
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = max(14, min(40, len(header) + 6))
        if target.removeprefix("custom:") in _TEXT_COLUMNS or target in _TEXT_COLUMNS:
            sheet.column_dimensions[letter].number_format = "@"
        if target == "category" and active:
            validation = DataValidation(type="list", formula1=f"=Lists!$A$2:$A${len(active) + 1}", allow_blank=True)
            validation.error = "Choose one of the campus's personnel categories."
            sheet.add_data_validation(validation)
            validation.add(f"{letter}2:{letter}1048576")
        if target.startswith("custom:"):
            field = next(f for f in custom_fields if f.key == target[7:])
            if field.choices:
                choices = ",".join(field.choices)
                if len(choices) <= 250:
                    validation = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=True)
                    sheet.add_data_validation(validation)
                    validation.add(f"{letter}2:{letter}1048576")
    sheet.freeze_panes = "A2"

    guide = workbook.create_sheet("Instructions")
    lines: list[tuple[str, ...]] = [
        ("How to fill in the Personnel sheet",),
        ("1. Enter one person per row in the 'Personnel' sheet, below the header row. Keep the headers.",),
        ("2. Columns ending with * are required for everyone. Some categories require more (see below).",),
        ("3. Dates: YYYY-MM-DD (for example 2026-09-01) or real Excel dates.",),
        ("4. Phone numbers: 7 to 15 digits, optionally starting with +. Keep the column formatted as Text.",),
        ("5. Do not use formulas; type the values. Formulas are refused.",),
        ("6. Institutional ID and Email Address must be unique in this campus and in the file.",),
        ("7. The Personnel ID (PSN-...) is created by the system. Do not add organization or campus IDs.",),
        (
            "8. All or nothing: if any row has a blocking error, NO rows are imported. You get a copy of the file "
            "with every problem marked; fix it and upload it again.",
        ),
        ("9. Other column names are fine too: you can match your columns to these fields after uploading.",),
        (),
        ("Categories", "Required fields for this category"),
    ]
    for category in active:
        needed = [FIELDS_BY_KEY[f].label for f in category.required_fields if f in FIELDS_BY_KEY]
        lines.append((category.label, ", ".join(needed) or "Only Full Name"))
    if any(f.is_active for f in custom_fields):
        lines += [(), ("Custom fields", "Type", "Required", "Allowed values", "Used for")]
        for field in custom_fields:
            if not field.is_active:
                continue
            used_for = ", ".join(by_id[c].label for c in field.category_ids if c in by_id) or "All categories"
            lines.append(
                (
                    field.label,
                    field.field_type.value,
                    "Yes" if field.is_required else "No",
                    ", ".join(field.choices),
                    used_for,
                )
            )
    for line in lines:
        guide.append(list(line))
    for row in (1, 12):
        guide.cell(row=row, column=1).font = _BOLD
    guide.column_dimensions["A"].width = 60
    guide.column_dimensions["B"].width = 45

    examples = workbook.create_sheet("Examples")
    examples.append(["Illustrative values only. This sheet is never imported."])
    examples.append([label for _, label, _, _ in columns[:10]])
    for example in _EXAMPLES:
        examples.append(list(example))
    examples.cell(row=1, column=1).font = _BOLD

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
