"""The ingestion service's view of the Core API (service token; decision D-023).

GET  /internal/cameras                    the cameras to ingest
POST /internal/cameras/{id}/status        ONLINE / OFFLINE / UNKNOWN with a generic reason
POST /internal/recordings/segments        a finished recording file

Nothing here logs or sends a stream URL; errors carry the camera ID only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


class CoreApiError(Exception):
    """The Core API refused or did not answer; the message never contains the URL or token."""


@dataclass(frozen=True, slots=True)
class CameraSpec:
    camera_id: UUID
    organization_id: UUID
    campus_id: UUID
    display_name: str
    connection_type: str
    stream_url: str | None


class CoreApiClient:
    def __init__(self, base_url: str, service_token: str, *, timeout_seconds: float = 5.0) -> None:
        if not service_token:
            raise CoreApiError("INGEST_SERVICE_TOKEN is not set.")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"X-Service-Token": service_token, "User-Agent": "cctv-video-ingestion"},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, path: str, json: dict[str, object] | None = None) -> httpx.Response:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.HTTPError as error:
            raise CoreApiError(f"Core API unreachable ({type(error).__name__}).") from None
        if response.status_code >= 400:
            code = ""
            try:
                code = response.json().get("code", "")
            except ValueError:
                pass
            raise CoreApiError(f"Core API answered {response.status_code} {code}".strip())
        return response

    def cameras(self) -> list[CameraSpec]:
        rows = self._call("GET", "/internal/cameras").json()
        return [
            CameraSpec(
                camera_id=UUID(r["camera_id"]),
                organization_id=UUID(r["organization_id"]),
                campus_id=UUID(r["campus_id"]),
                display_name=r["display_name"],
                connection_type=r["connection_type"],
                stream_url=r["stream_url"],
            )
            for r in rows
        ]

    def report_status(
        self, camera_id: UUID, state: str, error: str | None = None, seen_at: datetime | None = None
    ) -> str:
        body: dict[str, object] = {
            "state": state,
            "error": error,
            "seen_at": None if seen_at is None else seen_at.isoformat(),
        }
        return str(self._call("POST", f"/internal/cameras/{camera_id}/status", body).json()["outcome"])

    def register_segment(
        self, camera_id: UUID, started_at: datetime, ended_at: datetime, relative_path: str, size_bytes: int
    ) -> str:
        body: dict[str, object] = {
            "camera_id": str(camera_id),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "relative_path": relative_path,
            "size_bytes": size_bytes,
        }
        return str(self._call("POST", "/internal/recordings/segments", body).json()["outcome"])
