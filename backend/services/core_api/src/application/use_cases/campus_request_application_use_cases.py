"""Campus / branch request for an existing organization: the applicant's side (requirements
Section 8, "Already Registered Organization?"; decisions G, I and L).

1. Submit: the organization's exact name and Organization ID, the proposed campus name and the
   applicant's Admin details. Stores a pending application and emails a one-time code.
2. Resend: a new code, within the cooldown and the hourly limit.
3. Verify: a right code turns the application into a verified request in the Main Admin's
   queue (``GET /campus-requests``). The applicant gets no session: they can sign in only after
   the Main Admin approves, and before that sign-in answers ``ADMIN_APPROVAL_PENDING``.
"""

import re
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.organization_registration_use_cases import mask_email
from src.application.use_cases.otp_flow import (
    CodeSent,
    already,
    code_email,
    code_not_verified,
    counts_attempt,
    email_not_configured,
    gone,
    issue_refused,
    missing,
    send_or_roll_back,
)
from src.domain.exceptions.domain_errors import (
    Conflict,
    DomainError,
    NotFound,
    ValidationFailed,
)
from src.domain.repositories.i_campus_request_application_repo import (
    CampusApplicationForm,
    ICampusRequestApplicationRepository,
    VerifiedApplication,
)
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import FlowOutcome, VerifyResult
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import (
    check_campus_name,
    check_email,
    check_full_name,
    check_password,
    check_phone,
    check_username,
)
from src.domain.value_objects.organization_code import is_organization_code

_OTP_FORMAT = re.compile(r"[0-9]{6}")
_MAX_NAME = 200
ORGANIZATION_NOT_MATCHED = (
    "No active organization matches this exact name and Organization ID. Check both (the name is "
    "matched exactly, as at sign-in) and try again."
)
_PURPOSE = "Enter this code to verify your email for a new campus request."


@dataclass(frozen=True, slots=True)
class CampusApplication:
    organization_name: str
    organization_id: str
    campus_name: str
    full_name: str
    username: str
    email: str
    phone: str
    password: str = field(repr=False)


def _organization_not_matched() -> NotFound:
    return NotFound(ORGANIZATION_NOT_MATCHED, code="ORGANIZATION_NOT_MATCHED")


def _not_found() -> NotFound:
    return missing("Campus request not found. Please start a new request.", "CAMPUS_REQUEST_NOT_FOUND")


def _expired() -> DomainError:
    return gone("This campus request has expired. Please start a new request.", "CAMPUS_REQUEST_EXPIRED")


def _done() -> DomainError:
    return already("This email address is already verified for this request.", "CAMPUS_REQUEST_ALREADY_VERIFIED")


def _taken(outcome: FlowOutcome) -> DomainError:
    if outcome is FlowOutcome.CAMPUS_NAME_TAKEN:
        return Conflict(
            "This organization already has a campus, or a pending request, with this name.", code="CAMPUS_NAME_TAKEN"
        )
    if outcome is FlowOutcome.USERNAME_TAKEN:
        return Conflict("This username is already taken.", code="USERNAME_TAKEN")
    if outcome is FlowOutcome.EMAIL_ALREADY_REGISTERED:
        return Conflict(
            "This email address already belongs to an account. Use a different email address.",
            code="EMAIL_ALREADY_REGISTERED",
        )
    if outcome is FlowOutcome.EMAIL_ALREADY_PENDING:
        return Conflict(
            "A campus request with this email address is already waiting for approval.", code="EMAIL_ALREADY_PENDING"
        )
    if outcome is FlowOutcome.NOT_FOUND:
        return _organization_not_matched()
    return Conflict("The request could not be completed. Nothing was created.", code="CAMPUS_REQUEST_CONFLICT")


class _CodeIssuer:
    def __init__(
        self,
        repository: ICampusRequestApplicationRepository,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        limits: OtpLimits,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._email = email
        self._transaction = transaction
        self._limits = limits

    async def issue_and_send(self, submission_id: UUID) -> CodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_otp(
            submission_id, uuid4(), self._otp.hash_code(submission_id, code), self._limits
        )
        if result.outcome is not FlowOutcome.ISSUED:
            if result.outcome is FlowOutcome.EXPIRED:
                await self._transaction.commit()  # keep the erasure of the applicant's details
            else:
                await self._transaction.rollback()
            raise issue_refused(result, not_found=_not_found(), expired=_expired(), done=_done())
        if result.email is None or result.expires_at is None:
            raise RuntimeError("issue_otp returned ISSUED without a recipient")
        message = code_email(
            result.email,
            code,
            self._limits.ttl_seconds,
            subject="Your campus request verification code",
            purpose=_PURPOSE,
        )
        await send_or_roll_back(self._email, message, self._transaction)
        return CodeSent(
            subject_id=submission_id,
            masked_email=mask_email(result.email),
            delivery_channel=self._email.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=result.expires_at,
        )


class SubmitCampusRequest:
    def __init__(
        self,
        repository: ICampusRequestApplicationRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        application_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._email = email
        self._transaction = transaction
        self._ttl = application_ttl_seconds
        self._issuer = _CodeIssuer(repository, otp, email, transaction, limits)

    async def execute(self, form: CampusApplication) -> CodeSent:
        # The organization name is matched exactly (decision D), so it is never cleaned up here.
        organization_name = form.organization_name
        organization_code = form.organization_id.strip().upper()
        if not organization_name.strip() or len(organization_name) > _MAX_NAME:
            raise ValidationFailed("Enter the organization's exact registered name.", code="INVALID_ORGANIZATION_NAME")
        if not is_organization_code(organization_code):
            raise ValidationFailed(
                "Enter the Organization ID (6 capital letters followed by 11 digits).", code="INVALID_ORGANIZATION_ID"
            )
        campus_name = check_campus_name(form.campus_name)
        full_name = check_full_name(form.full_name)
        username = check_username(form.username)
        email = check_email(form.email)
        phone = check_phone(form.phone)
        check_password(form.password, username=username, email=email)
        if not self._email.is_configured:
            raise email_not_configured()

        submission_id = uuid4()
        outcome = await self._repository.submit(
            CampusApplicationForm(
                submission_id=submission_id,
                organization_name=organization_name,
                organization_code=organization_code,
                campus_name=campus_name,
                full_name=full_name,
                username=username,
                email=email,
                phone=phone,
                password_hash=self._hasher.hash(form.password),
                recipient_digest=self._otp.recipient_digest(email),
                ttl_seconds=self._ttl,
            )
        )
        if outcome is not FlowOutcome.CREATED:
            await self._transaction.commit()  # keep the clean-up of stale requests
            raise _taken(outcome)
        sent = await self._issuer.issue_and_send(submission_id)
        await self._transaction.commit()
        return sent


class ResendCampusRequestCode:
    def __init__(
        self,
        repository: ICampusRequestApplicationRepository,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
    ) -> None:
        self._email = email
        self._transaction = transaction
        self._issuer = _CodeIssuer(repository, otp, email, transaction, limits)

    async def execute(self, submission_id: UUID) -> CodeSent:
        if not self._email.is_configured:
            raise email_not_configured()
        sent = await self._issuer.issue_and_send(submission_id)
        await self._transaction.commit()
        return sent


class VerifyCampusRequest:
    def __init__(
        self, repository: ICampusRequestApplicationRepository, otp: IOtpCodec, transaction: ITransaction
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction

    async def execute(self, submission_id: UUID, code: str) -> VerifiedApplication:
        if not _OTP_FORMAT.fullmatch(code):
            raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
        result = await self._repository.verify(submission_id, self._otp.hash_code(submission_id, code), uuid4())
        if result.outcome is FlowOutcome.COMPLETED and result.application is not None:
            await self._transaction.commit()
            return result.application
        verification = VerifyResult(result.outcome, result.attempts_remaining)
        if counts_attempt(verification) or result.outcome is FlowOutcome.EXPIRED:
            await self._transaction.commit()  # keep the counted attempt, or the erasure
        else:
            await self._transaction.rollback()  # e.g. a conflict: the code stays usable, nothing is created
        if result.outcome in (
            FlowOutcome.CAMPUS_NAME_TAKEN,
            FlowOutcome.USERNAME_TAKEN,
            FlowOutcome.EMAIL_ALREADY_REGISTERED,
            FlowOutcome.EMAIL_ALREADY_PENDING,
        ):
            raise _taken(result.outcome)
        raise code_not_verified(verification, not_found=_not_found(), expired=_expired(), done=_done())
