"""Storage for "Already Registered Organization?" applications (migration 0021). Every method
calls one narrow, public database function: nobody is signed in."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import FlowOutcome, IssueResult


@dataclass(frozen=True, slots=True)
class CampusApplicationForm:
    submission_id: UUID
    organization_name: str
    organization_code: str
    campus_name: str
    full_name: str
    username: str
    email: str
    phone: str
    password_hash: str = field(repr=False)
    recipient_digest: str = field(repr=False)
    ttl_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class VerifiedApplication:
    """What the applicant sees once the email is verified: the request now waits for the Main Admin."""

    request_id: UUID
    organization_name: str
    campus_name: str
    email: str
    verified_at: datetime
    decision_deadline: datetime


@dataclass(frozen=True, slots=True)
class ApplicationVerifyResult:
    outcome: FlowOutcome
    attempts_remaining: int | None = None
    application: VerifiedApplication | None = None


class ICampusRequestApplicationRepository(Protocol):
    async def submit(self, form: CampusApplicationForm) -> FlowOutcome: ...

    async def issue_otp(
        self, submission_id: UUID, challenge_id: UUID, code_hash: str, limits: OtpLimits
    ) -> IssueResult: ...

    async def verify(self, submission_id: UUID, code_hash: str, request_id: UUID) -> ApplicationVerifyResult: ...
