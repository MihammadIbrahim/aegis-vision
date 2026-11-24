"""ByteTracker: camera-local tracks with Kalman prediction and two-stage association."""

from uuid import uuid4

import pytest

from src.tracking.single_tracker import ByteTracker
from tests.conftest import box, det


def walk(x: float, y: float = 50, conf: float = 0.9):  # type: ignore[no-untyped-def]
    return det("person", conf, box(x, y, 40, 100))


def test_a_moving_person_keeps_one_id_and_is_confirmed_on_the_second_frame(camera_id) -> None:
    tracker = ByteTracker()
    first = tracker.update(camera_id, 0, [walk(0)])
    assert len(first) == 1 and first[0].confirmed is False and first[0].camera_id == camera_id
    ids = [tracker.update(camera_id, i, [walk(6 * i)])[0] for i in range(1, 10)]
    assert {t.track_id for t in ids} == {first[0].track_id} and all(t.confirmed for t in ids)
    assert ids[-1].hits == 10


def test_several_people_get_separate_ids(camera_id) -> None:
    tracker = ByteTracker()
    a = tracker.update(camera_id, 0, [walk(0), walk(300), walk(600)])
    b = tracker.update(camera_id, 1, [walk(605), walk(5), walk(296)])  # different order
    assert len({t.track_id for t in a}) == 3
    by_x = {round(t.detection.box.x1, -2): t.track_id for t in b}
    assert by_x == {round(t.detection.box.x1, -2): t.track_id for t in a}


def test_missed_frames_are_bridged_and_old_tracks_expire(camera_id) -> None:
    tracker = ByteTracker(max_lost_frames=3)
    for i in range(3):
        original = tracker.update(camera_id, i, [walk(5 * i)])[0].track_id
    assert tracker.update(camera_id, 3, []) == []  # empty frames are fine
    tracker.update(camera_id, 4, [])
    back = tracker.update(camera_id, 5, [walk(25)])  # predicted motion carries it through the gap
    assert back[0].track_id == original
    for i in range(6, 11):
        tracker.update(camera_id, i, [])
    later = tracker.update(camera_id, 11, [walk(55)])
    assert later[0].track_id != original and later[0].confirmed is False


def test_low_confidence_keeps_a_track_but_never_starts_one(camera_id) -> None:
    tracker = ByteTracker(high_threshold=0.5, low_threshold=0.1, new_track_threshold=0.6)
    assert tracker.update(camera_id, 0, [walk(0, conf=0.3)]) == []  # too weak to start a track
    tid = tracker.update(camera_id, 1, [walk(0)])[0].track_id
    tracker.update(camera_id, 2, [walk(4)])
    occluded = tracker.update(camera_id, 3, [walk(8, conf=0.3)])  # partly hidden: low score
    assert [t.track_id for t in occluded] == [tid]
    assert tracker.update(camera_id, 4, [walk(12, conf=0.05)]) == []  # below the low threshold: ignored


def test_one_off_detections_do_not_leave_duplicate_tracks(camera_id) -> None:
    tracker = ByteTracker()
    flicker = tracker.update(camera_id, 0, [walk(500)])[0].track_id  # seen once, then gone
    tracker.update(camera_id, 1, [walk(0)])
    tracker.update(camera_id, 2, [walk(4)])
    tracks = tracker.update(camera_id, 3, [walk(8), walk(500)])
    assert flicker not in {t.track_id for t in tracks}  # the tentative track was dropped, a new one starts


def test_tracks_are_local_to_each_camera_and_never_shared(camera_id) -> None:
    other_camera = uuid4()
    tracker = ByteTracker()
    for i in range(3):
        here = tracker.update(camera_id, i, [walk(5 * i)])
        there = tracker.update(other_camera, i, [walk(5 * i)])  # identical boxes on another camera
    assert here[0].track_id != there[0].track_id
    assert here[0].camera_id == camera_id and there[0].camera_id == other_camera
    assert (
        tracker.update(other_camera, 3, []) == []
        and tracker.update(camera_id, 3, [walk(15)])[0].track_id == here[0].track_id
    )
    tracker.reset()
    assert tracker.update(camera_id, 4, [walk(20)])[0].track_id != here[0].track_id


def test_labels_are_tracked_separately(camera_id) -> None:
    tracker = ByteTracker()
    a = tracker.update(camera_id, 0, [walk(0), det("weapon", 0.9, box(0, 50, 40, 100))])
    assert len({t.track_id for t in a}) == 2


def test_invalid_settings_are_refused() -> None:
    with pytest.raises(ValueError):
        ByteTracker(low_threshold=0.6, high_threshold=0.5)
    with pytest.raises(ValueError):
        ByteTracker(high_threshold=0.5, new_track_threshold=0.4)
