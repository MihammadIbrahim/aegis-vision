"""Validation and cleaning of personnel values, shared by manual registration and the Excel/CSV
import (decision D-016): one set of rules, so a record that can be typed in the form can be
imported, and the other way round.

Input values may be strings (form, CSV) or Excel cell values (numbers, dates, booleans). Each
problem is a ``FieldIssue``: ``ERROR`` blocks the record; ``WARNING`` is shown but allowed.
Messages never repeat the value itself, so they can be stored and logged safely.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

from email_validator import EmailNotValidError, validate_email

from src.domain.entities.personnel import (
    CANONICAL_FIELDS,
    FIELDS_BY_KEY,
    CustomField,
    FieldType,
    PersonnelCategory,
    PersonnelValues,
)


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class FieldIssue:
    field: str | None  # canonical key, "custom:<key>", or None for the whole record
    code: str
    severity: Severity
    message: str


_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"^\+?[0-9]{7,15}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0"}


def label_of(field_key: str, custom: Mapping[str, CustomField] | None = None) -> str:
    if field_key.startswith("custom:"):
        definition = (custom or {}).get(field_key[7:])
        return definition.label if definition else field_key[7:]
    known = FIELDS_BY_KEY.get(field_key)
    return known.label if known else field_key


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_text(value: Any) -> str:
    """Excel stores 1001 as 1001.0; keep whole numbers whole."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def clean_phone(value: Any) -> tuple[str | None, str | None]:
    """(normalized phone, error message)."""
    text = _PHONE_SEPARATORS.sub("", _as_text(value).strip())
    if not _PHONE.match(text):
        return None, "must have 7 to 15 digits, optionally starting with +."
    return text, None


def clean_email(value: Any) -> tuple[str | None, str | None]:
    text = _as_text(value).strip()
    if len(text) > 320:
        return None, "is too long."
    try:
        normalized = validate_email(text, check_deliverability=False).normalized
    except EmailNotValidError:
        return None, "is not a valid email address."
    return normalized.lower(), None


def clean_date(value: Any) -> tuple[date | None, str | None]:
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    text = _as_text(value).strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None, "must be a date in the form YYYY-MM-DD."
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None, "must be a date in the form YYYY-MM-DD."
    return parsed, None


class PersonnelRules:
    """The campus's validation rules: its categories and custom fields."""

    def __init__(self, categories: Sequence[PersonnelCategory], custom_fields: Sequence[CustomField]) -> None:
        self.categories = list(categories)
        self.custom_fields = {f.key: f for f in custom_fields if f.is_active}
        self._by_name: dict[str, PersonnelCategory] = {}
        for category in self.categories:
            self._by_name.setdefault(category.code.lower(), category)
            self._by_name.setdefault(category.label.lower(), category)
        self._by_id = {c.id: c for c in self.categories}

    def category(self, value: Any) -> tuple[PersonnelCategory | None, FieldIssue | None]:
        """By ID (form), code or label (import), ignoring capitalization."""
        if isinstance(value, UUID):
            found = self._by_id.get(value)
        else:
            text = _as_text(value).strip()
            try:
                found = self._by_id.get(UUID(text))
            except ValueError:
                found = self._by_name.get(text.lower())
        if found is None:
            allowed = ", ".join(c.label for c in self.categories if c.is_active)
            return None, FieldIssue(
                "category", "UNKNOWN_CATEGORY", Severity.ERROR, f"Not a personnel category. Allowed: {allowed}."
            )
        if not found.is_active:
            return None, FieldIssue(
                "category", "CATEGORY_INACTIVE", Severity.ERROR, f"The category '{found.label}' is not in use."
            )
        return found, None

    def validate(
        self, raw: Mapping[str, Any], *, today: date, location_given: bool = False
    ) -> tuple[PersonnelValues | None, list[FieldIssue]]:
        """Checks and cleans one record. ``raw`` keys: canonical keys and ``custom:<key>``.
        Returns the values (None if any ERROR) and every issue found."""
        issues: list[FieldIssue] = []
        clean: dict[str, Any] = {}

        def error(key: str, code: str, message: str) -> None:
            issues.append(FieldIssue(key, code, Severity.ERROR, f"{label_of(key, self.custom_fields)} {message}"))

        def warn(key: str | None, code: str, message: str) -> None:
            issues.append(FieldIssue(key, code, Severity.WARNING, message))

        category, problem = (None, None)
        if is_blank(raw.get("category")):
            error("category", "REQUIRED", "is required.")
        else:
            category, problem = self.category(raw["category"])
            if problem:
                issues.append(problem)

        required = {f.key for f in CANONICAL_FIELDS if f.always_required}
        if category is not None:
            required |= set(category.required_fields)

        for definition in CANONICAL_FIELDS:
            key = definition.key
            if key in ("category", "assigned_location"):
                continue
            value = raw.get(key)
            if is_blank(value):
                if key in required:
                    suffix = "" if definition.always_required or category is None else f" for {category.label}"
                    error(
                        key,
                        "REQUIRED" if definition.always_required else "REQUIRED_FOR_CATEGORY",
                        f"is required{suffix}.",
                    )
                continue
            if definition.kind == "TEXT":
                text = _as_text(value).strip()
                if _CONTROL.search(text):
                    error(key, "INVALID_CHARACTERS", "contains control characters.")
                elif definition.max_length and len(text) > definition.max_length:
                    error(key, "TOO_LONG", f"must be at most {definition.max_length} characters.")
                else:
                    clean[key] = text
            elif definition.kind == "EMAIL":
                email, message = clean_email(value)
                if message:
                    error(key, "INVALID_EMAIL", message)
                else:
                    clean[key] = email
            elif definition.kind == "PHONE":
                phone, message = clean_phone(value)
                if message:
                    error(key, "INVALID_PHONE", message)
                else:
                    clean[key] = phone
                    if isinstance(value, int | float) and not isinstance(value, bool):
                        warn(
                            key,
                            "PHONE_STORED_AS_NUMBER",
                            f"{definition.label} was a number in the spreadsheet; a leading 0 may have been lost.",
                        )
            elif definition.kind == "DATE":
                parsed, message = clean_date(value)
                if message:
                    error(key, "INVALID_DATE", message)
                else:
                    clean[key] = parsed

        if "assigned_location" in required and not location_given:
            suffix = f" for {category.label}" if category is not None else ""
            error("assigned_location", "REQUIRED_FOR_CATEGORY", f"is required{suffix}.")

        start, end = clean.get("start_date"), clean.get("end_date")
        if start and end and end < start:
            error("end_date", "END_BEFORE_START", "must not be before the start date.")
        elif end and end < today:
            warn("end_date", "END_DATE_PAST", "The end date is in the past.")
        if not issues_for(issues, "email") and not issues_for(issues, "phone"):
            if "email" not in clean and "phone" not in clean:
                warn(None, "NO_CONTACT", "No email address or phone number is given.")

        clean["custom_attributes"] = self._custom(raw, category, issues)

        if category is None or any(i.severity is Severity.ERROR for i in issues):
            return None, issues
        return PersonnelValues(category_id=category.id, **clean), issues

    def _custom(self, raw: Mapping[str, Any], category: PersonnelCategory | None, issues: list[FieldIssue]) -> dict:
        values: dict[str, Any] = {}
        for name in raw:
            if name.startswith("custom:") and name[7:] not in self.custom_fields:
                issues.append(
                    FieldIssue(
                        name,
                        "UNKNOWN_CUSTOM_FIELD",
                        Severity.ERROR,
                        f"'{name[7:]}' is not a custom field of this campus.",
                    )
                )
        for key, definition in self.custom_fields.items():
            name = f"custom:{key}"
            value = raw.get(name)
            applies = category is None or definition.applies_to(category.id)
            if is_blank(value):
                if definition.is_required and applies and category is not None:
                    issues.append(FieldIssue(name, "REQUIRED", Severity.ERROR, f"{definition.label} is required."))
                continue
            cleaned, message = clean_custom_value(definition, value)
            if message:
                issues.append(FieldIssue(name, "INVALID_CUSTOM_VALUE", Severity.ERROR, f"{definition.label} {message}"))
                continue
            if not applies and category is not None:
                issues.append(
                    FieldIssue(
                        name,
                        "CUSTOM_FIELD_NOT_FOR_CATEGORY",
                        Severity.WARNING,
                        f"{definition.label} is not used for {category.label}; it is kept anyway.",
                    )
                )
            values[key] = cleaned
        return values


def issues_for(issues: Sequence[FieldIssue], key: str) -> list[FieldIssue]:
    return [i for i in issues if i.field == key]


def clean_custom_value(definition: CustomField, value: Any) -> tuple[Any, str | None]:
    """The stored JSON value of a custom field, or an error message (without the value)."""
    kind = definition.field_type
    if kind is FieldType.TEXT:
        text = _as_text(value).strip()
        if _CONTROL.search(text):
            return None, "contains control characters."
        if len(text) > definition.max_length:
            return None, f"must be at most {definition.max_length} characters."
        return text, None
    if kind is FieldType.NUMBER:
        if isinstance(value, bool):
            return None, "must be a number."
        try:
            number = Decimal(_as_text(value).strip().replace(",", ""))
        except InvalidOperation:
            return None, "must be a number."
        if not number.is_finite():
            return None, "must be a number."
        return (int(number) if number == number.to_integral_value() else float(number)), None
    if kind is FieldType.DATE:
        parsed, message = clean_date(value)
        return (None, message) if message else (parsed.isoformat() if parsed else None, None)
    if kind is FieldType.BOOLEAN:
        if isinstance(value, bool):
            return value, None
        text = _as_text(value).strip().lower()
        if text in _TRUE:
            return True, None
        if text in _FALSE:
            return False, None
        return None, "must be Yes or No."
    if kind is FieldType.CHOICE:
        text = _as_text(value).strip().lower()
        for choice in definition.choices:
            if choice.lower() == text:
                return choice, None
        return None, f"must be one of: {', '.join(definition.choices)}."
    if kind is FieldType.EMAIL:
        return clean_email(value)
    if kind is FieldType.PHONE:
        return clean_phone(value)
    return None, "has an unsupported type."
