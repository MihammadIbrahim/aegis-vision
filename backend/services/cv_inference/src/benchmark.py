"""Measures the person detector on this machine: no estimates, only timings.

    python -m src.benchmark --image photo.jpg [--runs 50] [--warmup 5]

Reports, per frame:
* ``inference_ms``: the ONNX Runtime call only (``OnnxRuntimeAdapter`` statistics);
* ``detect_ms``: the whole ``detect()``: letterbox pre-processing + inference + decoding and NMS;
* ``detect_fps`` = 1000 / mean ``detect_ms``. This is single-camera throughput of the detector alone.
  Video decoding, tracking and HTTP delivery are not included, and nothing runs in parallel.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import onnxruntime as ort

from src.config import PipelineSettings
from src.detectors.human_detector import YoloxHumanDetector
from src.model_loader import InferenceStats
from src.schemas import Frame


def _cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def run(image_path: Path, *, runs: int, warmup: int, settings: PipelineSettings) -> dict[str, object]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"Cannot read image: {image_path.name}")
    detector = YoloxHumanDetector(
        settings.human_model_spec,
        score_threshold=settings.human_score_threshold,
        nms_iou=settings.human_nms_iou,
        device=settings.device,
    )
    frame = Frame(uuid4(), "benchmark", 0, datetime.now(UTC), image)
    for _ in range(warmup):
        detector.detect(frame)
    detector.adapter.stats = InferenceStats()  # count the measured runs only
    detect_ms: list[float] = []
    detections = []
    for _ in range(runs):
        started = time.perf_counter()
        detections = detector.detect(frame)
        detect_ms.append((time.perf_counter() - started) * 1000)
    stats = detector.adapter.stats
    providers = getattr(detector.adapter, "providers", [])
    detector.close()
    return {
        "model": f"{detector.info.name} {detector.info.version}",
        "input_size": [detector.spec.input_height, detector.spec.input_width],
        "image_size": [int(image.shape[1]), int(image.shape[0])],
        "cpu": _cpu_name(),
        "logical_cpus": os.cpu_count(),
        "onnxruntime": ort.__version__,
        "providers": providers,
        "runs": runs,
        "warmup_runs": warmup,
        "inference_ms": {
            "mean": round(stats.mean_ms, 2),
            "p50": round(stats.percentile_ms(50), 2),
            "p95": round(stats.percentile_ms(95), 2),
        },
        "detect_ms": {
            "mean": round(float(np.mean(detect_ms)), 2),
            "p50": round(float(np.percentile(detect_ms, 50)), 2),
            "p95": round(float(np.percentile(detect_ms, 95)), 2),
        },
        "detect_fps": round(1000 / float(np.mean(detect_ms)), 2),
        "persons_found": len(detections),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the person detector on this machine.")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run(args.image, runs=args.runs, warmup=args.warmup, settings=PipelineSettings()), indent=2))


if __name__ == "__main__":
    main()
