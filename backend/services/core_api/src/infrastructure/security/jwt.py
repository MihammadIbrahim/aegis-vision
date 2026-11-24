"""Access tokens (PyJWT, HS256 by default).

Each token names its audience: ``tenant`` for organization users and ``platform``
for the Super Admin. A token is only accepted by routes of its own audience, so a
Super Admin token cannot call organization routes and the reverse.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from src.domain.exceptions.domain_errors import AuthenticationFailed

_ACCESS = "access"
TENANT_AUDIENCE = "tenant"
PLATFORM_AUDIENCE = "platform"


def create_access_token(
    user_id: UUID,
    *,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
    audience: str = TENANT_AUDIENCE,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    claims: dict[str, Any] = {**(extra_claims or {})}
    claims |= {
        "sub": str(user_id),
        "type": _ACCESS,
        "aud": audience,
        "iat": issued_at,
        # The standard "iat" has whole seconds only; this exact time lets a session revocation in the
        # same second still end tokens issued before it (decision D-018).
        "iat_us": int(issued_at.timestamp() * 1_000_000),
        "exp": issued_at + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(claims, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str, audience: str = TENANT_AUDIENCE) -> UUID:
    """Return the account ID in a valid access token for ``audience``. The token
    carries no role or campus: those are always read fresh from the database."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            audience=audience,
            options={"require": ["exp", "iat", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed("Invalid or expired token.") from exc
    if claims.get("type") != _ACCESS:
        raise AuthenticationFailed("Invalid or expired token.")
    try:
        return UUID(claims["sub"])
    except ValueError as exc:
        raise AuthenticationFailed("Invalid or expired token.") from exc


def decode_access_token_issued_at(
    token: str, *, secret: str, algorithm: str, audience: str = TENANT_AUDIENCE
) -> tuple[UUID, datetime]:
    """Like ``decode_access_token``, also returning when the token was issued, so a token
    from before the account's last password change can be refused. Exact to the microsecond when
    the token carries ``iat_us``; otherwise whole seconds (see ``issued_time_is_exact``)."""
    user_id = decode_access_token(token, secret=secret, algorithm=algorithm, audience=audience)
    claims = jwt.decode(token, secret, algorithms=[algorithm], audience=audience)
    if isinstance(claims.get("iat_us"), int):
        return user_id, datetime.fromtimestamp(claims["iat_us"] / 1_000_000, UTC)
    return user_id, datetime.fromtimestamp(int(claims["iat"]), UTC)


def issued_time_is_exact(issued_at: datetime) -> bool:
    """False for a time from the whole-second "iat" claim (tokens without ``iat_us``)."""
    return issued_at.microsecond != 0


LOGIN_METHOD_NORMAL = "NORMAL"
LOGIN_METHOD_RECOVERY = "RECOVERY"
_LOGIN_METHODS = {LOGIN_METHOD_NORMAL, LOGIN_METHOD_RECOVERY}


@dataclass(frozen=True, slots=True)
class PlatformTokenClaims:
    admin_id: UUID
    token_version: int
    login_method: str  # NORMAL or RECOVERY: how this session was opened


def create_platform_token(
    admin_id: UUID,
    token_version: int,
    *,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    login_method: str = LOGIN_METHOD_NORMAL,
) -> str:
    if login_method not in _LOGIN_METHODS:
        raise ValueError(f"unknown login method {login_method!r}")
    return create_access_token(
        admin_id,
        secret=secret,
        algorithm=algorithm,
        expires_minutes=expires_minutes,
        audience=PLATFORM_AUDIENCE,
        extra_claims={"ver": token_version, "lm": login_method},
    )


def decode_platform_token(token: str, *, secret: str, algorithm: str) -> PlatformTokenClaims:
    """Like decode_access_token for the `platform` audience, plus the token version, which
    must still match the account's current version (it changes on credential changes), and
    the login method. Tokens issued before the method was recorded count as NORMAL."""
    admin_id = decode_access_token(token, secret=secret, algorithm=algorithm, audience=PLATFORM_AUDIENCE)
    claims = jwt.decode(token, options={"verify_signature": False})  # already verified above
    version = claims.get("ver")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise AuthenticationFailed("Invalid or expired token.")
    method = claims.get("lm", LOGIN_METHOD_NORMAL)
    if method not in _LOGIN_METHODS:
        raise AuthenticationFailed("Invalid or expired token.")
    return PlatformTokenClaims(admin_id=admin_id, token_version=version, login_method=method)


def access_token_expires_at(token: str, *, secret: str, algorithm: str, audience: str = TENANT_AUDIENCE) -> datetime:
    """When a valid access token stops working (the real-time WebSocket closes then, decision D-025)."""
    try:
        claims = jwt.decode(token, secret, algorithms=[algorithm], audience=audience)
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed("Invalid or expired token.") from exc
    return datetime.fromtimestamp(int(claims["exp"]), UTC)
