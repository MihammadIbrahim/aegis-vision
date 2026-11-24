"""Admin profile, ID regeneration, User Records & Rejoining and permanent deletion (migration 0029, decision
D-024): password and email codes, phone changes, Admin and Guard IDs (Supervisor IDs stay permanent), status
history and activity, CSV export, rejoining the same account, erasing a former member's personal data while
history stays linked, campus isolation and refusals."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import csv
import io
from typing import Any

from httpx import AsyncClient

from tests.conftest import platform_auth_header
from tests.fakes import FakeEmailSender
from tests.integration.test_ownership_and_deletion import OTHER_PASSWORD, count, headers, one, ucp  # noqa: F401

API = "/api/v1"
PROFILE = f"{API}/organization-admin/profile"
RECORDS = f"{API}/user-records"


async def call(client: AsyncClient, user: dict[str, Any], method: str, path: str, body: Any = None):  # type: ignore[no-untyped-def]
    return await client.request(method, f"{API}{path}", json=body, headers=headers(user))


async def test_admin_profile_changes_need_password_and_codes_and_include_the_phone(
    registration_client: AsyncClient, outbox: FakeEmailSender, ucp: dict[str, Any], owner_query
) -> None:
    client, a = registration_client, ucp["a"]
    profile = (await client.get(PROFILE, headers=headers(a))).json()
    assert profile["admin_id"] == a["display_id"] and profile["is_main_admin"] is True
    assert profile["campus_name"] and profile["organization_name"] == "UCP" and profile["role"] == "ADMIN"

    wrong = await client.post(f"{PROFILE}/changes", json={"current_password": "Wrong-pass-2026", "full_name": "M S"},
                              headers=headers(a))  # fmt: skip
    assert wrong.status_code == 403
    nothing = await client.post(f"{PROFILE}/changes", json={"current_password": a["password"]}, headers=headers(a))
    assert nothing.status_code == 422 and nothing.json()["code"] == "NO_CHANGES"
    started = await client.post(
        f"{PROFILE}/changes",
        json={"current_password": a["password"], "full_name": "Maryam S.", "phone_number": "+92 333 000 1111",
              "email": "maryam.new@ucp.example.com"},
        headers=headers(a),
    )  # fmt: skip
    assert started.status_code == 200, started.text
    change = started.json()
    assert change["new_email"] is not None
    # Nothing changes until both codes are verified.
    assert one(owner_query, "SELECT full_name FROM users WHERE id = %(u)s", u=a["id"]) != "Maryam S."
    only_one = await client.post(f"{PROFILE}/changes/{change['change_id']}/verify",
                                 json={"otp": outbox.last_code(a["email"])}, headers=headers(a))  # fmt: skip
    assert only_one.status_code in (400, 409, 422)
    done = await client.post(
        f"{PROFILE}/changes/{change['change_id']}/verify",
        json={"otp": outbox.last_code(a["email"]), "new_email_otp": outbox.last_code("maryam.new@ucp.example.com")},
        headers=headers(a),
    )
    assert done.status_code == 200, done.text
    updated = done.json()["profile"]
    assert (updated["full_name"], updated["phone_number"], updated["email"]) == (
        "Maryam S.",
        "+923330001111",
        "maryam.new@ucp.example.com",
    )
    # Supervisors cannot use the Admin profile; their own profile has no phone change.
    assert (await client.get(PROFILE, headers=headers(ucp["sa"]))).status_code == 403
    phone = await client.post(f"{API}/supervisor/profile/changes",
                              json={"current_password": OTHER_PASSWORD, "phone_number": "+923000000000"},
                              headers=headers(ucp["sa"]))  # fmt: skip
    assert phone.status_code == 422


async def test_admin_and_guard_ids_are_regenerated_with_the_password(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    client, a, sa, ga = registration_client, ucp["a"], ucp["sa"], ucp["ga"]
    wrong = await client.post(f"{PROFILE}/regenerate-id", json={"current_password": "x"}, headers=headers(a))
    assert wrong.status_code == 403
    new_admin = (await client.post(f"{PROFILE}/regenerate-id", json={"current_password": a["password"]},
                                   headers=headers(a))).json()["display_id"]  # fmt: skip
    assert new_admin != a["display_id"] and len(new_admin) == 17
    assert one(owner_query, "SELECT display_id FROM users WHERE id = %(u)s", u=a["id"]) == new_admin
    assert (await client.get(PROFILE, headers=headers(a))).status_code == 200  # the session continues

    guard = await call(
        client, sa, "POST", f"/staff/guards/{ga['id']}/regenerate-id", {"current_password": OTHER_PASSWORD}
    )
    assert guard.status_code == 200, guard.text
    new_guard = guard.json()["display_id"]
    assert new_guard != ga["display_id"]
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'GUARD_ID_REGENERATED' "
                 "AND user_id = %(u)s", u=ga["id"]) == 1  # fmt: skip
    assert count(owner_query, "SELECT 1 FROM user_notifications WHERE kind = 'GUARD_ID_REGENERATED' "
                 "AND user_id = %(u)s", u=a["id"]) == 1  # the Admin hears about a Supervisor's change  # fmt: skip
    supervisor = await call(client, a, "POST", f"/staff/guards/{sa['id']}/regenerate-id",
                            {"current_password": a["password"]})  # fmt: skip
    assert supervisor.status_code == 403 and supervisor.json()["code"] == "SUPERVISOR_ID_PERMANENT"
    other = await call(client, ucp["b"], "POST", f"/staff/guards/{ga['id']}/regenerate-id",
                       {"current_password": OTHER_PASSWORD})  # fmt: skip
    assert other.status_code == 404
    assert (await call(client, ga, "POST", f"/staff/guards/{ga['id']}/regenerate-id",
                       {"current_password": OTHER_PASSWORD})).status_code == 403  # fmt: skip
    actions = [r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE action LIKE '%%ID_REGEN%%' "
                                         "ORDER BY occurred_at")]  # fmt: skip
    assert actions == ["PROFILE_ID_REGENERATED", "GUARD_ID_REGENERATED"]


async def test_records_rejoining_and_export(registration_client: AsyncClient, ucp: dict[str, Any], owner_query) -> None:
    client, a, ga = registration_client, ucp["a"], ucp["ga"]
    page = (await client.get(RECORDS, headers=headers(a))).json()
    assert {r["display_id"] for r in page["items"]} == {ucp["sa"]["display_id"], ga["display_id"]}
    assert (await client.get(RECORDS, headers=headers(ucp["sa"]))).status_code == 403
    guards = (await client.get(RECORDS, params={"role": "GUARD"}, headers=headers(a))).json()
    assert [r["role"] for r in guards["items"]] == ["GUARD"]

    deactivated = await call(client, a, "POST", f"/staff/{ga['id']}/deactivate", {"current_password": a["password"]})
    assert deactivated.status_code == 200, deactivated.text
    left = (await client.get(RECORDS, params={"status": "LEFT"}, headers=headers(a))).json()
    assert [r["user_id"] for r in left["items"]] == [str(ga["id"])]

    found = (await client.get(f"{RECORDS}/lookup", params={"display_id": ga["display_id"].lower()},
                              headers=headers(a))).json()  # fmt: skip
    assert found["user_id"] == str(ga["id"]) and found["status"] == "LEFT"
    rejoined = await call(client, a, "POST", f"/user-records/{ga['id']}/rejoin", {
        "full_name": "Gul A. Returned", "phone_number": "+92 300 555 0000", "new_password": "Returning-pass-2026",
        "current_password": a["password"]})  # fmt: skip
    assert rejoined.status_code == 200, rejoined.text
    assert rejoined.json()["status"] == "ACTIVE" and rejoined.json()["full_name"] == "Gul A. Returned"
    assert count(owner_query, "SELECT 1 FROM users WHERE display_id = %(d)s", d=ga["display_id"]) == 1  # no duplicate
    again = await call(client, a, "POST", f"/user-records/{ga['id']}/rejoin",
                       {"full_name": "X Y", "current_password": a["password"]})  # fmt: skip
    assert again.status_code == 409

    detail = (await client.get(f"{RECORDS}/{ga['id']}", headers=headers(a))).json()
    assert [h["event"] for h in detail["history"]] == ["CREATED", "DEACTIVATED", "REACTIVATED"]
    assert {x["action"] for x in detail["activity"]} >= {"STAFF_DEACTIVATED", "STAFF_REJOINED"}

    export = await client.get(f"{RECORDS}/export", headers=headers(a))
    rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
    assert rows[0][0] == "user_id" and len(rows) == 3
    assert count(owner_query, "SELECT 1 FROM organization_audit_logs WHERE action = 'USER_RECORDS_EXPORTED'") == 1


async def test_permanent_deletion_erases_personal_data_and_keeps_history(
    registration_client: AsyncClient, ucp: dict[str, Any], owner_query, super_admin
) -> None:
    client, a, sa = registration_client, ucp["a"], ucp["sa"]
    case = (await call(client, sa, "POST", "/incidents", {"title": "Opened by the Supervisor"})).json()
    active = await call(client, a, "POST", f"/user-records/{sa['id']}/permanent-delete",
                        {"current_password": a["password"], "confirmation": "DELETE"})  # fmt: skip
    assert active.status_code == 409 and active.json()["code"] == "STILL_ACTIVE"
    await call(client, a, "POST", f"/staff/{sa['id']}/deactivate", {"current_password": a["password"]})
    unconfirmed = await call(client, a, "POST", f"/user-records/{sa['id']}/permanent-delete",
                             {"current_password": a["password"], "confirmation": "delete it"})  # fmt: skip
    assert unconfirmed.status_code == 422
    by_branch = await call(client, ucp["b"], "POST", f"/user-records/{sa['id']}/permanent-delete",
                           {"current_password": OTHER_PASSWORD, "confirmation": "DELETE"})  # fmt: skip
    assert by_branch.status_code == 404
    deleted = await call(client, a, "POST", f"/user-records/{sa['id']}/permanent-delete",
                         {"current_password": a["password"], "confirmation": "DELETE"})  # fmt: skip
    assert deleted.status_code == 204, deleted.text

    row = owner_query("SELECT full_name, username, phone, email LIKE 'deleted-%%', deleted_at IS NOT NULL, display_id "
                      "FROM users WHERE id = %(u)s", {"u": sa["id"]})  # fmt: skip
    assert row == [("Deleted user", None, None, True, True, sa["display_id"])]
    assert count(owner_query, "SELECT 1 FROM user_credentials WHERE user_id = %(u)s", u=sa["id"]) == 0
    assert one(owner_query, "SELECT created_by_name FROM incidents WHERE id = %(i)s", i=case["id"]) == "Deleted user"
    assert one(owner_query, "SELECT actor_name FROM incident_events WHERE incident_id = %(i)s", i=case["id"]) == (
        "Deleted user"
    )
    assert "Sana A" not in str(owner_query("SELECT metadata FROM organization_audit_logs "
                                            "WHERE action = 'USER_PERMANENTLY_DELETED'"))  # fmt: skip
    again = await call(client, a, "POST", f"/user-records/{sa['id']}/permanent-delete",
                       {"current_password": a["password"], "confirmation": "DELETE"})  # fmt: skip
    assert again.status_code == 409
    hidden = (await client.get(RECORDS, headers=headers(a))).json()
    assert str(sa["id"]) not in {r["user_id"] for r in hidden["items"]}
    shown = (await client.get(RECORDS, params={"include_deleted": "true"}, headers=headers(a))).json()
    [record] = [r for r in shown["items"] if r["user_id"] == str(sa["id"])]
    assert record["deleted"] is True and record["email"] is None
    rejoin = await call(client, a, "POST", f"/user-records/{sa['id']}/rejoin",
                        {"full_name": "Back Again", "current_password": a["password"]})  # fmt: skip
    assert rejoin.status_code == 409 and rejoin.json()["code"] == "DELETED"
    assert (await client.get(RECORDS, headers=platform_auth_header(super_admin))).status_code == 403
