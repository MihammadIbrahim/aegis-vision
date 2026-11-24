"""Weapon detection: train YOLO11 or RT-DETR with ultralytics on a converted dataset.

    # 1. Convert / split a dataset once (see datasets/README.md for sources)
    python -m training_scripts.train_weapon_detector prepare --config configs/weapon_yolo11.yaml

    # 2. Train (RTX 4080/4090: batch 32 at 640 px for yolo11m; 16 for yolo11l / rtdetr-l)
    python -m training_scripts.train_weapon_detector train --config configs/weapon_yolo11.yaml \
        model=yolo11m.pt epochs=100 batch=32 device=0

The run directory holds ``weights/best.pt``, ``weights/last.pt``, ultralytics' plots, our
``metrics_test.json`` (per-class precision / recall / F1, AP@0.50, AP@0.50:0.95, false positives per
1,000 negative frames, latency) and ``summary.json``. Export with ``export.export_to_onnx``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from data_utils.common import load_config
from data_utils.weapon_dataset import convert_dataset, verify_layout
from training_scripts._ultralytics_common import train_detector


def prepare(config: dict[str, object]) -> dict[str, object]:
    """Converts every entry of ``config['sources']`` into the YOLO layout at ``config['dataset_root']``."""
    out_root = Path(str(config["dataset_root"]))
    class_names = list(config["classes"])  # type: ignore[call-overload]
    reports = {}
    for source in config.get("sources", []):  # type: ignore[union-attr]
        report = convert_dataset(
            Path(source["path"]),
            source["format"],
            out_root,
            class_names=class_names,
            class_map=dict(source["class_map"]),
            source_names=source.get("source_names"),
            images_dir=Path(source["images_dir"]) if source.get("images_dir") else None,
            ratios=tuple(config.get("split_ratios", (0.8, 0.1, 0.1))),  # type: ignore[arg-type]
            seed=int(config.get("seed", 42)),  # type: ignore[call-overload]
            keep_negatives=bool(source.get("keep_negatives", True)),
            respect_source_splits=bool(source.get("respect_source_splits", True)),
        )
        reports[source["path"]] = report.as_dict()
    summary = {"sources": reports, "layout": verify_layout(out_root), "data_yaml": str(out_root / "data.yaml")}
    (out_root / "conversion_report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["prepare", "train", "verify"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("overrides", nargs="*", help="key=value overrides, e.g. epochs=50 model=yolo11l.pt")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(args.config, args.overrides)
    if args.command == "prepare":
        print(json.dumps(prepare(config), indent=2))
        return 0
    if args.command == "verify":
        print(json.dumps(verify_layout(Path(str(config["dataset_root"]))), indent=2))
        return 0
    summary = train_detector(config, config_path=args.config)
    print(json.dumps({k: summary[k] for k in ("run_dir", "best_weights", "chosen_score_threshold")}, indent=2))
    print(json.dumps(summary["test"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
