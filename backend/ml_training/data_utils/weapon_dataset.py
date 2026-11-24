"""Weapon-detection datasets: parsing, conversion to the YOLO layout and deterministic splitting.

Public weapon datasets come in three annotation formats. All are converted to one layout that
``ultralytics`` reads directly and that our own evaluation code reads too:

    <root>/
      images/{train,val,test}/<name>.jpg
      labels/{train,val,test}/<name>.txt      "class_index cx cy w h" normalised to 0–1; empty file =
                                             negative image (no weapon); kept on purpose
      data.yaml                               names, paths

Supported sources (``convert_dataset``):
* ``yolo``: already ``images/`` + ``labels/`` (flat or pre-split), e.g. YouTube-GDD, Roboflow exports.
* ``coco``: one COCO JSON per split or one for all (``instances*.json``).
* ``voc``: Pascal VOC XML next to the images, e.g. OD-WeaponDetection ("Weapons and similar handled
  objects"), Sohas_weapon.

Class names are **mapped** through ``class_map`` in the config (dataset label → project class), so
"pistol" and "handgun" from different datasets become one class, and classes outside the map (e.g.
"wallet", "card" in Sohas) are dropped or kept as negatives.

Splitting is by a hash of the file name (``common.stable_split``), so the same image always lands in
the same split, and the test split is never used for training or model selection.

Licences: each dataset's licence and citation are listed in ``datasets/README.md``; nothing is
downloaded by this module.
"""

from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from data_utils.common import Split, stable_split

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SourceFormat = Literal["yolo", "coco", "voc"]


@dataclass(frozen=True)
class Box:
    """One object: class name as in the source dataset, box in pixels (x1, y1, x2, y2)."""

    label: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class AnnotatedImage:
    path: Path
    width: int
    height: int
    boxes: list[Box] = field(default_factory=list)
    split_hint: Split | None = None  # when the source is already split


class DatasetFormatError(ValueError):
    pass


# --- Parsers -------------------------------------------------------------------------------------------


def _image_size(path: Path) -> tuple[int, int]:
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise DatasetFormatError(f"cannot read image {path}")
    return int(image.shape[1]), int(image.shape[0])


def _split_from_path(path: Path) -> Split | None:
    for part in path.parts:
        lowered = part.lower()
        if lowered in ("train", "training"):
            return "train"
        if lowered in ("val", "valid", "validation"):
            return "val"
        if lowered == "test":
            return "test"
    return None


def parse_yolo(root: Path, names: Sequence[str]) -> list[AnnotatedImage]:
    """``root/images/**`` with ``root/labels/**`` mirroring the path, YOLO text labels."""
    images_dir, labels_dir = root / "images", root / "labels"
    if not images_dir.is_dir():
        raise DatasetFormatError(f"{images_dir} not found")
    items: list[AnnotatedImage] = []
    for image_path in sorted(p for p in images_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES):
        relative = image_path.relative_to(images_dir).with_suffix(".txt")
        label_path = labels_dir / relative
        width, height = _image_size(image_path)
        boxes: list[Box] = []
        if label_path.is_file():
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                index = int(float(parts[0]))
                if index < 0 or index >= len(names):
                    raise DatasetFormatError(f"{label_path}: class index {index} outside names ({len(names)})")
                cx, cy, w, h = (float(v) for v in parts[1:5])
                boxes.append(
                    Box(names[index], (cx - w / 2) * width, (cy - h / 2) * height, (cx + w / 2) * width, (cy + h / 2) * height)
                )
        items.append(AnnotatedImage(image_path, width, height, boxes, _split_from_path(relative)))
    return items


def parse_coco(json_path: Path, images_dir: Path, split_hint: Split | None = None) -> list[AnnotatedImage]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    categories = {c["id"]: c["name"] for c in data.get("categories", [])}
    by_image: dict[int, AnnotatedImage] = {}
    for image in data.get("images", []):
        path = images_dir / image["file_name"]
        by_image[image["id"]] = AnnotatedImage(path, int(image["width"]), int(image["height"]), [], split_hint)
    for annotation in data.get("annotations", []):
        item = by_image.get(annotation["image_id"])
        if item is None or annotation.get("iscrowd"):
            continue
        x, y, w, h = annotation["bbox"]
        if w <= 0 or h <= 0:
            continue
        item.boxes.append(Box(categories[annotation["category_id"]], x, y, x + w, y + h))
    return [item for item in by_image.values() if item.path.is_file()]


def parse_voc(root: Path) -> list[AnnotatedImage]:
    """Pascal VOC: ``*.xml`` files anywhere under ``root`` (``annotations/`` and ``images/`` in
    OD-WeaponDetection; XML next to images in Sohas)."""
    items: list[AnnotatedImage] = []
    xml_files = sorted(root.rglob("*.xml"))
    if not xml_files:
        raise DatasetFormatError(f"no VOC XML files under {root}")
    image_index = {p.name: p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES}
    for xml_path in xml_files:
        tree = ET.parse(xml_path)
        node = tree.getroot()
        filename = node.findtext("filename") or xml_path.with_suffix(".jpg").name
        image_path = image_index.get(filename) or image_index.get(xml_path.stem + ".jpg") or image_index.get(xml_path.stem + ".png")
        if image_path is None:
            continue
        size = node.find("size")
        if size is not None and size.findtext("width") and size.findtext("height"):
            width, height = int(float(size.findtext("width"))), int(float(size.findtext("height")))  # type: ignore[arg-type]
        else:
            width, height = _image_size(image_path)
        boxes = []
        for obj in node.findall("object"):
            name = (obj.findtext("name") or "").strip()
            bndbox = obj.find("bndbox")
            if not name or bndbox is None:
                continue
            x1, y1, x2, y2 = (float(bndbox.findtext(k) or 0) for k in ("xmin", "ymin", "xmax", "ymax"))
            if x2 > x1 and y2 > y1:
                boxes.append(Box(name, x1, y1, x2, y2))
        items.append(AnnotatedImage(image_path, width, height, boxes, _split_from_path(xml_path.relative_to(root))))
    return items


# --- Conversion -------------------------------------------------------------------------------------------


def _yolo_line(box: Box, class_index: int, width: int, height: int) -> str:
    x1, y1 = max(0.0, box.x1), max(0.0, box.y1)
    x2, y2 = min(float(width), box.x2), min(float(height), box.y2)
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    w, h = (x2 - x1) / width, (y2 - y1) / height
    return f"{class_index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


@dataclass
class ConversionReport:
    images: dict[str, int] = field(default_factory=dict)
    boxes: dict[str, int] = field(default_factory=dict)
    negatives: dict[str, int] = field(default_factory=dict)
    dropped_labels: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"images": self.images, "boxes": self.boxes, "negatives": self.negatives, "dropped_labels": self.dropped_labels}


def write_yolo_dataset(
    items: Iterable[AnnotatedImage],
    out_root: Path,
    *,
    class_names: Sequence[str],
    class_map: Mapping[str, str],
    ratios: Sequence[float] = (0.8, 0.1, 0.1),
    seed: int = 42,
    keep_negatives: bool = True,
    link: bool = True,
    respect_source_splits: bool = True,
) -> ConversionReport:
    """Writes the YOLO layout and ``data.yaml``. ``class_map`` maps source labels to project class
    names (anything not in it is dropped; an image left without boxes becomes a negative)."""
    index_of = {name: i for i, name in enumerate(class_names)}
    for target in class_map.values():
        if target not in index_of:
            raise ValueError(f"class_map target {target!r} is not in class_names {list(class_names)}")
    report = ConversionReport()
    splits: tuple[Split, ...] = ("train", "val", "test")
    for split in splits[: len(ratios)]:
        (out_root / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "labels" / split).mkdir(parents=True, exist_ok=True)
    for item in items:
        split: Split = (
            item.split_hint if respect_source_splits and item.split_hint else splits[stable_split(item.path.name, ratios, seed)]
        )
        lines = []
        for box in item.boxes:
            target = class_map.get(box.label)
            if target is None:
                report.dropped_labels[box.label] = report.dropped_labels.get(box.label, 0) + 1
                continue
            lines.append(_yolo_line(box, index_of[target], item.width, item.height))
        if not lines and not keep_negatives:
            continue
        destination = out_root / "images" / split / item.path.name
        if not destination.exists():
            if link:
                try:
                    destination.symlink_to(item.path.resolve())
                except OSError:
                    shutil.copy2(item.path, destination)
            else:
                shutil.copy2(item.path, destination)
        (out_root / "labels" / split / (item.path.stem + ".txt")).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        report.images[split] = report.images.get(split, 0) + 1
        report.boxes[split] = report.boxes.get(split, 0) + len(lines)
        if not lines:
            report.negatives[split] = report.negatives.get(split, 0) + 1
    write_data_yaml(out_root, class_names)
    return report


def write_data_yaml(root: Path, class_names: Sequence[str]) -> Path:
    data = {
        "path": str(root.resolve()),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "nc": len(class_names),
        "names": list(class_names),
    }
    path = root / "data.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def convert_dataset(
    source: Path,
    fmt: SourceFormat,
    out_root: Path,
    *,
    class_names: Sequence[str],
    class_map: Mapping[str, str],
    source_names: Sequence[str] | None = None,
    images_dir: Path | None = None,
    **kwargs: object,
) -> ConversionReport:
    if fmt == "yolo":
        if not source_names:
            raise ValueError("source_names (the source dataset's class list) is required for yolo input")
        items = parse_yolo(source, source_names)
    elif fmt == "coco":
        json_files = sorted(source.rglob("*.json")) if source.is_dir() else [source]
        items = []
        for json_file in json_files:
            hint = _split_from_path(json_file.relative_to(source if source.is_dir() else source.parent))
            items.extend(parse_coco(json_file, images_dir or (source if source.is_dir() else source.parent), hint))
    elif fmt == "voc":
        items = parse_voc(source)
    else:
        raise ValueError(f"unknown format {fmt}")
    return write_yolo_dataset(items, out_root, class_names=class_names, class_map=class_map, **kwargs)  # type: ignore[arg-type]


# --- Reading the converted layout --------------------------------------------------------------------------


def read_yolo_split(root: Path, split: Split) -> list[AnnotatedImage]:
    """Images and labels of one split of a converted dataset, with class names from ``data.yaml``."""
    data = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    names = list(data["names"].values()) if isinstance(data["names"], dict) else list(data["names"])
    images_dir = root / "images" / split
    items = []
    for image_path in sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        width, height = _image_size(image_path)
        label_path = root / "labels" / split / (image_path.stem + ".txt")
        boxes = []
        if label_path.is_file():
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    cx, cy, w, h = (float(v) for v in parts[1:5])
                    boxes.append(Box(names[int(parts[0])], (cx - w / 2) * width, (cy - h / 2) * height, (cx + w / 2) * width, (cy + h / 2) * height))
        items.append(AnnotatedImage(image_path, width, height, boxes, split))
    return items


def verify_layout(root: Path) -> dict[str, dict[str, int]]:
    """Counts images, labelled images and negatives per split; raises if the layout is incomplete."""
    data_yaml = root / "data.yaml"
    if not data_yaml.is_file():
        raise DatasetFormatError(f"{data_yaml} is missing: run convert_dataset first")
    summary: dict[str, dict[str, int]] = {}
    for split in ("train", "val", "test"):
        images = root / "images" / split
        labels = root / "labels" / split
        if not images.is_dir() or not labels.is_dir():
            raise DatasetFormatError(f"split {split!r} is missing images/ or labels/ under {root}")
        files = [p for p in images.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES]
        negatives = sum(1 for p in files if not (labels / (p.stem + ".txt")).read_text(encoding="utf-8").strip() if (labels / (p.stem + ".txt")).is_file())
        missing = sum(1 for p in files if not (labels / (p.stem + ".txt")).is_file())
        summary[split] = {"images": len(files), "negatives": negatives, "missing_label_files": missing}
    return summary
