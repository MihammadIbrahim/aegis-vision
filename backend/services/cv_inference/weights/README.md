# Model weights

The weights themselves are never committed (`*.onnx` is ignored in git); their metadata files are.

| File | What | In git |
|---|---|---|
| `yolox_tiny.onnx` | YOLOX-Tiny person detector, official ONNX export, Megvii-BaseDetection/YOLOX release 0.1.1rc0. Downloaded 2026-10-31 with the project owner's approval. SHA256 `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7` | No: download it (see `../README.md`) |
| `yolox_tiny.model.json` | Its metadata: format, shapes, class index, checksum, source, licences. The detector checks it | Yes |
| `<name>.onnx` + `<name>.manifest.json` | Models trained in `backend/ml_training` and exported with `python -m export.export_to_onnx …`: weapon detector (`weapon_detection`), fight / violence clip classifier (`violence_classification`), Re-ID embedder (`person_reid`). The manifest records pre-processing, output format, labels, calibrated thresholds, dataset, commit, test metrics and the file's SHA256; the runtime refuses a file that does not match it | manifest yes, `.onnx` no |

**Status 2026-11-15:** no trained weapon, fight or Re-ID weights exist yet. The default manifest
names in `src/config.py` (`weapon-yolo11m`, `fight-swin3d-t`, `reid-osnet-x1-0`) are where the first
exports go:

```
cp ../../../ml_training/export/weapon-yolo11m/weapon-yolo11m.{onnx,manifest.json} .
cp ../../../ml_training/export/fight-swin3d-t/fight-swin3d-t.{onnx,manifest.json} .
cp ../../../ml_training/export/reid-osnet-x1-0/reid-osnet-x1-0.{onnx,manifest.json} .
sha256sum *.onnx   # must equal the "sha256" field of each manifest
```

**Licences:**
- YOLOX-Tiny: code Apache-2.0; weights without a separately stated licence (COCO-trained). Fine for
  development and the FYP; check before shipping them in a commercial product.
- Weapon detectors trained with `ultralytics`: AGPL-3.0 unless an Ultralytics Enterprise licence is
  bought (the runtime does not import ultralytics; it runs the exported graph).
- Fight models: torchvision (BSD-3-Clause) with Kinetics-400 pre-training; Re-ID models trained
  here (OSNet architecture, MIT in torchreid; ResNet50 ImageNet weights BSD-3-Clause).
- Each manifest's `license` field and `ml_training/datasets/README.md` name the dataset licences.

The ONNX files in `tests/fixtures/` (`scale2.onnx`, `*_dummy.onnx`) are generated test graphs, not
models; `tests/fixtures/make_dummy_models.py` rebuilds them.
