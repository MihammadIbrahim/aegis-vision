"""Ingestion settings, from environment variables with the ``INGEST_`` prefix (or a ``.env`` file).

INGEST_API_BASE_URL              Core API, e.g. http://127.0.0.1:8000
INGEST_SERVICE_TOKEN             Core API's INTERNAL_SERVICE_TOKEN (header X-Service-Token)
INGEST_STORAGE_ROOT              CCTV_STORAGE_ROOT of the Core API (shared disk): recordings/, live/, frames/
INGEST_FRAME_SAMPLE_FPS          frames per second handed to CV inference and the live view (default 5)
INGEST_SEGMENT_SECONDS           length of one routine recording file (default 60)
INGEST_OFFLINE_AFTER_SECONDS     no frame for this long → the camera is reported OFFLINE (default 15)
INGEST_HEARTBEAT_SECONDS         how often an ONLINE camera's "last seen" is refreshed (default 60)
INGEST_REGISTRY_REFRESH_SECONDS  how often the camera list is re-read from the Core API (default 60)
INGEST_RECONNECT_MIN_SECONDS / INGEST_RECONNECT_MAX_SECONDS   reconnect back-off after a stream failure
INGEST_RTSP_TRANSPORT            tcp (default) | udp
INGEST_RTSP_TRANSCODE            false (default): H.264 streams are copied into MP4; true: re-encoded
INGEST_FILE_SOURCES              JSON {"<camera id>": "/path/to/recording.mp4"}: recorded-file fallback for
                                 cameras without reachable equipment (played in real time, looped)
INGEST_CAMPUS_ID                 optional: only cameras of this campus
INGEST_SPOOL_MAX_FRAMES          sampled frames kept per camera for CV inference (default 300)
INGEST_FFMPEG_PATH               ffmpeg binary (default "ffmpeg")
INGEST_CONFIG_FILE               optional YAML with the same keys (without the prefix); environment wins
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGEST_", env_file=".env", extra="ignore")

    api_base_url: str = "http://127.0.0.1:8000"
    service_token: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    storage_root: Path = Path("/mnt/cctv_storage")
    frame_sample_fps: float = Field(default=5.0, gt=0, le=30)
    segment_seconds: int = Field(default=60, ge=5, le=3600)
    offline_after_seconds: float = Field(default=15.0, ge=2, le=600)
    heartbeat_seconds: float = Field(default=60.0, ge=5, le=3600)
    registry_refresh_seconds: float = Field(default=60.0, ge=5, le=3600)
    reconnect_min_seconds: float = Field(default=2.0, ge=0.5, le=60)
    reconnect_max_seconds: float = Field(default=30.0, ge=0.5, le=600)
    rtsp_transport: Literal["tcp", "udp"] = "tcp"
    rtsp_transcode: bool = False
    file_sources: dict[UUID, Path] = Field(default_factory=dict)
    campus_id: UUID | None = None
    spool_max_frames: int = Field(default=300, ge=10, le=100000)
    frame_width: int = Field(default=640, ge=64, le=3840, description="sampled frames are scaled to this width")
    ffmpeg_path: str = "ffmpeg"

    @field_validator("file_sources", mode="before")
    @classmethod
    def _parse_sources(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value

    @field_validator("reconnect_max_seconds")
    @classmethod
    def _max_after_min(cls, value: float, info: Any) -> float:
        if "reconnect_min_seconds" in info.data and value < info.data["reconnect_min_seconds"]:
            raise ValueError("reconnect_max_seconds must be at least reconnect_min_seconds")
        return value

    @classmethod
    def from_sources(cls, config_file: Path | None = None) -> IngestionSettings:
        """Environment variables over an optional YAML file (``INGEST_CONFIG_FILE`` or the argument)."""
        path = config_file or (Path(os.environ["INGEST_CONFIG_FILE"]) if os.environ.get("INGEST_CONFIG_FILE") else None)
        values: dict[str, Any] = {}
        if path is not None:
            import yaml

            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("The config file must hold a mapping of settings.")
            values = {str(k).lower(): v for k, v in loaded.items()}
        env_keys = {k[len("INGEST_") :].lower() for k in os.environ if k.startswith("INGEST_")}
        return cls(**{k: v for k, v in values.items() if k not in env_keys})

    # Storage layout (decision D-023). Paths are relative to the storage root.
    def recordings_dir(self, organization_id: UUID, campus_id: UUID, camera_id: UUID) -> Path:
        return self.storage_root / "recordings" / str(organization_id) / str(campus_id) / str(camera_id)

    def live_frame_path(self, organization_id: UUID, campus_id: UUID, camera_id: UUID) -> Path:
        return self.storage_root / "live" / str(organization_id) / str(campus_id) / str(camera_id) / "latest.jpg"

    def spool_dir(self, camera_id: UUID) -> Path:
        return self.storage_root / "frames" / str(camera_id)
