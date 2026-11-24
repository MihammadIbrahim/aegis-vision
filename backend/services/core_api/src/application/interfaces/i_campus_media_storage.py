"""Files the application keeps for a campus (recordings, clips, evidence, exports, when those
features exist), as needed by permanent campus deletion.

Deletion is done in three steps around the database transaction:
1. ``stage`` (before commit): move the campus's files out of reach in one step. Reversible.
2. ``restore`` (only if the database commit fails): put them back.
3. ``finalize`` (after commit): erase the staged files. Raises ``MediaCleanupFailed`` if any
   remain; the caller must then not report success.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


class MediaCleanupFailed(Exception):
    """Some staged files could not be erased. The message never contains file contents."""


@dataclass(frozen=True, slots=True)
class StagedMediaDeletion:
    moves: tuple[tuple[Path, Path], ...] = field(default_factory=tuple)  # (original, staged)


class ICampusMediaStorage(Protocol):
    def stage(self, organization_id: UUID, campus_id: UUID, *, whole_organization: bool) -> StagedMediaDeletion: ...
    def restore(self, staged: StagedMediaDeletion) -> None: ...
    def finalize(self, staged: StagedMediaDeletion) -> None: ...
    def write_file(self, organization_id: UUID, campus_id: UUID, relative_path: str, data: bytes) -> None:
        """Store a campus file (for example a manual-report photo) under the campus folder."""
        ...

    def read_file(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> bytes | None: ...
    def local_path(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> Path:
        """Where a campus file lives on disk (inside the campus folder; parents are created), for
        files too large to pass around in memory, such as personnel imports."""
        ...

    def remove_file(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> None: ...

    # Storage tiers (decision D-023): "recordings" (routine, volatile), "evidence" (permanent) and
    # "live" (the newest frame per camera). Paths are checked to stay inside the campus's folder.
    def tier_path(self, tier: Any, organization_id: UUID, campus_id: UUID, relative_path: str = "") -> Path: ...
    def write_tier_file(
        self, tier: Any, organization_id: UUID, campus_id: UUID, relative_path: str, data: bytes
    ) -> Path: ...
    def remove_tier_tree(self, tier: Any, organization_id: UUID, campus_id: UUID, relative_path: str) -> None: ...
    def sweep(self) -> None:
        """Erase anything left staged by an earlier failed ``finalize``."""
        ...
