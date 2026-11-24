from typing import Protocol
from uuid import UUID


class IOtpCodec(Protocol):
    """Creates one-time codes and the keyed hashes stored in their place.

    Codes are never stored. Only ``hash_code`` results are, and they are useless without
    the server's secret key.
    """

    def new_code(self) -> str:
        """A random 6-digit code."""
        ...

    def hash_code(self, registration_id: UUID, code: str) -> str:
        """Bound to one registration: the same code for another registration hashes differently."""
        ...

    def hash_login_code(self, login_id: UUID, code: str) -> str:
        """For Organization Admin sign-in codes; bound to one sign-in attempt."""
        ...

    def hash_purpose_code(self, purpose: str, subject_id: UUID, code: str) -> str:
        """For the codes of migration 0007 (step-up, Supervisor email, password reset)."""
        ...

    def hash_token(self, purpose: str, token: str) -> str:
        """Keyed hash of a one-time secret token (e.g. a password-reset token)."""
        ...

    def recipient_digest(self, email: str) -> str:
        """A keyed hash of the email address (case-insensitive), for per-address limits."""
        ...
