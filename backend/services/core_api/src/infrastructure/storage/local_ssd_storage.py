"""Campus media on the local disk, under ``CCTV_STORAGE_ROOT`` (decision D-023 for the tiers):

    CCTV_STORAGE_ROOT/<organization UUID>/<campus UUID>/...            photos, imports (campus files)
    CCTV_STORAGE_ROOT/recordings/<organization>/<campus>/<camera>/...  Tier 1: routine recordings (volatile)
    CCTV_STORAGE_ROOT/evidence/<organization>/<campus>/<evidence id>/  Tier 2: the evidence vault (permanent)
    CCTV_STORAGE_ROOT/live/<organization>/<campus>/<camera>/latest.jpg the newest frame, for live viewing

The retention daemon works only inside ``recordings/``; nothing automatic ever deletes from
``evidence/``. Permanent campus deletion erases the campus's folder in every tier. Staged files are
moved into ``CCTV_STORAGE_ROOT/.deleting/``, which no campus path points into.
"""

import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import (
    MediaCleanupFailed,
    StagedMediaDeletion,
)

_STAGING = ".deleting"
Tier = Literal["recordings", "evidence", "live"]
TIERS: tuple[Tier, ...] = ("recordings", "evidence", "live")


class LocalCampusMediaStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def campus_path(self, organization_id: UUID, campus_id: UUID) -> Path:
        return self._root / str(organization_id) / str(campus_id)

    def tier_campus_path(self, tier: Tier, organization_id: UUID, campus_id: UUID) -> Path:
        return self._root / tier / str(organization_id) / str(campus_id)

    @staticmethod
    def _inside(base: Path, relative_path: str) -> Path:
        base = base.resolve()
        target = (base / relative_path).resolve()
        if base != target and base not in target.parents:
            raise ValueError("The path leaves the campus folder.")
        return target

    def _file(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> Path:
        target = self._inside(self.campus_path(organization_id, campus_id), relative_path)
        if target == self.campus_path(organization_id, campus_id).resolve():
            raise ValueError("The path leaves the campus folder.")
        return target

    def tier_path(self, tier: Tier, organization_id: UUID, campus_id: UUID, relative_path: str = "") -> Path:
        """A file or folder of one tier of this campus (never created here). Raises ``ValueError``
        when ``relative_path`` would leave the campus's folder of that tier."""
        return self._inside(self.tier_campus_path(tier, organization_id, campus_id), relative_path)

    def write_file(self, organization_id: UUID, campus_id: UUID, relative_path: str, data: bytes) -> None:
        target = self._file(organization_id, campus_id, relative_path)
        self._write_atomically(target, data)

    @staticmethod
    def _write_atomically(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.write_bytes(data)
        partial.replace(target)  # never leaves a half-written file under the final name

    def write_tier_file(
        self, tier: Tier, organization_id: UUID, campus_id: UUID, relative_path: str, data: bytes
    ) -> Path:
        target = self.tier_path(tier, organization_id, campus_id, relative_path)
        self._write_atomically(target, data)
        return target

    def remove_tier_tree(self, tier: Tier, organization_id: UUID, campus_id: UUID, relative_path: str) -> None:
        """Erase one file or folder of a tier (used for an authorized evidence deletion)."""
        target = self.tier_path(tier, organization_id, campus_id, relative_path)
        if target == self.tier_campus_path(tier, organization_id, campus_id).resolve():
            raise ValueError("Refusing to erase the whole campus tier.")
        self._erase(target)

    def local_path(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> Path:
        target = self._file(organization_id, campus_id, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def read_file(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> bytes | None:
        target = self._file(organization_id, campus_id, relative_path)
        return target.read_bytes() if target.is_file() else None

    def remove_file(self, organization_id: UUID, campus_id: UUID, relative_path: str) -> None:
        self._file(organization_id, campus_id, relative_path).unlink(missing_ok=True)

    def stage(self, organization_id: UUID, campus_id: UUID, *, whole_organization: bool) -> StagedMediaDeletion:
        sources = [self._root / str(organization_id)] + [self._root / tier / str(organization_id) for tier in TIERS]
        if not whole_organization:
            sources = [source / str(campus_id) for source in sources]
        moves: list[tuple[Path, Path]] = []
        staging = self._root / _STAGING
        try:
            for source in sources:
                if not source.exists():
                    continue
                staging.mkdir(parents=True, exist_ok=True)
                target = staging / uuid4().hex
                source.rename(target)  # one step on the same file system; raises OSError on failure
                moves.append((source, target))
        except OSError:
            # Put back whatever already moved, so a failed staging changes nothing.
            for original, target in moves:
                target.rename(original)
            raise
        return StagedMediaDeletion(moves=tuple(moves))

    def restore(self, staged: StagedMediaDeletion) -> None:
        for original, target in staged.moves:
            original.parent.mkdir(parents=True, exist_ok=True)
            target.rename(original)

    def finalize(self, staged: StagedMediaDeletion) -> None:
        for _, target in staged.moves:
            self._erase(target)

    def sweep(self) -> None:
        staging = self._root / _STAGING
        if staging.exists():
            for leftover in staging.iterdir():
                self._erase(leftover)

    @staticmethod
    def _erase(path: Path) -> None:
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
        except OSError as error:
            raise MediaCleanupFailed("Some campus files could not be erased.") from error
        if path.exists():
            raise MediaCleanupFailed("Some campus files could not be erased.")
