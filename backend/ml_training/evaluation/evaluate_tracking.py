"""Single-camera tracking metrics from MOTChallenge text files (CLEAR MOT: MOTA, MOTP, ID switches,
plus IDF1). Scores the ``cv_inference`` tracker's output against a labelled sequence.

File format (one line per box): ``frame, id, x, y, w, h, conf, -1, -1, -1`` (ground truth may use
``conf`` as the "consider" flag: lines with 0 are ignored, as in the benchmark).

    python -m evaluation.evaluate_tracking --gt seq/gt/gt.txt --pred tracker_output.txt --iou 0.5
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from evaluation.metrics_detection import box_iou


@dataclass(frozen=True)
class MotBox:
    frame: int
    track_id: int
    box: tuple[float, float, float, float]


def read_mot(path: Path, *, min_conf: float = 0.0) -> list[MotBox]:
    boxes = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.replace(";", ",").split(",")
        if len(parts) < 6:
            continue
        frame, track_id, x, y, w, h = int(float(parts[0])), int(float(parts[1])), *map(float, parts[2:6])
        conf = float(parts[6]) if len(parts) > 6 else 1.0
        if conf < min_conf or (len(parts) > 6 and conf == 0):
            continue
        boxes.append(MotBox(frame, track_id, (x, y, x + w, y + h)))
    return boxes


@dataclass
class TrackingReport:
    mota: float
    motp: float
    idf1: float
    id_switches: int
    misses: int
    false_positives: int
    matches: int
    ground_truth: int

    def as_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vars(self).items()}


def evaluate_tracking(ground_truth: Sequence[MotBox], predictions: Sequence[MotBox], *, iou_threshold: float = 0.5) -> TrackingReport:
    frames = sorted({b.frame for b in ground_truth} | {b.frame for b in predictions})
    gt_by, pr_by = {}, {}
    for b in ground_truth:
        gt_by.setdefault(b.frame, []).append(b)
    for b in predictions:
        pr_by.setdefault(b.frame, []).append(b)
    last_match: dict[int, int] = {}  # gt id → predicted id of its last match
    misses = false_positives = switches = matches = 0
    iou_sum = 0.0
    pair_counts: dict[tuple[int, int], int] = {}
    for frame in frames:
        gts, prs = gt_by.get(frame, []), pr_by.get(frame, [])
        if gts and prs:
            ious = box_iou(np.array([g.box for g in gts]), np.array([p.box for p in prs]))
            # Prefer keeping the previous assignment (standard CLEAR MOT), then maximise IoU.
            cost = 1.0 - ious
            for gi, g in enumerate(gts):
                for pi, p in enumerate(prs):
                    if last_match.get(g.track_id) == p.track_id and ious[gi, pi] >= iou_threshold:
                        cost[gi, pi] -= 1.0
            rows, cols = linear_sum_assignment(cost)
            matched_g, matched_p = set(), set()
            for gi, pi in zip(rows, cols, strict=True):
                if ious[gi, pi] < iou_threshold:
                    continue
                g, p = gts[gi], prs[pi]
                if g.track_id in last_match and last_match[g.track_id] != p.track_id:
                    switches += 1
                last_match[g.track_id] = p.track_id
                matches += 1
                iou_sum += float(ious[gi, pi])
                pair_counts[(g.track_id, p.track_id)] = pair_counts.get((g.track_id, p.track_id), 0) + 1
                matched_g.add(gi)
                matched_p.add(pi)
            misses += len(gts) - len(matched_g)
            false_positives += len(prs) - len(matched_p)
        else:
            misses += len(gts)
            false_positives += len(prs)
    total_gt = len(ground_truth)
    mota = 1.0 - (misses + false_positives + switches) / total_gt if total_gt else 0.0
    motp = iou_sum / matches if matches else 0.0
    idf1 = _idf1(ground_truth, predictions, pair_counts)
    return TrackingReport(mota, motp, idf1, switches, misses, false_positives, matches, total_gt)


def _idf1(ground_truth: Sequence[MotBox], predictions: Sequence[MotBox], pair_counts: dict[tuple[int, int], int]) -> float:
    """Identity F1: the best one-to-one assignment of ground-truth to predicted identities, scored by
    the frames they co-occur in."""
    gt_ids = sorted({b.track_id for b in ground_truth})
    pr_ids = sorted({b.track_id for b in predictions})
    if not gt_ids or not pr_ids:
        return 0.0
    overlap = np.zeros((len(gt_ids), len(pr_ids)))
    for (g, p), count in pair_counts.items():
        overlap[gt_ids.index(g), pr_ids.index(p)] = count
    rows, cols = linear_sum_assignment(-overlap)
    idtp = overlap[rows, cols].sum()
    gt_len = len(ground_truth)
    pr_len = len(predictions)
    return float(2 * idtp / (gt_len + pr_len)) if gt_len + pr_len else 0.0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gt", type=Path, required=True)
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_tracking(read_mot(args.gt), read_mot(args.pred), iou_threshold=args.iou)
    text = json.dumps(report.as_dict(), indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
