from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from src.video_source import VideoFileSource, VideoSourceError
from tests.conftest import START


def test_frames_are_sampled_and_carry_camera_and_time(video, camera_id: UUID) -> None:
    path = video(frames=50, fps=25.0)  # 2 seconds
    source = VideoFileSource(path, camera_id, sample_fps=5.0, start_time=START)
    frames = list(source.frames())
    assert [f.index for f in frames] == [0, 5, 10, 15, 20, 25, 30, 35, 40, 45]
    assert all(f.camera_id == camera_id and f.source_id == source.source_id for f in frames)
    assert frames[1].timestamp == START + timedelta(seconds=0.2)
    assert frames[0].image.shape == (48, 64, 3) and frames[0].image.dtype.name == "uint8"
    assert source.native_fps == 25.0


def test_every_frame_and_limits(video, camera_id: UUID) -> None:
    path = video(frames=12, fps=10.0)
    assert len(list(VideoFileSource(path, camera_id, sample_fps=None).frames())) == 12
    assert len(list(VideoFileSource(path, camera_id, sample_fps=None, max_frames=4).frames())) == 4
    # A sampling rate above the native rate yields each frame once.
    assert len(list(VideoFileSource(path, camera_id, sample_fps=50).frames())) == 12


def test_the_source_identity_and_default_times_are_stable(video, camera_id: UUID) -> None:
    path = video(frames=5)
    first = VideoFileSource(path, camera_id)
    second = VideoFileSource(path, camera_id)
    assert first.source_id == second.source_id and first.source_id.startswith("file:")
    assert [f.timestamp for f in first.frames()] == [f.timestamp for f in second.frames()]
    assert all(f.timestamp.tzinfo is not None for f in VideoFileSource(path, camera_id).frames())


def test_missing_and_undecodable_files_are_refused(tmp_path: Path, camera_id: UUID) -> None:
    with pytest.raises(VideoSourceError, match="not found"):
        VideoFileSource(tmp_path / "missing.mp4", camera_id)
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"this is not a video" * 50)
    with pytest.raises(VideoSourceError, match="Cannot open"):
        VideoFileSource(corrupt, camera_id)
    with pytest.raises(ValueError):
        VideoFileSource(corrupt, camera_id, sample_fps=0)


def test_the_capture_is_released_and_cannot_be_reused(video, camera_id: UUID) -> None:
    source = VideoFileSource(video(frames=3), camera_id)
    with source:
        next(iter(source.frames()))
    with pytest.raises(VideoSourceError, match="closed"):
        list(source.frames())


def test_a_truncated_file_ends_cleanly_or_reports_an_error(video, camera_id: UUID) -> None:
    path = video(frames=40)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])  # cut in the middle of the stream
    try:
        source = VideoFileSource(path, camera_id, sample_fps=None, max_decode_failures=2)
    except VideoSourceError:
        return  # refused at open: acceptable
    try:
        frames = list(source.frames())
    except VideoSourceError as error:
        assert "unreadable frames" in str(error)
    else:
        assert len(frames) < 40


def test_spool_source_consumes_ingested_frames_oldest_first(tmp_path: Path, camera_id: UUID) -> None:
    import cv2
    import numpy as np

    from src.video_source import SpoolFrameSource

    spool = tmp_path / "frames" / str(camera_id)
    spool.mkdir(parents=True)
    for ms, n in ((1_700_000_000_500, 2), (1_700_000_000_000, 1)):
        ok, data = cv2.imencode(".jpg", np.full((48, 64, 3), n * 40, np.uint8))
        assert ok
        (spool / f"{ms:013d}-{n:06d}.jpg").write_bytes(data.tobytes())
    (spool / "0000000000001-000009.jpg").write_bytes(b"not a jpeg")
    source = SpoolFrameSource(spool, camera_id, poll_seconds=0.01, idle_timeout_seconds=0.05)
    frames = list(source.frames())
    assert [f.index for f in frames] == [0, 1] and frames[0].timestamp < frames[1].timestamp
    assert frames[0].timestamp.tzinfo is not None and frames[0].image.shape == (48, 64, 3)
    assert source.source_id == f"spool:{camera_id}" and not any(spool.iterdir())  # consumed and deleted
