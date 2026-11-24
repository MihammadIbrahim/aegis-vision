# Datasets

**Nothing is stored here** (2026-11-15): no dataset has been downloaded yet. Each dataset below has
its own licence or access form; downloading and licence acceptance are manual, by the project owner.
The folder layouts are what the parsers in `data_utils/` expect. `raw/` and converted sets are
git-ignored.

Requirements Section 20 names CrowdHuman, AI City, Market-1501, weapon datasets and RWF-2000 /
UCF-Crime as candidates and asks that suitability and licences be verified; the notes below are a
starting point for that check, not a legal review.

## Weapon detection (`data_utils/weapon_dataset.py`, config `configs/weapon_yolo11.yaml`)

| Dataset | Format | Classes used | Access / licence notes |
|---|---|---|---|
| **OD-WeaponDetection** (University of Granada; "Weapons and similar handled objects", also knife and pistol sets) | Pascal VOC | pistol → handgun, knife; billete / monedero / tarjeta / smartphone become negatives | https://github.com/ari-dasci/OD-WeaponDetection — free for research; cite Olmos et al. 2018 / Pérez-Hernández et al. 2020 |
| **Sohas_weapon** (same group, "small objects handled similarly to a weapon") | Pascal VOC | pistol → handgun, knife | same repository; research use |
| **YouTube-GDD** (YouTube Gun Detection Dataset) | YOLO (`gun`, `person`) | gun → handgun; person boxes dropped | https://github.com/UCAS-GYX/YouTube-GDD — research use; frames from YouTube videos |
| CCTV weapon sets on Roboflow Universe (e.g. "weapon detection cctv") | YOLO with train/valid/test | per set | licence varies per set (often CC BY 4.0); check each page |

Layout for the converter (`prepare`): put each dataset under `datasets/raw/<Name>/` keeping the
publisher's structure; `convert_dataset` finds the XML/JSON/txt files and writes one merged YOLO
layout to `datasets/weapons_yolo/` with `data.yaml`, a hashed 80/10/10 split and a
`conversion_report.json` (images, boxes, negatives, dropped labels per split).

## Fight / violence (`data_utils/video_clips.py`, config `configs/fight_video_swin.yaml`)

| Dataset | Layout | Label rule | Access / licence notes |
|---|---|---|---|
| **RWF-2000** (Real-World Fighting, 2,000 surveillance clips, 5 s) | `RWF-2000/{train,val}/{Fight,NonFight}/*.avi` | folder name | https://github.com/mchengny/RWF2000-Video-Database-for-Violence-Detection — request form; research only. The published `val` is our **test**; model selection uses a hashed 10 % of `train` |
| **RLVS** (Real Life Violence Situations, 2,000 clips) | `RLVS/{Violence,NonViolence}/*.mp4` | folder name; hashed 70/15/15 split | Kaggle "real-life-violence-situations-dataset"; CC BY 4.0 per the Kaggle page (verify) |
| **UCF-Crime** (1,900 untrimmed surveillance videos, 13 anomaly classes) | `UCF-Crime/<Category>/*.mp4` | `Fighting`, `Assault` → violence (`Abuse`, `Robbery` with `ucf_extended: true`); `Normal_Videos_*` → normal; other classes excluded | https://www.crcv.ucf.edu/projects/real-world/ — research use; long videos, so the sliding-window sampler matters |
| Any custom set | `<root>/{train,val,test}/{Fight,NonFight}/*` (`layout: folders`) | folder name | — |

Clips are decoded with `decord` (OpenCV fallback), 16 frames at `target_fps` (8 fps → 2 s), short
side resized to `resize_short`, random (train) or centre (eval) crop of `size`×`size`.

## Person re-identification (`data_utils/reid_datasets.py`, config `configs/reid_osnet.yaml`)

| Dataset | Layout | Notes |
|---|---|---|
| **Market-1501** (1,501 identities, 6 cameras) | `Market-1501-v15.09.15/{bounding_box_train,query,bounding_box_test}/` | standard protocol: junk (−1) dropped, distractors (0000) kept in the gallery; research use (Zheng et al. 2015) |
| **MSMT17** (4,101 identities, 15 cameras) | `MSMT17/{train,test}/` + `list_{train,val,query,gallery}.txt` | request form (Wei et al. 2018); research use |
| **CUHK03** new protocol (`cuhk03-np`) | `cuhk03-np/{detected,labeled}/{bounding_box_train,query,bounding_box_test}/` | Zhong et al. re-ranking release; research use |

Validation uses `holdout_identity_fraction` of the training identities (query = first image per
identity and camera, gallery = the rest), so the published test set is never used for model
selection or band calibration.

## Person detection candidate (`configs/human_yolo.yaml`)

| Dataset | Layout | Notes |
|---|---|---|
| **CrowdHuman** (15k train / 4.4k val images, full-body boxes) | convert the `.odgt` annotations to COCO JSON (`instances_train.json`, `instances_val.json`) under `CrowdHuman/annotations/`, images under `CrowdHuman/images/` | https://www.crowdhuman.org — non-commercial research licence |

A small converter for `.odgt` → COCO (full-body `fbox` only):

```bash
python - <<'EOF'
import json, pathlib
root = pathlib.Path("datasets/raw/CrowdHuman"); (root / "annotations").mkdir(exist_ok=True)
for split in ("train", "val"):
    images, annotations = [], []
    for i, line in enumerate((root / f"annotation_{split}.odgt").read_text().splitlines()):
        item = json.loads(line); name = item["ID"] + ".jpg"
        import cv2; h, w = cv2.imread(str(root / "images" / name)).shape[:2]
        images.append({"id": i, "file_name": name, "width": w, "height": h})
        for box in item["gtboxes"]:
            if box["tag"] != "person" or box.get("extra", {}).get("ignore"): continue
            x, y, bw, bh = box["fbox"]; annotations.append({"id": len(annotations), "image_id": i, "category_id": 1, "bbox": [x, y, bw, bh], "iscrowd": 0})
    (root / "annotations" / f"instances_{split}.json").write_text(json.dumps({"images": images, "annotations": annotations, "categories": [{"id": 1, "name": "person"}]}))
EOF
```

## Separation from operational data

Training data lives only here. Operational CCTV footage, personnel photos and alert snapshots are
never copied into `datasets/`; campus and organization deletion never touch this folder (decisions
D-007, D-008). Whether operational footage may ever be used for training is **not decided**.
