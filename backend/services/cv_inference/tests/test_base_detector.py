"""Detector, tracker and model-adapter building blocks."""

from pathlib import Path

import numpy as np
import pytest

from src.detectors.synthetic_detector import ScriptEntry, SyntheticScriptedDetector
from src.model_loader import InferenceError, ModelLoadError, OnnxRuntimeAdapter
from src.schemas import BoundingBox, Detection, ModelInfo
from src.tracking.single_tracker import IouTracker
from tests.conftest import box, det, frame

FIXTURE = Path(__file__).parent / "fixtures" / "scale2.onnx"


def test_values_are_validated() -> None:
    with pytest.raises(ValueError):
        BoundingBox(10, 10, 5, 20)
    with pytest.raises(ValueError):
        BoundingBox(0, 0, float("nan"), 1)
    with pytest.raises(ValueError):
        Detection("weapon", 1.2, box())
    with pytest.raises(ValueError):
        Detection(" ", 0.5, box())
    assert box(0, 0, 10, 10).iou(box(5, 0, 10, 10)) == pytest.approx(50 / 150)
    assert box(0, 0, 10, 10).iou(box(50, 50, 10, 10)) == 0.0


def test_the_synthetic_detector_is_deterministic_and_labelled_synthetic(camera_id, tmp_path: Path) -> None:
    detector = SyntheticScriptedDetector([ScriptEntry(2, 4, "weapon", 0.8, box())])
    assert detector.info.synthetic is True and "synthetic" in detector.info.name
    assert [len(detector.detect(frame(camera_id, i))) for i in range(6)] == [0, 0, 1, 1, 1, 0]
    assert detector.detect(frame(camera_id, 3)) == detector.detect(frame(camera_id, 3))
    script = tmp_path / "script.json"
    script.write_text('[{"from_frame": 0, "to_frame": 1, "label": "weapon", "confidence": 0.7, "box": [1, 1, 5, 5]}]')
    assert SyntheticScriptedDetector.from_json(script).detect(frame(camera_id, 1))[0].confidence == 0.7
    script.write_text('[{"from_frame": 3, "to_frame": 1, "label": "x", "confidence": 0.7, "box": [1, 1, 5, 5]}]')
    with pytest.raises(ValueError, match="Invalid synthetic"):
        SyntheticScriptedDetector.from_json(script)


def test_the_tracker_keeps_ids_for_overlapping_boxes_per_camera(camera_id) -> None:
    from uuid import uuid4

    tracker = IouTracker(iou_threshold=0.3, max_missed_frames=2)
    a = tracker.update(camera_id, 0, [det("person", 0.9, box(0, 0)), det("person", 0.9, box(100, 100))])
    b = tracker.update(camera_id, 1, [det("person", 0.9, box(2, 1)), det("person", 0.9, box(101, 99))])
    assert [t.track_id for t in a] == [t.track_id for t in b] and b[0].hits == 2
    # Another label, or another camera, never continues a track.
    other_label = tracker.update(camera_id, 2, [det("weapon", 0.9, box(2, 1))])
    other_camera = tracker.update(uuid4(), 2, [det("person", 0.9, box(2, 1))])
    assert other_label[0].track_id not in {t.track_id for t in b}
    assert other_camera[0].track_id not in {t.track_id for t in b}
    # A track unseen for longer than max_missed_frames is dropped.
    later = tracker.update(camera_id, 10, [det("person", 0.9, box(2, 1))])
    assert later[0].track_id not in {t.track_id for t in b}


def test_the_onnx_adapter_loads_runs_and_times_a_real_graph() -> None:
    info = ModelInfo(name="scale2-test-graph", version="1", labels=())
    adapter = OnnxRuntimeAdapter(FIXTURE, info, device="cpu")
    assert adapter.input_names == ["input"] and adapter.providers == ["CPUExecutionProvider"]
    out = adapter.run({"input": np.ones((2, 4), np.float32)})
    assert np.array_equal(out[0], np.full((2, 4), 2.0, np.float32))
    adapter.run({"input": np.zeros((1, 4), np.float32)})
    assert adapter.stats.runs == 2 and adapter.stats.mean_ms > 0 and adapter.stats.percentile_ms(95) > 0
    with pytest.raises(InferenceError, match="Missing model inputs"):
        adapter.run({})
    with pytest.raises(InferenceError):
        adapter.run({"input": np.ones((2, 3), np.float32)})  # wrong shape
    assert OnnxRuntimeAdapter(FIXTURE, info, device="auto").providers[-1] == "CPUExecutionProvider"


def test_the_onnx_adapter_refuses_missing_files_and_unavailable_devices(tmp_path: Path) -> None:
    info = ModelInfo(name="x", version="1", labels=())
    with pytest.raises(ModelLoadError, match="not found"):
        OnnxRuntimeAdapter(tmp_path / "missing.onnx", info)
    broken = tmp_path / "broken.onnx"
    broken.write_bytes(b"not a model")
    with pytest.raises(ModelLoadError, match="Cannot load"):
        OnnxRuntimeAdapter(broken, info)
    import onnxruntime as ort

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        with pytest.raises(ModelLoadError, match="cuda execution provider is not available"):
            OnnxRuntimeAdapter(FIXTURE, info, device="cuda")
