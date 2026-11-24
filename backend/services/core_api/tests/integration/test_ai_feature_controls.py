"""AI feature controls (migration 0033, decision D-028): explicit per-camera opt-in (new cameras run nothing), AI
features with the camera's details, bulk 1-click assignment, Supervisor visibility set by Admins (hidden modules are
kept as they were), the Super Admin's per-organization blocks (SA-SEC-4 password, audited, immediate, never
re-enabling cameras) and the isolation of the new table and functions."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import SUPER_ADMIN_PASSWORD, platform_auth_header, run_as
from tests.integration.test_alert_operations import SERVICE, call, modules, ops_client  # noqa: F401
from tests.integration.test_alerts import ok, site  # noqa: F401  (site is a fixture)
from tests.integration.test_ownership_and_deletion import OTHER_PASSWORD, headers, one, ucp  # noqa: F401

API = "/api/v1"
NONE = dict.fromkeys(("human_detection", "person_reid", "weapon_detection", "fight_detection", "face_recognition"),
                     False)  # fmt: skip


async def worker_modules(client: AsyncClient, camera: Any) -> list[str]:
    response = await client.get(f"{API}/internal/cameras/{camera}/ai-config", headers=SERVICE)
    assert response.status_code == 200, response.text
    found: list[str] = response.json()["modules"]
    return found


def stored(owner_query, camera: Any) -> dict[str, bool]:
    row = owner_query(
        "SELECT human_detection, person_reid, weapon_detection, fight_detection, face_recognition "
        "FROM camera_ai_settings WHERE camera_id = %(c)s",
        {"c": camera},
    )
    return dict(zip(NONE, row[0], strict=True)) if row else {}


def camera_in(view: dict[str, Any], camera: Any) -> dict[str, Any]:
    [found] = [c for c in view["cameras"] if c["camera_id"] == str(camera)]
    return found


async def add_camera(client: AsyncClient, user: dict[str, Any], name: str, **extra: Any):  # type: ignore[no-untyped-def]
    stream = f"rtsp://10.0.0.5/{name.replace(' ', '-')}"
    body = {"display_name": name, "connection_type": "RTSP", "stream_url": stream, **extra}
    return await call(client, user, "POST", "/cameras", body)


def audit_actions(owner_query, action: str) -> list[dict[str, Any]]:
    rows = owner_query(
        "SELECT metadata FROM organization_audit_logs WHERE action = %(a)s ORDER BY occurred_at", {"a": action}
    )
    return [m if isinstance(m, dict) else json.loads(m) for (m,) in rows]


# --- Explicit per-camera opt-in and the camera's details ---------------------------------------------------


async def test_new_cameras_run_nothing_until_features_are_selected_with_the_camera(
    ops_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    plain = await add_camera(client, a, "Plain")
    assert plain.status_code == 201, plain.text
    plain_id = plain.json()["id"]
    assert plain.json()["ai_modules"] == [] and plain.json()["ai_effective_modules"] == []
    assert await worker_modules(client, plain_id) == []
    assert stored(owner_query, plain_id) == NONE  # a row is written with the camera
    # A camera inserted without any AI row (e.g. before the API wrote one) also runs nothing.
    assert await worker_modules(client, site["camera_a"]) == []

    chosen = await add_camera(client, sa, "Chosen", ai_modules={"weapon_detection": True, "person_reid": True})
    assert chosen.status_code == 201, chosen.text
    chosen_id = chosen.json()["id"]
    assert chosen.json()["ai_modules"] == ["person_reid", "weapon_detection"]
    assert await worker_modules(client, chosen_id) == ["person_reid", "weapon_detection"]

    face = await add_camera(client, a, "Face", ai_modules={"face_recognition": True})
    assert face.status_code == 409 and face.json()["code"] == "MODULE_NOT_PERMITTED"
    unknown = await add_camera(client, a, "Unknown", ai_modules={"teleport": True})
    assert unknown.status_code == 422
    assert not owner_query("SELECT 1 FROM cameras WHERE display_name IN ('Face', 'Unknown')")  # nothing written

    # Edit: only the named modules change; without ai_modules nothing changes.
    edited = await ok(client, a, "PUT", f"/cameras/{chosen_id}", {
        "display_name": "Chosen 2", "connection_type": "RTSP", "stream_url": "rtsp://10.0.0.5/c",
        "ai_modules": {"fight_detection": True, "person_reid": False},
    })  # fmt: skip
    assert edited["ai_modules"] == ["weapon_detection", "fight_detection"]
    renamed = await ok(client, a, "PUT", f"/cameras/{chosen_id}",
                       {"display_name": "Chosen 3", "connection_type": "RTSP", "stream_url": "rtsp://10.0.0.5/c"})
    assert renamed["ai_modules"] == ["weapon_detection", "fight_detection"]
    listed = {c["display_name"]: c for c in await ok(client, sa, "GET", "/cameras")}
    assert listed["Chosen 3"]["ai_effective_modules"] == ["weapon_detection", "fight_detection"]
    assert listed["Plain"]["ai_modules"] == []
    details = await ok(client, a, "GET", f"/cameras/{chosen_id}")
    assert details["ai_modules"] == ["weapon_detection", "fight_detection"]
    records = audit_actions(owner_query, "CAMERA_AI_SETTINGS_UPDATED")
    assert any(r.get("with_camera_details") and r["camera"] == "Chosen" for r in records)
    assert any(r["changed"] == {"person_reid": False, "fight_detection": True} for r in records)


# --- Bulk assignment ----------------------------------------------------------------------------------------


async def test_bulk_add_remove_and_set_are_atomic_and_campus_scoped(
    ops_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    ids = [(await add_camera(client, a, f"Bulk {n}")).json()["id"] for n in range(3)]
    other_campus = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=site["campus_b"])
    url = "/ai-configuration/cameras/bulk"

    def body(mode: str, chosen: dict[str, bool], cameras: list[Any], password: str = OTHER_PASSWORD) -> dict:  # type: ignore[type-arg]
        return {"camera_ids": [str(c) for c in cameras], "mode": mode, "modules": chosen, "current_password": password}

    added = await ok(client, sa, "PUT", url, body("ADD", {"weapon_detection": True, "fight_detection": True}, ids[:2]))
    assert added["mode"] == "ADD" and added["updated_count"] == 2
    assert all(c["configured"]["weapon_detection"] and c["configured"]["fight_detection"] for c in added["cameras"])
    assert await worker_modules(client, ids[0]) == ["weapon_detection", "fight_detection"]
    assert await worker_modules(client, ids[2]) == []

    removed = await ok(client, sa, "PUT", url, body("REMOVE", {"fight_detection": True}, ids[:1]))
    assert removed["cameras"][0]["configured"] == {**NONE, "weapon_detection": True}

    replaced = await ok(client, a, "PUT", url, body("SET", {"human_detection": True}, ids, a["password"]))
    assert [c["configured"] for c in replaced["cameras"]] == [{**NONE, "human_detection": True}] * 3

    # All or nothing: another campus's camera, a wrong password or a module beyond the campus changes nothing.
    before = [stored(owner_query, c) for c in ids]
    cross = await call(client, sa, "PUT", url, body("ADD", {"weapon_detection": True}, [ids[0], other_campus]))
    assert cross.status_code == 404 and cross.json()["code"] == "CAMERA_NOT_FOUND"
    wrong = await call(client, sa, "PUT", url, body("ADD", {"weapon_detection": True}, ids, "Wrong-pass-2026"))
    assert wrong.status_code == 403
    face = await call(client, sa, "PUT", url, body("ADD", {"face_recognition": True}, ids))
    assert face.status_code == 409 and face.json()["code"] == "MODULE_NOT_PERMITTED"
    nothing = await call(client, sa, "PUT", url, body("ADD", {"weapon_detection": False}, ids))
    assert nothing.status_code == 409 and nothing.json()["code"] == "NO_AI_MODULE_SELECTED"
    assert [stored(owner_query, c) for c in ids] == before

    assert (await call(client, sa, "PUT", url, body("ADD", {"weapon_detection": True}, []))).status_code == 422
    too_many = [str(uuid4()) for _ in range(201)]
    assert (await call(client, sa, "PUT", url, body("ADD", {"weapon_detection": True}, too_many))).status_code == 422
    assert (await call(client, sa, "PUT", url, body("MERGE", {"weapon_detection": True}, ids))).status_code == 422
    guard = await call(client, site["ga"], "PUT", url, body("ADD", {"weapon_detection": True}, ids))
    assert guard.status_code == 403
    [record, *_] = audit_actions(owner_query, "CAMERA_AI_BULK_UPDATED")
    assert record["mode"] == "ADD" and record["camera_count"] == 2
    assert record["modules"] == ["weapon_detection", "fight_detection"]
    assert len(audit_actions(owner_query, "CAMERA_AI_BULK_UPDATED")) == 3  # failures write nothing


# --- Supervisor visibility -----------------------------------------------------------------------------------


async def test_admins_hide_modules_from_supervisors_whose_changes_keep_them(
    ops_client: AsyncClient, site: dict[str, Any], owner_query
) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    camera = (await add_camera(client, a, "Vault", ai_modules={"weapon_detection": True, "human_detection": True}))
    camera_id = camera.json()["id"]
    view = await ok(client, a, "GET", "/ai-configuration")
    assert view["supervisor_visible_modules"] == {k: True for k in NONE}
    assert (await ok(client, sa, "GET", "/ai-configuration"))["supervisor_visible_modules"] is None

    hidden = {k: True for k in NONE} | {"weapon_detection": False}
    by_supervisor = await call(client, sa, "PUT", "/ai-configuration/campus",
                               {**modules(), "supervisor_visible_modules": hidden,
                                "current_password": OTHER_PASSWORD})  # fmt: skip
    assert by_supervisor.status_code == 403
    saved = await ok(client, a, "PUT", "/ai-configuration/campus",
                     {**modules(), "supervisor_visible_modules": hidden, "current_password": a["password"]})
    assert saved["supervisor_visible_modules"]["weapon_detection"] is False
    [record, *_] = audit_actions(owner_query, "AI_SETTINGS_UPDATED")
    assert record["supervisor_visible_changed"] == {"weapon_detection": False}

    # The Supervisor no longer sees weapon detection anywhere on the page...
    seen = await ok(client, sa, "GET", "/ai-configuration")
    assert "weapon_detection" not in [m["key"] for m in seen["modules"]]
    assert "weapon_detection" not in seen["available_modules"]
    assert "WEAPON" not in [r["detection"] for r in seen["alert_rules"]]
    assert camera_in(seen, camera_id)["configured"]["weapon_detection"] is False
    # ...but the camera keeps running it: hiding never switches a module off.
    assert camera_in(await ok(client, a, "GET", "/ai-configuration"), camera_id)["effective"]["weapon_detection"]
    assert "weapon_detection" in await worker_modules(client, camera_id)

    # Supervisor changes (single, bulk, camera details) are refused for the hidden module and keep it otherwise.
    turn_on = await call(client, sa, "PUT", f"/ai-configuration/cameras/{camera_id}",
                         {**NONE, "weapon_detection": True, "current_password": OTHER_PASSWORD})  # fmt: skip
    assert turn_on.status_code == 403 and turn_on.json()["code"] == "AI_MODULE_HIDDEN_FROM_SUPERVISOR"
    await ok(client, sa, "PUT", f"/ai-configuration/cameras/{camera_id}",
             {**NONE, "person_reid": True, "current_password": OTHER_PASSWORD})  # fmt: skip
    assert stored(owner_query, camera_id) == {**NONE, "person_reid": True, "weapon_detection": True}
    await ok(client, sa, "PUT", "/ai-configuration/cameras/bulk", {
        "camera_ids": [camera_id], "mode": "SET", "modules": {"fight_detection": True},
        "current_password": OTHER_PASSWORD,
    })  # fmt: skip
    assert stored(owner_query, camera_id) == {**NONE, "fight_detection": True, "weapon_detection": True}
    for mode in ("ADD", "REMOVE"):
        bulk = await call(client, sa, "PUT", "/ai-configuration/cameras/bulk", {
            "camera_ids": [camera_id], "mode": mode, "modules": {"weapon_detection": True},
            "current_password": OTHER_PASSWORD,
        })  # fmt: skip
        assert bulk.status_code == 403 and bulk.json()["code"] == "AI_MODULE_HIDDEN_FROM_SUPERVISOR", mode
    edited = await ok(client, sa, "PUT", f"/cameras/{camera_id}", {
        "display_name": "Vault 2", "connection_type": "RTSP", "stream_url": "rtsp://10.0.0.5/v",
        "ai_modules": {**NONE},
    })  # fmt: skip
    assert edited["ai_modules"] == []  # the Supervisor's view
    assert stored(owner_query, camera_id) == {**NONE, "weapon_detection": True}
    refused = await add_camera(client, sa, "Hidden", ai_modules={"weapon_detection": True})
    assert refused.status_code == 403 and refused.json()["code"] == "AI_MODULE_HIDDEN_FROM_SUPERVISOR"
    # Admins still manage it.
    await ok(client, a, "PUT", f"/ai-configuration/cameras/{camera_id}", {**NONE, "current_password": a["password"]})
    assert stored(owner_query, camera_id) == NONE


# --- Super Admin per-organization blocks --------------------------------------------------------------------


async def test_super_admin_blocks_and_unblocks_a_module_for_one_organization(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, super_admin: UUID
) -> None:
    client, a, sa = ops_client, site["a"], site["sa"]
    sa_token = platform_auth_header(super_admin)
    code = one(owner_query, "SELECT organization_code FROM organizations WHERE id = %(o)s", o=site["org"])
    camera_id = (await add_camera(client, a, "Hall", ai_modules={"weapon_detection": True, "human_detection": True})
                 ).json()["id"]  # fmt: skip
    url = f"{API}/platform/organizations/{code}/ai-permissions"

    detail = (await client.get(f"{API}/platform/organizations/{code}", headers=sa_token)).json()
    status = {p["module_key"]: p["status"] for p in detail["ai_permissions"]}
    assert status == {"human_detection": "ALLOWED", "person_reid": "ALLOWED", "weapon_detection": "ALLOWED",
                      "fight_detection": "ALLOWED", "face_recognition": "DISABLED_GLOBALLY"}  # fmt: skip
    assert [p["core"] for p in detail["ai_permissions"]] == [True, True, True, True, False]

    # Platform tokens only; the password is required (SA-SEC-4) and a wrong one is audited.
    tenant = await client.put(url, json={"module_key": "weapon_detection", "blocked": True,
                                         "current_password": a["password"]}, headers=headers(a))  # fmt: skip
    assert tenant.status_code == 401
    wrong = await client.put(url, json={"module_key": "weapon_detection", "blocked": True,
                                        "current_password": "Not-the-password-1"}, headers=sa_token)  # fmt: skip
    assert wrong.status_code == 403
    assert "weapon_detection" in await worker_modules(client, camera_id)
    unknown = await client.put(url, json={"module_key": "teleport", "blocked": True,
                                          "current_password": SUPER_ADMIN_PASSWORD}, headers=sa_token)  # fmt: skip
    assert unknown.status_code == 409 and unknown.json()["code"] == "UNKNOWN_AI_MODULE"
    missing = await client.put(f"{API}/platform/organizations/ORG-00000000000/ai-permissions", headers=sa_token,
                               json={"module_key": "weapon_detection", "blocked": True,
                                     "current_password": SUPER_ADMIN_PASSWORD})  # fmt: skip
    assert missing.status_code == 404

    # Block (by its feature name): the worker stops at once and nobody in the organization can switch it on.
    blocked = await client.put(url, json={"module_key": "weapon_detection", "blocked": True,
                                          "current_password": SUPER_ADMIN_PASSWORD}, headers=sa_token)  # fmt: skip
    assert blocked.status_code == 200, blocked.text
    assert {p["module_key"]: p["status"] for p in blocked.json()["ai_permissions"]}["weapon_detection"] == "BLOCKED"
    assert await worker_modules(client, camera_id) == ["human_detection"]
    assert stored(owner_query, camera_id)["weapon_detection"] is False
    view = await ok(client, a, "GET", "/ai-configuration")
    assert view["blocked_by_platform"] == ["weapon_detection"] and view["org_blocked"]["weapon_detection"] is True
    assert "weapon_detection" not in view["available_modules"]
    assert view["campus_permitted"]["weapon_detection"] is False
    for response in (
        await call(client, a, "PUT", f"/ai-configuration/cameras/{camera_id}",
                   {**NONE, "weapon_detection": True, "current_password": a["password"]}),
        await call(client, a, "PUT", "/ai-configuration/campus",
                   {**modules(), "current_password": a["password"]}),
        await call(client, sa, "PUT", "/ai-configuration/cameras/bulk",
                   {"camera_ids": [camera_id], "mode": "ADD", "modules": {"weapon_detection": True},
                    "current_password": OTHER_PASSWORD}),
        await add_camera(client, a, "Blocked", ai_modules={"weapon_detection": True}),
    ):  # fmt: skip
        assert response.status_code == 403 and response.json()["code"] == "AI_MODULE_BLOCKED_BY_PLATFORM"
    # The other campus of the organization is blocked as well; Organization Settings lists only what is permitted.
    assert (await ok(client, site["b"], "GET", "/ai-configuration"))["blocked_by_platform"] == ["weapon_detection"]

    # Unblock: allowed again, but no camera is switched back on by itself.
    unblocked = await client.put(url, json={"module_key": "weapon_detection", "blocked": False,
                                            "current_password": SUPER_ADMIN_PASSWORD}, headers=sa_token)  # fmt: skip
    assert unblocked.status_code == 200
    assert await worker_modules(client, camera_id) == ["human_detection"]
    again = await client.put(url, json={"module_key": "weapon_detection", "blocked": False,
                                        "current_password": SUPER_ADMIN_PASSWORD}, headers=sa_token)  # fmt: skip
    assert again.status_code == 200  # idempotent, not audited twice
    await ok(client, a, "PUT", f"/ai-configuration/cameras/{camera_id}",
             {**NONE, "weapon_detection": True, "current_password": a["password"]})  # fmt: skip
    assert "weapon_detection" in await worker_modules(client, camera_id)

    rows = owner_query(
        "SELECT outcome, metadata FROM platform_audit_logs WHERE action = 'ORGANIZATION_AI_PERMISSIONS_UPDATED' "
        "ORDER BY occurred_at"
    )
    outcomes = [(o, m if isinstance(m, dict) else json.loads(m)) for o, m in rows]
    successes = [m for o, m in outcomes if o == "SUCCESS"]
    assert [m["blocked"] for m in successes] == [True, False]
    assert successes[0]["organization_code"] == code and successes[0]["module"] == "weapon_detection"
    assert any(o != "SUCCESS" for o, _ in outcomes)  # the wrong password


async def test_the_block_table_and_functions_stay_isolated(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, app_role_engine, platform_role_engine
) -> None:
    admin = site["a"]["id"]
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, admin, "SELECT * FROM platform_organization_ai_blocks")
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, admin,
                     "SELECT platform_organization_ai_permissions_set('X', 'weapon_detection', true, NULL)")
    with pytest.raises(DBAPIError, match="permission denied"):
        async with platform_role_engine.connect() as conn:
            await conn.execute(text("SELECT * FROM platform_organization_ai_blocks"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with platform_role_engine.connect() as conn:
            await conn.execute(text("SELECT * FROM app_organization_ai_blocks()"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with platform_role_engine.connect() as conn:
            await conn.execute(text("SELECT * FROM camera_ai_settings"))
    # The tenant function answers only for the caller's own organization, and only for Admins and Supervisors.
    rows, _ = await run_as(app_role_engine, admin, "SELECT * FROM app_organization_ai_blocks()")
    assert len(rows) == 1 and not any(rows[0])
    rows, _ = await run_as(app_role_engine, site["ga"]["id"], "SELECT * FROM app_organization_ai_blocks()")
    assert rows == []
    # Organization erasure removes its block row (foreign key ON DELETE CASCADE).
    [(rule,)] = owner_query(
        "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'platform_organization_ai_blocks'::regclass "
        "AND contype = 'f'"
    )
    assert rule == "c"
