"""Cross-camera person re-identification: train an OSNet-x1.0 or ResNet50-IBN-a embedder with a
BNNeck head and cross-entropy (label smoothing) + batch-hard triplet (+ centre) losses.

    python -m training_scripts.train_reid_extractor --config configs/reid_osnet.yaml train.device=cuda
    python -m training_scripts.train_reid_extractor --config configs/reid_osnet.yaml \
        model.backbone=resnet50_ibn_a sampler.p=16 sampler.k=4

Model selection never touches the published test set: ``data.holdout_identity_fraction`` of the
training identities form a validation query/gallery. ``best.pt`` is the epoch with the best val mAP.
After training: test Rank-1/5/10 and mAP, the cosine bands calibrated on **val** and their precision
measured on **test**, and per-crop latency (``metrics_test.json``).
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

import torch
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
from data_utils.reid_datasets import PKSampler, ReidDataset, ReidItem, holdout_identities, parse_reid_dataset, relabel, train_transform
from evaluation.evaluate_reid import evaluate_split, torch_embedder
from models.losses import ReidCriterion
from models.reid_models import ReidModel, ReidModelSpec

logger = logging.getLogger("train_reid")


def gather_data(config: dict[str, Any]) -> tuple[list[ReidItem], list[ReidItem], list[ReidItem], list[ReidItem], list[ReidItem], dict[str, Any]]:
    data_cfg = config["data"]
    main = parse_reid_dataset(Path(data_cfg["root"]), data_cfg["layout"])
    train = list(main.train)
    offset = max((i.pid for i in train), default=0) + 1
    for extra in data_cfg.get("extra_train", []) or []:
        other = parse_reid_dataset(Path(extra["root"]), extra["layout"])
        train += [ReidItem(i.path, i.pid + offset, i.camid + 1000) for i in other.train]  # distinct ids and cameras
        offset = max(i.pid for i in train) + 1
    train, val_query, val_gallery = holdout_identities(train, float(data_cfg.get("holdout_identity_fraction", 0.1)), int(config.get("seed", 42)))
    info = {"train_images": len(train), "train_identities": len({i.pid for i in train}), "val_query": len(val_query), "val_gallery": len(val_gallery), "test": main.summary()}
    return train, val_query, val_gallery, list(main.query), list(main.gallery), info


def train(config: dict[str, Any], *, config_path: Path | None = None, resume: Path | None = None) -> dict[str, Any]:
    train_cfg, model_cfg, sampler_cfg, eval_cfg = config.get("train", {}), config.get("model", {}), config.get("sampler", {}), config.get("eval", {})
    seed = int(config.get("seed", 42))
    seed_everything(seed, deterministic=bool(config.get("deterministic", False)))
    compute = select_compute(train_cfg.get("device", "auto"), train_cfg.get("precision", "auto"))
    logger.info("compute: %s", compute.describe())

    train_items, val_query, val_gallery, test_query, test_gallery, data_info = gather_data(config)
    train_items, _ = relabel(train_items)
    num_classes = len({i.pid for i in train_items})
    logger.info("data: %s", data_info)
    height, width = int(model_cfg.get("height", 256)), int(model_cfg.get("width", 128))
    augment_cfg = config.get("augment", {}) or {}
    cctv = CctvAugmentConfig(**augment_cfg["cctv"]) if isinstance(augment_cfg.get("cctv"), dict) else (CctvAugmentConfig() if augment_cfg.get("cctv", True) else None)
    dataset = ReidDataset(train_items, train_transform(height, width, cctv=cctv, random_erasing=float(augment_cfg.get("random_erasing", 0.5))))
    sampler = PKSampler([i.pid for i in train_items], p=int(sampler_cfg.get("p", 16)), k=int(sampler_cfg.get("k", 4)), seed=seed)
    workers = int(train_cfg.get("workers", 8))
    loader = DataLoader(dataset, batch_size=sampler.batch_size, sampler=sampler, num_workers=workers, drop_last=True, pin_memory=compute.device.type == "cuda", persistent_workers=workers > 0)

    spec = ReidModelSpec(
        backbone=model_cfg.get("backbone", "osnet_x1_0"),
        num_classes=num_classes,
        embed_dim=int(model_cfg.get("embed_dim", 512)),
        pretrained=bool(model_cfg.get("pretrained", True)),
        pretrained_path=model_cfg.get("pretrained_path"),
        height=height,
        width=width,
    )
    model = ReidModel(spec).to(compute.device)
    criterion = ReidCriterion(
        num_classes,
        spec.embed_dim,
        label_smoothing=float(train_cfg.get("label_smoothing", 0.1)),
        triplet_margin=train_cfg.get("triplet_margin", 0.3),
        triplet_weight=float(train_cfg.get("triplet_weight", 1.0)),
        center_weight=float(train_cfg.get("center_weight", 0.0005)),
    ).to(compute.device)
    lr = float(train_cfg.get("lr", 3.5e-4))
    weight_decay = float(train_cfg.get("weight_decay", 5e-4))
    if train_cfg.get("optimizer", "adam") == "sgd":
        optimizer: torch.optim.Optimizer = torch.optim.SGD(model.parameters(), lr=lr, momentum=0.9, weight_decay=weight_decay, nesterov=True)
    else:
        optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    center_optimizer = torch.optim.SGD(criterion.center.parameters(), lr=float(train_cfg.get("center_lr", 0.5))) if criterion.center is not None else None
    epochs = int(train_cfg.get("epochs", 120))
    steps_per_epoch = max(1, len(loader))
    scheduler = CosineWithWarmup(optimizer, warmup_steps=int(float(train_cfg.get("warmup_epochs", 10)) * steps_per_epoch), total_steps=epochs * steps_per_epoch)
    scaler = torch.amp.GradScaler(enabled=compute.use_scaler)
    eval_every = int(train_cfg.get("eval_every", 10))
    patience_evals = int(train_cfg.get("patience_evals", 4))
    eval_batch = int(eval_cfg.get("batch_size", 128))

    run_dir = make_run_dir(config.get("project", "experiments/runs"), config.get("name", "reid")) if resume is None else resume.parent
    dump_config(config, run_dir / "train_config.yaml")
    start_epoch, best_map, history, bad_evals = 0, -1.0, [], 0
    if resume is not None:
        state = load_checkpoint(resume, compute.device)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        if center_optimizer is not None and state.get("criterion"):
            criterion.load_state_dict(state["criterion"])
        start_epoch, best_map, history = int(state["epoch"]) + 1, float(state["best_metric"]), list(state.get("history", []))

    def evaluate_val() -> dict[str, Any]:
        embedder = torch_embedder(model, compute.device, flip_tta=bool(eval_cfg.get("flip_tta", True)), amp_dtype=compute.amp_dtype)
        return evaluate_split(embedder, val_query, val_gallery, height=height, width=width, batch_size=eval_batch, workers=workers, likely_precision=float(eval_cfg.get("likely_precision", 0.95)), uncertain_precision=float(eval_cfg.get("uncertain_precision", 0.5)))

    for epoch in range(start_epoch, epochs):
        sampler.set_epoch(epoch)
        model.train()
        started = time.perf_counter()
        totals: dict[str, float] = {}
        steps = 0
        for step, (images, pids, _) in enumerate(loader):
            images, pids = images.to(compute.device, non_blocking=True), pids.to(compute.device, non_blocking=True)
            with torch.autocast(device_type=compute.device.type, dtype=compute.amp_dtype or torch.float32, enabled=compute.amp_enabled):
                logits, features, _ = model(images)
            loss, parts = criterion(logits.float(), features.float(), pids)
            optimizer.zero_grad(set_to_none=True)
            if center_optimizer is not None:
                center_optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(train_cfg.get("grad_clip", 5.0)))
            scaler.step(optimizer)
            if center_optimizer is not None:  # the centres' gradient is scaled back by 1/center_weight (original recipe)
                for param in criterion.center.parameters():
                    if param.grad is not None:
                        param.grad.data *= 1.0 / criterion.center_weight
                center_optimizer.step()
            scaler.update()
            scheduler.step()
            for key, value in {"loss": loss.item(), **parts}.items():
                totals[key] = totals.get(key, 0.0) + float(value)
            steps += 1
            if step % 50 == 0:
                logger.info("epoch %d step %d/%d loss %.4f (ce %.3f tri %.3f) lr %.2e", epoch, step, steps_per_epoch, loss.item(), parts["loss_ce"], parts["loss_triplet"], optimizer.param_groups[0]["lr"])
        record: dict[str, Any] = {"epoch": epoch, "train_seconds": round(time.perf_counter() - started, 1), **{k: v / max(1, steps) for k, v in totals.items()}}
        last_epoch = epoch == epochs - 1
        if (epoch + 1) % eval_every == 0 or last_epoch:
            val = evaluate_val()
            record["val"] = val
            logger.info("epoch %d: val rank1 %.4f mAP %.4f", epoch, val["rank1"], val["mAP"])
            if val["mAP"] > best_map:
                best_map, bad_evals = float(val["mAP"]), 0
                save_checkpoint(run_dir / "best.pt", model=model, optimizer=optimizer, scheduler=scheduler, epoch=epoch, best_metric=best_map, config=config, extra={"history": history, "num_classes": num_classes, "criterion": criterion.state_dict(), "val": val})
            else:
                bad_evals += 1
        history.append(record)
        save_checkpoint(run_dir / "last.pt", model=model, optimizer=optimizer, scheduler=scheduler, epoch=epoch, best_metric=best_map, config=config, extra={"history": history, "num_classes": num_classes, "criterion": criterion.state_dict()})
        save_json(history, run_dir / "history.json")
        if bad_evals >= patience_evals:
            logger.info("early stopping: no val mAP improvement in %d evaluations", patience_evals)
            break

    best_state = load_checkpoint(run_dir / "best.pt", compute.device)
    model.load_state_dict(best_state["model"])
    val = best_state.get("val") or evaluate_val()
    bands = {"likely_match": val["calibration"]["likely_threshold"], "uncertain": val["calibration"]["uncertain_threshold"]}
    embedder = torch_embedder(model, compute.device, flip_tta=bool(eval_cfg.get("flip_tta", True)), amp_dtype=compute.amp_dtype)
    test = evaluate_split(embedder, test_query, test_gallery, height=height, width=width, batch_size=eval_batch, workers=workers, cuhk03_protocol=config["data"]["layout"] == "cuhk03", bands=bands)
    summary = {
        "task": "person_reid",
        "run_dir": str(run_dir),
        "best_checkpoint": str(run_dir / "best.pt"),
        "best_epoch": int(best_state["epoch"]),
        "best_val_mAP": best_map,
        "bands_from_val": bands,
        "data": data_info,
        "compute": compute.describe(),
        "test": test,
        "git_commit": git_commit(Path(__file__).resolve().parents[2]),
        "config_path": str(config_path) if config_path else None,
        "finished_at": utc_now(),
    }
    save_json(summary, run_dir / "summary.json")
    save_json({**test, "bands": bands}, run_dir / "metrics_test.json")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("overrides", nargs="*")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    summary = train(load_config(args.config, args.overrides), config_path=args.config, resume=args.resume)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
