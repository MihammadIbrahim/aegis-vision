"""Evidence clips and snapshots cut from routine recording segments with ffmpeg (decision D-023).

Segments are browser-playable MP4 files written by the ingestion service. A clip that spans more
than one segment is joined with the concat demuxer, then trimmed and re-encoded (H.264, AAC-less),
so the cut is frame-accurate and the output plays everywhere. ffmpeg's output is never returned to
clients; a failure raises ``ClipToolFailed`` with a short, generic message.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ClipToolUnavailable(Exception):
    """ffmpeg is not installed where the API runs."""


class ClipToolFailed(Exception):
    """ffmpeg could not produce the file (bad input, timeout)."""


@dataclass(frozen=True, slots=True)
class ClipPiece:
    """One recording file and where, inside the whole, it begins (seconds from the clip origin)."""

    path: Path
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class MediaFile:
    path: Path
    size_bytes: int
    sha256: str


def _digest(path: Path) -> MediaFile:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return MediaFile(path, path.stat().st_size, sha.hexdigest())


class FfmpegClipTool:
    def __init__(self, ffmpeg: str = "ffmpeg", timeout_seconds: float = 120) -> None:
        self._ffmpeg = ffmpeg
        self._timeout = timeout_seconds

    def available(self) -> bool:
        return shutil.which(self._ffmpeg) is not None

    async def _run(self, *args: str) -> None:
        if not self.available():
            raise ClipToolUnavailable("ffmpeg is not available on the server.")
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ClipToolFailed("Cutting the clip took too long.") from None
        if process.returncode != 0:
            # The message is logged by the caller if needed; it never reaches clients.
            raise ClipToolFailed(f"ffmpeg failed ({process.returncode}): {stderr.decode(errors='replace')[-400:]}")

    async def cut_clip(self, pieces: list[ClipPiece], start_offset: float, duration: float, output: Path) -> MediaFile:
        """``start_offset`` is measured from the beginning of the first piece."""
        if not pieces or duration <= 0:
            raise ClipToolFailed("Nothing to cut.")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="clip-") as scratch:
            listing = Path(scratch) / "pieces.txt"
            listing.write_text("".join(f"file '{p.path.as_posix()}'\n" for p in pieces), encoding="utf-8")
            partial = output.with_name(output.name + ".part.mp4")
            await self._run(
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-ss", f"{max(0.0, start_offset):.3f}", "-t", f"{duration:.3f}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-f", "mp4", str(partial),
            )  # fmt: skip
            partial.replace(output)
        return _digest(output)

    async def snapshot(self, source: Path, offset: float, output: Path) -> MediaFile:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.name + ".part.jpg")
        await self._run(
            "-ss",
            f"{max(0.0, offset):.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-f",
            "image2",
            str(partial),
        )
        partial.replace(output)
        return _digest(output)

    @staticmethod
    def copy_file(source: Path, output: Path) -> MediaFile:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_name(output.name + ".part")
        shutil.copyfile(source, partial)
        partial.replace(output)
        return _digest(output)
