"""Live monitoring, recordings, retention and the evidence vault (migration 0023, decision D-023):
service reports (camera status, segments, disk), campus isolation (API and database), Guard and
Super Admin denial, retention with password confirmation, evidence immunity, audit trail."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import shutil
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import DETECTION_TOKEN, DatabaseUrls, _client_for, run_as
from tests.fakes import FakeEmailSender
from tests.integration.test_ownership_and_deletion import (
    OTHER_PASSWORD,
    count,
    headers,
    one,
    ucp,  # noqa: F401  (a fixture, used by name)
)

API = "/api/v1"
SERVICE_TOKEN = "test-only-service-token"
SERVICE = {"X-Service-Token": SERVICE_TOKEN}

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed to make recordings")


@pytest.fixture
async def video_client(
    ucp: dict[str, Any], outbox: FakeEmailSender, migrated_database: DatabaseUrls, storage_root: Path
) -> AsyncIterator[AsyncClient]:
    """The tenant API with the service token and the detection token switched on."""
    async with _client_for(
        migrated_database,
        email_sender=outbox,
        cctv_storage_root=storage_root,
        detection_ingest_token=DETECTION_TOKEN,
        internal_service_token=SERVICE_TOKEN,
        recording_retention_default_days=7,
        live_snapshot_stale_seconds=5,
    ) as http:
        yield http


def camera_of(owner_query, campus: UUID) -> UUID:
    cam: UUID = one(owner_query, "SELECT id FROM cameras WHERE campus_id = %(c)s", c=campus)
    return cam


def make_mp4(path: Path, seconds: float = 2.0) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path.stat().st_size


async def register_segment(
    client: AsyncClient, root: Path, org: UUID, campus: UUID, camera: UUID, started: datetime, seconds: float = 2.0
) -> dict[str, Any]:
    name = started.strftime("%Y%m%dT%H%M%S") + ".mp4"
    relative = f"{org}/{campus}/{camera}/{started:%Y-%m-%d}/{name}"
    size = make_mp4(root / "recordings" / relative, seconds)
    response = await client.post(
        f"{API}/internal/recordings/segments",
        json={
            "camera_id": str(camera),
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=seconds)).isoformat(),
            "relative_path": relative,
            "size_bytes": size,
        },
        headers=SERVICE,
    )
    assert response.status_code == 201, response.text
    return {**response.json(), "relative_path": relative}


def audit(owner_query, action: str, actor: UUID) -> int:
    return count(
        owner_query,
        "SELECT 1 FROM organization_audit_logs WHERE action = %(a)s AND actor_user_id = %(u)s",
        a=action,
        u=actor,
    )


def notifications(owner_query, user_id: UUID, kind: str) -> int:
    return count(
        owner_query, "SELECT 1 FROM user_notifications WHERE user_id = %(u)s AND kind = %(k)s", u=user_id, k=kind
    )


# --- Service reports: cameras, status, segments, disk -------------------------------------------------


async def test_service_endpoints_need_the_service_token(
    video_client: AsyncClient, migrated_database: DatabaseUrls
) -> None:
    assert (await video_client.get(f"{API}/internal/cameras")).status_code == 401
    assert (await video_client.get(f"{API}/internal/cameras", headers={"X-Service-Token": "wrong"})).status_code == 401
    async with _client_for(migrated_database) as no_token:
        off = await no_token.get(f"{API}/internal/cameras", headers=SERVICE)
    assert off.status_code == 503 and off.json()["code"] == "SERVICE_INTAKE_DISABLED"


async def test_camera_status_reports_notify_staff_once_per_change(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    camera = camera_of(owner_query, ucp["campus_a"])
    cameras = (await video_client.get(f"{API}/internal/cameras", headers=SERVICE)).json()
    assert str(camera) in [c["camera_id"] for c in cameras] and all("stream_url" in c for c in cameras)

    async def report(state: str, error: str | None = None) -> dict[str, Any]:
        response = await video_client.post(
            f"{API}/internal/cameras/{camera}/status", json={"state": state, "error": error}, headers=SERVICE
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert await report("ONLINE") == {"outcome": "CHANGED", "previous_state": "UNKNOWN"}
    assert await report("ONLINE") == {"outcome": "UNCHANGED", "previous_state": "ONLINE"}
    assert await report("OFFLINE", "Connection refused") == {"outcome": "CHANGED", "previous_state": "ONLINE"}
    assert (await report("OFFLINE", "Connection refused"))["outcome"] == "UNCHANGED"
    assert (await report("ONLINE"))["outcome"] == "CHANGED"
    # Admin A and Supervisor SA (campus A) were told once about the outage and once about the recovery;
    # the Guard and campus B staff were not.
    for user in (ucp["a"], ucp["sa"]):
        assert notifications(owner_query, user["id"], "CAMERA_OFFLINE") == 1
        assert notifications(owner_query, user["id"], "CAMERA_ONLINE") == 1
    for user in (ucp["ga"], ucp["b"], ucp["sb"]):
        assert notifications(owner_query, user["id"], "CAMERA_OFFLINE") == 0
    listed = (await video_client.get(f"{API}/notifications", headers=headers(ucp["a"]))).json()
    offline = next(n for n in listed if n["kind"] == "CAMERA_OFFLINE")
    assert offline["camera_id"] == str(camera) and offline["alert_id"] is None

    # The status is visible to campus A staff on the live page, with the generic error only.
    live = (await video_client.get(f"{API}/live/cameras", headers=headers(ucp["sa"]))).json()
    mine = next(c for c in live if c["camera"]["id"] == str(camera))
    assert mine["status"]["state"] == "ONLINE" and mine["status"]["source"] == "INGESTION"
    assert mine["status"]["last_error"] is None and mine["status"]["last_seen_at"] is not None

    unknown = await video_client.post(
        f"{API}/internal/cameras/{uuid4()}/status", json={"state": "ONLINE"}, headers=SERVICE
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "CAMERA_NOT_FOUND"


async def test_segments_are_registered_in_the_cameras_folder_only(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    org, campus = ucp["org"], ucp["campus_a"]
    camera = camera_of(owner_query, campus)
    started = datetime.now(UTC) - timedelta(minutes=3)
    first = await register_segment(video_client, storage_root, org, campus, camera, started)
    assert first["outcome"] == "REGISTERED"
    again = await video_client.post(
        f"{API}/internal/recordings/segments",
        json={
            "camera_id": str(camera),
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=2)).isoformat(),
            "relative_path": first["relative_path"],
            "size_bytes": 1,
        },
        headers=SERVICE,
    )
    assert again.status_code == 201 and again.json() == {"segment_id": first["segment_id"], "outcome": "DUPLICATE"}
    other_camera = camera_of(owner_query, ucp["campus_b"])
    outside = await video_client.post(
        f"{API}/internal/recordings/segments",
        json={
            "camera_id": str(camera),
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=2)).isoformat(),
            "relative_path": f"{org}/{ucp['campus_b']}/{other_camera}/x.mp4",
            "size_bytes": 1,
        },
        headers=SERVICE,
    )
    assert outside.status_code == 422 and outside.json()["code"] == "PATH_OUTSIDE_CAMERA"
    for bad in ("/abs/path.mp4", f"{org}/{campus}/{camera}/../x.mp4"):
        response = await video_client.post(
            f"{API}/internal/recordings/segments",
            json={
                "camera_id": str(camera),
                "started_at": started.isoformat(),
                "ended_at": (started + timedelta(seconds=2)).isoformat(),
                "relative_path": bad,
                "size_bytes": 1,
            },
            headers=SERVICE,
        )
        assert response.status_code == 422 and response.json()["code"] == "INVALID_PATH", bad


async def test_disk_reports_warn_staff_on_level_changes_only(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query
) -> None:
    async def report(used: int) -> dict[str, Any]:
        body = {"total_bytes": 100, "used_bytes": used, "free_bytes": 100 - used}
        response = await video_client.post(f"{API}/internal/storage/report", json=body, headers=SERVICE)
        assert response.status_code == 200, response.text
        return response.json()

    assert await report(50) == {"level": "OK", "notified": False}
    assert await report(85) == {"level": "WARNING", "notified": True}
    assert await report(86) == {"level": "WARNING", "notified": False}
    assert await report(95) == {"level": "CRITICAL", "notified": True}
    assert await report(50) == {"level": "OK", "notified": False}
    assert await report(85) == {"level": "WARNING", "notified": True}  # a new episode
    # Admins and Supervisors of every campus are warned (Supervisor spec page 5, item 7); Guards are not.
    for staff in (ucp["a"], ucp["b"], ucp["c"], ucp["sa"], ucp["sb"]):
        assert notifications(owner_query, staff["id"], "STORAGE_WARNING") == 2
        assert notifications(owner_query, staff["id"], "STORAGE_CRITICAL") == 1
    for guard in (ucp["ga"], ucp["gb"], ucp["gc"]):
        assert notifications(owner_query, guard["id"], "STORAGE_WARNING") == 0
    overview = (await video_client.get(f"{API}/recordings/retention", headers=headers(ucp["sa"]))).json()
    assert overview["storage"]["level"] == "WARNING" and overview["storage"]["percent_used"] == 85.0
    assert overview["retention"] == {
        "value": 7, "unit": "DAYS", "seconds": 604800, "is_default": True,
        "updated_at": None, "updated_by": None, "updated_by_name": None,
    }  # fmt: skip


# --- Live monitoring ---------------------------------------------------------------------------------


async def test_live_frames_sessions_and_health_checks(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query, owner_execute, storage_root: Path
) -> None:
    org, campus = ucp["org"], ucp["campus_a"]
    camera = camera_of(owner_query, campus)
    frame = storage_root / "live" / str(org) / str(campus) / str(camera) / "latest.jpg"
    missing = await video_client.get(f"{API}/live/cameras/{camera}/frame", headers=headers(ucp["a"]))
    assert missing.status_code == 404 and missing.json()["code"] == "LIVE_FRAME_UNAVAILABLE"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"\xff\xd8\xff\xd9")
    served = await video_client.get(f"{API}/live/cameras/{camera}/frame", headers=headers(ucp["sa"]))
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"] == "no-store" and served.content == b"\xff\xd8\xff\xd9"
    import os

    old = (datetime.now(UTC) - timedelta(seconds=60)).timestamp()
    os.utime(frame, (old, old))
    stale = await video_client.get(f"{API}/live/cameras/{camera}/frame", headers=headers(ucp["sa"]))
    assert stale.status_code == 404 and stale.json()["code"] == "LIVE_FRAME_STALE"
    # Another campus and a Guard never see the frame; the Guard cannot even list cameras.
    assert (await video_client.get(f"{API}/live/cameras/{camera}/frame", headers=headers(ucp["b"]))).status_code == 404
    assert (await video_client.get(f"{API}/live/cameras/{camera}/frame", headers=headers(ucp["ga"]))).status_code == 403
    assert (await video_client.get(f"{API}/live/cameras", headers=headers(ucp["ga"]))).status_code == 403

    session = await video_client.post(
        f"{API}/live/sessions", json={"camera_ids": [str(camera)]}, headers=headers(ucp["a"])
    )
    assert session.status_code == 200 and [c["camera"]["id"] for c in session.json()] == [str(camera)]
    assert audit(owner_query, "LIVE_VIEW_STARTED", ucp["a"]["id"]) == 1
    foreign = await video_client.post(
        f"{API}/live/sessions",
        json={"camera_ids": [str(camera_of(owner_query, ucp["campus_b"]))]},
        headers=headers(ucp["a"]),
    )
    assert foreign.status_code == 404

    no_url = await video_client.post(f"{API}/live/cameras/{camera}/health-check", headers=headers(ucp["a"]))
    assert no_url.status_code == 422 and no_url.json()["code"] == "NO_STREAM_URL"
    owner_execute("UPDATE cameras SET stream_url = 'rtsp://127.0.0.1:1/stream' WHERE id = %(c)s", {"c": camera})
    checked = await video_client.post(f"{API}/live/cameras/{camera}/health-check", headers=headers(ucp["sa"]))
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result["reachable"] is False and "127.0.0.1" not in result["detail"]
    assert result["camera"]["status"]["state"] == "OFFLINE" and result["camera"]["status"]["source"] == "HEALTH_CHECK"
    assert audit(owner_query, "CAMERA_HEALTH_CHECKED", ucp["sa"]["id"]) == 1


# --- Recordings timeline, playback, isolation ------------------------------------------------------


async def test_timeline_playback_markers_and_isolation(
    video_client: AsyncClient,
    ucp: dict[str, Any],
    owner_query,
    storage_root: Path,
    app_role_engine,
    platform_role_engine,
) -> None:
    org, campus = ucp["org"], ucp["campus_a"]
    camera = camera_of(owner_query, campus)
    now = datetime.now(UTC)
    first = await register_segment(video_client, storage_root, org, campus, camera, now - timedelta(minutes=5))
    second = await register_segment(video_client, storage_root, org, campus, camera, now - timedelta(minutes=4))
    other = await register_segment(
        video_client,
        storage_root,
        org,
        ucp["campus_b"],
        camera_of(owner_query, ucp["campus_b"]),
        now - timedelta(minutes=5),
    )
    detected = (now - timedelta(minutes=4, seconds=59)).isoformat()
    alert = await video_client.post(
        f"{API}/internal/detections",
        json={"camera_id": str(camera), "detection_type": "WEAPON", "detected_at": detected},
        headers={"X-Detection-Token": DETECTION_TOKEN},
    )
    assert alert.status_code == 200, alert.text

    timeline = await video_client.get(f"{API}/recordings", headers=headers(ucp["sa"]))
    assert timeline.status_code == 200, timeline.text
    data = timeline.json()
    assert [s["id"] for s in data["segments"]] == [second["segment_id"], first["segment_id"]]  # newest first
    assert data["segments"][0]["duration_seconds"] == 2.0 and data["segments"][0]["camera_name"] == "Gate"
    assert [m["alert_id"] for m in data["markers"]] == [alert.json()["alert_id"]]
    assert data["markers"][0]["alert_type"] == "WEAPON" and data["markers"][0]["camera_id"] == str(camera)
    assert [c["camera"]["id"] for c in data["cameras"]] == [str(camera)]
    by_camera = (
        await video_client.get(f"{API}/recordings", params={"camera_id": str(camera)}, headers=headers(ucp["a"]))
    ).json()
    assert len(by_camera["segments"]) == 2
    window = (
        await video_client.get(
            f"{API}/recordings",
            params={"since": (now - timedelta(minutes=4, seconds=30)).isoformat(), "until": now.isoformat()},
            headers=headers(ucp["a"]),
        )
    ).json()
    assert [s["id"] for s in window["segments"]] == [second["segment_id"]]
    bad = await video_client.get(
        f"{API}/recordings", params={"since": now.isoformat(), "until": now.isoformat()}, headers=headers(ucp["a"])
    )
    assert bad.status_code == 422 and bad.json()["code"] == "INVALID_PERIOD"

    media = await video_client.get(f"{API}/recordings/{first['segment_id']}/media", headers=headers(ucp["sa"]))
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4" and media.content[4:8] == b"ftyp"
    assert audit(owner_query, "RECORDING_PLAYED", ucp["sa"]["id"]) == 1
    (storage_root / "recordings" / second["relative_path"]).unlink()
    gone = await video_client.get(f"{API}/recordings/{second['segment_id']}/media", headers=headers(ucp["sa"]))
    assert gone.status_code == 404 and gone.json()["code"] == "RECORDING_FILE_MISSING"

    # Campus B staff: their own segment only; campus A's is 404. Guards: 403.
    b_timeline = (await video_client.get(f"{API}/recordings", headers=headers(ucp["b"]))).json()
    assert [s["id"] for s in b_timeline["segments"]] == [other["segment_id"]] and b_timeline["markers"] == []
    assert (
        await video_client.get(f"{API}/recordings/{first['segment_id']}", headers=headers(ucp["b"]))
    ).status_code == 404
    assert (
        await video_client.get(f"{API}/recordings/{first['segment_id']}/media", headers=headers(ucp["b"]))
    ).status_code == 404
    assert (await video_client.get(f"{API}/recordings", headers=headers(ucp["ga"]))).status_code == 403
    assert (
        await video_client.get(f"{API}/recordings/{first['segment_id']}/media", headers=headers(ucp["ga"]))
    ).status_code == 403

    # The database repeats the rules: the app role sees only its own campus, cannot delete, and the
    # platform (Super Admin) role has no access to any of the new tables.
    rows, _ = await run_as(app_role_engine, ucp["b"]["id"], "SELECT id FROM recording_segments")
    assert [r[0] for r in rows] == [UUID(other["segment_id"])]
    rows, _ = await run_as(app_role_engine, ucp["ga"]["id"], "SELECT id FROM recording_segments")
    assert rows == []
    with pytest.raises(DBAPIError, match="permission denied"):
        await run_as(app_role_engine, ucp["a"]["id"], "DELETE FROM recording_segments")
    for table in (
        "recording_segments",
        "camera_status",
        "campus_retention_settings",
        "evidence_items",
        "storage_status",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with platform_role_engine.connect() as conn:
                await conn.execute(text(f"SELECT * FROM {table} LIMIT 1"))
    for function in ("app_service_cameras()", "app_recording_segments_expired(1, 1)", "app_storage_status()"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with platform_role_engine.connect() as conn:
                await conn.execute(text(f"SELECT * FROM {function}"))


# --- Retention ---------------------------------------------------------------------------------------


async def test_admins_and_supervisors_set_retention_with_their_password_and_it_drives_expiry(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query, storage_root: Path
) -> None:
    org, campus = ucp["org"], ucp["campus_a"]
    camera = camera_of(owner_query, campus)
    now = datetime.now(UTC)
    old = await register_segment(video_client, storage_root, org, campus, camera, now - timedelta(minutes=10))
    fresh = await register_segment(video_client, storage_root, org, campus, camera, now - timedelta(seconds=30))
    other = await register_segment(
        video_client,
        storage_root,
        org,
        ucp["campus_b"],
        camera_of(owner_query, ucp["campus_b"]),
        now - timedelta(hours=2),
    )
    expired = (await video_client.get(f"{API}/internal/recordings/expired", headers=SERVICE)).json()
    assert expired == []  # the 7-day default applies everywhere

    # Requirements 17 and the Supervisor spec (page 8): Admins and Supervisors set the retention, each with
    # their own password. Guards cannot.
    body = {"value": 5, "unit": "MINUTES", "current_password": OTHER_PASSWORD}
    guard = await video_client.put(f"{API}/recordings/retention", json=body, headers=headers(ucp["ga"]))
    assert guard.status_code == 403
    sup_wrong = await video_client.put(
        f"{API}/recordings/retention", json={**body, "current_password": "nope"}, headers=headers(ucp["sa"])
    )
    assert sup_wrong.status_code == 403 and sup_wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    by_supervisor = await video_client.put(
        f"{API}/recordings/retention", json={**body, "value": 2, "unit": "HOURS"}, headers=headers(ucp["sa"])
    )
    assert by_supervisor.status_code == 200, by_supervisor.text
    assert by_supervisor.json()["retention"]["seconds"] == 7200
    assert by_supervisor.json()["retention"]["updated_by"] == str(ucp["sa"]["id"])
    assert audit(owner_query, "RECORDING_RETENTION_CHANGED", ucp["sa"]["id"]) == 1
    sup_metadata = one(
        owner_query,
        "SELECT metadata FROM organization_audit_logs WHERE action = 'RECORDING_RETENTION_CHANGED' "
        "AND actor_user_id = %(u)s AND actor_role = 'SUPERVISOR'",
        u=ucp["sa"]["id"],
    )
    assert sup_metadata["from"]["default"] is True and sup_metadata["to"] == {"value": 2, "unit": "HOURS"}
    expired = (await video_client.get(f"{API}/internal/recordings/expired", headers=SERVICE)).json()
    assert expired == []  # 2 hours: nothing has expired yet
    wrong = await video_client.put(
        f"{API}/recordings/retention", json={**body, "current_password": "nope"}, headers=headers(ucp["a"])
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    too_long = await video_client.put(
        f"{API}/recordings/retention", json={**body, "value": 400, "unit": "DAYS"}, headers=headers(ucp["a"])
    )
    assert too_long.status_code == 422 and too_long.json()["code"] == "INVALID_RETENTION"
    a_password = ucp["a"]["password"]
    changed = await video_client.put(
        f"{API}/recordings/retention", json={**body, "current_password": a_password}, headers=headers(ucp["a"])
    )
    assert changed.status_code == 200, changed.text
    retention = changed.json()["retention"]
    assert retention["value"] == 5 and retention["unit"] == "MINUTES" and retention["seconds"] == 300
    assert retention["is_default"] is False and retention["updated_by"] == str(ucp["a"]["id"])
    assert audit(owner_query, "RECORDING_RETENTION_CHANGED", ucp["a"]["id"]) == 1
    metadata = one(
        owner_query,
        "SELECT metadata FROM organization_audit_logs WHERE action = 'RECORDING_RETENTION_CHANGED' "
        "AND actor_user_id = %(u)s",
        u=ucp["a"]["id"],
    )
    assert metadata["from"] == {"value": 2, "unit": "HOURS", "default": False}
    assert metadata["to"] == {"value": 5, "unit": "MINUTES"}
    # The timeline shows when each segment will be purged (Supervisor spec page 8: deletion countdown),
    # and the overview what the campus stores in each tier.
    timeline = (await video_client.get(f"{API}/recordings", headers=headers(ucp["sa"]))).json()
    assert timeline["retention_seconds"] == 300
    by_id = {s["id"]: s for s in timeline["segments"]}
    fresh_seg = by_id[fresh["segment_id"]]
    assert datetime.fromisoformat(fresh_seg["expires_at"]) == datetime.fromisoformat(fresh_seg["ended_at"]) + timedelta(
        minutes=5
    )
    overview = (await video_client.get(f"{API}/recordings/retention", headers=headers(ucp["sa"]))).json()
    assert overview["usage"]["recording_segments"] == 2 and overview["usage"]["recording_bytes"] > 0
    assert overview["usage"]["evidence_items"] == 0 and overview["provider"] == "LOCAL_DISK"
    # Campus B sees its own (default) setting, untouched.
    b_view = (await video_client.get(f"{API}/recordings/retention", headers=headers(ucp["b"]))).json()
    assert b_view["retention"]["is_default"] is True

    expired = (await video_client.get(f"{API}/internal/recordings/expired", headers=SERVICE)).json()
    assert [e["segment_id"] for e in expired] == [old["segment_id"]]
    assert expired[0]["retention_seconds"] == 300 and expired[0]["relative_path"] == old["relative_path"]
    purged = await video_client.post(
        f"{API}/internal/recordings/purge", json={"segment_ids": [old["segment_id"], str(uuid4())]}, headers=SERVICE
    )
    assert purged.status_code == 200 and purged.json() == {"purged": 1}
    left = (await video_client.get(f"{API}/recordings", headers=headers(ucp["a"]))).json()["segments"]
    assert [s["id"] for s in left] == [fresh["segment_id"]]
    assert count(owner_query, "SELECT 1 FROM recording_segments WHERE id = %(i)s", i=other["segment_id"]) == 1


# --- Evidence vault ----------------------------------------------------------------------------------


async def test_evidence_lifecycle_audit_isolation_and_immunity(
    video_client: AsyncClient, ucp: dict[str, Any], owner_query, owner_execute, storage_root: Path, app_role_engine
) -> None:
    org, campus = ucp["org"], ucp["campus_a"]
    camera = camera_of(owner_query, campus)
    now = datetime.now(UTC)
    start = now - timedelta(minutes=2)
    segments = [
        await register_segment(video_client, storage_root, org, campus, camera, start + timedelta(seconds=2 * i))
        for i in range(3)
    ]
    detected = (start + timedelta(seconds=3)).isoformat()
    alert_id = (
        await video_client.post(
            f"{API}/internal/detections",
            json={"camera_id": str(camera), "detection_type": "FIGHT_VIOLENCE", "detected_at": detected},
            headers={"X-Detection-Token": DETECTION_TOKEN},
        )
    ).json()["alert_id"]

    # Save a clip from the alert (Supervisor) and a snapshot from playback (Admin).
    clip = await video_client.post(
        f"{API}/evidence/from-alert",
        json={"alert_id": alert_id, "kind": "CLIP", "title": "  Fight at the gate ", "investigation_ref": "INV-7"},
        headers=headers(ucp["sa"]),
    )
    assert clip.status_code == 201, clip.text
    item = clip.json()
    assert item["kind"] == "CLIP" and item["title"] == "Fight at the gate" and item["source"] == "ALERT"
    assert item["alert_id"] == alert_id and item["camera_id"] == str(camera) and item["camera_name"] == "Gate"
    assert item["saved_by"] == str(ucp["sa"]["id"]) and item["media_type"] == "video/mp4" and item["size_bytes"] > 0
    assert datetime.fromisoformat(item["covers_from"]) == start  # clipped to what was recorded
    clip_file = storage_root / "evidence" / str(org) / str(campus) / item["id"] / "clip.mp4"
    assert clip_file.is_file() and clip_file.stat().st_size == item["size_bytes"]
    assert audit(owner_query, "EVIDENCE_SAVED", ucp["sa"]["id"]) == 1

    snapshot = await video_client.post(
        f"{API}/evidence/from-playback",
        json={
            "segment_id": segments[1]["segment_id"],
            "kind": "SNAPSHOT",
            "start_offset_seconds": 1.0,
            "title": "Face",
        },
        headers=headers(ucp["a"]),
    )
    assert snapshot.status_code == 201, snapshot.text
    shot = snapshot.json()
    assert shot["kind"] == "SNAPSHOT" and shot["source"] == "PLAYBACK" and shot["media_type"] == "image/jpeg"
    assert shot["covers_from"] == shot["covers_to"]
    too_long = await video_client.post(
        f"{API}/evidence/from-playback",
        json={
            "segment_id": segments[0]["segment_id"],
            "kind": "CLIP",
            "start_offset_seconds": 0,
            "end_offset_seconds": 9999,
            "title": "x",
        },
        headers=headers(ucp["a"]),
    )
    assert too_long.status_code == 422 and too_long.json()["code"] == "CLIP_TOO_LONG"
    nothing = await video_client.post(
        f"{API}/evidence/from-alert", json={"alert_id": str(uuid4()), "title": "x"}, headers=headers(ucp["a"])
    )
    assert nothing.status_code == 404

    # List, details, media (audited), update (audited).
    listed = (await video_client.get(f"{API}/evidence", headers=headers(ucp["a"]))).json()
    assert [e["id"] for e in listed] == [shot["id"], item["id"]]
    found = (await video_client.get(f"{API}/evidence", params={"q": "INV-7"}, headers=headers(ucp["a"]))).json()
    assert [e["id"] for e in found] == [item["id"]]
    later = (now + timedelta(hours=1)).isoformat()
    in_period = await video_client.get(
        f"{API}/evidence", params={"since": start.isoformat(), "until": later}, headers=headers(ucp["a"])
    )
    assert {e["id"] for e in in_period.json()} == {item["id"], shot["id"]}
    after = await video_client.get(f"{API}/evidence", params={"since": later}, headers=headers(ucp["a"]))
    assert after.json() == []
    usage = (await video_client.get(f"{API}/recordings/retention", headers=headers(ucp["a"]))).json()["usage"]
    assert usage["evidence_items"] == 2 and usage["evidence_bytes"] == item["size_bytes"] + shot["size_bytes"]
    media = await video_client.get(f"{API}/evidence/{item['id']}/media", headers=headers(ucp["a"]))
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4" and media.content[4:8] == b"ftyp"
    image = await video_client.get(f"{API}/evidence/{shot['id']}/media", headers=headers(ucp["sa"]))
    assert image.status_code == 200 and image.content[:2] == b"\xff\xd8"
    assert audit(owner_query, "EVIDENCE_VIEWED", ucp["a"]["id"]) == 1
    updated = await video_client.patch(
        f"{API}/evidence/{item['id']}",
        json={"title": "Fight at the gate", "notes": "Two persons; knife visible", "investigation_ref": "INV-7"},
        headers=headers(ucp["sa"]),
    )
    assert updated.status_code == 200 and updated.json()["notes"] == "Two persons; knife visible"
    assert audit(owner_query, "EVIDENCE_UPDATED", ucp["sa"]["id"]) == 1

    # Isolation: campus B and Guards never see it; the app role sees only its campus's rows.
    for who, status in ((ucp["b"], 404), (ucp["ga"], 403)):
        assert (await video_client.get(f"{API}/evidence/{item['id']}", headers=headers(who))).status_code == status
        assert (
            await video_client.get(f"{API}/evidence/{item['id']}/media", headers=headers(who))
        ).status_code == status
    assert (await video_client.get(f"{API}/evidence", headers=headers(ucp["b"]))).json() == []
    rows, _ = await run_as(app_role_engine, ucp["b"]["id"], "SELECT id FROM evidence_items")
    assert rows == []

    # Immunity: the retention daemon's view never lists evidence, and purging recordings leaves the vault alone.
    owner_execute(
        "INSERT INTO campus_retention_settings (organization_id, campus_id, retention_value, retention_unit) "
        "VALUES (%(o)s, %(c)s, 1, 'MINUTES')",
        {"o": org, "c": campus},
    )
    expired = (await video_client.get(f"{API}/internal/recordings/expired", headers=SERVICE)).json()
    assert {e["segment_id"] for e in expired} == {s["segment_id"] for s in segments}
    assert all(e["relative_path"].startswith(f"{org}/{campus}/{camera}/") for e in expired)
    await video_client.post(
        f"{API}/internal/recordings/purge", json={"segment_ids": [e["segment_id"] for e in expired]}, headers=SERVICE
    )
    assert (
        clip_file.is_file()
        and (await video_client.get(f"{API}/evidence/{item['id']}", headers=headers(ucp["a"]))).status_code == 200
    )

    # Deletion: Admins only, with their password; the file goes with the record; audited.
    denied = await video_client.post(
        f"{API}/evidence/{item['id']}/delete", json={"current_password": OTHER_PASSWORD}, headers=headers(ucp["sa"])
    )
    assert denied.status_code == 403 and denied.json()["code"] == "ADMIN_ONLY"
    wrong = await video_client.post(
        f"{API}/evidence/{item['id']}/delete", json={"current_password": "nope"}, headers=headers(ucp["a"])
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "CURRENT_PASSWORD_INCORRECT"
    assert clip_file.is_file()
    deleted = await video_client.post(
        f"{API}/evidence/{item['id']}/delete",
        json={"current_password": ucp["a"]["password"]},
        headers=headers(ucp["a"]),
    )
    assert deleted.status_code == 204, deleted.text
    assert not clip_file.exists() and not clip_file.parent.exists()
    assert (await video_client.get(f"{API}/evidence/{item['id']}", headers=headers(ucp["a"]))).status_code == 404
    assert audit(owner_query, "EVIDENCE_DELETED", ucp["a"]["id"]) == 1
    # The database refuses a Supervisor's DELETE and an INSERT in someone else's name.
    _, deleted_rows = await run_as(app_role_engine, ucp["sa"]["id"], "DELETE FROM evidence_items")
    assert deleted_rows == 0
    with pytest.raises(DBAPIError):
        await run_as(
            app_role_engine,
            ucp["sa"]["id"],
            "INSERT INTO evidence_items (id, organization_id, campus_id, kind, title, source, camera_name, "
            "covers_from, covers_to, relative_path, media_type, size_bytes, sha256, saved_by, saved_by_name) "
            "VALUES (gen_random_uuid(), :org, :campus, 'CLIP', 't', 'PLAYBACK', 'Gate', now(), now(), "
            "'x/clip.mp4', 'video/mp4', 1, repeat('a', 64), :other, 'x')",
            {"org": org, "campus": campus, "other": ucp["a"]["id"]},
        )
