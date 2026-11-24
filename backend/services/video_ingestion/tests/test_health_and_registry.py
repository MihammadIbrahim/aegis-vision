"""Online/offline decisions, heartbeats, retry of failed reports; camera source resolution."""

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from src.camera_health_monitor import HealthMonitor
from src.camera_registry import diff_sources, resolve_sources
from src.config import IngestionSettings
from src.core_api_client import CameraSpec

CAM = uuid4()


class Reporter:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, str | None, datetime | None]] = []
        self.fail = False

    def __call__(self, camera_id: UUID, state: str, reason: str | None, seen_at: datetime | None) -> None:
        if self.fail:
            raise RuntimeError("API down")
        self.calls.append((camera_id, state, reason, seen_at))


def test_online_offline_transitions_and_heartbeat() -> None:
    reporter = Reporter()
    monitor = HealthMonitor(offline_after_seconds=5, heartbeat_seconds=30, reporter=reporter)
    monitor.frame_seen(CAM, 100.0)
    changes = monitor.evaluate(100.5)
    assert [c.state for c in changes] == ["ONLINE"] and reporter.calls[-1][1] == "ONLINE"
    assert reporter.calls[-1][3] is not None and reporter.calls[-1][3].tzinfo is not None
    monitor.frame_seen(CAM, 104.0)
    assert monitor.evaluate(105.0) == [] and len(reporter.calls) == 1  # unchanged, no heartbeat yet
    monitor.frame_seen(CAM, 130.0)
    assert monitor.evaluate(131.0) == [] and len(reporter.calls) == 2  # heartbeat refreshes "last seen"
    changes = monitor.evaluate(140.0)  # silent for 36 s
    assert [c.state for c in changes] == ["OFFLINE"] and changes[0].reason == "No frames received."
    assert monitor.evaluate(200.0) == [] and len(reporter.calls) == 3  # OFFLINE is sent once
    monitor.frame_seen(CAM, 201.0)
    assert [c.state for c in monitor.evaluate(201.0)] == ["ONLINE"]


def test_stream_failures_and_unavailable_sources_are_reported_once_and_retried_on_error() -> None:
    reporter = Reporter()
    monitor = HealthMonitor(5, 30, reporter)
    monitor.stream_failed(CAM, "The connection was refused.")
    reporter.fail = True
    assert monitor.evaluate(1.0) == [] and reporter.calls == []  # the API was down: nothing recorded
    reporter.fail = False
    assert [c.reason for c in monitor.evaluate(2.0)] == ["The connection was refused."]
    assert monitor.evaluate(3.0) == []
    other = uuid4()
    monitor.unavailable(other, "No stream URL is configured.")
    assert [(c.camera_id, c.state) for c in monitor.evaluate(4.0)] == [(other, "UNKNOWN")]
    monitor.forget(other)
    assert monitor.state_of(other) == "UNKNOWN" and monitor.evaluate(5.0) == []


def spec(
    camera: UUID, kind: str = "RTSP", url: str | None = "rtsp://10.0.0.5/stream", campus: UUID | None = None
) -> CameraSpec:
    return CameraSpec(camera, uuid4(), campus or uuid4(), "Gate", kind, url)


def test_sources_prefer_file_overrides_and_explain_unavailable_cameras(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    a, b, c, d, e = (uuid4() for _ in range(5))
    campus = uuid4()
    cameras = [
        spec(a, campus=campus),
        spec(b, url=None, campus=campus),
        spec(c, "ONVIF", "https://cam.local/onvif/device_service", campus=campus),
        spec(d, campus=campus),
        spec(e),  # another campus
    ]
    sources = resolve_sources(cameras, {a: clip, d: tmp_path / "gone.mp4"}, campus_id=campus)
    by_id = {s.camera_id: s for s in sources}
    assert set(by_id) == {a, b, c, d}
    assert by_id[a].kind == "file" and by_id[a].location == str(clip)
    assert by_id[b].kind == "unavailable" and "No stream URL" in (by_id[b].reason or "")
    assert by_id[c].kind == "unavailable" and "ONVIF" in (by_id[c].reason or "")
    assert by_id[d].kind == "unavailable" and "missing" in (by_id[d].reason or "")
    assert resolve_sources(cameras, {})[0].kind == "rtsp"

    current = {a: by_id[a], b: by_id[b]}
    changed = resolve_sources([spec(a, url="rtsp://10.0.0.9/new", campus=campus), spec(c, campus=campus)], {}, campus)
    to_start, to_stop = diff_sources(current, changed)
    assert to_stop == [b] and {s.camera_id for s in to_start} == {a, c}
    assert diff_sources({a: changed[0]}, [changed[0]]) == ([], [])


def test_settings_parse_file_sources_and_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_FILE_SOURCES", f'{{"{CAM}": "{tmp_path}/a.mp4"}}')
    monkeypatch.setenv("INGEST_STORAGE_ROOT", str(tmp_path))
    settings = IngestionSettings(_env_file=None)
    assert settings.file_sources == {CAM: tmp_path / "a.mp4"}
    org, campus = uuid4(), uuid4()
    assert settings.recordings_dir(org, campus, CAM) == tmp_path / "recordings" / str(org) / str(campus) / str(CAM)
    assert settings.live_frame_path(org, campus, CAM).name == "latest.jpg"
    assert settings.spool_dir(CAM) == tmp_path / "frames" / str(CAM)
    with pytest.raises(ValueError):
        IngestionSettings(reconnect_min_seconds=10, reconnect_max_seconds=5, _env_file=None)
