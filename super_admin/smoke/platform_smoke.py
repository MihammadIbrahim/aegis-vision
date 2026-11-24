"""Live smoke check of the Super Admin web app's contract with the real Core API (decision D-019).

Calls every route the app uses, **through the Vite dev server's /api proxy**, with the app's exact
methods, queries and bodies, and checks that each response has exactly the fields declared in
src/api/types.ts (zero drift). Changes it makes (an organization's status, the Main Admin's phone, the
AI settings, the Super Admin credentials during the recovery check) are put back at the end.

ONLY run it against a scratch database. Five wrong passwords would lock the account, so the lockout is
not exercised here (backend and frontend tests cover it).

    python3 smoke/platform_smoke.py --web http://127.0.0.1:5182 --username U --password P \
        --recovery-username RU --recovery-password RP

Standard library only. Exit code 0 when every check passes.
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlparse

results: list[tuple[bool, str]] = []

# Field sets of src/api/types.ts.
ORGANIZATION = {"organization_id", "name", "official_email", "official_phone_number", "status", "created_at",
                "main_admin", "campus_count", "camera_count"}
MAIN_ADMIN = {"full_name", "username", "email", "phone_number"}
CAMPUS = {"name", "status", "created_at", "building_count", "perimeter_count", "floor_count", "area_count",
          "camera_count", "admin_count", "supervisor_count", "guard_count"}
AI_VALUES = {"human_detection_tracking_enabled", "person_reid_enabled", "weapon_detection_enabled",
             "fight_violence_detection_enabled", "restricted_area_face_recognition_enabled", "rag_assistant_enabled",
             "agentic_investigation_enabled", "default_confidence_threshold", "frame_sample_fps",
             "detection_cooldown_seconds", "max_task_retries", "task_timeout_seconds"}
AI_SETTINGS = AI_VALUES | {"modules", "updated_at", "updated_by_username"}
AI_PERMISSION = {"module_key", "feature", "label", "core", "blocked", "platform_enabled", "status"}  # decision D-028
AUDIT_ENTRY = {"id", "occurred_at", "actor_type", "actor_username", "action", "outcome", "organization_id",
               "organization_name", "ip_address", "user_agent", "details"}
COUNTS = {"organizations_total", "organizations_active", "organizations_inactive", "organizations_suspended",
          "deleted_organizations_total", "campuses_total", "campuses_active", "cameras_total", "admins_active",
          "supervisors_active", "guards_active"}
HEALTH = {"checked_at", "overall_status", "summary", "services", "storage", "problems"}
SERVICE = {"key", "name", "status", "latency_ms", "detail", "metrics"}
STORAGE = {"path", "accessible", "writable", "total_bytes", "used_bytes", "free_bytes", "used_percent", "level",
           "warning_percent", "critical_percent"}
TOKEN = {"access_token", "token_type", "expires_in"}
ME = {"id", "username", "role", "created_at", "last_login_at"}


def check(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return ok


class Api:
    def __init__(self, web: str) -> None:
        self.base = web.rstrip("/") + "/api/v1"
        self.token: str | None = None

    def call(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> tuple[int, Any, dict[str, str]]:
        url = self.base + path + ("?" + urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")}) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                raw, status, hdrs = response.read(), response.status, {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as error:
            raw, status, hdrs = error.read(), error.code, {k.lower(): v for k, v in error.headers.items()}
        text = raw.decode("utf-8")
        try:
            return status, json.loads(text) if text else None, hdrs
        except json.JSONDecodeError:
            return status, text, hdrs


def lm(token: str) -> str:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["lm"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--recovery-username", required=True)
    parser.add_argument("--recovery-password", required=True)
    a = parser.parse_args()
    if urlparse(a.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    api = Api(a.web)

    # --- Sign-in --------------------------------------------------------------------------------------
    s, bad, _ = api.call("POST", "/platform/auth/login", {"username": a.username, "password": "Wrong-pass-0000"})
    check(s == 401, "wrong password: 401")
    s, tok, _ = api.call("POST", "/platform/auth/login", {"username": a.username, "password": a.password})
    check(s == 200 and set(tok) == TOKEN and lm(tok["access_token"]) == "NORMAL", "sign-in: TokenResponse, lm NORMAL")
    api.token = tok["access_token"]
    s, me, _ = api.call("GET", "/platform/me")
    check(s == 200 and set(me) == ME, "GET /platform/me fields")

    # --- Overview, organizations ------------------------------------------------------------------------
    s, ov, _ = api.call("GET", "/platform/overview")
    check(s == 200 and set(ov) == {"counts", "health", "ai_modules", "recent_activity"} and set(ov["counts"]) == COUNTS
          and set(ov["health"]) == {"overall_status", "summary"}
          and all(set(e) == AUDIT_ENTRY for e in ov["recent_activity"]), "GET /platform/overview fields")
    s, page, _ = api.call("GET", "/platform/organizations", query={"sort": "created_at", "order": "desc", "limit": 25, "offset": 0})
    check(s == 200 and set(page) == {"items", "total", "limit", "offset"} and page["items"]
          and all(set(o) == ORGANIZATION for o in page["items"]), "GET /platform/organizations fields")
    org = next((o for o in page["items"] if o["main_admin"] and o["status"] == "ACTIVE"), None)
    if not check(org is not None, "an active organization with a Main Admin exists"):
        return 1
    check(set(org["main_admin"]) == MAIN_ADMIN, "main_admin fields")
    s, found, _ = api.call("GET", "/platform/organizations", query={"q": org["organization_id"], "status": "ACTIVE",
                                                                   "sort": "camera_count", "order": "asc", "limit": 25, "offset": 0})
    check(s == 200 and [o["organization_id"] for o in found["items"]] == [org["organization_id"]], "search + filters")
    s, csv_text, hdrs = api.call("GET", "/platform/organizations/export", query={"sort": "name", "order": "asc"})
    check(s == 200 and hdrs.get("content-type", "").startswith("text/csv") and "organization_id,name" in csv_text,
          "GET /platform/organizations/export is CSV")
    s, detail, _ = api.call("GET", f"/platform/organizations/{org['organization_id']}")
    check(s == 200 and set(detail) == ORGANIZATION | {"registered_address", "campuses", "ai_permissions"}
          and all(set(c) == CAMPUS for c in detail["campuses"])
          and all(set(p) == AI_PERMISSION for p in detail["ai_permissions"]), "GET /platform/organizations/{id} fields")
    # Per-organization AI feature access (decision D-028): one feature per click, password, then put back.
    ai_url = f"/platform/organizations/{org['organization_id']}/ai-permissions"
    s, perms, _ = api.call("GET", ai_url)
    check(s == 200 and set(perms) == {"organization_id", "ai_permissions"} and len(perms["ai_permissions"]) == 5,
          "GET /platform/organizations/{id}/ai-permissions fields")
    was = {p["module_key"]: p["blocked"] for p in perms["ai_permissions"]}["fight_detection"]
    s, _, _ = api.call("PUT", ai_url, {"module_key": "fight_detection", "blocked": not was, "current_password": "Wrong-pass-0000"})
    check(s == 403, "AI feature block with a wrong password: 403")
    s, changed, _ = api.call("PUT", ai_url, {"module_key": "fight_detection", "blocked": not was, "current_password": a.password})
    check(s == 200 and {p["module_key"]: p["blocked"] for p in changed["ai_permissions"]}["fight_detection"] is (not was),
          "PUT /platform/organizations/{id}/ai-permissions (block/unblock one feature)")
    api.call("PUT", ai_url, {"module_key": "fight_detection", "blocked": was, "current_password": a.password})
    s, deleted, _ = api.call("GET", "/platform/deleted-organizations")
    check(s == 200 and set(deleted) == {"organization_names"}, "GET /platform/deleted-organizations")

    # --- Password-confirmed changes ------------------------------------------------------------------------
    m = org["main_admin"]
    url = f"/platform/organizations/{org['organization_id']}/main-admin"
    s, err, _ = api.call("PUT", url, {"full_name": m["full_name"], "email": m["email"], "phone_number": "+923009998877",
                                      "current_password": "Wrong-pass-0000"})
    check(s == 403, "Main Admin edit with a wrong password: 403")
    s, upd, _ = api.call("PUT", url, {"full_name": m["full_name"], "email": m["email"], "phone_number": "+923009998877",
                                      "current_password": a.password})
    check(s == 200 and set(upd) == ORGANIZATION and upd["main_admin"]["phone_number"] == "+923009998877", "Main Admin edit")
    api.call("PUT", url, {"full_name": m["full_name"], "email": m["email"], "phone_number": m["phone_number"] or "+923000000000",
                          "current_password": a.password})
    status_url = f"/platform/organizations/{org['organization_id']}/status"
    s, sus, _ = api.call("POST", status_url, {"status": "SUSPENDED", "reason": "smoke check", "current_password": a.password})
    check(s == 200 and sus["status"] == "SUSPENDED", "suspend")
    s, same, _ = api.call("POST", status_url, {"status": "SUSPENDED", "reason": None, "current_password": a.password})
    check(s == 409 and same.get("code") == "STATUS_UNCHANGED", "same status: 409 STATUS_UNCHANGED")
    s, act, _ = api.call("POST", status_url, {"status": "ACTIVE", "reason": None, "current_password": a.password})
    check(s == 200 and act["status"] == "ACTIVE", "reactivate")

    s, ai, _ = api.call("GET", "/platform/ai-settings")
    check(s == 200 and set(ai) == AI_SETTINGS and all(set(x) == {"key", "enabled", "implemented"} for x in ai["modules"]),
          "GET /platform/ai-settings fields")
    values = {k: ai[k] for k in AI_VALUES}
    s, saved, _ = api.call("PUT", "/platform/ai-settings", {**values, "rag_assistant_enabled": not values["rag_assistant_enabled"],
                                                            "current_password": a.password})
    check(s == 200 and saved["rag_assistant_enabled"] != values["rag_assistant_enabled"], "PUT /platform/ai-settings (all twelve values)")
    api.call("PUT", "/platform/ai-settings", {**values, "current_password": a.password})

    # --- Health and audit log -------------------------------------------------------------------------------
    s, health, _ = api.call("GET", "/platform/health")
    check(s == 200 and set(health) == HEALTH and all(set(x) == SERVICE for x in health["services"])
          and set(health["storage"]) == STORAGE, "GET /platform/health fields")
    check({x["key"]: x["status"] for x in health["services"]}.get("message_broker") == "NOT_DEPLOYED", "planned services NOT_DEPLOYED")
    s, logs, _ = api.call("GET", "/platform/audit-logs", query={"organization": org["organization_id"],
                                                                "action": "ORGANIZATION_STATUS_CHANGED", "outcome": "SUCCESS",
                                                                "limit": 50, "offset": 0})
    check(s == 200 and logs["total"] >= 2 and all(set(e) == AUDIT_ENTRY for e in logs["items"]), "GET /platform/audit-logs with filters")
    s, actions, _ = api.call("GET", "/platform/audit-logs/actions")
    check(s == 200 and all(set(x) == {"action", "count"} for x in actions), "GET /platform/audit-logs/actions")
    s, audit_csv, hdrs = api.call("GET", "/platform/audit-logs/export", query={"organization": org["organization_id"]})
    check(s == 200 and hdrs.get("content-type", "").startswith("text/csv") and "occurred_at,actor_type" in audit_csv and a.password not in audit_csv, "audit export: CSV, no password")

    # --- Emergency recovery, then the original credentials back -------------------------------------------------
    s, rec, _ = api.call("POST", "/platform/auth/login", {"username": a.recovery_username, "password": a.recovery_password})
    check(s == 200 and lm(rec["access_token"]) == "RECOVERY", "recovery sign-in: lm RECOVERY")
    api.token = rec["access_token"]
    temp = "Temporary-pass-2027"
    s, restored, _ = api.call("POST", "/platform/recovery/credentials", {
        "recovery_username": a.recovery_username, "recovery_password": a.recovery_password,
        "new_username": "smoke.restored", "new_password": temp, "confirm_new_password": temp})
    check(s == 200 and set(restored) == TOKEN, "POST /platform/recovery/credentials returns a fresh token")
    s, _, _ = api.call("GET", "/platform/me")
    check(s == 401, "the old token stops working after recovery")
    api.token = restored["access_token"]
    s, t1, _ = api.call("POST", "/platform/me/username", {"current_password": temp, "new_username": a.username})
    check(s == 200 and set(t1) == TOKEN, "POST /platform/me/username rotates the token")
    api.token = t1["access_token"]
    s, t2, _ = api.call("POST", "/platform/me/password", {"current_password": temp, "new_password": a.password,
                                                          "confirm_new_password": a.password})
    check(s == 200 and set(t2) == TOKEN, "POST /platform/me/password rotates the token")
    api.token = t2["access_token"]
    check(api.call("GET", "/platform/me")[1]["username"] == a.username, "original credentials restored")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
