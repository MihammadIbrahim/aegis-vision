"""Hands every admitted frame to the live store and the CV spool (decision D-023)."""

from __future__ import annotations

from uuid import UUID

from src.frame_store import FrameSpool, LiveFrameStore


class FramePublisher:
    def __init__(self, live: LiveFrameStore, spool: FrameSpool, camera_id: UUID) -> None:
        self._live = live
        self._spool = spool
        self.camera_id = camera_id
        self.published = 0

    def publish(self, jpeg: bytes, unix_ms: int) -> None:
        self._live.update(jpeg)
        self._spool.push(jpeg, unix_ms)
        self.published += 1

    def stop(self) -> None:
        """The live frame is removed so the API reports "no live frame" rather than a stale picture."""
        self._live.clear()
