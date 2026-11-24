"""End-to-end on CPU with synthetic data: train one epoch, pick thresholds, score the test split,
export to ONNX and evaluate the ONNX model with the same code paths the GPU server will use."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from data_utils.video_clips import ClipSpec, list_videos
from evaluation.evaluate_reid import onnx_embedder
from evaluation.evaluate_violence import evaluate_items, onnx_scorer
from export.export_to_onnx import export_fight, export_reid
from export.manifest import ModelManifest
from training_scripts.train_fight_detector import train as train_fight
from training_scripts.train_reid_extractor import train as train_reid


def test_fight_pipeline_end_to_end(rwf_dataset: Path, tmp_path: Path) -> None:
    config = {
        "name": "fight_smoke", "project": str(tmp_path / "runs"), "seed": 1,
        "model": {"backbone": "r2plus1d_18", "pretrained": False, "dropout": 0.1},
        "clip": {"clip_len": 8, "target_fps": 10, "size": 32, "resize_short": 36, "windows_per_video_eval": 2},
        "data": [{"root": str(rwf_dataset), "layout": "rwf2000"}],
        "augment": {"low_light_p": 0.5},
        "train": {"epochs": 2, "batch_size": 4, "workers": 0, "device": "cpu", "precision": "fp32", "lr": 1e-3, "warmup_epochs": 0, "patience": 5},
        "eval": {"threshold": "auto"},
    }
    summary = train_fight(config)
    run = Path(summary["run_dir"])
    assert (run / "best.pt").is_file() and (run / "last.pt").is_file() and (run / "history.json").is_file()
    assert 4 <= summary["test"]["clips"] <= 4 * 2 and summary["test"]["videos"] == 4
    assert set(summary["test"]["clip_level"]) >= {"precision", "recall", "f1", "roc_auc", "pr_auc", "confusion", "false_alarm_rate"}
    assert summary["test"]["latency_ms_per_clip_mean"] > 0
    args = Namespace(task="fight", checkpoint=run / "best.pt", out=tmp_path / "export", name="fight-smoke", version="0.0.1", architecture="", imgsz=None, tolerance=1e-4, no_simplify=False, dataset_notes="", notes="")
    export_fight(args)
    manifest = ModelManifest.load(tmp_path / "export" / "fight-smoke.manifest.json")
    assert manifest.export.parity_passed and manifest.thresholds["violence_probability"] == summary["chosen_threshold"]
    scorer, spec, thresholds = onnx_scorer(tmp_path / "export" / "fight-smoke.manifest.json")
    assert (spec.clip_len, spec.size, spec.target_fps) == (8, 32, 10.0)  # read back from the manifest
    test_items = [i for i in list_videos(rwf_dataset, "rwf2000") if i.split == "test"]
    same_windows = ClipSpec(**config["clip"])  # the manifest does not carry the evaluation window count
    onnx_report = evaluate_items(scorer, test_items, same_windows, threshold=thresholds["violence_probability"], workers=0)
    assert onnx_report["clip_level"]["confusion"] == summary["test"]["clip_level"]["confusion"]


def test_reid_pipeline_end_to_end(market_dataset: Path, tmp_path: Path) -> None:
    config = {
        "name": "reid_smoke", "project": str(tmp_path / "runs"), "seed": 1,
        "model": {"backbone": "osnet_x0_5", "embed_dim": 512, "pretrained": False, "height": 128, "width": 64},
        "data": {"root": str(market_dataset), "layout": "market1501", "holdout_identity_fraction": 0.34},
        "sampler": {"p": 2, "k": 2},
        "augment": {"cctv": True, "random_erasing": 0.5},
        "train": {"epochs": 2, "lr": 1e-3, "warmup_epochs": 0, "workers": 0, "device": "cpu", "precision": "fp32", "eval_every": 1, "center_weight": 5e-4},
        "eval": {"flip_tta": True, "batch_size": 8},
    }
    summary = train_reid(config)
    run = Path(summary["run_dir"])
    assert (run / "best.pt").is_file() and summary["best_val_mAP"] >= 0
    test = summary["test"]
    assert {"rank1", "rank5", "mAP", "calibration", "bands_applied", "latency_ms_per_crop_mean"} <= set(test)
    assert 0 <= test["rank1"] <= 1 and test["query_images"] == 3
    args = Namespace(task="reid", checkpoint=run / "best.pt", out=tmp_path / "export", name="reid-smoke", version="0.0.1", architecture="", imgsz=None, tolerance=1e-4, no_simplify=False, dataset_notes="", notes="")
    export_reid(args)
    manifest = ModelManifest.load(tmp_path / "export" / "reid-smoke.manifest.json")
    assert manifest.export.parity_passed and manifest.thresholds["likely_match"] >= manifest.thresholds["uncertain"]
    embedder, (height, width), _ = onnx_embedder(tmp_path / "export" / "reid-smoke.manifest.json")
    assert (height, width) == (128, 64)


@pytest.mark.slow
def test_ultralytics_detector_export_from_an_untrained_model(tmp_path: Path) -> None:
    """Builds yolo11n from its YAML (no download), exports it and checks the manifest and parity."""
    from ultralytics import YOLO

    from export.export_to_onnx import export_detector

    model = YOLO("yolo11n.yaml")
    model.model.names = {0: "handgun", 1: "knife"}
    model.model.nc = 2  # type: ignore[attr-defined]
    checkpoint = tmp_path / "best.pt"
    model.save(str(checkpoint))
    args = Namespace(task="weapon", checkpoint=checkpoint, out=tmp_path / "export", name="weapon-smoke", version="0.0.1", architecture="", imgsz=320, tolerance=1e-3, no_simplify=False, dataset_notes="", notes="")
    export_detector(args)
    manifest = ModelManifest.load(tmp_path / "export" / "weapon-smoke.manifest.json")
    assert manifest.output.format == "ultralytics-detect-v1" and manifest.input.shape == ["batch", 3, 320, 320]
    assert manifest.export.parity_passed and manifest.license.startswith("AGPL")
