"""Shared ``ultralytics`` training for the detector pipelines (weapons; optional person model).

``ultralytics`` (AGPL-3.0) provides YOLO11 and RT-DETR with CUDA, AMP, cosine LR, mosaic/mixup,
checkpoints (``best.pt`` / ``last.pt``) and early stopping. With ``albumentations`` installed it also
applies blur, median blur, grey, CLAHE and JPEG compression to training images; the CCTV-specific
colour and geometry settings come from the YAML config (``augment:``).

After training, the **test** split is scored with our own metrics (``evaluation.evaluate_weapons``)
so numbers do not depend on the library's reporting, and the score threshold is chosen on the
**val** split, never on test.

Licence note: ultralytics and models trained with it are AGPL-3.0 unless an Ultralytics Enterprise
licence is bought. The runtime (``cv_inference``) does not import ultralytics; it runs the exported
ONNX graph with its own decoder. Record the licence in the manifest and in
``experiments/model_registry.md``; this is acceptable for the FYP and must be reviewed before any
commercial deployment.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from data_utils.common import dump_config, git_commit, save_json, seed_everything, utc_now

logger = logging.getLogger(__name__)

DEFAULT_AUGMENT: dict[str, float] = {
    # CCTV: strong brightness range (night / IR), mild hue, some scale and perspective, no vertical flip
    "hsv_h": 0.015,
    "hsv_s": 0.6,
    "hsv_v": 0.5,
    "degrees": 5.0,
    "translate": 0.1,
    "scale": 0.5,
    "shear": 2.0,
    "perspective": 0.0005,
    "flipud": 0.0,
    "fliplr": 0.5,
    "mosaic": 1.0,
    "mixup": 0.15,
    "copy_paste": 0.0,
    "erasing": 0.4,
}


def load_detector(model_name: str):  # type: ignore[no-untyped-def]
    """``yolo11m.pt`` / ``yolo11l.yaml`` → YOLO; ``rtdetr-l.pt`` → RTDETR. ``.pt`` names are downloaded by
    ultralytics on first use (COCO pre-trained); ``.yaml`` builds the architecture from scratch."""
    from ultralytics import RTDETR, YOLO

    name = Path(model_name).name.lower()
    return RTDETR(model_name) if name.startswith("rtdetr") else YOLO(model_name)


def train_detector(config: dict[str, Any], *, config_path: Path | None = None) -> dict[str, Any]:
    """Runs ``model.train`` with the config, then evaluates on val (threshold choice) and test.
    Returns a summary dict (also written to ``<run_dir>/summary.json``)."""
    from evaluation.evaluate_weapons import evaluate_weights

    seed = int(config.get("seed", 42))
    seed_everything(seed, deterministic=bool(config.get("deterministic", True)))
    data_yaml = Path(config["data"]).resolve()
    if not data_yaml.is_file():
        raise FileNotFoundError(f"dataset file {data_yaml} not found; convert the dataset first (see README)")
    model = load_detector(config["model"])
    augment = {**DEFAULT_AUGMENT, **(config.get("augment") or {})}
    train_args: dict[str, Any] = {
        "data": str(data_yaml),
        "epochs": int(config.get("epochs", 100)),
        "imgsz": int(config.get("imgsz", 640)),
        "batch": config.get("batch", 16),
        "workers": int(config.get("workers", 8)),
        "device": config.get("device", 0),
        "project": str(config.get("project", "experiments/runs")),
        "name": str(config.get("name", "detector")),
        "exist_ok": False,
        "seed": seed,
        "deterministic": bool(config.get("deterministic", True)),
        "amp": bool(config.get("amp", True)),
        "optimizer": config.get("optimizer", "AdamW"),
        "lr0": float(config.get("lr0", 0.001)),
        "lrf": float(config.get("lrf", 0.01)),
        "cos_lr": bool(config.get("cos_lr", True)),
        "warmup_epochs": float(config.get("warmup_epochs", 3.0)),
        "weight_decay": float(config.get("weight_decay", 0.0005)),
        "patience": int(config.get("patience", 25)),
        "close_mosaic": int(config.get("close_mosaic", 10)),
        "cache": config.get("cache", False),
        "plots": True,
        "val": True,
        "verbose": True,
        **augment,
    }
    if config.get("freeze") is not None:
        train_args["freeze"] = config["freeze"]
    logger.info("ultralytics train args: %s", train_args)
    results = model.train(**train_args)
    run_dir = Path(results.save_dir)
    best = run_dir / "weights" / "best.pt"
    dump_config(config, run_dir / "train_config.yaml")

    eval_cfg = config.get("eval") or {}
    nms_iou = float(eval_cfg.get("nms_iou", 0.6))
    val_report = evaluate_weights(best, data_yaml, split="val", score_threshold=None, nms_iou=nms_iou, device=config.get("device", 0))
    chosen = float(val_report["chosen_threshold"]) if eval_cfg.get("score_threshold", "auto") == "auto" else float(eval_cfg["score_threshold"])
    test_report = evaluate_weights(best, data_yaml, split="test", score_threshold=chosen, nms_iou=nms_iou, device=config.get("device", 0))
    summary = {
        "task": config.get("task", "weapon_detection"),
        "model": config["model"],
        "run_dir": str(run_dir),
        "best_weights": str(best),
        "data": str(data_yaml),
        "epochs_trained": int(getattr(results, "epoch", train_args["epochs"]) or train_args["epochs"]),
        "chosen_score_threshold": chosen,
        "nms_iou": nms_iou,
        "val": val_report,
        "test": test_report,
        "git_commit": git_commit(Path(__file__).resolve().parents[2]),
        "config_path": str(config_path) if config_path else None,
        "finished_at": utc_now(),
    }
    save_json(summary, run_dir / "summary.json")
    save_json(test_report, run_dir / "metrics_test.json")
    logger.info("test mAP50 %.4f  mAP50-95 %.4f  FP/1000 negative frames %.2f", test_report["mAP50"], test_report["mAP50_95"], test_report["false_positives_per_1000_negative_frames"])
    return summary
