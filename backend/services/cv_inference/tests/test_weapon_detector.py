"""Weapon detector: manifest checks, letterbox/decode maths, class thresholds, and the dummy ONNX
graph end to end (its handgun score is the mean red value of the image)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from src.detectors.base_detector import DetectorError
from src.detectors.weapon_detector import WeaponOnnxDetector, letterbox, nms_per_class
from src.manifest import ModelManifest
from src.model_loader import InferenceStats, ModelLoadError
from src.schemas import Frame, ModelInfo

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WEAPON = FIXTURES / "weapon_dummy.manifest.json"


def frame_of(image: np.ndarray, index: int = 0) -> Frame:
    return Frame(camera_id=uuid4(), source_id="t", index=index, timestamp=datetime(2026, 11, 14, tzinfo=UTC), image=image)


def solid(bgr: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> np.ndarray:
    image = np.zeros((size[0], size[1], 3), np.uint8)
    image[:] = bgr
    return image


class FakeAdapter:
    def __init__(self, raw: np.ndarray | Exception) -> None:
        self.raw = raw
        self.info = ModelInfo("fake", "1", ())
        self.stats = InferenceStats()
        self.inputs: list[dict[str, np.ndarray]] = []

    @property
    def input_names(self) -> list[str]:
        return ["images"]

    def run(self, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.inputs.append(inputs)
        if isinstance(self.raw, Exception):
            raise self.raw
        return [self.raw]

    def close(self) -> None:
        return None


def test_letterbox_pads_both_sides_and_nms_is_class_aware() -> None:
    padded, scale, (left, top) = letterbox(np.zeros((60, 100, 3), np.uint8), 64, 64, 114)
    assert padded.shape == (64, 64, 3) and scale == pytest.approx(0.64) and (left, top) == (0, 13)
    assert padded[0, 0].tolist() == [114, 114, 114] and padded[13, 0].tolist() == [0, 0, 0]
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [0, 0, 10, 10]], np.float32)
    assert nms_per_class(boxes, np.array([0.9, 0.8, 0.7], np.float32), np.array([0, 0, 1]), 0.5, 10) == [0, 2]


def test_the_dummy_model_detects_a_red_handgun_and_a_fixed_knife() -> None:
    detector = WeaponOnnxDetector(WEAPON, verify_checksum=True)
    assert detector.info.name == "weapon-dummy" and detector.info.labels == ("handgun", "knife") and not detector.info.synthetic
    assert detector.class_thresholds == {"handgun": 0.5, "knife": 0.8}  # calibrated values from the manifest
    red = detector.detect(frame_of(solid((0, 0, 255))))  # BGR red → RGB channel 0 = 1.0
    assert sorted(d.label for d in red) == ["handgun", "knife"]
    handgun = next(d for d in red if d.label == "handgun")
    assert handgun.confidence == pytest.approx(1.0)
    assert (round(handgun.box.x1), round(handgun.box.y1), round(handgun.box.x2), round(handgun.box.y2)) == (22, 20, 42, 44)
    dark = detector.detect(frame_of(solid((0, 0, 0))))
    assert [d.label for d in dark] == ["knife"]  # the handgun score is 0 on a black image
    wide = detector.detect(frame_of(solid((0, 0, 255), (60, 120))))  # mapped back through scale and padding
    gun = next(d for d in wide if d.label == "handgun")
    assert gun.box.x2 <= 120 and gun.box.y2 <= 60 and gun.box.area > 0
    assert detector.adapter.stats.runs == 3 and detector.adapter.stats.mean_ms > 0


def test_thresholds_can_be_overridden_and_decoding_is_checked() -> None:
    strict = WeaponOnnxDetector(WEAPON, thresholds={"knife": 0.95})
    assert strict.detect(frame_of(solid((0, 0, 0)))) == []
    with pytest.raises(DetectorError, match="output shape"):
        WeaponOnnxDetector(WEAPON, adapter=FakeAdapter(np.zeros((1, 6, 3, 1), np.float32))).detect(frame_of(solid((0, 0, 0))))
    with pytest.raises(DetectorError, match="non-finite"):
        WeaponOnnxDetector(WEAPON, adapter=FakeAdapter(np.full((1, 6, 3), np.nan, np.float32))).detect(frame_of(solid((0, 0, 0))))
    with pytest.raises(DetectorError, match="Weapon detection failed"):
        WeaponOnnxDetector(WEAPON, adapter=FakeAdapter(RuntimeError("boom"))).detect(frame_of(solid((0, 0, 0))))
    fake = FakeAdapter(np.zeros((1, 6, 3), np.float32))
    WeaponOnnxDetector(WEAPON, adapter=fake).detect(frame_of(solid((255, 0, 0))))
    tensor = fake.inputs[0]["images"]
    assert tensor.shape == (1, 3, 64, 64) and tensor.dtype == np.float32
    assert tensor[0, 2].max() == pytest.approx(1.0) and tensor[0, 0].max() == 0.0  # BGR blue became RGB channel 2


def test_manifest_file_and_checksum_problems_are_refused(tmp_path: Path) -> None:
    data = json.loads(WEAPON.read_text())
    (tmp_path / "x.manifest.json").write_text(json.dumps(data))
    with pytest.raises(ModelLoadError, match="not found"):
        WeaponOnnxDetector(tmp_path / "x.manifest.json")
    (tmp_path / "weapon_dummy.onnx").write_bytes(b"not a model")
    with pytest.raises(ModelLoadError, match="Checksum"):
        WeaponOnnxDetector(tmp_path / "x.manifest.json")
    (tmp_path / "y.manifest.json").write_text(json.dumps({**data, "task": "person_reid"}))
    with pytest.raises(ModelLoadError, match="person_reid"):
        WeaponOnnxDetector(tmp_path / "y.manifest.json")
    (tmp_path / "z.manifest.json").write_text(json.dumps({**data, "export": {**data["export"], "parity_passed": False}}))
    with pytest.raises(ModelLoadError, match="parity"):
        WeaponOnnxDetector(tmp_path / "z.manifest.json")
    (tmp_path / "bad.manifest.json").write_text('{"name": 1}')
    with pytest.raises(ModelLoadError, match="Invalid model manifest"):
        ModelManifest.load(tmp_path / "bad.manifest.json")
    with pytest.raises(ModelLoadError, match="not found or unreadable"):
        ModelManifest.load(tmp_path / "missing.manifest.json")
