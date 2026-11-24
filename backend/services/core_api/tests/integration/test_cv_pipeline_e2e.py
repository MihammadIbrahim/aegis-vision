"""End to end: the cv_inference pipeline (a separate process) reads a generated video, runs the
**synthetic test detector**, and posts events over real HTTP to Core API served by uvicorn on a
local port. Checks the alert that results: priority, routing, merging, idempotent re-runs, and the
pipeline's handling of a wrong token and an unknown camera. No model, weights or GPU involved."""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import Settings
from src.main import create_app
from tests.conftest import DETECTION_TOKEN, JWT_ALGORITHM, JWT_SECRET, DatabaseUrls
from tests.fakes import FakeEmailSender
from tests.integration.test_alerts import site  # noqa: F401  (a fixture, used by name)
from tests.integration.test_ownership_and_deletion import count, one, ucp  # noqa: F401

CV_DIR = Path(__file__).resolve().parents[3] / "cv_inference"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_api(migrated_database: DatabaseUrls, storage_root: Path) -> Iterator[str]:
    """Core API on a real socket, on the test database (the same data the fixtures create)."""
    settings = Settings(
        database_url=migrated_database.app,
        platform_database_url=migrated_database.platform,
        migration_database_url=migrated_database.migration,
        jwt_secret_key=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        detection_ingest_token=DETECTION_TOKEN,
        cctv_storage_root=storage_root,
        _env_file=None,
    )
    app = create_app(
        settings,
        engine=create_async_engine(migrated_database.app, poolclass=NullPool),
        platform_engine=create_async_engine(migrated_database.platform, poolclass=NullPool),
        email_sender=FakeEmailSender(),
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("Core API did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _video(path: Path, frames: int = 125, fps: float = 25.0) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for i in range(frames):
        writer.write(np.full((48, 64, 3), (i * 3) % 255, np.uint8))
    writer.release()
    return path


def _script(path: Path, *entries: tuple[int, int, str, float]) -> Path:
    path.write_text(
        json.dumps(
            [
                {"from_frame": a, "to_frame": b, "label": label, "confidence": conf, "box": [5, 5, 30, 30]}
                for a, b, label, conf in entries
            ]
        )
    )
    return path


def _run(
    api: str, video: Path, script: Path, camera: Any, *, token: str = DETECTION_TOKEN
) -> tuple[int, dict[str, Any]]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "CV_API_BASE_URL": api,
        "CV_DETECTION_INGEST_TOKEN": token,
        "CV_SAMPLE_FPS": "5",
        "CV_COOLDOWN_SECONDS": "2",
        "CV_MAX_ATTEMPTS": "2",
        "CV_RETRY_BACKOFF_SECONDS": "0",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "--video",
            str(video),
            "--camera-id",
            str(camera),
            "--detector",
            "synthetic",
            "--script",
            str(script),
            "--start-time",
            "2026-10-31T10:00:00+00:00",
            "--log-level",
            "WARNING",
        ],
        cwd=CV_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert token not in done.stdout + done.stderr  # the token is never printed or logged
    return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])


async def test_a_synthetic_detection_reaches_the_backend_as_one_routed_alert(
    live_api: str, site: dict[str, Any], owner_query, tmp_path: Path
) -> None:
    video = _video(tmp_path / "gate.avi")
    script = _script(tmp_path / "script.json", (10, 124, "weapon", 0.91), (0, 124, "person", 0.97))
    code, report = _run(live_api, video, script, site["camera_a"])
    assert code == 0, report
    assert report["model"] == "synthetic-scripted-detector" and report["model_is_synthetic"] is True
    assert report["frames_processed"] == 25 and report["events"] == 3
    assert report["delivered"] == {"CREATED": 1, "REPEATED": 2}  # one alert, kept current
    assert report["rules"]["unmapped"] == 25  # person detections are never sent

    alert_id, alert_type, priority, status, detections, detected_at = one(
        owner_query,
        "SELECT id, alert_type, priority, status, detection_count, detected_at FROM alerts WHERE camera_id = %(c)s",
        c=site["camera_a"],
    )
    assert (alert_type, priority, status, detections) == ("WEAPON", "HIGH", "NEW", 3)
    # The third sampled positive frame: 0.8 s into the video.
    assert detected_at == datetime(2026, 10, 31, 10, 0, 0, 800000, tzinfo=UTC)
    recipients = {
        r[0]
        for r in owner_query(
            "SELECT u.display_id FROM alert_recipients r JOIN users u ON u.id = r.guard_user_id "
            "WHERE r.alert_id = %(a)s",
            {"a": alert_id},
        )
    }
    assert recipients == {site[g]["display_id"] for g in ("ga", "g_floor", "g_building")}
    assert one(owner_query, "SELECT detection_id FROM alerts WHERE id = %(a)s", a=alert_id).startswith(
        "synthetic-scripted-detector-"
    )

    # Processing the same video again sends the same detection IDs: nothing new is created.
    code, again = _run(live_api, video, script, site["camera_a"])
    assert code == 0 and again["delivered"] == {"DUPLICATE": 3}
    assert count(owner_query, "SELECT 1 FROM alerts") == 1
    assert one(owner_query, "SELECT detection_count FROM alerts WHERE id = %(a)s", a=alert_id) == 3


async def test_only_mapped_labels_create_alerts(
    live_api: str, site: dict[str, Any], owner_query, tmp_path: Path
) -> None:
    script = _script(tmp_path / "people.json", (0, 124, "person", 0.99), (0, 124, "weapon", 0.2))
    code, report = _run(live_api, _video(tmp_path / "people.avi"), script, site["camera_a"])
    assert code == 0 and report["events"] == 0 and report["rules"]["below_threshold"] == 25
    assert count(owner_query, "SELECT 1 FROM alerts") == 0


async def test_a_wrong_token_or_unknown_camera_stops_the_pipeline(
    live_api: str, site: dict[str, Any], owner_query, tmp_path: Path
) -> None:
    video = _video(tmp_path / "v.avi")
    script = _script(tmp_path / "s.json", (0, 124, "weapon", 0.9))
    code, report = _run(live_api, video, script, site["camera_a"], token="wrong-token-value")
    assert code == 2 and "401" in report["stopped_reason"] and report["delivered"] == {}
    code, report = _run(live_api, video, script, uuid4())
    assert code == 2 and "CAMERA_NOT_FOUND" in report["stopped_reason"]
    assert count(owner_query, "SELECT 1 FROM alerts") == 0


# --- The real YOLOX-Tiny person detector through the whole chain ------------------------------------

REAL_WEIGHTS = CV_DIR / "weights" / "yolox_tiny.onnx"


def _person_video(path: Path, frames: int = 50) -> Path:
    """A real photograph of a person (public-domain portrait bundled with matplotlib) moving across a
    grey canvas."""
    import matplotlib.cbook as cbook

    person = cv2.resize(cv2.imread(cbook.get_sample_data("grace_hopper.jpg", asfileobj=False)), (150, 176))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (640, 480))
    for i in range(frames):
        canvas = np.full((480, 640, 3), 128, np.uint8)
        canvas[150:326, 40 + 4 * i : 190 + 4 * i] = person
        writer.write(canvas)
    writer.release()
    return path


@pytest.mark.skipif(not REAL_WEIGHTS.is_file(), reason="YOLOX-Tiny weights not downloaded; real-model test skipped")
async def test_the_real_person_detector_runs_end_to_end_without_raising_alerts(
    live_api: str, site: dict[str, Any], owner_query, tmp_path: Path
) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "CV_API_BASE_URL": live_api,
        "CV_DETECTION_INGEST_TOKEN": DETECTION_TOKEN,
        "CV_SAMPLE_FPS": "5",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.main",
            "--video",
            str(_person_video(tmp_path / "walk.avi")),
            "--camera-id",
            str(site["camera_a"]),
            "--detector",
            "yolox-human",
            "--start-time",
            "2026-10-31T10:00:00+00:00",
            "--log-level",
            "WARNING",
        ],  # fmt: skip
        cwd=CV_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["model"] == "yolox-tiny-coco" and report["model_is_synthetic"] is False
    assert report["frames_processed"] == 10 and report["tracks_started"] == 1
    assert report["rules"]["unmapped"] >= 9  # the person was seen in (nearly) every sampled frame
    assert report["events"] == 0 and report["delivered"] == {}  # human detection alone never alerts
    assert count(owner_query, "SELECT 1 FROM alerts") == 0
