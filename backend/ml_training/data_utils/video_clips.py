"""Video clips for fight / violence classification.

* ``VideoClipReader``: decodes a fixed number of frames from a video at a target frame rate.
  Uses ``decord`` (fast, random access) when installed, otherwise OpenCV. Frame-rate
  normalisation: a 30 fps and a 15 fps video both yield clips that cover the same wall-clock span
  at ``target_fps``.
* ``list_videos``: the three supported dataset layouts →  (video path, label, split) items.
* ``ClipDataset``: sliding windows over each video. Training draws one random window per video per
  epoch (plus CCTV augmentation applied consistently across the clip); evaluation enumerates every
  window, so clip-level and video-level metrics are both possible.

Tensors are ``C × T × H × W`` float32, normalised with the Kinetics-400 mean/std used by the
torchvision video models (``models/fight_models.py``).

Dataset layouts (see ``datasets/README.md`` for sources and licences):
    rwf2000:  <root>/{train,val}/{Fight,NonFight}/*.avi            (the authors' split is kept)
    rlvs:     <root>/{Violence,NonViolence}/*.mp4                   (no official split: hashed split)
    ucfcrime: <root>/<Category>/*.mp4; Fighting + Assault (+ Abuse, Robbery if enabled) → violence,
              Normal_Videos_* → non-violence; other anomaly classes are excluded by default
    folders:  <root>/{train,val,test}/{<positive>,<negative>}/*     (any custom dataset)
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from data_utils.cctv_augment import CctvAugmentConfig, CctvClipAugmenter
from data_utils.common import Split, stable_split

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".mpg", ".mpeg"}
KINETICS_MEAN = (0.43216, 0.394666, 0.37645)
KINETICS_STD = (0.22803, 0.22145, 0.216989)
Layout = Literal["rwf2000", "rlvs", "ucfcrime", "folders"]

try:  # decord is optional at import time so CPU-only tests run everywhere
    import decord

    decord.bridge.set_bridge("native")
    HAVE_DECORD = True
except Exception:  # pragma: no cover - depends on the environment
    HAVE_DECORD = False


class VideoReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoMeta:
    frames: int
    fps: float
    width: int
    height: int

    @property
    def seconds(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


class VideoClipReader:
    """Reads clips as uint8 arrays (T, H, W, 3) in RGB."""

    def __init__(self, *, backend: Literal["auto", "decord", "opencv"] = "auto", num_threads: int = 1) -> None:
        if backend == "decord" and not HAVE_DECORD:
            raise VideoReadError("decord was requested but is not installed")
        self.backend = "decord" if backend == "auto" and HAVE_DECORD else ("opencv" if backend == "auto" else backend)
        self._threads = num_threads

    def meta(self, path: Path) -> VideoMeta:
        if self.backend == "decord":
            reader = decord.VideoReader(str(path), num_threads=self._threads)
            if len(reader) == 0:
                raise VideoReadError(f"{path.name}: no frames")
            first = reader[0].asnumpy()
            return VideoMeta(len(reader), float(reader.get_avg_fps()) or 25.0, int(first.shape[1]), int(first.shape[0]))
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise VideoReadError(f"{path.name}: cannot open")
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = float(capture.get(cv2.CAP_PROP_FPS)) or 25.0
            width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        finally:
            capture.release()
        if frames <= 0:
            raise VideoReadError(f"{path.name}: no frames")
        return VideoMeta(frames, fps, width, height)

    @staticmethod
    def frame_indices(meta: VideoMeta, start_frame: int, clip_len: int, target_fps: float) -> list[int]:
        """``clip_len`` indices from ``start_frame`` spaced for ``target_fps``; indices past the end
        repeat the last frame (short videos are padded, never skipped)."""
        step = max(1.0, meta.fps / target_fps) if target_fps > 0 else 1.0
        indices = [min(meta.frames - 1, int(round(start_frame + i * step))) for i in range(clip_len)]
        return indices

    def read_frames(self, path: Path, indices: Sequence[int]) -> np.ndarray:
        if self.backend == "decord":
            reader = decord.VideoReader(str(path), num_threads=self._threads)
            batch = reader.get_batch(list(indices)).asnumpy()
            return np.ascontiguousarray(batch)
        capture = cv2.VideoCapture(str(path))
        try:
            if not capture.isOpened():
                raise VideoReadError(f"{path.name}: cannot open")
            wanted = sorted(set(indices))
            frames: dict[int, np.ndarray] = {}
            position = 0
            last: np.ndarray | None = None
            target = 0
            while target < len(wanted):
                ok, image = capture.read()
                if not ok:
                    break
                if position == wanted[target]:
                    frames[position] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                    target += 1
                last = image
                position += 1
            if last is None:
                raise VideoReadError(f"{path.name}: no decodable frames")
            fallback = cv2.cvtColor(last, cv2.COLOR_BGR2RGB)
            return np.stack([frames.get(i, fallback) for i in indices])
        finally:
            capture.release()

    def read_clip(self, path: Path, *, start_frame: int, clip_len: int, target_fps: float) -> np.ndarray:
        meta = self.meta(path)
        return self.read_frames(path, self.frame_indices(meta, start_frame, clip_len, target_fps))


# --- Dataset listing --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoItem:
    path: Path
    label: int  # 1 = violence, 0 = normal
    split: Split
    source: str = ""


UCF_VIOLENCE = ("Fighting", "Assault")
UCF_VIOLENCE_EXTENDED = ("Fighting", "Assault", "Abuse", "Robbery")
UCF_NORMAL_PREFIX = "Normal"


def _videos(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES)


def list_videos(
    root: Path,
    layout: Layout,
    *,
    ratios: Sequence[float] = (0.7, 0.15, 0.15),
    seed: int = 42,
    ucf_extended: bool = False,
    positive_names: Sequence[str] = ("Fight", "Violence", "fight", "violence", "positive"),
) -> list[VideoItem]:
    root = Path(root)
    items: list[VideoItem] = []
    if layout == "rwf2000":
        for split_dir, split in (("train", "train"), ("val", "val"), ("test", "test")):
            for label_name, label in (("Fight", 1), ("NonFight", 0)):
                folder = root / split_dir / label_name
                if folder.is_dir():
                    items += [VideoItem(p, label, split, "rwf2000") for p in _videos(folder)]  # type: ignore[arg-type]
        if not any(i.split == "test" for i in items):
            # RWF-2000 publishes train/val only: the published val is our held-out TEST; model
            # selection uses a hashed slice of train, so test stays untouched.
            train = [i for i in items if i.split == "train"]
            val = [VideoItem(i.path, i.label, "test", i.source) for i in items if i.split == "val"]
            carved = [
                VideoItem(i.path, i.label, "val" if stable_split(i.path.name, (0.9, 0.1), seed) == 1 else "train", i.source)
                for i in train
            ]
            for label in (0, 1):  # tiny datasets: keep at least one validation video per class
                if not any(i.split == "val" and i.label == label for i in carved):
                    first = next((i for i in sorted(carved, key=lambda v: v.path.name) if i.split == "train" and i.label == label), None)
                    if first is not None:
                        carved[carved.index(first)] = VideoItem(first.path, first.label, "val", first.source)
            items = carved + val
    elif layout == "rlvs":
        for label_name, label in (("Violence", 1), ("NonViolence", 0)):
            folder = root / label_name
            if not folder.is_dir():
                raise VideoReadError(f"{folder} not found (RLVS layout: Violence/ and NonViolence/)")
            for p in _videos(folder):
                split = ("train", "val", "test")[stable_split(p.name, ratios, seed)]
                items.append(VideoItem(p, label, split, "rlvs"))  # type: ignore[arg-type]
    elif layout == "ucfcrime":
        violence = UCF_VIOLENCE_EXTENDED if ucf_extended else UCF_VIOLENCE
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if folder.name in violence:
                label = 1
            elif folder.name.startswith(UCF_NORMAL_PREFIX):
                label = 0
            else:
                continue
            for p in _videos(folder):
                split = ("train", "val", "test")[stable_split(p.name, ratios, seed)]
                items.append(VideoItem(p, label, split, "ucfcrime"))  # type: ignore[arg-type]
    elif layout == "folders":
        for split in ("train", "val", "test"):
            split_dir = root / split
            if not split_dir.is_dir():
                continue
            for class_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
                label = 1 if class_dir.name in positive_names else 0
                items += [VideoItem(p, label, split, "folders") for p in _videos(class_dir)]  # type: ignore[arg-type]
    else:
        raise ValueError(f"unknown layout {layout!r}")
    if not items:
        raise VideoReadError(f"no videos found under {root} for layout {layout}")
    return items


def split_counts(items: Sequence[VideoItem]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = out.setdefault(item.split, {"violence": 0, "normal": 0})
        bucket["violence" if item.label else "normal"] += 1
    return out


# --- Dataset ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClipSpec:
    clip_len: int = 16
    target_fps: float = 8.0
    size: int = 160  # frames are resized so the short side is ``resize_short`` and centre/random-cropped to size×size
    resize_short: int = 182
    windows_per_video_eval: int = 5  # evaluation: evenly spaced windows (all windows if fewer fit)
    clip_stride_seconds: float = 1.0  # minimum spacing between evaluation windows


@dataclass(frozen=True)
class ClipSample:
    video_index: int
    start_frame: int


def _resize_short(frames: np.ndarray, short: int) -> np.ndarray:
    t, h, w, _ = frames.shape
    scale = short / min(h, w)
    new_w, new_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    if (new_w, new_h) == (w, h):
        return frames
    return np.stack([cv2.resize(f, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR) for f in frames])


def _crop(frames: np.ndarray, size: int, *, random_position: bool, rng: random.Random) -> np.ndarray:
    _, h, w, _ = frames.shape
    size = min(size, h, w)
    if random_position:
        y, x = rng.randint(0, h - size), rng.randint(0, w - size)
    else:
        y, x = (h - size) // 2, (w - size) // 2
    return frames[:, y : y + size, x : x + size]


def to_tensor(frames: np.ndarray, mean: Sequence[float] = KINETICS_MEAN, std: Sequence[float] = KINETICS_STD) -> torch.Tensor:
    """(T, H, W, 3) uint8 RGB → (3, T, H, W) float32 normalised."""
    array = frames.astype(np.float32) / 255.0
    array = (array - np.asarray(mean, np.float32)) / np.asarray(std, np.float32)
    return torch.from_numpy(np.ascontiguousarray(array.transpose(3, 0, 1, 2)))


class ClipDataset(Dataset[tuple[torch.Tensor, int, int]]):
    """Returns ``(clip tensor, label, video_index)``.

    * ``train=True``: one random window per video per epoch, random crop, horizontal flip and CCTV
      augmentation with the same parameters across the clip.
    * ``train=False``: a fixed list of evenly spaced windows per video (``windows_per_video_eval``),
      centre crop, no augmentation. ``video_of`` maps samples back to videos for video-level scores.
    """

    def __init__(
        self,
        items: Sequence[VideoItem],
        spec: ClipSpec,
        *,
        train: bool,
        reader: VideoClipReader | None = None,
        augment: CctvAugmentConfig | None = None,
        seed: int = 0,
        meta_cache: dict[Path, VideoMeta] | None = None,
    ) -> None:
        self.items = list(items)
        self.spec = spec
        self.train = train
        self.reader = reader or VideoClipReader()
        self._augment = CctvClipAugmenter(augment) if (train and augment is not None) else None
        self._seed = seed
        self._epoch = 0
        self._meta = meta_cache if meta_cache is not None else {}
        self.samples: list[ClipSample] = []
        self._build_samples()

    def set_epoch(self, epoch: int) -> None:
        self._epoch = epoch

    def meta(self, index: int) -> VideoMeta:
        path = self.items[index].path
        if path not in self._meta:
            self._meta[path] = self.reader.meta(path)
        return self._meta[path]

    def _span_frames(self, meta: VideoMeta) -> int:
        """Source frames covered by one clip."""
        step = max(1.0, meta.fps / self.spec.target_fps)
        return int(math.ceil((self.spec.clip_len - 1) * step)) + 1

    def _build_samples(self) -> None:
        self.samples = []
        for video_index in range(len(self.items)):
            if self.train:
                self.samples.append(ClipSample(video_index, -1))  # start chosen per epoch
                continue
            meta = self.meta(video_index)
            span = self._span_frames(meta)
            last_start = max(0, meta.frames - span)
            stride_frames = max(1, int(round(self.spec.clip_stride_seconds * meta.fps)))
            possible = list(range(0, last_start + 1, stride_frames)) or [0]
            if len(possible) > self.spec.windows_per_video_eval:
                picks = np.linspace(0, len(possible) - 1, self.spec.windows_per_video_eval).round().astype(int)
                possible = [possible[i] for i in picks]
            self.samples += [ClipSample(video_index, start) for start in possible]

    @property
    def video_of(self) -> list[int]:
        return [s.video_index for s in self.samples]

    @property
    def labels(self) -> list[int]:
        return [self.items[s.video_index].label for s in self.samples]

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, int]:
        sample = self.samples[index]
        item = self.items[sample.video_index]
        meta = self.meta(sample.video_index)
        rng = random.Random(self._seed * 1_000_003 + self._epoch * 7919 + index)
        start = sample.start_frame
        if start < 0:
            start = rng.randint(0, max(0, meta.frames - self._span_frames(meta)))
        indices = self.reader.frame_indices(meta, start, self.spec.clip_len, self.spec.target_fps)
        frames = self.reader.read_frames(item.path, indices)
        frames = _resize_short(frames, self.spec.resize_short)
        frames = _crop(frames, self.spec.size, random_position=self.train, rng=rng)
        if self.train:
            if rng.random() < 0.5:
                frames = np.ascontiguousarray(frames[:, :, ::-1])
            if self._augment is not None:
                frames = self._augment(frames)
        return to_tensor(frames), item.label, sample.video_index


def collate_clips(batch: Sequence[tuple[torch.Tensor, int, int]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    clips = torch.stack([b[0] for b in batch])
    labels = torch.tensor([b[1] for b in batch], dtype=torch.long)
    videos = torch.tensor([b[2] for b in batch], dtype=torch.long)
    return clips, labels, videos


def make_balanced_sampler(labels: Sequence[int], seed: int) -> torch.utils.data.WeightedRandomSampler:
    """Draws violence and normal clips equally often; RLVS and UCF-Crime subsets are imbalanced."""
    counts = np.bincount(np.asarray(labels), minlength=2).astype(np.float64)
    weights = torch.tensor([1.0 / counts[label] for label in labels], dtype=torch.double)
    generator = torch.Generator().manual_seed(seed)
    return torch.utils.data.WeightedRandomSampler(weights, num_samples=len(labels), replacement=True, generator=generator)


FrameReader = Callable[[Path, Sequence[int]], np.ndarray]
