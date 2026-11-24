"""Fight / violence evaluation: clip-level and video-level metrics for a checkpoint or an exported
ONNX model, on any supported dataset layout.

    python -m evaluation.evaluate_violence --checkpoint runs/fight_swin3d_t/best.pt \
        --data datasets/raw/RWF-2000 --layout rwf2000 --split test --threshold 0.5
    python -m evaluation.evaluate_violence --manifest export/fight_swin3d_t/fight_swin3d_t.manifest.json \
        --data datasets/raw/RLVS --layout rlvs --split test --threshold 0.5

Clip level: precision, recall, F1, accuracy, ROC-AUC, PR-AUC, confusion matrix and the false-alarm
rate on normal clips. Video level: the same after averaging (or max-pooling) clip scores per video.
Latency: ms per 16-frame clip, measured (mean and p95), model only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from data_utils.common import load_checkpoint
from data_utils.video_clips import ClipDataset, ClipSpec, VideoItem, collate_clips, list_videos
from evaluation.metrics_classification import best_threshold, binary_report, video_level_scores
from models.fight_models import FightClassifier, FightModelSpec

Scorer = Callable[[torch.Tensor], np.ndarray]  # clips (B,3,T,H,W) → violence probability (B,)


def torch_scorer(model: FightClassifier, device: torch.device, amp_dtype: torch.dtype | None) -> Scorer:
    model.eval()

    def score(clips: torch.Tensor) -> np.ndarray:
        with torch.no_grad(), torch.autocast(device_type=device.type, dtype=amp_dtype or torch.float32, enabled=amp_dtype is not None):
            probs = torch.softmax(model(clips.to(device, non_blocking=True)).float(), dim=1)[:, 1]
        return probs.cpu().numpy()

    return score


def onnx_scorer(manifest_path: Path, providers: list[str] | None = None) -> tuple[Scorer, ClipSpec, dict[str, Any]]:
    import onnxruntime as ort

    from export.manifest import ModelManifest

    manifest = ModelManifest.load(manifest_path)
    session = ort.InferenceSession(str(manifest_path.parent / manifest.file), providers=providers or ["CPUExecutionProvider"])
    name = manifest.input.name
    spec = ClipSpec(
        clip_len=int(manifest.input.clip_len or 16),
        target_fps=float(manifest.input.target_fps or 8),
        size=int(manifest.input.crop or manifest.input.shape[-1]),
        resize_short=int(manifest.input.resize_short or manifest.input.shape[-1]),
    )

    def score(clips: torch.Tensor) -> np.ndarray:
        out = session.run(None, {name: clips.numpy().astype(np.float32)})[0]
        return np.asarray(out)[:, 1]

    return score, spec, manifest.thresholds


def score_dataset(scorer: Scorer, dataset: ClipDataset, *, batch_size: int, workers: int) -> tuple[np.ndarray, np.ndarray, list[int], list[float]]:
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=workers, collate_fn=collate_clips, pin_memory=False)
    scores, labels, videos, latencies = [], [], [], []
    for clips, batch_labels, batch_videos in loader:
        started = time.perf_counter()
        probs = scorer(clips)
        latencies.append((time.perf_counter() - started) * 1000 / len(clips))
        scores.append(probs)
        labels.append(batch_labels.numpy())
        videos += batch_videos.tolist()
    return np.concatenate(scores) if scores else np.array([]), np.concatenate(labels) if labels else np.array([]), videos, latencies


def evaluate_items(scorer: Scorer, items: Sequence[VideoItem], spec: ClipSpec, *, threshold: float | None, batch_size: int = 8, workers: int = 4, video_reduce: str = "mean", max_false_alarm_rate: float | None = None) -> dict[str, Any]:
    dataset = ClipDataset(items, spec, train=False)
    scores, labels, videos, latencies = score_dataset(scorer, dataset, batch_size=batch_size, workers=workers)
    chosen = best_threshold(scores, labels, max_false_alarm_rate=max_false_alarm_rate) if threshold is None else threshold
    clip_report = binary_report(scores, labels, chosen)
    video_scores, video_labels = video_level_scores(scores, labels, videos, reduce=video_reduce)
    video_report = binary_report(video_scores, video_labels, chosen)
    return {
        "threshold": round(chosen, 4),
        "threshold_was_chosen_here": threshold is None,
        "clips": len(scores),
        "videos": len(video_scores),
        "clip_level": clip_report.as_dict(),
        "video_level": video_report.as_dict(),
        "latency_ms_per_clip_mean": round(float(np.mean(latencies)), 2) if latencies else None,
        "latency_ms_per_clip_p95": round(float(np.percentile(latencies, 95)), 2) if latencies else None,
    }


def load_fight_checkpoint(path: Path, device: torch.device) -> tuple[FightClassifier, ClipSpec, dict[str, Any]]:
    checkpoint = load_checkpoint(path, map_location=device)
    config = checkpoint["config"]
    model_cfg = config.get("model", {})
    spec = FightModelSpec(backbone=model_cfg.get("backbone", "swin3d_t"), num_classes=2, pretrained=False, dropout=float(model_cfg.get("dropout", 0.3)))
    model = FightClassifier(spec)
    model.load_state_dict(checkpoint["model"])
    model.to(device)
    clip_cfg = config.get("clip", {})
    return model, ClipSpec(**{k: v for k, v in clip_cfg.items() if k in ClipSpec.__dataclass_fields__}), config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--checkpoint", type=Path)
    group.add_argument("--manifest", type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--layout", required=True, choices=["rwf2000", "rlvs", "ucfcrime", "folders"])
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--threshold", default="0.5", help="probability threshold, auto (val only), or manifest")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    items = [i for i in list_videos(args.data, args.layout) if i.split == args.split]
    if args.manifest:
        scorer, spec, thresholds = onnx_scorer(args.manifest)
        threshold: float | None = float(thresholds.get("violence_probability", 0.5)) if args.threshold == "manifest" else None if args.threshold == "auto" else float(args.threshold)
    else:
        device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else ("cpu" if args.device == "auto" else args.device))
        model, spec, _ = load_fight_checkpoint(args.checkpoint, device)
        scorer = torch_scorer(model, device, torch.float16 if device.type == "cuda" else None)
        threshold = None if args.threshold == "auto" else float(args.threshold)
    if threshold is None and args.split == "test":
        print("refusing: choose the threshold on val (--split val --threshold auto), then pass it for test", file=sys.stderr)
        return 2
    report = evaluate_items(scorer, items, spec, threshold=threshold, batch_size=args.batch_size, workers=args.workers)
    report.update({"split": args.split, "layout": args.layout, "data": str(args.data)})
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
