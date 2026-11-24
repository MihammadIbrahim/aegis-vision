"""The personnel validation rules shared by manual registration and the import (decision D-016)."""

from datetime import date, datetime
from uuid import uuid4

import pytest

from src.domain.entities.personnel import (
    CustomField,
    FieldType,
    PersonnelCategory,
    generate_personnel_code,
)
from src.domain.services.personnel_rules import PersonnelRules, Severity

TODAY = date(2026, 11, 1)
STUDENT = PersonnelCategory(uuid4(), "STUDENT", "Student", ("external_id",))
VISITOR = PersonnelCategory(uuid4(), "VISITOR", "Visitor", ("end_date",))
OTHER = PersonnelCategory(uuid4(), "OTHER", "Other")
RETIRED = PersonnelCategory(uuid4(), "RETIRED", "Retired", is_active=False)
PROGRAM = CustomField(uuid4(), "program", "Academic Program", FieldType.CHOICE, ("BSSE", "BSCS"), True, (STUDENT.id,))
SEMESTER = CustomField(uuid4(), "semester", "Semester", FieldType.NUMBER)
HOSTEL = CustomField(uuid4(), "hostel", "Hostel Resident", FieldType.BOOLEAN)


def rules() -> PersonnelRules:
    return PersonnelRules([STUDENT, VISITOR, OTHER, RETIRED], [PROGRAM, SEMESTER, HOSTEL])


def codes(issues, severity=None):  # type: ignore[no-untyped-def]
    found = [(i.field, i.code) for i in issues if severity is None or i.severity is severity]
    return sorted(found, key=lambda pair: (pair[0] or "", pair[1]))


def test_a_valid_student_is_cleaned() -> None:
    values, issues = rules().validate(
        {
            "full_name": "  Ayesha Khan ",
            "category": "student",
            "external_id": 1001.0,  # Excel keeps whole numbers as floats
            "email": "Ayesha.Khan@Example.COM",
            "phone": "0300-123 4567",
            "start_date": datetime(2026, 9, 1),
            "end_date": "2030-06-30",
            "custom:program": "bsse",
            "custom:semester": "3",
            "custom:hostel": "Yes",
        },
        today=TODAY,
    )
    assert codes(issues) == []
    assert values is not None
    assert values.full_name == "Ayesha Khan" and values.category_id == STUDENT.id and values.external_id == "1001"
    assert values.email == "ayesha.khan@example.com" and values.phone == "03001234567"
    assert values.start_date == date(2026, 9, 1) and values.end_date == date(2030, 6, 30)
    assert values.custom_attributes == {"program": "BSSE", "semester": 3, "hostel": True}


def test_required_fields_depend_on_the_category() -> None:
    _, issues = rules().validate({"full_name": "A", "category": "Student", "custom:program": "BSSE"}, today=TODAY)
    assert ("external_id", "REQUIRED_FOR_CATEGORY") in codes(issues, Severity.ERROR)
    # A visitor needs an end date but no institutional ID; nothing student-specific is forced on them.
    values, issues = rules().validate({"full_name": "V", "category": "VISITOR", "phone": "03001234567"}, today=TODAY)
    assert values is None and codes(issues, Severity.ERROR) == [("end_date", "REQUIRED_FOR_CATEGORY")]
    values, issues = rules().validate({"full_name": "V", "category": "Other", "phone": "03001234567"}, today=TODAY)
    assert values is not None and codes(issues) == []


def test_name_and_category_are_always_required_and_categories_must_be_known_and_active() -> None:
    values, issues = rules().validate({"full_name": " ", "category": ""}, today=TODAY)
    assert values is None
    assert {("full_name", "REQUIRED"), ("category", "REQUIRED")} <= set(codes(issues))
    _, issues = rules().validate({"full_name": "X", "category": "Studnt"}, today=TODAY)
    assert ("category", "UNKNOWN_CATEGORY") in codes(issues)
    _, issues = rules().validate({"full_name": "X", "category": "Retired"}, today=TODAY)
    assert ("category", "CATEGORY_INACTIVE") in codes(issues)
    values, _ = rules().validate({"full_name": "X", "category": OTHER.id, "email": "x@example.com"}, today=TODAY)
    assert values is not None and values.category_id == OTHER.id  # the form sends the category's ID


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("email", "not-an-email", "INVALID_EMAIL"),
        ("phone", "12345", "INVALID_PHONE"),
        ("phone", "+92 300 12345678901234", "INVALID_PHONE"),
        ("start_date", "01/09/2026", "INVALID_DATE"),
        ("start_date", "2026-13-01", "INVALID_DATE"),
        ("full_name", "x" * 201, "TOO_LONG"),
        ("department", "IT\x00", "INVALID_CHARACTERS"),
        ("custom:semester", "three", "INVALID_CUSTOM_VALUE"),
        ("custom:hostel", "maybe", "INVALID_CUSTOM_VALUE"),
        ("custom:unknown", "x", "UNKNOWN_CUSTOM_FIELD"),
    ],
)
def test_formats_are_checked_and_messages_never_repeat_the_value(field: str, value: str, code: str) -> None:
    raw = {"full_name": "Valid Name", "category": "Other", "email": "ok@example.com", field: value}
    values, issues = rules().validate(raw, today=TODAY)
    assert values is None and (field, code) in codes(issues, Severity.ERROR)
    for issue in issues:
        assert value not in issue.message


def test_warnings_do_not_block() -> None:
    values, issues = rules().validate(
        {"full_name": "Old Contractor", "category": "Visitor", "end_date": "2020-01-01", "phone": 3001234567},
        today=TODAY,
    )
    assert values is not None
    assert codes(issues, Severity.WARNING) == [("end_date", "END_DATE_PAST"), ("phone", "PHONE_STORED_AS_NUMBER")]
    values, issues = rules().validate({"full_name": "No Contact", "category": "Other"}, today=TODAY)
    assert values is not None and codes(issues) == [(None, "NO_CONTACT")]


def test_dates_must_be_in_order_and_custom_fields_follow_their_categories() -> None:
    _, issues = rules().validate(
        {
            "full_name": "A",
            "category": "Other",
            "phone": "03001234567",
            "start_date": "2026-02-01",
            "end_date": "2026-01-01",
        },
        today=TODAY,
    )
    assert ("end_date", "END_BEFORE_START") in codes(issues, Severity.ERROR)
    # Required only for students:
    _, issues = rules().validate({"full_name": "S", "category": "Student", "external_id": "S1"}, today=TODAY)
    assert ("custom:program", "REQUIRED") in codes(issues, Severity.ERROR)
    values, issues = rules().validate(
        {"full_name": "O", "category": "Other", "phone": "03001234567", "custom:program": "BSCS"}, today=TODAY
    )
    assert values is not None and values.custom_attributes == {"program": "BSCS"}  # kept, not silently dropped
    assert ("custom:program", "CUSTOM_FIELD_NOT_FOR_CATEGORY") in codes(issues, Severity.WARNING)


def test_assigned_location_can_be_required_by_a_category() -> None:
    guard_post = PersonnelCategory(uuid4(), "SECURITY", "Security", ("assigned_location",))
    r = PersonnelRules([guard_post], [])
    _, issues = r.validate({"full_name": "G", "category": "Security", "phone": "03001234567"}, today=TODAY)
    assert codes(issues) == [("assigned_location", "REQUIRED_FOR_CATEGORY")]
    values, _ = r.validate(
        {"full_name": "G", "category": "Security", "phone": "03001234567"}, today=TODAY, location_given=True
    )
    assert values is not None


def test_personnel_ids_are_random_and_well_formed() -> None:
    ids = {generate_personnel_code() for _ in range(2000)}
    assert len(ids) == 2000
    for value in ids:
        assert value.startswith("PSN-") and len(value) == 14
        assert not set(value[4:]) & set("IO01")
