"""Live smoke check of the Alerts, Manual Reports and Notifications web contract (Phase 6).

It calls the Core API **through the Vite dev server's /api proxy**, with the URLs, methods and bodies
the web pages send. Sign-in is real (codes from the development email outbox). Alerts are created
through the real detection intake, so the API must run with DETECTION_INGEST_TOKEN set; Guard
actions and the manual report are sent the way the (future) Guard app sends them.

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox
(see smoke/README.md). Each run creates two new organizations; nothing existing is changed or removed.

    python3 smoke/operations_smoke.py --web http://127.0.0.1:5180 --outbox /path/to/outbox --token <ingest token>

Standard library only. Exit code 0 when every check passes. The data is left in place, so the pages
can be looked at afterwards; the sign-in details are printed at the end.
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from personnel_smoke import PASSWORD, Api, check, code_for, results


def test_png(width: int = 320, height: int = 200) -> str:
    """A small generated gradient PNG (base64): a valid image for the optional report photo."""
    import struct
    import zlib

    rows = b"".join(
        b"\x00" + b"".join(bytes((40 + x * 160 // width, 70 + y * 120 // height, 110)) for x in range(width))
        for y in range(height)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


PNG = test_png()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Org:
    """A new organization with its Admin, through real registration and sign-in."""

    def __init__(self, api: Api, outbox: Path, label: str) -> None:
        self.api, self.outbox = api, outbox
        run = uuid.uuid4().hex[:8]
        self.run, self.name = run, f"{label} {run}"
        self.admin_email = f"admin.{run}@smoke.example.com"
        t = time.time() - 1
        _, reg = api.call("POST", "/auth/organization-registrations", {
            "organization_name": self.name, "campus_name": "Main Campus", "admin_full_name": f"{label} Admin", "username": f"admin{run}",
            "email": self.admin_email, "phone_number": "+923001234567", "password": PASSWORD})
        _, done = api.call("POST", f"/auth/organization-registrations/{reg['registration_id']}/verify",
                           {"otp": code_for(outbox, self.admin_email, t)})
        self.org_id = done["organization_id"]
        self.admin = self.sign_in(self.admin_email)

    def sign_in(self, email: str) -> str:
        t = time.time() - 1
        s, sent = self.api.call("POST", "/auth/organization-admin/login", {
            "organization_name": self.name, "organization_id": self.org_id, "email": email, "password": PASSWORD})
        assert s == 200, (s, sent)
        s, token = self.api.call("POST", f"/auth/organization-admin/login/{sent['login_id']}/verify",
                                 {"otp": code_for(self.outbox, email, t)})
        assert s == 200, (s, token)
        return token["access_token"]

    def _authorize(self, prefix: str, token: str, email: str) -> str:
        t = time.time() - 1
        _, auth = self.api.call("POST", f"{prefix}/authorization", {"current_password": PASSWORD}, token)
        _, ok = self.api.call("POST", f"{prefix}/authorization/{auth['id']}/verify",
                              {"otp": code_for(self.outbox, email, t)}, token)
        return ok["authorization_id"]

    def create(self, prefix: str, token: str, actor_email: str, name: str, email: str) -> dict:
        authorization = self._authorize(prefix, token, actor_email)
        t = time.time() - 1
        s, creation = self.api.call("POST", prefix, {
            "authorization_id": authorization, "full_name": name, "username": email.split("@")[0].replace(".", ""),
            "email": email, "phone_number": "+923001234599", "password": PASSWORD}, token)
        assert s in (200, 201, 202), (s, creation)
        s, created = self.api.call("POST", f"{prefix}/creations/{creation['id']}/verify",
                                   {"otp": code_for(self.outbox, email, t)}, token)
        assert s == 201, (s, created)
        return created

    def guard_sign_in(self, guard_id: str, email: str, mode: str = "STANDBY") -> str:
        """``mode`` (STANDBY or STATIONED) must match the Guard's assignment (decision D-018)."""
        t = time.time() - 1
        s, sent = self.api.call("POST", "/auth/guard/login", {"operational_mode": mode, "guard_id": guard_id,
                                                              "email": email, "password": PASSWORD})
        assert s == 200, (s, sent)
        s, token = self.api.call("POST", f"/auth/guard/login/{sent['login_id']}/verify",
                                 {"otp": code_for(self.outbox, email, t), "operational_mode": mode})
        assert s == 200, (s, token)
        return token["access_token"]


def detect(web: str, token: str, camera: str, kind: str) -> dict:
    body = json.dumps({"camera_id": camera, "detection_type": kind, "confidence": 0.87, "detected_at": now(),
                       "detection_id": uuid.uuid4().hex}).encode()
    request = urllib.request.Request(web.rstrip("/") + "/api/v1/internal/detections", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Detection-Token": token})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--token", required=True, help="the API's DETECTION_INGEST_TOKEN (scratch value)")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api = Api(args.web)

    # --- Organization A: Admin, Supervisor, two Guards, one location, one camera -----------------------
    a = Org(api, args.outbox, "Ops Smoke")
    sup_email = f"sup.{a.run}@smoke.example.com"
    a.create("/organization-admin/supervisors", a.admin, a.admin_email, "Sana Supervisor", sup_email)
    sup = a.sign_in(sup_email)
    check(api.call("GET", "/auth/me", token=sup)[1]["role"] == "SUPERVISOR", "Supervisor signs in")
    g1_email, g2_email = f"g1.{a.run}@smoke.example.com", f"g2.{a.run}@smoke.example.com"
    # Only Admins create Guards (decision D-017).
    g1 = a.create("/guards", a.admin, a.admin_email, "Bilal Guard", g1_email)
    g2 = a.create("/guards", a.admin, a.admin_email, "Hamza Guard", g2_email)
    guard1 = a.guard_sign_in(g1["guard_id"], g1_email)
    guard2 = a.guard_sign_in(g2["guard_id"], g2_email)
    check(bool(guard1 and guard2), "Admin creates two Guards; both sign in")

    _, block = api.call("POST", "/locations/buildings", {"name": "Admin Block", "kind": "INDOOR"}, a.admin)
    _, ground = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Ground", "level": 0}, a.admin)
    _, gate = api.call("POST", f"/locations/buildings/{block['id']}/areas", {"name": "Main Gate", "floor_id": ground["id"]}, a.admin)
    where = {"building_id": block["id"], "floor_id": ground["id"], "area_id": gate["id"]}
    s, _ = api.call("PUT", f"/locations/guard-assignments/{g1['guard_id']}", where, sup)
    check(s == 200, "Supervisor assigns Guard 1 to the Main Gate (Guard 2 has no location)")
    s, ended = api.call("GET", "/guard/dashboard", token=guard1)
    check(s == 401 and ended.get("code") == "ASSIGNMENT_CHANGED", "Guard 1's Standby session ended when the post was set")
    guard1 = a.guard_sign_in(g1["guard_id"], g1_email, "STATIONED")
    check(api.call("GET", "/guard/dashboard", token=guard1)[1]["dashboard"] == "STATIONED_MONITORING",
          "Guard 1 signs in again as Stationed (Stationed Monitoring Dashboard)")
    _, cam = api.call("POST", "/cameras", {"display_name": "Gate Camera", "connection_type": "RTSP", "location": where}, sup)
    _, cam2 = api.call("POST", "/cameras", {"display_name": "Lobby Camera", "connection_type": "ONVIF",
                                            "location": {"building_id": block["id"]}}, sup)

    # --- Detections become alerts ----------------------------------------------------------------------
    weapon = detect(args.web, args.token, cam["id"], "WEAPON")["alert_id"]
    fight = detect(args.web, args.token, cam["id"], "FIGHT_VIOLENCE")["alert_id"]
    restricted = detect(args.web, args.token, cam2["id"], "RESTRICTED_AREA")["alert_id"]
    s, listing = api.call("GET", "/alerts?limit=26&offset=0", token=sup)
    check(s == 200 and {x["id"] for x in listing} == {weapon, fight, restricted}, "Supervisor lists the campus's alerts (page URL)")
    check(listing[0]["priority"] == "HIGH" and listing[-1]["alert_type"] == "RESTRICTED_AREA", "high priority first")
    s, filtered = api.call("GET", f"/alerts?status=NEW&priority=HIGH&building_id={block['id']}&floor_id={ground['id']}&limit=26&offset=0", token=sup)
    check(s == 200 and {x["id"] for x in filtered} == {weapon, fight}, "status, priority and location filters (page URL)")
    s, detail = api.call("GET", f"/alerts/{weapon}", token=a.admin)
    check(s == 200 and [r["guard_id"] for r in detail["recipients"]] == [g1["guard_id"]]
          and detail["location"]["area_name"] == "Main Gate", "details: location and the routed Guard")
    s, history = api.call("GET", f"/alerts/{weapon}/history", token=sup)
    check(s == 200 and [e["event_type"] for e in history][:2] == ["CREATED", "ROUTED"], "response history")

    # --- Guard actions (as the Guard app sends them) and the Supervisor's view ---------------------------
    api.call("POST", f"/alerts/{weapon}/acknowledge", {"client_action_id": str(uuid.uuid4())}, guard1)
    api.call("POST", f"/alerts/{weapon}/assessment", {"assessment": "ADDITIONAL_GUARDS_REQUIRED"}, guard1)
    _, detail = api.call("GET", f"/alerts/{weapon}", token=sup)
    check(detail["status"] == "ACKNOWLEDGED" and detail["recipients"][0]["assessment"] == "ADDITIONAL_GUARDS_REQUIRED",
          "Supervisor sees the Guard's acknowledgement and assessment")
    s, mine = api.call("GET", "/notifications?unread_only=false&limit=100", token=sup)
    kinds = {n["kind"] for n in mine}
    check(s == 200 and {"ALERT_NEW", "ALERT_ASSISTANCE_REQUESTED"} <= kinds, "Supervisor is notified of new alerts and the help request")

    # --- Staff actions (page bodies) ------------------------------------------------------------------------
    s, msgs = api.call("POST", f"/alerts/{weapon}/messages", {"body": "Hamza, go to the Main Gate.", "client_message_id": str(uuid.uuid4())}, sup)
    check(s == 201 and msgs[-1]["sender_role"] == "SUPERVISOR", "Supervisor posts in the alert chat")
    api.call("POST", f"/alerts/{weapon}/messages", {"body": "On my way.", "client_message_id": str(uuid.uuid4())}, guard1)
    s, bad = api.call("POST", f"/alerts/{weapon}/escalate", {"scope": "GUARDS", "location": None, "guard_ids": ["NOSUCHGUARD"],
                                                             "note": None, "client_action_id": str(uuid.uuid4())}, sup)
    check(s == 422 and bad.get("code") == "NO_ELIGIBLE_GUARDS", "escalating to an unknown Guard is refused with a clear code")
    s, esc = api.call("POST", f"/alerts/{weapon}/escalate", {"scope": "CAMPUS", "location": None, "guard_ids": [],
                                                             "note": "Backup needed", "client_action_id": str(uuid.uuid4())}, sup)
    check(s == 200 and esc["added_guards"] == 1 and esc["alert"]["status"] == "ESCALATED", "Supervisor escalates to the campus (Guard 2 added)")
    api.call("POST", f"/alerts/{weapon}/responding", {"client_action_id": str(uuid.uuid4())}, guard2)
    s, _ = api.call("POST", f"/alerts/{fight}/dismiss", {"reason": "", "client_action_id": str(uuid.uuid4())}, sup)
    check(s == 422, "dismissing without a reason is refused")
    s, dismissed = api.call("POST", f"/alerts/{fight}/dismiss", {"reason": "False alarm: staff training", "client_action_id": str(uuid.uuid4())}, sup)
    check(s == 200 and dismissed["status"] == "DISMISSED" and dismissed["close_note"], "Supervisor dismisses with a reason")
    s, resolved = api.call("POST", f"/alerts/{restricted}/resolve", {"note": None, "client_action_id": str(uuid.uuid4())}, a.admin)
    check(s == 200 and resolved["status"] == "RESOLVED", "Admin resolves an alert")
    s, _ = api.call("POST", f"/alerts/{fight}/resolve", {"note": None, "client_action_id": str(uuid.uuid4())}, sup)
    check(s == 409, "a closed alert cannot be changed again")
    s, _ = api.call("POST", f"/alerts/{weapon}/resolve", {"note": None}, guard1)
    check(s == 403, "a Guard cannot resolve alerts")

    # --- Manual reports ------------------------------------------------------------------------------------
    s, sub = api.call("POST", "/guard/manual-reports", {
        "client_report_id": str(uuid.uuid4()), "report_type": "Suspicious Person",
        "description": "A person was trying the back door of the Admin Block.", "building_id": block["id"],
        "floor_id": ground["id"], "area_id": None, "occurred_at": now(), "photo_base64": PNG}, guard1)
    check(s == 201 and sub["created"], "Guard submits a manual report with a photo")
    api.call("POST", "/guard/manual-reports", {"client_report_id": str(uuid.uuid4()), "report_type": "Property Damage",
              "description": "Broken window near the gate.", "building_id": block["id"]}, guard2)
    s, reports = api.call("GET", "/manual-reports?limit=26&offset=0", token=sup)
    check(s == 200 and len(reports) == 2, "Supervisor lists the campus's manual reports (page URL)")
    rid = sub["report"]["id"]
    s, photo = api.call("GET", f"/manual-reports/{rid}/photo", token=sup)
    check(s == 200 and isinstance(photo, bytes) and photo.startswith(b"\x89PNG"), "report photo is served to staff")
    s, reviewed = api.call("POST", f"/manual-reports/{rid}/review", {"note": "Patrol informed."}, sup)
    check(s == 200 and reviewed["status"] == "REVIEWED", "Supervisor marks a report as reviewed (page body)")
    s, _ = api.call("GET", "/manual-reports?limit=26&offset=0", token=guard1)
    check(s == 403, "Guards cannot open the staff report list")

    # --- Notifications ------------------------------------------------------------------------------------
    s, unread = api.call("GET", "/notifications?unread_only=true&limit=100", token=a.admin)
    check(s == 200 and any(n["kind"] == "MANUAL_REPORT_SUBMITTED" for n in unread), "Admin is notified of manual reports")
    s, _ = api.call("POST", f"/notifications/{unread[0]['id']}/read", token=a.admin)
    check(s == 204, "mark one notification as read")
    s, mine = api.call("GET", "/notifications?unread_only=false&limit=100", token=sup)
    s2, other = api.call("POST", f"/notifications/{mine[0]['id']}/read", token=a.admin)
    check(s2 == 404 or (s2 == 204 and api.call("GET", "/notifications?unread_only=false&limit=100", token=sup)[1][0]["read_at"] is None),
          "nobody can mark another user's notification as read")
    s, done = api.call("POST", "/notifications/read-all", token=a.admin)
    check(s == 200 and done["marked_read"] >= 1, "mark all as read")
    s, left = api.call("GET", "/notifications?unread_only=true&limit=100", token=a.admin)
    check(s == 200 and left == [], "nothing unread afterwards")

    # --- Another organization sees none of it --------------------------------------------------------------
    b = Org(api, args.outbox, "Other Org")
    check(api.call("GET", "/alerts?limit=26&offset=0", token=b.admin)[1] == [], "another organization lists no alerts")
    check(api.call("GET", f"/alerts/{weapon}", token=b.admin)[0] == 404, "another organization cannot open the alert")
    check(api.call("GET", f"/alerts/{weapon}/messages", token=b.admin)[0] == 404, "… nor its chat")
    check(api.call("POST", f"/alerts/{weapon}/dismiss", {"reason": "x"}, b.admin)[0] == 404, "… nor dismiss it")
    check(api.call("GET", f"/manual-reports/{rid}", token=b.admin)[0] == 404, "… nor open the manual report")
    check(api.call("GET", "/notifications?unread_only=false&limit=100", token=b.admin)[1] == [], "… and receives none of its notifications")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(json.dumps({"organization_name": a.name, "organization_id": a.org_id, "admin_email": a.admin_email,
                      "supervisor_email": sup_email, "password": PASSWORD, "alert": weapon, "report": rid}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
