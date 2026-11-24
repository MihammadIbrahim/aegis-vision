"""Rules for the first Organization Admin registration form.

Each rule raises ``ValidationFailed`` with a stable ``code`` naming the field.

The organization name is never changed: a name with leading, trailing or repeated spaces
is refused with an explanation instead of being silently "fixed", because the exact name
is what later logins and join requests must match. Other fields get only harmless
clean-up: surrounding spaces are removed from the full name and email, and phone
formatting characters are dropped.
"""

import re
import unicodedata

from email_validator import EmailNotValidError, validate_email

from src.domain.entities.organization import RegistrationApplicant
from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.credential_policy import USERNAME_PATTERN

ORGANIZATION_NAME_LENGTH = (2, 150)
CAMPUS_NAME_LENGTH = (2, 100)
FULL_NAME_LENGTH = (2, 120)
PASSWORD_LENGTH = (12, 128)
EMAIL_MAX_LENGTH = 254

_PHONE_FORMATTING = re.compile(r"[\s().-]")
_PHONE_DIGITS = re.compile(r"\+?[0-9]{7,15}")


def _has_control_characters(value: str) -> bool:
    # Unicode "C*" categories: control, format (e.g. zero-width), unassigned, private use.
    return any(unicodedata.category(ch).startswith("C") for ch in value)


def check_organization_name(name: str) -> str:
    def fail(message: str) -> ValidationFailed:
        return ValidationFailed(message, code="INVALID_ORGANIZATION_NAME")

    low, high = ORGANIZATION_NAME_LENGTH
    if not name.strip():
        raise fail("Enter the organization name.")
    if name != name.strip():
        raise fail("The organization name must not start or end with a space.")
    if re.search(r"\s{2}", name) or any(ch.isspace() and ch != " " for ch in name):
        raise fail("Use single spaces between the words of the organization name.")
    if _has_control_characters(name):
        raise fail("The organization name contains characters that are not allowed.")
    if not low <= len(name) <= high:
        raise fail(f"The organization name must be {low} to {high} characters long.")
    if not any(ch.isalnum() for ch in name):
        raise fail("The organization name must contain letters or digits.")
    return name


def check_full_name(full_name: str) -> str:
    def fail(message: str) -> ValidationFailed:
        return ValidationFailed(message, code="INVALID_FULL_NAME")

    low, high = FULL_NAME_LENGTH
    cleaned = full_name.strip()
    if not cleaned:
        raise fail("Enter the Admin's full name.")
    if _has_control_characters(cleaned) or any(ch.isspace() and ch != " " for ch in cleaned):
        raise fail("The full name contains characters that are not allowed.")
    if not low <= len(cleaned) <= high:
        raise fail(f"The full name must be {low} to {high} characters long.")
    if not any(ch.isalpha() for ch in cleaned):
        raise fail("The full name must contain letters.")
    return cleaned


def check_username(username: str) -> str:
    if not re.fullmatch(USERNAME_PATTERN, username):
        raise ValidationFailed(
            "The username must be 3 to 64 letters, digits, '.', '_' or '-'.", code="INVALID_USERNAME"
        )
    return username


def check_email(email: str) -> str:
    """Returns the normalized address (domain in lower case)."""
    candidate = email.strip()
    try:
        result = validate_email(candidate, check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError:
        raise ValidationFailed("Enter a valid email address.", code="INVALID_EMAIL") from None
    if len(result.normalized) > EMAIL_MAX_LENGTH:
        raise ValidationFailed("The email address is too long.", code="INVALID_EMAIL")
    return result.normalized


def check_phone(phone: str) -> str:
    """Required but not verified (requirements Section 8). Stored as digits with an
    optional leading "+", e.g. "+92 300 123-4567" becomes "+923001234567"."""
    compact = _PHONE_FORMATTING.sub("", phone)
    if not _PHONE_DIGITS.fullmatch(compact):
        raise ValidationFailed(
            "Enter a phone number of 7 to 15 digits, optionally starting with +.", code="INVALID_PHONE_NUMBER"
        )
    return compact


def check_password(password: str, *, username: str, email: str) -> None:
    def fail(message: str) -> ValidationFailed:
        return ValidationFailed(message, code="WEAK_PASSWORD")

    low, high = PASSWORD_LENGTH
    if not low <= len(password) <= high:
        raise fail(f"The password must be {low} to {high} characters long.")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise fail("The password must contain at least one letter and one digit.")
    if username.lower() in password.lower():
        raise fail("The password must not contain the username.")
    if password.lower() == email.lower():
        raise fail("The password must not be the email address.")


def validate_registration(
    *,
    organization_name: str,
    campus_name: str,
    admin_full_name: str,
    username: str,
    email: str,
    phone: str,
    password: str,
) -> RegistrationApplicant:
    """Check every field, in form order, and return the cleaned-up values. The first campus is
    named by the applicant (decision D-021)."""
    name = check_organization_name(organization_name)
    first_campus = check_campus_name(campus_name)
    full_name = check_full_name(admin_full_name)
    checked_username = check_username(username)
    checked_email = check_email(email)
    checked_phone = check_phone(phone)
    check_password(password, username=checked_username, email=checked_email)
    return RegistrationApplicant(
        organization_name=name,
        campus_name=first_campus,
        admin_full_name=full_name,
        username=checked_username,
        email=checked_email,
        phone=checked_phone,
        password=password,
    )


def check_campus_name(name: str) -> str:
    """A campus / branch name: the first campus at registration (decision D-021) and the proposed
    campus of an "Already Registered Organization?" request. Surrounding spaces are removed and inner
    spaces collapsed (it is not a sign-in field)."""

    def fail(message: str) -> ValidationFailed:
        return ValidationFailed(message, code="INVALID_CAMPUS_NAME")

    low, high = CAMPUS_NAME_LENGTH
    cleaned = " ".join(name.split())
    if not cleaned:
        raise fail("Enter the proposed campus or branch name.")
    if _has_control_characters(cleaned):
        raise fail("The campus name contains characters that are not allowed.")
    if not low <= len(cleaned) <= high:
        raise fail(f"The campus name must be {low} to {high} characters long.")
    if not any(ch.isalnum() for ch in cleaned):
        raise fail("The campus name must contain letters or digits.")
    return cleaned
