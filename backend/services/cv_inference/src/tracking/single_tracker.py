"""Single-camera tracking: temporary track IDs for detections that persist across frames.

* ``IouTracker``: simple greedy matching by bounding-box overlap (IoU). Kept for the synthetic test
  detector and existing tests.
* ``ByteTracker``: ByteTrack-style association with Kalman motion prediction, used with the real
  person detector (decision D-015).

Both keep every camera's state separate, and their track IDs are never identities (requirements
4 B). The same number on two cameras means nothing; linking people across cameras is the future
Re-ID component's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import numpy as np
from scipy.optimize import linear_sum_assignment

from src.schemas import BoundingBox, Detection, Track


class Tracker(Protocol):
    def update(self, camera_id: UUID, frame_index: int, detections: list[Detection]) -> list[Track]: ...

    def reset(self) -> None: ...


@dataclass
class _State:
    track_id: int
    detection: Detection
    hits: int
    first_frame_index: int
    last_frame_index: int


class IouTracker:
    def __init__(self, *, iou_threshold: float = 0.3, max_missed_frames: int = 10) -> None:
        if not 0.0 < iou_threshold <= 1.0:
            raise ValueError("iou_threshold must be in (0, 1]")
        self._iou = iou_threshold
        self._max_missed = max_missed_frames
        self._tracks: dict[UUID, list[_State]] = {}
        self._next_id = 1

    def update(self, camera_id: UUID, frame_index: int, detections: list[Detection]) -> list[Track]:
        live = [t for t in self._tracks.get(camera_id, []) if frame_index - t.last_frame_index <= self._max_missed]
        pairs = sorted(
            (
                (t.detection.box.iou(d.box), ti, di)
                for ti, t in enumerate(live)
                for di, d in enumerate(detections)
                if t.detection.label == d.label
            ),
            reverse=True,
        )
        used_tracks: set[int] = set()
        assigned: dict[int, _State] = {}
        for overlap, ti, di in pairs:
            if overlap < self._iou or ti in used_tracks or di in assigned:
                continue
            state = live[ti]
            state.detection, state.hits, state.last_frame_index = detections[di], state.hits + 1, frame_index
            used_tracks.add(ti)
            assigned[di] = state
        for di, detection in enumerate(detections):
            if di not in assigned:
                state = _State(self._next_id, detection, 1, frame_index, frame_index)
                self._next_id += 1
                live.append(state)
                assigned[di] = state
        self._tracks[camera_id] = live
        return [
            Track(
                track_id=s.track_id,
                detection=s.detection,
                hits=s.hits,
                first_frame_index=s.first_frame_index,
                camera_id=camera_id,
            )
            for s in (assigned[di] for di in range(len(detections)))
        ]

    def reset(self) -> None:
        self._tracks.clear()


# --- ByteTrack-style tracker ----------------------------------------------------------------------
#
# Follows the association method of ByteTrack (Zhang et al., "ByteTrack: Multi-Object Tracking by
# Associating Every Detection Box", ECCV 2022; reference code MIT-licensed), written here because
# ``supervision`` 0.31 no longer ships a tracker. Motion is a constant-velocity Kalman filter on
# (centre x, centre y, aspect ratio, height), as in SORT/DeepSORT/ByteTrack.


class _Kalman:
    _POS, _VEL = 1.0 / 20, 1.0 / 160

    def __init__(self) -> None:
        self._F = np.eye(8)
        for i in range(4):
            self._F[i, 4 + i] = 1.0
        self._H = np.eye(4, 8)

    def initiate(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = z[3]
        std = [2 * self._POS * h, 2 * self._POS * h, 1e-2, 2 * self._POS * h,
               10 * self._VEL * h, 10 * self._VEL * h, 1e-5, 10 * self._VEL * h]  # fmt: skip
        return np.r_[z, np.zeros(4)], np.diag(np.square(std))

    def predict(self, mean: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        std = [self._POS * h, self._POS * h, 1e-2, self._POS * h, self._VEL * h, self._VEL * h, 1e-5, self._VEL * h]
        return self._F @ mean, self._F @ cov @ self._F.T + np.diag(np.square(std))

    def update(self, mean: np.ndarray, cov: np.ndarray, z: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = mean[3]
        r = np.diag(np.square([self._POS * h, self._POS * h, 1e-1, self._POS * h]))
        s = self._H @ cov @ self._H.T + r
        gain = cov @ self._H.T @ np.linalg.inv(s)
        return mean + gain @ (z - self._H @ mean), cov - gain @ s @ gain.T


def _xyah(box: BoundingBox) -> np.ndarray:
    w, h = box.x2 - box.x1, box.y2 - box.y1
    return np.array([box.x1 + w / 2, box.y1 + h / 2, w / h, h], dtype=float)


def _box(state: np.ndarray) -> BoundingBox | None:
    cx, cy, a, h = state[:4]
    w = a * h
    if not (np.isfinite([cx, cy, w, h]).all() and w > 0 and h > 0):
        return None
    return BoundingBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


@dataclass
class _ByteState:
    track_id: int
    label: str
    mean: np.ndarray
    cov: np.ndarray
    detection: Detection
    hits: int
    first_frame_index: int
    last_frame_index: int
    confirmed: bool
    lost: bool = False


class ByteTracker:
    """Two-stage association per camera and per label:

    1. confident detections (``>= high_threshold``) with all tracks, including recently lost ones;
    2. low-confidence detections (``low_threshold``–``high_threshold``) with the tracks still unmatched.
       They can keep a track alive through partial occlusion but never start one;
    3. remaining confident detections with tentative tracks (seen once), which become confirmed.

    Leftover confident detections at or above ``new_track_threshold`` start tentative tracks. A track
    unmatched for more than ``max_lost_frames`` sampled frames is removed. Matching minimises
    1 - IoU (Hungarian assignment) between Kalman-predicted and detected boxes.
    """

    def __init__(
        self,
        *,
        high_threshold: float = 0.5,
        low_threshold: float = 0.1,
        new_track_threshold: float = 0.6,
        first_match_iou: float = 0.2,
        second_match_iou: float = 0.5,
        tentative_match_iou: float = 0.3,
        max_lost_frames: int = 30,
    ) -> None:
        if not 0.0 <= low_threshold < high_threshold <= 1.0 or new_track_threshold < high_threshold:
            raise ValueError("need 0 <= low_threshold < high_threshold <= new_track_threshold <= 1")
        if max_lost_frames < 0:
            raise ValueError("max_lost_frames must be non-negative")
        self._high, self._low, self._new = high_threshold, low_threshold, new_track_threshold
        self._iou = (first_match_iou, second_match_iou, tentative_match_iou)
        self._max_lost = max_lost_frames
        self._kalman = _Kalman()
        self._tracks: dict[UUID, list[_ByteState]] = {}
        self._next_id = 1

    def update(self, camera_id: UUID, frame_index: int, detections: list[Detection]) -> list[Track]:
        tracks = self._tracks.setdefault(camera_id, [])
        for t in tracks:
            t.mean, t.cov = self._kalman.predict(t.mean, t.cov)
        matched: dict[int, _ByteState] = {}
        for label in sorted({d.label for d in detections} | {t.label for t in tracks}):
            self._associate(camera_id, frame_index, label, detections, matched)
        for t in tracks:
            if t.last_frame_index != frame_index:
                t.lost = True
        self._tracks[camera_id] = [
            t
            for t in self._tracks[camera_id]
            if (t.confirmed or t.last_frame_index == frame_index) and frame_index - t.last_frame_index <= self._max_lost
        ]
        return [
            Track(
                track_id=s.track_id,
                detection=s.detection,
                hits=s.hits,
                first_frame_index=s.first_frame_index,
                camera_id=camera_id,
                confirmed=s.confirmed,
            )
            for _, s in sorted(matched.items())
        ]

    def _associate(
        self, camera_id: UUID, frame_index: int, label: str, detections: list[Detection], matched: dict[int, _ByteState]
    ) -> None:
        own = [i for i, d in enumerate(detections) if d.label == label]
        high = [i for i in own if detections[i].confidence >= self._high]
        low = [i for i in own if self._low <= detections[i].confidence < self._high]
        tracks = [t for t in self._tracks[camera_id] if t.label == label]
        confirmed = [t for t in tracks if t.confirmed]
        tentative = [t for t in tracks if not t.confirmed]

        rest_tracks, rest_high = self._match(confirmed, high, detections, self._iou[0], frame_index, matched)
        still_active = [t for t in rest_tracks if not t.lost]
        self._match(still_active, low, detections, self._iou[1], frame_index, matched)
        _, rest_high = self._match(tentative, rest_high, detections, self._iou[2], frame_index, matched)
        for i in rest_high:
            if detections[i].confidence < self._new:
                continue
            mean, cov = self._kalman.initiate(_xyah(detections[i].box))
            state = _ByteState(self._next_id, label, mean, cov, detections[i], 1, frame_index, frame_index, False)
            self._next_id += 1
            self._tracks[camera_id].append(state)
            matched[i] = state

    def _match(
        self,
        tracks: list[_ByteState],
        indices: list[int],
        detections: list[Detection],
        min_iou: float,
        frame_index: int,
        matched: dict[int, _ByteState],
    ) -> tuple[list[_ByteState], list[int]]:
        """Assigns detections to tracks; returns the unmatched tracks and detection indices."""
        if not tracks or not indices:
            return tracks, indices
        predicted = [_box(t.mean) for t in tracks]
        iou = np.array([[p.iou(detections[i].box) if p else 0.0 for i in indices] for p in predicted])
        rows, cols = linear_sum_assignment(1.0 - iou)
        used_tracks, used_dets = set(), set()
        for r, c in zip(rows, cols, strict=True):
            if iou[r, c] < min_iou:
                continue
            track, index = tracks[r], indices[c]
            detection = detections[index]
            track.mean, track.cov = self._kalman.update(track.mean, track.cov, _xyah(detection.box))
            track.detection, track.hits, track.last_frame_index = detection, track.hits + 1, frame_index
            track.lost = False
            if track.hits >= 2:
                track.confirmed = True
            matched[index] = track
            used_tracks.add(r)
            used_dets.add(c)
        return (
            [t for k, t in enumerate(tracks) if k not in used_tracks],
            [i for k, i in enumerate(indices) if k not in used_dets],
        )

    def reset(self) -> None:
        self._tracks.clear()
