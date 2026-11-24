"""Object-detection metrics computed from raw predictions, independent of any training library.

* ``average_precision``: COCO-style AP with 101-point interpolation at one IoU threshold.
* ``evaluate_detections``: per-class precision, recall, F1 (at the score threshold), AP@0.50 and
  AP@0.50:0.95, their means (mAP), plus the **false-positive rate per 1,000 negative frames** (images
  with no object of any class), which matters more for a security alert system than mAP alone.

Inputs are plain lists so the same code scores ``ultralytics`` ``.pt`` predictions, our own ONNX
decoder's predictions (parity check) and the unit tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

IOU_RANGE = tuple(np.round(np.arange(0.5, 0.96, 0.05), 2).tolist())


@dataclass(frozen=True)
class Prediction:
    image_id: str
    label: str
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass(frozen=True)
class GroundTruth:
    image_id: str
    label: str
    box: tuple[float, float, float, float]


def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU matrix between boxes ``a`` (N×4) and ``b`` (M×4)."""
    if a.size == 0 or b.size == 0:
        return np.zeros((a.shape[0], b.shape[0]), dtype=np.float64)
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    wh = np.clip(rb - lt, 0, None)
    inter = wh[..., 0] * wh[..., 1]
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.clip(area_a[:, None] + area_b[None, :] - inter, 1e-12, None)


def _match(predictions: Sequence[Prediction], truths: Sequence[GroundTruth], iou_threshold: float) -> tuple[np.ndarray, np.ndarray, int]:
    """Greedy matching by descending score, per image. Returns (scores, tp flags, total truths)."""
    by_image: dict[str, list[GroundTruth]] = {}
    for truth in truths:
        by_image.setdefault(truth.image_id, []).append(truth)
    ordered = sorted(predictions, key=lambda p: -p.score)
    scores = np.array([p.score for p in ordered], dtype=np.float64)
    tp = np.zeros(len(ordered), dtype=bool)
    used: dict[str, np.ndarray] = {}
    for index, pred in enumerate(ordered):
        candidates = by_image.get(pred.image_id, [])
        if not candidates:
            continue
        gt_boxes = np.array([t.box for t in candidates], dtype=np.float64)
        ious = box_iou(np.array([pred.box], dtype=np.float64), gt_boxes)[0]
        taken = used.setdefault(pred.image_id, np.zeros(len(candidates), dtype=bool))
        ious[taken] = -1.0
        best = int(np.argmax(ious))
        if ious[best] >= iou_threshold:
            tp[index] = True
            taken[best] = True
    return scores, tp, len(truths)


def average_precision(predictions: Sequence[Prediction], truths: Sequence[GroundTruth], iou_threshold: float = 0.5) -> float:
    """COCO AP: precision made monotonic, sampled at 101 recall points."""
    if not truths:
        return 0.0
    _, tp, total = _match(predictions, truths, iou_threshold)
    if tp.size == 0:
        return 0.0
    cum_tp = np.cumsum(tp)
    cum_fp = np.cumsum(~tp)
    recall = cum_tp / total
    precision = cum_tp / np.maximum(cum_tp + cum_fp, 1e-12)
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    recall_points = np.linspace(0, 1, 101)
    indices = np.searchsorted(recall, recall_points, side="left")
    sampled = np.array([precision[i] if i < len(precision) else 0.0 for i in indices])
    return float(sampled.mean())


def precision_recall_f1(predictions: Sequence[Prediction], truths: Sequence[GroundTruth], *, score_threshold: float, iou_threshold: float = 0.5) -> tuple[float, float, float]:
    kept = [p for p in predictions if p.score >= score_threshold]
    _, tp, total = _match(kept, truths, iou_threshold)
    true_positives = int(tp.sum())
    precision = true_positives / len(kept) if kept else 0.0
    recall = true_positives / total if total else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


@dataclass
class ClassMetrics:
    precision: float
    recall: float
    f1: float
    ap50: float
    ap50_95: float
    support: int


@dataclass
class DetectionReport:
    per_class: dict[str, ClassMetrics]
    map50: float
    map50_95: float
    score_threshold: float
    negative_images: int
    false_positive_images: int
    false_positives_per_1000_negative_frames: float
    extra: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "score_threshold": self.score_threshold,
            "mAP50": round(self.map50, 4),
            "mAP50_95": round(self.map50_95, 4),
            "per_class": {
                name: {
                    "precision": round(m.precision, 4),
                    "recall": round(m.recall, 4),
                    "f1": round(m.f1, 4),
                    "AP50": round(m.ap50, 4),
                    "AP50_95": round(m.ap50_95, 4),
                    "support": m.support,
                }
                for name, m in self.per_class.items()
            },
            "negative_images": self.negative_images,
            "false_positive_images": self.false_positive_images,
            "false_positives_per_1000_negative_frames": round(self.false_positives_per_1000_negative_frames, 2),
            **self.extra,
        }


def evaluate_detections(
    predictions: Sequence[Prediction],
    truths: Sequence[GroundTruth],
    *,
    class_names: Sequence[str],
    image_ids: Sequence[str],
    score_threshold: float = 0.25,
) -> DetectionReport:
    """``image_ids`` must list every evaluated image, so images without any truth count as
    negatives (an FP on them is an alarm that a security team would have to investigate)."""
    per_class: dict[str, ClassMetrics] = {}
    for name in class_names:
        preds = [p for p in predictions if p.label == name]
        gts = [t for t in truths if t.label == name]
        precision, recall, f1 = precision_recall_f1(preds, gts, score_threshold=score_threshold)
        aps = [average_precision(preds, gts, iou) for iou in IOU_RANGE]
        per_class[name] = ClassMetrics(precision, recall, f1, aps[0], float(np.mean(aps)) if aps else 0.0, len(gts))
    present = [m for m in per_class.values() if m.support > 0]
    map50 = float(np.mean([m.ap50 for m in present])) if present else 0.0
    map50_95 = float(np.mean([m.ap50_95 for m in present])) if present else 0.0
    positive_images = {t.image_id for t in truths}
    negatives = [i for i in image_ids if i not in positive_images]
    fp_images = {p.image_id for p in predictions if p.score >= score_threshold and p.image_id not in positive_images}
    rate = 1000.0 * len(fp_images) / len(negatives) if negatives else 0.0
    return DetectionReport(per_class, map50, map50_95, score_threshold, len(negatives), len(fp_images), rate)


def best_threshold_by_f1(predictions: Sequence[Prediction], truths: Sequence[GroundTruth], *, label: str, candidates: Sequence[float] = tuple(np.round(np.arange(0.05, 0.96, 0.05), 2).tolist())) -> tuple[float, float]:
    """The score threshold with the highest F1 for one class (chosen on the validation split)."""
    preds = [p for p in predictions if p.label == label]
    gts = [t for t in truths if t.label == label]
    best = (float(candidates[0]), -1.0)
    for threshold in candidates:
        f1 = precision_recall_f1(preds, gts, score_threshold=threshold)[2]
        if f1 > best[1]:
            best = (float(threshold), f1)
    return best
