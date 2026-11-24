import pytest

from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.services.registration_policy import validate_registration

VALID = {
    "organization_name": "Lakeside Institute",
    "admin_full_name": "Maryam Siddiqui",
    "username": "maryam.s",
    "campus_name": "Main Campus",
    "email": "maryam@lakeside.example.com",
    "phone": "+92 300 123-4567",
    "password": "Lakeside-pass-2026",
}


def test_valid_form_is_accepted_and_only_harmlessly_cleaned() -> None:
    applicant = validate_registration(
        **{**VALID, "admin_full_name": "  Maryam Siddiqui ", "email": " Maryam@Lakeside.EXAMPLE.com "}
    )
    assert applicant.organization_name == "Lakeside Institute"
    assert applicant.admin_full_name == "Maryam Siddiqui"
    assert applicant.email == "Maryam@lakeside.example.com"  # domain lower-cased, mailbox kept
    assert applicant.phone == "+923001234567"
    assert applicant.password == VALID["password"]
    assert "Lakeside-pass-2026" not in repr(applicant)


@pytest.mark.parametrize("name", ["lakeSide INSTITUTE", "École Supérieure", "Org 42", "A&B Holdings (Pvt.) Ltd."])
def test_organization_name_is_kept_exactly_as_entered(name: str) -> None:
    assert validate_registration(**{**VALID, "organization_name": name}).organization_name == name


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("organization_name", "", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "   ", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", " Lakeside", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "Lakeside ", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "Lake  side", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "Lake\tside", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "Lake side", "INVALID_ORGANIZATION_NAME"),  # no-break space
        ("organization_name", "Lake\u200bside", "INVALID_ORGANIZATION_NAME"),  # zero-width space
        ("organization_name", "X", "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "A" * 151, "INVALID_ORGANIZATION_NAME"),
        ("organization_name", "!!!", "INVALID_ORGANIZATION_NAME"),
        ("admin_full_name", "", "INVALID_FULL_NAME"),
        ("admin_full_name", "M", "INVALID_FULL_NAME"),
        ("admin_full_name", "12345", "INVALID_FULL_NAME"),
        ("admin_full_name", "Maryam\nSiddiqui", "INVALID_FULL_NAME"),
        ("username", "ma", "INVALID_USERNAME"),
        ("username", "maryam s", "INVALID_USERNAME"),
        ("username", "maryam\n", "INVALID_USERNAME"),
        ("username", "m" * 65, "INVALID_USERNAME"),
        ("email", "maryam", "INVALID_EMAIL"),
        ("email", "maryam@localhost", "INVALID_EMAIL"),
        ("email", "maryam@riverside.test", "INVALID_EMAIL"),  # reserved test domain
        ("phone", "", "INVALID_PHONE_NUMBER"),
        ("phone", "123456", "INVALID_PHONE_NUMBER"),
        ("phone", "+1234567890123456", "INVALID_PHONE_NUMBER"),
        ("phone", "0300-CALL-NOW", "INVALID_PHONE_NUMBER"),
        ("password", "Short-pw-1", "WEAK_PASSWORD"),
        ("password", "x" * 120 + "-1234567890", "WEAK_PASSWORD"),
        ("password", "no-digits-in-this-one", "WEAK_PASSWORD"),
        ("password", "123456789012345", "WEAK_PASSWORD"),
        ("password", "my-MARYAM.S-pass-1", "WEAK_PASSWORD"),
    ],
)
def test_each_invalid_field_is_refused_with_its_code(field: str, value: str, code: str) -> None:
    with pytest.raises(ValidationFailed) as error:
        validate_registration(**{**VALID, field: value})
    assert error.value.code == code
    assert value not in str(error.value) or not value.strip()


def test_password_equal_to_the_email_is_refused() -> None:
    with pytest.raises(ValidationFailed) as error:
        validate_registration(**{**VALID, "email": "abc1234567@example.com", "password": "ABC1234567@example.com"})
    assert error.value.code == "WEAK_PASSWORD"
