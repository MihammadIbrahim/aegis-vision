"""Storage of personnel, their configuration, imports and restricted-area access (decision D-016).

Every method works inside the signed-in user's campus: implementations filter by organization
and campus, and the database enforces the same with Row-Level Security."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.domain.entities.area_access import AccessGrant, GrantTerms, RestrictedArea
from src.domain.entities.location import LocationTarget
from src.domain.entities.personnel import (
    AuditEntry,
    CustomField,
    Personnel,
    PersonnelCategory,
    PersonnelFilter,
    PersonnelSummary,
    PersonnelValues,
)


@dataclass(frozen=True, slots=True)
class NewPersonnel:
    id: UUID
    personnel_code: str
    values: PersonnelValues
    location: LocationTarget | None
    created_via: str  # MANUAL or IMPORT
    import_batch_id: UUID | None = None


class IPersonnelRepository(Protocol):
    async def categories(self) -> list[PersonnelCategory]: ...
    async def add_category(self, category: PersonnelCategory) -> None: ...
    async def update_category(self, category: PersonnelCategory) -> None: ...
    async def custom_fields(self) -> list[CustomField]: ...
    async def add_custom_field(self, field: CustomField, created_by: UUID) -> None: ...
    async def update_custom_field(self, field: CustomField) -> None: ...

    async def directory(self, where: PersonnelFilter) -> tuple[list[PersonnelSummary], int]: ...
    async def summary(self, personnel_id: UUID) -> PersonnelSummary | None: ...
    async def get(self, personnel_id: UUID) -> Personnel | None: ...
    async def add(self, person: NewPersonnel, actor_id: UUID) -> None: ...
    async def add_many(self, people: Sequence[NewPersonnel], actor_id: UUID) -> None: ...
    async def update(
        self, personnel_id: UUID, values: PersonnelValues, location: LocationTarget | None, actor_id: UUID
    ) -> None: ...
    async def set_status(self, personnel_id: UUID, active: bool, actor_id: UUID, reason: str | None) -> bool: ...
    async def set_photo(
        self, personnel_id: UUID, path: str | None, content_type: str | None, actor_id: UUID
    ) -> None: ...
    async def existing_identifiers(
        self, external_ids: Sequence[str], emails: Sequence[str]
    ) -> tuple[set[str], set[str]]: ...
    async def deactivate_import(self, batch_id: UUID, actor_id: UUID, reason: str) -> list[UUID]: ...
    async def audit_trail(self, target_type: str, target_id: UUID) -> list[AuditEntry]: ...


@dataclass(frozen=True, slots=True)
class ImportBatch:
    id: UUID
    original_filename: str
    file_format: str
    file_size: int
    file_sha256: str
    sheet_name: str | None
    header_row: int
    headers: list[str]
    mapping: list[str] | None
    status: str
    total_rows: int | None
    valid_rows: int | None
    warning_count: int
    error_count: int
    imported_count: int
    rolled_back_count: int
    failure_reason: str | None
    has_annotated_workbook: bool
    files_purged_at: datetime | None
    uploaded_by_name: str | None
    uploaded_at: datetime
    validated_at: datetime | None
    confirmed_by_name: str | None
    confirmed_at: datetime | None
    rolled_back_by_name: str | None
    rolled_back_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredIssue:
    row_number: int | None
    column_index: int | None
    column_name: str | None
    field: str | None
    severity: str
    code: str
    message: str


class IPersonnelImportRepository(Protocol):
    async def add_batch(self, batch_id: UUID, values: dict[str, Any], actor_id: UUID) -> None: ...
    async def get_batch(self, batch_id: UUID, *, lock: bool = False) -> ImportBatch | None: ...
    async def list_batches(self, limit: int, offset: int) -> list[ImportBatch]: ...
    async def update_batch(self, batch_id: UUID, values: dict[str, Any]) -> None: ...
    async def replace_issues(self, batch_id: UUID, issues: Sequence[StoredIssue]) -> None: ...
    async def issues(self, batch_id: UUID, severity: str | None, limit: int, offset: int) -> list[StoredIssue]: ...
    async def expired_batches(self, older_than: datetime) -> list[ImportBatch]: ...
    async def lock_campus_imports(self) -> None: ...
    async def mapping_profiles(self) -> list[tuple[UUID, str, dict[str, str]]]: ...
    async def save_mapping_profile(self, name: str, mapping: dict[str, str], actor_id: UUID) -> UUID: ...
    async def delete_mapping_profile(self, profile_id: UUID) -> bool: ...


class IAreaAccessRepository(Protocol):
    async def areas(self, *, restricted_only: bool) -> list[RestrictedArea]: ...
    async def area(self, area_id: UUID) -> RestrictedArea | None: ...
    async def mark_restricted(self, area_id: UUID, note: str | None, actor_id: UUID) -> bool: ...
    async def clear_restricted(self, area_id: UUID) -> bool: ...
    async def grants(
        self,
        *,
        area_id: UUID | None = None,
        personnel_id: UUID | None = None,
        grant_id: UUID | None = None,
        include_revoked: bool = False,
    ) -> list[AccessGrant]: ...
    async def add_grant(
        self, grant_id: UUID, personnel_id: UUID, area_id: UUID, terms: GrantTerms, valid_from: datetime, actor_id: UUID
    ) -> None: ...
    async def update_grant(self, grant_id: UUID, terms: GrantTerms, valid_from: datetime, actor_id: UUID) -> bool: ...
    async def revoke_grant(self, grant_id: UUID, reason: str | None, actor_id: UUID) -> bool: ...
    async def revoke_all_for(self, personnel_ids: Sequence[UUID], reason: str, actor_id: UUID) -> int: ...
