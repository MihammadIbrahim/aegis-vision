"""Runs one worker thread per camera and keeps the set of workers in step with the Core API's
camera list (decision D-023).

Worker loop: start ffmpeg → for every JPEG on its stdout: sample, publish (live frame + CV spool),
mark the camera seen; every second rotate finished segments; when the process ends: rotate the
last file, report the failure, wait (back-off) and reconnect. Stopping a worker ends ffmpeg,
rotates what is complete and removes the live frame.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from src.camera_health_monitor import HealthMonitor
from src.camera_registry import CameraSource
from src.config import IngestionSettings
from src.frame_publisher import FramePublisher
from src.frame_sampler import FrameSampler
from src.frame_store import FrameSpool, LiveFrameStore
from src.rtsp_client import StreamFailed, StreamPlan, StreamProcess
from src.segmented_writer import INCOMING, CompletedSegment, SegmentRotator

logger = logging.getLogger(__name__)

SegmentReporter = Callable[[CompletedSegment], None]


class CameraWorker(threading.Thread):
    def __init__(
        self,
        source: CameraSource,
        settings: IngestionSettings,
        health: HealthMonitor,
        on_segment: SegmentReporter,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        super().__init__(name=f"camera-{source.camera_id}", daemon=True)
        self.source = source
        self._settings = settings
        self._health = health
        self._clock = clock
        self._wall = wall_clock
        self._stopping = threading.Event()
        self._process: StreamProcess | None = None
        spec = source.spec
        camera_dir = settings.recordings_dir(spec.organization_id, spec.campus_id, spec.camera_id)
        self.rotator = SegmentRotator(spec.camera_id, spec.organization_id, spec.campus_id, camera_dir, on_segment)
        self.publisher = FramePublisher(
            LiveFrameStore(settings.live_frame_path(spec.organization_id, spec.campus_id, spec.camera_id)),
            FrameSpool(settings.spool_dir(spec.camera_id), settings.spool_max_frames),
            spec.camera_id,
        )
        self.attempts = 0

    def plan(self) -> StreamPlan:
        assert self.source.location is not None
        return StreamPlan(
            kind=self.source.kind,
            location=self.source.location,
            segment_dir=self.rotator.incoming_dir,
            segment_seconds=self._settings.segment_seconds,
            sample_fps=self._settings.frame_sample_fps,
            frame_width=self._settings.frame_width,
            transcode=self.source.kind == "file" or self._settings.rtsp_transcode,
            rtsp_transport=self._settings.rtsp_transport,
            ffmpeg=self._settings.ffmpeg_path,
        )

    def run(self) -> None:
        camera_id = self.source.camera_id
        while not self._stopping.is_set():
            self.attempts += 1
            sampler = FrameSampler(self._settings.frame_sample_fps)
            process = StreamProcess(self.plan())
            self._process = process
            last_rotate = self._clock()
            try:
                process.start()
                for jpeg in process.frames():
                    now = self._clock()
                    if sampler.admit(now):
                        self.publisher.publish(jpeg, int(self._wall().timestamp() * 1000))
                        self._health.frame_seen(camera_id, now)
                    if now - last_rotate >= 1.0:
                        self.rotator.rotate()
                        last_rotate = now
                    if self._stopping.is_set():
                        process.stop()
            except StreamFailed as failure:
                self.rotator.rotate(final=True)
                if self._stopping.is_set():
                    break
                self._health.stream_failed(camera_id, failure.reason)
                logger.warning("camera %s: %s (attempt %d)", camera_id, failure.reason, self.attempts)
                self._wait_before_retry()
            else:
                self.rotator.rotate(final=True)
        self.publisher.stop()

    def _wait_before_retry(self) -> None:
        low, high = self._settings.reconnect_min_seconds, self._settings.reconnect_max_seconds
        delay = min(high, low * (2 ** min(self.attempts - 1, 8)))
        delay += random.uniform(0, min(1.0, delay / 4))  # noqa: S311 - jitter only
        self._stopping.wait(delay)

    def stop(self) -> None:
        self._stopping.set()
        if self._process is not None:
            self._process.stop()


class StreamManager:
    def __init__(self, settings: IngestionSettings, health: HealthMonitor, on_segment: SegmentReporter) -> None:
        self._settings = settings
        self._health = health
        self._on_segment = on_segment
        self._workers: dict[UUID, CameraWorker] = {}
        self._sources: dict[UUID, CameraSource] = {}

    @property
    def sources(self) -> dict[UUID, CameraSource]:
        return dict(self._sources)

    def apply(self, wanted: list[CameraSource]) -> None:
        from src.camera_registry import diff_sources

        to_start, to_stop = diff_sources(self._sources, wanted)
        for camera_id in to_stop:
            self._stop_one(camera_id)
            self._health.forget(camera_id)
        for source in to_start:
            self._stop_one(source.camera_id)
            self._sources[source.camera_id] = source
            if source.kind == "unavailable":
                self._health.unavailable(source.camera_id, source.reason or "No usable source.")
                continue
            worker = CameraWorker(source, self._settings, self._health, self._on_segment)
            self._workers[source.camera_id] = worker
            worker.start()
            logger.info("camera %s: ingesting from a %s source", source.camera_id, source.kind)

    def _stop_one(self, camera_id: UUID) -> None:
        worker = self._workers.pop(camera_id, None)
        self._sources.pop(camera_id, None)
        if worker is not None:
            worker.stop()
            worker.join(timeout=10)

    def stop_all(self) -> None:
        for camera_id in list(self._workers):
            self._stop_one(camera_id)
        self._sources.clear()

    def incoming_dirs(self) -> list[str]:
        return [str(w.rotator.incoming_dir) for w in self._workers.values()]


__all__ = ["INCOMING", "CameraWorker", "StreamManager"]
