"""Live smoke check of the Staff Management web contract (Phase 8; decision D-017).

It calls the Core API **through the Vite dev server's /api proxy**, with the URLs, methods and bodies
the web pages send (features/staff/api.ts). Sign-in and the creation codes are real (development email
outbox).

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox
(see smoke/README.md). Each run creates two new organizations; nothing existing is changed or removed.

    python3 smoke/staff_smoke.py --web http://127.0.0.1:5179 --outbox /path/to/outbox

Standard library only. Exit code 0 when every check passes.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from operations_smoke import Org
from personnel_smoke import PASSWORD, Api, check, code_for, results

NEW_GUARD_PASSWORD = "Fresh-guard-2027"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api = Api(args.web)
    a = Org(api, args.outbox, "Staff Smoke")

    # --- Creation wizard, step by step, as the page sends it (Admin) ---------------------------------
    t = time.time() - 1
    s, sent = api.call("POST", "/guards/authorization", {"current_password": PASSWORD}, a.admin)
    check(s == 200 and sent["status"] == "OTP_REQUIRED" and "***" in sent["email"], "step 1: own password → code to the Admin")
    s, ok = api.call("POST", f"/guards/authorization/{sent['id']}/verify", {"otp": code_for(args.outbox, a.admin_email, t)}, a.admin)
    check(s == 200 and ok["status"] == "VERIFIED", "step 2: Admin's code opens the form")
    g_email = f"g1.{a.run}@smoke.example.com"
    t = time.time() - 1
    s, form = api.call("POST", "/guards", {
        "authorization_id": ok["authorization_id"], "full_name": "Bilal Guard", "username": f"bilal{a.run}",
        "email": g_email, "phone_number": "+92 300 1234567", "password": PASSWORD}, a.admin)
    check(s == 201 and form["status"] == "OTP_REQUIRED", "step 3: the form → code to the new Guard")
    s, created = api.call("POST", f"/guards/creations/{form['id']}/verify", {"otp": code_for(args.outbox, g_email, t)}, a.admin)
    check(s == 201 and len(created["guard_id"]) == 27 and created["guard_id"][:6].isalpha() and created["guard_id"][6:].isdigit(),
          "step 4: Guard created with a 27-character ID (6 letters + 21 digits)")
    guard_id = created["guard_id"]
    sup_email = f"sup.{a.run}@smoke.example.com"
    a.create("/organization-admin/supervisors", a.admin, a.admin_email, "Sana Supervisor", sup_email)
    sup = a.sign_in(sup_email)

    # --- Supervisors cannot create staff ------------------------------------------------------------
    s, refused = api.call("POST", "/guards/authorization", {"current_password": PASSWORD}, sup)
    check(s == 403 and refused.get("code") == "ADMIN_ONLY", "Supervisor cannot start Guard creation (403 ADMIN_ONLY)")
    s, _ = api.call("POST", "/organization-admin/supervisors/authorization", {"current_password": PASSWORD}, sup)
    check(s == 403, "Supervisor cannot start Supervisor creation (403)")

    # --- Listing: Standby vs Assigned ----------------------------------------------------------------
    s, rows = api.call("GET", "/staff?role=GUARD&status=ACTIVE&limit=26&offset=0", token=sup)
    row = next((r for r in rows if r["display_id"] == guard_id), None) if s == 200 else None
    check(row is not None and row["category"] == "STANDBY" and row["location"] is None, "new Guard is listed as STANDBY")
    gid = row["id"] if row else ""
    s, _ = api.call("GET", "/staff?role=SUPERVISOR&limit=26&offset=0", token=sup)
    check(s == 403, "Supervisor cannot list Supervisors (403)")
    s, sups = api.call("GET", "/staff?role=SUPERVISOR&status=ACTIVE&limit=26&offset=0", token=a.admin)
    check(s == 200 and [x["full_name"] for x in sups] == ["Sana Supervisor"], "Admin lists Supervisors")
    _, block = api.call("POST", "/locations/buildings", {"name": "Gate House", "kind": "INDOOR"}, a.admin)
    where = {"building_id": block["id"], "floor_id": None, "area_id": None}
    standby_token = a.guard_sign_in(guard_id, g_email, "STANDBY")
    check(api.call("GET", "/guard/dashboard", token=standby_token)[1]["dashboard"] == "STANDBY_EMERGENCY",
          "new Guard signs in as Standby (Standby Emergency Dashboard)")
    s, _ = api.call("PUT", f"/locations/guard-assignments/{guard_id}", where, sup)
    s, ended = api.call("GET", "/guard/dashboard", token=standby_token)
    check(s == 401 and ended.get("code") == "ASSIGNMENT_CHANGED", "setting a post signs the Guard out at once (401)")
    s, mismatch = api.call("POST", "/auth/guard/login", {"operational_mode": "STANDBY", "guard_id": guard_id,
                                                          "email": g_email, "password": PASSWORD})
    check(s == 409 and mismatch.get("code") == "OPERATIONAL_MODE_MISMATCH", "Standby sign-in refused for a stationed Guard")
    stationed_token = a.guard_sign_in(guard_id, g_email, "STATIONED")
    check(api.call("GET", "/guard/dashboard", token=stationed_token)[1]["dashboard"] == "STATIONED_MONITORING",
          "Stationed sign-in opens the Stationed Monitoring Dashboard")
    sent_before = len(list(args.outbox.iterdir()))
    s, _ = api.call("POST", "/auth/password-reset", {"guard_id": guard_id, "email": g_email, "operational_mode": "STANDBY"})
    check(s == 202 and len(list(args.outbox.iterdir())) == sent_before, "forgot password with the wrong mode: same answer, no code")
    s, _ = api.call("POST", "/auth/password-reset", {"guard_id": guard_id, "email": g_email, "operational_mode": "STATIONED"})
    check(s == 202 and len(list(args.outbox.iterdir())) > sent_before, "forgot password with the matching mode sends a code")
    s, assigned = api.call("GET", f"/staff/{gid}", token=sup)
    check(s == 200 and assigned["category"] == "STATIONED" and assigned["location"]["building_name"] == "Gate House",
          "Supervisor assigns a post: Guard becomes STATIONED")
    s, only = api.call("GET", "/staff?role=GUARD&category=STATIONED&limit=26&offset=0", token=sup)
    check(s == 200 and [x["display_id"] for x in only] == [guard_id], "category filter STATIONED")
    s, _ = api.call("DELETE", f"/locations/guard-assignments/{guard_id}", token=sup)
    s, back = api.call("GET", f"/staff/{gid}", token=sup)
    check(back["category"] == "STANDBY", "removing the post makes the Guard STANDBY again")
    check(api.call("GET", "/guard/dashboard", token=stationed_token)[0] == 401, "… and signs the Guard out again")

    # --- Updates by the Supervisor: audited, Admins notified -------------------------------------------
    body = {"full_name": "Bilal Khan", "username": f"bilalk{a.run}", "phone_number": "+923001112233"}
    s, wrong = api.call("PUT", f"/staff/guards/{gid}", {**body, "current_password": "Wrong-pass-2026"}, sup)
    check(s == 403 and wrong.get("code") == "CURRENT_PASSWORD_INCORRECT", "edit needs the Supervisor's own password")
    s, edited = api.call("PUT", f"/staff/guards/{gid}", {**body, "current_password": PASSWORD}, sup)
    check(s == 200 and edited["full_name"] == "Bilal Khan" and edited["phone_number"] == "+923001112233",
          "Supervisor edits Guard details (PUT /staff/guards/{id})")
    s, taken = api.call("PUT", f"/staff/guards/{gid}", {**body, "username": f"admin{a.run}", "current_password": PASSWORD}, sup)
    check(s == 409 and taken.get("code") == "USERNAME_TAKEN", "a taken username is refused (409)")
    g_token = a.guard_sign_in(guard_id, g_email)
    time.sleep(1.1)  # tokens carry whole seconds: a change in the same second would not end this session
    s, _ = api.call("PUT", f"/staff/guards/{gid}/password", {"new_password": NEW_GUARD_PASSWORD, "current_password": PASSWORD}, sup)
    check(s == 204, "Supervisor sets the Guard's password (PUT /staff/guards/{id}/password)")
    check(api.call("GET", "/guard/location", token=g_token)[0] == 401, "… the Guard's earlier session stops working")
    s, notes = api.call("GET", "/notifications?unread_only=false&limit=100", token=a.admin)
    kinds = sorted(n["kind"] for n in notes if n.get("subject_user_id") == gid)
    check(kinds == ["GUARD_LOCATION_CHANGED", "GUARD_LOCATION_CHANGED", "GUARD_PASSWORD_SET", "GUARD_UPDATED"],
          "Admin is notified of each Supervisor change to the Guard")
    s, sup_notes = api.call("GET", "/notifications?unread_only=false&limit=100", token=sup)
    check(not [n for n in sup_notes if n.get("subject_user_id")], "… and the Supervisor is not")

    # --- Deactivation: Admins only -------------------------------------------------------------------------
    s, _ = api.call("POST", f"/staff/{gid}/deactivate", {"current_password": PASSWORD}, sup)
    check(s == 403, "Supervisor cannot deactivate a Guard (403)")
    s, left = api.call("POST", f"/staff/{gid}/deactivate", {"current_password": PASSWORD}, a.admin)
    check(s == 200 and left["status"] == "LEFT", "Admin deactivates the Guard (status LEFT)")
    s, _ = api.call("POST", "/auth/guard/login", {"operational_mode": "STANDBY", "guard_id": guard_id, "email": g_email,
                                                   "password": NEW_GUARD_PASSWORD})
    check(s != 200, "a deactivated Guard cannot sign in")
    s, again = api.call("POST", f"/staff/{gid}/reactivate", {"current_password": PASSWORD}, a.admin)
    check(s == 200 and again["status"] == "ACTIVE" and again["category"] == "STANDBY", "Admin reactivates the Guard (Standby)")

    # --- Another organization sees nothing ------------------------------------------------------------------
    o = Org(api, args.outbox, "Staff Other")
    check(api.call("GET", f"/staff/{gid}", token=o.admin)[0] == 404, "another organization gets 404 for the Guard")
    s, mine = api.call("GET", "/staff?role=GUARD&limit=26&offset=0", token=o.admin)
    check(s == 200 and mine == [], "… and lists no Guards")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(json.dumps({"organization_name": a.name, "organization_id": a.org_id, "admin_email": a.admin_email,
                      "supervisor_email": sup_email, "password": PASSWORD, "guard_id": guard_id}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
