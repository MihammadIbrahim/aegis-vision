"""Displayed user IDs.

* Original format (requirements Section 10): ADM-/SUP-/GRD- followed by 11 digits.
  IDs already issued in this format stay valid.
* Approved 2026-10-27 for Admin and Supervisor accounts: 6 capital letters followed by
  11 digits, e.g. ``KQMTZX48271930561`` (decision C in backend/docs/requirements/
  Approved_Clarifications_and_Decisions_Organization_Admin_Auth.md). New Main Admins get
  this format. The Guard format is still to be decided.
"""

import re
import secrets
import string

from src.domain.value_objects.enums import UserRole

_PREFIXES = {UserRole.ADMIN: "ADM", UserRole.SUPERVISOR: "SUP", UserRole.GUARD: "GRD"}
_DIGITS = 11
_LETTERS = 6

DISPLAY_ID_PATTERN = re.compile(r"^(ADM|SUP|GRD)-\d{11}$")
STAFF_ID_PATTERN = re.compile(r"[A-Z]{6}[0-9]{11}")


def generate_staff_id() -> str:
    """A new Admin or Supervisor ID: 6 random capital letters + 11 random digits."""
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(_LETTERS))
    return f"{letters}{secrets.randbelow(10**_DIGITS):0{_DIGITS}d}"


def is_staff_id(value: str) -> bool:
    return STAFF_ID_PATTERN.fullmatch(value) is not None


SUPERVISOR_ID_PATTERN = re.compile(r"[A-Z]{6}[0-9]{21}")
_SUPERVISOR_DIGITS = 21


def generate_supervisor_id() -> str:
    """A new Supervisor ID: 6 random capital letters + 21 random digits (27 characters), made
    in one piece. Approved 2026-10-27; replaces the 6 + 11 format planned for Supervisors."""
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(_LETTERS))
    return f"{letters}{secrets.randbelow(10**_SUPERVISOR_DIGITS):0{_SUPERVISOR_DIGITS}d}"


def is_supervisor_id(value: str) -> bool:
    return SUPERVISOR_ID_PATTERN.fullmatch(value) is not None


def generate_display_id(role: UserRole) -> str:
    return f"{_PREFIXES[role]}-{secrets.randbelow(10**_DIGITS):0{_DIGITS}d}"


def display_id_matches_role(display_id: str, role: UserRole) -> bool:
    return bool(DISPLAY_ID_PATTERN.match(display_id)) and display_id.startswith(f"{_PREFIXES[role]}-")


def generate_guard_id() -> str:
    """A new Guard ID: 6 random capital letters + 21 random digits (27 characters), in one piece.
    Approved 2026-10-27; same shape as Supervisor IDs, unique across all users."""
    return generate_supervisor_id()


def is_guard_id(value: str) -> bool:
    return is_supervisor_id(value)
