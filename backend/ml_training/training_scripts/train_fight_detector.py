"""Fight / violence detection: fine-tune a Kinetics-pretrained video network on 16-frame clips.

    python -m training_scripts.train_fight_detector --config configs/fight_video_swin.yaml \
        train.device=cuda train.batch_size=16 model.backbone=swin3d_t

Per epoch: AMP training (bf16 on RTX 40-series), AdamW with a lower learning rate for the trunk,
cosine schedule with warm-up, gradient clipping, balanced sampling of violence / normal clips, CCTV
clip augmentation; then validation (clip-level ROC-AUC, PR-AUC, F1). ``best.pt`` is the epoch with
the best ``train.select_by`` metric on **val**; ``last.pt`` allows ``--resume``. After training, the
operating threshold is chosen on val and the **test** split is scored at clip and video level
(``metrics_test.json``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from data_utils.cctv_augment import CctvAugmentConfig
from data_utils.common import (
    CosineWithWarmup,
    dump_config,
    git_commit,
    load_checkpoint,
    load_config,
    make_run_dir,
    save_checkpoint,
    save_json,
    seed_everything,
    select_compute,
    utc_now,
)
from data_utils.video_clips import ClipDataset, ClipSpec, VideoItem, collate_clips, list_videos, make_balanced_sampler, split_counts
from evaluation.evaluate_violence import evaluate_items, score_dataset, torch_scorer
from evaluation.metrics_classification import best_threshold, binary_report
from models.fight_models import FightClassifier, FightModelSpec

logger = logging.getLogger("train_fight")


def gather_items(config: dict[str, Any]) -> list[VideoItem]:
    items: list[VideoItem] = []
    ratios = tuple(config.get("split_ratios", (0.7, 0.15, 0.15)))
    for source in config["data"]:
        items += list_videos(Path(source["root"]), source["layout"], ratios=ratios, seed=int(config.get("seed", 42)), ucf_extended=bool(source.get("ucf_extended", False)))  # type: ignore[arg-type]
    return items


def build_model(config: dict[str, Any]) -> FightClassifier:
    model_cfg = config.get("model", {})
    spec = FightModelSpec(
        backbone=model_cfg.get("backbone", "swin3d_t"),
        num_classes=2,
        pretrained=bool(model_cfg.get("pretrained", True)),
        dropout=float(model_cfg.get("dropout", 0.3)),
        clip_len=int(config.get("clip", {}).get("clip_len", 16)),
        size=int(config.get("clip", {}).get("size", 160)),
    )
    return FightClassifier(spec)


def train(config: dict[str, Any], *, config_path: Path | None = None, resume: Path | None = None) -> dict[str, Any]:
    train_cfg = config.get("train", {})
    seed = int(config.get("seed", 42))
    seed_everything(seed, deterministic=bool(config.get("deterministic", False)))
    compute = select_compute(train_cfg.get("device", "auto"), train_cfg.get("precision", "auto"))
    logger.info("compute: %s", compute.describe())

    spec = ClipSpec(**{k: v for k, v in config.get("clip", {}).items() if k in ClipSpec.__dataclass_fields__})
    items = gather_items(config)
    counts = split_counts(items)
    logger.info("videos per split: %s", counts)
    augment = CctvAugmentConfig(**config["augment"]) if config.get("augment") else None
    train_items = [i for i in items if i.split == "train"]
    val_items = [i for i in items if i.split == "val"]
    test_items = [i for i in items if i.split == "test"]
    if not train_items or not val_items:
        raise ValueError("train and val splits must both be non-empty")
    train_set = ClipDataset(train_items, spec, train=True, augment=augment, seed=seed)
    val_set = ClipDataset(val_items, spec, train=False)
    batch_size = int(train_cfg.get("batch_size", 16))
    workers = int(train_cfg.get("workers", 8))
    sampler = make_balanced_sampler(train_set.labels, seed) if train_cfg.get("balanced_sampling", True) else None
    loader = DataLoader(train_set, batch_size=batch_size, sampler=sampler, shuffle=sampler is None, num_workers=workers, collate_fn=collate_clips, drop_last=True, pin_memory=compute.device.type == "cuda", persistent_workers=workers > 0)

    model = build_model(config).to(compute.device)
    if compute.device.type == "cuda":
        model = model.to(memory_format=torch.channels_last_3d)
    base_lr = float(train_cfg.get("lr", 1e-4))
    groups = [{"params": g["params"], "lr": base_lr * float(g["lr_scale"])} for g in model.parameter_groups(backbone_lr_scale=float(train_cfg.get("backbone_lr_scale", 0.1)))]
    optimizer = torch.optim.AdamW(groups, lr=base_lr, weight_decay=float(train_cfg.get("weight_decay", 0.05)))
    epochs = int(train_cfg.get("epochs", 30))
    steps_per_epoch = max(1, len(loader))
    scheduler = CosineWithWarmup(optimizer, warmup_steps=int(float(train_cfg.get("warmup_epochs", 2)) * steps_per_epoch), total_steps=epochs * steps_per_epoch)
    scaler = torch.amp.GradScaler(enabled=compute.use_scaler)
    criterion = nn.CrossEntropyLoss(label_smoothing=float(train_cfg.get("label_smoothing", 0.1)))
    select_by = train_cfg.get("select_by", "roc_auc")
    patience = int(train_cfg.get("patience", 8))
    grad_clip = float(train_cfg.get("grad_clip", 1.0))

    run_dir = make_run_dir(config.get("project", "experiments/runs"), config.get("name", "fight")) if resume is None else resume.parent
    dump_config(config, run_dir / "train_config.yaml")
    start_epoch, best_metric, history = 0, -1.0, []
    if resume is not None:
        state = load_checkpoint(resume, compute.device)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start_epoch, best_metric = int(state["epoch"]) + 1, float(state["best_metric"])
        history = list(state.get("history", []))
        logger.info("resumed from %s at epoch %d", resume, start_epoch)

    bad_epochs = 0
    for epoch in range(start_epoch, epochs):
        train_set.set_epoch(epoch)
        model.train()
        started = time.perf_counter()
        total_loss, correct, seen = 0.0, 0, 0
        for step, (clips, labels, _) in enumerate(loader):
            clips = clips.to(compute.device, non_blocking=True)
            if compute.device.type == "cuda":
                clips = clips.to(memory_format=torch.channels_last_3d)
            labels = labels.to(compute.device, non_blocking=True)
            with torch.autocast(device_type=compute.device.type, dtype=compute.amp_dtype or torch.float32, enabled=compute.amp_enabled):
                logits = model(clips)
                loss = criterion(logits.float(), labels)
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            total_loss += loss.item() * len(labels)
            correct += int((logits.argmax(1) == labels).sum())
            seen += len(labels)
            if step % 20 == 0:
                logger.info("epoch %d step %d/%d loss %.4f lr %.2e", epoch, step, steps_per_epoch, loss.item(), optimizer.param_groups[-1]["lr"])
        train_time = time.perf_counter() - started
        scores, val_labels, _, _ = score_dataset(torch_scorer(model, compute.device, compute.amp_dtype), val_set, batch_size=batch_size, workers=workers)
        val_report = binary_report(scores, val_labels, 0.5)
        metric_value = {"roc_auc": val_report.roc_auc, "pr_auc": val_report.pr_auc, "f1": val_report.f1}[select_by]
        metric_value = -1.0 if np.isnan(metric_value) else float(metric_value)
        record = {"epoch": epoch, "train_loss": total_loss / max(1, seen), "train_accuracy": correct / max(1, seen), "train_seconds": round(train_time, 1), "val": val_report.as_dict()}
        history.append(record)
        logger.info("epoch %d: loss %.4f acc %.3f | val roc_auc %.4f pr_auc %.4f f1 %.4f", epoch, record["train_loss"], record["train_accuracy"], val_report.roc_auc, val_report.pr_auc, val_report.f1)
        improved = metric_value > best_metric
        if improved:
            best_metric, bad_epochs = metric_value, 0
            save_checkpoint(run_dir / "best.pt", model=model, optimizer=optimizer, scheduler=scheduler, epoch=epoch, best_metric=best_metric, config=config, extra={"history": history})
        else:
            bad_epochs += 1
        save_checkpoint(run_dir / "last.pt", model=model, optimizer=optimizer, scheduler=scheduler, epoch=epoch, best_metric=best_metric, config=config, extra={"history": history})
        save_json(history, run_dir / "history.json")
        if bad_epochs >= patience:
            logger.info("early stopping: no %s improvement for %d epochs", select_by, patience)
            break

    best_state = load_checkpoint(run_dir / "best.pt", compute.device)
    model.load_state_dict(best_state["model"])
    scorer = torch_scorer(model, compute.device, compute.amp_dtype)
    eval_cfg = config.get("eval", {})
    val_scores, val_labels, _, _ = score_dataset(scorer, val_set, batch_size=batch_size, workers=workers)
    threshold = best_threshold(val_scores, val_labels, max_false_alarm_rate=eval_cfg.get("max_false_alarm_rate")) if eval_cfg.get("threshold", "auto") == "auto" else float(eval_cfg["threshold"])
    test_report = evaluate_items(scorer, test_items, spec, threshold=threshold, batch_size=batch_size, workers=workers, video_reduce=eval_cfg.get("video_reduce", "mean")) if test_items else {}
    summary = {
        "task": "violence_classification",
        "run_dir": str(run_dir),
        "best_checkpoint": str(run_dir / "best.pt"),
        "best_epoch": int(best_state["epoch"]),
        f"best_val_{select_by}": best_metric,
        "chosen_threshold": threshold,
        "videos_per_split": counts,
        "compute": compute.describe(),
        "test": test_report,
        "git_commit": git_commit(Path(__file__).resolve().parents[2]),
        "config_path": str(config_path) if config_path else None,
        "finished_at": utc_now(),
    }
    save_json(summary, run_dir / "summary.json")
    save_json(test_report, run_dir / "metrics_test.json")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", type=Path, help="a last.pt to continue from")
    parser.add_argument("overrides", nargs="*")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = train(load_config(args.config, args.overrides), config_path=args.config, resume=args.resume)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
