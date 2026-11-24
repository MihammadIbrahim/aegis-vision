"""Re-ID: the ONNX embedder, the crop quality filter, the campus-scoped Faiss/NumPy gallery with its
time window, the match bands, and the track observer's rules (same camera excluded, UNCERTAIN stays
uncertain, nothing is ever 'confirmed', observations go to the sink and never to the alert intake)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from src.reid.embedding_store import HAVE_FAISS, CampusGallery
from src.reid.reid_extractor import (
    CropQualityFilter,
    JsonlObservationSink,
    MatchBand,
    MemoryObservationSink,
    PersonReIdOnnxEmbedder,
    ReIdMatcher,
    ReIdObservation,
    ReIdTrackObserver,
)
from src.schemas import BoundingBox, Detection, Frame, Track

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REID = FIXTURES / "reid_dummy.manifest.json"
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
BOX = BoundingBox(40, 20, 80, 110)


def scene(colour: tuple[int, int, int], *, box: BoundingBox = BOX, size: tuple[int, int] = (160, 200), camera=None, index: int = 0, at: datetime = T0, blur: bool = False) -> tuple[Frame, Track]:
    rng = np.random.default_rng(index)
    image = rng.integers(0, 255, (size[0], size[1], 3), dtype=np.uint8)  # textured background
    image[int(box.y1) : int(box.y2), int(box.x1) : int(box.x2)] = colour
    if not blur:  # stripes inside the crop so the sharpness check passes
        image[int(box.y1) : int(box.y2) : 2, int(box.x1) : int(box.x2)] = tuple(min(255, c + 60) for c in colour)
    frame = Frame(camera_id=camera or uuid4(), source_id="v", index=index, timestamp=at, image=image)
    track = Track(track_id=1, detection=Detection("person", 0.9, box), hits=5, first_frame_index=0, camera_id=frame.camera_id)
    return frame, track


def test_embedder_returns_unit_vectors_from_the_manifest_model() -> None:
    embedder = PersonReIdOnnxEmbedder(REID)
    assert embedder.dimension == 512 and embedder.likely_threshold == 0.9 and embedder.uncertain_threshold == 0.6
    frame, track = scene((200, 30, 30))
    vector = embedder.embed(frame, track.detection.box)
    assert vector.shape == (512,) and vector.dtype == np.float32 and np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)
    same = embedder.embed(scene((200, 30, 30), camera=uuid4())[0], BOX)
    other = embedder.embed(scene((30, 200, 30))[0], BOX)
    assert float(vector @ same) > 0.99 > float(vector @ other)
    assert embedder.preprocess(np.zeros((90, 40, 3), np.uint8)).shape == (1, 3, 64, 32)


def test_quality_filter_reasons() -> None:
    quality = CropQualityFilter(min_width=20, min_height=40, min_sharpness=20.0)
    frame, track = scene((200, 30, 30))
    assert quality.reason_to_skip(frame, track) is None
    assert quality.reason_to_skip(frame, Track(2, Detection("person", 0.2, BOX), 1, 0)) == "low_confidence"
    assert quality.reason_to_skip(frame, Track(2, Detection("person", 0.9, BoundingBox(40, 20, 50, 40)), 1, 0)) == "too_small"
    assert quality.reason_to_skip(frame, Track(2, Detection("person", 0.9, BoundingBox(40, 20, 140, 60)), 1, 0)) == "bad_aspect"
    assert quality.reason_to_skip(frame, Track(2, Detection("person", 0.9, BoundingBox(0, 20, 40, 110)), 1, 0)) == "touches_border"
    blurred, blurred_track = scene((120, 120, 120), blur=True)
    assert quality.reason_to_skip(blurred, blurred_track) == "blurred"


@pytest.mark.parametrize("use_faiss", [True, False])
def test_gallery_is_campus_scoped_time_windowed_and_camera_exclusive(use_faiss: bool) -> None:
    gallery = CampusGallery(4, window_seconds=60, use_faiss=use_faiss)
    assert gallery.backend == ("faiss" if use_faiss and HAVE_FAISS else "numpy")
    campus, other_campus, cam_a, cam_b = uuid4(), uuid4(), uuid4(), uuid4()
    e1 = np.array([1, 0, 0, 0], np.float32)
    e2 = np.array([0.9, 0.1, 0, 0], np.float32)
    gallery.upsert(campus, cam_a, 1, e1, T0)
    gallery.upsert(campus, cam_b, 7, e2, T0 + timedelta(seconds=10))
    gallery.upsert(other_campus, cam_b, 9, e1, T0)
    found = gallery.search(campus, e1, T0 + timedelta(seconds=20), k=5, exclude_camera=cam_a)
    assert [(c.entry.camera_id, c.entry.track_id) for c in found] == [(cam_b, 7)]  # same camera excluded; other campus invisible
    assert found[0].similarity == pytest.approx(float(e1 @ e2 / np.linalg.norm(e2)), abs=1e-5)
    assert gallery.search(campus, e1, T0 + timedelta(seconds=20), k=5)[0].entry.track_id == 1  # nothing excluded: the exact copy wins
    assert gallery.search(campus, e1, T0 + timedelta(seconds=300), k=5) == []  # outside the window
    gallery.upsert(campus, cam_a, 1, e2, T0 + timedelta(seconds=30))  # replaces the track's vector
    assert gallery.size(campus) == 2
    assert gallery.expire(campus, T0 + timedelta(seconds=100)) == 2 and gallery.size(campus) == 0
    with pytest.raises(ValueError):
        gallery.upsert(campus, cam_a, 2, np.array([1, 0, 0], np.float32), T0)


def test_matcher_bands_and_observer_rules(tmp_path: Path) -> None:
    embedder = PersonReIdOnnxEmbedder(REID)
    gallery = CampusGallery(embedder.dimension, window_seconds=120)
    matcher = ReIdMatcher(gallery, likely_threshold=0.9, uncertain_threshold=0.6, top_k=3)
    assert (matcher.band(0.95), matcher.band(0.7), matcher.band(0.1)) == (MatchBand.LIKELY_MATCH, MatchBand.UNCERTAIN, MatchBand.NO_RELIABLE_MATCH)
    with pytest.raises(ValueError):
        ReIdMatcher(gallery, likely_threshold=0.5, uncertain_threshold=0.6)
    campus, cam_a, cam_b = uuid4(), uuid4(), uuid4()
    sink = JsonlObservationSink(tmp_path / "reid" / "obs.jsonl")
    observer = ReIdTrackObserver(embedder, matcher, campus_id=campus, sink=sink, every_n_frames=5, quality=CropQualityFilter(min_sharpness=5.0))

    red_a, track_a = scene((200, 30, 30), camera=cam_a)
    observer.observe(red_a, [track_a])  # first sighting: nothing in the gallery yet
    assert observer.stats.observations == 1 and observer.stats.bands == {"NO_RELIABLE_MATCH": 1}
    observer.observe(red_a, [Track(1, track_a.detection, 6, 0, cam_a)])  # hits=6: not a multiple of 5, skipped
    assert observer.stats.embeddings == 1
    tentative = Track(3, track_a.detection, 5, 0, cam_a, confirmed=False)
    observer.observe(red_a, [tentative, Track(4, Detection("handgun", 0.9, BOX), 5, 0, cam_a)])
    assert observer.stats.embeddings == 1  # tentative tracks and non-person labels are ignored

    red_b, track_b = scene((200, 30, 30), camera=cam_b, at=T0 + timedelta(seconds=30), index=3)
    observer.observe(red_b, [track_b])  # the same colours on another camera: a likely candidate
    red_a_again, track_a_again = scene((200, 30, 30), camera=cam_a, at=T0 + timedelta(seconds=40), index=4)
    observer.observe(red_a_again, [Track(2, track_a_again.detection, 5, 0, cam_a)])  # same camera as track 1: excluded
    green_b, track_g = scene((30, 200, 30), camera=cam_b, at=T0 + timedelta(seconds=50), index=5)
    observer.observe(green_b, [Track(8, track_g.detection, 5, 0, cam_b)])

    sink.close()
    lines = [json.loads(line) for line in (tmp_path / "reid" / "obs.jsonl").read_text().splitlines()]
    assert len(lines) == 4 and all(line["review_required"] is True for line in lines)
    assert all(line["band"] in {b.value for b in MatchBand} and "CONFIRMED" not in json.dumps(line) for line in lines)
    cross = lines[1]
    assert cross["band"] == "LIKELY_MATCH" and cross["candidates"][0]["camera_id"] == str(cam_a) and cross["candidates"][0]["track_id"] == 1
    assert all(c["camera_id"] != str(cam_a) for c in lines[2]["candidates"])  # camera A never matches camera A
    assert lines[3]["band"] in ("UNCERTAIN", "NO_RELIABLE_MATCH")
    assert observer.stats.tracks_seen == 4 and gallery.size(campus) == 4
    memory = MemoryObservationSink()
    memory.record(ReIdObservation(campus, cam_a, 1, 0, T0, MatchBand.UNCERTAIN))
    assert memory.observations[0].as_dict()["band"] == "UNCERTAIN"
