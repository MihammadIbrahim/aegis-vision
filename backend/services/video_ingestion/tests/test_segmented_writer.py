"""Chunk rotation: complete files move into the dated Tier 1 layout and are reported once; the
file still being written stays; nothing in the evidence tier is touched. The last test runs the
real ffmpeg pipeline on a file source and checks segments, live frames and the CV spool."""

import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from src.camera_health_monitor import HealthMonitor
from src.camera_registry import CameraSource
from src.config import IngestionSettings
from src.core_api_client import CameraSpec
from src.segmented_writer import CompletedSegment, SegmentRotator, parse_started_at
from src.stream_manager import CameraWorker

ORG, CAMPUS, CAMERA = uuid4(), uuid4(), uuid4()


def test_segment_names_are_utc_timestamps() -> None:
    assert parse_started_at("20261116T063820.mp4") == datetime(2026, 11, 16, 6, 38, 20, tzinfo=UTC)
    assert parse_started_at("notes.txt") is None and parse_started_at("20261116T063820.mp4.part") is None


def test_rotation_moves_complete_chunks_and_keeps_the_open_one(tmp_path: Path) -> None:
    reported: list[CompletedSegment] = []
    camera_dir = tmp_path / "recordings" / str(ORG) / str(CAMPUS) / str(CAMERA)
    rotator = SegmentRotator(CAMERA, ORG, CAMPUS, camera_dir, reported.append)
    assert rotator.rotate() == []  # no incoming folder yet
    incoming = rotator.incoming_dir
    incoming.mkdir(parents=True)
    for name, size in (
        ("20261116T235959.mp4", 10),
        ("20261117T000059.mp4", 0),
        ("20261117T000159.mp4", 20),
        ("20261117T000259.mp4", 5),
    ):
        (incoming / name).write_bytes(b"x" * size)
    (incoming / "ignored.txt").write_text("not a chunk")

    done = rotator.rotate()
    # The newest file is still open; the empty one is dropped; the two complete ones moved by date.
    assert [s.path.name for s in done] == ["20261116T235959.mp4", "20261117T000159.mp4"]
    assert done[0].relative_path == f"{ORG}/{CAMPUS}/{CAMERA}/2026-11-16/20261116T235959.mp4"
    assert done[1].relative_path.split("/")[3] == "2026-11-17"
    assert (
        done[0].started_at == datetime(2026, 11, 16, 23, 59, 59, tzinfo=UTC) and done[0].ended_at > done[0].started_at
    )
    assert done[0].size_bytes == 10 and all(s.path.is_file() for s in done)
    assert sorted(os.listdir(incoming)) == ["20261117T000259.mp4", "ignored.txt"]
    assert reported == done

    # Final rotation (the process ended) moves the last one too; nothing is reported twice.
    assert [s.path.name for s in rotator.rotate(final=True)] == ["20261117T000259.mp4"]
    assert rotator.rotate(final=True) == [] and len(reported) == 3
    assert not (tmp_path / "evidence").exists()


def test_a_failing_report_keeps_the_file_in_place(tmp_path: Path) -> None:
    def explode(segment: CompletedSegment) -> None:
        raise RuntimeError("API down")

    rotator = SegmentRotator(CAMERA, ORG, CAMPUS, tmp_path / "cam", explode)
    rotator.incoming_dir.mkdir(parents=True)
    (rotator.incoming_dir / "20261116T063820.mp4").write_bytes(b"x")
    done = rotator.rotate(final=True)
    assert len(done) == 1 and done[0].path.is_file()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is needed for the real pipeline")
def test_file_source_pipeline_rotates_chunks_publishes_frames_and_reports_health(tmp_path: Path) -> None:
    source_file = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source_file)],
        check=True,
    )  # fmt: skip
    settings = IngestionSettings(
        storage_root=tmp_path / "storage",
        segment_seconds=5,  # the minimum; the test checks the first chunk completes
        frame_sample_fps=4,
        frame_width=160,
        offline_after_seconds=2,
        heartbeat_seconds=100,
        _env_file=None,
    )
    reports: list[tuple[str, str | None]] = []
    health = HealthMonitor(2, 100, lambda camera, state, reason, seen: reports.append((state, reason)))
    segments: list[CompletedSegment] = []
    spec = CameraSpec(CAMERA, ORG, CAMPUS, "Gate", "RTSP", None)
    worker = CameraWorker(CameraSource(spec, "file", str(source_file)), settings, health, segments.append)
    worker.start()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and not segments:
        health.evaluate(time.monotonic())
        time.sleep(0.5)
    live = settings.live_frame_path(ORG, CAMPUS, CAMERA)
    assert segments, "no chunk completed in time"
    assert live.is_file() and live.read_bytes()[:2] == b"\xff\xd8"
    assert any(n.endswith(".jpg") for n in os.listdir(settings.spool_dir(CAMERA)))
    assert reports[0] == ("ONLINE", None) and health.state_of(CAMERA) == "ONLINE"
    first = segments[0]
    assert first.path.is_file() and first.relative_path.startswith(f"{ORG}/{CAMPUS}/{CAMERA}/")
    assert first.path.parent.parent == settings.recordings_dir(ORG, CAMPUS, CAMERA)
    assert 3 <= (first.ended_at - first.started_at).total_seconds() <= 12 and first.size_bytes > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(first.path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert 3 <= float(probe.stdout.strip()) <= 7  # a browser-playable MP4 of about one segment
    worker.stop()
    worker.join(timeout=15)
    assert not worker.is_alive() and not live.exists()  # no stale live picture after a stop
    assert not os.listdir(worker.rotator.incoming_dir)  # the last chunk was rotated as well


def test_a_missing_file_source_is_reported_as_a_stream_failure(tmp_path: Path) -> None:
    settings = IngestionSettings(
        storage_root=tmp_path,
        reconnect_min_seconds=0.5,
        reconnect_max_seconds=0.5,
        ffmpeg_path="ffmpeg",
        _env_file=None,
    )
    reports: list[tuple[str, str | None]] = []
    health = HealthMonitor(2, 100, lambda camera, state, reason, seen: reports.append((state, reason)))
    spec = CameraSpec(CAMERA, ORG, CAMPUS, "Gate", "RTSP", None)
    worker = CameraWorker(CameraSource(spec, "file", str(tmp_path / "missing.mp4")), settings, health, lambda s: None)
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is needed")
    worker.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and health.state_of(CAMERA) != "OFFLINE":
        time.sleep(0.2)
    worker.stop()
    worker.join(timeout=5)
    assert health.state_of(CAMERA) == "OFFLINE"
    health.evaluate(time.monotonic())
    assert reports and reports[0][0] == "OFFLINE" and "missing.mp4" not in (reports[0][1] or "")
