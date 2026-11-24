"""Live smoke check of the Personnel Management web contract (Phase 0 safety net).

It calls the Core API **through the Vite dev server's /api proxy**, with the same URLs, methods and
bodies the web pages send. Sign-in is real: a new organization is registered and every one-time code
is read from the API's development email outbox. Nothing is mocked and nothing is deleted.

ONLY run it against a server that uses an isolated scratch database and
EMAIL_DELIVERY_MODE=dev_outbox (see smoke/README.md). Each run creates a new organization; it never
modifies or removes existing records.

    python3 smoke/personnel_smoke.py --web http://127.0.0.1:5179 --outbox /path/to/dev_outbox

Standard library only. Exit code 0 when every check passes.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

PASSWORD = "Smoke-check-pass-42"
results: list[tuple[bool, str]] = []


class Api:
    def __init__(self, web: str) -> None:
        self.base = web.rstrip("/") + "/api/v1"

    def call(self, method: str, path: str, body=None, token: str | None = None, raw: bytes | None = None,
             content_type: str | None = None):  # type: ignore[no-untyped-def]
        data, headers = None, {}
        if raw is not None:
            data, headers["Content-Type"] = raw, content_type or "application/octet-stream"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for _ in range(8):
            request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request) as response:
                    payload = response.read()
                    return response.status, _decode(payload, response.headers.get("content-type", ""))
            except urllib.error.HTTPError as error:
                payload = error.read()
                if error.code == 429:  # one-time-code cooldown: wait as told, then retry
                    time.sleep(int(error.headers.get("Retry-After", "10")) + 1)
                    continue
                return error.code, _decode(payload, error.headers.get("content-type", ""))
        raise RuntimeError("still rate limited")


def _decode(payload: bytes, content_type: str):  # type: ignore[no-untyped-def]
    if "json" in content_type:
        return json.loads(payload or b"null")
    return payload


def check(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return ok


def code_for(outbox: Path, email: str, after: float) -> str:
    """The newest 6-digit code sent to ``email`` since ``after``."""
    for _ in range(50):
        files = sorted((p for p in outbox.glob("*.txt") if p.stat().st_mtime >= after), key=lambda p: p.stat().st_mtime)
        for path in reversed(files):
            text = path.read_text(encoding="utf-8")
            if email.lower() in text.lower():
                found = re.search(r"verification code is: ([0-9]{6})", text)
                if found:
                    return found.group(1)
        time.sleep(0.2)
    raise RuntimeError(f"no code for {email} in the outbox")


def multipart(fields: dict[str, str], file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        "Content-Type: text/csv\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


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

    api = Api(args.web)
    run = uuid.uuid4().hex[:8]
    org_name = f"Smoke Org {run}"
    admin_email, sup_email = f"admin.{run}@smoke.example.com", f"sup.{run}@smoke.example.com"

    with urllib.request.urlopen(args.web) as page:
        check(b"<div id=\"root\">" in page.read(), "web app index is served by the dev server")

    # --- Real registration and sign-in (codes from the development outbox) ---------------------------
    t = time.time() - 1
    status, reg = api.call("POST", "/auth/organization-registrations", {
        "organization_name": org_name, "campus_name": "Main Campus", "admin_full_name": "Smoke Admin", "username": f"admin{run}",
        "email": admin_email, "phone_number": "+923001234567", "password": PASSWORD})
    check(status == 202 or status == 201 or status == 200, f"register organization ({status})")
    status, done = api.call("POST", f"/auth/organization-registrations/{reg['registration_id']}/verify",
                            {"otp": code_for(args.outbox, admin_email, t)})
    check(status == 200 and done["status"] == "COMPLETED", "verify registration code")
    org_id = done["organization_id"]

    def sign_in(email: str) -> str:
        start = time.time() - 1
        s, sent = api.call("POST", "/auth/organization-admin/login", {
            "organization_name": org_name, "organization_id": org_id, "email": email, "password": PASSWORD})
        assert s == 200, (s, sent)
        s, token = api.call("POST", f"/auth/organization-admin/login/{sent['login_id']}/verify",
                            {"otp": code_for(args.outbox, email, start)})
        assert s == 200, (s, token)
        return token["access_token"]

    admin = sign_in(admin_email)
    check(api.call("GET", "/auth/me", token=admin)[1]["role"] == "ADMIN", "Admin signs in with password + emailed code")

    t = time.time() - 1
    status, auth = api.call("POST", "/organization-admin/supervisors/authorization", {"current_password": PASSWORD}, admin)
    status, verified = api.call("POST", f"/organization-admin/supervisors/authorization/{auth['id']}/verify",
                                {"otp": code_for(args.outbox, admin_email, t)}, admin)
    t = time.time() - 1
    status, creation = api.call("POST", "/organization-admin/supervisors", {
        "authorization_id": verified["authorization_id"], "full_name": "Smoke Supervisor", "username": f"sup{run}",
        "email": sup_email, "phone_number": "+923001234568", "password": PASSWORD}, admin)
    status, _ = api.call("POST", f"/organization-admin/supervisors/creations/{creation['id']}/verify",
                         {"otp": code_for(args.outbox, sup_email, t)}, admin)
    check(status == 201, "Admin creates a Supervisor (real authorization and email codes)")
    supervisor = sign_in(sup_email)
    check(api.call("GET", "/auth/me", token=supervisor)[1]["role"] == "SUPERVISOR", "Supervisor signs in")

    # --- Personnel (the exact bodies the form and detail pages send) --------------------------------
    cats = {c["code"]: c["id"] for c in api.call("GET", "/personnel/categories", token=admin)[1]}
    check(len(cats) == 8 and len(api.call("GET", "/personnel/fields", token=admin)[1]) == 16, "form loads fields and categories")
    form_body = {"full_name": "Smoke Student", "category_id": cats["STUDENT"], "external_id": f"S-{run}",
                 "email": f"student.{run}@smoke.example.com", "phone": None, "department": "CS", "designation": None,
                 "joining_date": None, "start_date": None, "end_date": None, "address": "Private Street 9",
                 "emergency_contact_name": None, "emergency_contact_phone": None, "emergency_contact_relation": None,
                 "notes": None, "custom_attributes": {}, "location": None}
    status, created = api.call("POST", "/personnel", form_body, admin)
    check(status == 201 and created["personnel"]["personnel_id"].startswith("PSN-"), "Admin registers a person (form body)")
    person = created["personnel"]["id"]
    status, bad = api.call("POST", "/personnel", {**form_body, "external_id": None, "email": "x"}, admin)
    check(status == 422 and {e["field"] for e in bad["errors"]} == {"external_id", "email"}, "field errors come back per field")
    status, listing = api.call("GET", f"/personnel?q=Smoke&status=ACTIVE&limit=25&offset=0", token=admin)
    check(status == 200 and listing["total"] == 1 and listing["items"][0]["email"], "Admin directory shows contact details")
    status, listing = api.call("GET", "/personnel?q=Smoke&limit=25&offset=0", token=supervisor)
    check(status == 200 and listing["items"][0]["email"] is None, "Supervisor directory hides contact details")
    status, basic = api.call("GET", f"/personnel/{person}", token=supervisor)
    check(status == 200 and "address" not in basic and "email" not in basic, "Supervisor gets basic details only")
    for method, path, body in (("POST", "/personnel", form_body), ("PUT", f"/personnel/{person}", form_body),
                               ("POST", f"/personnel/{person}/deactivate", {"reason": None}),
                               ("GET", f"/personnel/{person}/history", None), ("GET", "/personnel-imports", None),
                               ("GET", "/personnel/custom-fields", None)):
        status, _ = api.call(method, path, body, supervisor)
        check(status == 403, f"Supervisor refused: {method} {path.replace(person, '{id}')}")
    status, photo = api.call("GET", f"/personnel/{person}/photo", token=supervisor)
    check(status == 403 and photo.get("code") == "PHOTO_REVIEW_ONLY", "Supervisor cannot see photos outside a review")

    # --- Restricted access (the grant form's body) ------------------------------------------------------
    status, block = api.call("POST", "/locations/buildings", {"name": "Smoke Block", "kind": "INDOOR"}, admin)
    status, room = api.call("POST", f"/locations/buildings/{block['id']}/areas", {"name": "Server Room"}, admin)
    status, _ = api.call("PUT", f"/restricted-access/areas/{room['id']}", {"access_note": None}, admin)
    check(status == 200, "Admin marks an area restricted")
    status, grants = api.call("POST", "/restricted-access/grants", {
        "personnel_id": person, "area_ids": [room["id"]], "valid_from": None, "valid_until": None, "schedule": None,
        "note": None}, admin)
    check(status == 201 and len(grants) == 1, "Admin grants access (form body)")
    status, seen = api.call("GET", f"/restricted-access/personnel/{person}/grants?include_revoked=false", token=supervisor)
    check(status == 200 and len(seen) == 1 and seen[0]["note"] is None, "Supervisor sees the current permission")
    status, _ = api.call("POST", "/restricted-access/grants", {"personnel_id": person, "area_ids": [room["id"]]}, supervisor)
    check(status == 403, "Supervisor cannot grant access")
    status, revoked = api.call("POST", f"/restricted-access/grants/{grants[0]['id']}/revoke", {"reason": "Smoke"}, admin)
    check(status == 200 and revoked["status"] == "REVOKED", "Admin revokes with a reason")

    # --- Import (multipart upload, mapping, confirm) ------------------------------------------------------
    status, template = api.call("GET", "/personnel-imports/template.xlsx", token=admin)
    check(status == 200 and template[:2] == b"PK", "template downloads as .xlsx")
    csv_data = f"Name,Type,Registration No\nImported One,Student,I1-{run}\nImported Two,Student,I2-{run}\n".encode()
    body, content_type = multipart({"header_row": "1"}, "file", "smoke.csv", csv_data)
    status, upload = api.call("POST", "/personnel-imports", raw=body, content_type=content_type, token=admin)
    check(status == 201 and [c["target"] for c in upload["columns"]] == ["full_name", "category", "external_id"],
          "upload reads headers and suggests fields")
    batch = upload["batch"]["id"]
    status, mapped = api.call("PUT", f"/personnel-imports/{batch}/mapping", {"columns": [
        {"index": 0, "target": "full_name"}, {"index": 1, "target": "category"}, {"index": 2, "target": "external_id"}],
        "save_profile_as": None}, admin)
    check(status == 200 and mapped["batch"]["status"] == "VALIDATED", "mapping confirmed, file validated")
    status, preview = api.call("GET", f"/personnel-imports/{batch}/preview?limit=20&offset=0", token=admin)
    check(status == 200 and len(preview) == 2, "preview shows the rows")
    status, confirmed = api.call("POST", f"/personnel-imports/{batch}/confirm", {"acknowledge_warnings": True}, admin)
    check(status == 200 and confirmed["batch"]["imported_count"] == 2, "confirmed import writes every row")
    status, history = api.call("GET", "/personnel-imports?limit=50&offset=0", token=admin)
    check(status == 200 and history[0]["status"] == "IMPORTED", "import history lists the batch")

    status, off = api.call("POST", f"/personnel/{person}/deactivate", {"reason": "Smoke done"}, admin)
    check(status == 200 and off["status"] == "INACTIVE", "Admin deactivates with a reason (record kept)")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
