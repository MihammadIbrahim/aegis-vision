from uuid import uuid4

import pytest

from src.rules import EventRules, RuleConfig
from src.schemas import AlertType, Track
from tests.conftest import det, frame


def tracks(*detections) -> list[Track]:  # type: ignore[no-untyped-def]
    return [Track(track_id=i + 1, detection=d, hits=1, first_frame_index=0) for i, d in enumerate(detections)]


def rules(**changes) -> EventRules:  # type: ignore[no-untyped-def]
    config = {
        "class_map": {"weapon": AlertType.WEAPON, "fight_violence": AlertType.FIGHT_VIOLENCE},
        "min_consecutive_frames": 3,
        "cooldown_seconds": 10.0,
        "reset_after_missing_frames": 1,
        **changes,
    }
    return EventRules(RuleConfig(**config), model_name="test-model")


def test_an_event_needs_consecutive_confident_frames(camera_id) -> None:
    r = rules(thresholds={"weapon": 0.6})
    assert r.process(frame(camera_id, 0), tracks(det("weapon", 0.9))) == []
    assert r.process(frame(camera_id, 1), tracks(det("weapon", 0.55))) == []  # below its threshold
    assert r.process(frame(camera_id, 2), []) == []  # second miss ends the episode (reset after 1)
    assert r.stats.below_threshold == 1 and r.stats.unconfirmed_episodes == 1
    events = [r.process(frame(camera_id, i), tracks(det("weapon", 0.7 + i / 100))) for i in range(3, 6)]
    assert [len(e) for e in events] == [0, 0, 1]
    event = events[2][0]
    assert (event.alert_type, event.label, event.frame_index, event.camera_id) == (
        AlertType.WEAPON,
        "weapon",
        5,
        camera_id,
    )
    assert event.confidence == 0.75 and event.detected_at == frame(camera_id, 5).timestamp
    assert event.detection_id.startswith("test-model-") and len(event.detection_id) <= 128


def test_unmapped_labels_are_tracked_but_never_sent(camera_id) -> None:
    r = rules(min_consecutive_frames=1)
    for i in range(5):
        assert r.process(frame(camera_id, i), tracks(det("person", 0.99))) == []
    assert r.stats.unmapped == 5 and r.stats.events == 0


def test_a_continuing_event_repeats_only_after_the_cooldown(camera_id) -> None:
    r = rules(min_consecutive_frames=2, cooldown_seconds=1.0)  # frames are 0.2 s apart
    emitted = [i for i in range(16) if r.process(frame(camera_id, i), tracks(det("weapon", 0.9)))]
    assert emitted == [1, 6, 11]  # confirmed at frame 1, then every 1 s of video
    assert r.stats.suppressed_by_cooldown == 15 - 3  # positive confirmed frames 1..15, 3 sent


def test_ids_are_stable_across_runs_and_unique_across_events(camera_id) -> None:
    def run() -> list[str]:
        r = rules(min_consecutive_frames=1, cooldown_seconds=0.4)
        ids = []
        for i in range(6):
            ids += [e.detection_id for e in r.process(frame(camera_id, i), tracks(det("weapon", 0.9)))]
        return ids

    first, second = run(), run()
    assert first == second and len(set(first)) == len(first) == 3
    other_camera = rules(min_consecutive_frames=1).process(frame(uuid4(), 0), tracks(det("weapon", 0.9)))
    assert other_camera[0].detection_id not in first


def test_each_alert_type_and_camera_has_its_own_episode(camera_id) -> None:
    r = rules(min_consecutive_frames=1)
    events = r.process(frame(camera_id, 0), tracks(det("weapon", 0.8), det("weapon", 0.95), det("fight_violence", 0.7)))
    assert sorted((e.alert_type.value, e.confidence) for e in events) == [("FIGHT_VIOLENCE", 0.7), ("WEAPON", 0.95)]
    assert len(r.process(frame(uuid4(), 0), tracks(det("weapon", 0.8)))) == 1


def test_a_new_episode_after_a_gap_gets_a_new_id(camera_id) -> None:
    r = rules(min_consecutive_frames=1, reset_after_missing_frames=0)
    first = r.process(frame(camera_id, 0), tracks(det("weapon", 0.9)))
    r.process(frame(camera_id, 1), [])
    second = r.process(frame(camera_id, 2), tracks(det("weapon", 0.9)))
    assert first[0].detection_id != second[0].detection_id


def test_invalid_configuration_is_refused() -> None:
    with pytest.raises(ValueError):
        RuleConfig(class_map={}, default_threshold=1.5)
    with pytest.raises(ValueError):
        RuleConfig(class_map={}, min_consecutive_frames=0)
