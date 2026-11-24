"""One optimisation step for each network on CPU, loss behaviour, and PyTorch → ONNX export with the
parity check and manifest (small inputs; no pretrained weights are downloaded)."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import numpy as np
import pytest
import torch

from data_utils.common import CosineWithWarmup, save_checkpoint, select_compute
from export.export_to_onnx import export_fight, export_reid
from export.manifest import ModelManifest
from export.onnx_detect import decode_ultralytics, letterbox, nms_per_class
from models.fight_models import FightClassifier, FightModelSpec, FightOnnxWrapper
from models.losses import BatchHardTripletLoss, CenterLoss, CrossEntropyLabelSmooth, ReidCriterion
from models.reid_models import OSNet, ReidModel, ReidModelSpec, load_pretrained


def test_compute_selection_on_cpu() -> None:
    compute = select_compute("cpu", "auto")
    assert compute.device.type == "cpu" and not compute.amp_enabled and "fp32" in compute.describe()
    with pytest.raises(RuntimeError):
        if not torch.cuda.is_available():
            select_compute("cuda")
        else:
            raise RuntimeError("cuda present")


def test_cosine_warmup_schedule() -> None:
    opt = torch.optim.SGD([torch.nn.Parameter(torch.zeros(1))], lr=1.0)
    sched = CosineWithWarmup(opt, warmup_steps=2, total_steps=6, final_factor=0.0)
    lrs = []
    for _ in range(6):
        opt.step()
        sched.step()
        lrs.append(round(opt.param_groups[0]["lr"], 4))
    assert lrs[0] == 1.0 and lrs[1] > lrs[2] > lrs[3] > lrs[4] and lrs[-1] == 0.0


def test_fight_model_one_training_step_lowers_the_loss() -> None:
    torch.manual_seed(0)
    model = FightClassifier(FightModelSpec(backbone="r2plus1d_18", pretrained=False, dropout=0.0))
    groups = model.parameter_groups(backbone_lr_scale=0.1)
    assert len(groups) == 2 and len(groups[1]["params"]) == 2  # head: weight + bias
    optimizer = torch.optim.AdamW([{"params": g["params"], "lr": 1e-3 * g["lr_scale"]} for g in groups])
    clips = torch.rand(4, 3, 8, 32, 32)
    labels = torch.tensor([0, 1, 0, 1])
    criterion = torch.nn.CrossEntropyLoss(label_smoothing=0.1)
    model.train()
    losses = []
    for _ in range(3):
        loss = criterion(model(clips), labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        losses.append(loss.item())
    assert losses[-1] < losses[0]
    assert model.violence_probability(clips).shape == (4,)
    with pytest.raises(ValueError):
        model(torch.rand(1, 3, 32, 32))


def test_reid_model_losses_and_one_training_step() -> None:
    torch.manual_seed(0)
    model = ReidModel(ReidModelSpec(backbone="osnet_x0_5", num_classes=4, pretrained=False))
    criterion = ReidCriterion(4, 512, triplet_margin=0.3, center_weight=5e-4)
    optimizer = torch.optim.Adam(list(model.parameters()) + list(criterion.parameters()), lr=1e-3)
    images = torch.rand(8, 3, 128, 64)
    pids = torch.tensor([0, 0, 1, 1, 2, 2, 3, 3])
    model.train()
    logits, pre, post = model(images)
    assert logits.shape == (8, 4) and pre.shape == post.shape == (8, 512)
    loss, parts = criterion(logits, pre, pids)
    assert {"loss_ce", "loss_triplet", "loss_center", "triplet_active_fraction"} <= set(parts)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    model.eval()
    embedding = model(images[:2])
    assert embedding.shape == (2, 512) and torch.allclose(embedding.norm(dim=1), torch.ones(2), atol=1e-5)
    with pytest.raises(ValueError):  # a batch without positives cannot form triplets
        BatchHardTripletLoss()(pre, torch.arange(8))
    smooth = CrossEntropyLabelSmooth(4, 0.1)(logits, pids)
    plain = torch.nn.functional.cross_entropy(logits, pids)
    assert smooth.item() == pytest.approx(plain.item(), abs=0.5) and smooth.item() != pytest.approx(plain.item(), abs=1e-6)
    assert CenterLoss(4, 512)(pre, pids).item() > 0
    assert sum(p.numel() for p in OSNet().parameters()) == pytest.approx(2.17e6, rel=0.02)  # OSNet-x1.0 is 2.2 M params


def test_pretrained_loader_matches_keys_and_shapes(tmp_path: Path) -> None:
    source = OSNet(channels=(32, 128, 192, 256))
    torch.save({"state_dict": {f"module.{k}": v for k, v in source.state_dict().items()}}, tmp_path / "w.pth")
    target = OSNet(channels=(32, 128, 192, 256))
    loaded, skipped = load_pretrained(target, tmp_path / "w.pth")
    assert loaded == len(source.state_dict()) and skipped == 0
    assert torch.equal(target.conv1.conv.weight, source.conv1.conv.weight)


# --- export ---------------------------------------------------------------------------------------------------------------


def _args(task: str, checkpoint: Path, out: Path) -> Namespace:
    return Namespace(task=task, checkpoint=checkpoint, out=out, name=f"test-{task}", version="0.0.1", architecture="", imgsz=None, tolerance=1e-4, no_simplify=False, dataset_notes="unit test", notes="")


def test_reid_export_has_parity_and_a_manifest(tmp_path: Path) -> None:
    config = {"model": {"backbone": "osnet_x0_5", "embed_dim": 512, "height": 128, "width": 64}, "data": {"root": "x", "layout": "market1501"}}
    model = ReidModel(ReidModelSpec(backbone="osnet_x0_5", num_classes=5, pretrained=False, height=128, width=64))
    run = tmp_path / "run"
    save_checkpoint(run / "best.pt", model=model, optimizer=None, scheduler=None, epoch=3, best_metric=0.5, config=config, extra={"num_classes": 5})
    (run / "metrics_test.json").write_text(json.dumps({"rank1": 0.9, "mAP": 0.7, "bands": {"likely_match": 0.81, "uncertain": 0.6}}))
    onnx_path = export_reid(_args("reid", run / "best.pt", tmp_path / "out"))
    manifest = ModelManifest.load(tmp_path / "out" / "test-reid.manifest.json")
    assert onnx_path.is_file() and manifest.export.parity_passed and manifest.export.parity_max_abs_diff < 1e-4
    assert manifest.input.shape == ["batch", 3, 128, 64] and manifest.output.format == "l2-normalized-embedding"
    assert manifest.thresholds == {"likely_match": 0.81, "uncertain": 0.6} and manifest.metrics["rank1"] == 0.9
    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    out = session.run(None, {"images": np.random.rand(3, 3, 128, 64).astype(np.float32)})[0]
    assert out.shape == (3, 512) and np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-4)
    assert [i.shape for i in session.get_inputs()] == [["batch", 3, 128, 64]]


def test_fight_export_has_parity_and_a_manifest(tmp_path: Path) -> None:
    config = {"model": {"backbone": "r2plus1d_18", "dropout": 0.0}, "clip": {"clip_len": 8, "target_fps": 8, "size": 32, "resize_short": 36}, "data": []}
    model = FightClassifier(FightModelSpec(backbone="r2plus1d_18", pretrained=False, dropout=0.0))
    run = tmp_path / "run"
    save_checkpoint(run / "best.pt", model=model, optimizer=None, scheduler=None, epoch=1, best_metric=0.9, config=config)
    (run / "summary.json").write_text(json.dumps({"chosen_threshold": 0.42, "git_commit": "abc123"}))
    onnx_path = export_fight(_args("fight", run / "best.pt", tmp_path / "out"))
    manifest = ModelManifest.load(tmp_path / "out" / "test-fight.manifest.json")
    assert manifest.export.parity_passed and manifest.input.layout == "NCTHW" and manifest.input.clip_len == 8
    assert manifest.thresholds == {"violence_probability": 0.42} and manifest.class_labels == ["normal", "violence"]
    assert manifest.training["commit"] == "abc123" and manifest.sha256 == __import__("data_utils.common", fromlist=["x"]).sha256_of(onnx_path)
    wrapper = FightOnnxWrapper(model).eval()
    assert torch.allclose(wrapper(torch.rand(2, 3, 8, 32, 32)).sum(dim=1), torch.ones(2), atol=1e-5)


@pytest.mark.slow
def test_video_swin_exports_with_the_constant_mask_patch(tmp_path: Path) -> None:
    config = {"model": {"backbone": "swin3d_t", "dropout": 0.0}, "clip": {"clip_len": 16, "target_fps": 8, "size": 64, "resize_short": 72}, "data": []}
    model = FightClassifier(FightModelSpec(backbone="swin3d_t", pretrained=False, dropout=0.0))
    run = tmp_path / "run"
    save_checkpoint(run / "best.pt", model=model, optimizer=None, scheduler=None, epoch=1, best_metric=0.9, config=config)
    export_fight(_args("fight", run / "best.pt", tmp_path / "out"))
    assert ModelManifest.load(tmp_path / "out" / "test-fight.manifest.json").export.parity_passed


def test_ultralytics_decoder_letterbox_and_nms() -> None:
    image = np.zeros((60, 100, 3), np.uint8)
    padded, scale, (pad_x, pad_y) = letterbox(image, 64, 64, 114)
    assert padded.shape == (64, 64, 3) and scale == pytest.approx(0.64) and pad_x == 0 and pad_y == 13
    assert padded[0, 0].tolist() == [114, 114, 114]
    boxes = np.array([[0, 0, 10, 10], [1, 1, 10, 10], [0, 0, 10, 10]], dtype=np.float32)
    kept = nms_per_class(boxes, np.array([0.9, 0.8, 0.7], np.float32), np.array([0, 0, 1]), 0.5, 10)
    assert kept == [0, 2]  # the overlapping same-class box goes; the other class stays
    manifest = ModelManifest.model_validate(
        {
            "name": "m", "version": "1", "task": "weapon_detection", "architecture": "a", "framework": "f", "file": "m.onnx", "sha256": "0" * 64, "size_bytes": 1,
            "input": {"name": "images", "shape": ["batch", 3, 64, 64], "layout": "NCHW", "letterbox_pad_value": 114},
            "output": {"name": "output0", "shape": ["batch", 6, 4], "format": "ultralytics-detect-v1", "box_format": "xywh-pixels"},
            "class_labels": ["handgun", "knife"],
            "export": {"opset": 17, "dynamic_batch": True, "simplified": True, "parity_tolerance": 1e-4, "parity_passed": True, "exported_at": "now", "exporter": "t"},
            "license": "test",
        }
    )
    # one anchor: centre (32, 32) in the 64×64 input, 20×20, knife 0.9; one anchor below threshold
    raw = np.zeros((1, 6, 2), np.float32)
    raw[0, :, 0] = [32, 32, 20, 20, 0.1, 0.9]
    raw[0, :, 1] = [10, 10, 5, 5, 0.2, 0.1]
    boxes_out = decode_ultralytics(raw, manifest=manifest, scale=scale, pad=(pad_x, pad_y), frame_size=(60, 100), input_size=(64, 64), score_threshold=0.5, nms_iou=0.5)
    assert len(boxes_out) == 1 and boxes_out[0].label == "knife" and boxes_out[0].score == pytest.approx(0.9)
    assert (round(boxes_out[0].x1), round(boxes_out[0].y1), round(boxes_out[0].x2), round(boxes_out[0].y2)) == (34, 14, 66, 45)
