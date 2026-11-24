from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as pyjwt
import pytest

from src.domain.exceptions.domain_errors import AuthenticationFailed
from src.infrastructure.security.jwt import (
    PLATFORM_AUDIENCE,
    TENANT_AUDIENCE,
    create_access_token,
    decode_access_token,
)

SECRET = "unit-test-signing-key-0123456789abcdef"


def make(audience: str) -> str:
    return create_access_token(uuid4(), secret=SECRET, algorithm="HS256", expires_minutes=5, audience=audience)


def test_platform_token_is_refused_by_tenant_routes() -> None:
    with pytest.raises(AuthenticationFailed):
        decode_access_token(make(PLATFORM_AUDIENCE), secret=SECRET, algorithm="HS256", audience=TENANT_AUDIENCE)


def test_tenant_token_is_refused_by_platform_routes() -> None:
    with pytest.raises(AuthenticationFailed):
        decode_access_token(make(TENANT_AUDIENCE), secret=SECRET, algorithm="HS256", audience=PLATFORM_AUDIENCE)


def test_each_token_works_for_its_own_audience() -> None:
    for audience in (TENANT_AUDIENCE, PLATFORM_AUDIENCE):
        decode_access_token(make(audience), secret=SECRET, algorithm="HS256", audience=audience)


def test_non_access_token_with_valid_audience_is_refused() -> None:
    now = datetime.now(UTC)
    token = pyjwt.encode(
        {"sub": str(uuid4()), "type": "refresh", "aud": TENANT_AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationFailed):
        decode_access_token(token, secret=SECRET, algorithm="HS256")
