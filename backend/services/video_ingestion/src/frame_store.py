"""Where sampled frames go (decision D-023):

* ``live/<org>/<campus>/<camera>/latest.jpg`` — the newest frame, replaced atomically, read by the
  Core API for the live view (``GET /live/cameras/{id}/frame``). Stale files mean no frames arrive.
* ``frames/<camera>/<unix ms>-<n>.jpg`` — a bounded spool for CV inference, which consumes and
  deletes them (``cv_inference`` ``SpoolFrameSource``). Oldest frames are dropped when the spool is
  full, so a slow or stopped analyser never fills the disk.
"""

from __future__ import annotations

import os
from pathlib import Path


def write_atomically(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(data)
    partial.replace(target)


class LiveFrameStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def update(self, jpeg: bytes) -> None:
        write_atomically(self._path, jpeg)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


class FrameSpool:
    def __init__(self, directory: Path, max_frames: int) -> None:
        self._dir = directory
        self._max = max_frames
        self._counter = 0

    @property
    def directory(self) -> Path:
        return self._dir

    def push(self, jpeg: bytes, unix_ms: int) -> Path:
        self._counter = (self._counter + 1) % 1_000_000
        target = self._dir / f"{unix_ms:013d}-{self._counter:06d}.jpg"
        write_atomically(target, jpeg)
        self.trim()
        return target

    def trim(self) -> int:
        """Drop the oldest frames beyond the limit; returns how many were dropped."""
        try:
            names = sorted(n for n in os.listdir(self._dir) if n.endswith(".jpg"))
        except FileNotFoundError:
            return 0
        excess = len(names) - self._max
        for name in names[: max(0, excess)]:
            (self._dir / name).unlink(missing_ok=True)
        return max(0, excess)
