import re

from src.domain.value_objects.system_id import generate_guard_id, is_guard_id


def test_guard_ids_are_27_characters_random_and_unique() -> None:
    ids = {generate_guard_id() for _ in range(3000)}
    assert len(ids) == 3000
    assert all(re.fullmatch(r"[A-Z]{6}[0-9]{21}", i) and is_guard_id(i) for i in ids)
    assert len({i[0] for i in ids}) == 26 and len({i[-1] for i in ids}) == 10
    for bad in ("GRD-12345678901", "abcdef123456789012345678901", "ABCDEF12345678901", "ABCDEF1234567890123456789012"):
        assert not is_guard_id(bad)
