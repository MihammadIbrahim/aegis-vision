"""Video ingestion service (decision D-023).

    python -m src.main [--config settings.yaml] [--run-seconds N] [--log-level INFO]

Every ``INGEST_REGISTRY_REFRESH_SECONDS`` the camera list is read from the Core API; workers are
started, restarted or stopped to match it. Every second camera health is evaluated and reported.
Segments are reported as they complete; a report that fails is retried on the next rotation of
that camera (the file stays in place, so nothing is lost).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from src.camera_health_monitor import HealthMonitor
from src.camera_registry import resolve_sources
from src.config import IngestionSettings
from src.core_api_client import CoreApiClient, CoreApiError
from src.segmented_writer import CompletedSegment
from src.stream_manager import StreamManager

log = logging.getLogger("video_ingestion")


class Service:
    def __init__(self, settings: IngestionSettings, api: CoreApiClient) -> None:
        self._settings = settings
        self._api = api
        self._stop = threading.Event()
        self._pending: dict[Path, CompletedSegment] = {}
        self._pending_lock = threading.Lock()
        self.health = HealthMonitor(settings.offline_after_seconds, settings.heartbeat_seconds, self._report_status)
        self.manager = StreamManager(settings, self.health, self._report_segment)

    # --- Core API reports -----------------------------------------------------------------------

    def _report_status(self, camera_id: UUID, state: str, reason: str | None, seen_at: datetime | None) -> None:
        self._api.report_status(camera_id, state, reason, seen_at)
        log.info("camera %s: %s%s", camera_id, state, f" ({reason})" if reason else "")

    def _report_segment(self, segment: CompletedSegment) -> None:
        with self._pending_lock:
            self._pending[segment.path] = segment
        self._flush_pending()

    def _flush_pending(self) -> None:
        with self._pending_lock:
            items = list(self._pending.values())
        for segment in items:
            try:
                outcome = self._api.register_segment(
                    segment.camera_id, segment.started_at, segment.ended_at, segment.relative_path, segment.size_bytes
                )
            except CoreApiError as error:
                log.warning("segment %s not registered yet: %s", segment.path.name, error)
                return  # keep the order; try again later
            with self._pending_lock:
                self._pending.pop(segment.path, None)
            log.info("camera %s: segment %s %s", segment.camera_id, segment.path.name, outcome)

    # --- Main loop ------------------------------------------------------------------------------

    def refresh_cameras(self) -> None:
        try:
            cameras = self._api.cameras()
        except CoreApiError as error:
            log.warning("camera list not refreshed: %s", error)
            return
        self.manager.apply(resolve_sources(cameras, self._settings.file_sources, self._settings.campus_id))

    def run(self, run_seconds: float | None = None) -> None:
        started = time.monotonic()
        next_refresh = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_refresh:
                    self.refresh_cameras()
                    next_refresh = now + self._settings.registry_refresh_seconds
                self.health.evaluate(now)
                self._flush_pending()
                if run_seconds is not None and now - started >= run_seconds:
                    break
                self._stop.wait(1.0)
        finally:
            self.manager.stop_all()
            self._flush_pending()
            for camera_id in list(self.manager.sources):
                self.health.forget(camera_id)

    def stop(self) -> None:
        self._stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CCTV video ingestion: recordings, live frames, CV frames")
    parser.add_argument("--config", type=Path, help="YAML settings file (same keys as INGEST_* without the prefix)")
    parser.add_argument("--run-seconds", type=float, help="stop after this long (for tests and demos)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = IngestionSettings.from_sources(args.config)
        api = CoreApiClient(
            settings.api_base_url,
            settings.service_token.get_secret_value(),
            timeout_seconds=settings.request_timeout_seconds,
        )
    except (ValidationError, ValueError, OSError, CoreApiError) as error:
        message = "; ".join(e["msg"] for e in error.errors()) if isinstance(error, ValidationError) else str(error)
        log.error("invalid settings: %s", message)
        return 2
    service = Service(settings, api)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: service.stop())
    log.info(
        "ingesting to %s (sample %.1f fps, %d s segments, offline after %.0f s)",
        settings.storage_root, settings.frame_sample_fps, settings.segment_seconds, settings.offline_after_seconds,
    )  # fmt: skip
    try:
        service.run(args.run_seconds)
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
