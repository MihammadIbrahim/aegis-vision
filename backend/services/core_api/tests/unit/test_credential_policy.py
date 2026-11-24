import pytest

from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.credential_policy import (
    ensure_acceptable_new_password,
    ensure_valid_username,
)


@pytest.mark.parametrize(
    ("new", "confirm", "message"),
    [
        ("Strong-pass-2026", "Strong-pass-2027", "do not match"),
        ("short1", "short1", "12 to 128"),
        ("only-letters-here", "only-letters-here", "letter and one digit"),
        ("123456789012345", "123456789012345", "letter and one digit"),
        ("my-admin-password-1", "my-admin-password-1", "must not contain the username"),
    ],
)
def test_weak_or_mismatched_passwords_are_refused(new: str, confirm: str, message: str) -> None:
    with pytest.raises(ValidationFailed, match=message):
        ensure_acceptable_new_password(new, confirm, "admin")


def test_good_password_is_accepted() -> None:
    ensure_acceptable_new_password("Strong-pass-2026", "Strong-pass-2026", "admin")


@pytest.mark.parametrize("username", ["ab", "has space", "x" * 65, "semi;colon"])
def test_bad_usernames_are_refused(username: str) -> None:
    with pytest.raises(ValidationFailed):
        ensure_valid_username(username)
