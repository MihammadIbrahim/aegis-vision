"""Storage retention daemon (decision D-023).

    python -m src.main [--once] [--log-level INFO]

Every ``RETENTION_INTERVAL_SECONDS``: purge expired routine recordings (as the Core API lists them),
sweep orphan recording files and stale CV spool frames, measure the disk and report it. The
evidence tier is never read or written by this process.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from uuid import UUID

import httpx
from pydantic import ValidationError

from src.cleaner import (
    CleanupReport,
    ExpiredSegment,
    RecordingsCleaner,
    clean_frame_spool,
)
from src.config import RetentionSettings
from src.monitor import measure, tier_sizes

log = logging.getLogger("storage_retention")


class CoreApiError(Exception):
    pass


class CoreApi:
    def __init__(self, settings: RetentionSettings) -> None:
        token = settings.service_token.get_secret_value()
        if not token:
            raise CoreApiError("RETENTION_SERVICE_TOKEN is not set.")
        self._client = httpx.Client(
            base_url=settings.api_base_url.rstrip("/") + "/api/v1",
            headers={"X-Service-Token": token, "User-Agent": "cctv-storage-retention"},
            timeout=settings.request_timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as error:
            raise CoreApiError(f"Core API unreachable ({type(error).__name__}).") from None
        if response.status_code >= 400:
            raise CoreApiError(f"Core API answered {response.status_code}.")
        return response

    def expired(self, limit: int) -> list[ExpiredSegment]:
        rows = self._call("GET", "/internal/recordings/expired", params={"limit": limit}).json()
        return [ExpiredSegment(UUID(r["segment_id"]), r["relative_path"]) for r in rows]

    def purge(self, segment_ids: list[UUID]) -> int:
        body = {"segment_ids": [str(i) for i in segment_ids]}
        return int(self._call("POST", "/internal/recordings/purge", json=body).json()["purged"])

    def purge_reid_observations(self) -> int:
        """Person Search observations past their campus's retention (decision D-024; the API deletes the
        rows and their crops)."""
        return int(self._call("POST", "/internal/reid/purge-expired").json()["deleted"])

    def purge_chat_sessions(self) -> int:
        """AI chatbot sessions inactive for longer than the API's AI_CHAT_RETENTION_DAYS (decision D-027)."""
        return int(self._call("POST", "/internal/assistant/purge-expired").json()["deleted"])

    def report_disk(self, total: int, used: int, free: int) -> dict[str, object]:
        body = {"total_bytes": total, "used_bytes": used, "free_bytes": free}
        return dict(self._call("POST", "/internal/storage/report", json=body).json())


def run_once(settings: RetentionSettings, api: CoreApi) -> CleanupReport:
    report = CleanupReport()
    cleaner = RecordingsCleaner(settings.recordings_root, api, batch_size=settings.batch_size)
    try:
        cleaner.purge_expired(report)
    except CoreApiError as error:
        report.errors.append(str(error))
    # Orphans are only swept when the API answers, and only with the index in hand; the daemon has
    # no index of its own, so a sweep without it removes nothing.
    try:
        report.reid_observations_removed = api.purge_reid_observations()
    except CoreApiError as error:
        report.errors.append(f"Re-ID observations: {error}")
    try:
        report.chat_sessions_removed = api.purge_chat_sessions()
    except CoreApiError as error:
        report.errors.append(f"AI chat sessions: {error}")
    clean_frame_spool(settings.frames_root, settings.spool_max_age_seconds, report)
    usage = measure(settings.storage_root)
    try:
        answer = api.report_disk(usage.total_bytes, usage.used_bytes, usage.free_bytes)
        level = answer.get("level")
    except CoreApiError as error:
        report.errors.append(str(error))
        level = "UNREPORTED"
    sizes = tier_sizes(settings.storage_root)
    log.info(
        "disk %.1f%% used (%s); deleted %d expired files (%d missing, %d refused), purged %d rows, "
        "spool -%d, dirs -%d, Re-ID observations -%d, chat sessions -%d; tiers: %s; errors: %d",
        usage.percent_used, level, report.deleted_files, report.missing_files, report.refused_paths,
        report.purged_rows, report.spool_removed, report.empty_dirs_removed, report.reid_observations_removed,
        report.chat_sessions_removed,
        ", ".join(f"{k}={v >> 20} MiB" for k, v in sizes.items()), len(report.errors),
    )  # fmt: skip
    for error in report.errors:
        log.warning("retention: %s", error)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Routine-recording retention and disk monitoring")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = RetentionSettings()
        api = CoreApi(settings)
    except (ValidationError, CoreApiError) as error:
        log.error("invalid settings: %s", error)
        return 2
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info(
        "retention daemon: every %.0f s under %s (evidence tier excluded)",
        settings.interval_seconds,
        settings.storage_root,
    )
    try:
        while not stop.is_set():
            started = time.monotonic()
            run_once(settings, api)
            if args.once:
                break
            stop.wait(max(1.0, settings.interval_seconds - (time.monotonic() - started)))
    finally:
        api.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
