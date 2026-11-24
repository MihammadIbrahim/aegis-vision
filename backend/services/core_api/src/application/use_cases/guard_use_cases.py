"""Guard self-service (migration 0009): a Guard may change only email, phone number and password.

Name, username, Guard ID, organization and campus are never changed here (the request cannot
name them). Save = current password, then a code to the registered email (and one to a new
email); the change is applied only after verification. Verifying, resending and cancelling
reuse the Supervisor-profile steps.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from src.application.interfaces.i_email_service import IEmailSender
from src.application.interfaces.i_otp_codec import IOtpCodec
from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.organization_registration_use_cases import mask_email
from src.application.use_cases.otp_flow import TIMING_DUMMY_HASH, email_not_configured
from src.application.use_cases.supervisor_profile_use_cases import (
    GetSupervisorProfile,
    ProfileChangeStarted,
    _Codes,
    _locked,
    _wrong_password,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import PermissionDenied, ValidationFailed
from src.domain.repositories.i_organization_registration_repo import OtpLimits
from src.domain.repositories.i_staff_account_repo import (
    FlowOutcome,
    IGuardProfileRepository,
)
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.registration_policy import (
    check_email,
    check_password,
    check_phone,
)

GetGuardProfile = GetSupervisorProfile  # same read: the caller's own row, organization and ID


@dataclass(frozen=True, slots=True)
class GuardProfileChangeForm:
    current_password: str = field(repr=False)
    email: str | None = None
    phone: str | None = None
    new_password: str | None = field(default=None, repr=False)
    confirm_new_password: str | None = field(default=None, repr=False)


class StartGuardProfileChange:
    def __init__(
        self,
        repository: IGuardProfileRepository,
        hasher: IPasswordHasher,
        otp: IOtpCodec,
        sender: IEmailSender,
        transaction: ITransaction,
        *,
        limits: OtpLimits,
        ttl_seconds: int,
        max_failed_attempts: int,
        lockout_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._otp = otp
        self._sender = sender
        self._transaction = transaction
        self._limits = limits
        self._ttl = ttl_seconds
        self._max_failed = max_failed_attempts
        self._lockout = lockout_seconds
        self._clock = clock
        self._codes = _Codes(repository, otp, sender, transaction, limits, "Guard")

    async def execute(self, actor: User, form: GuardProfileChangeForm) -> ProfileChangeStarted:
        current = await GetGuardProfile(self._repository).execute(actor)
        email = phone = password = None
        if form.email is not None and check_email(form.email).lower() != current.email.lower():
            email = check_email(form.email)
        if form.phone is not None and check_phone(form.phone) != current.phone:
            phone = check_phone(form.phone)
        if form.new_password is not None or form.confirm_new_password is not None:
            if form.new_password != form.confirm_new_password or form.new_password is None:
                raise ValidationFailed("The new password and its confirmation do not match.", code="PASSWORD_MISMATCH")
            check_password(form.new_password, username=current.username or "\u0000", email=email or current.email)
            password = form.new_password
        if email is None and phone is None and password is None:
            raise ValidationFailed("Nothing to save: no field differs from your current profile.", code="NO_CHANGES")
        if not self._sender.is_configured:
            raise email_not_configured()

        account = await self._repository.account()
        if account is None:
            self._hasher.verify(TIMING_DUMMY_HASH, form.current_password)
            raise _wrong_password()
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, form.current_password):
            locked_until = await self._repository.record_password_failure(
                account.user_id, max_attempts=self._max_failed, window_seconds=self._lockout
            )
            await self._transaction.commit()  # counts toward the same lock as sign-in
            raise _locked(self._lockout) if locked_until is not None else _wrong_password()

        change_id = uuid4()
        outcome = await self._repository.submit_guard_change(
            change_id,
            email=email,
            phone=phone,
            password_hash=self._hasher.hash(password) if password is not None else None,
            new_email_digest=self._otp.recipient_digest(email) if email is not None else None,
            ttl_seconds=self._ttl,
        )
        if outcome is not FlowOutcome.CREATED:
            await self._transaction.rollback()
            raise PermissionDenied("Only an active Guard can change this profile.")
        sent_current = await self._codes.send(change_id, new_email=False)
        sent_new = await self._codes.send(change_id, new_email=True) if email is not None else None
        await self._transaction.commit()
        assert sent_current is not None
        return ProfileChangeStarted(
            change_id=change_id,
            masked_email=mask_email(sent_current[0]),
            masked_new_email=mask_email(sent_new[0]) if sent_new else None,
            delivery_channel=self._sender.delivery_channel,
            code_expires_in_seconds=self._limits.ttl_seconds,
            resend_available_in_seconds=self._limits.resend_cooldown_seconds,
            expires_at=sent_current[1],
        )
