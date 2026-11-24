from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.config import PipelineSettings
from src.detection_publisher import SinkConfigurationError, SinkResult, SinkUnavailableError
from src.detectors.synthetic_detector import ScriptEntry, SyntheticScriptedDetector
from src.pipeline import Pipeline
from src.rules import EventRules
from src.schemas import Detection, DetectionEvent, Frame, ModelInfo
from src.tracking.single_tracker import IouTracker
from src.video_source import VideoFileSource
from tests.conftest import START, box


class RecordingSink:
    def __init__(self, failures: dict[int, Exception] | None = None) -> None:
        self.events: list[DetectionEvent] = []
        self._failures = failures or {}

    def send(self, event: DetectionEvent) -> SinkResult:
        self.events.append(event)
        failure = self._failures.get(len(self.events))
        if failure:
            raise failure
        return SinkResult(event.detection_id, uuid4(), "CREATED", 1)

    def close(self) -> None:
        return None


def build(video_path, camera: UUID, sink, script, **settings):  # type: ignore[no-untyped-def]
    config = PipelineSettings(_env_file=None, detection_ingest_token="x", **settings)
    detector = SyntheticScriptedDetector(script)
    return Pipeline(
        VideoFileSource(video_path, camera, sample_fps=config.sample_fps, start_time=START),
        detector,
        IouTracker(),
        EventRules(config.rule_config(), model_name=detector.info.name),
        sink,
    )


def test_a_recorded_video_produces_confirmed_throttled_events(video, camera_id) -> None:
    path = video(frames=125, fps=25.0)  # 5 s → 25 sampled frames at 5 fps
    script = [ScriptEntry(10, 124, "weapon", 0.9, box()), ScriptEntry(0, 124, "person", 0.95, box(30, 5))]
    sink = RecordingSink()
    report = build(path, camera_id, sink, script, cooldown_seconds=2.0).run()
    assert report.frames == 25 and report.stopped_reason is None and report.model_synthetic is True
    # Weapon from frame 10 (sample 2): confirmed on the 3rd sampled frame (index 20), then every 2 s.
    assert [e.frame_index for e in sink.events] == [20, 70, 120]
    assert all(e.detection_id.startswith("synthetic-scripted-detector-") for e in sink.events)
    assert sink.events[0].detected_at == datetime(2026, 10, 31, 10, 0, 0, 800000, tzinfo=UTC)
    assert report.rule_stats.unmapped == 25 and report.outcomes == {"CREATED": 3}
    assert report.tracks_started == 2  # one person track, one weapon track
    assert report.as_dict()["model_is_synthetic"] is True


def test_rerunning_the_same_video_repeats_the_same_ids(video, camera_id) -> None:
    path = video(frames=50)
    script = [ScriptEntry(0, 49, "weapon", 0.9, box())]
    first, second = RecordingSink(), RecordingSink()
    build(path, camera_id, first, script).run()
    build(path, camera_id, second, script).run()
    assert [e.detection_id for e in first.events] == [e.detection_id for e in second.events] != []


def test_undelivered_events_are_reported_and_processing_continues(video, camera_id) -> None:
    sink = RecordingSink({1: SinkUnavailableError("down")})
    report = build(
        video(frames=125), camera_id, sink, [ScriptEntry(0, 124, "weapon", 0.9, box())], cooldown_seconds=1.0
    ).run()
    assert len(report.undelivered) == 1 and report.undelivered[0].reason == "down"
    assert len(report.delivered) == len(sink.events) - 1 >= 3 and report.stopped_reason is None


def test_a_configuration_error_stops_the_run(video, camera_id) -> None:
    sink = RecordingSink({1: SinkConfigurationError("The backend refused the detection token (401).")})
    report = build(video(frames=125), camera_id, sink, [ScriptEntry(0, 124, "weapon", 0.9, box())]).run()
    assert report.stopped_reason and "401" in report.stopped_reason and len(sink.events) == 1


def test_a_detector_failure_stops_the_run(video, camera_id) -> None:
    class Broken:
        info = ModelInfo(name="broken", version="1", labels=())

        def detect(self, frame: Frame) -> list[Detection]:
            raise RuntimeError("model crashed")

        def close(self) -> None:
            return None

    config = PipelineSettings(_env_file=None, detection_ingest_token="x")
    pipeline = Pipeline(
        VideoFileSource(video(frames=10), camera_id),
        Broken(),
        IouTracker(),
        EventRules(config.rule_config(), model_name="broken"),
        RecordingSink(),
    )
    report = pipeline.run()
    assert report.stopped_reason == "detector failed at frame 0: RuntimeError" and report.frames == 1


def test_the_cooldown_must_fit_inside_the_backend_merge_window() -> None:
    with pytest.raises(ValueError, match="shorter than the backend"):
        PipelineSettings(_env_file=None, cooldown_seconds=60, backend_dedup_seconds=60)
    PipelineSettings(_env_file=None, cooldown_seconds=59, backend_dedup_seconds=60)


# --- Real person detector in the pipeline ----------------------------------------------------------------

from collections.abc import Sequence  # noqa: E402

from src.detectors.human_detector import YoloxHumanDetector  # noqa: E402
from src.schemas import Track  # noqa: E402
from src.tracking.single_tracker import ByteTracker  # noqa: E402
from tests.conftest import WEIGHTS_SPEC, requires_real_model, write_person_video  # noqa: E402


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Frame, list[Track]]] = []

    def observe(self, frame: Frame, tracks: Sequence[Track]) -> None:
        self.calls.append((frame, list(tracks)))


@requires_real_model
def test_real_person_detection_is_tracked_and_never_alerts(tmp_path, camera_id) -> None:
    """Kind 4: the real model inside the real pipeline (video → sampling → YOLOX → ByteTrack → rules)."""
    path = write_person_video(tmp_path / "walk.avi", frames=50)
    config = PipelineSettings(_env_file=None, detection_ingest_token="x")
    detector = YoloxHumanDetector(WEIGHTS_SPEC)
    sink, recorder = RecordingSink(), Recorder()
    report = Pipeline(
        VideoFileSource(path, camera_id, sample_fps=5.0, start_time=START),
        detector,
        ByteTracker(),
        EventRules(config.rule_config(), model_name=detector.info.name),
        sink,
        observers=[recorder],
    ).run()
    assert report.stopped_reason is None and report.frames == 10 and report.model_synthetic is False
    assert sink.events == [] and report.rule_stats.events == 0  # persons never alert
    assert report.rule_stats.unmapped == report.rule_stats.detections >= 9
    assert report.tracks_started == 1  # one walking person, one track
    ids = {t.track_id for _, tracks in recorder.calls for t in tracks if t.confirmed}
    assert len(ids) == 1 and all(t.camera_id == camera_id for _, ts in recorder.calls for t in ts)
    assert report.detector_ms_mean > 0


def test_the_cli_refuses_to_run_without_the_model_and_never_falls_back(
    tmp_path, video, camera_id, monkeypatch, capsys
) -> None:
    from src.main import main

    monkeypatch.setenv("CV_HUMAN_MODEL_SPEC", str(tmp_path / "absent.model.json"))
    monkeypatch.setenv("CV_DETECTION_INGEST_TOKEN", "x")
    code = main(["--video", str(video(frames=5)), "--camera-id", str(camera_id), "--detector", "yolox-human"])
    assert code == 2 and capsys.readouterr().out == ""  # stopped before processing; nothing reported as run
