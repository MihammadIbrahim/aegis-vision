"""Video input: the ``FrameSource`` interface and a recorded-video implementation.

The pipeline only depends on ``FrameSource``. ``VideoFileSource`` reads a recorded file;
``SpoolFrameSource`` consumes the JPEG frames the ``video_ingestion`` service samples from a live
camera into ``<storage root>/frames/<camera id>/`` (decision D-023), so the same pipeline runs on
live cameras without opening the stream a second time.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol
from uuid import UUID

import cv2
import numpy as np

from src.schemas import Frame

logger = logging.getLogger(__name__)


class VideoSourceError(Exception):
    """The video cannot be opened or decoded. The message never contains frame data."""


class FrameSource(Protocol):
    source_id: str
    camera_id: UUID

    def frames(self) -> Iterator[Frame]: ...

    def close(self) -> None: ...


class VideoFileSource:
    """Reads a recorded video file and yields sampled frames.

    * **Sampling:** about ``sample_fps`` frames per second of video time, the first frame always
      included. ``None`` yields every frame.
    * **Timestamps:** ``start_time`` plus the frame's offset. Without ``start_time``, the file's
      modification time is used, so re-processing the same file gives the same timestamps (and
      therefore the same detection IDs).
    * **Errors:** a file that cannot be opened raises ``VideoSourceError``. Isolated unreadable frames
      are skipped; more than ``max_decode_failures`` in a row raises ``VideoSourceError``.
    """

    def __init__(
        self,
        path: Path | str,
        camera_id: UUID,
        *,
        sample_fps: float | None = 5.0,
        start_time: datetime | None = None,
        max_frames: int | None = None,
        fallback_fps: float = 25.0,
        max_decode_failures: int = 5,
    ) -> None:
        self._path = Path(path)
        if sample_fps is not None and sample_fps <= 0:
            raise ValueError("sample_fps must be positive")
        if start_time is not None and start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")
        if not self._path.is_file():
            raise VideoSourceError(f"Video file not found: {self._path.name}")
        self.camera_id = camera_id
        self._sample_fps = sample_fps
        self._max_frames = max_frames
        self._max_failures = max_decode_failures
        stat = self._path.stat()
        self._start = start_time or datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        digest = hashlib.sha256(f"{self._path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
        self.source_id = f"file:{digest[:16]}"
        self._capture = cv2.VideoCapture(str(self._path))
        if not self._capture.isOpened():
            self._capture.release()
            raise VideoSourceError(f"Cannot open video file: {self._path.name}")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0 or fps != fps:  # 0 or NaN: the container does not say
            logger.warning("video %s reports no frame rate; assuming %.1f fps", self._path.name, fallback_fps)
            fps = fallback_fps
        self.native_fps = fps
        self._total = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._closed = False

    def frames(self) -> Iterator[Frame]:
        if self._closed:
            raise VideoSourceError("The video source is closed.")
        interval = None if self._sample_fps is None else 1.0 / self._sample_fps
        next_due = 0.0
        index = -1
        yielded = 0
        failures = 0
        try:
            while self._max_frames is None or yielded < self._max_frames:
                ok, image = self._capture.read()
                index += 1
                if not ok or image is None:
                    # End of stream, unless the container says there are more frames.
                    if self._total and index < self._total and failures < self._max_failures:
                        failures += 1
                        logger.warning("skipping unreadable frame %d of %s", index, self._path.name)
                        continue
                    if self._total and index < self._total:
                        raise VideoSourceError(
                            f"Too many unreadable frames in {self._path.name} (from frame {index - failures})."
                        )
                    return
                failures = 0
                offset = index / self.native_fps
                if interval is not None and offset + 1e-9 < next_due:
                    continue
                if interval is not None:
                    next_due += interval
                    while next_due <= offset:
                        next_due += interval
                if image.ndim == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                yielded += 1
                yield Frame(
                    camera_id=self.camera_id,
                    source_id=self.source_id,
                    index=index,
                    timestamp=self._start + timedelta(seconds=offset),
                    image=image,
                )
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._capture.release()
            self._closed = True

    def __enter__(self) -> VideoFileSource:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()


class SpoolFrameSource:
    """Frames the ingestion service wrote as ``<unix ms>-<n>.jpg``; each file is deleted once read.

    Files are taken oldest first. When the folder is empty the source waits ``poll_seconds`` and
    gives up after ``idle_timeout_seconds`` without a frame (None: wait forever). The timestamp is
    the capture time encoded in the name; ``index`` counts frames taken by this source.
    """

    def __init__(
        self,
        directory: Path | str,
        camera_id: UUID,
        *,
        poll_seconds: float = 0.2,
        idle_timeout_seconds: float | None = 30.0,
        max_frames: int | None = None,
    ) -> None:
        self._dir = Path(directory)
        self.camera_id = camera_id
        self.source_id = f"spool:{camera_id}"
        self._poll = poll_seconds
        self._idle = idle_timeout_seconds
        self._max_frames = max_frames
        self._closed = False

    def _next_file(self) -> Path | None:
        try:
            names = sorted(n for n in os.listdir(self._dir) if n.endswith(".jpg"))
        except FileNotFoundError:
            return None
        return self._dir / names[0] if names else None

    def frames(self) -> Iterator[Frame]:
        index = 0
        last_frame_at = time.monotonic()
        while not self._closed and (self._max_frames is None or index < self._max_frames):
            path = self._next_file()
            if path is None:
                if self._idle is not None and time.monotonic() - last_frame_at > self._idle:
                    return
                time.sleep(self._poll)
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            finally:
                path.unlink(missing_ok=True)
            image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                logger.warning("spool %s: an unreadable frame was skipped", self.camera_id)
                continue
            stamp = path.name.split("-", 1)[0]
            captured = datetime.fromtimestamp(int(stamp) / 1000, tz=UTC) if stamp.isdigit() else datetime.now(UTC)
            last_frame_at = time.monotonic()
            yield Frame(camera_id=self.camera_id, source_id=self.source_id, index=index, timestamp=captured, image=image)
            index += 1

    def close(self) -> None:
        self._closed = True
