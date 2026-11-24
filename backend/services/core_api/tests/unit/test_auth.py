from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as pyjwt
import pytest

from src.domain.exceptions.domain_errors import AuthenticationFailed
from src.infrastructure.security.jwt import create_access_token, decode_access_token

SECRET = "unit-test-signing-key-0123456789abcdef"


def test_token_round_trip_returns_user_id() -> None:
    user_id = uuid4()
    token = create_access_token(user_id, secret=SECRET, algorithm="HS256", expires_minutes=5)
    assert decode_access_token(token, secret=SECRET, algorithm="HS256") == user_id


def test_expired_token_is_rejected() -> None:
    token = create_access_token(
        uuid4(), secret=SECRET, algorithm="HS256", expires_minutes=5, now=datetime.now(UTC) - timedelta(hours=1)
    )
    with pytest.raises(AuthenticationFailed):
        decode_access_token(token, secret=SECRET, algorithm="HS256")


def test_token_signed_with_other_key_is_rejected() -> None:
    token = create_access_token(
        uuid4(), secret="another-key-0123456789abcdef-0000", algorithm="HS256", expires_minutes=5
    )
    with pytest.raises(AuthenticationFailed):
        decode_access_token(token, secret=SECRET, algorithm="HS256")


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": str(uuid4()), "type": "refresh"},
        {"sub": "not-a-uuid", "type": "access"},
    ],
)
def test_wrong_token_type_or_subject_is_rejected(claims: dict) -> None:
    now = datetime.now(UTC)
    token = pyjwt.encode({**claims, "iat": now, "exp": now + timedelta(minutes=5)}, SECRET, algorithm="HS256")
    with pytest.raises(AuthenticationFailed):
        decode_access_token(token, secret=SECRET, algorithm="HS256")
