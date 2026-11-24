"""Organization Personnel Management (migration 0017, decision D-016): Admin-only registration and
changes, Supervisor read-only basic access, no Guard or Super Admin access, validation shared with
the import, history and audit, photos, and strict campus isolation in the API and the database."""

# ruff: noqa: F811  (the imported ``ucp`` fixture is used as a test parameter)

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError

from tests.conftest import platform_auth_header, run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_ownership_and_deletion import (
    ACCOUNT,
    count,
    delete_account,
    headers,
    one,
    start_deletion,
    ucp,  # noqa: F401  (a fixture, used by name)
)

P = "/api/v1/personnel"
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 200


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str = "", body: Any = None, **kw: Any):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{P}{path}", json=body, headers=headers(user), **kw)


async def ok(client: AsyncClient, user: dict[str, Any], method: str, path: str = "", body: Any = None) -> Any:
    response = await call(client, user, method, path, body)
    assert response.status_code in (200, 201, 204), (method, path, response.text)
    return response.json() if response.content else None


async def category_ids(client: AsyncClient, user: dict[str, Any]) -> dict[str, str]:
    return {c["code"]: c["id"] for c in await ok(client, user, "GET", "/categories")}


async def register(
    client: AsyncClient, admin: dict[str, Any], name: str, code: str = "EMPLOYEE", **fields: Any
) -> dict:
    cats = await category_ids(client, admin)
    body = {"full_name": name, "category_id": cats[code], **fields}
    if code in ("EMPLOYEE", "STUDENT", "FACULTY", "STAFF", "MANAGEMENT"):
        body.setdefault("external_id", f"X-{uuid4().hex[:8]}")
    if code in ("VISITOR", "CONTRACTOR"):
        body.setdefault("end_date", "2030-01-01")
    body.setdefault("email", f"{uuid4().hex[:10]}@example.com")
    return (await ok(client, admin, "POST", "", body))["personnel"]


# --- Admin registration and changes --------------------------------------------------------------------------


async def test_admin_registers_edits_deactivates_and_reactivates_with_history(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    cats = await category_ids(registration_client, a)
    assert set(cats) == {"STUDENT", "FACULTY", "EMPLOYEE", "STAFF", "MANAGEMENT", "CONTRACTOR", "VISITOR", "OTHER"}
    created = await ok(
        registration_client,
        a,
        "POST",
        "",
        {
            "full_name": "  Ayesha Khan ",
            "category_id": cats["STUDENT"],
            "external_id": "STU-001",
            "email": "Ayesha@Example.com",
            "phone": "0300-1234567",
            "department": "Computer Science",
            "designation": "Student",
            "joining_date": "2026-09-01",
            "start_date": "2026-09-01",
            "end_date": "2030-06-30",
            "address": "Lahore",
            "emergency_contact_name": "Parent",
            "emergency_contact_phone": "03007654321",
            "emergency_contact_relation": "Father",
            "notes": "Hostel",
        },
    )
    person = created["personnel"]
    assert created["warnings"] == []
    assert person["personnel_id"].startswith("PSN-") and len(person["personnel_id"]) == 14
    assert person["full_name"] == "Ayesha Khan" and person["email"] == "ayesha@example.com"
    assert person["phone"] == "03001234567" and person["category"]["code"] == "STUDENT"
    assert person["created_via"] == "MANUAL" and person["created_by"] == "Maryam Siddiqui"
    assert person["status"] == "ACTIVE" and person["import_batch_id"] is None
    stored = one(owner_query, "SELECT organization_id, campus_id FROM personnel WHERE id = %(i)s", i=person["id"])
    assert stored == (ucp["org"], ucp["campus_a"])  # from the session, never from the request

    edit = {
        "full_name": "Ayesha K.",
        "category_id": cats["STUDENT"],
        "external_id": "STU-001",
        "email": "ayesha@example.com",
        "department": "Software Engineering",
    }
    updated = (await ok(registration_client, a, "PUT", f"/{person['id']}", edit))["personnel"]
    assert updated["department"] == "Software Engineering" and updated["phone"] is None
    assert updated["personnel_id"] == person["personnel_id"]  # the Personnel ID never changes

    off = await ok(registration_client, a, "POST", f"/{person['id']}/deactivate", {"reason": "Graduated"})
    assert off["status"] == "INACTIVE" and off["deactivation_reason"] == "Graduated" and off["deactivated_at"]
    again = await call(registration_client, a, "POST", f"/{person['id']}/deactivate")
    assert again.status_code == 409 and again.json()["code"] == "PERSONNEL_ALREADY_INACTIVE"
    on = await ok(registration_client, a, "POST", f"/{person['id']}/reactivate")
    assert on["status"] == "ACTIVE" and on["deactivated_at"] is None

    history = await ok(registration_client, a, "GET", f"/{person['id']}/history")
    assert [h["action"] for h in history] == [
        "PERSONNEL_CREATED",
        "PERSONNEL_UPDATED",
        "PERSONNEL_DEACTIVATED",
        "PERSONNEL_REACTIVATED",
    ]
    changed = history[1]["details"]["changed_fields"]
    assert set(changed) == {
        "full_name",
        "phone",
        "department",
        "designation",
        "joining_date",
        "start_date",
        "end_date",
        "address",
        "emergency_contact_name",
        "emergency_contact_phone",
        "emergency_contact_relation",
        "notes",
    }
    assert all(h["actor_name"] == "Maryam Siddiqui" and h["actor_role"] == "ADMIN" for h in history)
    # Names of changed fields only: no personal values in the audit log.
    logged = str(
        owner_query("SELECT metadata FROM organization_audit_logs WHERE target_id = %(i)s", {"i": person["id"]})
    )
    for value in ("Ayesha", "03001234567", "Lahore", "Graduated", "ayesha@example.com"):
        assert value not in logged


async def test_required_optional_and_category_specific_fields(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a = ucp["a"]
    cats = await category_ids(registration_client, a)
    response = await call(
        registration_client,
        a,
        "POST",
        "",
        {"full_name": "", "category_id": cats["STUDENT"], "email": "bad", "phone": "12"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_PERSONNEL"
    assert {(e["field"], e["code"]) for e in body["errors"]} == {
        ("full_name", "REQUIRED"),
        ("external_id", "REQUIRED_FOR_CATEGORY"),
        ("email", "INVALID_EMAIL"),
        ("phone", "INVALID_PHONE"),
    }
    # A visitor needs no institutional ID, only the end of the visit; a warning does not block.
    visitor = await ok(
        registration_client,
        a,
        "POST",
        "",
        {"full_name": "Guest", "category_id": cats["VISITOR"], "end_date": "2020-01-01"},
    )
    assert visitor["personnel"]["external_id"] is None
    assert {w["code"] for w in visitor["warnings"]} == {"END_DATE_PAST", "NO_CONTACT"}
    wrong = await call(registration_client, a, "POST", "", {"full_name": "X", "category_id": str(uuid4())})
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["code"] == "UNKNOWN_CATEGORY"
    order = await call(
        registration_client,
        a,
        "POST",
        "",
        {
            "full_name": "X",
            "category_id": cats["OTHER"],
            "phone": "03001234567",
            "start_date": "2026-05-01",
            "end_date": "2026-04-01",
        },
    )
    assert order.status_code == 422 and order.json()["errors"][0]["code"] == "END_BEFORE_START"
    extra = await call(
        registration_client,
        a,
        "POST",
        "",
        {"full_name": "X", "category_id": cats["OTHER"], "organization_id": str(ucp["org"])},
    )
    assert extra.status_code == 422  # organization and campus can never be sent


async def test_identifiers_are_unique_within_a_campus_only(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a, b = ucp["a"], ucp["b"]
    await register(registration_client, a, "First", external_id="EMP-1", email="same@example.com")
    for field, value, code in (
        ("external_id", "emp-1", "EXTERNAL_ID_TAKEN"),
        ("email", "SAME@example.com", "EMAIL_TAKEN"),
    ):
        cats = await category_ids(registration_client, a)
        body = {"full_name": "Second", "category_id": cats["EMPLOYEE"], "external_id": "EMP-2", field: value}
        response = await call(registration_client, a, "POST", "", body)
        assert response.status_code == 409 and response.json()["code"] == code
    # Campus B keeps its own, independent records.
    other = await register(registration_client, b, "Elsewhere", external_id="EMP-1", email="same@example.com")
    assert other["external_id"] == "EMP-1"


async def test_categories_and_custom_fields_are_admin_controlled(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a, sa = ucp["a"], ucp["sa"]
    intern = await ok(
        registration_client,
        a,
        "POST",
        "/categories",
        {"code": "intern", "label": "Intern", "required_fields": ["external_id", "end_date"]},
    )
    assert intern["code"] == "INTERN" and intern["is_system"] is False
    bad = await call(
        registration_client, a, "POST", "/categories", {"code": "X2", "label": "X", "required_fields": ["full_name"]}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_REQUIRED_FIELDS"
    dup = await call(registration_client, a, "POST", "/categories", {"code": "STUDENT", "label": "Learner"})
    assert dup.status_code == 409

    cats = await category_ids(registration_client, a)
    program = await ok(
        registration_client,
        a,
        "POST",
        "/custom-fields",
        {
            "key": "academic_program",
            "label": "Academic Program",
            "field_type": "CHOICE",
            "choices": ["BSSE", "BSCS"],
            "is_required": True,
            "category_ids": [cats["STUDENT"]],
        },
    )
    for key in ("email", "organization_id", "assigned_building", "Bad Key"):
        refused = await call(
            registration_client, a, "POST", "/custom-fields", {"key": key, "label": f"L {key}", "field_type": "TEXT"}
        )
        assert refused.status_code == 422 and refused.json()["code"] == "INVALID_FIELD_KEY", key
    student = {"full_name": "S", "category_id": cats["STUDENT"], "external_id": "S-1", "email": "s@example.com"}
    missing = await call(registration_client, a, "POST", "", student)
    assert missing.status_code == 422 and missing.json()["errors"][0]["field"] == "custom:academic_program"
    wrong = await call(
        registration_client, a, "POST", "", {**student, "custom_attributes": {"academic_program": "MBA"}}
    )
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["code"] == "INVALID_CUSTOM_VALUE"
    unknown = await call(registration_client, a, "POST", "", {**student, "custom_attributes": {"shoe_size": 9}})
    assert unknown.status_code == 422
    made = await ok(registration_client, a, "POST", "", {**student, "custom_attributes": {"academic_program": "bsse"}})
    assert made["personnel"]["custom_attributes"] == {"academic_program": "BSSE"}
    # Deactivating a field keeps stored values; it is no longer required for new records.
    await ok(
        registration_client,
        a,
        "PUT",
        f"/custom-fields/{program['id']}",
        {"label": "Academic Program", "choices": ["BSSE", "BSCS"], "is_active": False},
    )

    for method, path, body in (
        ("POST", "/categories", {"code": "NEW", "label": "New"}),
        ("PUT", f"/categories/{intern['id']}", {"label": "Trainee"}),
        ("GET", "/custom-fields", None),
        ("POST", "/custom-fields", {"key": "x_y", "label": "XY", "field_type": "TEXT"}),
        ("PUT", f"/custom-fields/{program['id']}", {"label": "P"}),
    ):
        response = await call(registration_client, sa, method, path, body)
        assert response.status_code == 403, (method, path)
    assert len(await ok(registration_client, sa, "GET", "/categories")) == 9  # Supervisors may read categories


# --- Supervisor, Guard and Super Admin ---------------------------------------------------------------------------


async def test_supervisors_get_read_only_basic_details(registration_client: AsyncClient, ucp: dict[str, Any]) -> None:
    a, sa = ucp["a"], ucp["sa"]
    person = await register(
        registration_client,
        a,
        "Bilal Ahmed",
        phone="03001112222",
        address="Secret Street 1",
        notes="Private note",
        department="IT",
    )
    listing = await ok(registration_client, sa, "GET", "?q=bilal")
    assert listing["total"] == 1
    row = listing["items"][0]
    assert row["full_name"] == "Bilal Ahmed" and row["email"] is None and row["phone"] is None
    details = await ok(registration_client, sa, "GET", f"/{person['id']}")
    assert set(details) == {
        "id",
        "personnel_id",
        "external_id",
        "full_name",
        "category",
        "department",
        "designation",
        "status",
        "start_date",
        "end_date",
        "assigned_location",
    }
    text = (await call(registration_client, sa, "GET", f"/{person['id']}")).text
    for private in ("Secret Street", "Private note", "03001112222", person["email"]):
        assert private not in text
    # Searching by email is an Admin feature; Supervisors cannot find people through it.
    assert (await ok(registration_client, sa, "GET", f"?q={person['email']}"))["total"] == 0
    assert (await ok(registration_client, a, "GET", f"?q={person['email']}"))["total"] == 1

    cats = await category_ids(registration_client, a)
    attempts = [
        ("POST", "", {"full_name": "Sneaky", "category_id": cats["OTHER"]}),
        ("PUT", f"/{person['id']}", {"full_name": "Changed", "category_id": cats["OTHER"]}),
        ("POST", f"/{person['id']}/deactivate", None),
        ("POST", f"/{person['id']}/reactivate", None),
        ("GET", f"/{person['id']}/history", None),
        ("DELETE", f"/{person['id']}/photo", None),
    ]
    for method, path, body in attempts:
        response = await call(registration_client, sa, method, path, body)
        assert response.status_code == 403, (method, path, response.text)
    upload = await registration_client.put(
        f"{P}/{person['id']}/photo", files={"file": ("p.jpg", JPEG, "image/jpeg")}, headers=headers(sa)
    )
    assert upload.status_code == 403
    photo = await call(registration_client, sa, "GET", f"/{person['id']}/photo")
    assert photo.status_code == 403 and photo.json()["code"] == "PHOTO_REVIEW_ONLY"
    assert (await ok(registration_client, a, "GET", f"/{person['id']}"))["full_name"] == "Bilal Ahmed"


async def test_guards_and_the_super_admin_have_no_access(
    registration_client: AsyncClient, ucp: dict[str, Any], super_admin: UUID
) -> None:
    person = await register(registration_client, ucp["a"], "Nadia")
    for path in ("", f"/{person['id']}", "/categories", "/fields", f"/{person['id']}/photo"):
        response = await call(registration_client, ucp["ga"], "GET", path)
        assert response.status_code == 403, path
        platform = await registration_client.get(f"{P}{path}", headers=platform_auth_header(super_admin))
        assert platform.status_code == 403, path
    guard_post = await call(registration_client, ucp["ga"], "POST", "", {"full_name": "G", "category_id": str(uuid4())})
    assert guard_post.status_code == 403


# --- Photo -------------------------------------------------------------------------------------------------------


async def test_admin_photo_upload_view_and_remove(
    registration_client: AsyncClient, ucp: dict[str, Any], storage_root
) -> None:
    a = ucp["a"]
    person = await register(registration_client, a, "Photo Person")
    bad = await registration_client.put(
        f"{P}/{person['id']}/photo", files={"file": ("x.gif", b"GIF89a....", "image/gif")}, headers=headers(a)
    )
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_PHOTO"
    up = await registration_client.put(
        f"{P}/{person['id']}/photo", files={"file": ("p.jpg", JPEG, "image/jpeg")}, headers=headers(a)
    )
    assert up.status_code == 200 and up.json()["has_photo"] is True
    got = await call(registration_client, a, "GET", f"/{person['id']}/photo")
    assert got.status_code == 200 and got.content == JPEG and got.headers["cache-control"] == "no-store"
    files = list((storage_root / str(ucp["org"]) / str(ucp["campus_a"]) / "personnel").rglob("*.jpg"))
    assert len(files) == 1
    await ok(registration_client, a, "DELETE", f"/{person['id']}/photo")
    assert not list((storage_root / str(ucp["org"]) / str(ucp["campus_a"]) / "personnel").rglob("*.jpg"))
    assert (await call(registration_client, a, "GET", f"/{person['id']}/photo")).status_code == 404


# --- Campus isolation ---------------------------------------------------------------------------------------------


async def test_campus_isolation_through_the_api(registration_client: AsyncClient, ucp: dict[str, Any]) -> None:
    a, b, sb = ucp["a"], ucp["b"], ucp["sb"]
    mine = await register(registration_client, a, "Campus A Person", external_id="A-1")
    await register(registration_client, b, "Campus B Person", external_id="B-1")
    for user in (b, sb):
        listing = await ok(registration_client, user, "GET", "?q=campus")
        assert [i["full_name"] for i in listing["items"]] == ["Campus B Person"]
        assert (await ok(registration_client, user, "GET", "?q=A-1"))["total"] == 0
        assert (await call(registration_client, user, "GET", f"/{mine['id']}")).status_code == 404
    cats_b = await category_ids(registration_client, b)
    for method, path, body in (
        ("PUT", f"/{mine['id']}", {"full_name": "Hijack", "category_id": cats_b["OTHER"]}),
        ("POST", f"/{mine['id']}/deactivate", None),
        ("GET", f"/{mine['id']}/history", None),
        ("GET", f"/{mine['id']}/photo", None),
    ):
        response = await call(registration_client, b, method, path, body)
        assert response.status_code == 404, (method, path)
    # A category ID of campus A cannot be used in campus B.
    cats_a = await category_ids(registration_client, a)
    foreign = await call(
        registration_client, b, "POST", "", {"full_name": "X", "category_id": cats_a["OTHER"], "phone": "03001234567"}
    )
    assert foreign.status_code == 422 and foreign.json()["errors"][0]["code"] == "UNKNOWN_CATEGORY"


async def test_campus_isolation_in_the_database(
    registration_client: AsyncClient, ucp: dict[str, Any], app_role_engine
) -> None:
    a = ucp["a"]
    person = await register(registration_client, a, "Database Person")
    cats = await category_ids(registration_client, a)
    count_sql = "SELECT count(*) FROM personnel"
    assert (await run_as(app_role_engine, a["id"], count_sql))[0][0][0] == 1
    for user in ("sa", "ga", "b", "sb"):  # Supervisors read only through the directory function
        assert (await run_as(app_role_engine, ucp[user]["id"], count_sql))[0][0][0] == 0, user
    directory = "SELECT count(*) FROM app_personnel_directory(NULL, NULL, NULL, NULL, NULL, NULL, NULL, 50, 0)"
    assert (await run_as(app_role_engine, ucp["sa"]["id"], directory))[0][0][0] == 1
    assert (await run_as(app_role_engine, ucp["ga"]["id"], directory))[0][0][0] == 0
    assert (await run_as(app_role_engine, ucp["b"]["id"], directory))[0][0][0] == 0
    contact = "SELECT email FROM app_personnel_directory(NULL, NULL, NULL, NULL, NULL, NULL, NULL, 50, 0)"
    assert (await run_as(app_role_engine, ucp["sa"]["id"], contact))[0][0][0] is None
    insert = (
        "INSERT INTO personnel (id, organization_id, campus_id, personnel_code, full_name, category_id, created_via) "
        f"VALUES (gen_random_uuid(), '{ucp['org']}', '{ucp['campus_a']}', 'PSN-ABCDEFGHJK', 'Sneaky', "
        f"'{cats['OTHER']}', 'MANUAL')"
    )
    for user in ("sa", "ga", "b"):
        with pytest.raises(DBAPIError):
            await run_as(app_role_engine, ucp[user]["id"], insert)
    update = f"UPDATE personnel SET full_name = 'Changed' WHERE id = '{person['id']}'"
    assert (await run_as(app_role_engine, ucp["sa"]["id"], update))[1] == 0
    assert (await run_as(app_role_engine, ucp["b"]["id"], update))[1] == 0
    with pytest.raises(DBAPIError):  # no DELETE right at all: records are deactivated, never deleted
        await run_as(app_role_engine, a["id"], f"DELETE FROM personnel WHERE id = '{person['id']}'")
    trail = f"SELECT count(*) FROM app_personnel_audit_trail('PERSONNEL', '{person['id']}')"
    assert (await run_as(app_role_engine, a["id"], trail))[0][0][0] == 1
    assert (await run_as(app_role_engine, ucp["sa"]["id"], trail))[0][0][0] == 0
    alerts_trail = f"SELECT count(*) FROM app_personnel_audit_trail('ALERT', '{person['id']}')"
    assert (await run_as(app_role_engine, a["id"], alerts_trail))[0][0][0] == 0  # only this module's entries


# --- Campus deletion and export --------------------------------------------------------------------------------


async def test_campus_deletion_erases_personnel_and_the_export_includes_it(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    c = ucp["c"]
    await register(registration_client, c, "Campus C Person", external_id="C-1", notes="kept in the export")
    started = await start_deletion(registration_client, c)
    export = (
        await registration_client.get(f"{ACCOUNT}/deletion/{started.json()['id']}/export", headers=headers(c))
    ).json()
    assert [p["full_name"] for p in export["personnel"]] == ["Campus C Person"]
    assert export["personnel"][0]["personnel_id"].startswith("PSN-") and len(export["personnel_categories"]) == 8
    assert export["area_access"] == [] and export["personnel_imports"] == []
    await registration_client.post(
        f"{ACCOUNT}/deletion/{started.json()['id']}/verify",
        json={"otp": outbox.last_code(c["email"])},
        headers=headers(c),
    )
    campus = ucp["campus_c"]
    for table in ("personnel", "personnel_categories", "personnel_custom_fields", "personnel_import_batches"):
        assert count(owner_query, f"SELECT 1 FROM {table} WHERE campus_id = %(c)s", c=campus) == 0, table
    assert count(owner_query, "SELECT 1 FROM personnel WHERE campus_id = %(c)s", c=ucp["campus_a"]) == 0
    assert count(owner_query, "SELECT 1 FROM personnel_categories WHERE campus_id = %(c)s", c=ucp["campus_a"]) == 8


async def test_deleting_an_admin_account_keeps_other_campuses_personnel(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    await register(registration_client, ucp["a"], "Survivor")
    await register(registration_client, ucp["c"], "Erased")
    await delete_account(registration_client, outbox, ucp["c"])
    assert [r[0] for r in owner_query("SELECT full_name FROM personnel")] == ["Survivor"]
