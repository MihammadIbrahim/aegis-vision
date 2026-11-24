"""Guard manual reports (requirements 7, 15.6; decision D-013). A report is a separate record from AI
alerts and never creates one. Building (or perimeter) → optional floor → optional area, a type, a
description and an optional JPEG/PNG photo. ``client_report_id`` makes an offline resend safe."""

import base64
import binascii
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.domain.entities.manual_report import ManualReport
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_manual_report_repo import (
    IManualReportRepository,
    ReportSubmission,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

_SIGNATURES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}
_OUTCOME_ERRORS = {
    "LOCATION_NOT_FOUND": lambda: NotFound("Building or perimeter not found.", code="LOCATION_NOT_FOUND"),
    "INVALID_LOCATION": lambda: ValidationFailed(
        "That floor or area is not part of the selected building.", code="INVALID_LOCATION"
    ),
    "NOT_ALLOWED": lambda: PermissionDenied("Only Guards submit manual reports.", code="GUARD_ONLY"),
}


@dataclass(frozen=True, slots=True)
class ReportDraft:
    client_report_id: UUID
    report_type: str
    description: str
    building_id: UUID
    floor_id: UUID | None = None
    area_id: UUID | None = None
    occurred_at: datetime | None = None
    photo_base64: str | None = None


def _photo(encoded: str | None, max_bytes: int) -> tuple[bytes, str, str] | None:
    if not encoded:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValidationFailed("The photo is not valid base64.", code="INVALID_PHOTO") from error
    if not data or len(data) > max_bytes:
        raise ValidationFailed(f"The photo must be at most {max_bytes} bytes.", code="PHOTO_TOO_LARGE")
    for signature, (content_type, extension) in _SIGNATURES.items():
        if data.startswith(signature):
            return data, content_type, extension
    raise ValidationFailed("The photo must be a JPEG or PNG image.", code="INVALID_PHOTO")


class SubmitManualReport:
    def __init__(
        self,
        reports: IManualReportRepository,
        storage: ICampusMediaStorage,
        transaction: ITransaction,
        *,
        max_photo_bytes: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reports = reports
        self._storage = storage
        self._transaction = transaction
        self._max_photo = max_photo_bytes
        self._clock = clock

    async def execute(self, actor: User, draft: ReportDraft) -> tuple[ManualReport, bool]:
        """The report, and whether it was new (False when this ``client_report_id`` was already sent)."""
        if actor.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards submit manual reports.", code="GUARD_ONLY")
        report_type = draft.report_type.strip()
        description = draft.description.strip()
        if not 2 <= len(report_type) <= 60:
            raise ValidationFailed("The report type must be 2 to 60 characters.", code="INVALID_TEXT")
        if not 1 <= len(description) <= 4000:
            raise ValidationFailed("The description must be 1 to 4000 characters.", code="INVALID_TEXT")
        if draft.occurred_at is not None and draft.occurred_at > self._clock():
            raise ValidationFailed("The time cannot be in the future.", code="INVALID_TIME")
        photo = _photo(draft.photo_base64, self._max_photo)
        report_id = uuid4()
        path = None if photo is None else f"manual_reports/{report_id}.{photo[2]}"
        if photo is not None and path is not None:
            self._storage.write_file(actor.organization_id, actor.campus_id, path, photo[0])
        try:
            stored_id, outcome = await self._reports.submit(
                ReportSubmission(
                    report_id=report_id,
                    client_report_id=draft.client_report_id,
                    report_type=report_type,
                    description=description,
                    building_id=draft.building_id,
                    floor_id=draft.floor_id,
                    area_id=draft.area_id,
                    photo_path=path,
                    photo_content_type=None if photo is None else photo[1],
                    occurred_at=draft.occurred_at,
                )
            )
            if outcome in _OUTCOME_ERRORS:
                raise _OUTCOME_ERRORS[outcome]()
            assert stored_id is not None
            created = outcome == "CREATED"
            if not created and path is not None:  # a resend: keep the photo stored the first time
                self._storage.remove_file(actor.organization_id, actor.campus_id, path)
            report = await self._reports.get(stored_id)
            assert report is not None
            await self._transaction.commit()
            return report, created
        except BaseException:
            await self._transaction.rollback()
            if path is not None:
                with contextlib.suppress(OSError):
                    self._storage.remove_file(actor.organization_id, actor.campus_id, path)
            raise


class ViewManualReports:
    """Staff see their campus's reports; a Guard sees only their own (database rule)."""

    def __init__(self, reports: IManualReportRepository, storage: ICampusMediaStorage | None = None) -> None:
        self._reports = reports
        self._storage = storage

    async def list_reports(self, actor: User, *, limit: int, offset: int) -> list[ManualReport]:
        return await self._reports.list_reports(limit=limit, offset=offset)

    async def get(self, actor: User, report_id: UUID) -> ManualReport:
        report = await self._reports.get(report_id)
        if report is None:
            raise NotFound("Report not found.", code="REPORT_NOT_FOUND")
        return report

    async def photo(self, actor: User, report_id: UUID) -> tuple[bytes, str]:
        stored = await self._reports.photo(report_id)
        data = None
        if stored is not None and self._storage is not None:
            data = self._storage.read_file(actor.organization_id, actor.campus_id, stored[0])
        if stored is None or data is None:
            raise NotFound("No photo for this report.", code="PHOTO_NOT_FOUND")
        return data, stored[1]

    async def locations(self, actor: User) -> list[dict[str, object]]:
        return await self._reports.guard_locations()


class ReviewManualReport:
    def __init__(
        self, reports: IManualReportRepository, audit: IOrganizationAuditLog, transaction: ITransaction
    ) -> None:
        self._reports = reports
        self._audit = audit
        self._transaction = transaction

    async def execute(self, actor: User, report_id: UUID, note: str | None) -> ManualReport:
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            raise PermissionDenied("Only Admins and Supervisors review reports.", code="STAFF_ONLY")
        cleaned = (note or "").strip() or None
        if cleaned is not None and len(cleaned) > 1000:
            raise ValidationFailed("The note must be at most 1000 characters.", code="INVALID_TEXT")
        if await self._reports.get(report_id) is None:
            raise NotFound("Report not found.", code="REPORT_NOT_FOUND")
        if not await self._reports.review(report_id, actor.id, cleaned):
            raise Conflict("This report was already reviewed.", code="REPORT_ALREADY_REVIEWED")
        await self._audit.record(actor, "MANUAL_REPORT_REVIEWED", "MANUAL_REPORT", report_id, {})
        report = await self._reports.get(report_id)
        assert report is not None
        await self._transaction.commit()
        return report
