"""Decision D-025 (migration 0030): the responder roster, device push tokens, push claiming, in-app voice calls
(LiveKit tokens, permissions, campus isolation, audit) and the real-time WebSocket (authentication, delivery only to
allowed users, forced sign-out on an assignment change or a suspended organization, Super Admin refusal).

Uses the ``site`` fixture of the alert tests: Main Campus with Guards ga (Lobby), g_floor, g_building, g_roof, g_gate,
g_none; the campus camera is in the Lobby, so a detection reaches ga, g_floor and g_building. Campus B has gb."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.application.interfaces.i_push_sender import PushMessage, PushOutcome
from src.config.settings import Settings
from src.infrastructure.background.push_dispatch import dispatch_once
from src.main import create_app
from tests.conftest import (
    DETECTION_TOKEN,
    JWT_ALGORITHM,
    JWT_SECRET,
    DatabaseUrls,
    platform_auth_header,
    super_admin,  # noqa: F401
)
from tests.integration.test_alerts import call, new_alert, ok, site  # noqa: F401
from tests.integration.test_locations import ok as loc_ok
from tests.integration.test_ownership_and_deletion import count, headers, ucp  # noqa: F401

LIVEKIT_KEY = "APItestkey0001"
LIVEKIT_SECRET = "test-only-livekit-secret-0123456789abcdef"
LIVEKIT_URL = "wss://cctv-test.livekit.example"
FCM_TOKEN = "fcm-token-" + "a" * 40


@asynccontextmanager
async def _app_client(urls: DatabaseUrls, **extra: Any) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        database_url=urls.app,
        platform_database_url=urls.platform,
        migration_database_url=urls.migration,
        jwt_secret_key=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        detection_ingest_token=DETECTION_TOKEN,
        _env_file=None,
        **extra,
    )
    engine = create_async_engine(urls.app, poolclass=NullPool)
    platform_engine = create_async_engine(urls.platform, poolclass=NullPool)
    app = create_app(settings, engine=engine, platform_engine=platform_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await app.state.realtime_hub.stop()
    await engine.dispose()
    await platform_engine.dispose()


@pytest.fixture
async def voice(migrated_database: DatabaseUrls) -> AsyncIterator[AsyncClient]:
    """A server with VOICE_CALL_PROVIDER=livekit (test keys; no LiveKit server is contacted except to close rooms,
    which fails quietly here)."""
    async with _app_client(
        migrated_database,
        voice_call_provider="livekit",
        livekit_url=LIVEKIT_URL,
        livekit_api_key=LIVEKIT_KEY,
        livekit_api_secret=LIVEKIT_SECRET,
        livekit_request_timeout_seconds=0.2,
    ) as http:
        yield http


def _app(client: AsyncClient) -> Any:
    transport = client._transport
    assert isinstance(transport, ASGITransport)
    return transport.app


# --- Responder roster ---------------------------------------------------------------------------------------------


async def test_recipient_guards_see_who_is_responding_without_guard_ids(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    assert await ok(registration_client, site["ga"], "GET", f"/alerts/{alert_id}/responders") == []
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/acknowledge")
    await ok(registration_client, site["g_floor"], "POST", f"/alerts/{alert_id}/responding")
    roster = await ok(registration_client, site["ga"], "GET", f"/alerts/{alert_id}/responders")
    assert [(r["full_name"], r["state"], r["is_me"]) for r in roster] == [
        ("g_floor", "RESPONDING", False),
        ("Gul A", "ACKNOWLEDGED", True),
    ]
    assert all("guard_id" not in r for r in roster)
    staff_view = await ok(registration_client, site["sa"], "GET", f"/alerts/{alert_id}/responders")
    assert len(staff_view) == 2 and not any(r["is_me"] for r in staff_view)
    # A Guard the alert never reached, and another campus, learn nothing.
    assert (await call(registration_client, site["g_roof"], "GET", f"/alerts/{alert_id}/responders")).status_code == 404
    assert (await call(registration_client, site["b"], "GET", f"/alerts/{alert_id}/responders")).status_code == 404


# --- Guard profile context (screen 10) ----------------------------------------------------------------------------


async def test_guard_profile_names_campus_status_and_mode(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    profile = await ok(registration_client, site["ga"], "GET", "/guard/profile")
    assert (profile["campus_name"], profile["status"], profile["operational_mode"]) == (
        "Main Campus",
        "ACTIVE",
        "STATIONED",
    )
    standby = await ok(registration_client, site["g_none"], "GET", "/guard/profile")
    assert standby["operational_mode"] == "STANDBY" and "password_hash" not in json.dumps(standby)


# --- Device push tokens and push claiming -------------------------------------------------------------------------


async def test_device_tokens_belong_to_one_guard_and_go_with_deactivation(
    registration_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute
) -> None:
    body = {"token": FCM_TOKEN, "platform": "ANDROID", "provider": "FCM", "app_version": "1.0.0"}
    response = await registration_client.post("/api/v1/guard/device-tokens", json=body, headers=headers(site["ga"]))
    assert response.status_code == 201 and response.json()["push_delivery_mode"] == "none"
    assert "token" not in response.json()
    # The same phone signed in by another Guard: the token moves.
    await ok(registration_client, site["g_floor"], "POST", "/guard/device-tokens", body)
    owners = owner_query("SELECT user_id FROM device_push_tokens WHERE token = %(t)s", {"t": FCM_TOKEN})
    assert owners == [(site["g_floor"]["id"],)]
    # Only one's own token can be removed; staff have no such route.
    assert (
        await call(registration_client, site["ga"], "DELETE", f"/guard/device-tokens/{FCM_TOKEN}")
    ).status_code == 404
    assert (await call(registration_client, site["sa"], "POST", "/guard/device-tokens", body)).status_code == 403
    await ok(registration_client, site["g_floor"], "DELETE", f"/guard/device-tokens/{FCM_TOKEN}")
    # Deactivation forgets the Guard's phones.
    await ok(registration_client, site["ga"], "POST", "/guard/device-tokens", body)
    owner_execute("UPDATE users SET status = 'LEFT' WHERE id = %(u)s", {"u": site["ga"]["id"]})
    assert count(owner_query, "SELECT 1 FROM device_push_tokens") == 0


async def test_super_admin_tokens_cannot_register_devices_or_calls(
    registration_client: AsyncClient, site: dict[str, Any], super_admin: UUID
) -> None:
    sa = platform_auth_header(super_admin)
    body = {"token": FCM_TOKEN, "platform": "ANDROID", "provider": "FCM"}
    for method, path, payload in (
        ("POST", "/api/v1/guard/device-tokens", body),
        ("GET", "/api/v1/calls", None),
        ("GET", "/api/v1/realtime/status", None),
    ):
        response = await registration_client.request(method, path, json=payload, headers=sa)
        assert response.status_code == 403 and response.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED", path


class _RecordingSender:
    def __init__(self, outcome: PushOutcome = PushOutcome.SENT) -> None:
        self.sent: list[tuple[str, PushMessage]] = []
        self.outcome = outcome

    @property
    def mode(self) -> str:
        return "fcm"

    async def send(self, token: str, message: PushMessage) -> PushOutcome:
        self.sent.append((token, message))
        return self.outcome


async def test_alert_notifications_are_claimed_for_push_once_and_carry_no_names(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    body = {"token": FCM_TOKEN, "platform": "ANDROID", "provider": "FCM"}
    await ok(registration_client, site["ga"], "POST", "/guard/device-tokens", body)
    alert_id = await new_alert(registration_client, site["camera_a"])
    app = _app(registration_client)
    sender = _RecordingSender()
    assert await dispatch_once(app.state.session_factory, sender, max_age_seconds=600) == 1
    token, message = sender.sent[0]
    assert token == FCM_TOKEN and message.data == {
        "kind": "ALERT_NEW",
        "notification_id": message.data["notification_id"],
        "alert_id": alert_id,
    }
    assert "Gate" not in message.title + message.body and message.channel_id == "security-alarm"
    # Claimed once: nothing is pushed twice, even by another process.
    assert await dispatch_once(app.state.session_factory, sender, max_age_seconds=600) == 0
    # A token the provider rejects is forgotten.
    await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")
    assert (
        await dispatch_once(app.state.session_factory, _RecordingSender(PushOutcome.INVALID_TOKEN), max_age_seconds=600)
        == 0
    )
    assert count(owner_query, "SELECT 1 FROM device_push_tokens") == 0


# --- Voice calls --------------------------------------------------------------------------------------------------


async def test_without_a_provider_calls_are_refused_and_nothing_is_written(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    active = await ok(registration_client, site["ga"], "GET", f"/alerts/{alert_id}/calls/active")
    assert active == {"voice_calls_available": False, "provider": "none", "call": None}
    response = await call(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/calls")
    assert response.status_code == 503 and response.json()["code"] == "VOICE_CALL_NOT_CONFIGURED"
    assert count(owner_query, "SELECT 1 FROM call_sessions") == 0
    status = await ok(registration_client, site["ga"], "GET", "/realtime/status")
    assert status["voice_calls_available"] is False and status["websocket_path"] == "/api/v1/realtime/ws"


async def test_an_alert_call_rings_staff_and_recipients_and_signs_audio_only_tokens(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    started = await voice.post(f"/api/v1/alerts/{alert_id}/calls", headers=headers(site["ga"]))
    assert started.status_code == 201, started.text
    body = started.json()
    call_id = body["call"]["id"]
    assert body["outcome"] == "STARTED" and body["call"]["my_state"] == "JOINED" and body["call"]["can_end"]
    claims = jwt.decode(body["connection"]["token"], LIVEKIT_SECRET, algorithms=["HS256"])
    assert claims["iss"] == LIVEKIT_KEY and claims["sub"] == str(site["ga"]["id"])
    assert claims["video"] == {
        "room": f"cctv-call-{call_id}",
        "roomJoin": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": False,
        "canPublishSources": ["microphone"],
    }
    assert body["connection"]["url"] == LIVEKIT_URL and claims["exp"] - claims["iat"] == 900
    states = {p["full_name"]: p["state"] for p in body["call"]["participants"]}
    # Staff of the campus and the alert's other Guards ring; Guards the alert did not reach do not.
    assert states["Gul A"] == "JOINED" and states["Sana A"] == "INVITED" and states["g_floor"] == "INVITED"
    assert "g_roof" not in states and "Gohar B" not in states
    # Ringing shows up for the polling fallback, and starting again joins the same call.
    assert [c["id"] for c in await ok(voice, site["g_floor"], "GET", "/calls")] == [call_id]
    again = await voice.post(f"/api/v1/alerts/{alert_id}/calls", headers=headers(site["sa"]))
    assert again.status_code == 200 and again.json()["outcome"] == "JOINED" and again.json()["call"]["id"] == call_id
    # Declining, isolation, and who may end it for everyone.
    declined = await ok(voice, site["g_building"], "POST", f"/calls/{call_id}/decline")
    assert declined["my_state"] == "DECLINED"
    assert (await call(voice, site["g_roof"], "GET", f"/calls/{call_id}")).status_code == 404
    assert (await call(voice, site["b"], "POST", f"/calls/{call_id}/join")).status_code == 404
    await ok(voice, site["g_floor"], "POST", f"/calls/{call_id}/join")
    refused = await call(voice, site["g_floor"], "POST", f"/calls/{call_id}/end")
    assert refused.status_code == 403 and refused.json()["code"] == "CALL_END_NOT_ALLOWED"
    ended = await ok(voice, site["sa"], "POST", f"/calls/{call_id}/end")
    assert ended["status"] == "ENDED" and ended["end_reason"] == "ENDED_BY_USER"
    rejoin = await call(voice, site["ga"], "POST", f"/calls/{call_id}/join")
    assert rejoin.status_code == 409 and rejoin.json()["code"] == "CALL_ENDED"
    actions = [r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE action LIKE 'ALERT_CALL%%'")]
    assert sorted(set(actions)) == [
        "ALERT_CALL_DECLINED",
        "ALERT_CALL_ENDED",
        "ALERT_CALL_JOINED",
        "ALERT_CALL_STARTED",
    ]
    metadata = owner_query("SELECT metadata::text FROM organization_audit_logs WHERE action LIKE 'ALERT_CALL%%'")
    assert all("token" not in m[0] and LIVEKIT_SECRET not in m[0] for m in metadata)
    categories = {
        r[0] for r in owner_query("SELECT category FROM organization_audit_logs WHERE action LIKE 'ALERT_CALL%%'")
    }
    assert categories <= {"ALERTS", "GUARD_RESPONSE"}


async def test_a_call_ends_when_everyone_leaves_or_the_alert_closes(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any]
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    call_id = (await ok(voice, site["ga"], "POST", f"/alerts/{alert_id}/calls"))["call"]["id"]
    left = await ok(voice, site["ga"], "POST", f"/calls/{call_id}/leave")
    assert left["call_ended"] and left["call"]["end_reason"] == "EVERYONE_LEFT"
    assert {p["state"] for p in left["call"]["participants"] if not p["is_me"]} == {"MISSED"}
    second = (await ok(voice, site["sa"], "POST", f"/alerts/{alert_id}/calls"))["call"]["id"]
    assert second != call_id
    await ok(voice, site["sa"], "POST", f"/alerts/{alert_id}/resolve", {"note": "Handled"})
    closed = await ok(voice, site["sa"], "GET", f"/calls/{second}")
    assert closed["status"] == "ENDED" and closed["end_reason"] == "TARGET_CLOSED"
    refused = await call(voice, site["ga"], "POST", f"/alerts/{alert_id}/calls")
    assert refused.status_code == 409 and refused.json()["code"] == "ALERT_CLOSED"


async def test_case_calls_follow_the_case_chat_rules(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any]
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    case = await ok(
        voice,
        site["sa"],
        "POST",
        "/incidents",
        {"title": "Weapon at the lobby", "priority": "HIGH", "alert_ids": [alert_id]},
    )
    # A Guard recipient of the linked alert takes part; a Guard outside it does not.
    started = await ok(voice, site["ga"], "POST", f"/incidents/{case['id']}/calls")
    assert started["call"]["scope"] == "INCIDENT" and started["call"]["incident_id"] == case["id"]
    assert (await call(voice, site["g_roof"], "GET", f"/incidents/{case['id']}/calls/active")).status_code == 404
    assert (await call(voice, site["b"], "POST", f"/incidents/{case['id']}/calls")).status_code == 404
    active = await ok(voice, site["g_floor"], "GET", f"/incidents/{case['id']}/calls/active")
    assert active["voice_calls_available"] and active["call"]["my_state"] == "INVITED"


# --- Real-time WebSocket ------------------------------------------------------------------------------------------


class Closed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class Socket:
    """Drives the ASGI WebSocket endpoint in the test's own event loop."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._in: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._out: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def open(self) -> "Socket":
        path = "/api/v1/realtime/ws"
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        }
        self._in.put_nowait({"type": "websocket.connect"})
        self._task = asyncio.create_task(self._app(scope, self._in.get, self._out.put))
        accepted = await asyncio.wait_for(self._out.get(), 5)
        assert accepted["type"] == "websocket.accept"
        return self

    async def send(self, message: dict[str, Any]) -> None:
        await self._in.put({"type": "websocket.receive", "text": json.dumps(message)})

    async def receive(self, timeout: float = 5) -> dict[str, Any]:
        message = await asyncio.wait_for(self._out.get(), timeout)
        if message["type"] == "websocket.close":
            raise Closed(message["code"])
        return json.loads(message["text"])

    async def event(self, name: str, timeout: float = 5) -> dict[str, Any]:
        while True:
            message = await self.receive(timeout)
            if message.get("type") == "event" and message["event"] == name:
                return message

    async def nothing(self, timeout: float = 0.6) -> None:
        with pytest.raises(TimeoutError):
            await self.receive(timeout)

    async def close(self) -> None:
        await self._in.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 5)
            except Exception:  # noqa: BLE001 - clean-up: never fail a test while closing the socket
                self._task.cancel()


async def _connected(client: AsyncClient, user: dict[str, Any]) -> Socket:
    app = _app(client)
    socket = await Socket(app).open()
    token = headers(user)["Authorization"].removeprefix("Bearer ")
    await socket.send({"type": "auth", "access_token": token})
    ready = await socket.receive()
    assert ready["type"] == "ready" and ready["user_id"] == str(user["id"]), ready
    await asyncio.wait_for(app.state.realtime_hub.listening.wait(), 5)
    return socket


async def test_the_socket_needs_a_valid_sign_in_first(
    registration_client: AsyncClient, site: dict[str, Any], super_admin: UUID
) -> None:
    app = _app(registration_client)
    socket = await Socket(app).open()
    await socket.send({"type": "hello"})
    assert (await socket.receive())["code"] == "AUTH_REQUIRED"
    with pytest.raises(Closed) as closed:
        await socket.receive()
    assert closed.value.code == 4401
    await socket.close()
    platform = await Socket(app).open()
    token = platform_auth_header(super_admin)["Authorization"].removeprefix("Bearer ")
    await platform.send({"type": "auth", "access_token": token})
    assert (await platform.receive())["code"] == "SUPER_ADMIN_NOT_ALLOWED"
    with pytest.raises(Closed) as refused:
        await platform.receive()
    assert refused.value.code == 4403
    await platform.close()


async def test_signals_reach_only_people_who_may_see_the_alert(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    ga = await _connected(registration_client, site["ga"])
    roof = await _connected(registration_client, site["g_roof"])
    other_campus = await _connected(registration_client, site["sb"])
    staff = await _connected(registration_client, site["sa"])
    await ga.send({"type": "ping"})
    assert (await ga.receive())["type"] == "pong"
    alert_id = await new_alert(registration_client, site["camera_a"])
    assigned = await ga.event("alert.assigned")
    assert assigned["alert_id"] == alert_id and set(assigned) <= {"type", "event", "alert_id", "at", "state", "kind"}
    assert (await staff.event("alert.updated"))["alert_id"] == alert_id
    await ok(registration_client, site["sa"], "POST", f"/alerts/{alert_id}/messages", {"body": "Go to the lobby"})
    message = await ga.event("alert.message")
    assert message["alert_id"] == alert_id and "Go to the lobby" not in json.dumps(message)
    await roof.nothing()
    await other_campus.nothing()
    for socket in (ga, roof, other_campus, staff):
        await socket.close()


async def test_an_assignment_change_ends_the_guards_socket_at_once(
    registration_client: AsyncClient, site: dict[str, Any]
) -> None:
    socket = await _connected(registration_client, site["ga"])
    await loc_ok(registration_client, site["a"], "DELETE", f"/guard-assignments/{site['ga']['display_id']}")
    ended = await socket.receive()
    assert ended == {
        "type": "session_ended",
        "code": "ASSIGNMENT_CHANGED",
        "detail": "Your operational assignment changed. Please sign in again.",
    }
    with pytest.raises(Closed) as closed:
        await socket.receive()
    assert closed.value.code == 4401
    await socket.close()


async def test_suspending_the_organization_ends_every_socket(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute
) -> None:
    sockets = [await _connected(registration_client, site[name]) for name in ("ga", "sa")]
    owner_execute("UPDATE organizations SET status = 'SUSPENDED' WHERE id = %(o)s", {"o": site["org"]})
    for socket in sockets:
        assert (await socket.receive())["code"] == "ORGANIZATION_INACTIVE"
        with pytest.raises(Closed) as closed:
            await socket.receive()
        assert closed.value.code == 4403
        await socket.close()


async def test_call_invitations_ring_over_the_socket(
    registration_client: AsyncClient, voice: AsyncClient, site: dict[str, Any]
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"])
    socket = await _connected(voice, site["g_floor"])
    call_id = (await ok(voice, site["ga"], "POST", f"/alerts/{alert_id}/calls"))["call"]["id"]
    invited = await socket.event("call.invited")
    assert invited["call_id"] == call_id and invited["alert_id"] == alert_id
    await ok(voice, site["ga"], "POST", f"/calls/{call_id}/end")
    ended = await socket.event("call.updated")
    while ended.get("state") != "ENDED":
        ended = await socket.event("call.updated")
    await socket.close()


async def test_unknown_calls_and_targets_are_not_found(registration_client: AsyncClient, site: dict[str, Any]) -> None:
    """An unknown call or target is simply not found (no information about other campuses)."""
    assert (await call(registration_client, site["ga"], "GET", f"/calls/{uuid4()}")).status_code == 404
    response = await call(registration_client, site["ga"], "GET", f"/alerts/{uuid4()}/calls/active")
    assert response.status_code == 404 and response.json()["code"] == "ALERT_NOT_FOUND"
