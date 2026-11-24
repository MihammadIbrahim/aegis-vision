"""One-time codes stored as keyed hashes (HMAC-SHA-256).

A 6-digit code has only a million possible values, so an unkeyed hash could be reversed
by trying them all. With a secret key, a leaked database row is useless without the key.
Codes themselves are never stored or logged.
"""

import hashlib
import hmac
import secrets
from uuid import UUID

from src.config.settings import Settings

_CODE_DIGITS = 6


def derive_otp_key(settings: Settings) -> bytes:
    """OTP_SECRET_KEY if set; otherwise a key derived from JWT_SECRET_KEY, so codes are
    never hashed with the token-signing key itself."""
    if settings.otp_secret_key is not None:
        return settings.otp_secret_key.get_secret_value().encode()
    return hmac.new(
        settings.jwt_secret_key.get_secret_value().encode(), b"core_api/otp-key/v1", hashlib.sha256
    ).digest()


class HmacOtpCodec:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("The OTP key must be at least 32 bytes long.")
        self._key = key

    def __repr__(self) -> str:
        return "HmacOtpCodec(key=<hidden>)"

    def new_code(self) -> str:
        return f"{secrets.randbelow(10**_CODE_DIGITS):0{_CODE_DIGITS}d}"

    def hash_code(self, registration_id: UUID, code: str) -> str:
        return self._hmac(f"otp:ORGANIZATION_REGISTRATION:{registration_id}:{code}")

    def hash_login_code(self, login_id: UUID, code: str) -> str:
        return self._hmac(f"otp:ADMIN_LOGIN:{login_id}:{code}")

    def hash_purpose_code(self, purpose: str, subject_id: UUID, code: str) -> str:
        return self._hmac(f"otp:{purpose}:{subject_id}:{code}")

    def hash_token(self, purpose: str, token: str) -> str:
        return self._hmac(f"token:{purpose}:{token}")

    def recipient_digest(self, email: str) -> str:
        return self._hmac(f"recipient:{email.strip().lower()}")

    def _hmac(self, message: str) -> str:
        return hmac.new(self._key, message.encode(), hashlib.sha256).hexdigest()
