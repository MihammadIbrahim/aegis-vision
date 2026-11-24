"""First Organization Admin registration.

A registration is only a request until its email OTP is verified. Verification creates,
together and all at once: the organization (with its permanent Organization ID), its first
campus (named by the applicant, decision D-021; "Main Campus" before that), and the Main Admin account.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

MAIN_CAMPUS_NAME = "Main Campus"  # the fixed name before decision D-021; kept for older records


@dataclass(frozen=True, slots=True)
class RegistrationApplicant:
    """The registration form after validation. The password exists only in memory, to be
    hashed; it is never stored or shown (``repr=False``)."""

    organization_name: str  # exactly as entered
    campus_name: str  # the first campus (decision D-021)
    admin_full_name: str
    username: str
    email: str
    phone: str  # digits with an optional leading "+"
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PendingRegistration:
    """What is stored while waiting for the email OTP."""

    id: UUID
    organization_code: str  # reserved now, kept through every retry
    organization_name: str
    campus_name: str
    admin_full_name: str
    username: str
    email: str
    phone: str
    password_hash: str = field(repr=False)
    recipient_digest: str = field(repr=False)  # keyed hash of the email, for the hourly OTP limit
    ttl_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class CompletedRegistration:
    """The result of a successful verification: the new organization and its Main Admin."""

    organization_code: str
    organization_name: str
    official_email: str
    official_phone: str
    campus_name: str
    admin_display_id: str
    admin_full_name: str
    admin_username: str
    admin_email: str
    admin_phone: str
    completed_at: datetime
