"""Live smoke check: video ingestion, live monitoring, recordings, retention and the evidence vault (D-023).

The web contract is called **through the Vite dev server's /api proxy** with the URLs, methods and
bodies the pages send (features/video/api.ts, features/cameras/api.ts). Unlike the other smoke
checks, this one also runs the real backend services against the same API:

* ``video_ingestion`` with a recorded-file source (``INGEST_FILE_SOURCES``) for one camera, because no
  camera hardware is attached here; a second camera points at a closed local RTSP port, so it must be
  reported OFFLINE; a third is ONVIF, which the ingestion cannot open without stored credentials;
* one pass of ``storage_retention_daemon`` after the campus retention is set to 1 minute.

It checks: statuses and the offline notification, live frames, the live-view audit call, the
connection test, segments on the timeline with an AI marker, playback, campus isolation against a
second organization, evidence from an alert and from playback, metadata edits, the password-confirmed
retention change (Admins and Supervisors, requirements Section 17), the per-segment deletion countdown,
campus usage by tier, Admin-only evidence deletion, and that the retention purge removes the routine
recordings but never the evidence files.

ONLY run it against a server that uses an isolated scratch database, EMAIL_DELIVERY_MODE=dev_outbox,
DETECTION_INGEST_TOKEN, INTERNAL_SERVICE_TOKEN and a scratch CCTV_STORAGE_ROOT (see smoke/README.md).

    python3 smoke/video_smoke.py --web http://127.0.0.1:5179 --outbox /path/outbox --token <detection token> \\
        --service-token <service token> --storage /path/storage --services ../backend/services \\
        --python ../backend/.venv/bin/python [--summary out.json]

Standard library only (plus ffmpeg to make the test video). Exit code 0 when every check passes. The
data is left in place for screenshots; the sign-in details are printed at the end.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

from operations_smoke import Org
from personnel_smoke import PASSWORD, Api, check, results


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def internal(web: str, path: str, body: dict, header: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(web.rstrip("/") + "/api/v1" + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **header})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def make_video(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15",
         "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def wait_for(predicate, seconds: float, step: float = 1.0):  # type: ignore[no-untyped-def]
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--web", required=True)
    parser.add_argument("--outbox", required=True, type=Path)
    parser.add_argument("--token", required=True, help="DETECTION_INGEST_TOKEN of the API")
    parser.add_argument("--service-token", required=True, help="INTERNAL_SERVICE_TOKEN of the API")
    parser.add_argument("--storage", required=True, type=Path, help="CCTV_STORAGE_ROOT of the API (scratch)")
    parser.add_argument("--services", required=True, type=Path, help="backend/services")
    parser.add_argument("--python", required=True, help="Python with the services' requirements")
    parser.add_argument("--summary", type=Path, help="write the created IDs and sign-ins here (for screenshots)")
    args = parser.parse_args()
    if urlparse(args.web).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: the smoke check only runs against a local development server.")
        return 2
    if not args.outbox.is_dir() or not args.storage.is_dir():
        print("Refusing: the outbox or the scratch storage root does not exist.")
        return 2
    api = Api(args.web)

    # --- Organizations, staff, cameras ---------------------------------------------------------
    a = Org(api, args.outbox, "Video Smoke")
    b = Org(api, args.outbox, "Video Other")
    sup_email = f"sup.{a.run}@smoke.example.com"
    a.create("/organization-admin/supervisors", a.admin, a.admin_email, "Sana Supervisor", sup_email)
    sup = a.sign_in(sup_email)

    def camera(token: str, name: str, kind: str, url: str | None) -> dict:
        s, created = api.call("POST", "/cameras", {"display_name": name, "connection_type": kind, "stream_url": url, "location": None}, token)
        assert s == 201, (s, created)
        return created

    lobby = camera(a.admin, "Lobby", "RTSP", "rtsp://127.0.0.1:8554/lobby")  # played from a recorded file
    gate = camera(a.admin, "Main Gate", "RTSP", "rtsp://127.0.0.1:9/gate")  # nothing listens: OFFLINE
    roof = camera(sup, "Roof", "ONVIF", "http://127.0.0.1:9/onvif/device_service")
    other = camera(b.admin, "Other Org Gate", "RTSP", "rtsp://127.0.0.1:9/x")
    campus = lobby["campus_id"]

    s, listed = api.call("GET", "/live/cameras", token=sup)
    check(s == 200 and {c["camera"]["id"] for c in listed} == {lobby["id"], gate["id"], roof["id"]}
          and all(c["status"]["state"] == "UNKNOWN" for c in listed), "before ingestion: own 3 cameras, all UNKNOWN")
    s, body = api.call("GET", f"/live/cameras/{lobby['id']}/frame", token=sup)
    check(s == 404 and body.get("code") == "LIVE_FRAME_UNAVAILABLE", "no live frame yet (404 LIVE_FRAME_UNAVAILABLE)")
    s, _ = api.call("GET", "/live/cameras")
    check(s == 401, "live cameras need a signed-in user (401)")
    s, _ = internal(args.web, f"/internal/cameras/{lobby['id']}/status", {"state": "ONLINE"}, {"X-Service-Token": "wrong"})
    check(s == 401, "internal status reports need the service token (401)")

    # --- Real ingestion with a recorded-file source ---------------------------------------------
    with tempfile.TemporaryDirectory(prefix="video-smoke-") as scratch:
        clip = Path(scratch) / "lobby.mp4"
        make_video(clip)
        env = {
            **os.environ,
            "INGEST_API_BASE_URL": args.web,
            "INGEST_SERVICE_TOKEN": args.service_token,
            "INGEST_STORAGE_ROOT": str(args.storage),
            "INGEST_CAMPUS_ID": campus,
            "INGEST_FILE_SOURCES": json.dumps({lobby["id"]: str(clip)}),
            "INGEST_SEGMENT_SECONDS": "5",
            "INGEST_FRAME_SAMPLE_FPS": "2",
            "INGEST_OFFLINE_AFTER_SECONDS": "6",
            "INGEST_REGISTRY_REFRESH_SECONDS": "5",
            "INGEST_RECONNECT_MIN_SECONDS": "2",
            "INGEST_RECONNECT_MAX_SECONDS": "4",
        }
        started = datetime.now(timezone.utc)
        ingest = subprocess.Popen([args.python, "-m", "src.main", "--run-seconds", "32", "--log-level", "WARNING"],
                                  cwd=args.services / "video_ingestion", env=env)
        try:
            def states() -> dict[str, str]:
                _, rows = api.call("GET", "/live/cameras", token=sup)
                return {r["camera"]["id"]: r["status"]["state"] for r in rows}

            seen = wait_for(lambda: (st := states()).get(lobby["id"]) == "ONLINE" and st.get(gate["id"]) == "OFFLINE" and st, 25)
            check(bool(seen), "ingestion: the file-fed camera is ONLINE and the unreachable one OFFLINE")
            _, rows = api.call("GET", "/live/cameras", token=a.admin)
            by_id = {r["camera"]["id"]: r["status"] for r in rows}
            check(by_id[roof["id"]]["state"] == "UNKNOWN" and "ONVIF" in (by_id[roof["id"]]["last_error"] or ""),
                  "the ONVIF camera stays UNKNOWN with the reason (no stored credentials)")
            check("127.0.0.1" not in (by_id[gate["id"]]["last_error"] or "") and by_id[gate["id"]]["last_error"],
                  f"the offline reason is generic, without the address ({by_id[gate['id']]['last_error']!r})")
            s, frame = api.call("GET", f"/live/cameras/{lobby['id']}/frame", token=sup)
            check(s == 200 and isinstance(frame, bytes) and frame[:2] == b"\xff\xd8", "a live JPEG frame is served for the online camera")
            s, rows = api.call("POST", "/live/sessions", {"camera_ids": [lobby["id"]]}, sup)
            check(s == 200 and [r["camera"]["id"] for r in rows] == [lobby["id"]], "the live view is recorded (POST /live/sessions)")
            s, rows = api.call("POST", "/live/sessions", {"camera_ids": [other["id"]]}, sup)
            check(s == 404, "another organization's camera cannot be viewed (404)")
            s, checked = api.call("POST", f"/live/cameras/{gate['id']}/health-check", token=a.admin)
            check(s == 200 and checked["reachable"] is False and checked["camera"]["status"]["state"] == "OFFLINE",
                  f"connection test of the unreachable camera: {checked.get('detail')!r}")
            detected = datetime.now(timezone.utc) - timedelta(seconds=3)
            s, alert = internal(args.web, "/internal/detections", {"camera_id": lobby["id"], "detection_type": "WEAPON",
                                "confidence": 0.91, "detected_at": iso(detected), "detection_id": uuid.uuid4().hex},
                                {"X-Detection-Token": args.token})
            check(s == 200 and alert["outcome"] == "CREATED", "an AI detection on the live camera creates an alert")
        finally:
            ingest.wait(timeout=90)
        check(ingest.returncode == 0, f"the ingestion service stopped cleanly (exit {ingest.returncode})")

    s, notes = api.call("GET", "/notifications", token=a.admin)
    offline = [n for n in notes if n["kind"] == "CAMERA_OFFLINE" and n["camera_id"] == gate["id"]]
    check(s == 200 and len(offline) == 1, "the Admin got one CAMERA_OFFLINE notification for the gate camera")
    s, notes_b = api.call("GET", "/notifications", token=b.admin)
    check(not any(n["kind"].startswith("CAMERA_") for n in notes_b), "the other organization got no camera notification")

    # --- Recordings: timeline, markers, playback, isolation --------------------------------------
    query = urlencode({"camera_id": lobby["id"], "since": iso(started - timedelta(minutes=1)),
                       "until": iso(datetime.now(timezone.utc) + timedelta(minutes=1))})
    s, timeline = api.call("GET", f"/recordings?{query}", token=sup)
    segments = timeline.get("segments", []) if s == 200 else []
    check(len(segments) >= 4, f"the ingestion registered {len(segments)} five-second segments")
    first_end = datetime.fromisoformat(segments[-1]["ended_at"])
    check(timeline.get("retention_seconds") == 7 * 86400
          and datetime.fromisoformat(segments[-1]["expires_at"]) == first_end + timedelta(days=7),
          "each segment shows when it will be purged (end + the 7-day server default)")
    check(any(m["alert_id"] == alert["alert_id"] for m in timeline.get("markers", [])), "the alert is a marker on the timeline")
    newest = segments[0]
    s, media = api.call("GET", f"/recordings/{newest['id']}/media", token=sup)
    check(s == 200 and isinstance(media, bytes) and media[4:8] == b"ftyp", "a segment plays as MP4")
    s, _ = api.call("GET", f"/recordings/{newest['id']}", token=b.admin)
    check(s == 404, "another organization cannot open the segment (404)")
    s, b_timeline = api.call("GET", "/recordings", token=b.admin)
    check(s == 200 and b_timeline["segments"] == [], "the other organization's timeline is empty")

    # --- Evidence vault ---------------------------------------------------------------------------
    s, from_alert = api.call("POST", "/evidence/from-alert", {"alert_id": alert["alert_id"], "kind": "CLIP",
                              "title": "Weapon at the lobby", "investigation_ref": "INV-2026-001"}, sup)
    check(s == 201 and from_alert["kind"] == "CLIP" and from_alert["size_bytes"] > 0, "the Supervisor saves a clip from the alert")
    covering = next(seg for seg in reversed(segments) if seg["duration_seconds"] >= 3)
    s, snap = api.call("POST", "/evidence/from-playback", {"segment_id": covering["id"], "kind": "SNAPSHOT",
                       "start_offset_seconds": 1.5, "end_offset_seconds": None, "title": "Lobby still"}, a.admin)
    check(s == 201 and snap["media_type"] == "image/jpeg", "the Admin saves a snapshot from playback")
    s, clip2 = api.call("POST", "/evidence/from-playback", {"segment_id": covering["id"], "kind": "CLIP",
                        "start_offset_seconds": 0.5, "end_offset_seconds": 3.5, "title": "Lobby 3 s"}, a.admin)
    check(s == 201 and clip2["kind"] == "CLIP", "the Admin saves a 3-second clip from playback (mark in/out)")
    s, media = api.call("GET", f"/evidence/{from_alert['id']}/media", token=a.admin)
    check(s == 200 and isinstance(media, bytes) and media[4:8] == b"ftyp", "the evidence clip plays")
    s, edited = api.call("PATCH", f"/evidence/{from_alert['id']}", {"title": "Weapon at the lobby", "notes": "Handgun visible at 10:03",
                         "investigation_ref": "INV-2026-001"}, sup)
    check(s == 200 and edited["notes"] == "Handgun visible at 10:03", "the Supervisor edits the evidence notes")
    s, refused = api.call("POST", f"/evidence/{from_alert['id']}/delete", {"current_password": PASSWORD}, sup)
    check(s == 403 and refused.get("code") == "ADMIN_ONLY", "a Supervisor cannot delete evidence (403 ADMIN_ONLY)")
    s, refused = api.call("POST", f"/evidence/{snap['id']}/delete", {"current_password": "Wrong-pass-2026"}, a.admin)
    check(s == 403 and refused.get("code") == "CURRENT_PASSWORD_INCORRECT", "deletion needs the Admin's correct password")
    s, _ = api.call("GET", f"/evidence/{from_alert['id']}", token=b.admin)
    check(s == 404, "the other organization cannot open the evidence (404)")
    evidence_files = sorted((args.storage / "evidence").rglob("*.mp4")) + sorted((args.storage / "evidence").rglob("*.jpg"))
    mine = [p for p in evidence_files if from_alert["id"] in str(p) or clip2["id"] in str(p) or snap["id"] in str(p)]
    check(len(mine) == 3, "the three evidence files are in the evidence tier")

    # --- Retention (Admin, password) and the daemon -----------------------------------------------
    s, refused = api.call("PUT", "/recordings/retention", {"value": 2, "unit": "HOURS", "current_password": "Wrong-pass-2026"}, sup)
    check(s == 403 and refused.get("code") == "CURRENT_PASSWORD_INCORRECT", "the retention change needs the correct password")
    s, overview = api.call("PUT", "/recordings/retention", {"value": 2, "unit": "HOURS", "current_password": PASSWORD}, sup)
    check(s == 200 and overview["retention"]["seconds"] == 7200, "the Supervisor sets the retention to 2 hours (own password)")
    check(overview["usage"]["recording_segments"] == len(segments) and overview["usage"]["evidence_items"] == 3
          and overview["provider"] == "LOCAL_DISK", "the overview shows the campus's recordings and evidence separately")
    s, overview = api.call("PUT", "/recordings/retention", {"value": 1, "unit": "MINUTES", "current_password": PASSWORD}, a.admin)
    check(s == 200 and overview["retention"]["seconds"] == 60 and not overview["retention"]["is_default"],
          "the Admin sets the retention to 1 minute")
    s, b_overview = api.call("GET", "/recordings/retention", token=b.admin)
    check(s == 200 and b_overview["retention"]["is_default"], "the other organization keeps the default retention")
    newest_end = max(datetime.fromisoformat(seg["ended_at"]) for seg in segments)
    wait = (newest_end + timedelta(seconds=62) - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        print(f"…waiting {wait:.0f} s for the newest segment to pass the 1-minute retention")
        time.sleep(wait)
    files_before = sorted((args.storage / "recordings").rglob("*.mp4"))
    daemon_env = {**os.environ, "RETENTION_API_BASE_URL": args.web, "RETENTION_SERVICE_TOKEN": args.service_token,
                  "RETENTION_STORAGE_ROOT": str(args.storage)}
    daemon = subprocess.run([args.python, "-m", "src.main", "--once", "--log-level", "WARNING"],
                            cwd=args.services / "storage_retention_daemon", env=daemon_env, timeout=120)
    check(daemon.returncode == 0, "the retention daemon ran one pass")
    s, after = api.call("GET", f"/recordings?{query}", token=sup)
    check(s == 200 and after["segments"] == [], "the expired routine recordings left the timeline")
    lobby_files = [p for p in files_before if lobby["id"] in str(p)]
    check(lobby_files and not any(p.exists() for p in lobby_files), f"their {len(lobby_files)} files were deleted from the recordings tier")
    check(all(p.exists() for p in mine), "the evidence files are untouched by the purge")
    s, still = api.call("GET", f"/evidence/{from_alert['id']}/media", token=sup)
    check(s == 200 and isinstance(still, bytes) and still[4:8] == b"ftyp", "the evidence clip still plays after the purge")
    s, overview = api.call("GET", "/recordings/retention", token=sup)
    check(s == 200 and overview["storage"] is not None and overview["storage"]["level"] in ("OK", "WARNING", "CRITICAL"),
          f"the disk report is visible ({overview['storage'] and overview['storage']['percent_used']}% used)")

    # --- Authorized deletion ------------------------------------------------------------------------
    snap_file = next(p for p in mine if snap["id"] in str(p))
    s, _ = api.call("POST", f"/evidence/{snap['id']}/delete", {"current_password": PASSWORD}, a.admin)
    check(s == 204 and not snap_file.exists(), "the Admin deletes one snapshot with their password; its file is gone")
    s, _ = api.call("GET", f"/evidence/{snap['id']}", token=a.admin)
    check(s == 404, "the deleted evidence is no longer listed")

    # Keep fresh routine recordings for the screenshots: the retention back to 7 days.
    api.call("PUT", "/recordings/retention", {"value": 7, "unit": "DAYS", "current_password": PASSWORD}, a.admin)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    print(f"Sign in (Admin):      {a.name} / {a.org_id} / {a.admin_email} / {PASSWORD}")
    print(f"Sign in (Supervisor): {a.name} / {a.org_id} / {sup_email} / {PASSWORD}")
    if args.summary:
        args.summary.write_text(json.dumps({
            "organization_name": a.name, "organization_id": a.org_id, "admin_email": a.admin_email,
            "supervisor_email": sup_email, "password": PASSWORD, "campus_id": campus,
            "cameras": {"lobby": lobby["id"], "gate": gate["id"], "roof": roof["id"]},
            "evidence": from_alert["id"], "alert": alert["alert_id"],
        }, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
