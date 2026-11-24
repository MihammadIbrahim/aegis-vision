# cv_inference — CV processing foundation

**Status (2026-11-15):** the processing pipeline is built and tested. It reads recorded video,
samples frames, runs the enabled detectors, tracks detections, confirms events and delivers them
to Core API.

**Real model with weights:** one, the pretrained **YOLOX-Tiny person detector** (decision D-015),
with a ByteTrack-style single-camera tracker. People are detected and tracked; they never raise
alerts.

**Adapters built, weights pending (2026-11-15):** the **weapon detector** (`WeaponOnnxDetector`),
the **fight / violence clip classifier** (`ViolenceOnnxClassifier` with a per-camera frame buffer) and
**cross-camera Re-ID** (`PersonReIdOnnxEmbedder` + a campus-scoped Faiss gallery) run any model
exported by `ml_training/export` (ONNX + manifest). They are tested with tiny generated ONNX graphs
(`tests/fixtures/*_dummy.onnx`), **not with trained models**: no weapon, fight or Re-ID model has been
trained yet, so no accuracy claim exists. Modules are switched on independently (`CV_ENABLE_*`);
`CV_DEVICE=auto` picks TensorRT, then CUDA, then CPU.

**Not built:** face recognition, unauthorized-area logic, RTSP input. A track ID is local to one
camera and is never a person's identity; a Re-ID band is never a confirmed identity.

## Data flow

```
VideoFileSource ──Frame──▶ CompositeDetector ──Detection[]──▶ ByteTracker ──Track[]──▶ EventRules ──DetectionEvent──▶ HttpDetectionSink
(src/video_source.py)      ├ YoloxHumanDetector (person)      (tracking/)      │          (src/rules.py)                 (src/detection_publisher.py)
                           ├ WeaponOnnxDetector (handgun, knife …)              │                                               │
                           └ TemporalDetectorAdapter(ViolenceOnnxClassifier)    ▼                       Core API  POST /api/v1/internal/detections ◀─┘
                             16-frame ring buffer per camera → fight_violence   ReIdTrackObserver        → WEAPON / FIGHT_VIOLENCE alerts (HIGH)
                                                                                 crop → embed → CampusGallery (Faiss, time window)
                                                                                 → LIKELY_MATCH / UNCERTAIN / NO_RELIABLE_MATCH → JSONL (no backend intake yet)
```

Each arrow is an interface: `FrameSource`, `Detector`, `Tracker` and `DetectionSink`.
`Pipeline` (`src/pipeline.py`) only connects them, so a real model, an RTSP source or another sink
replaces one part without changing the rest.

| Part | File | Real or test-only |
|---|---|---|
| Frame, Detection, Track, DetectionEvent, ModelInfo types | `src/schemas.py` | Real |
| Recorded-video input and sampling (OpenCV) | `src/video_source.py` | Real (files only; no RTSP yet) |
| `Detector` and `ClipDetector` interfaces | `src/detectors/base_detector.py` | Interfaces |
| `ModelAdapter` and `OnnxRuntimeAdapter` (CPU/CUDA selection, latency stats) | `src/model_loader.py` | Real, tested on a tiny generated ONNX graph only |
| `Embedder` interface (Re-ID) | `src/reid/reid_extractor.py` | Interface only |
| IoU single-camera tracker | `src/tracking/single_tracker.py` | Real, simple (used with the synthetic detector) |
| ByteTrack-style tracker: Kalman motion, two-stage association, lost-track buffer, tentative → confirmed | `src/tracking/single_tracker.py` (`ByteTracker`) | Real, used with the real detector |
| YOLOX-Tiny person detector: letterbox, ONNX inference, grid decode, person filter, NMS | `src/detectors/human_detector.py` | **Real model**, run and tested on this CPU |
| Benchmark | `src/benchmark.py` | Real (measures, never estimates) |
| Model manifest reader (checksum, task, shape checks; mirror of `ml_training/export/manifest.py`) | `src/manifest.py` | Real |
| Weapon detector: letterbox, ONNX inference, ultralytics-layout decode (YOLO11 / RT-DETR), class-aware NMS, per-class calibrated thresholds | `src/detectors/weapon_detector.py` | Real adapter; **no trained weights yet** (tested with `tests/fixtures/weapon_dummy.onnx`) |
| Fight / violence: per-camera ring buffer, clip pre-processing (resize short side, centre crop, Kinetics mean/std), ONNX clip classifier, temporal adapter into the frame pipeline | `src/detectors/fight_detector.py` | Real adapter; **no trained weights yet** (`fight_dummy.onnx`) |
| Composite of the enabled detectors | `src/detectors/composite.py` | Real |
| Re-ID embedder (crop → 512-D unit vector), crop quality filter, match bands, track observer | `src/reid/reid_extractor.py` | Real adapter; **no trained weights yet** (`reid_dummy.onnx`) |
| Campus-scoped embedding gallery: Faiss inner-product index (NumPy fallback), time window, same-camera exclusion | `src/reid/embedding_store.py` | Real (in memory; nothing persisted) |
| Thresholds, confirmation, cooldown, stable IDs | `src/rules.py` | Real |
| HTTP delivery: timeouts, bounded retries, error handling | `src/detection_publisher.py` | Real |
| Settings (`CV_*`) | `src/config.py` | Real |
| Command line | `src/main.py` | Real |
| `SyntheticScriptedDetector` | `src/detectors/synthetic_detector.py` | **Test-only.** It ignores the pixels and replays a JSON script. Its model info has `synthetic: true`, and its detection IDs start with `synthetic-` |

The other files in `src/` (`face/`, `reid/person_search_worker.py`, `clip_extractor.py`,
`frame_consumer.py`) are still **empty placeholders**.

## Who decides what

- **The pipeline decides** whether something is worth reporting:
  - which model labels map to which alert type (`CV_CLASS_MAP`; others, such as `person`, are tracked
    but never sent);
  - per-label thresholds;
  - `CV_MIN_CONSECUTIVE_FRAMES` in a row before an event fires;
  - at most one event per `CV_COOLDOWN_SECONDS` while the situation continues.
- **Core API decides** alert identity:
  - the same `detection_id` is ignored (DUPLICATE, every detection since migration 0016);
  - a repeat within `ALERT_DEDUP_SECONDS` merges into the open alert (REPEATED);
  - priority, routing to Guards and campus isolation stay entirely in Core API.
- **Coordination:** the cooldown must be shorter than the backend's merge window, which the settings
  check (`CV_BACKEND_DEDUP_SECONDS`, default 60).
- **Stable IDs:** a `detection_id` is a hash of the video's identity, the camera, the alert type, the
  episode start and the event number. Retries and re-processing the same video therefore never
  create extra alerts.

## The person model (D-015)

| | |
|---|---|
| Model | YOLOX-Tiny (COCO), official ONNX export, release `0.1.1rc0` |
| Source | https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx |
| SHA256 | `427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7` (20,219,662 bytes) |
| Code licence | Apache-2.0 (Megvii-BaseDetection/YOLOX) |
| Weight licence | Not stated separately by the publisher. Trained on COCO 2017 (annotations CC BY 4.0; images under various Flickr licences). Fine for development and the FYP; **check before redistributing it in a commercial product** |
| Input / output | `images` 1×3×416×416 float32 (BGR, 0–255) / `output` 1×3549×85 |
| Publisher's accuracy | COCO val mAP 32.8 at 416 px (their figure; not measured here) |

**Why this model:**
- it's permissively licensed, unlike AGPL Ultralytics YOLO;
- it's small enough for this CPU;
- it has an official ONNX file with documented pre- and post-processing.

**Alternatives considered:**
- YOLOX-Nano: faster but weaker (mAP 25.8);
- Ultralytics YOLO11n/YOLOv8n: AGPL;
- RT-DETR-R18: heavier, and needs its own export.

**Setup.** The weights are not in git (`*.onnx` is ignored):
```
cd backend/services/cv_inference/weights
curl -L -o yolox_tiny.onnx https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx
sha256sum yolox_tiny.onnx   # must print the SHA256 above
```

`yolox_tiny.model.json` describes the file:
- **fields:** format, input/output shape, strides, class index, checksum and licences;
- **refusals:** the detector refuses to start if anything differs, and never falls back to the
  synthetic detector.

**Measured on this machine (2026-10-31)**, `python -m src.benchmark`:
- **Hardware:** Intel Core i5-5200U, 2 cores / 4 threads, 2.2 GHz; ONNX Runtime 1.31.0,
  CPUExecutionProvider. There is no GPU (Intel HD 5500 and an old AMD R9 M275X, neither usable).
- **Method:** 100 runs after 10 warm-up runs. The desktop and editor were running (load average
  about 1.5).

| Image | Inference only (mean / p50 / p95) | detect(): pre + inference + post (mean / p50 / p95) | Detector FPS |
|---|---|---|---|
| 512×600 photo | 74.6 / 71.6 / 88.7 ms | 76.8 / 73.6 / 90.8 ms | ≈13.0 |
| 1280×720 scene | 77.7 / 73.2 / 96.4 ms | 79.6 / 74.9 / 98.6 ms | ≈12.6 |

**What the figures cover:** the detector alone, for one camera. Video decoding, tracking and HTTP
are not included, and nothing runs in parallel.

**What they mean:** the 5 fps default sampling fits one camera on this CPU, with some headroom.
Several cameras, or full frame rate, do not. **This is not a real-time CCTV claim** (see the end of
this file).

## Trained models: weapon, fight / violence, Re-ID (adapters built 2026-11-15, weights pending)

Models are trained and exported in `backend/ml_training` (see its README for the GPU runbook). Each
export is an ONNX file plus `<name>.manifest.json`; the manifest carries the pre-processing, output
format, labels, calibrated thresholds, dataset, commit and **test metrics**, and the runtime checks
the file's SHA256 and input shape against it before use. Copy both files into `weights/` and switch
the module on:

| Module | Switch | Model settings | What it emits |
|---|---|---|---|
| Person detection + tracking | `CV_ENABLE_HUMAN_TRACKING` (default on) | `CV_HUMAN_MODEL_SPEC` … | `person` tracks (never alerts) |
| Weapon detection | `CV_ENABLE_WEAPON_DETECTION` | `CV_WEAPON_MANIFEST`, `CV_WEAPON_DEFAULT_THRESHOLD`, `CV_WEAPON_CLASS_THRESHOLDS` (JSON, overrides the manifest's calibrated per-class values), `CV_WEAPON_NMS_IOU` | `handgun`, `knife` … detections → `WEAPON` alerts (HIGH) through the rules' class map |
| Fight / violence | `CV_ENABLE_FIGHT_DETECTION` | `CV_FIGHT_MANIFEST`, `CV_FIGHT_INFER_EVERY` (classify every N sampled frames once the 16-frame buffer is full), `CV_FIGHT_MIN_REPORT_PROBABILITY` | a whole-frame `fight_violence` detection with the latest clip probability → `FIGHT_VIOLENCE` alerts (HIGH); confirmation and cooldown as for any label |
| Cross-camera Re-ID | `CV_ENABLE_REID` (needs human tracking and `--campus-id`) | `CV_REID_MANIFEST`, `CV_REID_WINDOW_SECONDS` (600), `CV_REID_EVERY_N_FRAMES` (5), `CV_REID_TOP_K`, `CV_REID_LIKELY_THRESHOLD` / `CV_REID_UNCERTAIN_THRESHOLD` (override the manifest's calibrated bands), `CV_REID_OBSERVATIONS_PATH` (JSONL), `CV_REID_USE_FAISS` | `ReIdObservation` records: track, cross-camera candidates with cosine similarity and band |

`--detector auto` builds the composite from these switches; `CV_CONFIG_FILE=settings.yaml` (or
`--config`) holds the same keys without the prefix, and environment variables win over the file.

**Re-ID rules enforced in code:** embeddings are scoped to one campus and expire with the window;
the same camera is never matched against itself; a candidate is `LIKELY_MATCH`, `UNCERTAIN` or
`NO_RELIABLE_MATCH` and every observation has `review_required: true`; there is no `CONFIRMED` state.
Core API has **no Re-ID intake endpoint yet**, so observations are written to the JSONL file (or kept
in memory) and summarised in the report; they are never sent to the alert intake. Only crops that
pass the quality filter (size, aspect, confidence, sharpness, not touching the frame edge) are
embedded, and one running-mean embedding is kept per track.

**Frame rate:** the fight model is trained on clips sampled at the manifest's `target_fps` (8 by
default); keep `CV_SAMPLE_FPS` close to it. The adapter warns when they differ by more than 25 %.

## Configure and run

All settings use the `CV_` prefix (environment variables or `services/cv_inference/.env`); see
`src/config.py` for the full list.
- **`CV_DETECTION_INGEST_TOKEN`:** must equal Core API's `DETECTION_INGEST_TOKEN`. Put your own value
  in your `.env` only.
- **`CV_API_BASE_URL`:** where Core API runs.

```
cd backend/services/cv_inference
CV_API_BASE_URL=http://127.0.0.1:8000 CV_DETECTION_INGEST_TOKEN=... \
../../.venv/bin/python -m src.main --video clip.mp4 --camera-id <camera UUID> --detector yolox-human
```

- **`--detector auto`:** every module switched on in the settings (table above), on one video:
  ```
  CV_ENABLE_WEAPON_DETECTION=true CV_ENABLE_FIGHT_DETECTION=true CV_ENABLE_REID=true CV_DEVICE=auto \
  CV_REID_OBSERVATIONS_PATH=reid_observations.jsonl \
  ../../.venv/bin/python -m src.main --video clip.mp4 --camera-id <camera UUID> --campus-id <campus UUID> --detector auto
  ```
- **`--detector yolox-human`:** the real person detector. Settings:
  - `CV_HUMAN_MODEL_SPEC` (default `weights/yolox_tiny.model.json`);
  - `CV_HUMAN_SCORE_THRESHOLD` (0.35) and `CV_HUMAN_NMS_IOU` (0.45);
  - `CV_DEVICE` (cpu | cuda | tensorrt | auto = TensorRT → CUDA → CPU; a missing provider is an error).
- **`--detector synthetic --script script.json`:** the test-only detector.
- **`CV_TRACKER`:** `auto` (default) picks ByteTrack for real models and IoU for the synthetic one.
  ByteTrack's own settings start with `CV_BYTETRACK_` (high 0.5, low 0.1, new track 0.6,
  max lost frames 30).

**Campus AI configuration, live overlays and Re-ID intake (decision D-024).** With `CV_SERVICE_TOKEN` (the Core API's
`INTERNAL_SERVICE_TOKEN`) the worker:
- reads the camera's **effective modules** (platform ∩ campus ∩ camera, set on the AI Configuration page) from
  `GET /internal/cameras/{id}/ai-config` every `CV_AI_CONFIG_POLL_SECONDS` (30) and runs only those
  (`AiConfigWatcher` + `GatedDetector`); a module switched off on the page stops producing detections;
- reports what it runs (`POST /internal/cameras/{id}/ai-status`: modules, frames per second, last error);
- with `--storage-root <CCTV_STORAGE_ROOT>` writes the newest detections to
  `live/<organization>/<campus>/<camera>/overlay.json` (boxes as frame fractions, track ID, confidence, module) for
  the Live page (`OverlayWriter`); the page hides overlays older than the live-frame limit;
- with `CV_REID_SINK=core_api` sends Re-ID observations (512-number embedding, quality, small crop) to
  `POST /internal/reid/observations` for Person Search (`HttpObservationSink`); the API refuses cameras that may not
  run Re-ID (`REID_DISABLED`). `CV_REID_SINK=file` (default) keeps the JSONL file of `CV_REID_OBSERVATIONS_PATH`.

```
CV_API_BASE_URL=http://127.0.0.1:8000 CV_DETECTION_INGEST_TOKEN=... CV_SERVICE_TOKEN=... CV_REID_SINK=core_api \
CV_ENABLE_REID=true ../../.venv/bin/python -m src.main --spool-dir <frames/camera> --camera-id <camera UUID> \
  --campus-id <campus UUID> --storage-root /mnt/cctv_storage --detector auto
```

**Output:** the command prints a JSON report covering frames, events, delivery outcomes, undelivered
events, rule counters, mean detector time and processing FPS.

**Exit codes:**
- 0: all events delivered;
- 1: some events undelivered;
- 2: stopped (wrong token, intake off, unknown camera, unreadable video, detector error);
- 64: bad arguments.

**Script format:**
`[{"from_frame": 10, "to_frame": 124, "label": "weapon", "confidence": 0.9, "box": [x1, y1, x2, y2]}]`.

## Tests

**cv_inference: 75 tests**, run with `cd backend/services/cv_inference && ../../.venv/bin/python -m pytest -q`.
Result on 2026-11-15: 75 passed, 0 skipped (57 on 2026-10-31 + 18 for the new modules). By kind:
1. **Mocked model outputs:** pre-processing, decoding, person filter, thresholds, NMS, clipping,
   invalid outputs, metadata validation, adapter failures (`test_human_detector.py`).
2. **Synthetic fixtures:**
   - generated videos and the one-node ONNX graph;
   - missing, wrong-checksum and wrong-model files;
   - the IoU tracker and ByteTracker (`test_tracker.py`): IDs over frames, several people, empty
     frames, bridged gaps, expiry, low-score handling, no duplicate tracks, camera-local IDs;
   - rules, sink and pipeline.
3. **The real YOLOX-Tiny model:** it finds the person in a photograph (public-domain Grace Hopper
   portrait shipped with matplotlib), follows the box into a larger scene, and finds no one in a
   blank frame. Skipped, never faked, when the weights are absent.
4. **The real model inside the pipeline:** video → sampling → YOLOX → ByteTrack → rules gives
   one confirmed track and no event (`test_pipeline.py`).
5. **The new modules on generated ONNX graphs** (`tests/fixtures/make_dummy_models.py`; not vision
   models: each maps the mean colour of its input through fixed weights, so outputs depend on the
   image in a known way): weapon letterbox / decode / class thresholds / manifest and checksum
   refusals (`test_weapon_detector.py`); the ring buffer, clip pre-processing, the temporal adapter
   and FIGHT_VIOLENCE events through the rules (`test_fight_detector.py`); the embedder, quality
   filter, Faiss and NumPy galleries, bands and the observer's rules (`test_reid.py`); provider
   selection, settings from env and YAML, the composite detector, and a full run delivering WEAPON
   and FIGHT_VIOLENCE payloads to an emulated `/api/v1/internal/detections` through the real
   `HttpDetectionSink` while Re-ID records candidates (`test_modules_end_to_end.py`).

The person tests use one portrait only, so **accuracy on real CCTV is still unmeasured.**
- **End to end** (4): `backend/services/core_api/tests/integration/test_cv_pipeline_e2e.py`.
  - **How it runs:** it starts Core API with uvicorn on a local port, against the test database, and
    runs this command line as a separate process.
  - **What it checks:** one routed HIGH alert with 3 merged detections; a re-run gives 3 DUPLICATE
    and no change; a wrong token or unknown camera stops the run; `person` never alerts.
  - **Plus the real YOLOX-Tiny run:** one track, 0 events and 0 alerts in Core API.
  - **Needs:** the test PostgreSQL variables (see `backend/README.md`).
- **Test model:** `tests/fixtures/scale2.onnx` is a generated one-node graph (input × 2), not a vision
  model. Rebuild it with `tests/fixtures/make_scale_model.py`.

## Limitations and risks

- **Only one model with weights:** the person detector. The weapon, fight and Re-ID adapters have
  no trained weights yet; their tests use generated graphs. There is no labelled evaluation
  footage, so person precision and recall on CCTV are unmeasured. The publisher's COCO figure is
  not our measurement.
- **Re-ID has no backend endpoint:** candidates stay in a JSONL file; the Supervisor review page
  and storage (requirements 4 C) are not built. The gallery lives in memory for one run.
- **Weaknesses expected from COCO-trained models:** small, distant or heavily occluded people,
  night-time IR, unusual high-angle views.
- **Input:** recorded files only. There is no RTSP/IP-camera source and no `video_ingestion` link.
- **Processing:** frames are processed one at a time in one process. There is no GPU path in use, no
  batching and no backpressure.
- **Not built:**
  - clip buffering for `ClipDetector` (fight/violence);
  - Re-ID embeddings;
  - snapshot or clip evidence (`snapshot_ref` is not sent);
  - face recognition.
- **Tracker:** ByteTracker uses motion and overlap only, no appearance. It can swap IDs when people
  cross closely, and it restarts a track after a long occlusion (more than 30 sampled frames). At
  5 fps sampling, fast movement means less overlap between frames.
- **Undelivered events:** reported by the run, but not stored for a later retry. A real deployment
  needs a durable queue.

## Cross-camera person Re-ID (storage and review built in D-024)

- **Model:** a Re-ID embedder (e.g. OSNet) behind `Embedder`; **no trained weights are installed yet** (GPU training
  is a separate step), so photo search on the website says it is unavailable.
- **Feeding it:** a `TrackObserver` picks good person crops per confirmed track and sends observations to the Core API
  (`CV_REID_SINK=core_api`).
- **Matching (built):** observations are stored in pgvector; Person Search ranks them with "Likely / Uncertain" bands
  (0.75 / 0.55) and staff review each result; results never confirm an identity. Observations expire with the
  campus's recording retention (the retention daemon calls `POST /internal/reid/purge-expired`).

## (Done) First real pretrained human detector

1. **Choose a model** by comparing candidates on licence, size, CPU and GPU latency, and COCO
   person AP. For example:
   - YOLOv8n/s (Ultralytics, AGPL-3.0: check the licence fits the product);
   - RT-DETR;
   - an Apache/MIT-licensed YOLO variant.

   Record the decision and licence in `ml_training/experiments/model_registry.md`.
2. **Export it to ONNX** in `ml_training/` (torch and ultralytics are installed there, CPU builds).
   Keep the weights out of git, and note their source and checksum.
3. **Implement `human_detector.py`:** preprocessing (letterbox resize, normalise) →
   `OnnxRuntimeAdapter` → decode boxes and NMS → `Detection("person", …)`.
4. **Test it two ways:** unit-test pre/post-processing with fixed tensors, then test on a short,
   licensed clip.
5. **Measure:** precision and recall on a labelled sample, ID switches with the tracker, and
   latency/FPS from the adapter's statistics. Persons stay non-alerting (D-013).
6. **Then** move to a weapon model; fight/violence needs `ClipDetector` buffering.

## Before claiming real-time CCTV performance

- an RTSP/ONVIF source tested on the two planned IP cameras;
- a real model running at a measured frame rate on the target hardware (CPU and/or GPU), with p95
  latency recorded;
- end-to-end delay measured from frame capture to alert;
- behaviour under several cameras, network loss and backpressure;
- evaluation results per model on documented data, with false-positive rates.

None of these has been measured yet.
