"""Binary classification metrics for clip-level violence detection.

Everything is computed from scores (probability of the positive class) and binary labels, with no
training-library dependency: precision, recall, F1, accuracy, confusion matrix, ROC-AUC
(Mann–Whitney formulation, ties handled), PR-AUC (average precision), the **false-alarm rate on
normal clips** at the operating threshold, and threshold selection on the validation split.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    positives, negatives = int(y.sum()), int((1 - y).sum())
    if positives == 0 or negatives == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=np.float64)
    sorted_scores = s[order]
    i = 0
    while i < len(s):  # average ranks for tied scores
        j = i
        while j + 1 < len(s) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def average_precision_score(scores: Sequence[float], labels: Sequence[int]) -> float:
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    if y.sum() == 0:
        return float("nan")
    # Threshold-based AP (as scikit-learn): tied scores form one threshold step, so the result does
    # not depend on how ties are ordered.
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    distinct = np.where(np.diff(s_sorted))[0]
    ends = np.r_[distinct, len(s_sorted) - 1]
    cum_tp = np.cumsum(y_sorted)[ends]
    counts = ends + 1
    precision = cum_tp / counts
    recall = cum_tp / y.sum()
    previous_recall = np.r_[0.0, recall[:-1]]
    return float(((recall - previous_recall) * precision).sum())


@dataclass
class BinaryReport:
    threshold: float
    accuracy: float
    precision: float
    recall: float
    f1: float
    specificity: float
    false_alarm_rate: float  # FP / negatives at the threshold ("normal clip flagged as violence")
    roc_auc: float
    pr_auc: float
    confusion: dict[str, int]
    support: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "threshold": round(self.threshold, 4),
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "specificity": round(self.specificity, 4),
            "false_alarm_rate": round(self.false_alarm_rate, 4),
            "roc_auc": None if np.isnan(self.roc_auc) else round(self.roc_auc, 4),
            "pr_auc": None if np.isnan(self.pr_auc) else round(self.pr_auc, 4),
            "confusion": self.confusion,
            "support": self.support,
        }


def binary_report(scores: Sequence[float], labels: Sequence[int], threshold: float = 0.5) -> BinaryReport:
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    predicted = (s >= threshold).astype(np.int64)
    tp = int(((predicted == 1) & (y == 1)).sum())
    tn = int(((predicted == 0) & (y == 0)).sum())
    fp = int(((predicted == 1) & (y == 0)).sum())
    fn = int(((predicted == 0) & (y == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return BinaryReport(
        threshold=threshold,
        accuracy=(tp + tn) / len(y) if len(y) else 0.0,
        precision=precision,
        recall=recall,
        f1=f1,
        specificity=specificity,
        false_alarm_rate=fp / (tn + fp) if tn + fp else 0.0,
        roc_auc=roc_auc(s, y),
        pr_auc=average_precision_score(s, y),
        confusion={"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        support={"positive": int(y.sum()), "negative": int(len(y) - y.sum())},
    )


def best_threshold(scores: Sequence[float], labels: Sequence[int], *, criterion: str = "f1", max_false_alarm_rate: float | None = None) -> float:
    """Chooses the operating threshold on validation data: the one with the best ``criterion`` (f1 or
    youden), optionally among thresholds whose false-alarm rate stays below a budget."""
    s = np.asarray(scores, dtype=np.float64)
    candidates = np.unique(np.concatenate([np.round(np.linspace(0.05, 0.95, 19), 2), s]))
    best_value, best_threshold_ = -1.0, 0.5
    for threshold in candidates:
        report = binary_report(s, labels, float(threshold))
        if max_false_alarm_rate is not None and report.false_alarm_rate > max_false_alarm_rate:
            continue
        value = report.f1 if criterion == "f1" else report.recall + report.specificity - 1
        if value > best_value:
            best_value, best_threshold_ = value, float(threshold)
    return best_threshold_


def video_level_scores(clip_scores: Sequence[float], clip_labels: Sequence[int], video_of: Sequence[int], *, reduce: str = "mean") -> tuple[list[float], list[int]]:
    """Aggregates clip scores per video (mean or max) for video-level metrics."""
    by_video: dict[int, list[float]] = {}
    label_of: dict[int, int] = {}
    for score, label, video in zip(clip_scores, clip_labels, video_of, strict=True):
        by_video.setdefault(video, []).append(float(score))
        label_of[video] = int(label)
    videos = sorted(by_video)
    agg = (lambda v: float(np.mean(v))) if reduce == "mean" else (lambda v: float(np.max(v)))
    return [agg(by_video[v]) for v in videos], [label_of[v] for v in videos]
