"""Personnel, import and restricted-area access bodies (decision D-016).

Organization and campus never appear in a request: they always come from the signed-in user.
Supervisors receive the basic shapes only (no contact details, address, notes, custom attributes or
photo)."""

from datetime import date, datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.application.dtos.location_dto import (
    LocationPathResponse,
    LocationSelectionRequest,
)
from src.application.use_cases.personnel_use_cases import (
    CategoryDetails,
    CustomFieldDetails,
    PersonnelInput,
)
from src.domain.entities.area_access import (
    AccessGrant,
    AccessSchedule,
    GrantTerms,
    RestrictedArea,
)
from src.domain.entities.personnel import (
    CANONICAL_FIELDS,
    REQUIRABLE_FIELDS,
    AuditEntry,
    CustomField,
    FieldType,
    Personnel,
    PersonnelCategory,
    PersonnelSummary,
)
from src.domain.repositories.i_personnel_repo import ImportBatch, StoredIssue
from src.domain.services.personnel_rules import FieldIssue

CustomValue = str | int | float | bool | None


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Personnel ----------------------------------------------------------------------------------------------


class PersonnelRequest(_Request):
    """Register or replace a person's details. Field rules (required per category, formats) are
    checked by the server; problems come back in ``errors``."""

    full_name: str = Field(max_length=400)
    category_id: UUID
    external_id: str | None = Field(default=None, max_length=200, description="Institutional ID (optional)")
    email: str | None = Field(default=None, max_length=400)
    phone: str | None = Field(default=None, max_length=60)
    department: str | None = Field(default=None, max_length=300)
    designation: str | None = Field(default=None, max_length=300)
    joining_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    address: str | None = Field(default=None, max_length=1000)
    emergency_contact_name: str | None = Field(default=None, max_length=400)
    emergency_contact_phone: str | None = Field(default=None, max_length=60)
    emergency_contact_relation: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    custom_attributes: dict[str, CustomValue] = Field(default_factory=dict, max_length=200)
    location: LocationSelectionRequest | None = Field(default=None, description="Optional assigned location")

    def to_input(self) -> PersonnelInput:
        values: dict[str, Any] = self.model_dump(exclude={"category_id", "custom_attributes", "location"})
        values["category"] = self.category_id
        return PersonnelInput(
            values, dict(self.custom_attributes), None if self.location is None else self.location.to_selection()
        )


class CategoryRef(_Response):
    id: UUID
    code: str
    label: str


class AssignedLocation(_Response):
    building: str | None
    floor: str | None
    area: str | None


class PersonnelBasicResponse(_Response):
    """What Supervisors see (and each directory row)."""

    id: UUID
    personnel_id: str = Field(description="The system-generated Personnel ID")
    external_id: str | None = Field(description="Institutional ID")
    full_name: str
    category: CategoryRef
    department: str | None
    designation: str | None
    status: str
    start_date: date | None
    end_date: date | None
    assigned_location: AssignedLocation | None

    @classmethod
    def of(cls, s: PersonnelSummary) -> "PersonnelBasicResponse":
        return cls(**_summary_fields(s))


def _summary_fields(s: PersonnelSummary) -> dict[str, Any]:
    location = None
    if s.building_name:
        location = AssignedLocation(building=s.building_name, floor=s.floor_name, area=s.area_name)
    return {
        "id": s.id,
        "personnel_id": s.personnel_code,
        "external_id": s.external_id,
        "full_name": s.full_name,
        "category": CategoryRef(id=s.category_id, code=s.category_code, label=s.category_label),
        "department": s.department,
        "designation": s.designation,
        "status": s.status.value,
        "start_date": s.start_date,
        "end_date": s.end_date,
        "assigned_location": location,
    }


class DirectoryRowResponse(PersonnelBasicResponse):
    email: str | None = Field(description="Admins only; null for Supervisors")
    phone: str | None = Field(description="Admins only; null for Supervisors")

    @classmethod
    def of(cls, s: PersonnelSummary) -> "DirectoryRowResponse":
        return cls(**_summary_fields(s), email=s.email, phone=s.phone)


class DirectoryResponse(_Response):
    items: list[DirectoryRowResponse]
    total: int
    limit: int
    offset: int


class PersonnelDetailResponse(_Response):
    """The full record (Organization Admins only)."""

    id: UUID
    personnel_id: str
    external_id: str | None
    full_name: str
    category: CategoryRef
    email: str | None
    phone: str | None
    department: str | None
    designation: str | None
    joining_date: date | None
    start_date: date | None
    end_date: date | None
    status: str
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    emergency_contact_relation: str | None
    notes: str | None
    custom_attributes: dict[str, Any]
    location: LocationPathResponse | None
    has_photo: bool
    created_via: str
    import_batch_id: UUID | None
    created_by: str | None
    created_at: datetime | None
    updated_by: str | None
    updated_at: datetime | None
    deactivated_by: str | None
    deactivated_at: datetime | None
    deactivation_reason: str | None

    @classmethod
    def of(cls, p: Personnel) -> "PersonnelDetailResponse":
        v = p.values
        return cls(
            id=p.id,
            personnel_id=p.personnel_code,
            external_id=v.external_id,
            full_name=v.full_name,
            category=CategoryRef(id=p.category.id, code=p.category.code, label=p.category.label),
            email=v.email,
            phone=v.phone,
            department=v.department,
            designation=v.designation,
            joining_date=v.joining_date,
            start_date=v.start_date,
            end_date=v.end_date,
            status=p.status.value,
            address=v.address,
            emergency_contact_name=v.emergency_contact_name,
            emergency_contact_phone=v.emergency_contact_phone,
            emergency_contact_relation=v.emergency_contact_relation,
            notes=v.notes,
            custom_attributes=v.custom_attributes,
            location=None if p.location is None else LocationPathResponse.of(p.location),
            has_photo=p.has_photo,
            created_via=p.created_via.value,
            import_batch_id=p.import_batch_id,
            created_by=p.created_by_name,
            created_at=p.created_at,
            updated_by=p.updated_by_name,
            updated_at=p.updated_at,
            deactivated_by=p.deactivated_by_name,
            deactivated_at=p.deactivated_at,
            deactivation_reason=p.deactivation_reason,
        )


class IssueResponse(_Response):
    field: str | None
    code: str
    message: str

    @classmethod
    def of(cls, issue: FieldIssue) -> "IssueResponse":
        return cls(field=issue.field, code=issue.code, message=issue.message)


class PersonnelWriteResponse(_Response):
    personnel: PersonnelDetailResponse
    warnings: list[IssueResponse]


class DeactivateRequest(_Request):
    reason: str | None = Field(default=None, max_length=500)


class AuditEntryResponse(_Response):
    occurred_at: datetime
    action: str
    target_type: str
    target_id: UUID | None
    actor_role: str
    actor_id: str | None
    actor_name: str | None
    details: dict[str, Any]

    @classmethod
    def of(cls, e: AuditEntry) -> "AuditEntryResponse":
        return cls(
            occurred_at=e.occurred_at,
            action=e.action,
            target_type=e.target_type,
            target_id=e.target_id,
            actor_role=e.actor_role,
            actor_id=e.actor_display_id,
            actor_name=e.actor_name,
            details=e.metadata,
        )


class FieldDefinitionResponse(_Response):
    key: str
    label: str
    kind: str
    always_required: bool
    can_be_required_by_category: bool
    max_length: int | None
    help: str

    @classmethod
    def all(cls) -> list["FieldDefinitionResponse"]:
        return [
            cls(
                key=f.key,
                label=f.label,
                kind=f.kind,
                always_required=f.always_required,
                can_be_required_by_category=f.key in REQUIRABLE_FIELDS,
                max_length=f.max_length,
                help=f.help,
            )
            for f in CANONICAL_FIELDS
        ]


# --- Categories and custom fields --------------------------------------------------------------------------


class CategoryUpdateRequest(_Request):
    label: str = Field(max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    required_fields: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True
    sort_order: int = Field(default=100, ge=0, le=10000)

    def to_details(self) -> CategoryDetails:
        return CategoryDetails(
            self.label, self.description, tuple(self.required_fields), self.is_active, self.sort_order
        )


class CategoryCreateRequest(CategoryUpdateRequest):
    code: str = Field(max_length=60, description="e.g. INTERN; capital letters, digits, _")


class CategoryResponse(_Response):
    id: UUID
    code: str
    label: str
    description: str | None
    required_fields: list[str]
    is_system: bool
    is_active: bool
    sort_order: int

    @classmethod
    def of(cls, c: PersonnelCategory) -> "CategoryResponse":
        return cls(
            id=c.id,
            code=c.code,
            label=c.label,
            description=c.description,
            required_fields=list(c.required_fields),
            is_system=c.is_system,
            is_active=c.is_active,
            sort_order=c.sort_order,
        )


class CustomFieldUpdateRequest(_Request):
    label: str = Field(max_length=120)
    choices: list[str] = Field(default_factory=list, max_length=100)
    is_required: bool = False
    category_ids: list[UUID] = Field(default_factory=list, max_length=100, description="Empty: every category")
    max_length: int = Field(default=255, ge=1, le=2000)
    help_text: str | None = Field(default=None, max_length=600)
    is_active: bool = True

    def to_details(self) -> CustomFieldDetails:
        return CustomFieldDetails(
            self.label,
            tuple(self.choices),
            self.is_required,
            tuple(self.category_ids),
            self.max_length,
            self.help_text,
            self.is_active,
        )


class CustomFieldCreateRequest(CustomFieldUpdateRequest):
    key: str = Field(max_length=60, description="e.g. academic_program; lower-case letters, digits, _")
    field_type: FieldType


class CustomFieldResponse(_Response):
    id: UUID
    key: str
    label: str
    field_type: FieldType
    choices: list[str]
    is_required: bool
    category_ids: list[UUID]
    max_length: int
    help_text: str | None
    is_active: bool

    @classmethod
    def of(cls, f: CustomField) -> "CustomFieldResponse":
        return cls(
            id=f.id,
            key=f.key,
            label=f.label,
            field_type=f.field_type,
            choices=list(f.choices),
            is_required=f.is_required,
            category_ids=list(f.category_ids),
            max_length=f.max_length,
            help_text=f.help_text,
            is_active=f.is_active,
        )


# --- Import ---------------------------------------------------------------------------------------------------


class ImportBatchResponse(_Response):
    id: UUID
    original_filename: str
    file_format: str
    file_size: int
    sheet_name: str | None
    header_row: int
    status: Literal["UPLOADED", "INVALID", "VALIDATED", "IMPORTED", "FAILED", "ROLLED_BACK", "DISCARDED"]
    total_rows: int | None
    valid_rows: int | None
    warning_count: int
    error_count: int
    imported_count: int
    rolled_back_count: int
    failure_reason: str | None
    has_annotated_workbook: bool
    files_available: bool
    uploaded_by: str | None
    uploaded_at: datetime
    validated_at: datetime | None
    confirmed_by: str | None
    confirmed_at: datetime | None
    rolled_back_by: str | None
    rolled_back_at: datetime | None

    @classmethod
    def of(cls, b: ImportBatch) -> "ImportBatchResponse":
        return cls(
            id=b.id,
            original_filename=b.original_filename,
            file_format=b.file_format,
            file_size=b.file_size,
            sheet_name=b.sheet_name,
            header_row=b.header_row,
            status=b.status,  # type: ignore[arg-type]
            total_rows=b.total_rows,
            valid_rows=b.valid_rows,
            warning_count=b.warning_count,
            error_count=b.error_count,
            imported_count=b.imported_count,
            rolled_back_count=b.rolled_back_count,
            failure_reason=b.failure_reason,
            has_annotated_workbook=b.has_annotated_workbook,
            files_available=b.files_purged_at is None,
            uploaded_by=b.uploaded_by_name,
            uploaded_at=b.uploaded_at,
            validated_at=b.validated_at,
            confirmed_by=b.confirmed_by_name,
            confirmed_at=b.confirmed_at,
            rolled_back_by=b.rolled_back_by_name,
            rolled_back_at=b.rolled_back_at,
        )


class ImportColumn(_Response):
    index: int
    header: str
    target: str | None = Field(description="Confirmed field, or the suggestion while not yet confirmed")
    confidence: Literal["PROFILE", "EXACT", "SYNONYM", "FUZZY", "NONE"] = Field(
        description="How the suggestion was found; every suggestion must still be confirmed"
    )
    confirmed: bool


class ImportTarget(_Response):
    target: str
    label: str
    required: bool


class ImportDetailResponse(_Response):
    batch: ImportBatchResponse
    sheets: list[str]
    columns: list[ImportColumn]
    targets: list[ImportTarget] = Field(description="Fields a column can be mapped to; 'IGNORE' skips a column")


class MappingColumn(_Request):
    index: int = Field(ge=0, le=16383)
    target: str = Field(max_length=80, description="A field from 'targets', or IGNORE")


class MappingRequest(_Request):
    columns: list[MappingColumn] = Field(max_length=16384)
    save_profile_as: str | None = Field(default=None, max_length=80, description="Save this mapping for later files")


class ConfirmImportRequest(_Request):
    acknowledge_warnings: bool = False


class RollbackImportRequest(_Request):
    reason: str | None = Field(default=None, max_length=400)


class ImportIssueResponse(_Response):
    row_number: int | None
    column_index: int | None
    column_name: str | None
    field: str | None
    severity: str
    code: str
    message: str

    @classmethod
    def of(cls, i: StoredIssue) -> "ImportIssueResponse":
        return cls(
            row_number=i.row_number,
            column_index=i.column_index,
            column_name=i.column_name,
            field=i.field,
            severity=i.severity,
            code=i.code,
            message=i.message,
        )


class PreviewRowResponse(_Response):
    row_number: int
    category: str
    values: dict[str, Any]
    location: dict[str, str | None]


class MappingProfileResponse(_Response):
    id: UUID
    name: str
    mapping: dict[str, str]


# --- Restricted-area access ------------------------------------------------------------------------------------


class RestrictedAreaResponse(_Response):
    area_id: UUID
    area_name: str
    building_id: UUID
    building_name: str
    floor_id: UUID | None
    floor_name: str | None
    is_restricted: bool
    access_note: str | None
    marked_at: datetime | None
    active_grants: int

    @classmethod
    def of(cls, a: RestrictedArea) -> "RestrictedAreaResponse":
        return cls(**{name: getattr(a, name) for name in cls.model_fields})


class SupervisorRestrictedAreaResponse(_Response):
    """A restricted area as a Supervisor sees it: no ``access_note`` (an Admin note, D-016 item 16)."""

    area_id: UUID
    area_name: str
    building_id: UUID
    building_name: str
    floor_id: UUID | None
    floor_name: str | None
    is_restricted: bool
    marked_at: datetime | None
    active_grants: int

    @classmethod
    def of(cls, a: RestrictedArea) -> "SupervisorRestrictedAreaResponse":
        return cls(**{name: getattr(a, name) for name in cls.model_fields})


class MarkRestrictedRequest(_Request):
    access_note: str | None = Field(default=None, max_length=500, description="e.g. 'Server room: IT staff only'")


class ScheduleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[int] = Field(min_length=1, max_length=7, description="ISO weekdays: 1 = Monday ... 7 = Sunday")
    start: time = Field(description="Local start time, e.g. 08:00")
    end: time = Field(description="Local end time (same day), e.g. 18:00")
    timezone: str = Field(max_length=64, description="IANA time zone, e.g. Asia/Karachi")

    def to_schedule(self) -> AccessSchedule:
        return AccessSchedule(tuple(self.days), self.start, self.end, self.timezone)

    @classmethod
    def of(cls, s: AccessSchedule | None) -> "ScheduleModel | None":
        return None if s is None else cls(days=list(s.days), start=s.start, end=s.end, timezone=s.timezone)


class GrantTermsRequest(_Request):
    valid_from: AwareDatetime | None = Field(default=None, description="Default: now")
    valid_until: AwareDatetime | None = Field(default=None, description="Default: until revoked")
    schedule: ScheduleModel | None = None
    note: str | None = Field(default=None, max_length=500)

    def to_terms(self) -> GrantTerms:
        return GrantTerms(
            self.valid_from, self.valid_until, None if self.schedule is None else self.schedule.to_schedule(), self.note
        )


class GrantRequest(GrantTermsRequest):
    personnel_id: UUID
    area_ids: list[UUID] = Field(min_length=1, max_length=100)


class RevokeRequest(_Request):
    reason: str | None = Field(default=None, max_length=500)


class GrantResponse(_Response):
    id: UUID
    personnel: dict[str, Any]
    area: dict[str, Any]
    status: str
    valid_from: datetime
    valid_until: datetime | None
    schedule: ScheduleModel | None
    note: str | None = Field(description="Admins only")
    granted_by: str | None
    granted_at: datetime
    updated_at: datetime | None
    revoked_by: str | None
    revoked_at: datetime | None
    revoke_reason: str | None

    @classmethod
    def of(cls, g: AccessGrant) -> "GrantResponse":
        return cls(
            id=g.id,
            personnel={
                "id": g.personnel_id,
                "personnel_id": g.personnel_code,
                "full_name": g.personnel_name,
                "status": g.personnel_status,
                "category": g.category_label,
                "department": g.department,
            },
            area={"id": g.area_id, "name": g.area_name, "floor": g.floor_name, "building": g.building_name},
            status=g.status.value,
            valid_from=g.valid_from,
            valid_until=g.valid_until,
            schedule=ScheduleModel.of(g.schedule),
            note=g.note,
            granted_by=g.granted_by_name,
            granted_at=g.granted_at,
            updated_at=g.updated_at,
            revoked_by=g.revoked_by_name,
            revoked_at=g.revoked_at,
            revoke_reason=g.revoke_reason,
        )


class GrantHistoryResponse(_Response):
    grant: GrantResponse
    history: list[AuditEntryResponse]


class AccessCheckResponse(_Response):
    personnel_id: UUID
    area_id: UUID
    at: datetime
    decision: str = Field(
        description="AUTHORIZED, NO_PERMISSION, EXPIRED, NOT_YET_VALID, OUTSIDE_SCHEDULE, "
        "PERSONNEL_INACTIVE or AREA_NOT_RESTRICTED"
    )
    authorized: bool
    grant_id: UUID | None
