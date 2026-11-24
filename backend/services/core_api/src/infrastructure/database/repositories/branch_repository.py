from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Uuid, and_, bindparam, func, insert, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.branch import Campus
from src.domain.entities.branch_request import (
    DECISION_WINDOW,
    CampusRegistrationRequest,
)
from src.domain.exceptions.domain_errors import Conflict
from src.domain.repositories.i_branch_repo import ApprovalResult, CampusRequestFilter, CampusRequestPage
from src.domain.value_objects.enums import CampusRequestStatus
from src.infrastructure.database.models import (
    CampusModel,
    CampusRegistrationRequestModel,
    UserModel,
)

_Request = CampusRegistrationRequestModel

_APPROVE = text(
    "SELECT outcome, reviewed_at FROM app_campus_request_approve(:request_id, :campus_id, :user_id, :display_id)"
).bindparams(
    bindparam("request_id", type_=Uuid), bindparam("campus_id", type_=Uuid), bindparam("user_id", type_=Uuid)
)


def not_expired(now: datetime):  # type: ignore[no-untyped-def]
    """Pending requests verified within the decision window (older rows have no verification time)."""
    return or_(_Request.email_verified_at.is_(None), _Request.email_verified_at > now - DECISION_WINDOW)


class SqlCampusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_campus(self, campus: Campus) -> None:
        # No RETURNING: the caller is not a member of the new campus and cannot read it.
        statement = insert(CampusModel).values(
            id=campus.id,
            organization_id=campus.organization_id,
            name=campus.name,
            status=campus.status.value,
        )
        try:
            await self._session.execute(statement)
        except IntegrityError as exc:
            raise Conflict("A campus with this name already exists in the organization.") from exc

    async def list_pending_requests(self, organization_id: UUID) -> list[CampusRegistrationRequest]:
        statement = (
            select(_Request)
            .where(
                _Request.organization_id == organization_id,
                _Request.status == CampusRequestStatus.PENDING.value,
                not_expired(datetime.now(UTC)),
            )
            .order_by(_Request.submitted_at)
        )
        return [_to_entity(row) for row in (await self._session.execute(statement)).scalars()]

    async def get_request(self, request_id: UUID, organization_id: UUID) -> CampusRegistrationRequest | None:
        statement = select(_Request).where(_Request.id == request_id, _Request.organization_id == organization_id)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_entity(row)

    async def save_request_decision(self, request: CampusRegistrationRequest) -> None:
        # Only a still-pending row is updated, so two concurrent reviews cannot both win.
        statement = (
            update(_Request)
            .where(
                _Request.id == request.id,
                _Request.organization_id == request.organization_id,
                _Request.status == CampusRequestStatus.PENDING.value,
            )
            .values(
                status=request.status.value,
                reviewed_by=request.reviewed_by,
                reviewed_at=request.reviewed_at,
                approved_campus_id=request.approved_campus_id,
                # A rejection erases the applicant's details in the same statement (decision I).
                proposed_campus_name=request.proposed_campus_name,
                applicant_name=request.applicant_name,
                applicant_email=request.applicant_email,
                applicant_username=request.applicant_username,
                applicant_phone=request.applicant_phone,
                details_erased_at=request.details_erased_at,
            )
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        if result.rowcount != 1:
            raise Conflict("This request was already reviewed.")

    async def search_requests(self, organization_id: UUID, where: CampusRequestFilter) -> CampusRequestPage:
        """Every request of the organization, newest first. Pending requests past their decision window
        are left out: they can never be approved and are deleted by the clean-up (decision I)."""
        now = datetime.now(UTC)
        live = and_(
            _Request.organization_id == organization_id,
            or_(_Request.status != CampusRequestStatus.PENDING.value, not_expired(now)),
        )
        conditions = [live]
        if where.text and where.text.strip():
            needle = _like(where.text.strip())
            conditions.append(
                or_(
                    _Request.proposed_campus_name.ilike(needle, escape="\\"),
                    _Request.applicant_name.ilike(needle, escape="\\"),
                    _Request.applicant_email.ilike(needle, escape="\\"),
                    _Request.applicant_username.ilike(needle, escape="\\"),
                )
            )
        counted = (
            await self._session.execute(
                select(_Request.status, func.count()).where(*conditions).group_by(_Request.status)
            )
        ).all()
        counts = {status.value: 0 for status in CampusRequestStatus}
        counts.update({row[0]: int(row[1]) for row in counted})
        if where.status is not None:
            conditions.append(_Request.status == where.status.value)
        total = sum(counts.values()) if where.status is None else counts.get(where.status.value, 0)
        reviewer = select(UserModel.full_name).where(UserModel.id == _Request.reviewed_by).scalar_subquery()
        statement = (
            select(_Request, reviewer.label("reviewer_name"))
            .where(*conditions)
            .order_by(_Request.submitted_at.desc(), _Request.id)
            .limit(where.limit)
            .offset(where.offset)
        )
        rows = (await self._session.execute(statement)).all()
        items = [replace(_to_entity(row[0]), reviewed_by_name=row[1]) for row in rows]
        return CampusRequestPage(items=items, total=total, counts=counts)

    async def approve_request(
        self, request_id: UUID, *, campus_id: UUID, user_id: UUID, display_id: str
    ) -> ApprovalResult:
        params = {"request_id": request_id, "campus_id": campus_id, "user_id": user_id, "display_id": display_id}
        row = (await self._session.execute(_APPROVE, params)).one()
        return ApprovalResult(outcome=row.outcome, reviewed_at=row.reviewed_at)


def _to_entity(row: CampusRegistrationRequestModel) -> CampusRegistrationRequest:
    return CampusRegistrationRequest(
        id=row.id,
        organization_id=row.organization_id,
        proposed_campus_name=row.proposed_campus_name,
        applicant_name=row.applicant_name,
        applicant_email=row.applicant_email,
        email_verified=row.email_verified,
        status=CampusRequestStatus(row.status),
        submitted_at=row.submitted_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        approved_campus_id=row.approved_campus_id,
        applicant_username=row.applicant_username,
        applicant_phone=row.applicant_phone,
        email_verified_at=row.email_verified_at,
        details_erased_at=row.details_erased_at,
    )


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
