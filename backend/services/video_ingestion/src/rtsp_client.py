"""One ffmpeg process per camera: the stream is decoded once and split into (a) MP4 segments on
disk and (b) a low-rate MJPEG stream on stdout for the live view and CV inference (decision D-023).

Sources: an RTSP/RTSPS URL (TCP transport by default, 10 s socket timeout) or a recorded file,
played in real time and looped (the fallback until real equipment is connected). The command never
appears in logs; ffmpeg's own messages are summarised to a short, generic reason.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from src.frame_sampler import MjpegSplitter


class StreamFailed(Exception):
    """The stream could not be opened or ended. ``reason`` is safe to report (no URL)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class StreamPlan:
    kind: str  # rtsp | file
    location: str
    segment_dir: Path  # where ffmpeg writes <strftime>.mp4
    segment_seconds: int
    sample_fps: float
    frame_width: int
    transcode: bool
    rtsp_transport: str = "tcp"
    ffmpeg: str = "ffmpeg"

    def command(self) -> list[str]:
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats"]
        if self.kind == "rtsp":
            cmd += ["-rtsp_transport", self.rtsp_transport, "-timeout", "10000000", "-i", self.location]
        else:
            cmd += ["-re", "-stream_loop", "-1", "-i", self.location]
        # Re-encoding: a keyframe at every segment boundary, so chunks are exactly segment_seconds
        # long. Stream copy: chunks split at the camera's own keyframes (typically 1-2 s GOPs).
        video_codec = (
            [
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-force_key_frames", f"expr:gte(t,n_forced*{self.segment_seconds})",
            ]
            if self.transcode
            else ["-c:v", "copy"]
        )  # fmt: skip
        pattern = str(self.segment_dir / "%Y%m%dT%H%M%S.mp4")
        cmd += [
            "-map", "0:v:0", "-an", *video_codec,
            "-f", "segment", "-segment_time", str(self.segment_seconds), "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart", "-reset_timestamps", "1", "-strftime", "1", pattern,
            "-map", "0:v:0", "-an", "-vf", f"fps={self.sample_fps},scale={self.frame_width}:-2",
            "-c:v", "mjpeg", "-q:v", "4", "-f", "image2pipe", "pipe:1",
        ]  # fmt: skip
        return cmd


def summarize_error(stderr: bytes) -> str:
    """A short, generic reason from ffmpeg's last error lines (addresses are not repeated)."""
    text = stderr.decode(errors="replace").strip().splitlines()
    last = text[-1].lower() if text else ""
    if not last:
        return "The stream ended."
    if "connection refused" in last:
        return "The connection was refused."
    if "timed out" in last or "timeout" in last:
        return "The stream timed out."
    if "401" in last or "unauthorized" in last:
        return "The camera requires credentials."
    if "404" in last or "not found" in last or "no such file" in last:
        return "The stream or file was not found."
    if "invalid data" in last or "could not find codec" in last:
        return "The stream data could not be decoded."
    if "name or service not known" in last or "no address" in last:
        return "The host name could not be resolved."
    return "The stream failed (see the service log)."


class StreamProcess:
    """Runs the plan; iterate ``frames()`` for JPEG bytes until the process ends."""

    def __init__(self, plan: StreamPlan) -> None:
        self._plan = plan
        self._process: subprocess.Popen[bytes] | None = None
        self.stderr: bytes = b""

    def start(self) -> None:
        self._plan.segment_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TZ": "UTC"}  # segment names are UTC timestamps
        try:
            self._process = subprocess.Popen(
                self._plan.command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except OSError as error:
            raise StreamFailed(f"ffmpeg could not be started ({type(error).__name__}).") from None

    def frames(self) -> Iterator[bytes]:
        assert self._process is not None and self._process.stdout is not None
        splitter = MjpegSplitter()
        while True:
            chunk = self._process.stdout.read(64 * 1024)
            if not chunk:
                break
            yield from splitter.feed(chunk)
        self._finish()

    def _finish(self) -> None:
        assert self._process is not None
        try:
            _, self.stderr = self._process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            _, self.stderr = self._process.communicate()
        if self._process.returncode not in (0, None):
            raise StreamFailed(summarize_error(self.stderr))
        raise StreamFailed("The stream ended.")

    def stop(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None
