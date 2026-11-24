"""Decision D-027: the read-only AI Investigation & Security Analytics Chatbot (/api/v1/assistant/*, migration 0032).

Uses the ``site`` fixture of the alert tests (Main Campus: Admin a, Supervisor sa, Guard ga at the Lobby of Admin
Block, the campus camera "Gate" in the Lobby; Campus B: Admin b, Supervisor sb, Guard gb). Every figure in an answer is
checked against an independent SQL count over the same period the tool used (``tool_calls[].arguments.period``).
"""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from assistant_engine import AssistantEngine, build_provider
from assistant_engine.synthesizer import duration
from httpx import AsyncClient
from sqlalchemy.engine import make_url

from tests.conftest import CORE_API_DIR, DETECTION_TOKEN, DatabaseUrls, _client_for, platform_auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_alerts import call, new_alert, ok, report_body, site  # noqa: F401
from tests.integration.test_ownership_and_deletion import (  # noqa: F401
    count,
    delete_account,
    headers,
    one,
    ucp,
)

API = "/api/v1"
SERVICE_TOKEN = "test-only-assistant-service-token"


async def ask(client: AsyncClient, user: dict[str, Any], question: str, session: str | None = None) -> dict[str, Any]:
    if session is None:
        session = (await ok(client, user, "POST", "/assistant/sessions", {}))["id"]
    exchange = await ok(client, user, "POST", f"/assistant/sessions/{session}/messages", {"content": question})
    exchange["session_id"] = session
    return exchange


def answer(exchange: dict[str, Any]) -> dict[str, Any]:
    return exchange["assistant_message"]


def period_of(exchange: dict[str, Any], tool: str, key: str = "period") -> tuple[datetime, datetime]:
    call_ = next(t for t in answer(exchange)["tool_calls"] if t["tool"] == tool)
    period = call_["arguments"][key]
    return datetime.fromisoformat(period["since"]), datetime.fromisoformat(period["until"])


def alert_at(owner_execute, alert_id: str, when: datetime, *, close: bool = True) -> None:
    """Moves a created alert to ``when`` (and closes it, so the next detection opens a new alert)."""
    owner_execute(
        "UPDATE alerts SET created_at = %(t)s, detected_at = %(t)s, last_detected_at = %(t)s"
        + (", status = 'DISMISSED', closed_at = %(t)s + interval '1 minute'" if close else "")
        + " WHERE id = %(a)s",
        {"t": when, "a": alert_id},
    )


def month_start(now: datetime, back: int) -> datetime:
    index = now.year * 12 + now.month - 1 - back
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=UTC)


async def seed_alerts(client: AsyncClient, site: dict[str, Any], owner_execute) -> dict[str, list[str]]:
    """Last month: 2 weapon (22:10, 23:40) + 1 fight (22:50). This month so far: 1 weapon + 1 restricted area."""
    now = datetime.now(UTC)
    last = month_start(now, 1) + timedelta(days=9)
    this = max(month_start(now, 0), now - timedelta(hours=3))
    plan = [
        ("WEAPON", last.replace(hour=22, minute=10)),
        ("FIGHT_VIOLENCE", last.replace(hour=22, minute=50)),
        ("WEAPON", last.replace(hour=23, minute=40)),
        ("WEAPON", this),
        ("RESTRICTED_AREA", this),
    ]
    made: dict[str, list[str]] = {"last": [], "this": []}
    for kind, when in plan:
        alert_id = await new_alert(client, site["camera_a"], kind)
        alert_at(owner_execute, alert_id, when, close=when < this)
        made["last" if when < this else "this"].append(alert_id)
    return made


# --- Access ------------------------------------------------------------------------------------------------------


async def test_staff_only_and_status(registration_client: AsyncClient, site: dict[str, Any], super_admin: UUID) -> None:
    for user in (site["a"], site["sa"]):
        status = await ok(registration_client, user, "GET", "/assistant/status")
        assert status["provider"] == "deterministic" and status["available"] and status["read_only"] is True
        assert status["file_uploads"] is False and status["retention_days"] == 30
        assert [c["key"] for c in status["capabilities"]] == [
            "TRENDS",
            "HOTSPOTS",
            "INVESTIGATION",
            "GUARD_PERFORMANCE",
            "SEARCH",
            "FOLLOW_UP",
        ]
    assert (await ok(registration_client, site["sa"], "GET", "/assistant/status"))["role"] == "SUPERVISOR"
    guard = site["ga"]
    platform = platform_auth_header(super_admin)
    for method, path in (
        ("GET", "/assistant/status"),
        ("GET", "/assistant/sessions"),
        ("POST", "/assistant/sessions"),
        ("POST", f"/assistant/incidents/{uuid4()}/investigate"),
    ):
        response = await call(registration_client, guard, method, path, {} if method == "POST" else None)
        assert response.status_code == 403 and response.json()["code"] == "STAFF_ONLY", path
        response = await registration_client.request(method, f"{API}{path}", json={}, headers=platform)
        assert response.status_code == 403 and response.json()["code"] == "SUPER_ADMIN_NOT_ALLOWED", path


# --- 1. Trends ----------------------------------------------------------------------------------------------------


async def test_trends_peaks_and_comparisons_match_the_records(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query
) -> None:
    await seed_alerts(registration_client, site, owner_execute)
    await new_alert(registration_client, site["camera_b"], "FIGHT_VIOLENCE")  # another campus: never counted
    exchange = await ask(
        registration_client, site["sa"], "How many alerts last month and which type was most frequent?"
    )
    reply = answer(exchange)
    assert [t["tool"] for t in reply["tool_calls"]] == ["trend_summary"] and not reply["insufficient_evidence"]
    since, until = period_of(exchange, "trend_summary")
    expected = count(
        owner_query,
        "SELECT 1 FROM alerts WHERE campus_id = %(c)s AND created_at >= %(s)s AND created_at < %(u)s",
        c=site["campus_a"],
        s=since,
        u=until,
    )
    assert expected == 3
    assert "3 alerts (2 weapon, 1 fight / violence; 3 high priority)" in reply["content"]
    assert "The most frequent type was Weapon (2)" in reply["content"]
    assert reply["blocks"][0] == {
        "type": "metrics",
        "items": [
            {"label": "Alerts", "value": 3},
            {"label": "High priority", "value": 3},
            {"label": "Cases opened", "value": 0},
            {"label": "Manual reports", "value": 0},
        ],
    }
    assert all(c["route"].startswith("/alerts/") for c in reply["citations"]) and len(reply["citations"]) == 3

    peaks = answer(await ask(registration_client, site["a"], "What were our peak alert hours last month?"))
    assert [t["tool"] for t in peaks["tool_calls"]] == ["peak_times"]
    assert "the busiest hours were 22:00–23:00 (2), 23:00–00:00 (1)" in peaks["content"]
    assert "The busiest 2-hour window was 22:00–00:00 (3)" in peaks["content"]

    compared = answer(
        await ask(registration_client, site["a"], "Did weapon alerts increase this month compared with last month?")
    )
    assert [t["tool"] for t in compared["tool_calls"]] == ["compare_periods"]
    assert "Weapon alerts: 1 vs 2 (-1, -50%)" in compared["content"]
    assert compared["blocks"][0]["columns"][1] == "this month"


# --- 2. Hotspots, follow-up, camera reliability --------------------------------------------------------------------


async def test_hotspots_follow_up_context_and_camera_reliability(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query
) -> None:
    await seed_alerts(registration_client, site, owner_execute)
    await ok(registration_client, site["ga"], "POST", "/guard/manual-reports", report_body(site))  # North Fence › Gate
    first = await ask(registration_client, site["a"], "Which building had the most alerts this month?")
    reply = answer(first)
    assert [t["tool"] for t in reply["tool_calls"]] == ["location_hotspots"]
    assert "1. Admin Block — 2 (2 alerts, 0 cases, 0 manual reports)" in reply["content"]
    assert reply["citations"][0] == {
        "entity_type": "LOCATION",
        "id": str(site["block"]),
        "label": "Admin Block",
        "route": f"/locations?location={site['block']}",
    }

    second = answer(
        await ask(
            registration_client,
            site["a"],
            "How many of those are still unresolved and who is handling them?",
            first["session_id"],
        )
    )
    [tool] = second["tool_calls"]
    assert tool["tool"] == "case_list" and tool["arguments"]["source"] == "alerts"
    assert tool["arguments"]["location"]["id"] == str(site["block"]) and tool["arguments"]["unresolved_only"]
    open_alerts = count(
        owner_query,
        "SELECT 1 FROM alerts WHERE building_id = %(b)s AND status NOT IN ('RESOLVED', 'DISMISSED')",
        b=site["block"],
    )
    assert open_alerts == 2 and second["content"].startswith("2 unresolved alerts at Admin Block")
    assert "Handled by: Unassigned (2)" in second["content"]

    now = datetime.now(UTC)
    start = max(month_start(now, 0), now - timedelta(hours=5))
    for minutes, state in ((10, "OFFLINE"), (25, "ONLINE"), (40, "OFFLINE"), (50, "ONLINE")):
        owner_execute(
            """INSERT INTO camera_status_events (id, camera_id, organization_id, campus_id, state, changed_at)
               VALUES (%(id)s, %(cam)s, %(org)s, %(campus)s, %(s)s, %(t)s)""",
            {
                "id": uuid4(),
                "cam": site["camera_a"],
                "org": site["org"],
                "campus": site["campus_a"],
                "s": state,
                "t": start + timedelta(minutes=minutes),
            },
        )
    cameras = answer(await ask(registration_client, site["sa"], "Which cameras went offline most often this month?"))
    assert [t["tool"] for t in cameras["tool_calls"]] == ["camera_reliability"]
    assert "Gate — offline 2 times, 25 min 00 s in total" in cameras["content"]
    assert cameras["citations"][0]["route"] == f"/cameras?camera={site['camera_a']}"


# --- 3. Case investigation -----------------------------------------------------------------------------------------


async def test_case_investigation_timeline_related_events_and_gaps(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"], "WEAPON")
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/acknowledge")
    await ok(
        registration_client,
        site["ga"],
        "POST",
        f"/alerts/{alert_id}/assessment",
        {"assessment": "ADDITIONAL_GUARDS_REQUIRED"},
    )
    await ok(registration_client, site["ga"], "POST", f"/alerts/{alert_id}/messages", {"body": "At the lobby now"})
    case = await ok(
        registration_client,
        site["sa"],
        "POST",
        "/incidents",
        {"title": "Weapon at the lobby", "priority": "HIGH", "alert_ids": [alert_id]},
    )
    await ok(registration_client, site["sa"], "POST", f"/incidents/{case['id']}/notes", {"note": "Footage requested."})
    nearby = await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")  # same place, same minutes
    owner_execute(
        """INSERT INTO reid_observations (id, organization_id, campus_id, camera_id, camera_name, building_name,
               track_id, observed_at, embedding, model_version)
           VALUES (%(id)s, %(org)s, %(campus)s, %(cam)s, 'Gate', 'Admin Block', 4, now(), %(v)s, 'osnet-test')""",
        {
            "id": uuid4(),
            "org": site["org"],
            "campus": site["campus_a"],
            "cam": site["camera_a"],
            "v": "[" + ",".join(["0.01"] * 512) + "]",
        },
    )

    exchange = await ok(registration_client, site["sa"], "POST", f"/assistant/incidents/{case['id']}/investigate")
    reply = exchange["assistant_message"]
    assert exchange["session"]["context_incident_id"] == case["id"]
    assert exchange["session"]["context_incident_reference"] == case["reference"]
    assert [t["tool"] for t in reply["tool_calls"]] == ["case_timeline", "related_events", "evidence_gaps"]
    timeline = next(b for b in reply["blocks"] if b["type"] == "timeline")
    labels = [i["label"] for i in timeline["items"]]
    assert "Weapon alert at Gate" in labels and "Acknowledged" in labels
    assert "Assessment: Additional Guards Required" in labels
    assert any(label.startswith("Investigation note") for label in labels)
    assert any(label.startswith("Alert chat: Gul A") for label in labels)
    assert f"{case['reference']} · Weapon at the lobby — open, high priority, handled by nobody yet" in reply["content"]
    assert "1 other alerts, 0 manual reports and 1 Re-ID observations" in reply["content"]
    assert "possible sightings for review, not identities" in reply["content"]
    assert "no recording — Gate" in reply["content"] and "no evidence saved" in reply["content"]
    routes = {c["route"] for c in reply["citations"]}
    assert {f"/incidents/{case['id']}", f"/alerts/{alert_id}", f"/alerts/{nearby}"} <= routes

    # The case session keeps the case for follow-ups; general questions stay general.
    follow = answer(await ask(registration_client, site["sa"], "What happened?", exchange["session"]["id"]))
    assert [t["tool"] for t in follow["tool_calls"]] == ["case_timeline"]
    general = answer(
        await ask(registration_client, site["sa"], "How many alerts this month?", exchange["session"]["id"])
    )
    assert [t["tool"] for t in general["tool_calls"]] == ["trend_summary"]
    again = await ok(registration_client, site["sa"], "POST", f"/assistant/incidents/{case['id']}/investigate")
    assert again["session"]["id"] == exchange["session"]["id"]  # continues the same case chat

    # Evidence linked to the case, found by tag.
    evidence_id = uuid4()
    owner_execute(
        """INSERT INTO evidence_items (id, organization_id, campus_id, kind, title, source, alert_id, camera_id,
               camera_name, covers_from, covers_to, relative_path, media_type, size_bytes, sha256, saved_by_name,
               incident_id, tags)
           VALUES (%(id)s, %(org)s, %(campus)s, 'CLIP', 'Lobby clip', 'ALERT', %(alert)s, %(cam)s, 'Gate', now(),
                   now(), 'evidence/test/clip.mp4', 'video/mp4', 10, %(sha)s, 'Sana A', %(case)s,
                   ARRAY['weapon', 'Gate-Breach'])""",
        {
            "id": evidence_id,
            "org": site["org"],
            "campus": site["campus_a"],
            "alert": alert_id,
            "cam": site["camera_a"],
            "sha": "a" * 64,
            "case": case["id"],
        },
    )
    found = answer(await ask(registration_client, site["a"], "Show evidence tagged gate-breach"))
    assert [t["tool"] for t in found["tool_calls"]] == ["evidence_search"]
    assert found["content"].startswith("1 evidence items tagged gate-breach")
    assert {
        "entity_type": "EVIDENCE",
        "id": str(evidence_id),
        "label": "Evidence · Lobby clip",
        "route": f"/evidence/{evidence_id}",
    } in found["citations"]
    gaps = answer(await ask(registration_client, site["a"], f"Any evidence gaps in {case['reference']}?"))
    assert "no evidence saved" not in gaps["content"] and "1 evidence items are saved" in gaps["content"]


# --- 4. Guard response --------------------------------------------------------------------------------------------


async def test_guard_response_metrics_match_the_records(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query
) -> None:
    first = await new_alert(registration_client, site["camera_a"], "WEAPON")
    await ok(registration_client, site["ga"], "POST", f"/alerts/{first}/acknowledge")
    await ok(
        registration_client,
        site["ga"],
        "POST",
        f"/alerts/{first}/assessment",
        {"assessment": "ADDITIONAL_GUARDS_REQUIRED"},
    )
    owner_execute(
        "UPDATE alert_recipients SET acknowledged_at = created_at + interval '45 seconds' FROM alerts "
        "WHERE alerts.id = alert_recipients.alert_id AND alerts.id = %(a)s",
        {"a": first},
    )
    second = await new_alert(registration_client, site["camera_a"], "FIGHT_VIOLENCE")
    owner_execute(
        """INSERT INTO alert_events (id, alert_id, organization_id, campus_id, actor_role, event_type, details)
           VALUES (%(id)s, %(a)s, %(org)s, %(campus)s, 'SYSTEM', 'AUTO_ESCALATED', '{}')""",
        {"id": uuid4(), "a": second, "org": site["org"], "campus": site["campus_a"]},
    )
    reply = answer(
        await ask(registration_client, site["sa"], "What is our average Guard response and escalation rate?")
    )
    assert [t["tool"] for t in reply["tool_calls"]] == ["guard_response"]
    expected_ack = one(
        owner_query,
        """SELECT avg(EXTRACT(EPOCH FROM (SELECT min(r.acknowledged_at) FROM alert_recipients r WHERE r.alert_id = a.id)
                  - a.created_at)) FROM alerts a WHERE a.campus_id = %(c)s""",
        c=site["campus_a"],
    )
    assert (
        f"the first acknowledgement came after {duration(float(expected_ack))} on average (1 acknowledged)"
        in (reply["content"])
    )
    assert "Guards asked for Additional Guards 1 times; automatic escalation fired 1 times" in reply["content"]
    assert "(after 120 s for high and 300 s for standard priority)" in reply["content"]
    assert (
        "1 alerts (50%) were escalated" in reply["content"] and "1 alerts were never acknowledged" in reply["content"]
    )
    assert "Stationed Guards received" in reply["content"]


# --- 5. Search and missing data -------------------------------------------------------------------------------------


async def test_manual_report_search_and_missing_data(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    for kind in ("Suspicious Person", "Suspicious Person", "Property Damage"):
        await ok(registration_client, site["ga"], "POST", "/guard/manual-reports", report_body(site, report_type=kind))
    reply = answer(
        await ask(registration_client, site["a"], "Summarize manual reports at the Gate over the last 3 months")
    )
    [tool] = reply["tool_calls"]
    assert tool["tool"] == "manual_report_search" and tool["arguments"]["location"]["id"] == str(site["gate"])
    assert count(owner_query, "SELECT 1 FROM manual_reports WHERE area_id = %(a)s", a=site["gate"]) == 3
    assert reply["content"].startswith("3 Guard manual reports at North Fence › Gate in the last 3 months")
    assert "(Suspicious Person 2, Property Damage 1); 0 reviewed and 3 awaiting review" in reply["content"]
    assert len([c for c in reply["citations"] if c["entity_type"] == "MANUAL_REPORT"]) == 3

    missing = answer(await ask(registration_client, site["a"], "How many alerts in 2019?"))
    assert missing["insufficient_evidence"] is True
    assert missing["content"] == "No alerts, cases or manual reports were found in 2019."
    nothing = answer(await ask(registration_client, site["a"], "Show evidence tagged firearm"))
    assert nothing["insufficient_evidence"] and nothing["content"] == "No evidence items tagged firearm were found."


# --- Guardrails, privacy, audit -------------------------------------------------------------------------------------


async def test_read_only_and_no_accusations(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    alert_id = await new_alert(registration_client, site["camera_a"], "WEAPON")
    case = await ok(registration_client, site["a"], "POST", "/incidents", {"title": "Lobby case", "priority": "HIGH"})
    before = owner_query("SELECT status, updated_at FROM alerts WHERE id = %(a)s", {"a": alert_id})
    for question, kind in (
        (f"Resolve the alert and close {case['reference']}", "read-only"),
        ("Upload our SOP PDF for weapons", "file uploads"),
    ):
        reply = answer(await ask(registration_client, site["a"], question))
        assert reply["tool_calls"] == [] and kind in reply["content"]
    assert owner_query("SELECT status, updated_at FROM alerts WHERE id = %(a)s", {"a": alert_id}) == before
    assert one(owner_query, "SELECT status FROM incidents WHERE id = %(i)s", i=case["id"]) == "OPEN"
    accused = answer(await ask(registration_client, site["sa"], f"Who is guilty in {case['reference']}?"))
    assert accused["content"].startswith("I can't say who is responsible or confirm anyone's identity.")
    assert [t["tool"] for t in accused["tool_calls"]] == ["case_timeline"]


async def test_supervisors_never_get_admin_only_data_and_everything_is_audited(
    registration_client: AsyncClient, site: dict[str, Any], owner_execute, owner_query
) -> None:
    for actor, role, action in (
        (site["a"], "ADMIN", "AUTH_LOGIN_SUCCEEDED"),
        (site["a"], "ADMIN", "STAFF_CREATED"),
        (site["sa"], "SUPERVISOR", "ALERT_RESOLVED"),
    ):
        owner_execute(
            """INSERT INTO organization_audit_logs (id, organization_id, campus_id, actor_user_id, actor_role, action,
                   target_type, metadata) VALUES (%(id)s, %(org)s, %(campus)s, %(actor)s, %(role)s, %(action)s,
                   'USER', '{}')""",
            {
                "id": uuid4(),
                "org": site["org"],
                "campus": site["campus_a"],
                "actor": actor["id"],
                "role": role,
                "action": action,
            },
        )
    admin = answer(await ask(registration_client, site["a"], "Show the audit activity this month"))
    assert "authentication 1" in admin["content"] and "user management" in admin["content"]
    supervisor = answer(await ask(registration_client, site["sa"], "Show the audit activity this month"))
    assert "(operational records and your own only)" in supervisor["content"]
    assert "authentication" not in supervisor["content"] and "user management" not in supervisor["content"]
    assert "alerts 1" in supervisor["content"]

    refused = answer(
        await ask(registration_client, site["sa"], "List the personnel records with restricted-area access")
    )
    assert refused["tool_calls"] == [] and "not available to Supervisors" in refused["content"]
    denied = owner_query(
        """SELECT outcome, category, metadata FROM organization_audit_logs WHERE actor_user_id = %(u)s
              AND action = 'ASSISTANT_QUERY' ORDER BY occurred_at DESC LIMIT 1""",
        {"u": site["sa"]["id"]},
    )[0]
    assert denied[0] == "DENIED" and denied[1] == "AI_ASSISTANT" and denied[2]["refusal"] == "EXCLUDED"

    await ask(
        registration_client, site["a"], "How many alerts this month? reply to maryam@ucp.example.com +923001234567"
    )
    query = owner_query(
        """SELECT metadata FROM organization_audit_logs WHERE actor_user_id = %(u)s AND action = 'ASSISTANT_QUERY'
            ORDER BY occurred_at DESC LIMIT 1""",
        {"u": site["a"]["id"]},
    )[0][0]
    assert query["tools"] == ["trend_summary"] and query["provider"] == "deterministic"
    assert "ucp.example.com" not in json.dumps(query) and "3001234567" not in json.dumps(query)
    tool_rows = owner_query(
        "SELECT metadata FROM organization_audit_logs WHERE actor_user_id = %(u)s AND action = 'ASSISTANT_TOOL_CALL'",
        {"u": site["a"]["id"]},
    )
    assert {r[0]["tool"] for r in tool_rows} == {"audit_activity", "trend_summary"}
    assert all(set(r[0]) == {"tool", "arguments", "outcome", "rows", "latency_ms"} for r in tool_rows)
    # The Supervisor's audit page never shows the Admin's chatbot questions; the Admin's shows everyone's.
    sup_page = await ok(registration_client, site["sa"], "GET", "/audit-logs?category=AI_ASSISTANT&limit=100")
    admin_page = await ok(registration_client, site["a"], "GET", "/audit-logs?category=AI_ASSISTANT&limit=100")
    items = lambda page: page["items"] if isinstance(page, dict) else page  # noqa: E731
    assert {i["actor_user_id"] for i in items(sup_page)} == {str(site["sa"]["id"])}
    assert {i["actor_user_id"] for i in items(admin_page)} == {str(site["sa"]["id"]), str(site["a"]["id"])}


async def test_chats_are_private_and_can_be_deleted(
    registration_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    exchange = await ask(registration_client, site["a"], "How many alerts this month?")
    session = exchange["session_id"]
    detail = await ok(registration_client, site["a"], "GET", f"/assistant/sessions/{session}")
    assert detail["title"] == "How many alerts this month?" and [m["role"] for m in detail["messages"]] == [
        "USER",
        "ASSISTANT",
    ]
    for other in (site["sa"], site["b"]):
        response = await call(registration_client, other, "GET", f"/assistant/sessions/{session}")
        assert response.status_code == 404 and response.json()["code"] == "ASSISTANT_SESSION_NOT_FOUND"
        assert await ok(registration_client, other, "GET", "/assistant/sessions") == []
        sent = await call(
            registration_client, other, "POST", f"/assistant/sessions/{session}/messages", {"content": "x"}
        )
        assert sent.status_code == 404
    other_campus = answer(await ask(registration_client, site["b"], "How many alerts this month?"))
    assert other_campus["insufficient_evidence"]  # Campus B has none; Campus A's never leak
    blank = await call(
        registration_client, site["a"], "POST", f"/assistant/sessions/{session}/messages", {"content": "   "}
    )
    assert blank.status_code == 422
    assert (await call(registration_client, site["a"], "DELETE", f"/assistant/sessions/{session}")).status_code == 204
    assert (await call(registration_client, site["a"], "GET", f"/assistant/sessions/{session}")).status_code == 404
    assert count(owner_query, "SELECT 1 FROM ai_chat_messages WHERE session_id = %(s)s", s=session) == 0
    assert "ASSISTANT_SESSION_DELETED" in [
        r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE target_id = %(s)s", {"s": session})
    ]


async def test_retention_purge_and_erasure(
    seed: Any,
    outbox: FakeEmailSender,
    migrated_database: DatabaseUrls,
    storage_root: Any,
    ucp: dict[str, Any],
    owner_execute,
    owner_query,
    registration_client: AsyncClient,
) -> None:
    async with _client_for(
        migrated_database,
        email_sender=outbox,
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
        internal_service_token=SERVICE_TOKEN,
    ) as client:
        old = (await ok(client, ucp["a"], "POST", "/assistant/sessions", {}))["id"]
        recent = (await ok(client, ucp["a"], "POST", "/assistant/sessions", {}))["id"]
        owner_execute(
            "UPDATE ai_chat_sessions SET last_active_at = now() - interval '31 days' WHERE id = %(s)s", {"s": old}
        )
        refused = await client.post(f"{API}/internal/assistant/purge-expired")
        assert refused.status_code in (401, 403)
        purged = await client.post(
            f"{API}/internal/assistant/purge-expired", headers={"X-Service-Token": SERVICE_TOKEN}
        )
        assert purged.json() == {"deleted": 1, "retention_days": 30}
        assert [r[0] for r in owner_query("SELECT id FROM ai_chat_sessions")] == [UUID(recent)]

        supervisor_chat = (await ok(client, ucp["sa"], "POST", "/assistant/sessions", {}))["id"]
        owner_execute("UPDATE users SET status = 'LEFT', deleted_at = now() WHERE id = %(u)s", {"u": ucp["sa"]["id"]})
        assert count(owner_query, "SELECT 1 FROM ai_chat_sessions WHERE id = %(s)s", s=supervisor_chat) == 0

        b_chat = await ask(client, ucp["b"], "How many alerts this month?")
        assert count(owner_query, "SELECT 1 FROM ai_chat_messages WHERE campus_id = %(c)s", c=ucp["campus_b"]) == 2
        await delete_account(client, outbox, ucp["b"])  # Campus B is erased with its last Admin
        assert count(owner_query, "SELECT 1 FROM ai_chat_sessions WHERE id = %(s)s", s=b_chat["session_id"]) == 0
        assert count(owner_query, "SELECT 1 FROM ai_chat_messages WHERE campus_id = %(c)s", c=ucp["campus_b"]) == 0
        assert count(owner_query, "SELECT 1 FROM ai_chat_sessions WHERE id = %(s)s", s=recent) == 1


async def test_database_isolation_of_chats(
    registration_client: AsyncClient, site: dict[str, Any], app_role_engine: Any, platform_role_engine: Any
) -> None:
    from sqlalchemy import text

    from tests.conftest import run_as

    session = (await ask(registration_client, site["a"], "How many alerts this month?"))["session_id"]
    for user in (site["sa"], site["ga"], site["b"], None):
        rows, _ = await run_as(app_role_engine, user["id"] if user else None, "SELECT id FROM ai_chat_sessions")
        assert rows == [], user
        rows, _ = await run_as(app_role_engine, user["id"] if user else None, "SELECT id FROM ai_chat_messages")
        assert rows == [], user
    rows, _ = await run_as(app_role_engine, site["a"]["id"], "SELECT id FROM ai_chat_messages")
    assert len(rows) == 2
    with pytest.raises(Exception):  # noqa: B017 - a Supervisor cannot write into the Admin's chat
        await run_as(
            app_role_engine,
            site["sa"]["id"],
            "INSERT INTO ai_chat_messages (id, session_id, organization_id, campus_id, role, content) "
            f"VALUES ('{uuid4()}', '{session}', '{site['org']}', '{site['campus_a']}', 'USER', 'x')",
        )
    async with platform_role_engine.connect() as conn:
        with pytest.raises(Exception):  # noqa: B017 - the platform role has no privilege at all
            await conn.execute(text("SELECT 1 FROM ai_chat_sessions"))


# --- A configured model (mocked Ollama) -----------------------------------------------------------------------------


async def test_a_local_ollama_model_is_used_and_an_unreachable_one_reported(
    seed: Any, outbox: FakeEmailSender, migrated_database: DatabaseUrls, storage_root: Any, ucp: dict[str, Any]
) -> None:
    def ollama(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        body = json.loads(request.content)
        if body.get("format") == "json":
            plan = {"tool_calls": [{"name": "trend_summary", "arguments": {"period": "this month"}}]}
            return httpx.Response(200, json={"message": {"content": json.dumps(plan)}})
        return httpx.Response(200, json={"message": {"content": "No alerts so far this month."}})

    engine = AssistantEngine(build_provider("ollama", transport=httpx.MockTransport(ollama)))
    async with _client_for(
        migrated_database, email_sender=outbox, cctv_storage_root=storage_root, assistant_engine=engine
    ) as client:
        status = await ok(client, ucp["a"], "GET", "/assistant/status")
        assert status["provider"] == "ollama" and status["model_name"] == "qwen2.5:7b" and status["available"]
        reply = answer(await ask(client, ucp["a"], "Anything this month?"))
        assert reply["provider"] == "ollama" and reply["model_name"] == "qwen2.5:7b"
        assert [t["tool"] for t in reply["tool_calls"]] == ["trend_summary"] and reply["insufficient_evidence"]

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    broken = AssistantEngine(build_provider("ollama", transport=httpx.MockTransport(down)))
    async with _client_for(
        migrated_database, email_sender=outbox, cctv_storage_root=storage_root, assistant_engine=broken
    ) as client:
        status = await ok(client, ucp["a"], "GET", "/assistant/status")
        assert not status["available"] and "unreachable" in status["detail"]
        reply = answer(await ask(client, ucp["a"], "How many alerts this month?"))
        assert reply["blocks"][0]["type"] == "notice" and "could not be used" in reply["blocks"][0]["text"]
        assert reply["content"] == "No alerts, cases or manual reports were found in this month."


# --- Migration 0032 -------------------------------------------------------------------------------------------------

SCRATCH = "cctv_migration_0032_roundtrip"


@pytest.fixture
def scratch(database_urls: DatabaseUrls) -> Iterator[tuple[Config, str]]:
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{SCRATCH}"')
    owner = make_url(database_urls.owner_server).set(database=SCRATCH)
    config = Config(str(CORE_API_DIR / "alembic.ini"))
    url = owner.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("APP_DB_USER", database_urls.app_user)
        mp.setenv("PLATFORM_DB_USER", database_urls.platform_user)
        yield config, owner.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(database_urls.owner_server, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


def _state(dsn: str) -> tuple[set[str], str]:
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'ai_chat%'")}
        category = conn.execute("SELECT app_audit_category('ASSISTANT_QUERY', 'ADMIN')").fetchone()
    return tables, category[0] if category else ""


def test_migration_0032_round_trips(scratch: tuple[Config, str]) -> None:
    config, dsn = scratch
    command.upgrade(config, "0032")
    assert _state(dsn) == ({"ai_chat_sessions", "ai_chat_messages"}, "AI_ASSISTANT")
    command.downgrade(config, "0031")
    assert _state(dsn) == (set(), "OTHER")
    command.upgrade(config, "head")
    assert _state(dsn) == ({"ai_chat_sessions", "ai_chat_messages"}, "AI_ASSISTANT")
