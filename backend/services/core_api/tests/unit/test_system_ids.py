import pytest

from src.domain.value_objects.enums import UserRole
from src.domain.value_objects.system_id import (
    DISPLAY_ID_PATTERN,
    display_id_matches_role,
    generate_display_id,
)


@pytest.mark.parametrize(
    ("role", "prefix"), [(UserRole.ADMIN, "ADM-"), (UserRole.SUPERVISOR, "SUP-"), (UserRole.GUARD, "GRD-")]
)
def test_generated_id_has_role_prefix_and_11_digits(role: UserRole, prefix: str) -> None:
    for _ in range(200):
        display_id = generate_display_id(role)
        assert display_id.startswith(prefix)
        assert DISPLAY_ID_PATTERN.match(display_id)
        assert display_id_matches_role(display_id, role)


def test_id_does_not_match_another_role() -> None:
    assert not display_id_matches_role("ADM-00000000001", UserRole.GUARD)
    assert not display_id_matches_role("ADM-0001", UserRole.ADMIN)
