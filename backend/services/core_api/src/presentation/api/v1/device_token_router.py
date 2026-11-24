"""Device push tokens of the Guard app (decision D-025).

POST   /guard/device-tokens             {token, platform: ANDROID | IOS, provider: FCM | APNS, app_version?} -> 201
DELETE /guard/device-tokens/{token}     204; only the caller's own token (sign-out)

Tokens are stored whether or not push delivery is on (PUSH_DELIVERY_MODE), so phones are ready when it is switched
on. A token belongs to one account at a time; the tokens of an account that stops being active are deleted. Tokens are
never returned by any route and never written to the audit log.
"""

from fastapi import APIRouter, Request, status
from sqlalchemy import text

from src.application.dtos.call_dto import DeviceTokenRequest, DeviceTokenResponse
from src.domain.exceptions.domain_errors import NotFound
from src.infrastructure.database.connection import bind_actor
from src.presentation.dependencies import GuardDep, SessionDep

router = APIRouter(prefix="/guard/device-tokens", tags=["Guard app"])


@router.post("", response_model=DeviceTokenResponse, status_code=status.HTTP_201_CREATED)
async def register_device_token(
    body: DeviceTokenRequest, request: Request, guard: GuardDep, session: SessionDep
) -> DeviceTokenResponse:
    token_id = await session.scalar(
        text("SELECT app_device_token_register(:token, :platform, :provider, :version)"),
        {"token": body.token, "platform": body.platform, "provider": body.provider, "version": body.app_version},
    )
    await session.commit()
    return DeviceTokenResponse(
        id=token_id,
        platform=body.platform,
        provider=body.provider,
        push_delivery_mode=request.app.state.settings.push_delivery_mode,
    )


@router.delete("/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_device_token(token: str, guard: GuardDep, session: SessionDep) -> None:
    await bind_actor(session, guard.id)
    result = await session.execute(
        text("DELETE FROM device_push_tokens WHERE token = :token AND user_id = :me"), {"token": token, "me": guard.id}
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFound("This device token is not registered for you.", code="DEVICE_TOKEN_NOT_FOUND")
    await session.commit()
