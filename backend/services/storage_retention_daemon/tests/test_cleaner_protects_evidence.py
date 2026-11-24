"""Expired routine recordings are deleted and reported; the evidence tier is untouchable; paths
outside the recordings tier are refused; empty date folders go; minutes/hours/days expiry is the
API's decision, exercised here through a fake that mirrors ``app_recording_segments_expired``."""

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from src.cleaner import (
    CleanupReport,
    ExpiredSegment,
    RecordingsCleaner,
    clean_frame_spool,
)
from src.config import RetentionSettings
from src.monitor import measure, tier_sizes

ORG, CAMPUS, CAM = uuid4(), uuid4(), uuid4()


class FakeIndex:
    """What the Core API knows: segments with an end time, per-campus retention in seconds."""

    def __init__(self, default_seconds: int) -> None:
        self.default = default_seconds
        self.rows: dict[UUID, tuple[str, datetime, UUID]] = {}
        self.retention: dict[UUID, int] = {}
        self.purged: list[UUID] = []

    def add(self, relative: str, ended_at: datetime, campus: UUID = CAMPUS) -> UUID:
        segment_id = uuid4()
        self.rows[segment_id] = (relative, ended_at, campus)
        return segment_id

    def expired(self, limit: int) -> list[ExpiredSegment]:
        now = datetime.now(UTC)
        out = [
            ExpiredSegment(i, rel)
            for i, (rel, ended, campus) in sorted(self.rows.items(), key=lambda kv: kv[1][1])
            if ended < now - timedelta(seconds=self.retention.get(campus, self.default))
        ]
        return out[:limit]

    def purge(self, segment_ids: list[UUID]) -> int:
        count = 0
        for i in segment_ids:
            if self.rows.pop(i, None) is not None:
                count += 1
                self.purged.append(i)
        return count


def recording(root: Path, started: datetime, camera: UUID = CAM) -> str:
    relative = f"{ORG}/{CAMPUS}/{camera}/{started:%Y-%m-%d}/{started:%Y%m%dT%H%M%S}.mp4"
    path = root / "recordings" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return relative


def test_expiry_in_minutes_hours_and_days_deletes_only_expired_routine_files(tmp_path: Path) -> None:
    index = FakeIndex(default_seconds=7 * 86400)
    now = datetime.now(UTC)
    old_day = recording(tmp_path, now - timedelta(days=8))
    old_hour = recording(tmp_path, now - timedelta(hours=3))
    old_minute = recording(tmp_path, now - timedelta(minutes=10))
    fresh = recording(tmp_path, now - timedelta(seconds=30))
    ids = {
        rel: index.add(rel, now - age)
        for rel, age in (
            (old_day, timedelta(days=8)),
            (old_hour, timedelta(hours=3)),
            (old_minute, timedelta(minutes=10)),
            (fresh, timedelta(seconds=30)),
        )
    }
    cleaner = RecordingsCleaner(tmp_path / "recordings", index, batch_size=2)

    report = cleaner.purge_expired()  # default: 7 days
    assert report.deleted_files == 1 and report.purged_rows == 1 and index.purged == [ids[old_day]]
    assert not (tmp_path / "recordings" / old_day).exists() and (tmp_path / "recordings" / old_hour).exists()

    index.retention[CAMPUS] = 2 * 3600  # 2 hours
    report = cleaner.purge_expired()
    assert report.deleted_files == 1 and index.purged[-1] == ids[old_hour]

    index.retention[CAMPUS] = 5 * 60  # 5 minutes
    report = cleaner.purge_expired()
    assert report.deleted_files == 1 and index.purged[-1] == ids[old_minute]
    assert (tmp_path / "recordings" / fresh).exists() and set(index.rows) == {ids[fresh]}
    # Empty date folders went; the camera folder with the fresh file stays.
    assert not (tmp_path / "recordings" / old_day).parent.exists()
    assert (tmp_path / "recordings" / fresh).parent.exists()


def test_evidence_is_immune_and_outside_paths_are_refused(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence" / str(ORG) / str(CAMPUS) / str(uuid4()) / "clip.mp4"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"evidence")
    sibling = tmp_path / "secret.mp4"
    sibling.write_bytes(b"x")
    index = FakeIndex(default_seconds=60)
    long_ago = datetime.now(UTC) - timedelta(days=30)
    # Even if the index pointed at evidence or outside the tier, the cleaner refuses to delete it.
    traversal = index.add(f"../evidence/{ORG}/{CAMPUS}/{evidence.parent.name}/clip.mp4", long_ago)
    absolute = index.add(str(evidence), long_ago)
    outside = index.add("../secret.mp4", long_ago)
    legit = index.add(recording(tmp_path, long_ago), long_ago)
    cleaner = RecordingsCleaner(tmp_path / "recordings", index)
    assert cleaner.resolve("../evidence/x.mp4") is None and cleaner.resolve("/etc/passwd") is None
    assert cleaner.resolve(f"{ORG}/{CAMPUS}/{CAM}/2026-11-16/a.mp4") is not None
    report = cleaner.purge_expired()
    assert report.refused_paths == 3 and report.deleted_files == 1
    assert evidence.read_bytes() == b"evidence" and sibling.exists()
    assert set(index.purged) == {traversal, absolute, outside, legit}  # rows go; the files never do
    # Orphan sweeping and tier sizing walk the recordings tier only.
    stray = recording(tmp_path, long_ago - timedelta(days=1), uuid4())
    old = time.time() - 100 * 3600
    os.utime(tmp_path / "recordings" / stray, (old, old))
    os.utime(evidence, (old, old))
    cleaner.sweep_orphans(48 * 3600, indexed=set(), report=report)
    assert report.orphans_removed == 1 and not (tmp_path / "recordings" / stray).exists() and evidence.exists()
    assert tier_sizes(tmp_path)["evidence"] == len(b"evidence")


def test_sweep_without_an_index_removes_nothing_and_missing_files_are_tolerated(tmp_path: Path) -> None:
    index = FakeIndex(default_seconds=60)
    gone = index.add(f"{ORG}/{CAMPUS}/{CAM}/2026-01-01/20260101T000000.mp4", datetime.now(UTC) - timedelta(days=1))
    cleaner = RecordingsCleaner(tmp_path / "recordings", index)
    report = cleaner.purge_expired()
    assert report.missing_files == 1 and index.purged == [gone]
    stray = recording(tmp_path, datetime.now(UTC) - timedelta(days=9))
    old = time.time() - 100 * 3600
    os.utime(tmp_path / "recordings" / stray, (old, old))
    cleaner.sweep_orphans(1, indexed=None, report=report)
    assert report.orphans_removed == 0 and (tmp_path / "recordings" / stray).exists()


def test_spool_cleanup_and_disk_measurement(tmp_path: Path) -> None:
    settings = RetentionSettings(storage_root=tmp_path, _env_file=None)
    spool = settings.frames_root / str(CAM)
    spool.mkdir(parents=True)
    stale, recent = spool / "1-1.jpg", spool / "2-2.jpg"
    stale.write_bytes(b"a")
    recent.write_bytes(b"b")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))
    report = clean_frame_spool(settings.frames_root, 600, CleanupReport())
    assert report.spool_removed == 1 and recent.exists() and not stale.exists()
    usage = measure(settings.storage_root)
    assert usage.total_bytes > 0 and 0 <= usage.percent_used <= 100
    assert settings.evidence_root == tmp_path / "evidence"


class FakeApi(FakeIndex):
    """The daemon's whole Core API surface, for one run."""

    def __init__(self, reid_error: bool = False, chat_error: bool = False) -> None:
        super().__init__(3600)
        self.reid_error = reid_error
        self.chat_error = chat_error

    def purge_chat_sessions(self) -> int:
        if self.chat_error:
            from src.main import CoreApiError

            raise CoreApiError("Core API answered 503.")
        return 2

    def purge_reid_observations(self) -> int:
        if self.reid_error:
            from src.main import CoreApiError

            raise CoreApiError("Core API answered 503.")
        return 3

    def report_disk(self, total: int, used: int, free: int) -> dict[str, object]:
        return {"level": "OK"}


def test_each_run_also_purges_expired_reid_observations(tmp_path: Path) -> None:
    """Decision D-024: Person Search observations expire with the campus retention."""
    from src.main import run_once

    settings = RetentionSettings(storage_root=tmp_path, service_token="t")  # type: ignore[arg-type]
    report = run_once(settings, FakeApi())  # type: ignore[arg-type]
    assert report.reid_observations_removed == 3 and report.errors == []
    failed = run_once(settings, FakeApi(reid_error=True))  # type: ignore[arg-type]
    assert failed.reid_observations_removed == 0 and any("Re-ID" in e for e in failed.errors)


def test_each_run_also_purges_inactive_ai_chat_sessions(tmp_path: Path) -> None:
    """Decision D-027: chatbot sessions inactive longer than AI_CHAT_RETENTION_DAYS (30) are purged each pass; a
    failure is reported and does not stop the rest of the pass."""
    from src.main import run_once

    settings = RetentionSettings(storage_root=tmp_path, service_token="t")  # type: ignore[arg-type]
    report = run_once(settings, FakeApi())  # type: ignore[arg-type]
    assert report.chat_sessions_removed == 2 and report.errors == []
    failed = run_once(settings, FakeApi(chat_error=True))  # type: ignore[arg-type]
    assert failed.chat_sessions_removed == 0 and failed.reid_observations_removed == 3
    assert any("AI chat sessions" in e for e in failed.errors)


def test_the_chat_purge_calls_the_internal_route(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import httpx

    from src.main import CoreApi

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path} {request.headers['X-Service-Token']}")
        return httpx.Response(200, json={"deleted": 4, "retention_days": 30})

    settings = RetentionSettings(service_token="svc")  # type: ignore[arg-type]
    api = CoreApi(settings)
    api._client = httpx.Client(
        base_url="http://core/api/v1", headers={"X-Service-Token": "svc"}, transport=httpx.MockTransport(handler)
    )
    assert api.purge_chat_sessions() == 4
    assert seen == ["POST /api/v1/internal/assistant/purge-expired svc"]
