"""Delivering confirmed events to Core API.

``HttpDetectionSink`` posts to the detection intake that Core API registers at
``POST /api/v1/internal/detections`` (``alert_router.internal_router``), with the shared secret in the
``X-Detection-Token`` header. The body matches Core API's ``DetectionRequest`` exactly (extra fields
are refused there).

Responses:
* 200 → ``SinkResult`` with ``CREATED``, ``REPEATED`` (merged into an open alert) or ``DUPLICATE``
  (this detection ID was already received).
* 401, 503 ``DETECTION_INTAKE_DISABLED``, 404 ``CAMERA_NOT_FOUND`` → ``SinkConfigurationError``: wrong
  token, intake switched off, or a camera ID the backend does not know. Not retried; the pipeline
  stops, because every later event would fail the same way.
* 422 → ``SinkRejectedError``: the payload is invalid. Not retried.
* network errors, timeouts, 429 and other 5xx → retried up to ``max_attempts`` with exponential
  backoff, then ``SinkUnavailableError``. Retrying is safe: the detection ID makes the request
  idempotent.

The token is never logged or included in an error message.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from src.schemas import AlertType, DetectionEvent

logger = logging.getLogger(__name__)

INGEST_PATH = "/api/v1/internal/detections"


class SinkError(Exception):
    """Base class. Messages contain status codes and API error codes, never the token."""


class SinkConfigurationError(SinkError):
    """Wrong token, intake disabled, or unknown camera: stop processing."""


class SinkRejectedError(SinkError):
    """The backend refused this event's payload (422)."""


class SinkUnavailableError(SinkError):
    """The backend could not be reached or kept failing after all retries."""


@dataclass(frozen=True, slots=True)
class SinkResult:
    detection_id: str
    alert_id: UUID
    outcome: Literal["CREATED", "REPEATED", "DUPLICATE"]
    attempts: int


class DetectionSink(Protocol):
    def send(self, event: DetectionEvent) -> SinkResult: ...

    def close(self) -> None: ...


class DetectionPayload(BaseModel):
    """Mirror of Core API's ``DetectionRequest`` (``application/dtos/alert_dto.py``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    camera_id: UUID
    detection_type: AlertType
    confidence: Decimal | None = Field(default=None, ge=0, le=1, max_digits=5, decimal_places=4)
    detected_at: datetime
    detection_id: str | None = Field(default=None, min_length=1, max_length=128)
    snapshot_ref: str | None = Field(default=None, max_length=1024)

    @classmethod
    def of(cls, event: DetectionEvent) -> DetectionPayload:
        return cls(
            camera_id=event.camera_id,
            detection_type=event.alert_type,
            confidence=Decimal(f"{event.confidence:.4f}"),
            detected_at=event.detected_at,
            detection_id=event.detection_id,
        )


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


class HttpDetectionSink:
    def __init__(
        self,
        base_url: str,
        token: SecretStr,
        *,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token.get_secret_value():
            raise SinkConfigurationError("No detection token configured (CV_DETECTION_INGEST_TOKEN).")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._token = token
        self._attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self._owns_client = client is None

    def send(self, event: DetectionEvent) -> SinkResult:
        body = DetectionPayload.of(event).model_dump(mode="json", exclude_none=True)
        headers = {"X-Detection-Token": self._token.get_secret_value()}
        last_problem = "no attempt made"
        for attempt in range(1, self._attempts + 1):
            try:
                response = self._client.post(INGEST_PATH, json=body, headers=headers)
            except httpx.TransportError as error:  # connection refused, timeout, DNS…
                last_problem = type(error).__name__
                logger.warning("detection %s: attempt %d failed (%s)", event.detection_id, attempt, last_problem)
            else:
                status, code = response.status_code, _error_code(response)
                if status == 200:
                    data = response.json()
                    result = SinkResult(event.detection_id, UUID(data["alert_id"]), data["outcome"], attempt)
                    logger.info("detection %s → alert %s (%s)", event.detection_id, result.alert_id, result.outcome)
                    return result
                if status == 401:
                    raise SinkConfigurationError("The backend refused the detection token (401).")
                if status == 503 and code == "DETECTION_INTAKE_DISABLED":
                    raise SinkConfigurationError("The backend's detection intake is switched off (503).")
                if status == 404:
                    raise SinkConfigurationError(f"The backend does not know this camera (404 {code or ''}).".strip())
                if status == 422:
                    raise SinkRejectedError(f"The backend rejected detection {event.detection_id} (422 {code or ''}).")
                if status != 429 and status < 500:
                    raise SinkRejectedError(f"Unexpected response {status} for detection {event.detection_id}.")
                last_problem = f"HTTP {status}"
                logger.warning("detection %s: attempt %d got %s", event.detection_id, attempt, last_problem)
            if attempt < self._attempts:
                self._sleep(self._backoff * 2 ** (attempt - 1))
        raise SinkUnavailableError(
            f"Detection {event.detection_id} not delivered after {self._attempts} attempts ({last_problem})."
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
