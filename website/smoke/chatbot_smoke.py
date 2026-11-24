"""Live smoke check: the read-only AI Investigation & Security Analytics Chatbot (decision D-027), end to end.

Through the Vite dev server's /api proxy, against a real Core API on a scratch database: real sign-ins (codes from the
development outbox), alerts from the real detection intake, Guard actions and manual reports as the Guard app sends
them, a case opened from an alert — then the chatbot's answers are checked against **independent REST counts** of the
same records. It also checks follow-up context, the case investigation, missing data, read-only refusals, the
Supervisor's exclusions, chat privacy, campus isolation, Guard and Super Admin refusals, audit records and the
retention purge route.

ONLY run it against a server that uses an isolated scratch database and EMAIL_DELIVERY_MODE=dev_outbox (see
smoke/README.md). The API runs with AI_LLM_PROVIDER=deterministic (no model, no network).

    python3 smoke/chatbot_smoke.py --web http://127.0.0.1:5181 --outbox /path/to/outbox --token <ingest token> \
        --service-token <INTERNAL_SERVICE_TOKEN> [--super-admin <username> --super-admin-password <password>]

Standard library only. Exit code 0 when every check passes. The data is left in place (screenshots use it).
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from operations_smoke import Org, detect
from personnel_smoke import PASSWORD, Api, check, results
from realtime_comms_smoke import second_campus


def ask(api: Api, token: str, question: str, session: str | None = None) -> tuple[str, dict[str, Any]]:
    if session is None:
        _, created = api.call("POST", "/assistant/sessions", {}, token)
        session = created["id"]
    status, exchange = api.call("POST", f"/assistant/sessions/{session}/messages", {"content": question}, token)
    assert status == 201, (status, exchange)
    return session, exchange["assistant_message"]


def tools(answer: dict[str, Any]) -> list[str]:
    return [t["tool"] for t in answer["tool_calls"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--token", required=True, help="the API's DETECTION_INGEST_TOKEN (scratch value)")
    parser.add_argument("--service-token", help="the API's INTERNAL_SERVICE_TOKEN, to check the purge route")
    parser.add_argument("--super-admin")
    parser.add_argument("--super-admin-password")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir():
        print("Refusing: the development outbox directory does not exist (EMAIL_DELIVERY_MODE=dev_outbox).")
        return 2
    api = Api(args.web)

    # --- People, places, records -------------------------------------------------------------------------------
    org = Org(api, args.outbox, "Chatbot Smoke")
    sup_email = f"sup.{org.run}@smoke.example.com"
    org.create("/organization-admin/supervisors", org.admin, org.admin_email, "Sana Malik", sup_email)
    sup = org.sign_in(sup_email)
    g1_email, g2_email = f"g1.{org.run}@smoke.example.com", f"g2.{org.run}@smoke.example.com"
    g1 = org.create("/guards", org.admin, org.admin_email, "Bilal Ahmed", g1_email)
    g2 = org.create("/guards", org.admin, org.admin_email, "Hamza Iqbal", g2_email)
    _, block = api.call("POST", "/locations/buildings", {"name": "Admin Block", "kind": "INDOOR"}, org.admin)
    _, ground = api.call("POST", f"/locations/buildings/{block['id']}/floors", {"name": "Ground", "level": 0}, org.admin)
    _, gate = api.call("POST", f"/locations/buildings/{block['id']}/areas", {"name": "Main Gate", "floor_id": ground["id"]}, org.admin)
    where = {"building_id": block["id"], "floor_id": ground["id"], "area_id": gate["id"]}
    api.call("PUT", f"/locations/guard-assignments/{g1['guard_id']}", where, sup)
    guard1 = org.guard_sign_in(g1["guard_id"], g1_email, "STATIONED")
    guard2 = org.guard_sign_in(g2["guard_id"], g2_email, "STANDBY")
    _, cam = api.call("POST", "/cameras", {"display_name": "Gate Camera", "connection_type": "RTSP", "location": where}, sup)
    weapon = detect(args.web, args.token, cam["id"], "WEAPON")["alert_id"]
    detect(args.web, args.token, cam["id"], "FIGHT_VIOLENCE")
    detect(args.web, args.token, cam["id"], "RESTRICTED_AREA")
    cid = lambda: str(uuid.uuid4())  # noqa: E731
    api.call("POST", f"/alerts/{weapon}/acknowledge", {"client_action_id": cid()}, guard1)
    api.call("POST", f"/alerts/{weapon}/assessment", {"assessment": "ADDITIONAL_GUARDS_REQUIRED"}, guard1)
    api.call("POST", f"/alerts/{weapon}/messages", {"body": "At the gate, keeping distance.", "client_message_id": cid()}, guard1)
    for kind in ("Suspicious Person", "Unattended Bag"):
        api.call("POST", "/guard/manual-reports", {"client_report_id": cid(), "report_type": kind,
                  "description": "Near the main gate.", "building_id": block["id"], "floor_id": ground["id"],
                  "area_id": gate["id"]}, guard2)  # fmt: skip
    _, case = api.call("POST", "/incidents", {"title": "Weapon at the main gate", "priority": "HIGH", "alert_ids": [weapon]}, sup)
    api.call("POST", f"/incidents/{case['id']}/notes", {"note": "Gate Camera footage requested."}, sup)
    north = second_campus(api, org, args.outbox)
    check(bool(sup and guard1 and guard2 and north and case.get("reference")), "setup: staff, Guards, records, a case, a second campus")

    # --- Access ------------------------------------------------------------------------------------------------------
    for name, token in (("Admin", org.admin), ("Supervisor", sup)):
        s, st = api.call("GET", "/assistant/status", token=token)
        check(s == 200 and st["provider"] == "deterministic" and st["available"] and st["read_only"] and not st["file_uploads"],
              f"{name}: status 200, deterministic provider, read-only, no uploads")  # fmt: skip
    s, body = api.call("GET", "/assistant/status", token=guard1)
    check(s == 403 and body.get("code") == "STAFF_ONLY", "a Guard is refused (403 STAFF_ONLY)")
    if args.super_admin and args.super_admin_password:
        _, platform = api.call("POST", "/platform/auth/login", {"username": args.super_admin, "password": args.super_admin_password})
        s, body = api.call("POST", "/assistant/sessions", {}, platform["access_token"])
        check(s == 403 and body.get("code") == "SUPER_ADMIN_NOT_ALLOWED", "the Super Admin is refused (403 SUPER_ADMIN_NOT_ALLOWED)")
    else:
        print("SKIP  Super Admin check (no --super-admin given)")

    # --- 1. Trends, against an independent REST count ----------------------------------------------------------------
    _, alerts = api.call("GET", "/alerts?limit=200&offset=0", token=org.admin)
    session, answer = ask(api, org.admin, "How many alerts this month and which type was most frequent?")
    check(tools(answer) == ["trend_summary"] and f"{len(alerts)} alerts" in answer["content"] and len(alerts) == 3,
          f"trend: '{len(alerts)} alerts' matches GET /alerts ({len(alerts)})")  # fmt: skip
    check("2 Guard manual reports" in answer["content"] and "1 cases opened" in answer["content"],
          "trend: cases and manual reports match the records")  # fmt: skip
    check(all(c["route"].startswith(("/alerts/", "/incidents/")) for c in answer["citations"]) and answer["citations"],
          "trend: clickable references to the alerts and the case")  # fmt: skip

    # --- 2 + 6. Hotspots and a follow-up ------------------------------------------------------------------------------
    session, answer = ask(api, org.admin, "Which building had the most alerts this month?")
    check(tools(answer) == ["location_hotspots"] and "1. Admin Block — 3 (3 alerts" in answer["content"],
          "hotspots: Admin Block first with its 3 alerts")  # fmt: skip
    _, follow = ask(api, org.admin, "How many of those are still unresolved and who is handling them?", session)
    open_alerts = [a for a in alerts if a["status"] not in ("RESOLVED", "DISMISSED")]
    check(tools(follow) == ["case_list"] and follow["content"].startswith(f"{len(open_alerts)} unresolved alerts at Admin Block"),
          f"follow-up keeps the place and period: {len(open_alerts)} unresolved alerts (REST)")  # fmt: skip

    # --- 4. Guard response -------------------------------------------------------------------------------------------
    _, guards = ask(api, sup, "What is our average Guard response and escalation rate this month?")
    check(tools(guards) == ["guard_response"] and "Guards asked for Additional Guards 1 times" in guards["content"]
          and "(1 acknowledged)" in guards["content"], "guard response: 1 acknowledgement and 1 Additional Guards request")  # fmt: skip

    # --- 5. Manual reports -------------------------------------------------------------------------------------------
    _, reports = api.call("GET", "/manual-reports?limit=26&offset=0", token=sup)
    _, summary = ask(api, sup, "Summarize manual reports at the Main Gate this month")
    check(tools(summary) == ["manual_report_search"] and summary["content"].startswith(f"{len(reports)} Guard manual reports"),
          f"manual reports: {len(reports)} at the Main Gate (REST)")  # fmt: skip

    # --- 3. Case investigation ---------------------------------------------------------------------------------------
    s, investigated = api.call("POST", f"/assistant/incidents/{case['id']}/investigate", token=sup)
    inv = investigated["assistant_message"]
    labels = [i["label"] for b in inv["blocks"] if b["type"] == "timeline" for i in b["items"]]
    check(s == 201 and tools(inv) == ["case_timeline", "related_events", "evidence_gaps"]
          and investigated["session"]["context_incident_reference"] == case["reference"],
          "Investigate with AI: timeline, related events and gaps in a case chat")  # fmt: skip
    check("Weapon alert at Gate Camera" in labels and "Acknowledged" in labels
          and "Assessment: Additional Guards Required" in labels and any(l.startswith("Alert chat") for l in labels),
          "the timeline holds the detection, the Guard's actions and the chat")  # fmt: skip
    check("2 other alerts" in inv["content"] and "no evidence saved" in inv["content"],
          "related events (the other 2 alerts nearby) and the missing-evidence gap")  # fmt: skip

    # --- Missing data, guardrails, exclusions ------------------------------------------------------------------------
    _, missing = ask(api, org.admin, "How many alerts in 2019?")
    check(missing["insufficient_evidence"] and missing["content"] == "No alerts, cases or manual reports were found in 2019.",
          "missing data is said explicitly (insufficient_evidence)")  # fmt: skip
    before = api.call("GET", f"/alerts/{weapon}", token=org.admin)[1]["status"]
    _, refused = ask(api, org.admin, "Resolve the weapon alert now")
    after = api.call("GET", f"/alerts/{weapon}", token=org.admin)[1]["status"]
    check(refused["tool_calls"] == [] and "read-only" in refused["content"] and before == after,
          "a state-changing request is refused and nothing changes")  # fmt: skip
    _, accused = ask(api, sup, f"Who is guilty in {case['reference']}?")
    check(accused["content"].startswith("I can't say who is responsible"), "no accusation or identity claim")
    _, excluded = ask(api, sup, "Show the personnel records with restricted-area access")
    check(excluded["tool_calls"] == [] and "not available to Supervisors" in excluded["content"],
          "Supervisor: personnel and restricted-area access are excluded")  # fmt: skip

    # --- Privacy, isolation, audit, deletion, purge ------------------------------------------------------------------
    check(api.call("GET", f"/assistant/sessions/{session}", token=sup)[0] == 404, "a Supervisor cannot open the Admin's chat")
    check(api.call("GET", f"/assistant/sessions/{session}", token=north)[0] == 404, "another campus cannot open it")
    _, north_answer = ask(api, north, "How many alerts this month and which type was most frequent?")
    check(north_answer["insufficient_evidence"], "the other campus's chatbot finds none of this campus's records")
    _, admin_audit = api.call("GET", "/audit-logs?category=AI_ASSISTANT&limit=100", token=org.admin)
    _, sup_audit = api.call("GET", "/audit-logs?category=AI_ASSISTANT&limit=100", token=sup)
    items = lambda page: page["items"] if isinstance(page, dict) else page  # noqa: E731
    admin_actions = {i["action"] for i in items(admin_audit)}
    sup_actors = {i["actor_role"] for i in items(sup_audit)}
    check({"ASSISTANT_QUERY", "ASSISTANT_TOOL_CALL"} <= admin_actions, "queries and tool calls are audited (AI_ASSISTANT)")
    check(sup_actors == {"SUPERVISOR"}, "a Supervisor's audit page shows only their own chatbot records")
    check("password" not in json.dumps(items(admin_audit)).lower(), "no secret in the chatbot audit records")
    s, _ = api.call("DELETE", f"/assistant/sessions/{session}", token=org.admin)
    check(s == 204 and api.call("GET", f"/assistant/sessions/{session}", token=org.admin)[0] == 404, "delete a chat")
    if args.service_token:
        import urllib.request

        request = urllib.request.Request(args.web.rstrip("/") + "/api/v1/internal/assistant/purge-expired", data=b"",
                                         method="POST", headers={"X-Service-Token": args.service_token})  # fmt: skip
        with urllib.request.urlopen(request) as response:
            purged = json.loads(response.read())
        check(purged["retention_days"] == 30 and purged["deleted"] >= 0, "retention purge route answers (30 days)")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(json.dumps({"organization_name": org.name, "organization_id": org.org_id, "admin_email": org.admin_email,
                      "supervisor_email": sup_email, "password": PASSWORD, "case": case["id"]}))  # fmt: skip
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
