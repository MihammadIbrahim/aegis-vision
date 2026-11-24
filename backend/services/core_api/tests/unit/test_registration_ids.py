"""Organization ID and the new Admin/Supervisor ID: both 6 capital letters + 11 digits."""

import re

from src.domain.value_objects.enums import UserRole
from src.domain.value_objects.organization_code import (
    generate_organization_code,
    is_organization_code,
)
from src.domain.value_objects.system_id import (
    generate_display_id,
    generate_staff_id,
    is_staff_id,
)


def test_organization_codes_have_the_approved_format() -> None:
    for _ in range(200):
        code = generate_organization_code()
        assert re.fullmatch(r"[A-Z]{6}[0-9]{11}", code) and len(code) == 17
        assert is_organization_code(code)


def test_organization_codes_are_random_and_unique() -> None:
    codes = {generate_organization_code() for _ in range(5000)}
    assert len(codes) == 5000
    # Every position takes many values: letters and digits are random, not a counter.
    for position in range(6):
        assert len({code[position] for code in codes}) == 26
    for position in range(6, 17):
        assert len({code[position] for code in codes}) == 10


def test_organization_code_check_is_strict() -> None:
    for bad in [
        "abcdef12345678901",
        "ABCDE12345678901",
        "ABCDEFG12345678901",
        "ABCDEF1234567890",
        "ABCDEF123456789012",
        "ABCDÉF12345678901",
        "ABCDEF12345678901\n",
        "ABC-DEF12345678901",
        "ORG-1234567890",
        "",
    ]:
        assert not is_organization_code(bad)


def test_organization_ids_issued_in_the_earlier_format_stay_valid() -> None:
    assert is_organization_code("ORG-48271930561")
    assert not generate_organization_code().startswith("ORG-")


def test_staff_ids_have_the_approved_format() -> None:
    for _ in range(200):
        staff_id = generate_staff_id()
        assert re.fullmatch(r"[A-Z]{6}[0-9]{11}", staff_id)
        assert is_staff_id(staff_id)


def test_staff_ids_are_random_and_unique() -> None:
    ids = {generate_staff_id() for _ in range(5000)}
    assert len(ids) == 5000
    assert len({staff_id[0] for staff_id in ids}) > 20  # letters are random too
    assert len({staff_id[6] for staff_id in ids}) == 10


def test_staff_id_check_is_strict() -> None:
    for bad in ["kqmtzx48271930561", "KQMTZ48271930561", "KQMTZX4827193056", "ADM-48271930561", "KQMTZX48271930561\n"]:
        assert not is_staff_id(bad)


def test_organization_ids_and_staff_ids_share_one_shape() -> None:
    """Both are 6 letters + 11 digits, so the format alone cannot tell them apart: every API
    field must say which kind of ID it holds."""
    assert is_staff_id(generate_organization_code())
    assert is_organization_code(generate_staff_id())


def test_original_display_id_format_is_unchanged() -> None:
    assert re.fullmatch(r"ADM-[0-9]{11}", generate_display_id(UserRole.ADMIN))
