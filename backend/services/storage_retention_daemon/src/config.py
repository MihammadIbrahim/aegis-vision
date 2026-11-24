"""Retention daemon settings (environment variables with the ``RETENTION_`` prefix, or ``.env``).

RETENTION_API_BASE_URL          Core API, e.g. http://127.0.0.1:8000
RETENTION_SERVICE_TOKEN         Core API's INTERNAL_SERVICE_TOKEN (header X-Service-Token)
RETENTION_STORAGE_ROOT          CCTV_STORAGE_ROOT (shared disk); the daemon only works inside <root>/recordings/
RETENTION_INTERVAL_SECONDS      how often expired recordings are purged and the disk measured (default 300)
RETENTION_BATCH_SIZE            expired segments handled per pass (default 500)
RETENTION_SPOOL_MAX_AGE_SECONDS CV frame spool files older than this are removed (default 600)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetentionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RETENTION_", env_file=".env", extra="ignore")

    api_base_url: str = "http://127.0.0.1:8000"
    service_token: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    storage_root: Path = Path("/mnt/cctv_storage")
    interval_seconds: float = Field(default=300.0, ge=5, le=86400)
    batch_size: int = Field(default=500, ge=1, le=5000)
    spool_max_age_seconds: float = Field(default=600.0, ge=10, le=86400)
    node_name: str = "primary"

    @property
    def recordings_root(self) -> Path:
        return self.storage_root / "recordings"

    @property
    def evidence_root(self) -> Path:
        return self.storage_root / "evidence"

    @property
    def frames_root(self) -> Path:
        return self.storage_root / "frames"
