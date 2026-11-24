"""The modules together: settings switches (env and YAML), the composite detector, provider
selection, and a full run that delivers WEAPON and FIGHT_VIOLENCE events to an emulated
``POST /api/v1/internal/detections`` with the real ``HttpDetectionSink`` while Re-ID records candidates."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

from src.config import PipelineSettings
from src.detection_publisher import HttpDetectionSink
from src.detectors.composite import CompositeDetector
from src.detectors.fight_detector import TemporalDetectorAdapter, ViolenceOnnxClassifier
from src.detectors.synthetic_detector import ScriptEntry, SyntheticScriptedDetector
from src.detectors.weapon_detector import WeaponOnnxDetector
from src.main import build_auto_detector, main
from src.model_loader import ModelLoadError, choose_providers
from src.pipeline import Pipeline
from src.reid.embedding_store import CampusGallery
from src.reid.reid_extractor import CropQualityFilter, MemoryObservationSink, PersonReIdOnnxEmbedder, ReIdMatcher, ReIdTrackObserver
from src.rules import EventRules
from src.schemas import BoundingBox, Frame
from src.tracking.single_tracker import IouTracker

FIXTURES = Path(__file__).resolve().parent / "fixtures"
START = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def test_provider_selection() -> None:
    assert choose_providers("auto", ["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert choose_providers("auto", ["CUDAExecutionProvider", "CPUExecutionProvider"]) == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert choose_providers("auto", ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"])[0] == "TensorrtExecutionProvider"
    assert choose_providers("tensorrt", ["TensorrtExecutionProvider", "CPUExecutionProvider"]) == ["TensorrtExecutionProvider", "CPUExecutionProvider"]
    with pytest.raises(ModelLoadError, match="tensorrt execution provider"):
        choose_providers("tensorrt", ["CPUExecutionProvider"])
    with pytest.raises(ModelLoadError):
        choose_providers("cuda", ["CPUExecutionProvider"])


def test_settings_switches_from_environment_and_yaml(tmp_path: Path, monkeypatch) -> None:
    settings = PipelineSettings(_env_file=None, detection_ingest_token="x")
    assert settings.enabled_modules() == ["human_tracking"] and settings.class_map["handgun"].value == "WEAPON"
    with pytest.raises(ValueError, match="REID needs"):
        PipelineSettings(_env_file=None, enable_reid=True, enable_human_tracking=False)
    with pytest.raises(ValueError, match="UNCERTAIN"):
        PipelineSettings(_env_file=None, reid_likely_threshold=0.5, reid_uncertain_threshold=0.7)
    config = tmp_path / "cv.yaml"
    config.write_text(
        "enable_weapon_detection: true\nenable_fight_detection: true\nenable_reid: true\n"
        f"weapon_manifest: {FIXTURES / 'weapon_dummy.manifest.json'}\nfight_manifest: {FIXTURES / 'fight_dummy.manifest.json'}\n"
        f"reid_manifest: {FIXTURES / 'reid_dummy.manifest.json'}\nCV_DEVICE: auto\nsample_fps: 4\n"
    )
    monkeypatch.setenv("CV_SAMPLE_FPS", "6")  # the environment wins over the file
    monkeypatch.setenv("CV_DETECTION_INGEST_TOKEN", "secret")
    loaded = PipelineSettings.from_sources(config)
    assert loaded.enabled_modules() == ["human_tracking", "weapon_detection", "fight_detection", "reid"]
    assert loaded.sample_fps == 6 and loaded.device == "auto" and loaded.detection_ingest_token.get_secret_value() == "secret"
    monkeypatch.setenv("CV_CONFIG_FILE", str(config))
    assert PipelineSettings.from_sources().enable_reid is True
    (tmp_path / "bad.yaml").write_text("- not a mapping\n")
    with pytest.raises(ValueError, match="mapping"):
        PipelineSettings.from_sources(tmp_path / "bad.yaml")


def test_auto_detector_is_built_from_the_switches(monkeypatch) -> None:
    settings = PipelineSettings(
        _env_file=None,
        enable_human_tracking=False,
        enable_weapon_detection=True,
        enable_fight_detection=True,
        weapon_manifest=FIXTURES / "weapon_dummy.manifest.json",
        fight_manifest=FIXTURES / "fight_dummy.manifest.json",
    )
    detector = build_auto_detector(settings)
    assert isinstance(detector, CompositeDetector)
    assert detector.info.name == "weapon-dummy+fight-dummy" and detector.info.labels == ("handgun", "knife", "fight_violence")
    assert not detector.info.synthetic and "test fixture" in (detector.info.license or "")
    only_weapon = build_auto_detector(PipelineSettings(_env_file=None, enable_human_tracking=False, enable_weapon_detection=True, weapon_manifest=FIXTURES / "weapon_dummy.manifest.json"))
    assert isinstance(only_weapon, WeaponOnnxDetector)
    with pytest.raises(ValueError, match="no module is enabled"):
        build_auto_detector(PipelineSettings(_env_file=None, enable_human_tracking=False))
    with pytest.raises(ModelLoadError):
        build_auto_detector(PipelineSettings(_env_file=None, enable_human_tracking=False, enable_weapon_detection=True, weapon_manifest=FIXTURES / "absent.manifest.json"))
    detector.close()


class FakeIntake:
    """Emulates Core API's detection intake: token check, body validation, dedup by detection_id."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.requests: list[dict] = []
        self.seen: dict[str, UUID] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/internal/detections"
        if request.headers.get("X-Detection-Token") != self.token:
            return httpx.Response(401, json={"detail": "bad token", "code": "INVALID_DETECTION_TOKEN"})
        body = json.loads(request.content)
        self.requests.append(body)
        required = {"camera_id", "detection_type", "confidence", "detected_at", "detection_id"}
        if set(body) - required or not required <= set(body) or body["detection_type"] not in ("WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA"):
            return httpx.Response(422, json={"detail": "invalid", "code": "VALIDATION_ERROR"})
        outcome = "DUPLICATE" if body["detection_id"] in self.seen else "CREATED"
        alert_id = self.seen.setdefault(body["detection_id"], uuid4())
        return httpx.Response(200, json={"alert_id": str(alert_id), "outcome": outcome})


def test_weapon_fight_and_reid_run_together_and_deliver_to_the_intake(camera_id: UUID) -> None:
    intake = FakeIntake("secret")
    client = httpx.Client(base_url="http://core-api", transport=httpx.MockTransport(intake.handler))
    sink = HttpDetectionSink("http://core-api", SecretStr("secret"), client=client)
    settings = PipelineSettings(_env_file=None, detection_ingest_token="secret", cooldown_seconds=2.0, min_consecutive_frames=3)
    # "persons" come from the synthetic scripted detector, standing in for YOLOX (no weights in tests)
    person_box = BoundingBox(40, 20, 80, 110)
    people = SyntheticScriptedDetector([ScriptEntry(0, 59, "person", 0.95, person_box)])
    weapon = WeaponOnnxDetector(FIXTURES / "weapon_dummy.manifest.json")
    fight = TemporalDetectorAdapter(ViolenceOnnxClassifier(FIXTURES / "fight_dummy.manifest.json"), infer_every=2)
    detector = CompositeDetector([people, weapon, fight])
    embedder = PersonReIdOnnxEmbedder(FIXTURES / "reid_dummy.manifest.json")
    gallery = CampusGallery(embedder.dimension, window_seconds=600)
    observations = MemoryObservationSink()
    campus = uuid4()
    reid = ReIdTrackObserver(embedder, ReIdMatcher(gallery, likely_threshold=0.9, uncertain_threshold=0.6), campus_id=campus, sink=observations, every_n_frames=4, quality=CropQualityFilter(min_sharpness=1.0))

    class Source:
        def frames(self):
            rng = np.random.default_rng(0)
            for index in range(60):
                image = rng.integers(0, 60, (160, 200, 3), dtype=np.uint8)
                if index >= 20:  # the scene turns red: the dummy weapon model and the dummy fight model both fire
                    image[:, :, 2] = 255
                image[20:110, 40:80] = (200, 30, 30)
                image[20:110:2, 40:80] = (255, 90, 90)
                yield Frame(camera_id=camera_id, source_id="synthetic-run", index=index, timestamp=START + timedelta(seconds=index / 5), image=image)

        def close(self) -> None:
            return None

    report = Pipeline(Source(), detector, IouTracker(), EventRules(settings.rule_config(), model_name=detector.info.name), sink, observers=[reid]).run()
    assert report.stopped_reason is None and report.frames == 60 and report.model_synthetic is True  # the person stand-in is synthetic
    types = {r["detection_type"] for r in intake.requests}
    assert types == {"WEAPON", "FIGHT_VIOLENCE"}
    first_weapon = next(r for r in intake.requests if r["detection_type"] == "WEAPON")
    assert first_weapon["camera_id"] == str(camera_id) and 0.5 <= float(first_weapon["confidence"]) <= 1.0
    assert first_weapon["detection_id"].startswith("synthetic-scripted-detector+weapon-dummy+fight-dummy-")
    assert report.outcomes == {"CREATED": len(report.delivered)} and not report.undelivered
    assert report.rule_stats.unmapped > 0  # persons were tracked but never sent
    assert observations.observations and all(o.review_required for o in observations.observations)
    assert all(o.band.value != "CONFIRMED" for o in observations.observations)
    assert not any(r["detection_type"] not in ("WEAPON", "FIGHT_VIOLENCE") for r in intake.requests)  # Re-ID never posts to the intake
    detector.close()
    reid.close()


def test_cli_auto_mode_refuses_missing_weights_and_requires_a_campus_for_reid(tmp_path: Path, video, camera_id, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CV_DETECTION_INGEST_TOKEN", "x")
    monkeypatch.setenv("CV_ENABLE_HUMAN_TRACKING", "false")
    monkeypatch.setenv("CV_ENABLE_WEAPON_DETECTION", "true")
    monkeypatch.setenv("CV_WEAPON_MANIFEST", str(tmp_path / "absent.manifest.json"))
    assert main(["--video", str(video(frames=5)), "--camera-id", str(camera_id), "--detector", "auto"]) == 2
    monkeypatch.setenv("CV_WEAPON_MANIFEST", str(FIXTURES / "weapon_dummy.manifest.json"))
    monkeypatch.setenv("CV_ENABLE_HUMAN_TRACKING", "true")
    monkeypatch.setenv("CV_ENABLE_REID", "true")
    assert main(["--video", str(video(frames=5)), "--camera-id", str(camera_id), "--detector", "auto"]) == 64
    assert capsys.readouterr().out == ""
