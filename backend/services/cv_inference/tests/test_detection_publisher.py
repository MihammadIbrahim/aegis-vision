import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.detection_publisher import (
    INGEST_PATH,
    HttpDetectionSink,
    SinkConfigurationError,
    SinkRejectedError,
    SinkUnavailableError,
)
from src.schemas import AlertType, DetectionEvent

TOKEN = "secret-token-value-for-tests"


def event() -> DetectionEvent:
    return DetectionEvent(
        detection_id="test-model-abc",
        camera_id=uuid4(),
        alert_type=AlertType.WEAPON,
        confidence=0.87654,
        detected_at=datetime(2026, 10, 31, 10, 0, tzinfo=UTC),
        label="weapon",
        frame_index=5,
    )


def sink(responses: list[object], sleeps: list[float] | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    client = httpx.Client(base_url="http://core-api", transport=httpx.MockTransport(handler))
    s = HttpDetectionSink(
        "http://core-api",
        SecretStr(TOKEN),
        client=client,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        **kwargs,
    )  # type: ignore[arg-type]
    return s, seen


def ok(outcome: str = "CREATED") -> httpx.Response:
    return httpx.Response(200, json={"alert_id": str(uuid4()), "outcome": outcome})


def test_the_payload_matches_the_backend_contract() -> None:
    s, seen = sink([ok()])
    e = event()
    result = s.send(e)
    assert result.outcome == "CREATED" and result.attempts == 1
    request = seen[0]
    assert request.url.path == INGEST_PATH == "/api/v1/internal/detections"
    assert request.headers["X-Detection-Token"] == TOKEN
    assert json.loads(request.content) == {
        "camera_id": str(e.camera_id),
        "detection_type": "WEAPON",
        "confidence": "0.8765",
        "detected_at": "2026-10-31T10:00:00Z",
        "detection_id": "test-model-abc",
    }


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx.Response(401, json={"detail": "Invalid detection token."}), SinkConfigurationError, "401"),
        (httpx.Response(503, json={"code": "DETECTION_INTAKE_DISABLED"}), SinkConfigurationError, "switched off"),
        (httpx.Response(404, json={"code": "CAMERA_NOT_FOUND"}), SinkConfigurationError, "CAMERA_NOT_FOUND"),
        (httpx.Response(422, json={"detail": []}), SinkRejectedError, "422"),
        (httpx.Response(400, json={}), SinkRejectedError, "Unexpected response 400"),
    ],
)
def test_permanent_failures_are_not_retried(response: httpx.Response, error: type, match: str, caplog) -> None:
    s, seen = sink([response, ok()])
    with caplog.at_level(logging.DEBUG), pytest.raises(error, match=match) as raised:
        s.send(event())
    assert len(seen) == 1
    assert TOKEN not in str(raised.value) and TOKEN not in caplog.text


def test_transient_failures_are_retried_with_backoff_then_succeed(caplog) -> None:
    sleeps: list[float] = []
    s, seen = sink(
        [httpx.ConnectError("refused"), httpx.Response(500), ok("REPEATED")],
        sleeps,
        max_attempts=3,
        backoff_seconds=0.5,
    )
    with caplog.at_level(logging.DEBUG):
        result = s.send(event())
    assert result.outcome == "REPEATED" and result.attempts == 3 and sleeps == [0.5, 1.0]
    assert len({r.content for r in seen}) == 1  # the same body (and detection ID) every time
    assert TOKEN not in caplog.text


def test_retries_are_bounded() -> None:
    s, seen = sink([httpx.ReadTimeout("slow"), httpx.Response(503), httpx.Response(429)], max_attempts=3)
    with pytest.raises(SinkUnavailableError, match="after 3 attempts"):
        s.send(event())
    assert len(seen) == 3


def test_a_missing_token_is_refused_up_front() -> None:
    with pytest.raises(SinkConfigurationError, match="No detection token"):
        HttpDetectionSink("http://core-api", SecretStr(""))
