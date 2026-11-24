# ml_training — model training, evaluation and ONNX export

**Status (2026-11-15):** the three training pipelines, their evaluation protocols and the ONNX
exporter are **built and tested on CPU with synthetic data** (30 tests). **No model has been trained
on a real dataset yet**: no dataset is downloaded here, and no accuracy figure exists. The first
real runs are planned for the RTX 4080/4090 server (runbook below). Until then the runtime
(`services/cv_inference`) has adapters for the weapon, fight and Re-ID models but no weights.

| Pipeline | Model | Data | Metrics produced |
|---|---|---|---|
| Weapon detection | YOLO11 (m/l) or RT-DETR-L via `ultralytics`, CCTV augmentations | OD-WeaponDetection, Sohas, YouTube-GDD, any YOLO/COCO/VOC set | per class P / R / F1, AP@0.50, AP@0.50:0.95, mAP, **false positives per 1,000 negative frames**, ms/frame |
| Fight / violence | Video Swin-T (default), Swin-S, MViT v2-S or R(2+1)D-18, Kinetics-400 pre-trained; 16-frame clips read with `decord` | RWF-2000, RLVS, UCF-Crime (Fighting/Assault vs Normal) | clip and video level: P / R / F1, accuracy, ROC-AUC, PR-AUC, confusion matrix, **false-alarm rate on normal clips**, ms/clip |
| Person Re-ID | OSNet-x1.0 (built here) or ResNet50-IBN-a, BNNeck, 512-D; CE (label smoothing) + batch-hard triplet + centre loss, PK sampling | Market-1501, MSMT17, CUHK03 | Rank-1/5/10, mAP (Market protocol), **cosine bands** LIKELY / UNCERTAIN / NO RELIABLE MATCH calibrated to a target precision, ms/crop |

Every pipeline: deterministic hashed splits (the test split is never used for training or model
selection), AMP (bf16 on RTX 40-series), cosine schedule with warm-up, `best.pt` / `last.pt`,
`--resume`, a `summary.json` and `metrics_test.json` per run, and one export command that writes
`<name>.onnx` + `<name>.manifest.json` (opset 17, dynamic batch, `onnxsim`, PyTorch↔ONNX parity
< 1e-4). The manifest carries pre-processing, output format, labels, calibrated thresholds,
dataset, commit/date and test metrics, and the runtime refuses weights whose checksum or shape
differs from it.

## Layout

```
ml_training/
├── configs/            weapon_yolo11.yaml  fight_video_swin.yaml  reid_osnet.yaml  human_yolo.yaml
├── data_utils/         common (config, seeds, splits, AMP, checkpoints), cctv_augment, weapon_dataset,
│                       video_clips (decord/OpenCV reader, clip datasets), reid_datasets (parsers, PK sampler)
├── models/             fight_models (torchvision video nets), reid_models (OSNet, ResNet50-IBN, BNNeck), losses
├── training_scripts/   train_weapon_detector, train_human_detector, train_fight_detector, train_reid_extractor
├── evaluation/         metrics_detection / _classification / _reid, evaluate_weapons / _violence / _reid /
│                       _human_detector / _tracking (MOTA, IDF1)
├── export/             export_to_onnx (+ convert_to_onnx alias), manifest, onnx_detect (NumPy YOLO decoder)
├── tests/              30 CPU tests on synthetic data (parsers, metrics, one training step, export parity,
│                       end-to-end train → export → ONNX evaluation)
├── datasets/           README with sources, licences, layouts; raw/ and converted sets are git-ignored
├── chatbot/            D-027 chatbot QLoRA (Qwen2.5-7B / 14B on the RTX 5080): dataset_builder, train_chatbot_qlora,
│                       evaluate_chatbot, metrics, export_to_ollama, pii — runbook in chatbot/README.md
└── experiments/        runs/ (git-ignored) and model_registry.md
```

**AI Investigation & Security Analytics Chatbot (decision D-027, 2026-11-19):** the fine-tuning and evaluation
pipeline for the read-only chatbot lives in [`chatbot/`](chatbot/README.md) (configs
`configs/chatbot_qwen25_7b_qlora.yaml` and `configs/chatbot_qwen25_14b_qlora.yaml`, requirements in
`requirements-chatbot.txt`). Built and CPU-tested (9 tests); no weights downloaded and no GPU run yet.

## GPU server runbook (RTX 4080 16 GB / RTX 4090 24 GB, Linux)

### 1. Environment (once)

```bash
# NVIDIA driver ≥ 550 and CUDA 12.x already installed; check:
nvidia-smi
cd backend/ml_training
python3.11 -m venv .venv && source .venv/bin/activate      # or: uv venv --python 3.11 .venv
pip install --upgrade pip
# PyTorch with CUDA 12.4 wheels (pick the tag matching the driver; see pytorch.org/get-started)
pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements-train.txt
pip uninstall -y onnxruntime && pip install onnxruntime-gpu==1.31.0   # GPU export parity checks and ONNX evaluation
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.is_bf16_supported())"
python -m pytest -q      # 30 tests, CPU, ~1 min: confirms the environment before any download
```

Optional, for TensorRT in the runtime later: `pip install tensorrt` matching the ONNX Runtime build.

### 2. Datasets

Download into `datasets/raw/<name>/` exactly as described in [`datasets/README.md`](datasets/README.md)
(sources, licences, expected folder layouts). Nothing here downloads data automatically: each
dataset has its own licence and most require a request form.

### 3. Weapon detection

```bash
# convert + split every source listed in the config into one YOLO layout (symlinks, deterministic split)
python -m training_scripts.train_weapon_detector prepare --config configs/weapon_yolo11.yaml
python -m training_scripts.train_weapon_detector verify  --config configs/weapon_yolo11.yaml

# RTX 4080: yolo11m, batch 32 @ 640.  RTX 4090: yolo11l batch 24, or rtdetr-l batch 16.
python -m training_scripts.train_weapon_detector train --config configs/weapon_yolo11.yaml \
    model=yolo11m.pt epochs=100 batch=32 device=0 workers=8 name=weapon_yolo11m
python -m training_scripts.train_weapon_detector train --config configs/weapon_yolo11.yaml \
    model=rtdetr-l.pt epochs=80 batch=16 device=0 name=weapon_rtdetr_l

# re-evaluate a run (threshold chosen on val, then applied to test)
python -m evaluation.evaluate_weapons --weights experiments/runs/weapon_yolo11m/weights/best.pt \
    --data datasets/weapons_yolo/data.yaml --split val --threshold auto --device 0
python -m evaluation.evaluate_weapons --weights experiments/runs/weapon_yolo11m/weights/best.pt \
    --data datasets/weapons_yolo/data.yaml --split test --threshold 0.45 --device 0 --out experiments/runs/weapon_yolo11m/metrics_test.json
```

Outputs: `experiments/runs/weapon_yolo11m/{weights/best.pt, weights/last.pt, results.png, metrics_test.json, summary.json}`.

### 4. Fight / violence detection

```bash
# RTX 4080: swin3d_t, 160² crops, batch 16 (bf16).  RTX 4090: clip.size=224 batch 8, or model.backbone=swin3d_s.
python -m training_scripts.train_fight_detector --config configs/fight_video_swin.yaml \
    train.device=cuda train.batch_size=16 train.workers=8 train.epochs=30
# resume after an interruption
python -m training_scripts.train_fight_detector --config configs/fight_video_swin.yaml --resume experiments/runs/fight_swin3d_t/last.pt

python -m evaluation.evaluate_violence --checkpoint experiments/runs/fight_swin3d_t/best.pt \
    --data datasets/raw/RWF-2000 --layout rwf2000 --split test --threshold 0.5 --device cuda
```

Outputs: `experiments/runs/fight_swin3d_t/{best.pt, last.pt, history.json, metrics_test.json, summary.json}`.

### 5. Cross-camera Re-ID

```bash
# OSNet-x1.0 from scratch (or model.pretrained_path=weights/osnet_x1_0_imagenet.pth from torchreid), P16×K4
python -m training_scripts.train_reid_extractor --config configs/reid_osnet.yaml train.device=cuda train.workers=8
# ResNet50-IBN-a with ImageNet weights, trained on Market-1501 + MSMT17
python -m training_scripts.train_reid_extractor --config configs/reid_osnet.yaml model.backbone=resnet50_ibn_a \
    'data.extra_train=[{root: datasets/raw/MSMT17, layout: msmt17}]' name=reid_r50ibn_market_msmt

python -m evaluation.evaluate_reid --checkpoint experiments/runs/reid_osnet_x1_0_market/best.pt \
    --data datasets/raw/Market-1501 --layout market1501 --device cuda
```

Outputs: `experiments/runs/reid_osnet_x1_0_market/{best.pt, last.pt, history.json, metrics_test.json, summary.json}`;
`metrics_test.json` holds Rank-k / mAP, the calibrated bands from val and their precision on test.

### 6. Export to ONNX (with manifest and parity check)

```bash
python -m export.export_to_onnx weapon --checkpoint experiments/runs/weapon_yolo11m/weights/best.pt \
    --out export/weapon-yolo11m --name weapon-yolo11m --version 1.0.0
python -m export.export_to_onnx fight  --checkpoint experiments/runs/fight_swin3d_t/best.pt \
    --out export/fight-swin3d-t --name fight-swin3d-t --version 1.0.0
python -m export.export_to_onnx reid   --checkpoint experiments/runs/reid_osnet_x1_0_market/best.pt \
    --out export/reid-osnet-x1-0 --name reid-osnet-x1-0 --version 1.0.0

# the ONNX file, decoded by the runtime's own code, must reproduce the PyTorch metrics:
python -m evaluation.evaluate_weapons  --weights export/weapon-yolo11m/weapon-yolo11m.manifest.json --data datasets/weapons_yolo/data.yaml --split test --threshold 0.45
python -m evaluation.evaluate_violence --manifest export/fight-swin3d-t/fight-swin3d-t.manifest.json --data datasets/raw/RWF-2000 --layout rwf2000 --split test --threshold manifest
python -m evaluation.evaluate_reid     --manifest export/reid-osnet-x1-0/reid-osnet-x1-0.manifest.json --data datasets/raw/Market-1501 --layout market1501
```

The exporter exits non-zero if `max |torch − onnx| ≥ 1e-4`. Video Swin needs the exporter's
constant-mask patch (`models.fight_models.export_friendly_swin`), applied automatically; the
parity check still compares against the unpatched PyTorch model.

### 7. Hand over to the runtime

```bash
cp export/weapon-yolo11m/weapon-yolo11m.{onnx,manifest.json}   ../services/cv_inference/weights/
cp export/fight-swin3d-t/fight-swin3d-t.{onnx,manifest.json}   ../services/cv_inference/weights/
cp export/reid-osnet-x1-0/reid-osnet-x1-0.{onnx,manifest.json} ../services/cv_inference/weights/
```

Then record the run in `experiments/model_registry.md` (model, dataset version, config, hardware,
measured metrics, licence) and switch the module on in the runtime (`CV_ENABLE_WEAPON_DETECTION=true`
…; see `services/cv_inference/README.md`). The `.onnx` files are git-ignored; the manifests are not.

## Design notes

- **Honest evaluation:** thresholds and bands are chosen on validation data (a hashed slice of
  train, or held-out Re-ID identities), and reported on the untouched test split. RWF-2000's
  published `val` is treated as the test split.
- **CCTV augmentation** (`data_utils/cctv_augment.py`): low light / gamma, JPEG compression, sensor
  noise, motion blur, perspective, grey/IR, fog. For clips, one random draw is applied to every frame
  (`ReplayCompose`), so augmentation never looks like motion.
- **Frame-rate normalisation:** clips are sampled at `target_fps` from the source frame rate; the
  runtime should sample at a similar rate (`CV_SAMPLE_FPS`).
- **Why torchvision video models, not pytorchvideo:** pytorchvideo 0.1.5 imports a torchvision
  module removed in 0.17, so it does not import with torch 2.x. torchvision ships Video Swin,
  MViT v2 and R(2+1)D with Kinetics-400 weights.
- **OSNet is implemented here** (module names follow `torchreid`, so its pretrained `.pth` files load);
  `torchreid` itself is not a dependency.
- **Licences:** `ultralytics` and models trained with it are AGPL-3.0 (an enterprise licence is
  needed for commercial use); the runtime never imports ultralytics, it runs the exported graph with
  its own decoder. torchvision models are BSD-3. Dataset licences are listed in `datasets/README.md`
  and must be checked before any non-research use.
- **Separation:** training data stays apart from operational CCTV data; whether operational footage
  may ever be used for training is not decided (decisions D-007, D-008).
