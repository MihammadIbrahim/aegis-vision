"""Person re-identification datasets, the PK sampler and the crop transforms.

Parsers return ``(image path, person id, camera id)`` triples for the standard protocol splits:

* **Market-1501** (``bounding_box_train``, ``query``, ``bounding_box_test``): file names
  ``0002_c1s1_000451_01.jpg`` → pid 2, camera 1. pid ``-1`` (junk) is dropped everywhere; pid ``0``
  (distractors) is dropped from train but kept in the gallery, as the protocol requires.
* **MSMT17** (``train/``, ``test/`` with ``list_train.txt``, ``list_val.txt``, ``list_query.txt``,
  ``list_gallery.txt``): each line ``relative/path.jpg pid``; the camera is the third ``_`` field of the
  file name (``0000_000_01_0303morning_0015_0.jpg`` → camera 1).
* **CUHK03** in the new-protocol release (``cuhk03-np/{detected,labeled}/{bounding_box_train,query,
  bounding_box_test}``): names ``1_001_1_02.png`` → camera pair 1, person 1, camera 1 of the pair. The
  pid is unique across pairs as ``pair * 1000 + person``; the camera id as ``(pair - 1) * 2 + (cam - 1)``.

``holdout_identities`` carves a validation query/gallery out of the **training** identities, so the
published test set is never used to pick epochs or thresholds.

Training transforms: resize to 256×128, flip, pad-and-random-crop, colour jitter, CCTV photometric
augmentation, ImageNet normalisation, Random Erasing. ``PKSampler`` builds batches of P identities ×
K images each, which batch-hard triplet loss needs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset, Sampler
from torchvision import transforms as T

from data_utils.cctv_augment import CctvAugmentConfig, cctv_image_transform
from data_utils.common import stable_split

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
ReidLayout = Literal["market1501", "msmt17", "cuhk03"]


@dataclass(frozen=True)
class ReidItem:
    path: Path
    pid: int
    camid: int


@dataclass
class ReidSplits:
    train: list[ReidItem]
    query: list[ReidItem]
    gallery: list[ReidItem]

    def summary(self) -> dict[str, dict[str, int]]:
        def stats(items: Sequence[ReidItem]) -> dict[str, int]:
            return {"images": len(items), "identities": len({i.pid for i in items}), "cameras": len({i.camid for i in items})}

        return {"train": stats(self.train), "query": stats(self.query), "gallery": stats(self.gallery)}


class ReidFormatError(ValueError):
    pass


_MARKET = re.compile(r"^(-?\d+)_c(\d+)")
_CUHK = re.compile(r"^(\d+)_(\d+)_(\d+)_(\d+)")


def _market_dir(folder: Path, *, drop_junk: bool = True, drop_distractors: bool = False) -> list[ReidItem]:
    if not folder.is_dir():
        raise ReidFormatError(f"{folder} not found")
    items = []
    for path in sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.png")):
        match = _MARKET.match(path.name)
        if not match:
            continue
        pid, camid = int(match.group(1)), int(match.group(2))
        if pid == -1 and drop_junk:
            continue
        if pid == 0 and drop_distractors:
            continue
        items.append(ReidItem(path, pid, camid))
    return items


def parse_market1501(root: Path) -> ReidSplits:
    root = Path(root)
    if (root / "Market-1501-v15.09.15").is_dir():
        root = root / "Market-1501-v15.09.15"
    return ReidSplits(
        train=_market_dir(root / "bounding_box_train", drop_distractors=True),
        query=_market_dir(root / "query"),
        gallery=_market_dir(root / "bounding_box_test"),
    )


def _msmt_list(root: Path, subdir: str, list_file: str) -> list[ReidItem]:
    path = root / list_file
    if not path.is_file():
        raise ReidFormatError(f"{path} not found")
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        relative, pid = parts[0], int(parts[1])
        name_parts = Path(relative).name.split("_")
        camid = int(name_parts[2]) if len(name_parts) > 2 and name_parts[2].isdigit() else 0
        items.append(ReidItem(root / subdir / relative, pid, camid))
    return items


def parse_msmt17(root: Path, *, combine_train_val: bool = True) -> ReidSplits:
    root = Path(root)
    train = _msmt_list(root, "train", "list_train.txt")
    if combine_train_val and (root / "list_val.txt").is_file():
        train += _msmt_list(root, "train", "list_val.txt")
    return ReidSplits(train=train, query=_msmt_list(root, "test", "list_query.txt"), gallery=_msmt_list(root, "test", "list_gallery.txt"))


def _cuhk_dir(folder: Path) -> list[ReidItem]:
    if not folder.is_dir():
        raise ReidFormatError(f"{folder} not found")
    items = []
    for path in sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg")):
        match = _CUHK.match(path.name)
        if not match:
            continue
        pair, person, cam = int(match.group(1)), int(match.group(2)), int(match.group(3))
        items.append(ReidItem(path, pair * 1000 + person, (pair - 1) * 2 + (cam - 1)))
    return items


def parse_cuhk03(root: Path, *, variant: Literal["detected", "labeled"] = "detected") -> ReidSplits:
    root = Path(root)
    base = root / variant if (root / variant).is_dir() else root
    return ReidSplits(train=_cuhk_dir(base / "bounding_box_train"), query=_cuhk_dir(base / "query"), gallery=_cuhk_dir(base / "bounding_box_test"))


def parse_reid_dataset(root: Path, layout: ReidLayout, **kwargs: object) -> ReidSplits:
    if layout == "market1501":
        return parse_market1501(root)
    if layout == "msmt17":
        return parse_msmt17(root, **kwargs)  # type: ignore[arg-type]
    if layout == "cuhk03":
        return parse_cuhk03(root, **kwargs)  # type: ignore[arg-type]
    raise ValueError(f"unknown Re-ID layout {layout!r}")


def holdout_identities(train: Sequence[ReidItem], fraction: float, seed: int) -> tuple[list[ReidItem], list[ReidItem], list[ReidItem]]:
    """Splits training identities into (train, val_query, val_gallery). For each held-out identity,
    the first image of each camera is a query and the rest are gallery, so every query has a
    cross-camera match when the identity was seen by two cameras."""
    if not 0 <= fraction < 1:
        raise ValueError("fraction must be in [0, 1)")
    pids = sorted({i.pid for i in train})
    held = {pid for pid in pids if stable_split(str(pid), (1 - fraction, fraction), seed) == 1}
    kept = [i for i in train if i.pid not in held]
    query, gallery = [], []
    seen: set[tuple[int, int]] = set()
    for item in sorted((i for i in train if i.pid in held), key=lambda i: str(i.path)):
        key = (item.pid, item.camid)
        if key not in seen:
            seen.add(key)
            query.append(item)
        else:
            gallery.append(item)
    return kept, query, gallery


def relabel(items: Sequence[ReidItem]) -> tuple[list[ReidItem], dict[int, int]]:
    """Maps person ids to 0..N-1 for the classifier head."""
    mapping = {pid: index for index, pid in enumerate(sorted({i.pid for i in items}))}
    return [ReidItem(i.path, mapping[i.pid], i.camid) for i in items], mapping


# --- Transforms and dataset --------------------------------------------------------------------------------------


class _Albumentations:
    def __init__(self, cfg: CctvAugmentConfig) -> None:
        self._tf = cctv_image_transform(cfg, geometric=False)

    def __call__(self, image: Image.Image) -> Image.Image:
        return Image.fromarray(self._tf(image=np.asarray(image))["image"])


def train_transform(height: int = 256, width: int = 128, *, cctv: CctvAugmentConfig | None = CctvAugmentConfig(), random_erasing: float = 0.5) -> T.Compose:
    steps: list[object] = [
        T.Resize((height, width), interpolation=T.InterpolationMode.BILINEAR),
        T.RandomHorizontalFlip(),
        T.Pad(10),
        T.RandomCrop((height, width)),
        T.ColorJitter(brightness=0.2, contrast=0.15, saturation=0.1, hue=0.0),
    ]
    if cctv is not None:
        steps.append(_Albumentations(cctv))
    steps += [T.ToTensor(), T.Normalize(IMAGENET_MEAN, IMAGENET_STD), T.RandomErasing(p=random_erasing, value="random")]
    return T.Compose(steps)  # type: ignore[arg-type]


def eval_transform(height: int = 256, width: int = 128) -> T.Compose:
    return T.Compose([T.Resize((height, width)), T.ToTensor(), T.Normalize(IMAGENET_MEAN, IMAGENET_STD)])


class ReidDataset(Dataset[tuple[torch.Tensor, int, int]]):
    def __init__(self, items: Sequence[ReidItem], transform: T.Compose) -> None:
        self.items = list(items)
        self.transform = transform

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int, int]:
        item = self.items[index]
        with Image.open(item.path) as image:
            tensor = self.transform(image.convert("RGB"))
        return tensor, item.pid, item.camid


class PKSampler(Sampler[int]):
    """Each batch holds ``p`` identities with ``k`` images each (identities with fewer images are
    sampled with replacement). One epoch visits every identity about once; the order changes every
    epoch from the seed, so runs are repeatable."""

    def __init__(self, pids: Sequence[int], *, p: int, k: int, seed: int = 0) -> None:
        if p < 2 or k < 2:
            raise ValueError("PK sampling needs p >= 2 identities and k >= 2 images each")
        self._by_pid: dict[int, list[int]] = {}
        for index, pid in enumerate(pids):
            self._by_pid.setdefault(pid, []).append(index)
        if len(self._by_pid) < p:
            raise ValueError(f"only {len(self._by_pid)} identities, need at least p={p}")
        self.p, self.k, self._seed, self._epoch = p, k, seed, 0

    def set_epoch(self, epoch: int) -> None:
        self._epoch = epoch

    @property
    def batch_size(self) -> int:
        return self.p * self.k

    def __len__(self) -> int:
        batches = len(self._by_pid) // self.p
        return batches * self.p * self.k

    def __iter__(self) -> Iterator[int]:
        rng = np.random.default_rng(self._seed * 100_003 + self._epoch)
        pids = list(self._by_pid)
        rng.shuffle(pids)
        for start in range(0, len(pids) - self.p + 1, self.p):
            for pid in pids[start : start + self.p]:
                pool = self._by_pid[pid]
                chosen = rng.choice(pool, size=self.k, replace=len(pool) < self.k)
                yield from (int(i) for i in chosen)
