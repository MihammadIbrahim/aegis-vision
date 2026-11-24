"""Which cameras to ingest and from where (decision D-023).

The Core API lists the cameras of every active campus. A camera gets a *source*:

* ``rtsp``: its RTSP/RTSPS stream URL (no credentials are stored; the camera must allow the
  server's address or be on a protected network).
* ``file``: a recorded video from ``INGEST_FILE_SOURCES`` — the fallback used until real
  equipment is connected; played in real time and looped, so recordings, live frames and CV
  inference behave as with a camera.
* ``unavailable``: ONVIF cameras (stream discovery needs device credentials, which the system
  does not store yet) and cameras without a stream URL. They are reported UNKNOWN with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from src.core_api_client import CameraSpec

SourceKind = Literal["rtsp", "file", "unavailable"]


@dataclass(frozen=True, slots=True)
class CameraSource:
    spec: CameraSpec
    kind: SourceKind
    location: str | None  # the stream URL or the file path; None when unavailable
    reason: str | None = None  # why it is unavailable (generic; safe to report)

    @property
    def camera_id(self) -> UUID:
        return self.spec.camera_id


def resolve_sources(
    cameras: list[CameraSpec], file_sources: dict[UUID, Path], campus_id: UUID | None = None
) -> list[CameraSource]:
    resolved: list[CameraSource] = []
    for spec in cameras:
        if campus_id is not None and spec.campus_id != campus_id:
            continue
        override = file_sources.get(spec.camera_id)
        if override is not None:
            if override.is_file():
                resolved.append(CameraSource(spec, "file", str(override)))
            else:
                resolved.append(CameraSource(spec, "unavailable", None, "The configured recording file is missing."))
            continue
        if not spec.stream_url:
            resolved.append(CameraSource(spec, "unavailable", None, "No stream URL is configured."))
        elif spec.connection_type == "ONVIF":
            resolved.append(
                CameraSource(spec, "unavailable", None, "ONVIF stream discovery needs device credentials (not stored).")
            )
        else:
            resolved.append(CameraSource(spec, "rtsp", spec.stream_url))
    return resolved


def diff_sources(
    current: dict[UUID, CameraSource], wanted: list[CameraSource]
) -> tuple[list[CameraSource], list[UUID]]:
    """(sources to start or restart, camera IDs to stop). A camera restarts when its source changed."""
    wanted_by_id = {s.camera_id: s for s in wanted}
    to_stop = [camera_id for camera_id in current if camera_id not in wanted_by_id]
    to_start = [s for camera_id, s in wanted_by_id.items() if current.get(camera_id) != s]
    return to_start, to_stop
