"""Campus/Branch Approval Requests (Org Admin page 15; requirements Section 8; decisions D-020, D-024).

Only the Main Admin reviews requests of their own organization.
- **Approve** needs the Main Admin's current password **and** a fresh code emailed to them (decision H):
  ``POST /campus-requests/authorization`` → code → ``…/verify`` gives a single-use confirmation that the
  approval then uses up (``app_step_up_consume``, migration 0024).
- **Reject** needs the Main Admin's current password (page 15 item 9; the decision-H proposal to also
  require a code for rejection was not adopted). A rejection erases the applicant's details (decision I).
- **History** lists pending, approved and rejected requests with a status filter and a search.
- Both decisions are written to the organization audit log; a rejection's record holds no applicant
  details, since those must be erased.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from src.application.use_cases.supervisor_use_cases import AccountKind
from src.domain.entities.branch_request import CampusRegistrationRequest
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    DomainError,
    Gone,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_branch_repo import CampusRequestFilter, CampusRequestPage, ICampusRepository
from src.domain.services.permission_policy import ensure_first_admin
from src.domain.value_objects.enums import UserRole
from src.domain.value_objects.system_id import generate_display_id

_UNIQUE_ID_ATTEMPTS = 5
CAMPUS_REQUEST_APPROVED = "CAMPUS_REQUEST_APPROVED"
CAMPUS_REQUEST_REJECTED = "CAMPUS_REQUEST_REJECTED"

# The Main Admin's confirmation before approving: the Admin step-up of migration 0007 with its own
# code label, so a code emailed for another purpose cannot confirm an approval.
CAMPUS_APPROVAL_KIND = AccountKind(
    "campus request",
    "CAMPUS_REQUEST",
    "Main Admin",
    "CAMPUS_APPROVAL_STEP_UP",
    "CAMPUS_APPROVAL_UNUSED",
    step_up_reason="before approving a campus request",
)


class IStepUpGrants(Protocol):
    async def consume_step_up(self, grant_id: UUID) -> str: ...


class IPasswordConfirmation(Protocol):
    async def confirm(self, current_password: str) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _expired() -> Gone:
    return Gone(
        "This request was not decided within 72 hours of the applicant's email verification and has expired.",
        code="CAMPUS_REQUEST_EXPIRED",
    )


def _confirmation_refused(outcome: str) -> DomainError:
    if outcome == "NOT_VERIFIED":
        return PermissionDenied("Enter the code sent to your email first.", code="AUTHORIZATION_NOT_VERIFIED")
    if outcome == "ALREADY_USED":
        return Conflict(
            "This confirmation was already used. Please confirm your password again.", code="AUTHORIZATION_USED"
        )
    if outcome == "EXPIRED":
        return Gone("This confirmation has expired. Please confirm your password again.", code="AUTHORIZATION_EXPIRED")
    return NotFound("Confirmation not found. Please confirm your password again.", code="AUTHORIZATION_NOT_FOUND")


class SearchCampusRequests:
    """Request history with filters (page 15, items 10–11). Main Admin only."""

    def __init__(self, campuses: ICampusRepository) -> None:
        self._campuses = campuses

    async def execute(self, actor: User, where: CampusRequestFilter) -> CampusRequestPage:
        ensure_first_admin(actor)
        if not 1 <= where.limit <= 200 or where.offset < 0:
            raise ValidationFailed("Invalid page.", code="INVALID_PAGE")
        return await self._campuses.search_requests(actor.organization_id, where)


class ListPendingCampusRequests:
    def __init__(self, campuses: ICampusRepository) -> None:
        self._campuses = campuses

    async def execute(self, actor: User) -> list[CampusRegistrationRequest]:
        ensure_first_admin(actor)
        return await self._campuses.list_pending_requests(actor.organization_id)


class _ReviewCampusRequest:
    def __init__(
        self,
        campuses: ICampusRepository,
        clock: Callable[[], datetime] = _utcnow,
        *,
        audit: IOrganizationAuditLog | None = None,
    ) -> None:
        self._campuses = campuses
        self._clock = clock
        self._audit = audit

    async def _load(self, actor: User, request_id: UUID) -> CampusRegistrationRequest:
        ensure_first_admin(actor)
        request = await self._campuses.get_request(request_id, actor.organization_id)
        if request is None:
            raise NotFound("Campus registration request not found.")
        return request


class ApproveCampusRequest(_ReviewCampusRequest):
    """Creates the campus and the applicant's Admin account, with the username, phone and
    password the applicant gave (migration 0021), all or nothing.

    Approving does not give the Main Admin access to the new campus: the new campus's data
    stays visible only to users assigned to it.
    """

    def __init__(
        self,
        campuses: ICampusRepository,
        clock: Callable[[], datetime] = _utcnow,
        *,
        confirmations: IStepUpGrants,
        audit: IOrganizationAuditLog | None = None,
        display_id_factory: Callable[[], str] = lambda: generate_display_id(UserRole.ADMIN),
    ) -> None:
        super().__init__(campuses, clock, audit=audit)
        self._confirmations = confirmations
        self._new_display_id = display_id_factory

    async def execute(self, actor: User, request_id: UUID, authorization_id: UUID) -> CampusRegistrationRequest:
        request = await self._load(actor, request_id)
        campus_id, user_id = uuid4(), uuid4()
        now = self._clock()
        approved = request.approve(actor.id, campus_id, now)  # the state rules, before any write
        if request.is_expired(now):
            raise _expired()
        # The password + emailed code confirmation is used up only if the approval succeeds: any
        # failure below rolls the whole transaction back (decision H).
        outcome = await self._confirmations.consume_step_up(authorization_id)
        if outcome != "CONSUMED":
            raise _confirmation_refused(outcome)
        result = None
        for _ in range(_UNIQUE_ID_ATTEMPTS):
            result = await self._campuses.approve_request(
                request.id, campus_id=campus_id, user_id=user_id, display_id=self._new_display_id()
            )
            if result.outcome != "DISPLAY_ID_TAKEN":
                break
        assert result is not None
        if result.outcome != "APPROVED":
            raise _approval_refused(result.outcome)
        if self._audit is not None:
            await self._audit.record(
                actor,
                CAMPUS_REQUEST_APPROVED,
                "CAMPUS_REQUEST",
                request.id,
                {
                    "campus_name": request.proposed_campus_name,
                    "campus_id": str(campus_id),
                    "applicant_name": request.applicant_name,
                    "applicant_username": request.applicant_username,
                },
            )
        return replace(approved, reviewed_at=result.reviewed_at or approved.reviewed_at)


def _approval_refused(outcome: str) -> DomainError:
    if outcome == "NOT_FOUND":
        return NotFound("Campus registration request not found.")
    if outcome == "NOT_AUTHORIZED":
        return PermissionDenied("Only the Main Admin can review campus requests.")
    if outcome == "EXPIRED":
        return _expired()
    if outcome == "EMAIL_NOT_VERIFIED":
        return InvalidStateTransition("The applicant's official email has not been verified.")
    if outcome == "ALREADY_COMPLETED":
        return InvalidStateTransition("This request has already been decided.")
    messages = {
        "CAMPUS_NAME_TAKEN": "A campus with this name already exists in the organization.",
        "EMAIL_ALREADY_REGISTERED": "The applicant's email address already belongs to an account.",
        "USERNAME_TAKEN": "The applicant's username was taken by another account.",
    }
    return Conflict(
        messages.get(outcome, "The request could not be approved.") + " Nothing was created.",
        code=outcome if outcome in messages else "CAMPUS_REQUEST_CONFLICT",
    )


class RejectCampusRequest(_ReviewCampusRequest):
    """Rejects with the Main Admin's current password and erases the applicant's details (decision I).
    Wrong passwords count toward the sign-in lock."""

    def __init__(
        self,
        campuses: ICampusRepository,
        clock: Callable[[], datetime] = _utcnow,
        *,
        confirmation: IPasswordConfirmation,
        audit: IOrganizationAuditLog | None = None,
    ) -> None:
        super().__init__(campuses, clock, audit=audit)
        self._confirmation = confirmation

    async def execute(self, actor: User, request_id: UUID, current_password: str) -> CampusRegistrationRequest:
        request = await self._load(actor, request_id)
        rejected = request.reject(actor.id, self._clock())  # the state rules, before the password
        await self._confirmation.confirm(current_password)
        await self._campuses.save_request_decision(rejected)
        if self._audit is not None:
            # No applicant details: they are erased by the rejection (decision I).
            await self._audit.record(
                actor,
                CAMPUS_REQUEST_REJECTED,
                "CAMPUS_REQUEST",
                request.id,
                {"email_verified": request.email_verified, "submitted_at": request.submitted_at.isoformat()},
            )
        return rejected
