"""Live smoke check: registration, campus requests, sign-in and forgot password, through the web proxy.

Sends exactly what the website's auth pages send (features/registration, auth/LoginPage,
auth/ForgotPasswordPage) to the real Core API, with codes from the development email outbox:
- organization registration with a required first-campus name (decision D-021): both IDs returned;
- "Already Registered Organization?" campus requests: exact organization name AND Organization ID,
  email code, "approval pending" at sign-in, Main Admin approval, then normal sign-in;
- forgot password with Organization ID + email: same answer for unknown accounts, code, one-time
  reset token, new password, earlier session signed out.

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox.
Each run creates a new organization; nothing is deleted.

    python3 smoke/auth_smoke.py --web http://127.0.0.1:5179 --outbox /path/to/outbox
"""

import argparse
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from personnel_smoke import PASSWORD, Api, check, code_for, results

ORG_ID = re.compile(r"[A-Z]{6}[0-9]{11}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True, help="Vite dev server, e.g. http://127.0.0.1:5179")
    parser.add_argument("--outbox", required=True, type=Path, help="the API's DEV_OUTBOX_DIR")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api, outbox, run = Api(args.web), args.outbox, uuid.uuid4().hex[:8]
    name, main_email = f"Auth Smoke {run}", f"main.{run}@smoke.example.com"

    def sign_in(email: str, password: str) -> tuple[int, dict]:
        t = time.time() - 1
        s, sent = api.call("POST", "/auth/organization-admin/login",
                           {"organization_name": name, "organization_id": org_id, "email": email, "password": password})
        if s != 200:
            return s, sent
        return api.call("POST", f"/auth/organization-admin/login/{sent['login_id']}/verify",
                        {"otp": code_for(outbox, email, t)})

    # --- Registration with a required first-campus name ---
    form = {"organization_name": name, "campus_name": "City Campus", "admin_full_name": "Main Admin",
            "username": f"main{run}", "email": main_email, "phone_number": "+923001234567", "password": PASSWORD}
    s, body = api.call("POST", "/auth/organization-registrations", {k: v for k, v in form.items() if k != "campus_name"})
    check(s == 422, f"registration without a campus name is refused ({s})")
    t = time.time() - 1
    s, reg = api.call("POST", "/auth/organization-registrations", form)
    check(s == 201, f"registration with a campus name is accepted ({s})")
    s, done = api.call("POST", f"/auth/organization-registrations/{reg['registration_id']}/verify",
                       {"otp": code_for(outbox, main_email, t)})
    org_id = done.get("organization_id", "")
    check(s == 200 and done["campus_name"] == "City Campus", "the first campus has the name given")
    check(bool(ORG_ID.fullmatch(org_id)) and bool(ORG_ID.fullmatch(done["main_admin"]["user_id"])),
          "both the Organization ID and the Main Admin ID are returned")

    # --- Sign-in ---
    s, token = sign_in(main_email, PASSWORD)
    check(s == 200 and "access_token" in token, "Main Admin signs in with the email code")
    main_token = token["access_token"]
    s, me = api.call("GET", "/auth/me", token=main_token)
    check(s == 200 and me["role"] == "ADMIN", "session is the Main Admin")

    # --- Campus request: exact name AND Organization ID ---
    branch_email, branch_pw = f"branch.{run}@smoke.example.com", "Branch-pass-2026"
    request = {"organization_name": name, "organization_id": org_id, "campus_name": "North Campus",
               "admin_full_name": "Branch Admin", "username": f"branch{run}", "email": branch_email,
               "phone_number": "+923001112233", "password": branch_pw}
    s, r = api.call("POST", "/auth/campus-requests", {**request, "organization_name": name.lower()})
    check(s == 404 and r["code"] == "ORGANIZATION_NOT_MATCHED", "campus request with the name in other capitals is refused")
    s, r = api.call("POST", "/auth/campus-requests", {**request, "organization_id": "ZZZZZZ00000000000"})
    check(s == 404 and r["code"] == "ORGANIZATION_NOT_MATCHED", "campus request with another Organization ID is refused")
    s, r = api.call("POST", "/auth/campus-requests", {**request, "campus_name": "city campus"})
    check(s == 409 and r["code"] == "CAMPUS_NAME_TAKEN", "campus name already used in the organization is refused")
    t = time.time() - 1
    s, r = api.call("POST", "/auth/campus-requests", request)
    check(s == 201, f"campus request with the exact name and ID is accepted ({s})")
    s, v = api.call("POST", f"/auth/campus-requests/{r['application_id']}/verify", {"otp": code_for(outbox, branch_email, t)})
    check(s == 200 and v["status"] == "PENDING_APPROVAL", "email verified; the request waits for the Main Admin")
    s, p = api.call("POST", "/auth/organization-admin/login",
                    {"organization_name": name, "organization_id": org_id, "email": branch_email, "password": branch_pw})
    check(s == 403 and p["code"] == "ADMIN_APPROVAL_PENDING", "sign-in before approval answers ADMIN_APPROVAL_PENDING")
    s, queue = api.call("GET", "/campus-requests", token=main_token)
    mine = [q for q in queue if q["applicant_email"] == branch_email] if s == 200 else []
    check(len(mine) == 1, "the request is in the Main Admin's queue")
    s, a = api.call("POST", f"/campus-requests/{mine[0]['id']}/approve", token=main_token)
    check(s == 200 and a["status"] == "APPROVED", "the Main Admin approves")
    s, token = sign_in(branch_email, branch_pw)
    check(s == 200, "the campus Admin now signs in normally")

    # --- Forgot password (Organization ID + email) ---
    s, unknown = api.call("POST", "/auth/password-reset", {"organization_id": org_id, "email": f"nobody.{run}@smoke.example.com"})
    t = time.time() - 1
    s2, started = api.call("POST", "/auth/password-reset", {"organization_id": org_id, "email": main_email})
    check(s == 202 and s2 == 202 and set(unknown) == set(started) and unknown["message"] == started["message"],
          "reset answers the same for unknown and known accounts")
    s, tok = api.call("POST", f"/auth/password-reset/{started['reset_id']}/verify", {"otp": code_for(outbox, main_email, t)})
    check(s == 200 and bool(tok.get("reset_token")), "the code gives a one-time reset token")
    new_pw = "Changed-pass-2026"
    s, changed = api.call("POST", f"/auth/password-reset/{started['reset_id']}/complete",
                          {"reset_token": tok["reset_token"], "new_password": new_pw, "confirm_new_password": new_pw})
    check(s == 200 and changed["status"] == "PASSWORD_CHANGED", "the new password is set")
    s, again = api.call("POST", f"/auth/password-reset/{started['reset_id']}/complete",
                        {"reset_token": tok["reset_token"], "new_password": new_pw, "confirm_new_password": new_pw})
    check(s >= 400, f"the reset token cannot be used twice ({s})")
    s, _ = api.call("GET", "/auth/me", token=main_token)
    check(s == 401, "the earlier session is signed out")
    s, _ = sign_in(main_email, PASSWORD)
    check(s == 401, "the old password no longer works")
    s, _ = sign_in(main_email, new_pw)
    check(s == 200, "the new password works")

    passed = sum(ok for ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
