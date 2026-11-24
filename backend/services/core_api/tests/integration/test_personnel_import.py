"""Excel/CSV personnel import end to end (migration 0017, decision D-016): template, upload, flexible
column mapping, validation, the annotated error workbook, all-or-nothing confirmation, revalidation
at commit, history, rollback by batch, Admin-only access, campus isolation, limits and large files."""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)

import csv
import hashlib
import io
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from openpyxl import Workbook, load_workbook

from tests.conftest import DETECTION_TOKEN, DatabaseUrls, _client_for
from tests.fakes import FakeEmailSender
from tests.integration.test_ownership_and_deletion import (
    count,
    headers,
    one,
    ucp,  # noqa: F401  (a fixture, used by name)
)
from tests.integration.test_personnel import register

IMPORTS = "/api/v1/personnel-imports"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# A university's own column names (not the template's).
UNI_HEADERS = ["Student Name", "Registration Number", "Type", "E-mail", "Mobile", "Degree Program", "Hobby"]


def workbook(rows: list[list[Any]]) -> bytes:
    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def csv_bytes(rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


async def upload(client: AsyncClient, user: dict[str, Any], data: bytes, name: str = "people.xlsx", **form: Any):  # type: ignore[no-untyped-def]
    mime = XLSX if name.endswith(".xlsx") else "text/csv"
    return await client.post(IMPORTS, files={"file": (name, data, mime)}, data=form, headers=headers(user))


async def uploaded(client: AsyncClient, user: dict[str, Any], data: bytes, name: str = "people.xlsx") -> dict:
    response = await upload(client, user, data, name)
    assert response.status_code == 201, response.text
    return response.json()


async def map_columns(client: AsyncClient, user: dict[str, Any], batch: dict, targets: list[str], **extra: Any):  # type: ignore[no-untyped-def]
    body = {"columns": [{"index": i, "target": t} for i, t in enumerate(targets)], **extra}
    return await client.put(f"{IMPORTS}/{batch['batch']['id']}/mapping", json=body, headers=headers(user))


async def validated(client: AsyncClient, user: dict[str, Any], batch: dict, targets: list[str], **extra: Any) -> dict:
    response = await map_columns(client, user, batch, targets, **extra)
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(client: AsyncClient, user: dict[str, Any], batch_id: str, acknowledge: bool = False):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{IMPORTS}/{batch_id}/confirm", json={"acknowledge_warnings": acknowledge}, headers=headers(user)
    )


def people(owner_query, campus) -> int:  # type: ignore[no-untyped-def]
    return count(owner_query, "SELECT 1 FROM personnel WHERE campus_id = %(c)s", c=campus)


UNI_TARGETS = ["full_name", "external_id", "category", "email", "phone", "custom:program", "IGNORE"]


async def program_field(client: AsyncClient, admin: dict[str, Any]) -> None:
    response = await client.post(
        "/api/v1/personnel/custom-fields",
        headers=headers(admin),
        json={"key": "program", "label": "Academic Program", "field_type": "TEXT"},
    )
    assert response.status_code == 201, response.text


# --- Template ----------------------------------------------------------------------------------------------------


async def test_the_template_is_generated_for_the_admins_campus(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    await program_field(registration_client, ucp["a"])
    response = await registration_client.get(f"{IMPORTS}/template.xlsx", headers=headers(ucp["a"]))
    assert response.status_code == 200 and response.headers["content-type"] == XLSX
    assert "attachment" in response.headers["content-disposition"]
    book = load_workbook(io.BytesIO(response.content))
    names = [c.value for c in book["Personnel"][1] if c.value]
    assert names[0] == "Full Name *" and "Institutional ID" in names and "Academic Program" in names
    assert not any("organization" in n.lower() for n in names)
    for user in ("sa", "ga"):
        assert (
            await registration_client.get(f"{IMPORTS}/template.xlsx", headers=headers(ucp[user]))
        ).status_code == 403


# --- The full workflow ------------------------------------------------------------------------------------------


async def test_a_valid_file_with_its_own_column_names_is_imported_after_confirmation(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    await program_field(registration_client, a)
    rows = [
        UNI_HEADERS,
        ["Ali Raza", "F23-001", "Student", "ali@example.com", "0300 1234567", "BSSE", "Chess"],
        ["Dr. Hina", "FAC-9", "faculty", "hina@example.com", "", "", ""],
        ["Omar", "EMP-4", "Employee", "omar@example.com", "03007654321", "", "Cricket"],
    ]
    batch = await uploaded(registration_client, a, workbook(rows))
    assert batch["batch"]["status"] == "UPLOADED" and batch["sheets"] == ["Sheet"]
    suggested = [(c["header"], c["target"], c["confidence"], c["confirmed"]) for c in batch["columns"]]
    assert suggested == [
        ("Student Name", "full_name", "SYNONYM", False),
        ("Registration Number", "external_id", "SYNONYM", False),
        ("Type", "category", "SYNONYM", False),
        ("E-mail", "email", "SYNONYM", False),
        ("Mobile", "phone", "SYNONYM", False),
        ("Degree Program", None, "NONE", False),  # suggestions are never assumed: the Admin decides
        ("Hobby", None, "NONE", False),
    ]
    assert {"full_name", "category", "custom:program", "IGNORE"} <= {t["target"] for t in batch["targets"]}
    assert people(owner_query, ucp["campus_a"]) == 0  # nothing is written before confirmation

    result = await validated(registration_client, a, batch, UNI_TARGETS, save_profile_as="University list")
    assert result["batch"]["status"] == "VALIDATED" and result["batch"]["total_rows"] == 3
    assert result["batch"]["valid_rows"] == 3 and result["batch"]["error_count"] == 0
    assert all(c["confirmed"] for c in result["columns"])
    preview = (await registration_client.get(f"{IMPORTS}/{batch['batch']['id']}/preview", headers=headers(a))).json()
    assert [(p["row_number"], p["values"]["full_name"], p["category"]) for p in preview] == [
        (2, "Ali Raza", "Student"),
        (3, "Dr. Hina", "Faculty"),
        (4, "Omar", "Employee"),
    ]
    assert preview[0]["values"]["phone"] == "03001234567" and preview[0]["values"]["custom_attributes"] == {
        "program": "BSSE"
    }
    assert people(owner_query, ucp["campus_a"]) == 0

    done = await confirm(registration_client, a, batch["batch"]["id"])
    assert done.status_code == 200, done.text
    assert done.json()["batch"]["status"] == "IMPORTED" and done.json()["batch"]["imported_count"] == 3
    stored = owner_query(
        "SELECT full_name, created_via, import_batch_id, organization_id, campus_id, personnel_code "
        "FROM personnel ORDER BY full_name"
    )
    assert [r[0] for r in stored] == ["Ali Raza", "Dr. Hina", "Omar"]
    assert all(r[1] == "IMPORT" and str(r[2]) == batch["batch"]["id"] for r in stored)
    assert all((r[3], r[4]) == (ucp["org"], ucp["campus_a"]) for r in stored)  # campus from the session
    assert len({r[5] for r in stored}) == 3
    assert "Chess" not in str(owner_query("SELECT custom_attributes FROM personnel"))  # the ignored column
    again = await confirm(registration_client, a, batch["batch"]["id"])
    assert again.status_code == 409 and again.json()["code"] == "IMPORT_ALREADY_CONFIRMED"

    history = (await registration_client.get(IMPORTS, headers=headers(a))).json()
    assert history[0]["original_filename"] == "people.xlsx" and history[0]["uploaded_by"] == "Maryam Siddiqui"
    assert history[0]["confirmed_by"] == "Maryam Siddiqui" and history[0]["imported_count"] == 3
    actions = [
        r[0]
        for r in owner_query(
            "SELECT action FROM organization_audit_logs WHERE target_id = %(b)s ORDER BY occurred_at",
            {"b": batch["batch"]["id"]},
        )
    ]
    assert actions == ["PERSONNEL_IMPORT_UPLOADED", "PERSONNEL_IMPORT_VALIDATED", "PERSONNEL_IMPORT_CONFIRMED"]
    ali = one(owner_query, "SELECT id FROM personnel WHERE full_name = 'Ali Raza'")
    trail = (await registration_client.get(f"/api/v1/personnel/{ali}/history", headers=headers(a))).json()
    assert [t["action"] for t in trail] == ["PERSONNEL_IMPORT_CONFIRMED"]  # a person's history shows their import

    # The saved mapping profile is suggested for the next file with the same headers.
    second = await uploaded(
        registration_client, a, workbook([UNI_HEADERS, ["Zara", "F23-002", "Student", "", "", "", ""]])
    )
    assert [c["target"] for c in second["columns"]] == UNI_TARGETS
    assert {c["confidence"] for c in second["columns"]} == {"PROFILE"}


async def test_blocking_errors_import_nothing_and_produce_an_annotated_workbook(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    a = ucp["a"]
    rows = [
        ["Name", "Category", "Institutional ID", "Email"],
        ["Good Person", "Employee", "E-1", "good@example.com"],
        ["", "Employee", "E-2", "x@example.com"],
        ["Bad Email", "Employee", "E-3", "not-an-email"],
        ["Missing ID", "Student", "", "m@example.com"],
        ["Twin", "Employee", "E-1", "twin@example.com"],
        ["Typo", "Studnt", "S-9", "t@example.com"],
    ]
    data = workbook(rows)
    batch = await uploaded(registration_client, a, data, "staff.xlsx")
    result = await validated(registration_client, a, batch, ["full_name", "category", "external_id", "email"])
    info = result["batch"]
    assert info["status"] == "INVALID" and info["error_count"] == 5 and info["valid_rows"] == 1
    assert info["has_annotated_workbook"] is True
    issues = (
        await registration_client.get(
            f"{IMPORTS}/{info['id']}/issues", params={"severity": "ERROR"}, headers=headers(a)
        )
    ).json()
    assert sorted((i["row_number"], i["code"]) for i in issues) == [
        (3, "REQUIRED"),
        (4, "INVALID_EMAIL"),
        (5, "REQUIRED_FOR_CATEGORY"),
        (6, "DUPLICATE_IN_FILE"),
        (7, "UNKNOWN_CATEGORY"),
    ]
    assert next(i for i in issues if i["row_number"] == 4)["column_name"] == "Email"
    assert all("not-an-email" not in i["message"] for i in issues)  # messages hold no cell values

    refused = await confirm(registration_client, a, info["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "IMPORT_NOT_VALIDATED"
    assert people(owner_query, ucp["campus_a"]) == 0  # not even the valid row

    annotated = await registration_client.get(f"{IMPORTS}/{info['id']}/annotated-workbook", headers=headers(a))
    assert annotated.status_code == 200 and annotated.headers["cache-control"] == "no-store"
    assert 'filename="staff-validation.xlsx"' in annotated.headers["content-disposition"]
    book = load_workbook(io.BytesIO(annotated.content))
    sheet = book["Annotated Data"]
    assert sheet["D4"].value == "not-an-email" and sheet["D4"].fill.fgColor.rgb.endswith("FFC7CE")
    assert "INVALID_EMAIL" in sheet["D4"].comment.text
    assert sheet["E2"].value == "OK" and "Validation Report" in book.sheetnames
    original = next((storage_root / str(ucp["org"]) / str(ucp["campus_a"])).rglob("original.xlsx"))
    assert hashlib.sha256(original.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest()

    # The Admin fixes the file and uploads it again: a new batch, validated again, then imported.
    fixed = [rows[0], rows[1], ["Second", "Employee", "E-2", "x@example.com"]]
    retry = await uploaded(registration_client, a, workbook(fixed), "staff-fixed.xlsx")
    assert retry["columns"][2]["target"] == "external_id"
    ok = await validated(registration_client, a, retry, ["full_name", "category", "external_id", "email"])
    assert ok["batch"]["status"] == "VALIDATED" and ok["batch"]["has_annotated_workbook"] is False
    assert (await confirm(registration_client, a, ok["batch"]["id"])).status_code == 200
    assert people(owner_query, ucp["campus_a"]) == 2


async def test_existing_records_unknown_columns_and_custom_fields(
    registration_client: AsyncClient, ucp: dict[str, Any]
) -> None:
    a = ucp["a"]
    await register(registration_client, a, "Already Here", external_id="EMP-7", email="here@example.com")
    await program_field(registration_client, a)
    rows = [
        ["Name", "Type", "Employee Code", "Email", "Program", "Shoe Size"],
        ["New", "Employee", "emp-7", "new@example.com", "", "42"],
        ["Other", "Employee", "EMP-8", "HERE@example.com", "", "40"],
    ]
    batch = await uploaded(registration_client, a, workbook(rows))
    assert batch["columns"][4]["target"] == "custom:program" and batch["columns"][5]["target"] is None
    incomplete = await map_columns(
        registration_client, a, batch, ["full_name", "category", "external_id", "email", "custom:program"]
    )
    assert incomplete.status_code == 422 and incomplete.json()["code"] == "MAPPING_INCOMPLETE"
    assert incomplete.json()["errors"] == [
        {
            "column_index": 5,
            "column_name": "Shoe Size",
            "code": "UNMAPPED_COLUMN",
            "message": "Choose a field for this column, or choose to ignore it.",
        }
    ]
    bad = await map_columns(
        registration_client, a, batch, ["full_name", "full_name", "external_id", "email", "custom:nope", "IGNORE"]
    )
    assert {e["code"] for e in bad.json()["errors"]} == {
        "DUPLICATE_TARGET",
        "UNKNOWN_TARGET",
        "REQUIRED_FIELD_UNMAPPED",
    }
    result = await validated(
        registration_client, a, batch, ["full_name", "category", "external_id", "email", "custom:program", "IGNORE"]
    )
    issues = (await registration_client.get(f"{IMPORTS}/{result['batch']['id']}/issues", headers=headers(a))).json()
    assert sorted((i["row_number"], i["field"], i["code"]) for i in issues if i["severity"] == "ERROR") == [
        (2, "external_id", "ALREADY_EXISTS"),
        (3, "email", "ALREADY_EXISTS"),
    ]


async def test_warnings_must_be_acknowledged_before_confirmation(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    rows = [["Name", "Category", "Phone", "End Date"], ["Guest One", "Visitor", 3001234567, "2020-01-01"]]
    batch = await uploaded(registration_client, a, workbook(rows))
    result = await validated(registration_client, a, batch, ["full_name", "category", "phone", "end_date"])
    assert result["batch"]["status"] == "VALIDATED" and result["batch"]["warning_count"] == 2
    assert result["batch"]["has_annotated_workbook"] is True  # warnings are marked in amber
    first = await confirm(registration_client, a, result["batch"]["id"])
    assert first.status_code == 422 and first.json()["code"] == "WARNINGS_NOT_ACKNOWLEDGED"
    assert people(owner_query, ucp["campus_a"]) == 0
    assert (await confirm(registration_client, a, result["batch"]["id"], acknowledge=True)).status_code == 200
    assert people(owner_query, ucp["campus_a"]) == 1


async def test_a_change_after_validation_rolls_the_whole_import_back(
    registration_client: AsyncClient,
    ucp: dict[str, Any],
    migrated_database: DatabaseUrls,
    storage_root: Path,
    owner_query,
) -> None:
    a = ucp["a"]
    rows = [["Name", "Category", "ID"]] + [[f"Person {n}", "Employee", f"E-{n}"] for n in range(1, 301)]
    # Chunks of 100 rows: rows 2-201 are already inserted when the conflict in row 251 is found.
    async with _client_for(
        migrated_database,
        email_sender=FakeEmailSender(),
        cctv_storage_root=storage_root,
        personnel_import_chunk_rows=100,
    ) as client:
        batch = await uploaded(client, a, workbook(rows))
        result = await validated(client, a, batch, ["full_name", "category", "external_id"])
        assert result["batch"]["status"] == "VALIDATED"
        # Someone registers row 251's person by hand before the import is confirmed.
        await register(registration_client, a, "Hand Made", external_id="E-250")
        failed = await confirm(client, a, result["batch"]["id"], acknowledge=True)
        assert failed.status_code == 409 and failed.json()["code"] == "IMPORT_REVALIDATION_FAILED"
        assert people(owner_query, ucp["campus_a"]) == 1  # the first 200 inserted rows were rolled back too
        state = (await client.get(f"{IMPORTS}/{result['batch']['id']}", headers=headers(a))).json()["batch"]
        assert state["status"] == "INVALID" and state["error_count"] == 1 and state["imported_count"] == 0
        assert state["failure_reason"].startswith("Nothing was imported")
        issues = (
            await client.get(f"{IMPORTS}/{state['id']}/issues", params={"severity": "ERROR"}, headers=headers(a))
        ).json()
        assert [(i["row_number"], i["code"]) for i in issues] == [(251, "ALREADY_EXISTS")]
        assert (await confirm(client, a, state["id"], acknowledge=True)).status_code == 409  # INVALID never imports


async def test_rollback_deactivates_the_batch_and_keeps_history(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    batch = await uploaded(
        registration_client,
        a,
        csv_bytes([["Name", "Category", "ID"], ["R1", "Employee", "R-1"], ["R2", "Employee", "R-2"]]),
        "people.csv",
    )
    result = await validated(registration_client, a, batch, ["full_name", "category", "external_id"])
    batch_id = result["batch"]["id"]
    assert (await confirm(registration_client, a, batch_id, acknowledge=True)).status_code == 200
    r1 = one(owner_query, "SELECT id FROM personnel WHERE external_id = 'R-1'")
    block = (
        await registration_client.post(
            "/api/v1/locations/buildings", json={"name": "Lab Block", "kind": "INDOOR"}, headers=headers(a)
        )
    ).json()["id"]
    lab = (
        await registration_client.post(
            f"/api/v1/locations/buildings/{block}/areas", json={"name": "Lab"}, headers=headers(a)
        )
    ).json()["id"]
    await registration_client.put(f"/api/v1/restricted-access/areas/{lab}", json={}, headers=headers(a))
    grant = await registration_client.post(
        "/api/v1/restricted-access/grants", json={"personnel_id": str(r1), "area_ids": [lab]}, headers=headers(a)
    )
    assert grant.status_code == 201
    supervisor = await registration_client.post(f"{IMPORTS}/{batch_id}/rollback", json={}, headers=headers(ucp["sa"]))
    assert supervisor.status_code == 403
    rolled = await registration_client.post(
        f"{IMPORTS}/{batch_id}/rollback", json={"reason": "Wrong file"}, headers=headers(a)
    )
    assert rolled.status_code == 200 and rolled.json()["batch"]["status"] == "ROLLED_BACK"
    assert rolled.json()["batch"]["rolled_back_count"] == 2
    rows = owner_query(
        "SELECT status, deactivation_reason FROM personnel WHERE import_batch_id = %(b)s", {"b": batch_id}
    )
    assert rows == [("INACTIVE", "Import rolled back: Wrong file")] * 2  # kept, never deleted
    assert one(owner_query, "SELECT status FROM personnel_area_access") == "REVOKED"
    again = await registration_client.post(f"{IMPORTS}/{batch_id}/rollback", json={}, headers=headers(a))
    assert again.status_code == 409


# --- Access and isolation --------------------------------------------------------------------------------------


async def test_only_organization_admins_can_import(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    a = ucp["a"]
    data = workbook([["Name", "Category"], ["P", "Other"]])
    batch = await uploaded(registration_client, a, data)
    batch_id = batch["batch"]["id"]
    for user in ("sa", "ga"):
        assert (await upload(registration_client, ucp[user], data)).status_code == 403
        for method, path in (
            ("GET", ""),
            ("GET", f"/{batch_id}"),
            ("PUT", f"/{batch_id}/mapping"),
            ("GET", f"/{batch_id}/issues"),
            ("GET", f"/{batch_id}/preview"),
            ("GET", f"/{batch_id}/annotated-workbook"),
            ("POST", f"/{batch_id}/confirm"),
            ("POST", f"/{batch_id}/rollback"),
            ("DELETE", f"/{batch_id}"),
            ("GET", "/mapping-profiles"),
        ):
            response = await registration_client.request(
                method,
                f"{IMPORTS}{path}",
                json={"columns": []} if method == "PUT" else None,
                headers=headers(ucp[user]),
            )
            assert response.status_code == 403, (user, method, path)
    assert count(owner_query, "SELECT 1 FROM personnel_import_batches") == 1


async def test_imports_are_campus_isolated(registration_client: AsyncClient, ucp: dict[str, Any], owner_query) -> None:
    a, b = ucp["a"], ucp["b"]
    batch = await uploaded(registration_client, a, workbook([["Name", "Category", "ID"], ["P", "Employee", "SAME-1"]]))
    for method, path in (
        ("GET", ""),
        ("PUT", "/mapping"),
        ("GET", "/issues"),
        ("POST", "/confirm"),
        ("GET", "/annotated-workbook"),
        ("DELETE", ""),
    ):
        response = await registration_client.request(
            method,
            f"{IMPORTS}/{batch['batch']['id']}{path}",
            json={"columns": [{"index": 0, "target": "full_name"}]} if method == "PUT" else None,
            headers=headers(b),
        )
        assert response.status_code == 404, (method, path)
    assert (await registration_client.get(IMPORTS, headers=headers(b))).json() == []
    # The same institutional ID can be imported in campus B: duplicates are checked per campus.
    await register(registration_client, a, "A Person", external_id="B-SAME")
    other = await uploaded(registration_client, b, workbook([["Name", "Category", "ID"], ["P", "Employee", "B-SAME"]]))
    result = await validated(registration_client, b, other, ["full_name", "category", "external_id"])
    assert result["batch"]["status"] == "VALIDATED"
    assert (await confirm(registration_client, b, result["batch"]["id"], acknowledge=True)).status_code == 200
    assert (
        one(owner_query, "SELECT campus_id FROM personnel WHERE import_batch_id = %(b)s", b=result["batch"]["id"])
        == ucp["campus_b"]
    )


# --- Files, limits and size ----------------------------------------------------------------------------------------


async def test_unsafe_files_are_refused_and_unconfirmed_imports_can_be_discarded(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    a = ucp["a"]
    for name, data, code in (
        ("x.xlsm", workbook([["Name"]]), "MACRO_WORKBOOK"),
        ("x.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "LEGACY_XLS"),
        ("x.pdf", b"%PDF-1.4", "UNSUPPORTED_FILE_TYPE"),
        ("x.xlsx", b"not a zip", "NOT_A_WORKBOOK"),
        ("x.csv", b"", "EMPTY_FILE"),
        ("x.csv", b"\n\n", "NO_HEADER_ROW"),
    ):
        response = await upload(registration_client, a, data, name)
        assert response.status_code == 422 and response.json()["code"] == code, (name, response.text)
    assert count(owner_query, "SELECT 1 FROM personnel_import_batches") == 0
    campus_folder = storage_root / str(ucp["org"]) / str(ucp["campus_a"]) / "personnel_imports"
    assert not campus_folder.exists() or not any(campus_folder.iterdir())  # refused files are not kept

    batch = await uploaded(registration_client, a, workbook([["Name", "Category"], ["P", "Other"]]))
    assert (
        await registration_client.delete(f"{IMPORTS}/{batch['batch']['id']}", headers=headers(a))
    ).status_code == 204
    state = (await registration_client.get(f"{IMPORTS}/{batch['batch']['id']}", headers=headers(a))).json()["batch"]
    assert state["status"] == "DISCARDED" and state["files_available"] is False
    assert not any(campus_folder.iterdir())
    gone = await map_columns(registration_client, a, batch, ["full_name", "category"])
    assert gone.status_code == 409


async def test_upload_limits_are_settings_and_refuse_the_whole_file(
    seed, ucp: dict[str, Any], migrated_database: DatabaseUrls, storage_root: Path, owner_query
) -> None:
    async with _client_for(
        migrated_database,
        email_sender=FakeEmailSender(),
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
        personnel_import_max_upload_bytes=10_000,
    ) as client:
        big = csv_bytes([["Name", "Category"]] + [[f"Person {n}", "Other"] for n in range(2000)])
        response = await upload(client, ucp["a"], big, "big.csv")
        assert response.status_code == 422 and response.json()["code"] == "FILE_TOO_LARGE"
        assert "PERSONNEL_IMPORT_MAX_UPLOAD_BYTES" in response.json()["detail"]
    assert count(owner_query, "SELECT 1 FROM personnel_import_batches") == 0


async def test_a_large_import_has_no_row_limit_and_is_complete(
    seed, ucp: dict[str, Any], migrated_database: DatabaseUrls, storage_root: Path, owner_query
) -> None:
    rows = [["Name", "Category", "ID", "Email"]] + [
        [f"Person {n}", "Employee", f"E{n:06d}", f"p{n}@example.com"] for n in range(20_000)
    ]
    async with _client_for(
        migrated_database,
        email_sender=FakeEmailSender(),
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
        personnel_import_chunk_rows=700,
    ) as client:
        batch = await uploaded(client, ucp["a"], csv_bytes(rows), "everyone.csv")
        result = await validated(client, ucp["a"], batch, ["full_name", "category", "external_id", "email"])
        assert result["batch"]["status"] == "VALIDATED" and result["batch"]["total_rows"] == 20_000
        done = await confirm(client, ucp["a"], result["batch"]["id"])
        assert done.status_code == 200 and done.json()["batch"]["imported_count"] == 20_000
    assert people(owner_query, ucp["campus_a"]) == 20_000  # no silent truncation
    assert one(owner_query, "SELECT count(DISTINCT personnel_code) FROM personnel") == 20_000
