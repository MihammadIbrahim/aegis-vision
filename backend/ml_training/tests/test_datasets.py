from pathlib import Path

import numpy as np
import pytest
import torch

from data_utils.cctv_augment import CctvAugmentConfig, CctvClipAugmenter, cctv_image_transform
from data_utils.common import split_items, stable_split
from data_utils.reid_datasets import PKSampler, ReidDataset, holdout_identities, parse_cuhk03, parse_market1501, parse_msmt17, relabel, train_transform
from data_utils.video_clips import ClipDataset, ClipSpec, VideoClipReader, list_videos, make_balanced_sampler, split_counts
from data_utils.weapon_dataset import convert_dataset, parse_coco, parse_voc, read_yolo_split, verify_layout, write_yolo_dataset

# --- splits -------------------------------------------------------------------------------------------


def test_hashed_splits_are_deterministic_and_disjoint() -> None:
    names = [f"video_{i}.mp4" for i in range(300)]
    first = split_items(names, (0.7, 0.15, 0.15), seed=7)
    second = split_items(reversed(names), (0.7, 0.15, 0.15), seed=7)
    assert first == second
    assert set(first["train"]) & set(first["val"]) == set() and set(first["val"]) & set(first["test"]) == set()
    assert 180 < len(first["train"]) < 240  # roughly 70 %
    assert stable_split("x", (0.5, 0.5), 1) in (0, 1)
    with pytest.raises(ValueError):
        stable_split("x", (0.5, 0.6), 1)


# --- weapon datasets --------------------------------------------------------------------------------------


def test_voc_to_yolo_conversion_maps_classes_keeps_negatives_and_writes_data_yaml(voc_dataset: Path, tmp_path: Path) -> None:
    out = tmp_path / "yolo"
    report = convert_dataset(voc_dataset, "voc", out, class_names=["handgun", "knife"], class_map={"pistol": "handgun", "knife": "knife"}, ratios=(0.5, 0.25, 0.25), seed=1)
    assert sum(report.images.values()) == 6 and report.dropped_labels == {"wallet": 2}
    assert sum(report.negatives.values()) == 2  # "d" has nothing; "e" has only a wallet
    layout = verify_layout(out)
    assert set(layout) == {"train", "val", "test"} and sum(v["images"] for v in layout.values()) == 6
    everything = [item for split in ("train", "val", "test") for item in read_yolo_split(out, split)]  # type: ignore[arg-type]
    boxes = {b.label for item in everything for b in item.boxes}
    assert boxes == {"handgun", "knife"}
    pistol = next(item for item in everything if item.path.name == "a.jpg").boxes[0]
    assert (round(pistol.x1), round(pistol.y1), round(pistol.x2), round(pistol.y2)) == (10, 10, 40, 30)  # round-trips through normalised text
    # the same seed gives the same split on a second conversion
    again = convert_dataset(voc_dataset, "voc", tmp_path / "yolo2", class_names=["handgun", "knife"], class_map={"pistol": "handgun", "knife": "knife"}, ratios=(0.5, 0.25, 0.25), seed=1)
    assert again.images == report.images


def test_coco_parser_reads_boxes_and_sizes(coco_dataset: Path, tmp_path: Path) -> None:
    items = parse_coco(coco_dataset / "instances_train.json", coco_dataset / "images")
    assert len(items) == 3 and sum(len(i.boxes) for i in items) == 3
    gun = next(b for i in items for b in i.boxes if b.label == "gun")
    assert (gun.x1, gun.y1, gun.x2, gun.y2) == (10, 10, 30, 25)
    report = write_yolo_dataset(items, tmp_path / "out", class_names=["handgun"], class_map={"gun": "handgun"}, ratios=(1.0,))
    assert report.boxes == {"train": 2} and report.dropped_labels == {"person": 1} and report.negatives == {"train": 1}


def test_voc_parser_requires_annotations(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_voc(tmp_path)


# --- video clips ---------------------------------------------------------------------------------------------


def test_rwf_layout_keeps_published_val_as_test_and_slices_a_val_from_train(rwf_dataset: Path) -> None:
    items = list_videos(rwf_dataset, "rwf2000")
    counts = split_counts(items)
    assert counts["test"] == {"violence": 2, "normal": 2}
    assert counts.get("train", {}).get("violence", 0) + counts.get("val", {}).get("violence", 0) == 4
    assert all(i.label == (1 if i.path.name.startswith("Fight_") else 0) for i in items)


def test_frame_rate_normalisation_and_both_backends_agree(rwf_dataset: Path) -> None:
    video = next(rwf_dataset.rglob("*_0.avi"))  # 60 frames at 15 fps
    cv_reader = VideoClipReader(backend="opencv")
    meta = cv_reader.meta(video)
    assert (meta.frames, meta.fps) == (60, 15.0)
    indices = cv_reader.frame_indices(meta, 0, 8, target_fps=7.5)
    assert indices == [0, 2, 4, 6, 8, 10, 12, 14]  # every second frame covers the same wall-clock span
    assert cv_reader.frame_indices(meta, 55, 8, 7.5)[-1] == 59  # padded with the last frame, never past the end
    clip = cv_reader.read_frames(video, indices)
    assert clip.shape == (8, 64, 96, 3) and clip.dtype == np.uint8
    auto = VideoClipReader()
    if auto.backend == "decord":
        other = auto.read_frames(video, indices)
        assert other.shape == clip.shape and abs(int(other.mean()) - int(clip.mean())) <= 2  # codecs differ by rounding only


def test_clip_dataset_shapes_sampling_and_augmentation(rwf_dataset: Path) -> None:
    items = list_videos(rwf_dataset, "rwf2000")
    spec = ClipSpec(clip_len=8, target_fps=10, size=48, resize_short=56, windows_per_video_eval=3, clip_stride_seconds=0.5)
    train = ClipDataset([i for i in items if i.split in ("train", "val")], spec, train=True, augment=CctvAugmentConfig(), seed=3)
    clip, label, video = train[0]
    assert clip.shape == (3, 8, 48, 48) and clip.dtype == torch.float32 and label in (0, 1) and video == 0
    train.set_epoch(1)
    assert not torch.equal(train[0][0], clip)  # a different random window / augmentation per epoch
    evaluation = ClipDataset([i for i in items if i.split == "test"], spec, train=False)
    assert 4 <= len(evaluation) <= 4 * 3 and len(set(evaluation.video_of)) == 4  # windows_per_video_eval is a maximum
    assert evaluation[0][0].shape == (3, 8, 48, 48)
    sampler = make_balanced_sampler(train.labels, seed=0)
    assert len(list(sampler)) == len(train)
    frames = np.random.default_rng(0).integers(0, 255, (4, 32, 32, 3), dtype=np.uint8)
    out = CctvClipAugmenter(CctvAugmentConfig(perspective_p=1.0, low_light_p=1.0))(frames)
    assert out.shape == frames.shape and out.dtype == np.uint8
    assert cctv_image_transform()(image=frames[0])["image"].shape == (32, 32, 3)


def test_other_layouts(tmp_path: Path) -> None:
    from tests.conftest import write_clip

    for folder, base in (("Violence", 200), ("NonViolence", 10)):
        (tmp_path / "rlvs" / folder).mkdir(parents=True)
        for n in range(6):
            write_clip(tmp_path / "rlvs" / folder / f"{folder}_{n}.avi", frames=12, fps=12, base=base + n)
    items = list_videos(tmp_path / "rlvs", "rlvs", ratios=(0.5, 0.25, 0.25))
    assert len(items) == 12 and {i.split for i in items} <= {"train", "val", "test"}
    for folder in ("Fighting", "Normal_Videos_event", "Shoplifting"):
        (tmp_path / "ucf" / folder).mkdir(parents=True)
        write_clip(tmp_path / "ucf" / folder / "v.avi", frames=12, fps=12, base=5)
    ucf = list_videos(tmp_path / "ucf", "ucfcrime")
    assert {(i.path.parent.name, i.label) for i in ucf} == {("Fighting", 1), ("Normal_Videos_event", 0)}  # Shoplifting excluded


# --- Re-ID datasets -------------------------------------------------------------------------------------------------


def test_market_parser_follows_the_protocol(market_dataset: Path) -> None:
    splits = parse_market1501(market_dataset)
    assert splits.summary()["train"] == {"images": 24, "identities": 6, "cameras": 2}
    assert all(i.pid not in (0, -1) for i in splits.train)
    gallery_pids = {i.pid for i in splits.gallery}
    assert 0 in gallery_pids and -1 not in gallery_pids  # distractors kept, junk dropped
    assert {i.camid for i in splits.query} == {1}
    train, query, gallery = holdout_identities(splits.train, 0.34, seed=0)
    held = {i.pid for i in query}
    assert held and held.isdisjoint({i.pid for i in train})
    assert all(q.pid in {g.pid for g in gallery} for q in query)
    relabelled, mapping = relabel(train)
    assert set(mapping.values()) == set(range(len(mapping))) and {i.pid for i in relabelled} == set(range(len(mapping)))


def test_msmt17_and_cuhk03_parsers(tmp_path: Path) -> None:
    root = tmp_path / "msmt"
    (root / "train" / "0001").mkdir(parents=True)
    (root / "test" / "0002").mkdir(parents=True)
    (root / "train" / "0001" / "0001_000_01_0303morning_0015_0.jpg").touch()
    (root / "test" / "0002" / "0002_000_05_0303morning_0015_0.jpg").touch()
    (root / "list_train.txt").write_text("0001/0001_000_01_0303morning_0015_0.jpg 1\n")
    (root / "list_val.txt").write_text("")
    (root / "list_query.txt").write_text("0002/0002_000_05_0303morning_0015_0.jpg 2\n")
    (root / "list_gallery.txt").write_text("0002/0002_000_05_0303morning_0015_0.jpg 2\n")
    msmt = parse_msmt17(root)
    assert msmt.train[0].camid == 1 and msmt.query[0].camid == 5 and msmt.query[0].pid == 2
    cuhk = tmp_path / "cuhk03-np" / "detected"
    for folder in ("bounding_box_train", "query", "bounding_box_test"):
        (cuhk / folder).mkdir(parents=True)
    (cuhk / "bounding_box_train" / "1_001_1_01.png").touch()
    (cuhk / "bounding_box_train" / "2_001_2_01.png").touch()
    (cuhk / "query" / "3_005_1_01.png").touch()
    (cuhk / "bounding_box_test" / "3_005_2_01.png").touch()
    splits = parse_cuhk03(tmp_path / "cuhk03-np")
    assert [(i.pid, i.camid) for i in splits.train] == [(1001, 0), (2001, 3)]  # pair 1 cam 1 → 0; pair 2 cam 2 → 3
    assert splits.query[0].pid == splits.gallery[0].pid == 3005


def test_pk_sampler_and_train_transform(market_dataset: Path) -> None:
    splits = parse_market1501(market_dataset)
    items, _ = relabel(splits.train)
    sampler = PKSampler([i.pid for i in items], p=3, k=4, seed=0)
    indices = list(sampler)
    assert len(indices) == len(sampler) == 6 // 3 * 12
    for start in range(0, len(indices), 12):
        batch = [items[i].pid for i in indices[start : start + 12]]
        assert len(set(batch)) == 3 and all(batch.count(pid) == 4 for pid in set(batch))
    sampler.set_epoch(1)
    assert list(sampler) != indices
    with pytest.raises(ValueError):
        PKSampler([0, 0, 1, 1], p=3, k=2)
    tensor, pid, camid = ReidDataset(items, train_transform(256, 128))[0]
    assert tensor.shape == (3, 256, 128) and isinstance(pid, int) and camid in (1, 2)
