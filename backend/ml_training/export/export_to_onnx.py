"""Export a trained model to ONNX with a manifest, for ``services/cv_inference/weights/``.

    python -m export.export_to_onnx weapon --checkpoint experiments/runs/weapon_yolo11m/weights/best.pt \
        --out export/weapon_yolo11m --name weapon-yolo11m --version 1.0.0
    python -m export.export_to_onnx fight  --checkpoint experiments/runs/fight_swin3d_t/best.pt --out export/fight_swin3d_t --name fight-swin3d-t --version 1.0.0
    python -m export.export_to_onnx reid   --checkpoint experiments/runs/reid_osnet_x1_0_market/best.pt --out export/reid_osnet --name reid-osnet-x1-0 --version 1.0.0

Every export: opset 17, a dynamic ``batch`` axis, fixed spatial / temporal shape, ``onnxsim``
simplification, and a numerical parity check of the PyTorch model against ONNX Runtime on random
inputs at batch 1 and 2 (``max |torch − onnx| < tolerance``, default 1e-4). The manifest
(``<name>.manifest.json``) records the input normalisation, output format, class labels, calibrated
thresholds, dataset, training commit/date, test metrics and the parity result. ``metrics_test.json``
and ``summary.json`` are read from the checkpoint's run directory when present.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import torch

from data_utils.common import git_commit, load_json, sha256_of, utc_now
from data_utils.reid_datasets import IMAGENET_MEAN, IMAGENET_STD
from data_utils.video_clips import KINETICS_MEAN, KINETICS_STD
from export.manifest import ExportInfo, InputSpec, ModelManifest, OutputSpec

logger = logging.getLogger("export")
OPSET = 17


class ExportError(RuntimeError):
    pass


# --- Shared steps ----------------------------------------------------------------------------------------------------


def simplify(path: Path) -> bool:
    import onnxsim

    model = onnx.load(str(path))
    simplified, ok = onnxsim.simplify(model)
    if ok:
        onnx.save(simplified, str(path))
    return bool(ok)


def rename_batch_dim(path: Path, input_name: str) -> None:
    """The TorchScript exporter sometimes names the dynamic axis ``unk__N``; call it ``batch``."""
    model = onnx.load(str(path))
    for value in list(model.graph.input) + list(model.graph.output):
        dims = value.type.tensor_type.shape.dim
        if dims and (dims[0].dim_param or dims[0].dim_value == 0):
            dims[0].dim_param = "batch"
    onnx.save(model, str(path))


def parity(path: Path, input_name: str, reference: Any, sample_shape: Sequence[int], *, batches: Sequence[int] = (1, 2), seed: int = 0) -> float:
    """Max absolute difference between ``reference(tensor)`` (NumPy) and ONNX Runtime, over batches."""
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    worst = 0.0
    generator = torch.Generator().manual_seed(seed)
    for batch in batches:
        x = torch.rand((batch, *sample_shape), generator=generator)
        expected = np.asarray(reference(x), dtype=np.float32)
        got = np.asarray(session.run(None, {input_name: x.numpy()})[0], dtype=np.float32)
        if expected.shape != got.shape:
            raise ExportError(f"parity: shapes differ {expected.shape} vs {got.shape}")
        worst = max(worst, float(np.abs(expected - got).max()))
    return worst


def torch_export(module: torch.nn.Module, sample: torch.Tensor, path: Path, *, input_name: str, output_name: str) -> None:
    module.eval()
    torch.onnx.export(
        module,
        (sample,),
        str(path),
        input_names=[input_name],
        output_names=[output_name],
        opset_version=OPSET,
        dynamic_axes={input_name: {0: "batch"}, output_name: {0: "batch"}},
        dynamo=False,
    )
    onnx.checker.check_model(str(path))


def run_dir_metrics(checkpoint: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(metrics_test.json, summary.json) from the run directory, if present."""
    candidates = [checkpoint.parent, checkpoint.parent.parent]
    metrics, summary = {}, {}
    for folder in candidates:
        if not metrics and (folder / "metrics_test.json").is_file():
            metrics = load_json(folder / "metrics_test.json")
        if not summary and (folder / "summary.json").is_file():
            summary = load_json(folder / "summary.json")
    return metrics, summary


def write_manifest(out_dir: Path, onnx_path: Path, *, manifest: ModelManifest) -> Path:
    target = out_dir / f"{onnx_path.stem}.manifest.json"
    manifest.save(target)
    return target


def framework_string(extra: str = "") -> str:
    import torchvision

    base = f"torch {torch.__version__}, torchvision {torchvision.__version__}, onnx {onnx.__version__}"
    return f"{base}, {extra}" if extra else base


# --- Weapon / human detectors (ultralytics) ------------------------------------------------------------------------------


def export_detector(args: argparse.Namespace) -> Path:
    from ultralytics import RTDETR, YOLO

    checkpoint = args.checkpoint.resolve()
    is_rtdetr = checkpoint.name.lower().startswith("rtdetr") or args.architecture.lower().startswith("rtdetr")
    model = RTDETR(str(checkpoint)) if is_rtdetr else YOLO(str(checkpoint))
    imgsz = int(args.imgsz or (model.overrides.get("imgsz") if hasattr(model, "overrides") else None) or 640)
    names = [model.names[i] for i in sorted(model.names)]
    exported = Path(model.export(format="onnx", imgsz=imgsz, opset=OPSET, dynamic=True, simplify=False, half=False, verbose=False))
    onnx_path = args.out / f"{args.name}.onnx"
    args.out.mkdir(parents=True, exist_ok=True)
    shutil.move(str(exported), onnx_path)
    simplified = simplify(onnx_path) if not args.no_simplify else False
    graph = onnx.load(str(onnx_path))
    input_name, output_name = graph.graph.input[0].name, graph.graph.output[0].name
    # Parity against the fused PyTorch model (ultralytics fuses Conv+BN for export).
    torch_model = model.model.float().eval()
    if hasattr(torch_model, "fuse"):
        torch_model.fuse()

    def reference(x: torch.Tensor) -> np.ndarray:
        with torch.no_grad():
            out = torch_model(x)
        return (out[0] if isinstance(out, (tuple, list)) else out).numpy()

    diff = parity(onnx_path, input_name, reference, (3, imgsz, imgsz))
    metrics, summary = run_dir_metrics(checkpoint)
    thresholds = {"nms_iou": float(summary.get("nms_iou", 0.6))}
    if summary.get("chosen_score_threshold") is not None:
        thresholds["score"] = float(summary["chosen_score_threshold"])
    for label, entry in (metrics.get("per_class_best_threshold") or {}).items():
        thresholds[f"score_{label}"] = float(entry["threshold"])
    manifest = ModelManifest(
        name=args.name,
        version=args.version,
        task="weapon_detection" if args.task == "weapon" else "human_detection",
        architecture=args.architecture or ("RT-DETR" if is_rtdetr else f"YOLO ({checkpoint.stem})"),
        framework=framework_string(f"ultralytics {__import__('ultralytics').__version__}"),
        file=onnx_path.name,
        sha256=sha256_of(onnx_path),
        size_bytes=onnx_path.stat().st_size,
        input=InputSpec(name=input_name, shape=["batch", 3, imgsz, imgsz], layout="NCHW", color_order="RGB", scale=1 / 255, mean=[0, 0, 0], std=[1, 1, 1], letterbox_pad_value=114),
        output=OutputSpec(name=output_name, shape=["batch", 4 + len(names), "queries" if is_rtdetr else "anchors"] if not is_rtdetr else ["batch", "queries", 4 + len(names)], format="ultralytics-rtdetr-v1" if is_rtdetr else "ultralytics-detect-v1", box_format="xywh-normalized" if is_rtdetr else "xywh-pixels"),
        class_labels=names,
        thresholds=thresholds,
        dataset={"data_yaml": summary.get("data"), "notes": args.dataset_notes},
        training={"commit": summary.get("git_commit") or git_commit(), "finished_at": summary.get("finished_at"), "run_dir": summary.get("run_dir"), "config": summary.get("config_path"), "checkpoint": str(checkpoint)},
        metrics=metrics,
        export=ExportInfo(opset=OPSET, dynamic_batch=True, simplified=simplified, parity_max_abs_diff=diff, parity_tolerance=args.tolerance, parity_passed=diff < args.tolerance, exported_at=utc_now(), exporter="export.export_to_onnx (ultralytics export + onnxsim)"),
        license="AGPL-3.0 (ultralytics; an Ultralytics Enterprise licence is needed for commercial use). Dataset licences: see datasets/README.md",
        notes=args.notes,
    )
    write_manifest(args.out, onnx_path, manifest=manifest)
    return onnx_path


# --- Fight / violence clip classifier -------------------------------------------------------------------------------------


def export_fight(args: argparse.Namespace) -> Path:
    from evaluation.evaluate_violence import load_fight_checkpoint
    from models.fight_models import FightOnnxWrapper, export_friendly_swin

    checkpoint = args.checkpoint.resolve()
    model, spec, config = load_fight_checkpoint(checkpoint, torch.device("cpu"))
    wrapper = FightOnnxWrapper(model).eval()
    sample = torch.rand(1, 3, spec.clip_len, spec.size, spec.size)
    args.out.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out / f"{args.name}.onnx"
    with export_friendly_swin():
        torch_export(wrapper, sample, onnx_path, input_name="clips", output_name="probabilities")
    rename_batch_dim(onnx_path, "clips")
    simplified = simplify(onnx_path) if not args.no_simplify else False

    def reference(x: torch.Tensor) -> np.ndarray:
        with torch.no_grad():
            return wrapper(x).numpy()

    diff = parity(onnx_path, "clips", reference, (3, spec.clip_len, spec.size, spec.size))
    metrics, summary = run_dir_metrics(checkpoint)
    threshold = summary.get("chosen_threshold", metrics.get("threshold", 0.5))
    manifest = ModelManifest(
        name=args.name,
        version=args.version,
        task="violence_classification",
        architecture=args.architecture or f"{model.spec.backbone} (torchvision, Kinetics-400 pre-trained)",
        framework=framework_string(),
        file=onnx_path.name,
        sha256=sha256_of(onnx_path),
        size_bytes=onnx_path.stat().st_size,
        input=InputSpec(name="clips", shape=["batch", 3, spec.clip_len, spec.size, spec.size], layout="NCTHW", color_order="RGB", scale=1 / 255, mean=list(KINETICS_MEAN), std=list(KINETICS_STD), clip_len=spec.clip_len, target_fps=spec.target_fps, resize_short=spec.resize_short, crop=spec.size),
        output=OutputSpec(name="probabilities", shape=["batch", 2], format="softmax-probabilities"),
        class_labels=["normal", "violence"],
        thresholds={"violence_probability": float(threshold)},
        dataset={"sources": config.get("data"), "videos_per_split": summary.get("videos_per_split"), "notes": args.dataset_notes},
        training={"commit": summary.get("git_commit") or git_commit(), "finished_at": summary.get("finished_at"), "run_dir": summary.get("run_dir"), "config": summary.get("config_path"), "best_epoch": summary.get("best_epoch"), "compute": summary.get("compute"), "checkpoint": str(checkpoint)},
        metrics=metrics,
        export=ExportInfo(opset=OPSET, dynamic_batch=True, simplified=simplified, parity_max_abs_diff=diff, parity_tolerance=args.tolerance, parity_passed=diff < args.tolerance, exported_at=utc_now(), exporter="export.export_to_onnx (torch.onnx + onnxsim)"),
        license="Model code BSD-3-Clause (torchvision); Kinetics-400 pre-training weights per torchvision. Dataset licences: see datasets/README.md",
        notes=args.notes,
    )
    write_manifest(args.out, onnx_path, manifest=manifest)
    return onnx_path


# --- Re-ID embedder ------------------------------------------------------------------------------------------------------------


def export_reid(args: argparse.Namespace) -> Path:
    from evaluation.evaluate_reid import load_reid_checkpoint
    from models.reid_models import ReidOnnxWrapper

    checkpoint = args.checkpoint.resolve()
    model, config = load_reid_checkpoint(checkpoint, torch.device("cpu"))
    wrapper = ReidOnnxWrapper(model).eval()
    height, width = model.spec.height, model.spec.width
    args.out.mkdir(parents=True, exist_ok=True)
    onnx_path = args.out / f"{args.name}.onnx"
    torch_export(wrapper, torch.rand(1, 3, height, width), onnx_path, input_name="images", output_name="embeddings")
    rename_batch_dim(onnx_path, "images")
    simplified = simplify(onnx_path) if not args.no_simplify else False

    def reference(x: torch.Tensor) -> np.ndarray:
        with torch.no_grad():
            return wrapper(x).numpy()

    diff = parity(onnx_path, "images", reference, (3, height, width))
    metrics, summary = run_dir_metrics(checkpoint)
    bands = metrics.get("bands") or summary.get("bands_from_val") or {}
    calibration = metrics.get("calibration") or {}
    thresholds = {
        "likely_match": float(bands.get("likely_match", calibration.get("likely_threshold", 0.75))),
        "uncertain": float(bands.get("uncertain", calibration.get("uncertain_threshold", 0.55))),
    }
    manifest = ModelManifest(
        name=args.name,
        version=args.version,
        task="person_reid",
        architecture=args.architecture or f"{model.spec.backbone} + BNNeck ({model.embed_dim}-D)",
        framework=framework_string(),
        file=onnx_path.name,
        sha256=sha256_of(onnx_path),
        size_bytes=onnx_path.stat().st_size,
        input=InputSpec(name="images", shape=["batch", 3, height, width], layout="NCHW", color_order="RGB", scale=1 / 255, mean=list(IMAGENET_MEAN), std=list(IMAGENET_STD)),
        output=OutputSpec(name="embeddings", shape=["batch", model.embed_dim], format="l2-normalized-embedding"),
        class_labels=[],
        thresholds=thresholds,
        dataset={"root": config.get("data", {}).get("root"), "layout": config.get("data", {}).get("layout"), "summary": summary.get("data"), "notes": args.dataset_notes},
        training={"commit": summary.get("git_commit") or git_commit(), "finished_at": summary.get("finished_at"), "run_dir": summary.get("run_dir"), "config": summary.get("config_path"), "best_epoch": summary.get("best_epoch"), "compute": summary.get("compute"), "checkpoint": str(checkpoint)},
        metrics=metrics,
        export=ExportInfo(opset=OPSET, dynamic_batch=True, simplified=simplified, parity_max_abs_diff=diff, parity_tolerance=args.tolerance, parity_passed=diff < args.tolerance, exported_at=utc_now(), exporter="export.export_to_onnx (torch.onnx + onnxsim)"),
        license="OSNet: architecture from Zhou et al. 2019 (MIT in torchreid), trained here; ResNet50-IBN: torchvision ImageNet weights (BSD-3-Clause). Dataset licences: see datasets/README.md",
        notes=args.notes,
    )
    write_manifest(args.out, onnx_path, manifest=manifest)
    return onnx_path


# --- CLI -----------------------------------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task", choices=["weapon", "human", "fight", "reid"])
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", required=True, help="file stem and manifest name, e.g. weapon-yolo11m")
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", default="")
    parser.add_argument("--imgsz", type=int, help="detectors: export size (default: the training size)")
    parser.add_argument("--tolerance", type=float, default=1e-4)
    parser.add_argument("--no-simplify", action="store_true")
    parser.add_argument("--dataset-notes", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--allow-parity-failure", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    exporter = {"weapon": export_detector, "human": export_detector, "fight": export_fight, "reid": export_reid}[args.task]
    onnx_path = exporter(args)
    manifest = ModelManifest.load(args.out / f"{args.name}.manifest.json")
    print(json.dumps({"onnx": str(onnx_path), "manifest": str(args.out / f"{args.name}.manifest.json"), "sha256": manifest.sha256, "parity_max_abs_diff": manifest.export.parity_max_abs_diff, "parity_passed": manifest.export.parity_passed, "simplified": manifest.export.simplified}, indent=2))
    if not manifest.export.parity_passed and not args.allow_parity_failure:
        print(f"parity check failed: {manifest.export.parity_max_abs_diff} >= {args.tolerance}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
