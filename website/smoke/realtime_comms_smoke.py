"""Live smoke check: real-time messaging and voice calls across the Guard app, the web app and the Core API (D-026).

Guards (as the mobile app does), an Admin and a Supervisor (as the web app does) and an Admin of another campus of
the same organization all open the real-time WebSocket **through the Vite dev server's /api proxy** (proving
`ws: true`) and use the real REST routes at the same time. It checks, in both directions:

- alert chat, Guard actions (acknowledge, Additional Guards, I'm Responding) and new alerts reach the right sockets;
- case chat: an invited Guard is told, case notes and status changes reach the staff, messages go both ways;
- voice calls: a Guard-started alert call rings the staff with the target's title and location; a Supervisor joins and
  an Admin declines; staff end it for everyone and the Guard hears ENDED; a Supervisor-started case call rings the
  invited Guard, a second "start" joins the same call, a Guard cannot end it, an Admin can; LiveKit room tokens are
  audio-only and for the right room;
- boundaries: the other campus receives no signal and gets 404 for every alert, case and call; a Super Admin token is
  refused over REST (403) and on the socket (4403); a bad token is refused (4401); a Guard's assignment change ends
  only that Guard's socket.

Media (LiveKit audio) is NOT exercised: the API is run with VOICE_CALL_PROVIDER=livekit and placeholder keys, so tokens
are signed and the call lifecycle is real, but nobody connects to a LiveKit server.

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox (see
smoke/README.md). Each run creates a new organization; nothing existing is changed or removed.

    python3 smoke/realtime_comms_smoke.py --web http://127.0.0.1:5181 --outbox /path/to/outbox --token <ingest token> \
        [--livekit-secret <the API's LIVEKIT_API_SECRET>] [--super-admin <username> --super-admin-password <password>]

Standard library only. Exit code 0 when every check passes.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from operations_smoke import Org, detect
from personnel_smoke import PASSWORD, Api, check, code_for, results

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Closed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"closed with {code}")
        self.code = code


class Socket:
    """A minimal RFC 6455 client (text frames, client masking, ping/pong, close codes)."""

    def __init__(self, web: str, path: str = "/api/v1/realtime/ws") -> None:
        url = urlparse(web)
        host, port = url.hostname or "127.0.0.1", url.port or 80
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {web.rstrip('/')}\r\n\r\n"
            ).encode()
        )
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("no upgrade response")
            data += chunk
        head, self.buffer = data.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        self.status_line = lines[0]
        headers = {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if " 101 " not in self.status_line or headers.get("sec-websocket-accept") != expected:
            raise ConnectionError(f"upgrade refused: {self.status_line}")
        self.last_ping = time.monotonic()
        self.ready: dict[str, Any] = {}

    # --- frames ---
    def _read(self, n: int, deadline: float) -> bytes:
        while len(self.buffer) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(65536)  # socket.timeout is TimeoutError
            if not chunk:
                raise Closed(1006)
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, message: dict[str, Any]) -> None:
        self._frame(0x1, json.dumps(message).encode())

    def _next(self, deadline: float) -> dict[str, Any]:
        """The next JSON text message, pongs included (answers pings; raises Closed on a close frame)."""
        while True:
            first, second = self._read(2, deadline)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2, deadline))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8, deadline))[0]
            payload = self._read(length, deadline)
            if opcode == 0x8:
                raise Closed(struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005)
            if opcode == 0x9:
                self._frame(0xA, payload)
            elif opcode == 0x1:
                return json.loads(payload)

    def receive(self, timeout: float = 10) -> dict[str, Any]:
        """The next message other than a pong. Sends the heartbeat ping itself during long checks."""
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() - self.last_ping > 15:
                self.send({"type": "ping"})
                self.last_ping = time.monotonic()
            message = self._next(deadline)
            if message.get("type") != "pong":
                return message

    def event(self, name: str, timeout: float = 10, **match: str) -> dict[str, Any]:
        """The next event called ``name`` whose fields match (other signals are skipped)."""
        deadline = time.monotonic() + timeout
        while True:
            message = self.receive(max(0.05, deadline - time.monotonic()))
            if (
                message.get("type") == "event"
                and message.get("event") == name
                and all(message.get(k) == v for k, v in match.items())
            ):
                return message

    def events(self, seconds: float = 1.0) -> list[dict[str, Any]]:
        """Every message that arrives within ``seconds`` (used to prove that nothing arrives)."""
        got: list[dict[str, Any]] = []
        deadline = time.monotonic() + seconds
        try:
            while True:
                got.append(self.receive(max(0.05, deadline - time.monotonic())))
        except TimeoutError:
            return got

    def alive(self) -> bool:
        """Answers a ping (any queued events are skipped)."""
        self.send({"type": "ping"})
        deadline = time.monotonic() + 5
        try:
            while self._next(deadline).get("type") != "pong":
                pass
        except (Closed, TimeoutError):
            return False
        return True

    def close(self) -> None:
        try:
            self._frame(0x8, struct.pack(">H", 1000))
        except OSError:
            pass
        self.sock.close()


def connect(web: str, token: str) -> Socket:
    ws = Socket(web)
    ws.send({"type": "auth", "access_token": token})
    ws.ready = ws.receive()
    if ws.ready.get("type") != "ready":
        raise RuntimeError(f"socket not ready: {ws.ready}")
    return ws


def refused(web: str, token: str) -> tuple[str | None, int | None]:
    """(session_ended code, close code) for a token the socket must refuse."""
    ws = Socket(web)
    ws.send({"type": "auth", "access_token": token})
    code: str | None = None
    try:
        first = ws.receive()
        code = first.get("code")
        ws.receive()
    except Closed as closed:
        ws.close()
        return code, closed.code
    except TimeoutError:
        pass
    ws.close()
    return code, None


def token_claims(token: str, secret: str | None) -> tuple[dict[str, Any], bool | None]:
    head, body, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if secret is None:
        return claims, None
    expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return claims, hmac.compare_digest(expected.rstrip(b"=").decode(), signature)


def second_campus(api: Api, org: Org, outbox: Path) -> str:
    """A second campus of the same organization (decision D-021), approved by the Main Admin; its Admin's token."""
    email = f"north.{org.run}@smoke.example.com"
    t = time.time() - 1
    s, r = api.call("POST", "/auth/campus-requests", {
        "organization_name": org.name, "organization_id": org.org_id, "campus_name": "North Campus",
        "admin_full_name": "North Admin", "username": f"north{org.run}", "email": email,
        "phone_number": "+923001112233", "password": PASSWORD})
    assert s == 201, (s, r)
    s, _ = api.call("POST", f"/auth/campus-requests/{r['application_id']}/verify", {"otp": code_for(outbox, email, t)})
    _, queue = api.call("GET", "/campus-requests", token=org.admin)
    request = next(q for q in queue if q["applicant_email"] == email)
    # Approval needs the Main Admin's password and an email code first (decision D-024).
    authorization = org._authorize("/campus-requests", org.admin, org.admin_email)
    s, approved = api.call("POST", f"/campus-requests/{request['id']}/approve", {"authorization_id": authorization}, org.admin)
    assert s == 200, (s, approved)
    return org.sign_in(email)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--token", required=True, help="the API's DETECTION_INGEST_TOKEN (scratch value)")
    parser.add_argument("--livekit-secret", help="the API's LIVEKIT_API_SECRET (scratch value), to verify signatures")
    parser.add_argument("--super-admin", help="a seeded Super Admin username on the scratch database")
    parser.add_argument("--super-admin-password")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api = Api(args.web)
    web = args.web

    # --- People: Main Campus Admin, Supervisor, two Guards; North Campus Admin ---------------------------------------
    org = Org(api, args.outbox, "Realtime Smoke")
    sup_email = f"sup.{org.run}@smoke.example.com"
    org.create("/organization-admin/supervisors", org.admin, org.admin_email, "Sana Supervisor", sup_email)
    sup = org.sign_in(sup_email)
    g1_email, g2_email = f"g1.{org.run}@smoke.example.com", f"g2.{org.run}@smoke.example.com"
    g1 = org.create("/guards", org.admin, org.admin_email, "Bilal Guard", g1_email)
    g2 = org.create("/guards", org.admin, org.admin_email, "Hamza Guard", g2_email)
    _, block = api.call("POST", "/locations/buildings", {"name": "Admin Block", "kind": "INDOOR"}, org.admin)
    _, ground = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Ground", "level": 0}, org.admin)
    _, gate = api.call("POST", f"/locations/buildings/{block['id']}/areas", {"name": "Main Gate", "floor_id": ground["id"]}, org.admin)
    where = {"building_id": block["id"], "floor_id": ground["id"], "area_id": gate["id"]}
    api.call("PUT", f"/locations/guard-assignments/{g1['guard_id']}", where, sup)
    guard1 = org.guard_sign_in(g1["guard_id"], g1_email, "STATIONED")
    guard2 = org.guard_sign_in(g2["guard_id"], g2_email, "STANDBY")
    _, cam = api.call("POST", "/cameras", {"display_name": "Gate Camera", "connection_type": "RTSP", "location": where}, sup)
    north = second_campus(api, org, args.outbox)
    check(bool(sup and guard1 and guard2 and north), "Admin, Supervisor, two Guards and a second campus's Admin sign in")
    s, offered = api.call("GET", "/realtime/status", token=sup)
    check(s == 200 and offered["websocket_path"] == "/api/v1/realtime/ws" and offered["voice_call_provider"] == "livekit",
          "GET /realtime/status (Supervisor) names the WebSocket path and the voice provider")

    # --- Sockets through the Vite proxy -------------------------------------------------------------------------------
    sockets = {name: connect(web, token) for name, token in
               (("admin", org.admin), ("sup", sup), ("guard1", guard1), ("guard2", guard2), ("north", north))}
    check(all(ws.ready.get("type") == "ready" for ws in sockets.values()),
          "five WebSockets open through the Vite dev proxy (ws: true): auth message first, then ready")
    check(sockets["admin"].ready["role"] == "ADMIN" and sockets["sup"].ready["role"] == "SUPERVISOR"
          and sockets["guard1"].ready["role"] == "GUARD", "ready names each role (Admin, Supervisor, Guard)")
    check("access_token" not in sockets["admin"].status_line and "token" not in sockets["admin"].status_line,
          "the token is never in the socket URL")
    admin_ws, sup_ws, g1_ws, g2_ws, north_ws = (sockets[k] for k in ("admin", "sup", "guard1", "guard2", "north"))
    g2_user = g2_ws.ready["user_id"]

    # --- Alerts: new alert, Guard actions, chat both ways -------------------------------------------------------------
    alert = detect(web, args.token, cam["id"], "WEAPON")["alert_id"]
    check(admin_ws.event("alert.updated", alert_id=alert)["alert_id"] == alert
          and sup_ws.event("alert.updated", alert_id=alert)["alert_id"] == alert, "a new alert reaches the web staff at once")
    check(g1_ws.event("alert.assigned", alert_id=alert)["alert_id"] == alert, "… and the routed Guard's app (alert.assigned)")
    api.call("POST", f"/alerts/{alert}/acknowledge", {"client_action_id": str(uuid.uuid4())}, guard1)
    check(sup_ws.event("alert.updated", alert_id=alert)["alert_id"] == alert, "Guard acknowledges → Supervisor's page is signalled")
    api.call("POST", f"/alerts/{alert}/assessment", {"assessment": "ADDITIONAL_GUARDS_REQUIRED"}, guard1)
    check(admin_ws.event("alert.updated", alert_id=alert)["alert_id"] == alert, "Guard asks for Additional Guards → Admin signalled")
    api.call("POST", f"/alerts/{alert}/responding", {"client_action_id": str(uuid.uuid4())}, guard1)
    sup_ws.event("alert.updated", alert_id=alert)
    _, detail = api.call("GET", f"/alerts/{alert}", token=sup)
    check(detail["recipients"][0]["state"] == "RESPONDING" and detail.get("responding_count") == 1,
          "the re-read alert shows I'm Responding and the responder count")
    api.call("POST", f"/alerts/{alert}/messages", {"body": "At the gate.", "client_message_id": str(uuid.uuid4())}, guard1)
    signal = admin_ws.event("alert.message", alert_id=alert)
    check(signal["alert_id"] == alert and sup_ws.event("alert.message", alert_id=alert)["alert_id"] == alert,
          "Guard → web: alert chat signal to Admin and Supervisor")
    check("At the gate." not in json.dumps(signal) and "body" not in signal, "signals carry IDs only, never message text")
    _, thread = api.call("GET", f"/alerts/{alert}/messages", token=org.admin)
    check(thread[-1]["body"] == "At the gate." and thread[-1]["sender_role"] == "GUARD", "the web re-reads the Guard's message")
    api.call("POST", f"/alerts/{alert}/messages", {"body": "Hold the door.", "client_message_id": str(uuid.uuid4())}, sup)
    check(g1_ws.event("alert.message", alert_id=alert)["alert_id"] == alert, "web → Guard: the Supervisor's reply reaches the app")

    # --- Cases: invitation, notes, status, chat both ways ---------------------------------------------------------------
    s, case = api.call("POST", "/incidents", {"title": "Weapon at the gate", "priority": "HIGH"}, sup)
    case_id = case["id"]
    api.call("PUT", f"/incidents/{case_id}/participants/{g2_user}", token=sup)
    check(g2_ws.event("incident.invited", incident_id=case_id)["incident_id"] == case_id, "the invited Guard's app is told (incident.invited)")
    api.call("POST", f"/incidents/{case_id}/notes", {"note": "Footage requested."}, sup)
    note = admin_ws.event("incident.updated", incident_id=case_id, kind="NOTE_ADDED")
    check(note["incident_id"] == case_id, "a case note reaches the other staff (incident.updated, kind NOTE_ADDED)")
    api.call("POST", f"/incidents/{case_id}/messages", {"body": "On my way."}, guard2)
    check(admin_ws.event("incident.message", incident_id=case_id)["incident_id"] == case_id
          and sup_ws.event("incident.message", incident_id=case_id)["incident_id"] == case_id, "Guard → web: case chat signal")
    api.call("POST", f"/incidents/{case_id}/messages", {"body": "Thanks."}, org.admin)
    check(g2_ws.event("incident.message", incident_id=case_id)["incident_id"] == case_id, "web → Guard: case chat signal")
    _, case_thread = api.call("GET", f"/incidents/{case_id}/messages", token=guard2)
    check([m["body"] for m in case_thread][-2:] == ["On my way.", "Thanks."], "both clients read the same case thread")
    api.call("POST", f"/incidents/{case_id}/status", {"status": "UNDER_INVESTIGATION"}, org.admin)
    status = sup_ws.event("incident.updated", incident_id=case_id, kind="STATUS_CHANGED")
    check(status.get("state") == "UNDER_INVESTIGATION", "a status change reaches the staff with the new state")

    # --- Voice: a Guard-started alert call ---------------------------------------------------------------------------
    s, active = api.call("GET", f"/alerts/{alert}/calls/active", token=sup)
    check(s == 200 and active["voice_calls_available"] and active["provider"] == "livekit" and active["call"] is None,
          "voice calls are offered (VOICE_CALL_PROVIDER=livekit), no call yet")
    s, started = api.call("POST", f"/alerts/{alert}/calls", token=guard1)
    check(s == 201 and started["outcome"] == "STARTED", "the Guard starts an alert call (201 STARTED)")
    call_id = started["call"]["id"]
    claims, signed = token_claims(started["connection"]["token"], args.livekit_secret)
    grant = claims.get("video", {})
    check(grant.get("room") == f"cctv-call-{call_id}" and grant.get("roomJoin") is True
          and grant.get("canPublishSources") == ["microphone"] and signed in (True, None),
          "the LiveKit token joins only this call's room, audio only" + (" (signature verified)" if signed else ""))
    check(admin_ws.event("call.invited", call_id=call_id)["alert_id"] == alert
          and sup_ws.event("call.invited", call_id=call_id)["alert_id"] == alert, "the call rings both web staff at once (call.invited)")
    _, ringing = api.call("GET", "/calls", token=org.admin)
    mine = next(c for c in ringing if c["id"] == call_id)
    check(mine["my_state"] == "INVITED" and mine["started_by_role"] == "GUARD" and mine["started_by_name"] == "Bilal Guard"
          and mine["target_title"] == "Weapon alert · Gate Camera" and mine["target_location"] == "Admin Block → Ground → Main Gate",
          "the web's incoming-call banner data: caller, role, alert title and location")
    s, joined = api.call("POST", f"/calls/{call_id}/join", token=sup)
    check(s == 200 and joined["outcome"] == "JOINED" and joined["call"]["can_end"], "the Supervisor joins from the web (can end it)")
    g1_ws.event("call.updated", call_id=call_id)
    s, declined = api.call("POST", f"/calls/{call_id}/decline", token=org.admin)
    check(s == 200 and declined["my_state"] == "DECLINED", "the Admin declines")
    s, active = api.call("GET", f"/alerts/{alert}/calls/active", token=org.admin)
    check(active["call"]["id"] == call_id and active["call"]["joined_count"] == 2, "the alert page shows the active call (2 in it)")
    s, ended = api.call("POST", f"/calls/{call_id}/end", token=sup)
    check(s == 200 and ended["status"] == "ENDED" and ended["end_reason"] == "ENDED_BY_USER", "the Supervisor ends it for everyone")
    check(g1_ws.event("call.updated", call_id=call_id, state="ENDED")["call_id"] == call_id, "the Guard's app hears ENDED at once")
    check(api.call("GET", f"/alerts/{alert}/calls/active", token=guard1)[1]["call"] is None
          and api.call("GET", "/calls", token=guard1)[1] == [], "the active call clears for everyone")

    # --- Voice: a Supervisor-started case call ---------------------------------------------------------------------
    s, case_call = api.call("POST", f"/incidents/{case_id}/calls", token=sup)
    case_call_id = case_call["call"]["id"]
    check(s == 201 and case_call["call"]["target_title"].endswith("· Weapon at the gate"), "the Supervisor starts a case call")
    check(g2_ws.event("call.invited", call_id=case_call_id)["incident_id"] == case_id, "it rings the invited Guard's app")
    s, _ = api.call("POST", f"/calls/{case_call_id}/join", token=guard2)
    check(s == 200, "the Guard joins from the app")
    s, again = api.call("POST", f"/incidents/{case_id}/calls", token=org.admin)
    check(s == 200 and again["outcome"] == "JOINED" and again["call"]["id"] == case_call_id,
          "the Admin's Start voice call joins the same call (200 JOINED)")
    s, denied = api.call("POST", f"/calls/{case_call_id}/end", token=guard2)
    check(s == 403 and denied.get("code") == "CALL_END_NOT_ALLOWED", "a Guard who did not start it cannot end it (403)")
    s, _ = api.call("POST", f"/calls/{case_call_id}/end", token=org.admin)
    check(s == 200 and g2_ws.event("call.updated", call_id=case_call_id, state="ENDED")["call_id"] == case_call_id,
          "the Admin ends it for everyone; the Guard's app hears ENDED")

    # --- Boundaries ---------------------------------------------------------------------------------------------------
    leaked = [m for m in north_ws.events(1.5) if m.get("type") == "event"]
    check(leaked == [], f"the other campus's socket received no signal at all ({len(leaked)} received)")
    check(all(api.call(m, p, token=north)[0] == 404 for m, p in (
        ("GET", f"/alerts/{alert}"), ("GET", f"/alerts/{alert}/messages"), ("GET", f"/alerts/{alert}/calls/active"),
        ("POST", f"/alerts/{alert}/calls"), ("GET", f"/incidents/{case_id}"), ("GET", f"/incidents/{case_id}/messages"),
        ("POST", f"/calls/{call_id}/join"), ("GET", f"/calls/{case_call_id}"))),
        "the other campus gets 404 for the alert, its chat and calls, the case and the calls")
    check(api.call("GET", "/calls", token=north)[1] == [], "… and has no ringing calls")
    if args.super_admin and args.super_admin_password:
        s, platform = api.call("POST", "/platform/auth/login", {"username": args.super_admin, "password": args.super_admin_password})
        sa = platform["access_token"] if s == 200 else ""
        rest = [api.call(m, p, token=sa) for m, p in (("GET", "/calls"), ("POST", f"/alerts/{alert}/calls"), ("GET", "/realtime/status"))]
        check(s == 200 and all(r[0] == 403 and r[1].get("code") == "SUPER_ADMIN_NOT_ALLOWED" for r in rest),
              "a Super Admin token is refused by the call and real-time routes (403 SUPER_ADMIN_NOT_ALLOWED)")
        code, close = refused(web, sa)
        check(code == "SUPER_ADMIN_NOT_ALLOWED" and close == 4403, f"… and by the socket ({code}, close {close})")
    else:
        print("SKIP  Super Admin checks (no --super-admin given)")
    code, close = refused(web, "not-a-token")
    check(close == 4401, f"a bad token is refused on the socket ({code}, close {close})")
    api.call("DELETE", f"/locations/guard-assignments/{g1['guard_id']}", token=sup)
    try:
        ended_msg: dict[str, Any] = g1_ws.receive()
        while ended_msg.get("type") != "session_ended":
            ended_msg = g1_ws.receive()
        g1_ws.receive()
        close_code = None
    except Closed as closed:
        close_code = closed.code
    check(ended_msg.get("code") == "ASSIGNMENT_CHANGED" and close_code == 4401,
          "a Guard's assignment change ends that Guard's socket (session_ended ASSIGNMENT_CHANGED, 4401)")
    check(admin_ws.alive() and sup_ws.alive() and g2_ws.alive(), "… and only that one: Admin, Supervisor and other Guard still answer pings")
    for ws in sockets.values():
        ws.close()

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(json.dumps({"organization_name": org.name, "organization_id": org.org_id, "admin_email": org.admin_email,
                      "supervisor_email": sup_email, "password": PASSWORD, "alert": alert, "case": case_id}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
