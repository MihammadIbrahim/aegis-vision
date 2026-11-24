"""Frame sampling and the MJPEG byte stream ffmpeg hands us (decision D-023).

One ffmpeg process per camera decodes the stream once and emits two outputs: the MP4 segments on
disk and a low-rate MJPEG stream on its stdout. ``MjpegSplitter`` cuts that byte stream into JPEG
frames; ``FrameSampler`` keeps at most ``sample_fps`` frames per second by wall clock (ffmpeg's
``fps`` filter already rate-limits, so the sampler is the safety net for bursts after a stall).
"""

from __future__ import annotations

from collections.abc import Iterator

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


class MjpegSplitter:
    """Feed bytes; get complete JPEG images out, in order. Garbage between frames is dropped."""

    def __init__(self, max_frame_bytes: int = 8 << 20) -> None:
        self._buffer = bytearray()
        self._max = max_frame_bytes

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        self._buffer.extend(chunk)
        while True:
            start = self._buffer.find(SOI)
            if start < 0:
                # Keep a trailing 0xFF: it may be the first byte of the next marker.
                tail = bytes(self._buffer[-1:])
                self._buffer.clear()
                if tail == b"\xff":
                    self._buffer.extend(tail)
                return
            if start > 0:
                del self._buffer[:start]
            end = self._buffer.find(EOI, 2)
            if end < 0:
                if len(self._buffer) > self._max:
                    self._buffer.clear()  # a frame this large is corrupt
                return
            frame = bytes(self._buffer[: end + 2])
            del self._buffer[: end + 2]
            yield frame


class FrameSampler:
    """Admits at most ``sample_fps`` frames per second of wall-clock time; the first frame always."""

    def __init__(self, sample_fps: float) -> None:
        if sample_fps <= 0:
            raise ValueError("sample_fps must be positive")
        self._interval = 1.0 / sample_fps
        self._next_due: float | None = None

    def admit(self, now: float) -> bool:
        if self._next_due is None or now >= self._next_due:
            # Re-anchor after a stall so a burst of late frames is not all admitted at once.
            base = now if self._next_due is None or now - self._next_due > self._interval else self._next_due
            self._next_due = base + self._interval
            return True
        return False

    def reset(self) -> None:
        self._next_due = None
