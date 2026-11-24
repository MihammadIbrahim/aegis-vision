"""The real-time hub (decision D-025): delivers database signals to connected WebSocket clients.

How it works
* Triggers of migration 0030 call ``pg_notify('cctv_realtime', json)`` with an event name and IDs only. Each API
  process keeps one connection that LISTENs on the channel, so signals reach every process, whichever process (or
  database function, or background task) caused the change; nothing is sent before the change is committed.
* A signal for one user (``alert.assigned``, ``notification``, ``incident.invited``, ``incident.removed``,
  ``call.invited``) goes to that user's connections. A signal about an alert, a case or a call goes to the connected
  Admins and Supervisors of the same campus and to the connected Guards allowed to see it
  (``app_comm_target_visible``, checked as that Guard under Row-Level Security, briefly cached).
* ``session.revoked`` (password change, Guard assignment change, deactivation) and ``organization.status`` make the
  hub check the affected connections again with the rules of every REST route; a connection that no longer passes is
  told why (``ASSIGNMENT_CHANGED``, ``ORGANIZATION_INACTIVE``, ...) and closed.
* Clients receive signals, never content: they read messages, alerts and calls through the REST routes.

Limits: delivery is best effort (a client that was offline catches up by reading the REST routes; the Guard app also
polls as a fallback). Each process serves only the connections it accepted.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.config.settings import Settings
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import AuthenticationFailed, DomainError, PermissionDenied
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.connection import bind_actor
from src.presentation.dependencies import authenticate_tenant_token

logger = logging.getLogger(__name__)

CHANNEL = "cctv_realtime"
CLOSE_UNAUTHORIZED = 4401  # sign in again
CLOSE_FORBIDDEN = 4403  # organization inactive, or a Super Admin token
CLOSE_AUTH_TIMEOUT = 4408
CLOSE_OVERLOADED = 4429
CLOSE_GOING_AWAY = 1001

USER_EVENTS = frozenset({"alert.assigned", "notification", "incident.invited", "incident.removed", "call.invited"})
SCOPED_EVENTS = frozenset({"alert.updated", "alert.message", "incident.message", "incident.updated", "call.updated"})
_POSITIVE_TTL = 30.0
_NEGATIVE_TTL = 5.0
_QUEUE_LIMIT = 200


def close_code_for(error: DomainError) -> int:
    return CLOSE_FORBIDDEN if isinstance(error, PermissionDenied) else CLOSE_UNAUTHORIZED


def session_ended_message(error: DomainError) -> dict[str, Any]:
    return {"type": "session_ended", "code": error.code or "SESSION_EXPIRED", "detail": str(error)}


@dataclass(eq=False)
class RealtimeClient:
    websocket: WebSocket
    user: User
    token: str
    expires_at: datetime
    queue: asyncio.Queue[dict[str, Any] | None] = field(default_factory=lambda: asyncio.Queue(_QUEUE_LIMIT))
    visible: dict[tuple[str, str], tuple[bool, float]] = field(default_factory=dict)
    closed: bool = False

    @property
    def is_staff(self) -> bool:
        return self.user.role in (UserRole.ADMIN, UserRole.SUPERVISOR)


def _uuid(value: Any) -> UUID | None:
    try:
        return None if value is None else UUID(str(value))
    except ValueError:
        return None


def client_event(signal: dict[str, Any], event: str | None = None) -> dict[str, Any]:
    """What a client receives: the event name and IDs (never content)."""
    out: dict[str, Any] = {"type": "event", "event": event or signal["e"], "at": datetime.now(UTC).isoformat()}
    for key, name in (("alert", "alert_id"), ("incident", "incident_id"), ("call", "call_id")):
        if signal.get(key) is not None:
            out[name] = str(signal[key])
    for key in ("state", "kind"):
        if signal.get(key) is not None:
            out[key] = str(signal[key])
    return out


class RealtimeHub:
    def __init__(self, engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession], settings: Settings):
        self._engine = engine
        self._sessions = session_factory
        self._settings = settings
        self._clients: set[RealtimeClient] = set()
        self._signals: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._start_lock = asyncio.Lock()
        self._stopping = asyncio.Event()
        self._closing: set[asyncio.Task[None]] = set()
        self.listening = asyncio.Event()

    # --- Lifecycle ------------------------------------------------------------------------------------------------

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._tasks:
                return
            self._stopping.clear()
            self._tasks = [
                asyncio.create_task(self._listen_forever(), name="realtime-listen"),
                asyncio.create_task(self._dispatch_forever(), name="realtime-dispatch"),
            ]

    async def stop(self) -> None:
        self._stopping.set()
        for client in list(self._clients):
            await self._close(client, CLOSE_GOING_AWAY, None)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        self.listening.clear()

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    # --- Authentication -------------------------------------------------------------------------------------------

    async def authenticate(self, token: str) -> User:
        async with self._sessions() as session:
            return await authenticate_tenant_token(session, self._settings, token)

    async def revalidate(self, client: RealtimeClient) -> bool:
        """Checks a connection again (rules of every REST route, plus the token's expiry); closes it if it fails."""
        error: DomainError | None = None
        if datetime.now(UTC) >= client.expires_at:
            error = AuthenticationFailed("Your session expired. Please sign in again.", code="SESSION_EXPIRED")
        else:
            try:
                await self.authenticate(client.token)
            except DomainError as failure:
                error = failure
        if error is None:
            return True
        await self._close(client, close_code_for(error), session_ended_message(error))
        return False

    # --- Connections ----------------------------------------------------------------------------------------------

    def register(self, client: RealtimeClient) -> None:
        self._clients.add(client)

    def unregister(self, client: RealtimeClient) -> None:
        self._clients.discard(client)

    def send(self, client: RealtimeClient, message: dict[str, Any]) -> None:
        if client.closed:
            return
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            # A client that does not read is closed rather than slowing everyone down; it reconnects and resyncs.
            task = asyncio.get_running_loop().create_task(self._close(client, CLOSE_OVERLOADED, None))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

    async def writer(self, client: RealtimeClient) -> None:
        """Sends a client's queued messages in order (one task per connection)."""
        while not client.closed:
            message = await client.queue.get()
            if message is None or client.closed:
                return
            await client.websocket.send_json(message)

    async def _close(self, client: RealtimeClient, code: int, message: dict[str, Any] | None) -> None:
        if client.closed:
            return
        client.closed = True
        self.unregister(client)
        with contextlib.suppress(asyncio.QueueFull):
            client.queue.put_nowait(None)  # wakes the writer, which then stops
        with contextlib.suppress(Exception):
            if message is not None:
                await client.websocket.send_json(message)
            await client.websocket.close(code=code)

    # --- Listening ------------------------------------------------------------------------------------------------

    def _on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        self._signals.put_nowait(payload)

    async def _listen_forever(self) -> None:
        delay = 1.0
        while not self._stopping.is_set():
            try:
                async with self._engine.connect() as conn:
                    raw = await conn.get_raw_connection()
                    driver: Any = raw.driver_connection
                    await driver.add_listener(CHANNEL, self._on_notify)
                    self.listening.set()
                    delay = 1.0
                    try:
                        while not self._stopping.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(self._stopping.wait(), timeout=20)
                            if not self._stopping.is_set():
                                await driver.execute("SELECT 1")  # raises when the connection was lost
                    finally:
                        self.listening.clear()
                        with contextlib.suppress(Exception):
                            await driver.remove_listener(CHANNEL, self._on_notify)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the database restarted or the network dropped: retry
                logger.warning("Real-time listener lost its database connection: %s", type(error).__name__)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                delay = min(delay * 2, 30.0)

    async def _dispatch_forever(self) -> None:
        while True:
            payload = await self._signals.get()
            try:
                await self.dispatch(json.loads(payload))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Real-time signal could not be delivered")

    # --- Delivery -------------------------------------------------------------------------------------------------

    async def _can_see(self, client: RealtimeClient, alert: UUID | None, incident: UUID | None) -> bool:
        if client.is_staff:
            return True
        key = ("alert", str(alert)) if alert is not None else ("incident", str(incident))
        cached = client.visible.get(key)
        now = time.monotonic()
        if cached is not None and cached[1] > now:
            return cached[0]
        async with self._sessions() as session:
            await bind_actor(session, client.user.id)
            allowed = bool(
                await session.scalar(
                    text("SELECT app_comm_target_visible(CAST(:a AS uuid), CAST(:i AS uuid))"),
                    {"a": alert, "i": incident},
                )
            )
        client.visible[key] = (allowed, now + (_POSITIVE_TTL if allowed else _NEGATIVE_TTL))
        return allowed

    async def dispatch(self, signal: dict[str, Any]) -> None:
        event = str(signal.get("e", ""))
        user = _uuid(signal.get("user"))
        if event == "session.revoked":
            for client in [c for c in self._clients if c.user.id == user]:
                await self.revalidate(client)
            return
        if event == "organization.status":
            org = _uuid(signal.get("org"))
            for client in [c for c in self._clients if c.user.organization_id == org]:
                await self.revalidate(client)
            return
        if event in USER_EVENTS and user is not None:
            for client in [c for c in self._clients if c.user.id == user]:
                if event in ("alert.assigned", "incident.invited", "incident.removed"):
                    client.visible.clear()  # what this Guard may see just changed
                self.send(client, client_event(signal))
        # Everyone else who may see the target hears that it changed.
        scoped = "alert.updated" if event == "alert.assigned" else event
        if scoped not in SCOPED_EVENTS:
            return
        org, campus = _uuid(signal.get("org")), _uuid(signal.get("campus"))
        alert, incident = _uuid(signal.get("alert")), _uuid(signal.get("incident"))
        if alert is None and incident is None:
            return
        for client in list(self._clients):
            if client.user.organization_id != org or client.user.campus_id != campus:
                continue
            if event == "alert.assigned" and client.user.id == user:
                continue  # already told
            if await self._can_see(client, alert, incident):
                self.send(client, client_event(signal, scoped))
