# Model registry

No model has been trained, evaluated or exported yet (2026-10-29). When one is, record here:
- model name and version;
- dataset and version;
- configuration and threshold;
- hardware;
- measured results.

These fields are required by requirements Section 6.


## yolox-tiny-coco 0.1.1rc0 (person detector, in use since 2026-10-31)

- **Source:** https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx
  (official ONNX export, not trained by this project).
- **SHA256:** `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7`.
- **Licences:** code Apache-2.0; weights without a separately stated licence (COCO 2017 training
  data).
- **Input:** 416×416. Publisher's COCO val mAP 32.8 (not reproduced here).
- **Use:** person class only, through `cv_inference/src/detectors/human_detector.py`.
- **Measured here** (i5-5200U, CPU, ONNX Runtime 1.31): inference 74.6 ms mean, 88.7 ms p95;
  full detect 76.8 ms mean, about 13 fps.
- **Not evaluated** on project footage yet.


## Pipelines ready, no trained model yet (2026-11-15)

The training, evaluation and export pipelines for the three modules exist and pass their tests on
CPU with synthetic data (`ml_training/README.md`). **No entry below this line describes a real
model yet**: nothing has been trained on a real dataset, so there are no accuracy figures. When the
first GPU runs finish, add one entry per exported model with:

- name, version and task (as in `<name>.manifest.json`); the ONNX file's SHA256;
- dataset(s) and the conversion report / split counts;
- config file and overrides, commit, date, hardware and precision (from `summary.json`);
- the **test** metrics from `metrics_test.json` (never val), the chosen threshold / bands and how
  they were chosen;
- the PyTorch↔ONNX parity figure; the measured ms/frame or ms/clip or ms/crop on the serving hardware;
- licences of the code, the weights and the data.

Template:

```
## <name> <version> (<task>) — <date>
- Dataset: … (licence: …)
- Config: configs/….yaml + overrides …; commit …; hardware …; precision …
- Test metrics: …
- Operating point: … (chosen on val)
- Export: opset 17, dynamic batch, onnxsim; parity max |Δ| = …; SHA256 …
- Serving latency: … ms on …
- Licence of the weights: …
```
