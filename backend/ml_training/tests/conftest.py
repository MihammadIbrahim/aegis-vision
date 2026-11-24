"""Synthetic fixtures: tiny datasets written at test time (no downloads, no real footage)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest


def _image(path: Path, size: tuple[int, int] = (96, 64), seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    cv2.imwrite(str(path), image)


@pytest.fixture
def voc_dataset(tmp_path: Path) -> Path:
    """Pascal VOC: 6 images; pistol / knife / wallet labels; one image without objects."""
    root = tmp_path / "voc"
    (root / "images").mkdir(parents=True)
    (root / "annotations").mkdir()
    objects = {
        "a": [("pistol", 10, 10, 40, 30)],
        "b": [("knife", 5, 5, 20, 40), ("wallet", 50, 20, 70, 50)],
        "c": [("pistol", 30, 20, 60, 50), ("pistol", 0, 0, 15, 15)],
        "d": [],
        "e": [("wallet", 10, 10, 30, 30)],
        "f": [("knife", 20, 20, 50, 60)],
    }
    for index, (name, boxes) in enumerate(objects.items()):
        _image(root / "images" / f"{name}.jpg", seed=index)
        items = "".join(
            f"<object><name>{label}</name><bndbox><xmin>{x1}</xmin><ymin>{y1}</ymin><xmax>{x2}</xmax><ymax>{y2}</ymax></bndbox></object>"
            for label, x1, y1, x2, y2 in boxes
        )
        (root / "annotations" / f"{name}.xml").write_text(
            f"<annotation><filename>{name}.jpg</filename><size><width>96</width><height>64</height><depth>3</depth></size>{items}</annotation>",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def coco_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "coco"
    (root / "images").mkdir(parents=True)
    for i in range(3):
        _image(root / "images" / f"img{i}.jpg", seed=i)
    data = {
        "images": [{"id": i, "file_name": f"img{i}.jpg", "width": 96, "height": 64} for i in range(3)],
        "categories": [{"id": 1, "name": "gun"}, {"id": 2, "name": "person"}],
        "annotations": [
            {"id": 1, "image_id": 0, "category_id": 1, "bbox": [10, 10, 20, 15], "iscrowd": 0},
            {"id": 2, "image_id": 0, "category_id": 2, "bbox": [0, 0, 50, 60], "iscrowd": 0},
            {"id": 3, "image_id": 1, "category_id": 1, "bbox": [30, 30, 10, 10], "iscrowd": 0},
        ],
    }
    (root / "instances_train.json").write_text(json.dumps(data), encoding="utf-8")
    return root


def write_clip(path: Path, *, frames: int, fps: float, base: int, size: tuple[int, int] = (96, 64)) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    rng = np.random.default_rng(base)
    for i in range(frames):
        frame = np.full((size[1], size[0], 3), (base + i * 3) % 255, np.uint8)
        frame += rng.integers(0, 20, frame.shape, dtype=np.uint8)
        writer.write(frame)
    writer.release()


@pytest.fixture
def rwf_dataset(tmp_path: Path) -> Path:
    """RWF-2000 layout: train/ and val/ with Fight and NonFight; two frame rates."""
    root = tmp_path / "rwf"
    for split in ("train", "val"):
        for label, base in (("Fight", 200), ("NonFight", 20)):
            folder = root / split / label
            folder.mkdir(parents=True)
            for n in range(4 if split == "train" else 2):
                write_clip(folder / f"{label}_{split}_{n}.avi", frames=40 if n % 2 else 60, fps=30.0 if n % 2 else 15.0, base=base + n)
    return root


@pytest.fixture
def market_dataset(tmp_path: Path) -> Path:
    """Market-1501-style tree: 6 identities × 2 cameras × 2 images in train; query/gallery for 3 ids;
    one junk (-1) and one distractor (0000) image in the gallery."""
    root = tmp_path / "market"
    for folder in ("bounding_box_train", "query", "bounding_box_test"):
        (root / folder).mkdir(parents=True)
    counter = 0
    for pid in range(1, 7):
        for cam in (1, 2):
            for shot in range(2):
                counter += 1
                _image(root / "bounding_box_train" / f"{pid:04d}_c{cam}s1_{counter:06d}_00.jpg", size=(64, 128), seed=pid * 10 + cam)
    for pid in (11, 12, 13):
        _image(root / "query" / f"{pid:04d}_c1s1_000001_00.jpg", size=(64, 128), seed=pid)
        for cam in (1, 2):
            for shot in range(2):
                _image(root / "bounding_box_test" / f"{pid:04d}_c{cam}s1_{shot:06d}_00.jpg", size=(64, 128), seed=pid * 3 + cam + shot)
    _image(root / "bounding_box_test" / "-1_c1s1_000009_00.jpg", size=(64, 128))
    _image(root / "bounding_box_test" / "0000_c2s1_000010_00.jpg", size=(64, 128))
    return root
