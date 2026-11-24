"""Person-detector evaluation on a YOLO-layout person dataset (e.g. CrowdHuman converted with
``train_human_detector prepare``), for either a fine-tuned ultralytics model or the runtime's
YOLOX-Tiny ONNX file.

    python -m evaluation.evaluate_human_detector --weights runs/human_yolo11n/weights/best.pt --data datasets/crowdhuman_yolo/data.yaml --threshold 0.35
    python -m evaluation.evaluate_human_detector --yolox ../services/cv_inference/weights/yolox_tiny.model.json --data datasets/crowdhuman_yolo/data.yaml --threshold 0.35

The YOLOX path reuses the runtime's own detector code (pre-processing, grid decode, NMS), so the
measured numbers are the numbers the pipeline would get.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from evaluation.evaluate_weapons import _ground_truth, evaluate_weights
from evaluation.metrics_detection import Prediction, evaluate_detections

CV_INFERENCE_SRC = Path(__file__).resolve().parents[2] / "services" / "cv_inference"


def evaluate_yolox(spec_path: Path, data_yaml: Path, *, split: str, score_threshold: float) -> dict[str, Any]:
    sys.path.insert(0, str(CV_INFERENCE_SRC))
    from src.detectors.human_detector import YoloxHumanDetector  # noqa: PLC0415
    from src.schemas import Frame  # noqa: PLC0415

    detector = YoloxHumanDetector(spec_path, score_threshold=0.001)
    truths, image_ids, paths, names = _ground_truth(data_yaml, split)
    camera = uuid4()
    predictions, latencies = [], []
    for index, path in enumerate(paths):
        image = cv2.imread(str(path))
        if image is None:
            continue
        frame = Frame(camera_id=camera, source_id=path.name, index=index, timestamp=datetime.now(UTC), image=image)
        started = time.perf_counter()
        detections = detector.detect(frame)
        latencies.append((time.perf_counter() - started) * 1000)
        predictions += [Prediction(path.name, names[0], d.confidence, (d.box.x1, d.box.y1, d.box.x2, d.box.y2)) for d in detections]
    report = evaluate_detections(predictions, truths, class_names=names, image_ids=image_ids, score_threshold=score_threshold).as_dict()
    report.update({"split": split, "images": len(image_ids), "weights": str(spec_path), "latency_ms_mean": round(float(np.mean(latencies)), 2), "latency_ms_p95": round(float(np.percentile(latencies, 95)), 2)})
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--weights", type=Path, help="ultralytics .pt or an exported manifest.json")
    group.add_argument("--yolox", type=Path, help="the runtime's yolox_tiny.model.json")
    parser.add_argument("--data", type=Path, required=True, help="data.yaml with a single 'person' class")
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.yolox:
        report = evaluate_yolox(args.yolox, args.data, split=args.split, score_threshold=args.threshold)
    else:
        report = evaluate_weights(args.weights, args.data, split=args.split, score_threshold=args.threshold, device=args.device)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
