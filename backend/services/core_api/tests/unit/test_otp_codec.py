import hashlib
import hmac
import re
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.infrastructure.security.otp import HmacOtpCodec, derive_otp_key

KEY = b"k" * 32


def _settings(**extra: object) -> Settings:
    return Settings(
        database_url="x",
        platform_database_url="x",
        migration_database_url="x",
        jwt_secret_key="j" * 40,
        _env_file=None,  # type: ignore[call-arg]
        **extra,  # type: ignore[arg-type]
    )


def test_codes_are_six_random_digits() -> None:
    codec = HmacOtpCodec(KEY)
    codes = [codec.new_code() for _ in range(2000)]
    assert all(re.fullmatch(r"[0-9]{6}", c) for c in codes)
    assert len(set(codes)) > 1900
    assert any(c.startswith("0") for c in codes)  # leading zeros are kept


def test_code_hash_is_keyed_and_bound_to_the_registration() -> None:
    codec = HmacOtpCodec(KEY)
    registration = uuid4()
    digest = codec.hash_code(registration, "123456")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == codec.hash_code(registration, "123456")
    assert digest != codec.hash_code(registration, "123457")
    assert digest != codec.hash_code(uuid4(), "123456")
    assert digest != HmacOtpCodec(b"z" * 32).hash_code(registration, "123456")
    assert digest != hashlib.sha256(b"123456").hexdigest()  # not a plain, reversible hash


def test_recipient_digest_ignores_capitalization_and_spaces() -> None:
    codec = HmacOtpCodec(KEY)
    assert codec.recipient_digest("Maryam@Example.com") == codec.recipient_digest(" maryam@example.com ")
    assert codec.recipient_digest("maryam@example.com") != codec.recipient_digest("other@example.com")


def test_short_keys_are_refused_and_the_key_is_never_shown() -> None:
    with pytest.raises(ValueError):
        HmacOtpCodec(b"short")
    assert "k" * 32 not in repr(HmacOtpCodec(KEY))


def test_key_comes_from_otp_secret_key_when_set() -> None:
    assert derive_otp_key(_settings(otp_secret_key="s" * 32)) == b"s" * 32


def test_key_is_derived_from_the_jwt_secret_but_is_not_the_jwt_secret() -> None:
    key = derive_otp_key(_settings())
    assert key == hmac.new(b"j" * 40, b"core_api/otp-key/v1", hashlib.sha256).digest()
    assert key != b"j" * 40
