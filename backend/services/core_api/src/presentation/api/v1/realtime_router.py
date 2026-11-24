"""Real-time signals over a WebSocket (decision D-025). Role-compatible: Guards, Supervisors and Admins.

WS     /realtime/ws        1. connect; 2. send {"type": "auth", "access_token": "<token>"} within
                           REALTIME_AUTH_TIMEOUT_SECONDS; 3. receive {"type": "ready", ...}; 4. send {"type": "ping"}
                           at least every REALTIME_HEARTBEAT_SECONDS (answered {"type": "pong"}); events arrive as
                           {"type": "event", "event": ..., "alert_id"?, "incident_id"?, "call_id"?, "state"?, "kind"?,
                           "at"}. When the session stops being valid: {"type": "session_ended", "code", "detail"},
                           then close code 4401 (sign in again) or 4403 (organization inactive / Super Admin token).
GET    /realtime/status    what this server offers: the WebSocket path, heartbeat, voice-call provider and push mode

The token travels in the first message, not in the URL, so it never appears in access logs.
"""

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from src.application.dtos.call_dto import RealtimeStatusResponse
from src.config.settings import Settings
from src.domain.exceptions.domain_errors import DomainError
from src.infrastructure.security.jwt import TENANT_AUDIENCE, access_token_expires_at
from src.presentation.dependencies import MemberDep
from src.presentation.realtime.hub import (
    CLOSE_AUTH_TIMEOUT,
    CLOSE_UNAUTHORIZED,
    RealtimeClient,
    RealtimeHub,
    close_code_for,
    session_ended_message,
)

router = APIRouter(prefix="/realtime", tags=["Real-time"])


@router.get("/status", response_model=RealtimeStatusResponse)
async def realtime_status(request: Request, actor: MemberDep) -> RealtimeStatusResponse:
    settings: Settings = request.app.state.settings
    voice = request.app.state.voice_provider
    return RealtimeStatusResponse(
        heartbeat_seconds=settings.realtime_heartbeat_seconds,
        auth_timeout_seconds=settings.realtime_auth_timeout_seconds,
        voice_call_provider=voice.provider,
        voice_calls_available=voice.configured,
        push_delivery_mode=settings.push_delivery_mode,
    )


async def _refuse(websocket: WebSocket, code: int, message: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json(message)
        await websocket.close(code=code)


async def _receive(websocket: WebSocket, timeout: float) -> dict[str, Any] | None:
    """The next JSON object from the client; None for anything that is not a JSON object."""
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


@router.websocket("/ws")
async def realtime_socket(websocket: WebSocket) -> None:
    settings: Settings = websocket.app.state.settings
    hub: RealtimeHub = websocket.app.state.realtime_hub
    await websocket.accept()
    await hub.ensure_started()
    try:
        first = await _receive(websocket, settings.realtime_auth_timeout_seconds)
    except TimeoutError:
        await _refuse(
            websocket,
            CLOSE_AUTH_TIMEOUT,
            {"type": "error", "code": "AUTH_TIMEOUT", "detail": "Sign-in message missing."},
        )
        return
    except WebSocketDisconnect:
        return
    token = first.get("access_token") if first and first.get("type") == "auth" else None
    if not isinstance(token, str) or not token:
        await _refuse(
            websocket,
            CLOSE_UNAUTHORIZED,
            {"type": "error", "code": "AUTH_REQUIRED", "detail": 'Send {"type": "auth", "access_token": ...} first.'},
        )
        return
    try:
        user = await hub.authenticate(token)
        expires_at = access_token_expires_at(
            token,
            secret=settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
            audience=TENANT_AUDIENCE,
        )
    except DomainError as error:
        await _refuse(websocket, close_code_for(error), session_ended_message(error))
        return

    client = RealtimeClient(websocket=websocket, user=user, token=token, expires_at=expires_at)
    hub.register(client)
    writer = asyncio.create_task(hub.writer(client))
    hub.send(
        client,
        {
            "type": "ready",
            "user_id": str(user.id),
            "role": user.role.value,
            "heartbeat_seconds": settings.realtime_heartbeat_seconds,
            "token_expires_at": expires_at.isoformat(),
        },
    )
    try:
        while not client.closed:
            try:
                message = await _receive(websocket, settings.realtime_heartbeat_seconds * 2)
            except TimeoutError:
                await _refuse(websocket, 1001, {"type": "error", "code": "HEARTBEAT_MISSED", "detail": "No ping."})
                break
            if message is not None and message.get("type") == "ping":
                # Each heartbeat checks the session again: expiry, password change, assignment, organization.
                if await hub.revalidate(client):
                    hub.send(client, {"type": "pong"})
            else:
                hub.send(client, {"type": "error", "code": "INVALID_MESSAGE", "detail": "Only ping is accepted."})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unregister(client)
        client.closed = True
        with contextlib.suppress(asyncio.QueueFull):
            client.queue.put_nowait(None)
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await writer
