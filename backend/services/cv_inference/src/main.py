"""Command line: run the pipeline on a recorded video.

    python -m src.main --video clip.mp4 --camera-id <UUID> --detector auto --campus-id <UUID>
    python -m src.main --video clip.mp4 --camera-id <UUID> --detector synthetic --script script.json

Settings come from ``CV_*`` environment variables or ``--config settings.yaml`` (see ``config.py``).
Detectors:

* ``auto``: the modules switched on in the settings, run together on every sampled frame:
  person detection + tracking (YOLOX-Tiny), weapon detection (ONNX + manifest), fight/violence
  classification (clip model with a per-camera frame buffer), and cross-camera Re-ID as a track
  observer. Missing or mismatched weights stop the run (exit 2); there is no fallback.
* ``yolox-human``: the person detector only (as before).
* ``synthetic``: the **test-only** scripted detector (``--script``), reported as synthetic.

Prints a JSON report. Re-ID candidates, when enabled, are appended to ``CV_REID_OBSERVATIONS_PATH``
(JSONL) and summarised in the report; they are never sent as alerts.

Exit codes: 0 all events delivered; 1 some events undelivered; 2 stopped (configuration, input or
detector error); 64 bad command-line arguments.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import ValidationError

from src.config import PipelineSettings
from src.detection_publisher import HttpDetectionSink, SinkConfigurationError
from src.detectors.base_detector import Detector, DetectorError
from src.detectors.composite import CompositeDetector
from src.detectors.fight_detector import TemporalDetectorAdapter, ViolenceOnnxClassifier
from src.detectors.human_detector import YoloxHumanDetector
from src.detectors.synthetic_detector import SyntheticScriptedDetector
from src.detectors.weapon_detector import WeaponOnnxDetector
from src.model_loader import ModelLoadError
from src.pipeline import Pipeline, TrackObserver
from src.reid.embedding_store import CampusGallery
from src.reid.reid_extractor import (
    JsonlObservationSink,
    MemoryObservationSink,
    PersonReIdOnnxEmbedder,
    ReIdMatcher,
    ReIdObservationSink,
    ReIdTrackObserver,
)
from src.rules import EventRules
from src.runtime import (
    AiConfigWatcher,
    GatedDetector,
    HttpObservationSink,
    OverlayWriter,
)
from src.tracking.single_tracker import ByteTracker, IouTracker, Tracker
from src.video_source import (
    FrameSource,
    SpoolFrameSource,
    VideoFileSource,
    VideoSourceError,
)


def build_human_detector(settings: PipelineSettings) -> YoloxHumanDetector:
    return YoloxHumanDetector(
        settings.human_model_spec,
        score_threshold=settings.human_score_threshold,
        nms_iou=settings.human_nms_iou,
        device=settings.device,
    )


def build_auto_detector(settings: PipelineSettings) -> Detector:
    """The composite of every enabled module, in a fixed order: persons, weapons, fights."""
    parts: list[Detector] = []
    if settings.enable_human_tracking:
        parts.append(build_human_detector(settings))
    if settings.enable_weapon_detection:
        parts.append(
            WeaponOnnxDetector(
                settings.weapon_manifest,
                default_threshold=settings.weapon_default_threshold,
                thresholds=settings.weapon_class_thresholds,
                nms_iou=settings.weapon_nms_iou,
                device=settings.device,
            )
        )
    if settings.enable_fight_detection:
        classifier = ViolenceOnnxClassifier(settings.fight_manifest, device=settings.device)
        parts.append(
            TemporalDetectorAdapter(
                classifier,
                infer_every=settings.fight_infer_every,
                min_report_probability=settings.fight_min_report_probability,
                sample_fps=settings.sample_fps,
            )
        )
    if not parts:
        raise ValueError("no module is enabled: set CV_ENABLE_HUMAN_TRACKING, CV_ENABLE_WEAPON_DETECTION or CV_ENABLE_FIGHT_DETECTION")
    return parts[0] if len(parts) == 1 else CompositeDetector(parts)


def build_reid_observer(
    settings: PipelineSettings, campus_id: UUID, watcher: AiConfigWatcher | None = None
) -> ReIdTrackObserver:
    embedder = PersonReIdOnnxEmbedder(settings.reid_manifest, device=settings.device)
    gallery = CampusGallery(embedder.dimension, window_seconds=settings.reid_window_seconds, use_faiss=settings.reid_use_faiss)
    matcher = ReIdMatcher(
        gallery,
        likely_threshold=settings.reid_likely_threshold if settings.reid_likely_threshold is not None else embedder.likely_threshold,
        uncertain_threshold=settings.reid_uncertain_threshold if settings.reid_uncertain_threshold is not None else embedder.uncertain_threshold,
        window_seconds=settings.reid_window_seconds,
        top_k=settings.reid_top_k,
    )
    sink: ReIdObservationSink
    if settings.reid_sink == "core_api" and watcher is not None:
        sink = HttpObservationSink(watcher.client, watcher.token, f"{embedder.info.name}-{embedder.info.version}")
    elif settings.reid_observations_path:
        sink = JsonlObservationSink(settings.reid_observations_path)
    else:
        sink = MemoryObservationSink()
    return ReIdTrackObserver(
        embedder,
        matcher,
        campus_id=campus_id,
        sink=sink,
        every_n_frames=settings.reid_every_n_frames,
        allowed=(lambda: watcher.allows("person_reid")) if watcher is not None else None,
    )


def build_detector(name: str, settings: PipelineSettings, script: Path | None) -> Detector:
    if name == "auto":
        return build_auto_detector(settings)
    if name == "yolox-human":
        return build_human_detector(settings)
    assert script is not None
    return SyntheticScriptedDetector.from_json(script)


def build_tracker(settings: PipelineSettings, detector: Detector) -> Tracker:
    kind = settings.tracker
    if kind == "auto":
        kind = "iou" if detector.info.synthetic else "bytetrack"
    if kind == "bytetrack":
        return ByteTracker(
            high_threshold=settings.bytetrack_high_threshold,
            low_threshold=settings.bytetrack_low_threshold,
            new_track_threshold=settings.bytetrack_new_track_threshold,
            max_lost_frames=settings.bytetrack_max_lost_frames,
        )
    return IouTracker(
        iou_threshold=settings.tracker_iou_threshold, max_missed_frames=settings.tracker_max_missed_frames
    )


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CV pipeline on a recorded video.")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--video", type=Path, help="a recorded video file")
    inputs.add_argument("--spool-dir", type=Path, help="the ingestion service's frames/<camera id>/ folder (live camera)")
    parser.add_argument("--idle-timeout", type=float, default=None, help="with --spool-dir: stop after this many idle seconds")
    parser.add_argument("--camera-id", type=UUID, required=True)
    parser.add_argument("--campus-id", type=UUID, help="scopes the Re-ID gallery (required when CV_ENABLE_REID is on)")
    parser.add_argument(
        "--storage-root", type=Path, help="CCTV_STORAGE_ROOT: write live detection overlays for the Live page"
    )
    parser.add_argument(
        "--detector", choices=["auto", "yolox-human", "synthetic"], required=True, help="synthetic = test detector only"
    )
    parser.add_argument("--script", type=Path, help="JSON script for the synthetic detector")
    parser.add_argument("--config", type=Path, help="YAML settings file (same keys as CV_* without the prefix)")
    parser.add_argument("--start-time", type=datetime.fromisoformat, help="video start time (ISO 8601 with zone)")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("cv_inference")
    try:
        settings = PipelineSettings.from_sources(args.config)
    except (ValidationError, ValueError, OSError) as error:
        message = "; ".join(e["msg"] for e in error.errors()) if isinstance(error, ValidationError) else str(error)
        log.error("invalid settings: %s", message)
        return 2
    if args.detector == "synthetic" and args.script is None:
        log.error("--script is required for the synthetic detector")
        return 64
    watcher: AiConfigWatcher | None = None
    if settings.service_token.get_secret_value():
        # The camera's AI Configuration (decision D-024): which modules may run, reported back.
        watcher = AiConfigWatcher(
            args.camera_id,
            httpx.Client(base_url=settings.api_base_url.rstrip("/"), timeout=settings.request_timeout_seconds),
            settings.service_token.get_secret_value(),
            poll_seconds=settings.ai_config_poll_seconds,
        )
        if watcher.refresh(force=True) is None:
            log.error("cannot read the camera's AI configuration: %s", watcher.last_error)
            return 2
    campus_id = args.campus_id or (watcher.config.campus_id if watcher and watcher.config else None)
    use_reid = args.detector == "auto" and settings.enable_reid
    if use_reid and campus_id is None:
        log.error("--campus-id is required when CV_ENABLE_REID is on")
        return 64
    if args.storage_root is not None and watcher is None:
        log.error("--storage-root needs CV_SERVICE_TOKEN (the camera's organization and campus come from the Core API)")
        return 64
    observers: list[TrackObserver] = []
    reid: ReIdTrackObserver | None = None
    try:
        detector = build_detector(args.detector, settings, args.script)
        if watcher is not None:
            detector = GatedDetector(detector, watcher)
        if use_reid:
            assert campus_id is not None
            reid = build_reid_observer(settings, campus_id, watcher)
            observers.append(reid)
        if args.storage_root is not None and watcher is not None:
            observers.append(OverlayWriter(args.storage_root, watcher))
        source: FrameSource
        if args.spool_dir is not None:
            source = SpoolFrameSource(
                args.spool_dir, args.camera_id, idle_timeout_seconds=args.idle_timeout, max_frames=args.max_frames
            )
        else:
            source = VideoFileSource(
                args.video,
                args.camera_id,
                sample_fps=settings.sample_fps,
                start_time=args.start_time,
                max_frames=args.max_frames,
                max_decode_failures=settings.max_decode_failures,
            )
        sink = HttpDetectionSink(
            settings.api_base_url,
            settings.detection_ingest_token,
            timeout_seconds=settings.request_timeout_seconds,
            max_attempts=settings.max_attempts,
            backoff_seconds=settings.retry_backoff_seconds,
        )
    except (ValueError, VideoSourceError, SinkConfigurationError, ModelLoadError, DetectorError) as error:
        log.error("cannot start: %s", error)
        return 2
    log.info("modules: %s; device: %s", ", ".join(settings.enabled_modules()) if args.detector == "auto" else args.detector, settings.device)
    tracker = build_tracker(settings, detector)
    rules = EventRules(settings.rule_config(), model_name=detector.info.name)
    try:
        report = Pipeline(source, detector, tracker, rules, sink, observers=observers).run()
    finally:
        sink.close()
        detector.close()
        if reid is not None:
            reid.close()
        if watcher is not None:
            watcher.report(running=[])  # stopped: nothing runs on this camera now
            watcher.client.close()
    output = report.as_dict()
    if reid is not None:
        output["reid"] = {
            "tracks_seen": reid.stats.tracks_seen,
            "embeddings": reid.stats.embeddings,
            "observations": reid.stats.observations,
            "bands": reid.stats.bands,
            "crops_skipped": reid.stats.crops_skipped,
            "gallery_backend": reid.matcher.gallery.backend,
            "observations_path": str(settings.reid_observations_path) if settings.reid_observations_path else None,
        }
    print(json.dumps(output, default=str))
    if report.stopped_reason:
        return 2
    return 1 if report.undelivered else 0


if __name__ == "__main__":
    sys.exit(main())
