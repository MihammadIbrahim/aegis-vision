"""Weapon-detector evaluation with the project's own metrics, for a ``.pt`` (ultralytics) or an
exported ``.onnx`` + manifest (our NumPy decoder, as the runtime decodes it).

    python -m evaluation.evaluate_weapons --weights runs/weapon_yolo11m/weights/best.pt \
        --data datasets/weapons_yolo/data.yaml --split test --threshold 0.4
    python -m evaluation.evaluate_weapons --weights export/weapon_yolo11m/weapon_yolo11m.manifest.json \
        --data datasets/weapons_yolo/data.yaml --split test --threshold 0.4

Reports per class: precision, recall, F1 (at the threshold), AP@0.50, AP@0.50:0.95; mAP; the number
of negative images and the false-positive rate per 1,000 negative frames; and inference latency
(ms/frame, mean and p95, measured). ``--threshold auto`` picks the threshold with the best F1 on the
split being evaluated: use that on **val** only, then pass the value explicitly for **test**.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from data_utils.weapon_dataset import read_yolo_split
from evaluation.metrics_detection import GroundTruth, Prediction, best_threshold_by_f1, evaluate_detections

RAW_SCORE = 0.001  # keep everything for AP; thresholds are applied by the metrics


def _ground_truth(data_yaml: Path, split: str) -> tuple[list[GroundTruth], list[str], list[Path], list[str]]:
    root = data_yaml.parent
    items = read_yolo_split(root, split)  # type: ignore[arg-type]
    data = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    names = list(data["names"].values()) if isinstance(data["names"], dict) else list(data["names"])
    truths = [GroundTruth(item.path.name, box.label, (box.x1, box.y1, box.x2, box.y2)) for item in items for box in item.boxes]
    return truths, [item.path.name for item in items], [item.path for item in items], names


def predict_pt(weights: Path, images: Sequence[Path], *, nms_iou: float, device: Any, imgsz: int | None) -> tuple[list[Prediction], list[float]]:
    from ultralytics import RTDETR, YOLO

    model = RTDETR(str(weights)) if weights.name.lower().startswith("rtdetr") else YOLO(str(weights))
    predictions, latencies = [], []
    kwargs: dict[str, Any] = {"conf": RAW_SCORE, "iou": nms_iou, "device": device, "verbose": False, "max_det": 300}
    if imgsz:
        kwargs["imgsz"] = imgsz
    for path in images:
        started = time.perf_counter()
        result = model.predict(str(path), **kwargs)[0]
        latencies.append((time.perf_counter() - started) * 1000)
        names = result.names
        for box, score, cls in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(), result.boxes.cls.cpu().numpy(), strict=True):
            predictions.append(Prediction(path.name, names[int(cls)], float(score), tuple(map(float, box))))  # type: ignore[arg-type]
    return predictions, latencies


def predict_onnx(manifest_path: Path, images: Sequence[Path], *, nms_iou: float, providers: list[str] | None) -> tuple[list[Prediction], list[float]]:
    from export.onnx_detect import OnnxDetector

    detector = OnnxDetector(manifest_path, score_threshold=RAW_SCORE, nms_iou=nms_iou, providers=providers)
    predictions, latencies = [], []
    for path in images:
        image = cv2.imread(str(path))
        if image is None:
            continue
        started = time.perf_counter()
        boxes = detector.detect(image)
        latencies.append((time.perf_counter() - started) * 1000)
        predictions += [Prediction(path.name, b.label, b.score, (b.x1, b.y1, b.x2, b.y2)) for b in boxes]
    return predictions, latencies


def evaluate_weights(
    weights: Path,
    data_yaml: Path,
    *,
    split: str,
    score_threshold: float | None,
    nms_iou: float = 0.6,
    device: Any = "cpu",
    imgsz: int | None = None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    truths, image_ids, paths, names = _ground_truth(data_yaml, split)
    if weights.suffix == ".json":
        predictions, latencies = predict_onnx(weights, paths, nms_iou=nms_iou, providers=providers)
    else:
        predictions, latencies = predict_pt(weights, paths, nms_iou=nms_iou, device=device, imgsz=imgsz)
    chosen = score_threshold
    per_class_best = {}
    if chosen is None:
        for name in names:
            per_class_best[name] = best_threshold_by_f1(predictions, truths, label=name)
        present = [v for k, v in per_class_best.items() if any(t.label == k for t in truths)]
        chosen = float(np.mean([t for t, _ in present])) if present else 0.25
    report = evaluate_detections(predictions, truths, class_names=names, image_ids=image_ids, score_threshold=chosen)
    out = report.as_dict()
    out.update(
        {
            "split": split,
            "images": len(image_ids),
            "weights": str(weights),
            "chosen_threshold": round(chosen, 4),
            "per_class_best_threshold": {k: {"threshold": t, "f1": round(f, 4)} for k, (t, f) in per_class_best.items()},
            "latency_ms_mean": round(float(np.mean(latencies)), 2) if latencies else None,
            "latency_ms_p95": round(float(np.percentile(latencies, 95)), 2) if latencies else None,
        }
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, required=True, help="best.pt, or <name>.manifest.json next to the ONNX file")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--threshold", default="auto", help="score threshold, or auto (val only)")
    parser.add_argument("--nms-iou", type=float, default=0.6)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--imgsz", type=int)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    threshold = None if args.threshold == "auto" else float(args.threshold)
    if threshold is None and args.split == "test":
        print("refusing: choose the threshold on val (--split val --threshold auto), then pass it for test", file=sys.stderr)
        return 2
    report = evaluate_weights(args.weights, args.data, split=args.split, score_threshold=threshold, nms_iou=args.nms_iou, device=args.device, imgsz=args.imgsz)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
