"""Tier 1 cleanup (requirements Section 17; decision D-023).

The Core API decides what has expired: ``GET /internal/recordings/expired`` applies each campus's
retention (Minutes / Hours / Days, or the server default) to the recordings index. The daemon
unlinks those files, reports them purged, and removes empty date folders.

**Evidence immunity.** The cleaner can only delete paths that resolve inside ``<root>/recordings/``.
``<root>/evidence/`` is never listed, walked, or opened; a path outside the recordings root — even
one the API would send — is refused and counted, not deleted. There is no code path that removes
evidence: that happens only through the API, by an Admin, with their password.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExpiredSegment:
    segment_id: UUID
    relative_path: str


class RetentionApi(Protocol):
    def expired(self, limit: int) -> list[ExpiredSegment]: ...

    def purge(self, segment_ids: list[UUID]) -> int: ...


@dataclass(slots=True)
class CleanupReport:
    deleted_files: int = 0
    missing_files: int = 0
    refused_paths: int = 0
    purged_rows: int = 0
    orphans_removed: int = 0
    spool_removed: int = 0
    empty_dirs_removed: int = 0
    reid_observations_removed: int = 0
    chat_sessions_removed: int = 0  # AI chatbot sessions inactive past AI_CHAT_RETENTION_DAYS (D-027)
    errors: list[str] = field(default_factory=list)


class RecordingsCleaner:
    def __init__(self, recordings_root: Path, api: RetentionApi, *, batch_size: int = 500) -> None:
        self._root = recordings_root.resolve()
        self._api = api
        self._batch = batch_size

    def resolve(self, relative_path: str) -> Path | None:
        """The file for an index path, or None when it would leave the recordings tier."""
        if (
            not relative_path
            or relative_path.startswith(("/", "\\"))
            or ".." in relative_path.replace("\\", "/").split("/")
        ):
            return None
        target = (self._root / relative_path).resolve()
        if self._root not in target.parents:
            return None
        return target

    def purge_expired(self, report: CleanupReport | None = None) -> CleanupReport:
        report = report or CleanupReport()
        while True:
            expired = self._api.expired(self._batch)
            if not expired:
                return report
            done: list[UUID] = []
            for segment in expired:
                target = self.resolve(segment.relative_path)
                if target is None:
                    report.refused_paths += 1
                    logger.error(
                        "refusing to delete a path outside the recordings tier for segment %s", segment.segment_id
                    )
                    done.append(segment.segment_id)  # the index row goes; the file (wherever it is) stays
                    continue
                try:
                    target.unlink()
                    report.deleted_files += 1
                except FileNotFoundError:
                    report.missing_files += 1
                except OSError as error:
                    report.errors.append(f"{segment.segment_id}: {type(error).__name__}")
                    continue  # keep the row; try again next pass
                done.append(segment.segment_id)
                self._remove_empty_parents(target.parent, report)
            if done:
                report.purged_rows += self._api.purge(done)
            if len(expired) < self._batch:
                return report

    def _remove_empty_parents(self, directory: Path, report: CleanupReport) -> None:
        # Date folders and camera folders only; never the campus/org level (other services create them).
        current = directory
        for _ in range(2):
            if current == self._root or self._root not in current.parents:
                return
            try:
                os.rmdir(current)
                report.empty_dirs_removed += 1
            except OSError:
                return
            current = current.parent

    def sweep_orphans(self, max_age_seconds: float, indexed: set[str] | None, report: CleanupReport) -> CleanupReport:
        """Remove recording files older than ``max_age_seconds`` that the index does not know
        (left behind by a crash between writing and registering). ``indexed`` is the set of
        relative paths the API still lists; None skips the check (nothing is removed)."""
        if indexed is None or not self._root.exists():
            return report
        cutoff = time.time() - max_age_seconds
        for path in self._root.rglob("*.mp4"):
            relative = path.relative_to(self._root).as_posix()
            if relative in indexed or "/incoming/" in f"/{relative}":
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    report.orphans_removed += 1
            except OSError as error:
                report.errors.append(f"orphan {path.name}: {type(error).__name__}")
        return report


def clean_frame_spool(frames_root: Path, max_age_seconds: float, report: CleanupReport) -> CleanupReport:
    """Sampled frames CV inference never consumed (it was stopped) are removed after a while."""
    if not frames_root.exists():
        return report
    cutoff = time.time() - max_age_seconds
    for path in frames_root.rglob("*.jpg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                report.spool_removed += 1
        except OSError:
            continue
    return report
