"""Platform Super Admin management (decision D-019; migration 0020): overview, organizations, Main Admin
contact, organization status, AI settings, health, audit log and exports, and the isolation rules."""

import csv
import io
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import (
    SUPER_ADMIN_PASSWORD,
    auth_header,
    platform_auth_header,
    run_as,
)
from tests.seed import Seed

P = "/api/v1/platform"
WRONG = "Not-the-password-1"


def code_of(owner_query, name: str) -> str:
    return owner_query("SELECT organization_code FROM organizations WHERE name = %(n)s", {"n": name})[0][0]


def audit_rows(owner_query, action: str, code: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    rows = owner_query(
        "SELECT outcome, metadata FROM platform_audit_logs WHERE action = %(a)s "
        "AND (%(c)s::text IS NULL OR metadata ->> 'organization_code' = %(c)s) ORDER BY occurred_at",
        {"a": action, "c": code},
    )
    return [(o, m if isinstance(m, dict) else json.loads(m)) for o, m in rows]


@pytest.fixture
def sa(super_admin: UUID) -> dict[str, str]:
    return platform_auth_header(super_admin)


# --- Boundaries ----------------------------------------------------------------------------------------


async def test_only_platform_tokens_reach_the_management_routes(
    registration_client: AsyncClient, seed: Seed, sa: dict[str, str], super_admin: UUID
) -> None:
    for path in ("/overview", "/organizations", "/ai-settings", "/health", "/audit-logs", "/audit-logs/actions"):
        assert (await registration_client.get(P + path)).status_code == 401
        assert (await registration_client.get(P + path, headers=auth_header(seed.first_admin))).status_code == 401
        assert (await registration_client.get(P + path, headers=sa)).status_code == 200, path
    # And a platform token opens no tenant route (403 since decision D-024: identified, not allowed).
    assert (await registration_client.get("/api/v1/cameras", headers=sa)).status_code == 403


async def test_the_platform_role_reads_no_tenant_table_and_the_app_role_no_platform_function(
    registration_client: AsyncClient, platform_role_engine, app_role_engine, seed: Seed
) -> None:
    for table in (
        "organizations", "campuses", "users", "cameras", "alerts", "alert_messages", "manual_reports",
        "personnel", "personnel_area_access", "user_credentials", "guard_location_assignments",
    ):  # fmt: skip
        with pytest.raises(DBAPIError, match="permission denied"):
            async with platform_role_engine.connect() as conn:
                await conn.execute(text(f"SELECT * FROM {table} LIMIT 1"))
    for function in ("platform_overview()", "platform_organization_detail('ORG-00000000000')"):
        with pytest.raises(DBAPIError, match="permission denied"):
            await run_as(app_role_engine, seed.first_admin, f"SELECT * FROM {function}")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, seed.first_admin, "SELECT * FROM platform_ai_settings")


# --- Overview ---------------------------------------------------------------------------------------------


async def test_overview_has_counts_health_ai_modules_and_recent_activity(
    registration_client: AsyncClient, sa: dict[str, str]
) -> None:
    body = (await registration_client.get(f"{P}/overview", headers=sa)).json()
    counts = body["counts"]
    assert (counts["organizations_total"], counts["organizations_active"], counts["organizations_suspended"]) == (
        2,
        2,
        0,
    )
    assert (counts["campuses_total"], counts["cameras_total"]) == (3, 3)
    assert set(body["health"]) == {"overall_status", "summary"}
    assert [m["key"] for m in body["ai_modules"]][0] == "human_detection_tracking_enabled"
    assert body["ai_modules"][0]["implemented"] is True and body["ai_modules"][1]["implemented"] is False
    assert len(body["recent_activity"]) <= 10
    assert "ORGANIZATION_REGISTERED" in {e["action"] for e in body["recent_activity"]}
    assert "rtsp" not in json.dumps(body).lower()


# --- Organizations ----------------------------------------------------------------------------------------


async def test_organization_list_search_filter_sort_and_paging(
    registration_client: AsyncClient, sa: dict[str, str], owner_execute, owner_query
) -> None:
    async def names(**params: Any) -> tuple[list[str], int]:
        r = await registration_client.get(f"{P}/organizations", params=params, headers=sa)
        assert r.status_code == 200, r.text
        return [o["name"] for o in r.json()["items"]], r.json()["total"]

    assert await names(q="river") == (["Riverside University"], 1)
    harbor = code_of(owner_query, "Harbor Logistics")
    assert await names(q=harbor.lower()) == (["Harbor Logistics"], 1)
    assert await names(sort="camera_count", order="desc") == (["Riverside University", "Harbor Logistics"], 2)
    assert await names(sort="name", order="asc") == (["Harbor Logistics", "Riverside University"], 2)
    assert await names(sort="name", order="asc", limit=1, offset=1) == (["Riverside University"], 2)
    assert await names(sort="name", limit=1, offset=5) == ([], 2)
    owner_execute("UPDATE organizations SET status = 'SUSPENDED' WHERE organization_code = %(c)s", {"c": harbor})
    assert await names(status="SUSPENDED") == (["Harbor Logistics"], 1)
    first = (await registration_client.get(f"{P}/organizations", params={"q": "river"}, headers=sa)).json()["items"][0]
    assert set(first) == {
        "organization_id", "name", "official_email", "official_phone_number", "status", "created_at", "main_admin",
        "campus_count", "camera_count",
    }  # fmt: skip
    assert (first["campus_count"], first["camera_count"], first["status"]) == (2, 2, "ACTIVE")
    assert first["main_admin"]["full_name"] and "password" not in json.dumps(first).lower()
    bad = await registration_client.get(f"{P}/organizations", params={"sort": "password"}, headers=sa)
    assert bad.status_code == 422
    assert (await registration_client.get(f"{P}/organizations", params={"limit": 101}, headers=sa)).status_code == 422


async def test_organization_details_with_campus_breakdown(
    registration_client: AsyncClient, sa: dict[str, str], owner_query, owner_execute, seed: Seed
) -> None:
    code = code_of(owner_query, "Riverside University")
    owner_execute(
        "INSERT INTO buildings (id, organization_id, campus_id, kind, name) "
        "VALUES (%(i)s, %(o)s, %(c)s, 'INDOOR', 'Lab')",
        {"i": uuid4(), "o": seed.org1, "c": seed.campus_a},
    )
    r = await registration_client.get(f"{P}/organizations/{code}", headers=sa)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["organization_id"] == code and body["registered_address"] is None
    campuses = {c["name"]: c for c in body["campuses"]}
    assert set(campuses) == {"Main Campus", "City Campus"}
    assert (campuses["Main Campus"]["building_count"], campuses["Main Campus"]["camera_count"]) == (1, 1)
    assert campuses["City Campus"]["camera_count"] == 1
    assert "display_name" not in json.dumps(body) and "stream" not in json.dumps(body)
    missing = await registration_client.get(f"{P}/organizations/ORG-00000000000", headers=sa)
    assert missing.status_code == 404 and missing.json()["code"] == "ORGANIZATION_NOT_FOUND"


async def test_export_organizations_is_csv_safe_and_audited(
    registration_client: AsyncClient, sa: dict[str, str], owner_query, owner_execute
) -> None:
    code = code_of(owner_query, "Harbor Logistics")
    owner_execute("UPDATE organizations SET name = '=HYPERLINK(1)' WHERE organization_code = %(c)s", {"c": code})
    r = await registration_client.get(f"{P}/organizations/export", params={"sort": "name", "order": "asc"}, headers=sa)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0][:3] == ["organization_id", "name", "official_email"] and len(rows) == 3
    assert "'=HYPERLINK(1)" in [row[1] for row in rows[1:]]
    assert audit_rows(owner_query, "ORGANIZATIONS_EXPORTED")[-1][1]["rows"] == 2


# --- Main Admin contact -----------------------------------------------------------------------------------


async def test_updating_the_main_admin_needs_the_current_password(
    registration_client: AsyncClient, sa: dict[str, str], owner_query, owner_execute, seed: Seed
) -> None:
    code = code_of(owner_query, "Riverside University")
    url = f"{P}/organizations/{code}/main-admin"
    body = {"full_name": "Hina Malik Khan", "email": "hina.new@riverside.example.com", "phone_number": "+923001112222"}
    wrong = await registration_client.put(url, json={**body, "current_password": WRONG}, headers=sa)
    assert wrong.status_code == 403
    assert audit_rows(owner_query, "ORGANIZATION_ADMIN_UPDATED")[-1][0] == "FAILURE"
    assert owner_query("SELECT failed_login_attempts FROM platform_admins")[0][0] == 1
    taken_email = "owner@harbor.example.com"
    owner_execute("UPDATE users SET email = %(e)s WHERE id = %(u)s", {"e": taken_email, "u": seed.org2_first_admin})
    taken = await registration_client.put(
        url, json={**body, "email": taken_email, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert taken.status_code == 409 and taken.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    invalid = await registration_client.put(
        url, json={**body, "phone_number": "abc", "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert invalid.status_code == 422 and invalid.json()["code"] == "INVALID_PHONE_NUMBER"
    done = await registration_client.put(url, json={**body, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa)
    assert done.status_code == 200, done.text
    assert done.json()["main_admin"] == {
        "full_name": "Hina Malik Khan",
        "username": done.json()["main_admin"]["username"],
        "email": "hina.new@riverside.example.com",
        "phone_number": "+923001112222",
    }
    stored = owner_query("SELECT full_name, email, phone FROM users WHERE id = %(u)s", {"u": seed.first_admin})[0]
    assert stored == ("Hina Malik Khan", "hina.new@riverside.example.com", "+923001112222")
    outcome, meta = audit_rows(owner_query, "ORGANIZATION_ADMIN_UPDATED", code)[-1]
    assert outcome == "SUCCESS" and meta["fields"] == ["full_name", "email", "phone_number"]
    every = json.dumps(owner_query("SELECT metadata FROM platform_audit_logs"), default=str)
    assert SUPER_ADMIN_PASSWORD not in every and WRONG not in every


async def test_five_wrong_passwords_lock_the_super_admin(
    registration_client: AsyncClient, sa: dict[str, str], owner_query
) -> None:
    code = code_of(owner_query, "Riverside University")
    body = {"status": "SUSPENDED", "current_password": WRONG}
    codes = [(await registration_client.post(f"{P}/organizations/{code}/status", json=body, headers=sa)).status_code
             for _ in range(6)]  # fmt: skip
    assert codes[:5] == [403] * 5 and codes[5] == 429
    good = await registration_client.post(
        f"{P}/organizations/{code}/status", json={**body, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert good.status_code == 429  # still locked, even with the right password
    assert (
        owner_query("SELECT status FROM organizations WHERE organization_code = %(c)s", {"c": code})[0][0] == "ACTIVE"
    )


# --- Organization status ----------------------------------------------------------------------------------


async def test_suspending_blocks_the_organization_everywhere_and_reactivating_restores_it(
    registration_client: AsyncClient, sa: dict[str, str], owner_query, seed: Seed
) -> None:
    code = code_of(owner_query, "Riverside University")
    tenant = auth_header(seed.first_admin)
    assert (await registration_client.get("/api/v1/cameras", headers=tenant)).status_code == 200
    url = f"{P}/organizations/{code}/status"
    r = await registration_client.post(
        url,
        json={"status": "SUSPENDED", "reason": "Unpaid invoice", "current_password": SUPER_ADMIN_PASSWORD},
        headers=sa,
    )
    assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"
    for path in ("/api/v1/cameras", "/api/v1/auth/me", "/api/v1/organization/admin-directory"):
        blocked = await registration_client.get(path, headers=tenant)
        assert blocked.status_code == 403 and blocked.json()["code"] == "ORGANIZATION_INACTIVE", path
    other = auth_header(seed.org2_first_admin)  # another organization is not affected
    assert (await registration_client.get("/api/v1/cameras", headers=other)).status_code == 200
    again = await registration_client.post(
        url, json={"status": "SUSPENDED", "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert again.status_code == 409 and again.json()["code"] == "STATUS_UNCHANGED"
    for status in ("INACTIVE", "ACTIVE"):
        changed = await registration_client.post(
            url, json={"status": status, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
        )
        assert changed.status_code == 200 and changed.json()["status"] == status
    assert (await registration_client.get("/api/v1/cameras", headers=tenant)).status_code == 200
    history = [m for o, m in audit_rows(owner_query, "ORGANIZATION_STATUS_CHANGED", code) if o == "SUCCESS"]
    assert [(m["from_status"], m["to_status"]) for m in history] == [
        ("ACTIVE", "SUSPENDED"), ("SUSPENDED", "INACTIVE"), ("INACTIVE", "ACTIVE"),
    ]  # fmt: skip
    assert history[0]["reason"] == "Unpaid invoice" and history[0]["organization_name"] == "Riverside University"
    unknown = await registration_client.post(
        f"{P}/organizations/ORG-00000000000/status",
        json={"status": "ACTIVE", "current_password": SUPER_ADMIN_PASSWORD},
        headers=sa,
    )
    assert unknown.status_code == 404
    bad = await registration_client.post(url, json={"status": "DELETED", "current_password": "x"}, headers=sa)
    assert bad.status_code == 422


# --- AI settings ------------------------------------------------------------------------------------------


async def test_ai_settings_read_and_update_with_password(
    registration_client: AsyncClient, sa: dict[str, str], owner_query
) -> None:
    current = (await registration_client.get(f"{P}/ai-settings", headers=sa)).json()
    assert current["human_detection_tracking_enabled"] is True and current["rag_assistant_enabled"] is False
    assert float(current["default_confidence_threshold"]) == 0.5 and current["frame_sample_fps"] == 5
    assert {m["key"]: m["implemented"] for m in current["modules"]}["weapon_detection_enabled"] is False
    values = {k: current[k] for k in current if k not in ("modules", "updated_at", "updated_by_username")}
    new = {**values, "weapon_detection_enabled": False, "rag_assistant_enabled": True,
           "default_confidence_threshold": "0.65", "frame_sample_fps": 8}  # fmt: skip
    wrong = await registration_client.put(f"{P}/ai-settings", json={**new, "current_password": WRONG}, headers=sa)
    assert wrong.status_code == 403
    out_of_range = await registration_client.put(
        f"{P}/ai-settings", json={**new, "frame_sample_fps": 99, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert out_of_range.status_code == 422
    missing = await registration_client.put(
        f"{P}/ai-settings",
        json={k: v for k, v in new.items() if k != "max_task_retries"} | {"current_password": SUPER_ADMIN_PASSWORD},
        headers=sa,
    )
    assert missing.status_code == 422
    done = await registration_client.put(
        f"{P}/ai-settings", json={**new, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["weapon_detection_enabled"] is False and body["rag_assistant_enabled"] is True
    assert float(body["default_confidence_threshold"]) == 0.65 and body["frame_sample_fps"] == 8
    assert body["updated_by_username"] == "platform.root"
    outcome, meta = audit_rows(owner_query, "AI_SETTINGS_UPDATED")[-1]
    assert outcome == "SUCCESS"
    assert {c["field"] for c in meta["changes"]} == {
        "weapon_detection_enabled", "rag_assistant_enabled", "default_confidence_threshold", "frame_sample_fps",
    }  # fmt: skip
    # Back to the defaults so other tests start from them.
    await registration_client.put(
        f"{P}/ai-settings", json={**values, "current_password": SUPER_ADMIN_PASSWORD}, headers=sa
    )


# --- Health -----------------------------------------------------------------------------------------------


async def test_health_reports_measured_and_not_deployed_services(
    registration_client: AsyncClient, sa: dict[str, str], storage_root: Path
) -> None:
    body = (await registration_client.get(f"{P}/health", headers=sa)).json()
    services = {s["key"]: s for s in body["services"]}
    assert services["core_api"]["status"] == "OPERATIONAL" and services["core_api"]["metrics"]["uptime_seconds"] >= 0
    assert services["database"]["status"] == "OPERATIONAL" and services["database"]["latency_ms"] is not None
    assert "pgvector_installed" in services["database"]["metrics"]
    for key in ("video_ingestion", "cv_workers", "message_broker", "object_storage"):
        assert services[key]["status"] == "NOT_DEPLOYED"
    assert services["cv_workers"]["metrics"]["detection_intake_enabled"] is True
    # The media folder does not exist yet: reported honestly, the platform is degraded, not down.
    assert body["storage"]["accessible"] is False and body["storage"]["level"] == "UNAVAILABLE"
    assert body["overall_status"] == "DEGRADED" and "The media storage folder is not reachable." in body["problems"]
    storage_root.mkdir(parents=True)
    body = (await registration_client.get(f"{P}/health", headers=sa)).json()
    assert body["storage"]["accessible"] and body["storage"]["writable"] and body["storage"]["total_bytes"] > 0
    assert body["storage"]["level"] in ("OK", "WARNING", "CRITICAL")
    assert body["overall_status"] in ("OPERATIONAL", "DEGRADED") and "not deployed" in body["summary"]
    assert list(storage_root.iterdir()) == []  # the write probe leaves nothing behind


# --- Audit log --------------------------------------------------------------------------------------------


async def test_audit_log_filters_paging_actions_and_export(
    registration_client: AsyncClient, sa: dict[str, str], owner_query
) -> None:
    code = code_of(owner_query, "Riverside University")
    await registration_client.post(
        f"{P}/organizations/{code}/status",
        json={"status": "SUSPENDED", "current_password": SUPER_ADMIN_PASSWORD},
        headers=sa,
    )
    await registration_client.post(
        f"{P}/organizations/{code}/status", json={"status": "ACTIVE", "current_password": WRONG}, headers=sa
    )

    async def page(**params: Any) -> dict[str, Any]:
        r = await registration_client.get(f"{P}/audit-logs", params=params, headers=sa)
        assert r.status_code == 200, r.text
        return r.json()

    mine = await page(organization=code)
    assert {e["organization_id"] for e in mine["items"]} == {code}
    assert {"ORGANIZATION_REGISTERED", "ORGANIZATION_STATUS_CHANGED"} <= {e["action"] for e in mine["items"]}
    by_name = await page(organization="riverside", action="ORGANIZATION_STATUS_CHANGED", outcome="SUCCESS")
    assert by_name["total"] >= 1 and all(e["organization_name"] == "Riverside University" for e in by_name["items"])
    entry = by_name["items"][0]
    assert entry["actor_type"] == "SUPER_ADMIN" and entry["actor_username"] == "platform.root"
    assert entry["details"]["to_status"] == "SUSPENDED"
    registered = (await page(action="ORGANIZATION_REGISTERED", organization=code))["items"][0]
    assert registered["actor_type"] == "SYSTEM"
    failures = await page(action="ORGANIZATION_STATUS_CHANGED", outcome="FAILURE")
    assert failures["total"] >= 1
    assert (await page(date_from="2999-01-01"))["total"] == 0
    first = await page(limit=1, offset=0)
    second = await page(limit=1, offset=1)
    assert first["total"] == second["total"] >= 2 and first["items"][0]["id"] != second["items"][0]["id"]
    bad = await registration_client.get(
        f"{P}/audit-logs", params={"date_from": "2026-02-02", "date_to": "2026-02-01"}, headers=sa
    )
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_DATE_RANGE"
    assert (
        await registration_client.get(f"{P}/audit-logs", params={"action": "drop;--"}, headers=sa)
    ).status_code == 422
    actions = (await registration_client.get(f"{P}/audit-logs/actions", headers=sa)).json()
    assert {"action": "ORGANIZATION_STATUS_CHANGED", "count": actions[[a["action"] for a in actions].index(
        "ORGANIZATION_STATUS_CHANGED")]["count"]} in actions  # fmt: skip
    export = await registration_client.get(f"{P}/audit-logs/export", params={"organization": code}, headers=sa)
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(export.text.lstrip("﻿"))))
    assert rows[0][:4] == ["occurred_at", "actor_type", "actor_username", "action"] and len(rows) >= 3
    assert SUPER_ADMIN_PASSWORD not in export.text and WRONG not in export.text
    assert audit_rows(owner_query, "AUDIT_LOGS_EXPORTED")[-1][1]["filters"]["organization"] == code
