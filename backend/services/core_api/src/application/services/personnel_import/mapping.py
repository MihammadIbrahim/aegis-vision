"""Column mapping: which spreadsheet column fills which personnel field (decision D-016).

Suggestions come, in this order, from: a saved mapping profile, an exact match with a field's key
or label, a known synonym, or a cautious fuzzy match. **Every suggestion is only a suggestion:**
the Admin confirms the whole mapping before anything is validated, and a column with no decision
is never dropped silently. Each column is mapped to one target:

* a canonical field key (``full_name``, ``external_id``, ...);
* ``assigned_building`` / ``assigned_floor`` / ``assigned_area`` (location names);
* ``custom:<key>`` for an active custom field of the campus;
* ``IGNORE``: the Admin has looked at the column and chosen not to import it.
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.domain.entities.personnel import CANONICAL_FIELDS, CustomField
from src.domain.exceptions.domain_errors import ValidationFailed

IGNORE = "IGNORE"
LOCATION_TARGETS = {
    "assigned_building": "Assigned Building",
    "assigned_floor": "Assigned Floor",
    "assigned_area": "Assigned Area",
}
REQUIRED_TARGETS = ("full_name", "category")
FUZZY_THRESHOLD = 0.82


class Confidence(StrEnum):
    PROFILE = "PROFILE"  # from a saved mapping profile
    EXACT = "EXACT"  # the field's key or label
    SYNONYM = "SYNONYM"  # a known alternative name
    FUZZY = "FUZZY"  # similar spelling: check it
    NONE = "NONE"


# Normalized header → canonical target. "Organization ID" and "Campus ID" are deliberately absent:
# organization and campus always come from the signed-in Admin, never from the file.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "full_name": (
        "name",
        "full name",
        "person name",
        "student name",
        "employee name",
        "staff name",
        "faculty name",
        "member name",
        "visitor name",
        "contractor name",
        "personnel name",
    ),
    "category": (
        "category",
        "type",
        "person type",
        "personnel type",
        "personnel category",
        "member type",
        "role",
        "person category",
    ),
    "external_id": (
        "id",
        "institutional id",
        "external id",
        "student id",
        "employee id",
        "employee code",
        "emp id",
        "emp code",
        "staff id",
        "faculty id",
        "registration number",
        "registration no",
        "reg no",
        "reg number",
        "roll number",
        "roll no",
        "enrollment number",
        "enrolment number",
        "badge number",
        "badge id",
        "card number",
        "id number",
        "member id",
    ),
    "email": ("email", "email address", "e mail", "mail", "official email", "work email"),
    "phone": (
        "phone",
        "phone number",
        "mobile",
        "mobile number",
        "mobile no",
        "cell",
        "cell number",
        "contact",
        "contact number",
        "contact no",
        "telephone",
        "tel",
    ),
    "department": ("department", "dept", "faculty", "division", "section", "unit", "school"),
    "designation": ("designation", "job title", "title", "position", "post", "rank"),
    "joining_date": (
        "joining date",
        "date of joining",
        "registration date",
        "admission date",
        "hire date",
        "date joined",
    ),
    "start_date": (
        "start date",
        "enrollment start",
        "enrolment start",
        "employment start",
        "contract start",
        "valid from",
    ),
    "end_date": (
        "end date",
        "expiry date",
        "expiry",
        "valid until",
        "contract end",
        "enrollment end",
        "employment end",
        "graduation date",
    ),
    "address": ("address", "home address", "residential address", "postal address"),
    "emergency_contact_name": ("emergency contact", "emergency contact name", "next of kin"),
    "emergency_contact_phone": (
        "emergency phone",
        "emergency contact phone",
        "emergency contact number",
        "emergency number",
    ),
    "emergency_contact_relation": ("relation", "relationship", "emergency contact relation"),
    "notes": ("notes", "remarks", "comments", "additional information"),
    "assigned_building": ("building", "assigned building", "block"),
    "assigned_floor": ("floor", "assigned floor"),
    "assigned_area": ("area", "room", "assigned area", "office", "zone"),
}


def normalize_header(header: str) -> str:
    """'Student ID *' → 'student id'; 'E-mail_Address' → 'e mail address'."""
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


@dataclass(frozen=True, slots=True)
class Suggestion:
    target: str | None
    confidence: Confidence


def available_targets(custom_fields: Sequence[CustomField]) -> dict[str, str]:
    """target → label, for the mapping dropdowns."""
    targets = {f.key: f.label for f in CANONICAL_FIELDS if f.key != "assigned_location"}
    targets.update(LOCATION_TARGETS)
    targets.update({f"custom:{f.key}": f.label for f in custom_fields if f.is_active})
    return targets


def suggest(
    headers: Sequence[str], custom_fields: Sequence[CustomField], profile: Mapping[str, str] | None = None
) -> list[Suggestion]:
    targets = available_targets(custom_fields)
    exact: dict[str, str] = {}
    for target, label in targets.items():
        exact.setdefault(normalize_header(label), target)
        exact.setdefault(normalize_header(target.removeprefix("custom:")), target)
    synonyms = {normalize_header(name): target for target, names in _SYNONYMS.items() for name in names}
    candidates = {**synonyms, **exact}
    used: set[str] = set()
    result: list[Suggestion] = []
    for header in headers:
        key = normalize_header(header)
        suggestion = Suggestion(None, Confidence.NONE)
        if profile and key in profile and (profile[key] == IGNORE or profile[key] in targets):
            suggestion = Suggestion(profile[key], Confidence.PROFILE)
        elif key in exact:
            suggestion = Suggestion(exact[key], Confidence.EXACT)
        elif key in synonyms:
            suggestion = Suggestion(synonyms[key], Confidence.SYNONYM)
        elif key:
            close = difflib.get_close_matches(key, list(candidates), n=1, cutoff=FUZZY_THRESHOLD)
            if close:
                suggestion = Suggestion(candidates[close[0]], Confidence.FUZZY)
        # The same target is suggested for one column only (the first); the Admin decides the rest.
        picked = suggestion.target
        if picked is not None and picked != IGNORE:
            if picked in used:
                suggestion = Suggestion(None, Confidence.NONE)
            else:
                used.add(picked)
        result.append(suggestion)
    return result


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """The confirmed decision for every column: column index → target (or IGNORE)."""

    targets: tuple[str, ...]

    def column_of(self, target: str) -> int | None:
        try:
            return self.targets.index(target)
        except ValueError:
            return None

    def as_profile(self, headers: Sequence[str]) -> dict[str, str]:
        return {normalize_header(h): t for h, t in zip(headers, self.targets, strict=True) if normalize_header(h)}


def confirm_mapping(
    headers: Sequence[str], decisions: Mapping[int, str], custom_fields: Sequence[CustomField]
) -> ColumnMapping:
    """Checks the Admin's mapping. Every column needs a decision, required fields must be mapped,
    and no field may come from two columns. Problems are listed per column in ``errors``."""
    targets = available_targets(custom_fields)
    problems: list[dict[str, object]] = []
    chosen: list[str] = []
    seen: dict[str, int] = {}
    for index, header in enumerate(headers):
        target = decisions.get(index)
        if target is None:
            problems.append(
                {
                    "column_index": index,
                    "column_name": header,
                    "code": "UNMAPPED_COLUMN",
                    "message": "Choose a field for this column, or choose to ignore it.",
                }
            )
            chosen.append(IGNORE)
            continue
        if target != IGNORE and target not in targets:
            problems.append(
                {
                    "column_index": index,
                    "column_name": header,
                    "code": "UNKNOWN_TARGET",
                    "message": "This field does not exist (or the custom field is not active).",
                }
            )
        elif target != IGNORE and target in seen:
            problems.append(
                {
                    "column_index": index,
                    "column_name": header,
                    "code": "DUPLICATE_TARGET",
                    "message": f"'{targets[target]}' is already filled from another column.",
                }
            )
        else:
            seen[target] = index
        chosen.append(target)
    extra = [i for i in decisions if i < 0 or i >= len(headers)]
    if extra:
        problems.append({"code": "UNKNOWN_COLUMN", "message": "The mapping refers to columns the file does not have."})
    for required in REQUIRED_TARGETS:
        if required not in seen:
            problems.append(
                {
                    "field": required,
                    "code": "REQUIRED_FIELD_UNMAPPED",
                    "message": f"'{targets[required]}' must be mapped to a column.",
                }
            )
    if problems:
        raise ValidationFailed("The column mapping is not complete.", code="MAPPING_INCOMPLETE", errors=problems)
    return ColumnMapping(tuple(chosen))
