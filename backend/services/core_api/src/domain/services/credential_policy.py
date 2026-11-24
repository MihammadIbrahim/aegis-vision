"""Rules for new Super Admin usernames and passwords (the initial seed values are checked separately)."""

import re

from src.domain.exceptions.domain_errors import ValidationFailed

MIN_LENGTH = 12
MAX_LENGTH = 128


def ensure_acceptable_new_password(new_password: str, confirm_new_password: str, username: str) -> None:
    if new_password != confirm_new_password:
        raise ValidationFailed("The new password and its confirmation do not match.")
    if not MIN_LENGTH <= len(new_password) <= MAX_LENGTH:
        raise ValidationFailed(f"The new password must be {MIN_LENGTH} to {MAX_LENGTH} characters long.")
    if not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
        raise ValidationFailed("The new password must contain at least one letter and one digit.")
    if username.lower() in new_password.lower():
        raise ValidationFailed("The new password must not contain the username.")


USERNAME_PATTERN = r"^[A-Za-z0-9_.-]{3,64}$"


def ensure_valid_username(username: str) -> None:
    if not re.fullmatch(USERNAME_PATTERN, username):
        raise ValidationFailed("The username must be 3 to 64 letters, digits, '.', '_' or '-'.")
