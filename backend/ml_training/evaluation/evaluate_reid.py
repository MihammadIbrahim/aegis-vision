"""Re-ID evaluation: Rank-1/5/10 and mAP under the standard protocol, cosine-band calibration and
per-crop latency, for a checkpoint or an exported ONNX model.

    python -m evaluation.evaluate_reid --checkpoint runs/reid_osnet_x1_0/best.pt --data datasets/raw/Market-1501 --layout market1501
    python -m evaluation.evaluate_reid --manifest export/reid_osnet/reid_osnet_x1_0.manifest.json --data datasets/raw/MSMT17 --layout msmt17
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
from data_utils.reid_datasets import ReidDataset, ReidItem, eval_transform, parse_reid_dataset
from evaluation.metrics_reid import calibrate_bands, cosine_distance, evaluate_rank, pair_similarities
from models.reid_models import ReidModel, ReidModelSpec

Embedder = Callable[[torch.Tensor], np.ndarray]  # images (B,3,H,W) → L2-normalised (B, D)


def torch_embedder(model: ReidModel, device: torch.device, *, flip_tta: bool, amp_dtype: torch.dtype | None) -> Embedder:
    model.eval()

    def embed(images: torch.Tensor) -> np.ndarray:
        images = images.to(device, non_blocking=True)
        with torch.no_grad(), torch.autocast(device_type=device.type, dtype=amp_dtype or torch.float32, enabled=amp_dtype is not None):
            features = model(images).float()
            if flip_tta:
                features = features + model(torch.flip(images, dims=[3])).float()
        features = torch.nn.functional.normalize(features, dim=1)
        return features.cpu().numpy()

    return embed


def onnx_embedder(manifest_path: Path, providers: list[str] | None = None) -> tuple[Embedder, tuple[int, int], dict[str, float]]:
    import onnxruntime as ort

    from export.manifest import ModelManifest

    manifest = ModelManifest.load(manifest_path)
    session = ort.InferenceSession(str(manifest_path.parent / manifest.file), providers=providers or ["CPUExecutionProvider"])
    name = manifest.input.name

    def embed(images: torch.Tensor) -> np.ndarray:
        out = np.asarray(session.run(None, {name: images.numpy().astype(np.float32)})[0])
        return out / np.clip(np.linalg.norm(out, axis=1, keepdims=True), 1e-12, None)

    return embed, (int(manifest.input.shape[2]), int(manifest.input.shape[3])), manifest.thresholds


def extract(embedder: Embedder, items: Sequence[ReidItem], *, height: int, width: int, batch_size: int, workers: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    loader = DataLoader(ReidDataset(items, eval_transform(height, width)), batch_size=batch_size, shuffle=False, num_workers=workers)
    feats, pids, camids, latencies = [], [], [], []
    for images, batch_pids, batch_camids in loader:
        started = time.perf_counter()
        feats.append(embedder(images))
        latencies.append((time.perf_counter() - started) * 1000 / len(images))
        pids.append(batch_pids.numpy())
        camids.append(batch_camids.numpy())
    return np.concatenate(feats), np.concatenate(pids), np.concatenate(camids), latencies


def evaluate_split(
    embedder: Embedder,
    query: Sequence[ReidItem],
    gallery: Sequence[ReidItem],
    *,
    height: int,
    width: int,
    batch_size: int = 128,
    workers: int = 4,
    cuhk03_protocol: bool = False,
    likely_precision: float = 0.95,
    uncertain_precision: float = 0.5,
    bands: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Rank metrics, mAP, calibration (or the precision of given ``bands``) and latency."""
    q_feats, q_pids, q_cams, q_lat = extract(embedder, query, height=height, width=width, batch_size=batch_size, workers=workers)
    g_feats, g_pids, g_cams, g_lat = extract(embedder, gallery, height=height, width=width, batch_size=batch_size, workers=workers)
    distmat = cosine_distance(q_feats, g_feats)
    report = evaluate_rank(distmat, q_pids, g_pids, q_cams, g_cams, cuhk03_protocol=cuhk03_protocol)
    sims, is_match = pair_similarities(q_feats, g_feats, q_pids, g_pids, q_cams, g_cams)
    calibration = calibrate_bands(sims, is_match, likely_precision=likely_precision, uncertain_precision=uncertain_precision)
    out: dict[str, Any] = {
        **report.as_dict(),
        "query_images": len(query),
        "gallery_images": len(gallery),
        "calibration": calibration.as_dict(),
        "latency_ms_per_crop_mean": round(float(np.mean(q_lat + g_lat)), 3),
        "latency_ms_per_crop_p95": round(float(np.percentile(q_lat + g_lat, 95)), 3),
    }
    if bands:
        likely, uncertain = float(bands["likely_match"]), float(bands["uncertain"])
        above_likely = sims >= likely
        between = (sims >= uncertain) & ~above_likely
        out["bands_applied"] = {
            "likely_match": {"threshold": likely, "pairs": int(above_likely.sum()), "precision": round(float(is_match[above_likely].mean()), 4) if above_likely.any() else None},
            "uncertain": {"threshold": uncertain, "pairs": int(between.sum()), "precision": round(float(is_match[between].mean()), 4) if between.any() else None},
            "recall_of_true_matches_as_likely": round(float((above_likely & is_match).sum() / max(1, is_match.sum())), 4),
        }
    return out


def load_reid_checkpoint(path: Path, device: torch.device) -> tuple[ReidModel, dict[str, Any]]:
    checkpoint = load_checkpoint(path, map_location=device)
    config = checkpoint["config"]
    model_cfg = config.get("model", {})
    spec = ReidModelSpec(
        backbone=model_cfg.get("backbone", "osnet_x1_0"),
        num_classes=int(checkpoint.get("num_classes") or model_cfg.get("num_classes", 751)),
        embed_dim=int(model_cfg.get("embed_dim", 512)),
        pretrained=False,
        height=int(model_cfg.get("height", 256)),
        width=int(model_cfg.get("width", 128)),
    )
    model = ReidModel(spec)
    model.load_state_dict(checkpoint["model"])
    return model.to(device), config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--checkpoint", type=Path)
    group.add_argument("--manifest", type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--layout", required=True, choices=["market1501", "msmt17", "cuhk03"])
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--no-flip-tta", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    splits = parse_reid_dataset(args.data, args.layout)
    bands = None
    if args.manifest:
        embedder, (height, width), thresholds = onnx_embedder(args.manifest)
        bands = {"likely_match": thresholds["likely_match"], "uncertain": thresholds["uncertain"]} if "likely_match" in thresholds else None
    else:
        device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available() else ("cpu" if args.device == "auto" else args.device))
        model, config = load_reid_checkpoint(args.checkpoint, device)
        height, width = model.spec.height, model.spec.width
        embedder = torch_embedder(model, device, flip_tta=not args.no_flip_tta, amp_dtype=torch.float16 if device.type == "cuda" else None)
    report = evaluate_split(embedder, splits.query, splits.gallery, height=height, width=width, batch_size=args.batch_size, workers=args.workers, cuhk03_protocol=args.layout == "cuhk03", bands=bands)
    report.update({"data": str(args.data), "layout": args.layout, "splits": splits.summary()})
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
