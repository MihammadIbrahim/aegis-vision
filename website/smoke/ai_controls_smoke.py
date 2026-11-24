"""Live smoke check: AI feature controls (decision D-028), end to end.

Through the Vite dev server's /api proxy, against a real Core API on a scratch database: real sign-ins (codes from the
development outbox). It checks that new cameras run no AI module; AI features chosen with the camera's details; the
AI pills data on GET /cameras; multi-camera bulk ADD / REMOVE / SET (all or nothing, campus-scoped, password needed);
the Admin's Supervisor visibility (hidden modules disappear for the Supervisor and are kept by the Supervisor's
changes); and the Super Admin's per-organization block / unblock (password, immediate effect on the vision worker's
configuration, no automatic re-enable, the other organization untouched, audit records).

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox (see
smoke/README.md).

    python3 smoke/ai_controls_smoke.py --web http://127.0.0.1:5181 --outbox /path/to/outbox \
        --service-token <INTERNAL_SERVICE_TOKEN> --super-admin <username> --super-admin-password <password>

Standard library only. Exit code 0 when every check passes. The data is left in place (screenshots use it).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from operations_smoke import Org
from personnel_smoke import PASSWORD, Api, check, results
from realtime_comms_smoke import second_campus

NONE = dict.fromkeys(("human_detection", "person_reid", "weapon_detection", "fight_detection", "face_recognition"), False)


def worker(web: str, service_token: str, camera: str) -> list[str]:
    """What a vision worker is told to run on the camera (GET /internal/cameras/{id}/ai-config)."""
    request = urllib.request.Request(f"{web.rstrip('/')}/api/v1/internal/cameras/{camera}/ai-config",
                                     headers={"X-Service-Token": service_token})  # fmt: skip
    with urllib.request.urlopen(request) as response:
        modules: list[str] = json.loads(response.read())["modules"]
    return modules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--service-token", required=True, help="the API's INTERNAL_SERVICE_TOKEN (scratch value)")
    parser.add_argument("--super-admin", required=True)
    parser.add_argument("--super-admin-password", required=True)
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api, web = Api(args.web), args.web

    def run(camera: str) -> list[str]:
        return worker(web, args.service_token, camera)

    # --- Setup ----------------------------------------------------------------------------------------------------
    org = Org(api, args.outbox, "AI Controls Smoke")
    other = Org(api, args.outbox, "AI Controls Other")
    sup_email = f"sup.{org.run}@smoke.example.com"
    org.create("/organization-admin/supervisors", org.admin, org.admin_email, "Sana Malik", sup_email)
    sup = org.sign_in(sup_email)
    _, block = api.call("POST", "/locations/buildings", {"name": "Admin Block", "kind": "INDOOR"}, org.admin)
    _, ground = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Ground", "level": 0}, org.admin)
    where = {"building_id": block["id"], "floor_id": ground["id"], "area_id": None}

    def add(token: str, name: str, **extra: Any) -> tuple[int, dict[str, Any]]:
        body = {"display_name": name, "connection_type": "RTSP", "stream_url": f"rtsp://10.0.0.{len(name)}/{name.replace(' ', '-')}",
                "location": where, **extra}  # fmt: skip
        status, camera = api.call("POST", "/cameras", body, token)
        return status, camera

    # --- 1. New cameras run nothing ---------------------------------------------------------------------------------
    s, plain = add(org.admin, "Main Gate")
    check(s == 201 and plain["ai_modules"] == [] and plain["ai_effective_modules"] == [] and run(plain["id"]) == [],
          "a new camera without AI features runs nothing (ai_modules [], worker [])")  # fmt: skip
    s, chosen = add(sup, "Lobby", ai_modules={"weapon_detection": True, "human_detection": True})
    check(s == 201 and chosen["ai_modules"] == ["human_detection", "weapon_detection"]
          and run(chosen["id"]) == ["human_detection", "weapon_detection"],
          "features chosen with the camera run at once (Supervisor add)")  # fmt: skip
    s, body = add(org.admin, "Face Cam", ai_modules={"face_recognition": True})
    check(s == 409 and body.get("code") == "MODULE_NOT_PERMITTED", "a module the platform disables is refused (409)")
    s, edited = api.call("PUT", f"/cameras/{chosen['id']}", {"display_name": "Lobby", "connection_type": "RTSP",
                         "stream_url": chosen["stream_url"], "location": where,
                         "ai_modules": {"fight_detection": True}}, org.admin)  # fmt: skip
    check(s == 200 and edited["ai_modules"] == ["human_detection", "weapon_detection", "fight_detection"],
          "edit adds a feature and keeps the others")  # fmt: skip
    _, listed = api.call("GET", "/cameras", token=sup)
    check({c["display_name"]: c["ai_modules"] for c in listed}.get("Main Gate") == [], "GET /cameras carries the AI pills data")

    # --- 2. Search and bulk ---------------------------------------------------------------------------------------------
    for name in ("Gate North", "Gate South", "Gate East"):
        add(org.admin, name)
    _, gates = api.call("GET", "/cameras?q=gate", token=sup)
    ids = [c["id"] for c in gates]
    check(len(ids) == 4, f"search 'gate' finds the 4 gate cameras ({len(ids)})")
    bulk = {"camera_ids": ids, "mode": "ADD", "modules": {"weapon_detection": True, "fight_detection": True},
            "current_password": PASSWORD}  # fmt: skip
    s, added = api.call("PUT", "/ai-configuration/cameras/bulk", bulk, sup)
    check(s == 200 and added["updated_count"] == 4 and all(run(i) == ["weapon_detection", "fight_detection"] for i in ids),
          "bulk ADD: 4 cameras run weapon + fight detection")  # fmt: skip
    s, _ = api.call("PUT", "/ai-configuration/cameras/bulk", {**bulk, "mode": "REMOVE", "modules": {"fight_detection": True},
                                                               "camera_ids": ids[:2]}, sup)  # fmt: skip
    check(s == 200 and run(ids[0]) == ["weapon_detection"] and run(ids[3]) == ["weapon_detection", "fight_detection"],
          "bulk REMOVE: only the selected cameras lose fight detection")  # fmt: skip
    s, _ = api.call("PUT", "/ai-configuration/cameras/bulk", {**bulk, "mode": "SET", "modules": {"human_detection": True}}, org.admin)
    check(s == 200 and all(run(i) == ["human_detection"] for i in ids), "bulk SET (Replace): exactly human detection")
    north = second_campus(api, org, args.outbox)
    _, north_camera = add(north, "North Gate", location=None)
    s, body = api.call("PUT", "/ai-configuration/cameras/bulk", {**bulk, "camera_ids": [ids[0], north_camera["id"]]}, sup)
    check(s == 404 and run(ids[0]) == ["human_detection"], "another campus's camera: 404 and nothing changes")
    s, _ = api.call("PUT", "/ai-configuration/cameras/bulk", {**bulk, "current_password": "Wrong-pass-2026"}, sup)
    check(s == 403 and run(ids[1]) == ["human_detection"], "a wrong password: 403 and nothing changes")

    # --- 3. Supervisor visibility ---------------------------------------------------------------------------------------
    _, view = api.call("GET", "/ai-configuration", token=org.admin)
    check(view["supervisor_visible_modules"] == {k: True for k in NONE}, "Admin: every module visible to Supervisors by default")
    campus = {k: view["campus"][k] for k in NONE}
    s, _ = api.call("PUT", "/ai-configuration/campus", {**campus, "supervisor_visible_modules": {**{k: True for k in NONE},
                    "weapon_detection": False}, "current_password": PASSWORD}, sup)  # fmt: skip
    check(s == 403, "a Supervisor cannot change Supervisor visibility (403)")
    s, _ = api.call("PUT", "/ai-configuration/campus", {**campus, "supervisor_visible_modules": {**{k: True for k in NONE},
                    "weapon_detection": False}, "current_password": PASSWORD}, org.admin)  # fmt: skip
    _, seen = api.call("GET", "/ai-configuration", token=sup)
    check(s == 200 and "weapon_detection" not in [m["key"] for m in seen["modules"]] and seen["supervisor_visible_modules"] is None,
          "Admin hides weapon detection: the Supervisor no longer sees it")  # fmt: skip
    lobby = chosen["id"]
    s, body = api.call("PUT", f"/ai-configuration/cameras/{lobby}", {**NONE, "weapon_detection": True, "current_password": PASSWORD}, sup)
    check(s == 403 and body.get("code") == "AI_MODULE_HIDDEN_FROM_SUPERVISOR", "the Supervisor cannot switch it on (403)")
    s, _ = api.call("PUT", f"/ai-configuration/cameras/{lobby}", {**NONE, "person_reid": True, "current_password": PASSWORD}, sup)
    check(s == 200 and run(lobby) == ["person_reid", "weapon_detection"],
          "the Supervisor's change keeps the hidden module running")  # fmt: skip

    # --- 4. Super Admin block / unblock --------------------------------------------------------------------------------
    _, platform = api.call("POST", "/platform/auth/login", {"username": args.super_admin, "password": args.super_admin_password})
    sa = platform["access_token"]
    s, detail = api.call("GET", f"/platform/organizations/{org.org_id}", token=sa)
    statuses = {p["module_key"]: p["status"] for p in detail.get("ai_permissions", [])}
    check(s == 200 and statuses.get("weapon_detection") == "ALLOWED" and statuses.get("face_recognition") == "DISABLED_GLOBALLY",
          "Super Admin sees the organization's AI feature access")  # fmt: skip
    url = f"/platform/organizations/{org.org_id}/ai-permissions"
    s, _ = api.call("PUT", url, {"module_key": "weapon_detection", "blocked": True, "current_password": PASSWORD}, org.admin)
    check(s == 401, "a tenant token cannot block (401)")
    s, _ = api.call("PUT", url, {"module_key": "weapon_detection", "blocked": True, "current_password": "Not-the-password-1"}, sa)
    check(s == 403 and "weapon_detection" in run(lobby), "a wrong Super Admin password: 403 and nothing changes")
    _, other_cam = add(other.admin, "Other Gate", location=None, ai_modules={"weapon_detection": True})
    s, blocked = api.call("PUT", url, {"module_key": "weapon_detection", "blocked": True, "current_password": args.super_admin_password}, sa)
    check(s == 200 and {p["module_key"]: p["status"] for p in blocked["ai_permissions"]}["weapon_detection"] == "BLOCKED",
          "Super Admin blocks weapon detection for the organization")  # fmt: skip
    check(run(lobby) == ["person_reid"] and run(other_cam["id"]) == ["weapon_detection"],
          "the block is immediate for this organization only")  # fmt: skip
    _, admin_view = api.call("GET", "/ai-configuration", token=org.admin)
    check(admin_view["blocked_by_platform"] == ["weapon_detection"] and "weapon_detection" not in admin_view["available_modules"],
          "Admins see it as blocked and not available")  # fmt: skip
    s, body = add(org.admin, "Blocked Cam", ai_modules={"weapon_detection": True})
    check(s == 403 and body.get("code") == "AI_MODULE_BLOCKED_BY_PLATFORM", "it cannot be switched on (403 AI_MODULE_BLOCKED_BY_PLATFORM)")
    s, _ = api.call("PUT", url, {"module_key": "weapon_detection", "blocked": False, "current_password": args.super_admin_password}, sa)
    check(s == 200 and run(lobby) == ["person_reid"], "unblocking does not switch any camera back on")
    s, _ = api.call("PUT", f"/ai-configuration/cameras/{lobby}", {**NONE, "weapon_detection": True, "person_reid": True,
                    "current_password": PASSWORD}, org.admin)  # fmt: skip
    check(s == 200 and run(lobby) == ["person_reid", "weapon_detection"], "after unblocking the Admin can switch it on again")
    s, logs = api.call("GET", "/platform/audit-logs?action=ORGANIZATION_AI_PERMISSIONS_UPDATED&limit=50&offset=0", token=sa)
    mine = [e for e in logs.get("items", []) if e.get("organization_id") == org.org_id and e["outcome"] == "SUCCESS"]
    check(s == 200 and len(mine) == 2, f"platform audit: block and unblock recorded ({len(mine)})")
    s, logs = api.call("GET", "/audit-logs?category=AI_SETTINGS&limit=100&offset=0", token=org.admin)
    actions = {i["action"] for i in logs.get("items", [])}
    check({"CAMERA_AI_BULK_UPDATED", "CAMERA_AI_SETTINGS_UPDATED", "AI_SETTINGS_UPDATED"} <= actions,
          "organization audit: bulk, camera and campus AI changes recorded")  # fmt: skip

    print(f"\nAdmin {org.admin_email} / Supervisor {sup_email} (password {PASSWORD}); organization {org.org_id}")
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as error:
        print(f"Cannot reach the server: {error}")
        sys.exit(2)
