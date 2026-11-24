"""Business-rule errors. The presentation layer maps each one to an HTTP status.

Every error may also carry:
- ``code``: a stable machine-readable code (for example ``OTP_INVALID``) that clients
  can rely on, unlike the human-readable message. Older errors have none.
- ``retry_after``: seconds until the request may be repeated (sent as ``Retry-After``).
- ``errors``: per-field problems (``field``, ``code``, ``message``), only where a form needs them.
"""


class DomainError(Exception):
    """Base class for all business-rule errors."""

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        retry_after: int | None = None,
        errors: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        # Optional per-field problems (for example a personnel form); never contains submitted values.
        self.errors = errors


class AuthenticationFailed(DomainError):
    """The caller could not be identified, or their account is not active."""


class PermissionDenied(DomainError):
    """The caller is identified but their role does not allow the action."""


class NotFound(DomainError):
    """The record does not exist, or the caller's campus cannot see it."""


class InvalidStateTransition(DomainError):
    """The record is not in a state that allows the action."""


class Conflict(DomainError):
    """The action would break a uniqueness rule (e.g. duplicate email or campus name)."""


class AccountLocked(DomainError):
    """Too many failed login attempts; the account is temporarily locked."""


class ValidationFailed(DomainError):
    """The request is well-formed but breaks a business rule (e.g. password policy)."""


class CurrentPasswordIncorrect(DomainError):
    """An authenticated user gave the wrong current password for a sensitive change."""


class VerificationFailed(DomainError):
    """A one-time code was wrong, expired, or never issued."""


class RateLimited(DomainError):
    """Too many requests or attempts; ``retry_after`` says when to try again, if known."""


class Gone(DomainError):
    """The record existed but has expired and can no longer be used."""


class ServiceUnavailable(DomainError):
    """A service the request needs (for example email delivery) is not set up."""


class UpstreamServiceFailed(DomainError):
    """An external service (for example the email provider) failed or refused the request."""


class OperationFailed(DomainError):
    """The server could not finish the operation; the message says what state was left."""
