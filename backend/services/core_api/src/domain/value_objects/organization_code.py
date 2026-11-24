"""The Organization ID shown to people: 6 random capital letters + 11 random digits,
e.g. ``ABCDEF12345678901`` (17 characters, no prefix or separator).

It is separate from the organization's database key and from campus and user IDs. It is
reserved when a registration is submitted, kept through every retry of that registration,
attached to the organization permanently, and never reused.

IDs issued before migration 0005 used ``ORG-`` + 11 digits. They are not rewritten and
remain valid; only new IDs use the format above.
"""

import re
import secrets
import string

_LETTERS = 6
_DIGITS = 11
ORGANIZATION_CODE_PATTERN = re.compile(r"[A-Z]{6}[0-9]{11}")
LEGACY_ORGANIZATION_CODE_PATTERN = re.compile(r"ORG-[0-9]{11}")


def generate_organization_code() -> str:
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(_LETTERS))
    return f"{letters}{secrets.randbelow(10**_DIGITS):0{_DIGITS}d}"


def is_organization_code(value: str) -> bool:
    """True for a valid Organization ID in the current format or the earlier ``ORG-`` format."""
    return (
        ORGANIZATION_CODE_PATTERN.fullmatch(value) is not None
        or LEGACY_ORGANIZATION_CODE_PATTERN.fullmatch(value) is not None
    )
