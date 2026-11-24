"""Disk capacity of the storage node (decision D-023). The measurement is sent to the Core API,
which keeps the last report, applies its warning / critical thresholds and notifies the Admins."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total_bytes: int
    used_bytes: int
    free_bytes: int

    @property
    def percent_used(self) -> float:
        return 0.0 if self.total_bytes <= 0 else round(100.0 * self.used_bytes / self.total_bytes, 2)


def measure(root: Path) -> DiskUsage:
    """Usage of the file system holding ``root`` (created if missing, so a fresh node reports too)."""
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    return DiskUsage(usage.total, usage.used, usage.free)


def tier_sizes(root: Path) -> dict[str, int]:
    """Bytes used by each tier (for the log line only; evidence is measured, never touched)."""
    sizes: dict[str, int] = {}
    for tier in ("recordings", "evidence", "live", "frames"):
        total = 0
        base = root / tier
        if base.exists():
            for path in base.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        sizes[tier] = total
    return sizes
