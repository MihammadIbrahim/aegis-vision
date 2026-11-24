"""The Re-ID gallery: recent person embeddings per campus, searched by cosine similarity.

* One index per **campus**: embeddings never cross campus or organization boundaries
  (requirements 4 C, 21). Camera ids are kept with each entry so same-camera entries can be excluded.
* Entries expire after ``window_seconds``: Re-ID matches recent appearances on other cameras, not a
  biometric archive. Nothing is written to disk by this module.
* Backend: Faiss ``IndexIDMap2(IndexFlatIP)`` (exact inner product on unit vectors = cosine) when the
  ``faiss`` package is installed, otherwise an exact NumPy search with identical results. Faiss is
  needed only when a campus gallery grows into the tens of thousands.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

try:
    import faiss

    HAVE_FAISS = True
except Exception:  # pragma: no cover - depends on the environment
    faiss = None
    HAVE_FAISS = False


@dataclass(frozen=True, slots=True)
class GalleryEntry:
    entry_id: int
    campus_id: UUID
    camera_id: UUID
    track_id: int
    seen_at: datetime
    embedding: np.ndarray


@dataclass(frozen=True, slots=True)
class Candidate:
    entry: GalleryEntry
    similarity: float  # cosine, −1..1


class _Index:
    """Exact inner-product search over unit vectors with integer ids (Faiss or NumPy)."""

    def __init__(self, dimension: int, *, use_faiss: bool) -> None:
        self.dimension = dimension
        self._faiss = use_faiss and HAVE_FAISS
        if self._faiss:
            self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
        else:
            self._ids = np.zeros((0,), dtype=np.int64)
            self._vectors = np.zeros((0, dimension), dtype=np.float32)

    def add(self, entry_id: int, vector: np.ndarray) -> None:
        v = np.ascontiguousarray(vector.reshape(1, -1), dtype=np.float32)
        if self._faiss:
            self._index.add_with_ids(v, np.array([entry_id], dtype=np.int64))
        else:
            self._ids = np.append(self._ids, entry_id)
            self._vectors = np.vstack([self._vectors, v])

    def remove(self, entry_ids: Sequence[int]) -> None:
        if not entry_ids:
            return
        if self._faiss:
            self._index.remove_ids(np.asarray(entry_ids, dtype=np.int64))
        else:
            keep = ~np.isin(self._ids, np.asarray(entry_ids, dtype=np.int64))
            self._ids, self._vectors = self._ids[keep], self._vectors[keep]

    def search(self, vector: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.size == 0 or k < 1:
            return []
        q = np.ascontiguousarray(vector.reshape(1, -1), dtype=np.float32)
        k = min(k, self.size)
        if self._faiss:
            scores, ids = self._index.search(q, k)
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0], strict=True) if i >= 0]
        scores = self._vectors @ q[0]
        order = np.argsort(-scores)[:k]
        return [(int(self._ids[i]), float(scores[i])) for i in order]

    @property
    def size(self) -> int:
        return int(self._index.ntotal) if self._faiss else int(len(self._ids))


class CampusGallery:
    """Embeddings of recently seen people, scoped per campus and limited to a time window."""

    def __init__(self, dimension: int, *, window_seconds: float = 600.0, max_entries_per_campus: int = 20_000, use_faiss: bool = True) -> None:
        if dimension < 1 or window_seconds <= 0 or max_entries_per_campus < 1:
            raise ValueError("dimension >= 1, window_seconds > 0, max_entries_per_campus >= 1")
        self.dimension = dimension
        self.window_seconds = window_seconds
        self.max_entries = max_entries_per_campus
        self._use_faiss = use_faiss
        self._indexes: dict[UUID, _Index] = {}
        self._entries: dict[int, GalleryEntry] = {}
        self._by_track: dict[tuple[UUID, UUID, int], int] = {}
        self._next_id = 1

    @property
    def backend(self) -> str:
        return "faiss" if (self._use_faiss and HAVE_FAISS) else "numpy"

    def size(self, campus_id: UUID | None = None) -> int:
        if campus_id is not None:
            return self._indexes[campus_id].size if campus_id in self._indexes else 0
        return len(self._entries)

    def _check(self, embedding: np.ndarray) -> np.ndarray:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.dimension or not np.isfinite(vector).all():
            raise ValueError(f"embedding must be a finite vector of length {self.dimension}")
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector

    def upsert(self, campus_id: UUID, camera_id: UUID, track_id: int, embedding: np.ndarray, seen_at: datetime) -> GalleryEntry:
        """Stores (or replaces) the embedding of one camera-local track."""
        vector = self._check(embedding)
        index = self._indexes.setdefault(campus_id, _Index(self.dimension, use_faiss=self._use_faiss))
        key = (campus_id, camera_id, track_id)
        if key in self._by_track:
            old = self._by_track.pop(key)
            index.remove([old])
            self._entries.pop(old, None)
        entry = GalleryEntry(self._next_id, campus_id, camera_id, track_id, seen_at, vector)
        self._next_id += 1
        index.add(entry.entry_id, vector)
        self._entries[entry.entry_id] = entry
        self._by_track[key] = entry.entry_id
        self.expire(campus_id, seen_at)
        return entry

    def expire(self, campus_id: UUID, now: datetime) -> int:
        """Drops entries older than the window (and the oldest beyond ``max_entries``)."""
        index = self._indexes.get(campus_id)
        if index is None:
            return 0
        mine = [e for e in self._entries.values() if e.campus_id == campus_id]
        stale = [e for e in mine if (now - e.seen_at).total_seconds() > self.window_seconds]
        fresh = sorted((e for e in mine if e not in stale), key=lambda e: e.seen_at)
        if len(fresh) > self.max_entries:
            stale += fresh[: len(fresh) - self.max_entries]
        if stale:
            index.remove([e.entry_id for e in stale])
            for entry in stale:
                self._entries.pop(entry.entry_id, None)
                self._by_track.pop((entry.campus_id, entry.camera_id, entry.track_id), None)
        return len(stale)

    def search(self, campus_id: UUID, embedding: np.ndarray, now: datetime, *, k: int = 5, exclude_camera: UUID | None = None, window_seconds: float | None = None) -> list[Candidate]:
        """Nearest recent entries of the same campus, newest window first, excluding one camera."""
        index = self._indexes.get(campus_id)
        if index is None or index.size == 0:
            return []
        vector = self._check(embedding)
        window = self.window_seconds if window_seconds is None else window_seconds
        raw = index.search(vector, k + 50)  # over-fetch, then filter by camera and time
        out: list[Candidate] = []
        for entry_id, score in raw:
            entry = self._entries.get(entry_id)
            if entry is None or entry.camera_id == exclude_camera:
                continue
            age = (now - entry.seen_at).total_seconds()
            if age < 0 or age > window:
                continue
            out.append(Candidate(entry, score))
            if len(out) == k:
                break
        return out

    def clear(self, campus_id: UUID | None = None) -> None:
        if campus_id is None:
            self._indexes.clear()
            self._entries.clear()
            self._by_track.clear()
            return
        self._indexes.pop(campus_id, None)
        for entry_id in [i for i, e in self._entries.items() if e.campus_id == campus_id]:
            entry = self._entries.pop(entry_id)
            self._by_track.pop((entry.campus_id, entry.camera_id, entry.track_id), None)
