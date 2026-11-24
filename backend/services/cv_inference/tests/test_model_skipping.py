"""True per-camera model skipping (decision D-028): ``GatedDetector`` asks the camera's AI configuration before each
model runs, so a module that is off costs no inference. Spies count every call."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from src.detectors.composite import CompositeDetector
from src.reid.reid_extractor import ReIdTrackObserver
from src.runtime import AiConfigWatcher, CameraConfig, GatedDetector, OverlayWriter, modules_served
from src.schemas import Detection, Frame, ModelInfo, Track
from tests.conftest import box, det


class SpyDetector:
    """A model with fixed labels that counts its calls (and its resets, like the fight clip buffer)."""

    def __init__(self, name: str, labels: tuple[str, ...]) -> None:
        self.info = ModelInfo(name, "1", labels)
        self.call_count = 0
        self.resets = 0

    def detect(self, frame: Frame) -> list[Detection]:
        self.call_count += 1
        return [det(label) for label in self.info.labels]

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        pass


class StaticWatcher(AiConfigWatcher):
    """The camera's configuration without a Core API (no polling, no status reports)."""

    def __init__(self, modules: set[str]) -> None:
        super().__init__(uuid4(), None, "token")  # type: ignore[arg-type]
        self.set(modules)

    def set(self, modules: set[str]) -> None:
        self.config = CameraConfig(uuid4(), uuid4(), frozenset(modules))

    def refresh(self, *, force: bool = False) -> CameraConfig | None:
        return self.config

    def report(self, running=None) -> None:  # type: ignore[no-untyped-def]
        pass


def a_frame(camera) -> Frame:  # type: ignore[no-untyped-def]
    return Frame(camera, "spool:test", 1, datetime(2026, 11, 20, 10, 0, tzinfo=UTC), np.zeros((100, 200, 3), np.uint8))


def pipeline(modules: set[str]) -> tuple[GatedDetector, SpyDetector, SpyDetector, SpyDetector, StaticWatcher]:
    person = SpyDetector("persons", ("person",))
    weapon = SpyDetector("weapons", ("pistol", "knife"))
    fight = SpyDetector("fights", ("fight_violence",))
    watcher = StaticWatcher(modules)
    return GatedDetector(CompositeDetector([person, weapon, fight]), watcher), person, weapon, fight, watcher


def run(gated: GatedDetector, watcher: StaticWatcher, frames: int = 3) -> list[str]:
    labels: list[str] = []
    for _ in range(frames):
        labels = [d.label for d in gated.detect(a_frame(watcher.camera_id))]
    return labels


def test_the_modules_each_model_serves() -> None:
    assert modules_served(SpyDetector("p", ("person",))) == {"human_detection", "person_reid"}
    assert modules_served(SpyDetector("w", ("pistol",))) == {"weapon_detection"}
    assert modules_served(SpyDetector("f", ("fight_violence",))) == {"fight_detection"}


def test_weapon_off_never_calls_the_weapon_model() -> None:
    gated, person, weapon, fight, watcher = pipeline({"human_detection", "fight_detection"})
    assert run(gated, watcher) == ["person", "fight_violence"]
    assert weapon.call_count == 0 and person.call_count == 3 and fight.call_count == 3


def test_fight_off_never_calls_the_fight_classifier_and_clears_its_clip_buffer() -> None:
    gated, person, weapon, fight, watcher = pipeline({"human_detection", "weapon_detection"})
    assert run(gated, watcher) == ["person", "pistol", "knife"]
    assert fight.call_count == 0 and fight.resets == 1  # cleared once when it stopped, not every frame
    watcher.set({"fight_detection"})
    assert run(gated, watcher, 2) == ["fight_violence"]
    assert fight.call_count == 2 and person.call_count == 3 and weapon.call_count == 3


def test_no_module_selected_runs_no_model_at_all() -> None:
    gated, person, weapon, fight, watcher = pipeline(set())  # D-028: a new camera runs nothing
    assert run(gated, watcher, 5) == []
    assert (person.call_count, weapon.call_count, fight.call_count) == (0, 0, 0)
    assert gated.skipped == [5, 5, 5]


def test_human_and_reid_off_skip_the_person_detector() -> None:
    gated, person, weapon, _, watcher = pipeline({"weapon_detection"})
    assert run(gated, watcher) == ["pistol", "knife"]
    assert person.call_count == 0 and weapon.call_count == 3


def test_reid_without_human_detection_detects_persons_internally_only(tmp_path: Path) -> None:
    gated, person, weapon, fight, watcher = pipeline({"person_reid"})
    assert run(gated, watcher) == ["person"]  # kept for tracking and the Re-ID crops
    assert person.call_count == 3 and weapon.call_count == 0 and fight.call_count == 0
    overlay = OverlayWriter(tmp_path, watcher)
    frame = a_frame(watcher.camera_id)
    overlay.observe(frame, [Track(7, det("person", b=box(20, 10, 40, 50)), hits=3, first_frame_index=0)])
    written = next(tmp_path.rglob("overlay.json")).read_text()
    assert '"detections": []' in written  # never drawn while human detection is off


class SpyEmbedder:
    dimension = 4

    def __init__(self) -> None:
        self.call_count = 0

    def embed(self, frame, box):  # type: ignore[no-untyped-def]
        self.call_count += 1
        return np.ones(4, np.float32)


class SpyMatcher:
    def __init__(self) -> None:
        self.call_count = 0

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def called(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.call_count += 1
            return []

        return called


class SpySink:
    def __init__(self) -> None:
        self.call_count = 0

    def record(self, observation) -> None:  # type: ignore[no-untyped-def]
        self.call_count += 1


@pytest.mark.parametrize("modules", [set(), {"human_detection", "weapon_detection", "fight_detection"}])
def test_reid_off_never_embeds_matches_or_sends(modules: set[str]) -> None:
    watcher = StaticWatcher(modules)
    embedder, matcher, sink = SpyEmbedder(), SpyMatcher(), SpySink()
    observer = ReIdTrackObserver(
        embedder, matcher, campus_id=uuid4(), sink=sink, every_n_frames=1,  # type: ignore[arg-type]
        allowed=lambda: watcher.allows("person_reid"),
    )  # fmt: skip
    frame = a_frame(watcher.camera_id)
    for hits in range(1, 6):
        observer.observe(frame, [Track(1, det("person", b=box(20, 10, 100, 80)), hits=hits, first_frame_index=0)])
    assert (embedder.call_count, matcher.call_count, sink.call_count) == (0, 0, 0)
