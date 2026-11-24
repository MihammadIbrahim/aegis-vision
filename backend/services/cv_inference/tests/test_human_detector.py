"""YOLOX person detector.

Kinds of test, kept apart on purpose:
1. mocked model outputs: a fake adapter returns hand-built tensors (decoder and error handling);
2. synthetic fixtures: the tiny generated ONNX graph (loading and signature checks);
3. the real YOLOX-Tiny model, skipped (never faked) when its weights are absent.
Only kind 3 shows that a real model works.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from src.detectors.base_detector import DetectorError
from src.detectors.human_detector import YoloxDecoder, YoloxHumanDetector, YoloxModelSpec, letterbox, nms
from src.model_loader import InferenceStats, ModelLoadError
from src.schemas import Frame, ModelInfo
from tests.conftest import WEIGHTS_SPEC, person_photo, requires_real_model

FIXTURE = Path(__file__).parent / "fixtures" / "scale2.onnx"
SPEC = json.loads(WEIGHTS_SPEC.read_text()) if WEIGHTS_SPEC.is_file() else None
N = 52 * 52 + 26 * 26 + 13 * 13  # 3549 anchor points at 416×416
LOG4 = float(np.log(4))  # raw width/height that decodes to 4 × stride


def spec_dict(**changes: object) -> dict[str, object]:
    base = {
        "name": "yolox-tiny-coco", "version": "test", "format": "yolox-grid-v1", "file": "model.onnx",
        "sha256": "0" * 64, "input_name": "images", "input_height": 416, "input_width": 416,
        "strides": [8, 16, 32], "num_classes": 80, "person_class_index": 0, "pad_value": 114,
        "source": "test", "code_license": "test", "weights_license": "test",
    }  # fmt: skip
    return {**base, **changes}


def write_spec(tmp_path: Path, **changes: object) -> Path:
    path = tmp_path / "model.json"
    path.write_text(json.dumps(spec_dict(**changes)))
    return path


class FakeAdapter:
    """Returns a fixed raw output: stands in for the network in kind-1 tests."""

    def __init__(self, raw: np.ndarray | Exception) -> None:
        self._raw = raw
        self.info = ModelInfo(name="fake", version="0", labels=())
        self.stats = InferenceStats()
        self.inputs: list[dict[str, np.ndarray]] = []

    @property
    def input_names(self) -> list[str]:
        return ["images"]

    def run(self, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.inputs.append(inputs)
        if isinstance(self._raw, Exception):
            raise self._raw
        return [self._raw]

    def close(self) -> None:
        return None


def raw_output() -> np.ndarray:
    return np.zeros((1, N, 85), np.float32)


def put(raw: np.ndarray, index: int, *, xy=(0.5, 0.5), wh=(LOG4, LOG4), obj=0.9, person=0.8, other=None):  # type: ignore[no-untyped-def]
    raw[0, index, 0:2] = xy
    raw[0, index, 2:4] = wh
    raw[0, index, 4] = obj
    raw[0, index, 5] = person
    if other is not None:
        raw[0, index, 5 + other[0]] = other[1]


def frame_of(image: np.ndarray) -> Frame:
    return Frame(uuid4(), "test", 0, datetime(2026, 10, 31, tzinfo=UTC), image)


# --- 1. Mocked outputs ---------------------------------------------------------------------------------


def test_letterbox_matches_the_publishers_preprocessing() -> None:
    image = np.zeros((200, 400, 3), np.uint8)
    image[:, :, 0] = 10  # blue channel stays first: BGR is kept
    tensor, ratio = letterbox(image, 416, 416, 114)
    assert tensor.shape == (1, 3, 416, 416) and tensor.dtype == np.float32 and tensor.flags["C_CONTIGUOUS"]
    assert ratio == pytest.approx(416 / 400)
    assert tensor[0, 0, 0, 0] == 10 and tensor[0, 1, 0, 0] == 0  # top-left placement, no normalisation
    assert (tensor[0, :, 300:, :] == 114).all()  # bottom padding
    for bad in (np.zeros((10, 10), np.uint8), np.zeros((10, 10, 4), np.uint8), np.zeros((0, 10, 3), np.uint8),
                np.zeros((10, 10, 3), np.float32)):  # fmt: skip
        with pytest.raises(DetectorError):
            letterbox(bad, 416, 416, 114)


def test_decoding_keeps_confident_persons_only_and_maps_boxes_back() -> None:
    spec = YoloxModelSpec.model_validate(spec_dict())
    decoder = YoloxDecoder(spec, score_threshold=0.35, nms_iou=0.45, max_detections=100)
    raw = raw_output()
    put(raw, 5 * 52 + 10)  # stride 8, grid (10, 5): centre (84, 44), size 32 → score 0.72
    put(raw, 100, person=0.01, other=(2, 0.99))  # a confident car: not a person
    put(raw, 2000, obj=0.5, person=0.5)  # score 0.25: below the threshold
    detections = decoder.decode(raw, ratio=0.5, frame_width=1000, frame_height=1000)
    assert len(detections) == 1
    person = detections[0]
    assert person.label == "person" and person.confidence == pytest.approx(0.72)
    assert (person.box.x1, person.box.y1, person.box.x2, person.box.y2) == pytest.approx((136, 56, 200, 120))
    assert decoder.decode(raw_output(), 1.0, 416, 416) == []  # nothing in the frame


def test_overlapping_boxes_are_suppressed_and_boxes_clipped() -> None:
    spec = YoloxModelSpec.model_validate(spec_dict())
    decoder = YoloxDecoder(spec, score_threshold=0.3, nms_iou=0.45, max_detections=100)
    raw = raw_output()
    put(raw, 5 * 52 + 10, person=0.9)
    put(raw, 5 * 52 + 11, xy=(0.0, 0.5), person=0.7)  # 4 px to the side: same person
    put(raw, 0, xy=(0.0, 0.0), wh=(float(np.log(8)), float(np.log(8))), person=0.9)  # at the corner, extends outside
    detections = decoder.decode(raw, ratio=1.0, frame_width=416, frame_height=416)
    assert len(detections) == 2 and detections[0].confidence == pytest.approx(0.81)
    corner = detections[1].box
    assert corner.x1 == 0 and corner.y1 == 0 and corner.x2 == pytest.approx(32)
    assert nms(
        np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]], float), np.array([0.9, 0.8, 0.7]), 0.45
    ) == [0, 2]


def test_invalid_model_outputs_are_errors(tmp_path: Path) -> None:
    spec = YoloxModelSpec.model_validate(spec_dict())
    decoder = YoloxDecoder(spec, score_threshold=0.35, nms_iou=0.45, max_detections=10)
    with pytest.raises(DetectorError, match="Unexpected model output shape"):
        decoder.decode(np.zeros((1, 100, 85), np.float32), 1.0, 416, 416)
    broken = raw_output()
    broken[0, 3, 1] = np.nan
    with pytest.raises(DetectorError, match="non-finite"):
        decoder.decode(broken, 1.0, 416, 416)
    detector = YoloxHumanDetector(write_spec(tmp_path), adapter=FakeAdapter(RuntimeError("boom")))
    with pytest.raises(DetectorError, match="Person detection failed: RuntimeError"):
        detector.detect(frame_of(np.zeros((100, 100, 3), np.uint8)))


def test_the_detector_feeds_the_adapter_and_returns_project_detections(tmp_path: Path) -> None:
    raw = raw_output()
    put(raw, 5 * 52 + 10)
    adapter = FakeAdapter(raw)
    detector = YoloxHumanDetector(write_spec(tmp_path), adapter=adapter, score_threshold=0.5)
    detections = detector.detect(frame_of(np.zeros((416, 416, 3), np.uint8)))
    assert [d.label for d in detections] == ["person"]
    assert adapter.inputs[0]["images"].shape == (1, 3, 416, 416)
    assert detector.info.synthetic is False and detector.info.labels == ("person",)
    assert YoloxHumanDetector(write_spec(tmp_path), adapter=FakeAdapter(raw), score_threshold=0.8).detect(
        frame_of(np.zeros((416, 416, 3), np.uint8))) == []  # fmt: skip


@pytest.mark.parametrize(
    "changes",
    [
        {"format": "yolov8"},  # another output layout: never decoded as YOLOX
        {"sha256": "xyz"},
        {"person_class_index": 80},
        {"strides": [8, 16, 30]},
        {"input_height": 400},
        {"unexpected": 1},
    ],
)
def test_invalid_metadata_is_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    with pytest.raises(ModelLoadError, match="Invalid model metadata"):
        YoloxHumanDetector(write_spec(tmp_path, **changes))


# --- 2. Synthetic fixtures: loading and checks ------------------------------------------------------------


def test_missing_files_wrong_checksums_and_wrong_models_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ModelLoadError, match="metadata not found"):
        YoloxHumanDetector(tmp_path / "absent.json")
    with pytest.raises(ModelLoadError, match="not found next to"):
        YoloxHumanDetector(write_spec(tmp_path))
    shutil.copy(FIXTURE, tmp_path / "model.onnx")
    with pytest.raises(ModelLoadError, match="Checksum mismatch"):
        YoloxHumanDetector(write_spec(tmp_path))
    import hashlib

    real_sha = hashlib.sha256((tmp_path / "model.onnx").read_bytes()).hexdigest()
    with pytest.raises(ModelLoadError, match="does not match the metadata"):  # a valid ONNX, but not YOLOX
        YoloxHumanDetector(write_spec(tmp_path, sha256=real_sha))


# --- 3. The real YOLOX-Tiny model -----------------------------------------------------------------------------


@requires_real_model
def test_the_real_model_finds_the_person_in_a_photograph() -> None:
    detector = YoloxHumanDetector(WEIGHTS_SPEC)
    photo = person_photo()  # 512×600 portrait
    people = detector.detect(frame_of(photo))
    assert len(people) == 1 and people[0].confidence > 0.8
    b = people[0].box
    assert b.x1 < 100 and b.y1 < 100 and b.x2 > 400 and b.y2 > 500  # the subject fills the picture
    assert detector.adapter.stats.runs == 1
    # Scaled into a larger scene, the box follows; a plain frame has no one.
    scene = np.full((720, 1280, 3), 128, np.uint8)
    scene[200:500, 600:856] = cv2.resize(photo, (256, 300))
    found = detector.detect(frame_of(scene))
    assert len(found) == 1 and 580 < found[0].box.x1 < 640 and 820 < found[0].box.x2 < 880
    assert detector.detect(frame_of(np.full((480, 640, 3), 114, np.uint8))) == []


@requires_real_model
def test_the_real_model_file_matches_its_metadata() -> None:
    assert SPEC is not None
    detector = YoloxHumanDetector(WEIGHTS_SPEC)
    assert detector.spec.sha256 == SPEC["sha256"] and detector.adapter.providers == ["CPUExecutionProvider"]
