"""Fight / violence classifier: the per-camera ring buffer, clip pre-processing, the dummy clip
model (violence probability rises with the mean red value), and the temporal adapter inside the
frame pipeline, producing confirmed FIGHT_VIOLENCE events through the unchanged rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from src.config import PipelineSettings
from src.detectors.base_detector import DetectorError
from src.detectors.fight_detector import VIOLENCE_LABEL, FrameRingBuffer, TemporalDetectorAdapter, ViolenceOnnxClassifier
from src.pipeline import Pipeline
from src.rules import EventRules
from src.schemas import AlertType, Frame
from src.tracking.single_tracker import IouTracker
from tests.test_pipeline import RecordingSink

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIGHT = FIXTURES / "fight_dummy.manifest.json"
START = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def frames(camera, count: int, *, red: int, start: int = 0, size: tuple[int, int] = (48, 72)) -> list[Frame]:
    image = np.zeros((size[0], size[1], 3), np.uint8)
    image[:, :, 2] = red  # BGR: channel 2 is red
    return [Frame(camera_id=camera, source_id="v", index=start + i, timestamp=START + timedelta(seconds=(start + i) / 5), image=image) for i in range(count)]


def test_ring_buffer_is_per_camera_and_bounded() -> None:
    buffer = FrameRingBuffer(4)
    a, b = uuid4(), uuid4()
    for frame in frames(a, 6, red=0) + frames(b, 2, red=0):
        buffer.push(frame)
    assert buffer.full(a) and not buffer.full(b)
    assert [f.index for f in buffer.frames(a)] == [2, 3, 4, 5] and buffer.pushed(a) == 6
    buffer.reset(a)
    assert not buffer.full(a) and buffer.pushed(a) == 0 and len(buffer.frames(b)) == 2
    with pytest.raises(ValueError):
        FrameRingBuffer(1)


def test_classifier_preprocesses_like_training_and_scores_the_dummy_model() -> None:
    classifier = ViolenceOnnxClassifier(FIGHT)
    assert classifier.clip_length == 8 and classifier.target_fps == 5.0 and classifier.threshold == 0.5
    assert classifier.info.labels == (VIOLENCE_LABEL,) and not classifier.info.synthetic
    camera = uuid4()
    tensor = classifier.preprocess(frames(camera, 8, red=255))
    assert tensor.shape == (1, 3, 8, 32, 32) and tensor.dtype == np.float32
    assert tensor[0, 0].max() == pytest.approx(1.0) and tensor[0, 2].max() == 0.0  # red → RGB channel 0
    violent = classifier.classify(frames(camera, 8, red=255))[0]
    calm = classifier.classify(frames(camera, 8, red=0))[0]
    assert violent.label == VIOLENCE_LABEL and violent.confidence > 0.9 > 0.2 > calm.confidence
    assert (violent.first_frame_index, violent.last_frame_index) == (0, 7)
    with pytest.raises(DetectorError, match="exactly 8 frames"):
        classifier.classify(frames(camera, 5, red=0))


def test_temporal_adapter_reports_the_latest_clip_probability_until_the_next_inference() -> None:
    adapter = TemporalDetectorAdapter(ViolenceOnnxClassifier(FIGHT), infer_every=3, min_report_probability=0.5, sample_fps=5.0)
    camera = uuid4()
    outputs = [adapter.detect(f) for f in frames(camera, 7, red=0)]  # the buffer fills at the 8th frame
    assert all(o == [] for o in outputs) and adapter.clips_classified == 0
    first_full = adapter.detect(frames(camera, 1, red=255, start=7)[0])
    assert adapter.clips_classified == 1 and first_full == []  # one red frame among eight: probability ≈0.38, below min_report
    for index in range(8, 16):
        adapter.detect(frames(camera, 1, red=255, start=index)[0])
    assert adapter.clips_classified == 1 + 8 // 3
    detections = adapter.detect(frames(camera, 1, red=255, start=16)[0])
    assert len(detections) == 1 and detections[0].label == VIOLENCE_LABEL and detections[0].confidence > 0.9
    assert (detections[0].box.x2, detections[0].box.y2) == (72.0, 48.0)  # the whole frame
    other = uuid4()
    assert adapter.detect(frames(other, 1, red=255)[0]) == []  # another camera starts with an empty buffer
    adapter.reset()
    assert adapter.detect(frames(camera, 1, red=255, start=17)[0]) == []


def test_fight_events_flow_through_the_rules_to_the_sink(camera_id) -> None:
    settings = PipelineSettings(_env_file=None, detection_ingest_token="x", cooldown_seconds=2.0, min_consecutive_frames=3)
    adapter = TemporalDetectorAdapter(ViolenceOnnxClassifier(FIGHT), infer_every=2, min_report_probability=0.05)

    class Source:
        def frames(self):
            yield from frames(camera_id, 10, red=0) + frames(camera_id, 30, red=255, start=10)

        def close(self) -> None:
            return None

    sink = RecordingSink()
    report = Pipeline(Source(), adapter, IouTracker(), EventRules(settings.rule_config(), model_name=adapter.info.name), sink).run()
    assert report.stopped_reason is None and report.frames == 40 and report.model_synthetic is False
    assert sink.events and all(e.alert_type is AlertType.FIGHT_VIOLENCE and e.label == VIOLENCE_LABEL for e in sink.events)
    assert sink.events[0].confidence > 0.5 and sink.events[0].frame_index >= 10 + 3  # confirmed on the 3rd positive sampled frame
    assert all(b.detected_at - a.detected_at >= timedelta(seconds=2) for a, b in zip(sink.events, sink.events[1:], strict=False))
    assert all(e.detection_id.startswith("fight-dummy-") for e in sink.events)
