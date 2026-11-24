from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.domain.exceptions.domain_errors import (
    AccountLocked,
    AuthenticationFailed,
    Conflict,
    CurrentPasswordIncorrect,
    DomainError,
    Gone,
    InvalidStateTransition,
    NotFound,
    OperationFailed,
    PermissionDenied,
    RateLimited,
    ServiceUnavailable,
    UpstreamServiceFailed,
    ValidationFailed,
    VerificationFailed,
)

_STATUS_CODES: dict[type[DomainError], int] = {
    AuthenticationFailed: 401,
    PermissionDenied: 403,
    NotFound: 404,
    InvalidStateTransition: 409,
    Conflict: 409,
    AccountLocked: 429,
    CurrentPasswordIncorrect: 403,
    ValidationFailed: 422,
    VerificationFailed: 400,
    RateLimited: 429,
    Gone: 410,
    ServiceUnavailable: 503,
    UpstreamServiceFailed: 502,
    OperationFailed: 500,
}


def status_for(exc: DomainError) -> int:
    """The HTTP status a domain error is answered with (also used per item by the offline sync)."""
    return next((code for kind, code in _STATUS_CODES.items() if isinstance(exc, kind)), 400)


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    """Body: ``{"detail": message}``, plus ``"code"``, ``"errors"`` and ``"retry_after_seconds"``
    only for errors that carry them (older errors keep exactly their previous shape)."""
    assert isinstance(exc, DomainError)
    status_code = next((code for kind, code in _STATUS_CODES.items() if isinstance(exc, kind)), 400)
    body: dict[str, object] = {"detail": str(exc)}
    headers: dict[str, str] = {}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if exc.code is not None:
        body["code"] = exc.code
    if exc.errors is not None:
        body["errors"] = exc.errors
    if exc.retry_after is not None:
        body["retry_after_seconds"] = exc.retry_after
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(body, status_code=status_code, headers=headers or None)


async def _handle_request_validation(_: Request, exc: Exception) -> JSONResponse:
    """Like FastAPI's default 422, but without echoing submitted values back, so a
    password sent in a malformed request never appears in a response."""
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
