"""Organization personnel (decision D-016): people associated with one campus (students, faculty,
employees, contractors, visitors, ...). They are organizational records, not login accounts, and
a record or photograph is never proof of who appears on camera.

- **Personnel ID:** the system-generated ``personnel_code`` (``PSN-`` + 10 characters). Email and
  phone are never identifiers.
- **Institutional ID** (``external_id``): the organization's own ID, optional, unique per campus.
- **Category:** one of the campus's configurable categories. A category can make some optional
  canonical fields required (``required_fields``).
- **Custom attributes:** values for the campus's controlled custom fields only.
- Records are **deactivated**, never deleted, so history stays valid.
"""

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.domain.value_objects.location_path import LocationPath


class PersonnelStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CreatedVia(StrEnum):
    MANUAL = "MANUAL"
    IMPORT = "IMPORT"


class FieldType(StrEnum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"
    CHOICE = "CHOICE"
    EMAIL = "EMAIL"
    PHONE = "PHONE"


@dataclass(frozen=True, slots=True)
class CanonicalField:
    """A fixed column of the personnel record. ``always_required`` fields are required for every
    category; the others are optional unless the category's ``required_fields`` lists them."""

    key: str
    label: str
    kind: str  # TEXT, EMAIL, PHONE, DATE, CATEGORY, LOCATION
    always_required: bool = False
    max_length: int | None = None
    help: str = ""


CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField("full_name", "Full Name", "TEXT", True, 200, "The person's full name."),
    CanonicalField("category", "Personnel Category", "CATEGORY", True, None, "One of the campus's categories."),
    CanonicalField(
        "external_id",
        "Institutional ID",
        "TEXT",
        False,
        64,
        "Your organization's own ID (registration number, employee code). Unique in this campus.",
    ),
    CanonicalField("email", "Email Address", "EMAIL", False, 320, "Unique in this campus."),
    CanonicalField("phone", "Phone Number", "PHONE", False, None, "7 to 15 digits, optional leading +."),
    CanonicalField("department", "Department", "TEXT", False, 120),
    CanonicalField("designation", "Designation", "TEXT", False, 120),
    CanonicalField("joining_date", "Joining / Registration Date", "DATE", False, None, "YYYY-MM-DD"),
    CanonicalField("start_date", "Employment / Enrollment Start Date", "DATE", False, None, "YYYY-MM-DD"),
    CanonicalField("end_date", "Employment / Enrollment End Date", "DATE", False, None, "YYYY-MM-DD"),
    CanonicalField("address", "Address", "TEXT", False, 500),
    CanonicalField("emergency_contact_name", "Emergency Contact Name", "TEXT", False, 200),
    CanonicalField("emergency_contact_phone", "Emergency Contact Phone", "PHONE", False),
    CanonicalField("emergency_contact_relation", "Emergency Contact Relation", "TEXT", False, 60),
    CanonicalField("notes", "Additional Information", "TEXT", False, 2000),
    CanonicalField(
        "assigned_location",
        "Assigned Location",
        "LOCATION",
        False,
        None,
        "Optional building, floor or area of this campus.",
    ),
)
FIELDS_BY_KEY = {f.key: f for f in CANONICAL_FIELDS}

# Optional canonical fields a category may make required (same list as migration 0017).
REQUIRABLE_FIELDS = tuple(f.key for f in CANONICAL_FIELDS if not f.always_required)

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0 or 1


def generate_personnel_code() -> str:
    """A new Personnel ID, e.g. ``PSN-7KQ2MZX4AB``. Random, so it reveals nothing about order."""
    return "PSN-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))


@dataclass(frozen=True, slots=True)
class PersonnelCategory:
    id: UUID
    code: str
    label: str
    required_fields: tuple[str, ...] = ()
    description: str | None = None
    is_system: bool = False
    is_active: bool = True
    sort_order: int = 100


@dataclass(frozen=True, slots=True)
class CustomField:
    id: UUID
    key: str
    label: str
    field_type: FieldType
    choices: tuple[str, ...] = ()
    is_required: bool = False
    category_ids: tuple[UUID, ...] = ()  # empty: every category
    max_length: int = 255
    help_text: str | None = None
    is_active: bool = True

    def applies_to(self, category_id: UUID) -> bool:
        return not self.category_ids or category_id in self.category_ids


@dataclass(frozen=True, slots=True)
class PersonnelValues:
    """The canonical, cleaned values of one person (manual form or one import row)."""

    full_name: str
    category_id: UUID
    external_id: str | None = None
    email: str | None = None
    phone: str | None = None
    department: str | None = None
    designation: str | None = None
    joining_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    emergency_contact_relation: str | None = None
    notes: str | None = None
    custom_attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PersonnelSummary:
    """What a directory row shows. ``email`` and ``phone`` are filled for Admins only."""

    id: UUID
    personnel_code: str
    external_id: str | None
    full_name: str
    category_id: UUID
    category_code: str
    category_label: str
    department: str | None
    designation: str | None
    status: PersonnelStatus
    email: str | None = None
    phone: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    building_name: str | None = None
    floor_name: str | None = None
    area_name: str | None = None


@dataclass(frozen=True, slots=True)
class Personnel:
    """The full record (Admins only)."""

    id: UUID
    organization_id: UUID
    campus_id: UUID
    personnel_code: str
    values: PersonnelValues
    category: PersonnelCategory
    status: PersonnelStatus
    created_via: CreatedVia
    location: LocationPath | None = None
    import_batch_id: UUID | None = None
    has_photo: bool = False
    photo_path: str | None = None
    photo_content_type: str | None = None
    created_by_name: str | None = None
    created_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None
    deactivated_by_name: str | None = None
    deactivated_at: datetime | None = None
    deactivation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PersonnelFilter:
    text: str | None = None
    category_id: UUID | None = None
    status: PersonnelStatus | None = None
    department: str | None = None
    designation: str | None = None
    area_id: UUID | None = None  # people with active access to this restricted area
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AuditEntry:
    occurred_at: datetime
    action: str
    target_type: str
    target_id: UUID | None
    actor_role: str
    actor_display_id: str | None
    actor_name: str | None
    metadata: dict[str, Any]
