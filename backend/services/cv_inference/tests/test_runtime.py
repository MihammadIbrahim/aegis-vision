"""The worker's connections to the Core API (decision D-024): the camera's AI configuration gates the modules
and is reported back, live overlays are written for the Live page, Re-ID observations go to Person Search."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np

from src.reid.reid_extractor import MatchBand, ReIdObservation
from src.runtime import (
    AiConfigWatcher,
    GatedDetector,
    HttpObservationSink,
    OverlayWriter,
    module_of,
)
from src.schemas import Frame, ModelInfo, Track
from tests.conftest import box, det

TOKEN = "service-token"


def watcher_for(responses: dict[str, object], seen: list[httpx.Request], **kwargs: object) -> AiConfigWatcher:
    camera = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/ai-config"):
            item = responses["config"]
            if isinstance(item, Exception):
                raise item
            return item  # type: ignore[return-value]
        return httpx.Response(204)

    client = httpx.Client(base_url="http://core-api", transport=httpx.MockTransport(handler))
    return AiConfigWatcher(camera, client, TOKEN, **kwargs)  # type: ignore[arg-type]


def config(modules: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"camera_id": str(uuid4()), "organization_id": str(uuid4()), "campus_id": str(uuid4()),
              "modules": modules, "confidence_threshold": 0.5, "frame_sample_fps": 5, "detection_cooldown_seconds": 60},
    )  # fmt: skip


class FakeDetector:
    info = ModelInfo("fake", "1", ("person", "pistol", "fight"))

    def detect(self, frame: Frame) -> list:  # type: ignore[type-arg]
        return [det("person"), det("pistol"), det("fight")]

    def close(self) -> None:
        pass


def a_frame(camera, width: int = 200, height: int = 100) -> Frame:  # type: ignore[no-untyped-def]
    return Frame(camera, "spool:test", 1, datetime(2026, 11, 18, 10, 0, tzinfo=UTC), np.zeros((height, width, 3), np.uint8))


def test_modules_follow_the_camera_configuration_and_status_is_reported() -> None:
    seen: list[httpx.Request] = []
    responses: dict[str, object] = {"config": config(["human_detection", "fight_detection"])}
    watcher = watcher_for(responses, seen, poll_seconds=30, clock=lambda: 0.0)
    assert watcher.refresh(force=True) is not None
    assert seen[0].headers["X-Service-Token"] == TOKEN
    status = json.loads(seen[1].content)
    assert status["modules"] == ["fight_detection", "human_detection"] and status["worker"] == "cv_inference"
    gated = GatedDetector(FakeDetector(), watcher)  # type: ignore[arg-type]
    labels = [d.label for d in gated.detect(a_frame(watcher.camera_id))]
    assert labels == ["person", "fight"]  # weapon detection is switched off for this camera
    assert module_of("Pistol") == "weapon_detection" and module_of("violence") == "fight_detection"


def test_an_unreachable_core_api_keeps_the_last_configuration() -> None:
    seen: list[httpx.Request] = []
    responses: dict[str, object] = {"config": config(["human_detection"])}
    watcher = watcher_for(responses, seen, poll_seconds=0)
    watcher.refresh(force=True)
    responses["config"] = httpx.ConnectError("down")
    watcher.refresh(force=True)
    assert watcher.allows("human_detection") and watcher.last_error and "unreachable" in watcher.last_error
    refused = watcher_for({"config": httpx.Response(404)}, [], poll_seconds=0)
    assert refused.refresh(force=True) is None and not refused.allows("human_detection")


def test_overlays_are_written_as_fractions_of_the_frame(tmp_path: Path) -> None:
    watcher = watcher_for({"config": config(["human_detection"])}, [])
    watcher.refresh(force=True)
    writer = OverlayWriter(tmp_path, watcher)
    frame = a_frame(watcher.camera_id)
    tracks = [Track(7, det("person", 0.912345, box(20, 10, 40, 50)), 3, 0, watcher.camera_id, True)]
    writer.observe(frame, tracks)
    assert watcher.config is not None
    path = tmp_path / "live" / str(watcher.config.organization_id) / str(watcher.config.campus_id) / str(
        watcher.camera_id
    ) / "overlay.json"
    body = json.loads(path.read_text())
    assert body["frame_width"] == 200 and body["modules"] == ["human_detection"]
    assert body["detections"] == [
        {"label": "person", "confidence": 0.9123, "box": [0.1, 0.1, 0.3, 0.6], "track_id": 7,
         "module": "human_detection"}
    ]  # fmt: skip
    assert not list(path.parent.glob(".overlay-*"))  # written atomically


def test_reid_observations_go_to_person_search() -> None:
    sent: list[httpx.Request] = []
    statuses = iter([201, 409])

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(next(statuses), json={})

    client = httpx.Client(base_url="http://core-api", transport=httpx.MockTransport(handler))
    sink = HttpObservationSink(client, TOKEN, "osnet-1")
    observation = ReIdObservation(
        uuid4(), uuid4(), 4, 12, datetime(2026, 11, 18, tzinfo=UTC), MatchBand.UNCERTAIN, [],
        embedding=[0.1] * 512, crop_jpeg=b"\xff\xd8jpeg", quality=0.8,
    )  # fmt: skip
    sink.record(observation)
    sink.record(observation)
    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/api/v1/internal/reid/observations"
    assert len(body["embedding"]) == 512 and base64.b64decode(body["crop_jpeg_base64"]) == b"\xff\xd8jpeg"
    assert (sink.sent, sink.refused) == (1, 1)
    assert "embedding" not in observation.as_dict()  # never written to JSONL files
