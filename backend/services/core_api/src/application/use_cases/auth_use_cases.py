"""Organization Admin sign-in (decision K of docs/requirements/
Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md; requirements Section 9.1).

1. Credentials: Organization Name (exact, including capitalization) + Organization ID +
   email + password. Every mismatch gives the same answer, so the response never says
   which part was wrong. Wrong passwords count toward a temporary lock.
2. A one-time code is emailed to the Admin's registered address.
3. Verify the code. Only then does the caller get an access token (issued by the router).

Nothing here works for Supervisors or Guards, and a pending registration has no
account, so it cannot sign in.
"""

import html
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from src.application.interfaces.i_email_service import (
    EmailDeliveryError,
    EmailMessage,
    IEmailSender,
)
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.organization_registration_use_cases import mask_email
from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    Conflict,
    DomainError,
    Gone,
    NotFound,
    PermissionDenied,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    ValidationFailed,
    VerificationFailed,
)
from src.domain.repositories.i_admin_login_repo import (
    IAdminLoginRepository,
    LoginAccount,
    LoginIssueOutcome,
    LoginIssueResult,
    LoginVerifyOutcome,
    LoginVerifyResult,
)
from src.domain.repositories.i_audit_log_repo import IAuthEventLog
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_unit_of_work import ITransaction

INVALID_CREDENTIALS = "The organization details, email address or password are incorrect."
# Checked when no account matches, so that answer takes as long as a real password check.
_TIMING_DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$LgmK9ajp4hVuEECnmlELqA$3Ow5Y0vPv42OYedhueQ7MCCh1C2JdRJn13qG/0HadeE"
_OTP_FORMAT = re.compile(r"[0-9]{6}")
# Organization audit log, category AUTHENTICATION (decision D-024).
AUTH_SIGN_IN_SUCCEEDED = "AUTH_SIGN_IN_SUCCEEDED"
AUTH_SIGN_IN_FAILED = "AUTH_SIGN_IN_FAILED"
AUTH_SIGN_IN_BLOCKED = "AUTH_SIGN_IN_BLOCKED"
# Bounds the work done for absurd input; real values are far shorter.
_MAX_FIELD = 320


@dataclass(frozen=True, slots=True)
class AdminLoginForm:
    organization_name: str
    organization_id: str
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LoginCodeSent:
    login_id: UUID
    masked_email: str
    delivery_channel: str
    code_expires_in_seconds: int
    resend_available_in_seconds: int
    login_expires_at: datetime


def login_code_email(to_email: str, code: str, expires_in_seconds: int) -> EmailMessage:
    """Fixed wording plus the code; nothing typed into the sign-in form is included."""
    minutes = max(1, expires_in_seconds // 60)
    expiry = f"{minutes} minute{'s' if minutes != 1 else ''}"
    text = (
        f"Your verification code is: {code}\n\n"
        f"Enter this code to finish signing in. It expires in {expiry} "
        "and can be used only once.\n\n"
        "If you did not try to sign in, someone may know your password: change it as soon as possible. "
        "Nobody can sign in without this code.\n"
    )
    body = (
        f"<p>Your verification code is: <strong>{html.escape(code)}</strong></p>"
        f"<p>Enter this code to finish signing in. It expires in {expiry} "
        "and can be used only once.</p>"
        "<p>If you did not try to sign in, someone may know your password: change it as soon as possible. "
        "Nobody can sign in without this code.</p>"
    )
    return EmailMessage(to_email=to_email, subject="Your sign-in code", text_body=text, html_body=body)


def _invalid_credentials() -> AuthenticationFailed:
    return AuthenticationFailed(INVALID_CREDENTIALS, code="INVALID_CREDENTIALS")


def _email_not_configured() -> ServiceUnavailable:
    return ServiceUnavailable(
        "Email delivery is not configured on this server, so no sign-in code can be sent.",
        code="EMAIL_DELIVERY_NOT_CONFIGURED",
    )


def _login_expired() -> Gone:
    return Gone("This sign-in has expired. Please sign in again.", code="LOGIN_EXPIRED")


def _login_not_found() -> NotFound:
    return NotFound("Sign-in not found. Please sign in again.", code="LOGIN_NOT_FOUND")


def _already_completed() -> Conflict:
    return Conflict("This sign-in is already complete. Its code cannot be used again.", code="LOGIN_ALREADY_COMPLETED")


class _LoginCodeIssuer:
    def __init__(
        self,
        repository: IAdminLoginRepository,
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

    async def issue_and_send(self, login_id: UUID) -> LoginCodeSent:
        code = self._otp.new_code()
        result = await self._repository.issue_otp(
            login_id, uuid4(), self._otp.hash_login_code(login_id, code), self._limits
        )
        if result.outcome is not LoginIssueOutcome.ISSUED:
            await self._transaction.rollback()
            raise _issue_refused(result)
        if result.email is None or result.login_expires_at is None:
            raise RuntimeError("issue_otp returned ISSUED without a recipient")
        try:
            await self._email.send(login_code_email(result.email, code, self._limits.ttl_seconds))
        except EmailDeliveryError:
            # No token can follow and nothing is kept: the code was never delivered.
            await self._transaction.rollback()
            raise UpstreamServiceFailed(
                "The sign-in code could not be sent. Please try again in a few minutes.",
                code="EMAIL_DELIVERY_FAILED",
            ) from None
        return LoginCodeSent(
            login_id=login_id,
            masked_email=mask_email(result.email),
            delivery_channel=self._email.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            login_expires_at=result.login_expires_at,
        )


def _issue_refused(result: LoginIssueResult) -> DomainError:
    if result.outcome is LoginIssueOutcome.COOLDOWN:
        return RateLimited(
            "Please wait before requesting another code.",
            code="OTP_RESEND_COOLDOWN",
            retry_after=result.retry_after_seconds,
        )
    if result.outcome is LoginIssueOutcome.HOURLY_LIMIT:
        return RateLimited(
            "Too many sign-in codes have been sent to this account in the last hour. Please try again later.",
            code="OTP_HOURLY_LIMIT",
            retry_after=result.retry_after_seconds,
        )
    if result.outcome is LoginIssueOutcome.LOGIN_EXPIRED:
        return _login_expired()
    if result.outcome is LoginIssueOutcome.ALREADY_COMPLETED:
        return _already_completed()
    if result.outcome is LoginIssueOutcome.NOT_ELIGIBLE:
        return _invalid_credentials()
    return _login_not_found()


class StartOrganizationAdminLogin:
    def __init__(
        self,
        repository: IAdminLoginRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        login_ttl_seconds: int,
        max_failed_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        auth_events: IAuthEventLog | None = None,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._email = email
        self._transaction = transaction
        self._ttl = login_ttl_seconds
        self._max_failed = max_failed_attempts
        self._lockout = lockout_seconds
        self._clock = clock
        self._issuer = _LoginCodeIssuer(repository, otp, email, transaction, limits)
        self._events = auth_events

    async def _event(self, user_id: UUID, action: str, outcome: str, reason: str) -> None:
        """An audit record of the attempt for the account's own campus (decision D-024); never a secret."""
        if self._events is not None:
            await self._events.auth_event(user_id, action, outcome, {"reason": reason})

    async def _check_account(self, form: Any, account: LoginAccount) -> None:
        """Further checks once the password is known to be right (none for staff)."""

    async def _pending_request_hash(self, form: Any) -> str | None:
        """An applicant whose campus request is verified but not yet approved (decision L)."""
        fields = (form.organization_name, form.organization_id, form.email)
        if all(fields) and all(len(value) <= _MAX_FIELD for value in fields):
            return await self._repository.find_pending_campus_request(
                form.organization_name, form.organization_id, form.email.strip()
            )
        return None

    async def _find(self, form: Any) -> LoginAccount | None:
        fields = (form.organization_name, form.organization_id, form.email)
        if all(fields) and all(len(value) <= _MAX_FIELD for value in fields):
            return await self._repository.find_account(form.organization_name, form.organization_id, form.email.strip())
        return None

    async def execute(self, form: Any) -> LoginCodeSent:
        if not self._email.is_configured:
            raise _email_not_configured()
        account = await self._find(form)
        if account is None:
            pending = await self._pending_request_hash(form)
            if pending is not None and self._hasher.verify(pending, form.password):
                # Right password, but the Main Admin has not approved the request yet: no session, no code.
                await self._transaction.rollback()
                raise PermissionDenied(
                    "Your campus request is verified and waiting for the Main Admin's approval. "
                    "You can sign in once it is approved.",
                    code="ADMIN_APPROVAL_PENDING",
                )
            if pending is None:
                self._hasher.verify(_TIMING_DUMMY_HASH, form.password)  # same cost as a real check
            raise _invalid_credentials()

        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            await self._event(account.user_id, AUTH_SIGN_IN_BLOCKED, "DENIED", "ACCOUNT_LOCKED")
            await self._transaction.commit()
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, form.password):
            locked_until = await self._repository.record_failure(
                account.user_id, max_attempts=self._max_failed, window_seconds=self._lockout
            )
            reason = "WRONG_PASSWORD_ACCOUNT_LOCKED" if locked_until is not None else "WRONG_PASSWORD"
            await self._event(account.user_id, AUTH_SIGN_IN_FAILED, "FAILURE", reason)
            await self._transaction.commit()  # keep the counted failure
            if locked_until is not None:
                raise _locked(self._lockout)
            raise _invalid_credentials()
        if not account.eligible:
            # Right password, but the account, campus or organization is not active. Same
            # answer as a wrong password, so the response does not reveal the account's state.
            await self._transaction.rollback()
            if self._events is not None:
                await self._event(account.user_id, AUTH_SIGN_IN_BLOCKED, "DENIED", "ACCOUNT_NOT_ACTIVE")
                await self._transaction.commit()
            raise _invalid_credentials()
        await self._check_account(form, account)

        login_id = uuid4()
        await self._repository.start(account.user_id, login_id, ttl_seconds=self._ttl)
        sent = await self._issuer.issue_and_send(login_id)
        await self._transaction.commit()
        return sent


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many failed sign-in attempts. Please try again later.",
        code="ACCOUNT_LOCKED",
        retry_after=max(1, retry_after),
    )


class ResendOrganizationAdminLoginCode:
    def __init__(
        self,
        repository: IAdminLoginRepository,
        otp: IOtpCodec,
        email: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
    ) -> None:
        self._email = email
        self._transaction = transaction
        self._issuer = _LoginCodeIssuer(repository, otp, email, transaction, limits)

    async def execute(self, login_id: UUID) -> LoginCodeSent:
        if not self._email.is_configured:
            raise _email_not_configured()
        sent = await self._issuer.issue_and_send(login_id)
        await self._transaction.commit()
        return sent


class VerifyOrganizationAdminLogin:
    """Returns the signed-in Admin's account ID; the caller turns it into a token."""

    def __init__(
        self,
        repository: IAdminLoginRepository,
        otp: IOtpCodec,
        transaction: ITransaction,
        auth_events: IAuthEventLog | None = None,
    ) -> None:
        self._repository = repository
        self._otp = otp
        self._transaction = transaction
        self._events = auth_events

    async def execute(self, login_id: UUID, code: str) -> UUID:
        if not _OTP_FORMAT.fullmatch(code):
            raise ValidationFailed("Enter the 6-digit code from the email.", code="INVALID_OTP_FORMAT")
        result = await self._repository.verify_otp(login_id, self._otp.hash_login_code(login_id, code))
        if result.outcome is LoginVerifyOutcome.VERIFIED and result.user_id is not None:
            if self._events is not None:
                await self._events.auth_event(result.user_id, AUTH_SIGN_IN_SUCCEEDED, "SUCCESS", {})
            await self._transaction.commit()
            return result.user_id
        raise await self._not_verified(result)

    async def _not_verified(self, result: LoginVerifyResult) -> DomainError:
        outcome = result.outcome
        if outcome in (LoginVerifyOutcome.INVALID_CODE, LoginVerifyOutcome.ATTEMPTS_EXCEEDED):
            await self._transaction.commit()  # keep the counted attempt
        else:
            await self._transaction.rollback()
        if outcome is LoginVerifyOutcome.INVALID_CODE:
            left = result.attempts_remaining or 0
            return VerificationFailed(
                f"The code is incorrect. {left} attempt{'s' if left != 1 else ''} left.", code="OTP_INVALID"
            )
        if outcome is LoginVerifyOutcome.ATTEMPTS_EXCEEDED:
            return RateLimited("Too many incorrect codes. Request a new code.", code="OTP_ATTEMPTS_EXCEEDED")
        if outcome is LoginVerifyOutcome.CODE_EXPIRED:
            return VerificationFailed("This code has expired. Request a new code.", code="OTP_EXPIRED")
        if outcome is LoginVerifyOutcome.NO_ACTIVE_CODE:
            return VerificationFailed("No code is active for this sign-in. Request a new code.", code="OTP_NOT_ISSUED")
        if outcome is LoginVerifyOutcome.LOGIN_EXPIRED:
            return _login_expired()
        if outcome is LoginVerifyOutcome.ALREADY_COMPLETED:
            return _already_completed()
        if outcome is LoginVerifyOutcome.NOT_ELIGIBLE:
            return _invalid_credentials()
        return _login_not_found()


@dataclass(frozen=True, slots=True)
class GuardLoginForm:
    guard_id: str
    email: str
    password: str = field(repr=False)
    operational_mode: str = "STANDBY"  # STANDBY or STATIONED (decision D-018)


STANDBY, STATIONED = "STANDBY", "STATIONED"


def operational_mode_mismatch(chosen: str, stationed: bool) -> Conflict | None:
    """The Guard chose a mode that does not match the current assignment (decision D-018)."""
    if stationed and chosen != STATIONED:
        return Conflict(
            "You are stationed at a post. Choose Stationed / Standalone to sign in.", code="OPERATIONAL_MODE_MISMATCH"
        )
    if not stationed and chosen != STANDBY:
        return Conflict("You have no assigned post. Choose Standby to sign in.", code="OPERATIONAL_MODE_MISMATCH")
    return None


class StartGuardLogin(StartOrganizationAdminLogin):
    """Guard sign-in (Guard app): Guard ID + registered email + password, then the same emailed
    code, lock and timing protections as the Organization Admin sign-in. Only Guards match."""

    async def _pending_request_hash(self, form: Any) -> str | None:
        return None  # campus requests are for Admins only

    async def _find(self, form: Any) -> LoginAccount | None:
        fields = (form.guard_id, form.email)
        if all(fields) and all(len(value) <= _MAX_FIELD for value in fields):
            return await self._repository.find_guard_account(form.guard_id.strip(), form.email.strip())
        return None

    async def _check_account(self, form: Any, account: LoginAccount) -> None:
        """The chosen operational mode must match the Guard's assignment now. Checked only after the
        right password, so the assignment is never revealed to someone without it."""
        stationed = await self._repository.guard_is_stationed(account.user_id)
        problem = operational_mode_mismatch(form.operational_mode, bool(stationed))
        if stationed is None or problem is not None:
            await self._transaction.rollback()
            raise problem if problem is not None else _invalid_credentials()
