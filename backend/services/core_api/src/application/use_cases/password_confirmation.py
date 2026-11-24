"""Password confirmation for sensitive staff actions (the same rules as Staff Management, D-017):
the signed-in user's own password, checked with the sign-in lockout, so a stolen session alone
cannot change retention or delete evidence (decision D-023)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from src.application.interfaces.i_password_hasher import IPasswordHasher
from src.application.use_cases.otp_flow import TIMING_DUMMY_HASH
from src.domain.exceptions.domain_errors import AccountLocked, CurrentPasswordIncorrect
from src.domain.repositories.i_staff_account_repo import StepUpAccount
from src.domain.repositories.i_unit_of_work import ITransaction


class IStepUpRepository(Protocol):
    async def actor_account(self) -> StepUpAccount | None: ...

    async def record_password_failure(
        self, user_id: UUID, *, max_attempts: int, window_seconds: int
    ) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    max_failed_attempts: int
    lockout_seconds: int


def _locked(retry_after: int) -> AccountLocked:
    return AccountLocked(
        "Too many incorrect passwords. Please try again later.", code="ACCOUNT_LOCKED", retry_after=max(1, retry_after)
    )


class PasswordConfirmation:
    def __init__(
        self,
        repository: IStepUpRepository,
        transaction: ITransaction,
        hasher: IPasswordHasher,
        policy: PasswordPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._transaction = transaction
        self._hasher = hasher
        self._policy = policy
        self._clock = clock

    async def confirm(self, current_password: str) -> None:
        account = await self._repo.actor_account()
        if account is None:
            self._hasher.verify(TIMING_DUMMY_HASH, current_password)
            raise CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")
        now = self._clock()
        if account.locked_until is not None and account.locked_until > now:
            raise _locked(math.ceil((account.locked_until - now).total_seconds()))
        if not self._hasher.verify(account.password_hash, current_password):
            locked_until = await self._repo.record_password_failure(
                account.user_id,
                max_attempts=self._policy.max_failed_attempts,
                window_seconds=self._policy.lockout_seconds,
            )
            await self._transaction.commit()  # wrong passwords count toward the sign-in lock
            if locked_until is not None:
                raise _locked(self._policy.lockout_seconds)
            raise CurrentPasswordIncorrect("The current password is incorrect.", code="CURRENT_PASSWORD_INCORRECT")
