"""Cross-camera person re-identification (requirements 4 C, 14.2) on top of the person tracker.

* ``PersonReIdOnnxEmbedder`` (``Embedder``): a crop of a tracked person → a 512-D L2-normalised vector,
  from the OSNet / ResNet50-IBN model trained in ``ml_training`` (ONNX + manifest).
* ``CropQualityFilter``: only crops that are large, upright, sharp and confidently detected are
  embedded; tiny or blurred crops produce unreliable vectors.
* ``ReIdMatcher``: queries the campus gallery within a time window, **excluding the same camera**
  (a camera-local track is already one person), and labels the best candidate with a band from the
  manifest's calibrated thresholds:
  ``LIKELY_MATCH`` (≥ likely threshold), ``UNCERTAIN`` (≥ uncertain threshold), ``NO_RELIABLE_MATCH``.
* ``ReIdTrackObserver`` (``TrackObserver``): attaches to the pipeline, embeds confirmed person tracks
  every ``every_n_frames`` (running mean per track), records an ``ReIdObservation`` through a sink, and
  adds the track to the gallery.

Hard rules, enforced in code and not configurable:
* a band is never ``CONFIRMED``; every observation is a *candidate* for a Supervisor's review;
* an ``UNCERTAIN`` candidate is reported as uncertain, never upgraded;
* embeddings are scoped to one campus and expire with the gallery window;
* Core API has **no Re-ID intake yet**, so observations go to a local JSONL file or memory
  (``ReIdObservationSink``), never to the alert intake and never as an alert.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

import cv2
import numpy as np

from src.detectors.base_detector import DetectorError
from src.manifest import ModelManifest, load_model
from src.model_loader import Device, ModelAdapter
from src.reid.embedding_store import CampusGallery, Candidate
from src.schemas import BoundingBox, Frame, ModelInfo, Track

logger = logging.getLogger(__name__)

PERSON_LABELS = frozenset({"person"})


class Embedder(Protocol):
    info: ModelInfo
    dimension: int

    def embed(self, frame: Frame, box: BoundingBox) -> np.ndarray:
        """A float32 vector of length ``dimension`` with unit L2 norm."""
        ...

    def close(self) -> None: ...


class MatchBand(StrEnum):
    LIKELY_MATCH = "LIKELY_MATCH"
    UNCERTAIN = "UNCERTAIN"
    NO_RELIABLE_MATCH = "NO_RELIABLE_MATCH"


def crop(image: np.ndarray, box: BoundingBox, *, margin: float = 0.0) -> np.ndarray:
    h, w = image.shape[:2]
    bw, bh = box.x2 - box.x1, box.y2 - box.y1
    x1 = max(0, int(np.floor(box.x1 - margin * bw)))
    y1 = max(0, int(np.floor(box.y1 - margin * bh)))
    x2 = min(w, int(np.ceil(box.x2 + margin * bw)))
    y2 = min(h, int(np.ceil(box.y2 + margin * bh)))
    if x2 - x1 < 1 or y2 - y1 < 1:
        raise DetectorError("The box lies outside the frame.")
    return image[y1:y2, x1:x2]


class PersonReIdOnnxEmbedder:
    def __init__(self, manifest_path: Path | str, *, device: Device = "cpu", verify_checksum: bool = True, adapter: ModelAdapter | None = None) -> None:
        if adapter is None:
            self.manifest, loaded = load_model(manifest_path, expected_task="person_reid", device=device, verify_checksum=verify_checksum)
            self.adapter: ModelAdapter = loaded
        else:
            self.manifest = ModelManifest.load(manifest_path)
            self.adapter = adapter
        if self.manifest.output.format != "l2-normalized-embedding":
            raise DetectorError("The manifest does not describe an embedding model.")
        self.info: ModelInfo = self.manifest.model_info()
        self.dimension = int(self.manifest.output.shape[-1])
        spec = self.manifest.input
        self._size = (spec.width, spec.height)
        self._mean = np.asarray(spec.mean, np.float32)
        self._std = np.asarray(spec.std, np.float32)
        self.likely_threshold = float(self.manifest.thresholds.get("likely_match", 0.75))
        self.uncertain_threshold = float(self.manifest.thresholds.get("uncertain", 0.55))

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        spec = self.manifest.input
        resized = cv2.resize(image, self._size, interpolation=cv2.INTER_LINEAR)
        rgb = resized[:, :, ::-1] if spec.color_order == "RGB" else resized
        array = (rgb.astype(np.float32) * spec.scale - self._mean) / self._std
        return np.ascontiguousarray(array.transpose(2, 0, 1)[None])

    def embed(self, frame: Frame, box: BoundingBox) -> np.ndarray:
        tensor = self.preprocess(crop(frame.image, box))
        try:
            outputs = self.adapter.run({self.manifest.input.name: tensor})
        except Exception as error:
            raise DetectorError(f"Re-ID embedding failed: {type(error).__name__}") from error
        if not outputs:
            raise DetectorError("The model returned no output.")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.dimension or not np.isfinite(vector).all():
            raise DetectorError(f"Unexpected embedding shape {vector.shape}.")
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector

    def close(self) -> None:
        self.adapter.close()


@dataclass(frozen=True, slots=True)
class CropQualityFilter:
    min_width: int = 32
    min_height: int = 64
    min_aspect: float = 1.2  # height / width: people are taller than wide
    max_aspect: float = 4.5
    min_confidence: float = 0.5
    min_sharpness: float = 20.0  # variance of the Laplacian on the grey crop
    border_margin: int = 2  # boxes touching the frame edge are usually cut off

    def reason_to_skip(self, frame: Frame, track: Track) -> str | None:
        box, detection = track.detection.box, track.detection
        w, h = box.x2 - box.x1, box.y2 - box.y1
        if detection.confidence < self.min_confidence:
            return "low_confidence"
        if w < self.min_width or h < self.min_height:
            return "too_small"
        aspect = h / w
        if not self.min_aspect <= aspect <= self.max_aspect:
            return "bad_aspect"
        if box.x1 <= self.border_margin or box.y1 <= self.border_margin or box.x2 >= frame.width - self.border_margin or box.y2 >= frame.height - self.border_margin:
            return "touches_border"
        grey = cv2.cvtColor(crop(frame.image, box), cv2.COLOR_BGR2GRAY)
        if float(cv2.Laplacian(grey, cv2.CV_64F).var()) < self.min_sharpness:
            return "blurred"
        return None


@dataclass(frozen=True, slots=True)
class ReIdCandidate:
    camera_id: UUID
    track_id: int
    seen_at: datetime
    similarity: float
    band: MatchBand


@dataclass(frozen=True, slots=True)
class ReIdObservation:
    """A person track and its cross-camera candidates. Never an identity; never an alert."""

    campus_id: UUID
    camera_id: UUID
    track_id: int
    frame_index: int
    observed_at: datetime
    band: MatchBand
    candidates: list[ReIdCandidate] = field(default_factory=list)
    review_required: bool = True  # always: decided by a Supervisor, not by this code
    # Sent to the Core API's Person Search intake (decision D-024); not written to JSONL files.
    embedding: list[float] | None = field(default=None, repr=False)
    crop_jpeg: bytes | None = field(default=None, repr=False)
    quality: float | None = None

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("embedding", None)
        data.pop("crop_jpeg", None)
        data["band"] = self.band.value
        for candidate in data["candidates"]:
            candidate["band"] = MatchBand(candidate["band"]).value
        return json.loads(json.dumps(data, default=str))


class ReIdObservationSink(Protocol):
    def record(self, observation: ReIdObservation) -> None: ...

    def close(self) -> None: ...


class MemoryObservationSink:
    def __init__(self) -> None:
        self.observations: list[ReIdObservation] = []

    def record(self, observation: ReIdObservation) -> None:
        self.observations.append(observation)

    def close(self) -> None:
        return None


class JsonlObservationSink:
    """One JSON object per line. Core API has no Re-ID intake yet; this is the hand-over point."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    def record(self, observation: ReIdObservation) -> None:
        self._handle.write(json.dumps(observation.as_dict()) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class ReIdMatcher:
    def __init__(self, gallery: CampusGallery, *, likely_threshold: float, uncertain_threshold: float, window_seconds: float | None = None, top_k: int = 5) -> None:
        if not -1.0 <= uncertain_threshold <= likely_threshold <= 1.0:
            raise ValueError("−1 <= uncertain_threshold <= likely_threshold <= 1")
        self.gallery = gallery
        self.likely = likely_threshold
        self.uncertain = uncertain_threshold
        self.window = window_seconds
        self.top_k = top_k

    def band(self, similarity: float) -> MatchBand:
        if similarity >= self.likely:
            return MatchBand.LIKELY_MATCH
        if similarity >= self.uncertain:
            return MatchBand.UNCERTAIN
        return MatchBand.NO_RELIABLE_MATCH

    def match(self, campus_id: UUID, camera_id: UUID, embedding: np.ndarray, now: datetime) -> tuple[MatchBand, list[ReIdCandidate]]:
        candidates: list[Candidate] = self.gallery.search(campus_id, embedding, now, k=self.top_k, exclude_camera=camera_id, window_seconds=self.window)
        ranked = [ReIdCandidate(c.entry.camera_id, c.entry.track_id, c.entry.seen_at, round(c.similarity, 4), self.band(c.similarity)) for c in candidates]
        overall = ranked[0].band if ranked else MatchBand.NO_RELIABLE_MATCH
        return overall, ranked


@dataclass
class ReIdStats:
    tracks_seen: int = 0
    crops_skipped: dict[str, int] = field(default_factory=dict)
    embeddings: int = 0
    observations: int = 0
    bands: dict[str, int] = field(default_factory=dict)


class ReIdTrackObserver:
    """``TrackObserver``: embeds confirmed person tracks and records cross-camera candidates."""

    def __init__(
        self,
        embedder: Embedder,
        matcher: ReIdMatcher,
        *,
        campus_id: UUID,
        sink: ReIdObservationSink,
        quality: CropQualityFilter | None = None,
        every_n_frames: int = 5,
        person_labels: frozenset[str] = PERSON_LABELS,
        allowed: Callable[[], bool] | None = None,
    ) -> None:
        if every_n_frames < 1:
            raise ValueError("every_n_frames >= 1")
        self.embedder = embedder
        self.matcher = matcher
        self.campus_id = campus_id
        self.sink = sink
        self.quality = quality if quality is not None else CropQualityFilter()
        self.every = every_n_frames
        self.person_labels = person_labels
        self.allowed = allowed  # the camera's AI configuration (decision D-024); None = always
        self.stats = ReIdStats()
        self._running: dict[tuple[UUID, int], tuple[np.ndarray, int]] = {}
        self._first_seen: set[tuple[UUID, int]] = set()

    def observe(self, frame: Frame, tracks: Sequence[Track]) -> None:
        if self.allowed is not None and not self.allowed():
            return
        for track in tracks:
            if not track.confirmed or track.detection.label not in self.person_labels:
                continue
            key = (frame.camera_id, track.track_id)
            if key not in self._first_seen:
                self._first_seen.add(key)
                self.stats.tracks_seen += 1
            if track.hits % self.every:
                continue
            reason = self.quality.reason_to_skip(frame, track)
            if reason:
                self.stats.crops_skipped[reason] = self.stats.crops_skipped.get(reason, 0) + 1
                continue
            vector = self.embedder.embed(frame, track.detection.box)
            self.stats.embeddings += 1
            mean, count = self._running.get(key, (np.zeros_like(vector), 0))
            mean = (mean * count + vector) / (count + 1)
            mean /= max(float(np.linalg.norm(mean)), 1e-12)
            self._running[key] = (mean, count + 1)
            band, candidates = self.matcher.match(self.campus_id, frame.camera_id, mean, frame.timestamp)
            media: dict[str, object] = {}
            if getattr(self.sink, "wants_media", False):
                encoded, jpeg = cv2.imencode(".jpg", crop(frame.image, track.detection.box), [cv2.IMWRITE_JPEG_QUALITY, 85])
                media = {
                    "embedding": [float(v) for v in mean],
                    "crop_jpeg": jpeg.tobytes() if encoded else None,
                    "quality": round(float(track.detection.confidence), 4),
                }
            observation = ReIdObservation(self.campus_id, frame.camera_id, track.track_id, frame.index, frame.timestamp, band, candidates, **media)  # type: ignore[arg-type]
            self.sink.record(observation)
            self.stats.observations += 1
            self.stats.bands[band.value] = self.stats.bands.get(band.value, 0) + 1
            self.matcher.gallery.upsert(self.campus_id, frame.camera_id, track.track_id, mean, frame.timestamp)

    def close(self) -> None:
        self.sink.close()
        self.embedder.close()
