"""Generates the tiny ONNX graphs the tests use in place of trained weights, with manifests in the
same format ``ml_training/export`` writes. They are NOT vision models: each reduces the input to
its mean per channel and maps that through fixed weights, so the output depends on the image in a
known way and the detectors' pre/post-processing can be checked without any download.

* ``weapon_dummy.onnx``   images [B,3,64,64] → output0 [B, 4+2, 3] (ultralytics-detect-v1 layout)
* ``fight_dummy.onnx``    clips  [B,3,8,32,32] → probabilities [B,2] (softmax)
* ``reid_dummy.onnx``     images [B,3,64,32] → embeddings [B,512] (not normalised; the embedder does it)

Run with an environment that has ``onnx`` (``backend/ml_training/.venv``):
    ../../ml_training/.venv/bin/python tests/fixtures/make_dummy_models.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

HERE = Path(__file__).resolve().parent
OPSET = 17


def _save(model: onnx.ModelProto, name: str) -> Path:
    model.ir_version = 8
    onnx.checker.check_model(model)
    path = HERE / name
    onnx.save(model, path)
    return path


def _manifest(path: Path, **fields: object) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    data = {
        "manifest_version": 1,
        "version": "test",
        "framework": "onnx test graph",
        "file": path.name,
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "dataset": {"note": "none: synthetic test graph"},
        "training": {"commit": None, "finished_at": None},
        "metrics": {},
        "export": {"opset": OPSET, "dynamic_batch": True, "simplified": False, "parity_max_abs_diff": 0.0, "parity_tolerance": 1e-4, "parity_passed": True, "exported_at": "2026-11-14T00:00:00+00:00", "exporter": "tests/fixtures/make_dummy_models.py"},
        "license": "test fixture, not a model",
        **fields,
    }
    (HERE / f"{path.stem}.manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def weapon() -> None:
    """mean RGB (3) → Gemm → 18 values → reshape [B,6,3]. Anchor 0: a box at the image centre whose
    handgun score is the mean red channel (so a bright-red image yields a detection); anchor 1: a knife
    with a fixed 0.9 score at the top-left; anchor 2: everything 0."""
    w = np.zeros((3, 18), np.float32)
    b = np.zeros((18,), np.float32)
    # layout per anchor a: indices [0a cx, 1a cy, 2a w, 3a h, 4a handgun, 5a knife] with row = feature*3 + anchor
    def idx(feature: int, anchor: int) -> int:
        return feature * 3 + anchor

    b[idx(0, 0)], b[idx(1, 0)], b[idx(2, 0)], b[idx(3, 0)] = 32, 32, 20, 24
    w[0, idx(4, 0)] = 1.0  # handgun score = mean of channel 0 (RGB after preprocessing → red)
    b[idx(0, 1)], b[idx(1, 1)], b[idx(2, 1)], b[idx(3, 1)], b[idx(5, 1)] = 8, 8, 10, 10, 0.9
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["mean"], axes=[2, 3], keepdims=0),
        helper.make_node("Gemm", ["mean", "W", "B"], ["flat"]),
        helper.make_node("Reshape", ["flat", "shape"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes,
        "weapon_dummy",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, ["batch", 3, 64, 64])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, ["batch", 6, 3])],
        initializer=[numpy_helper.from_array(w, "W"), numpy_helper.from_array(b, "B"), numpy_helper.from_array(np.array([-1, 6, 3], np.int64), "shape")],
    )
    path = _save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)], producer_name="cv_inference-tests"), "weapon_dummy.onnx")
    _manifest(
        path,
        name="weapon-dummy",
        task="weapon_detection",
        architecture="test graph",
        input={"name": "images", "shape": ["batch", 3, 64, 64], "layout": "NCHW", "color_order": "RGB", "scale": 1 / 255, "mean": [0, 0, 0], "std": [1, 1, 1], "letterbox_pad_value": 114},
        output={"name": "output0", "shape": ["batch", 6, 3], "format": "ultralytics-detect-v1", "box_format": "xywh-pixels"},
        class_labels=["handgun", "knife"],
        thresholds={"score": 0.4, "nms_iou": 0.6, "score_handgun": 0.5, "score_knife": 0.8},
    )


def fight() -> None:
    """mean over channels/time/space (3) → Gemm → 2 logits → Softmax. The violence logit grows with
    the mean of channel 0, so a red clip is 'violence' and a dark clip is 'normal'."""
    w = np.array([[-6.0, 6.0], [0.0, 0.0], [0.0, 0.0]], np.float32)
    b = np.array([1.0, -1.0], np.float32)
    nodes = [
        helper.make_node("ReduceMean", ["clips"], ["mean"], axes=[2, 3, 4], keepdims=0),
        helper.make_node("Gemm", ["mean", "W", "B"], ["logits"]),
        helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
    ]
    graph = helper.make_graph(
        nodes,
        "fight_dummy",
        [helper.make_tensor_value_info("clips", TensorProto.FLOAT, ["batch", 3, 8, 32, 32])],
        [helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["batch", 2])],
        initializer=[numpy_helper.from_array(w, "W"), numpy_helper.from_array(b, "B")],
    )
    path = _save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)], producer_name="cv_inference-tests"), "fight_dummy.onnx")
    _manifest(
        path,
        name="fight-dummy",
        task="violence_classification",
        architecture="test graph",
        input={"name": "clips", "shape": ["batch", 3, 8, 32, 32], "layout": "NCTHW", "color_order": "RGB", "scale": 1 / 255, "mean": [0, 0, 0], "std": [1, 1, 1], "clip_len": 8, "target_fps": 5.0, "resize_short": 36, "crop": 32},
        output={"name": "probabilities", "shape": ["batch", 2], "format": "softmax-probabilities"},
        class_labels=["normal", "violence"],
        thresholds={"violence_probability": 0.5},
    )


def reid() -> None:
    """mean RGB (3) → Gemm with a fixed random 3×512 matrix → 512-D vector. Crops with similar colours
    get similar embeddings, so same-colour 'people' match across cameras in the tests."""
    rng = np.random.default_rng(7)
    w = rng.normal(size=(3, 512)).astype(np.float32)
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["mean"], axes=[2, 3], keepdims=0),
        helper.make_node("MatMul", ["mean", "W"], ["embeddings"]),
    ]
    graph = helper.make_graph(
        nodes,
        "reid_dummy",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, ["batch", 3, 64, 32])],
        [helper.make_tensor_value_info("embeddings", TensorProto.FLOAT, ["batch", 512])],
        initializer=[numpy_helper.from_array(w, "W")],
    )
    path = _save(helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)], producer_name="cv_inference-tests"), "reid_dummy.onnx")
    _manifest(
        path,
        name="reid-dummy",
        task="person_reid",
        architecture="test graph",
        input={"name": "images", "shape": ["batch", 3, 64, 32], "layout": "NCHW", "color_order": "RGB", "scale": 1 / 255, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
        output={"name": "embeddings", "shape": ["batch", 512], "format": "l2-normalized-embedding"},
        class_labels=[],
        thresholds={"likely_match": 0.9, "uncertain": 0.6},
    )


if __name__ == "__main__":
    weapon()
    fight()
    reid()
    print("wrote", sorted(p.name for p in HERE.glob("*_dummy*")))
