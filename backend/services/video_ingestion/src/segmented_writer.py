"""Chunk rotation for routine recordings (decision D-023).

ffmpeg writes ``<UTC timestamp>.mp4`` files into the camera's ``incoming/`` folder, one after the
other. A file is complete once a newer one exists (or the process ended). ``SegmentRotator`` moves
each complete file into the Tier 1 layout

    recordings/<org>/<campus>/<camera>/<YYYY-MM-DD>/<YYYYmmddTHHMMSS>.mp4

and reports it to the Core API, which indexes it. Nothing here deletes anything: expiry is the
retention daemon's job, and the evidence tier is never touched.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

INCOMING = "incoming"
_NAME = re.compile(r"^(\d{8}T\d{6})\.mp4$")


@dataclass(frozen=True, slots=True)
class CompletedSegment:
    camera_id: UUID
    path: Path  # final location
    relative_path: str  # <org>/<campus>/<camera>/<date>/<name>.mp4
    started_at: datetime
    ended_at: datetime
    size_bytes: int


def parse_started_at(name: str) -> datetime | None:
    match = _NAME.match(name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


class SegmentRotator:
    def __init__(
        self,
        camera_id: UUID,
        organization_id: UUID,
        campus_id: UUID,
        camera_dir: Path,
        on_completed: Callable[[CompletedSegment], None],
    ) -> None:
        self.camera_id = camera_id
        self._org = organization_id
        self._campus = campus_id
        self._camera_dir = camera_dir
        self._incoming = camera_dir / INCOMING
        self._on_completed = on_completed

    @property
    def incoming_dir(self) -> Path:
        return self._incoming

    def rotate(self, *, final: bool = False) -> list[CompletedSegment]:
        """Move every complete file out of ``incoming/``. The newest file is still being written
        unless ``final`` (the process ended)."""
        try:
            names = sorted(n for n in os.listdir(self._incoming) if _NAME.match(n))
        except FileNotFoundError:
            return []
        complete = names if final else names[:-1]
        done: list[CompletedSegment] = []
        for name in complete:
            segment = self._place(self._incoming / name)
            if segment is not None:
                done.append(segment)
                try:
                    self._on_completed(segment)
                except Exception:  # noqa: BLE001 - the file is already in place; reported, retried later
                    logger.exception("camera %s: segment %s could not be reported", self.camera_id, segment.path.name)
        return done

    def _place(self, source: Path) -> CompletedSegment | None:
        started_at = parse_started_at(source.name)
        if started_at is None:
            return None
        try:
            stat = source.stat()
        except FileNotFoundError:
            return None
        if stat.st_size == 0:
            source.unlink(missing_ok=True)  # an empty file from a stream that died at once
            return None
        ended_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if ended_at <= started_at:
            ended_at = started_at.replace(microsecond=0) + (ended_at - ended_at.replace(microsecond=0))
            ended_at = max(ended_at, started_at + _one_second())
        target = self._camera_dir / started_at.strftime("%Y-%m-%d") / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():  # the same second twice (restart): keep both
            target = target.with_name(f"{target.stem}-{stat.st_ino % 10000:04d}.mp4")
        source.replace(target)
        relative = f"{self._org}/{self._campus}/{self.camera_id}/{target.parent.name}/{target.name}"
        return CompletedSegment(self.camera_id, target, relative, started_at, ended_at, stat.st_size)


def _one_second():  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(seconds=1)
