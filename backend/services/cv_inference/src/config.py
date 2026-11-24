"""Pipeline settings, from environment variables with the ``CV_`` prefix (or a ``.env`` file).
Nothing operational is hard-coded in the pipeline itself.

    CV_API_BASE_URL                  Core API, e.g. http://127.0.0.1:8000
    CV_DETECTION_INGEST_TOKEN        the same secret as Core API's DETECTION_INGEST_TOKEN
    CV_SAMPLE_FPS                    frames analysed per second of video (default 5, CPU-friendly)
    CV_CLASS_MAP                     JSON {"model label": "WEAPON" | "FIGHT_VIOLENCE" | "RESTRICTED_AREA"}
    CV_CLASS_THRESHOLDS              JSON {"model label": 0.0–1.0}; CV_DEFAULT_THRESHOLD for the rest
    CV_MIN_CONSECUTIVE_FRAMES        sampled frames in a row before an event (default 3)
    CV_COOLDOWN_SECONDS              at most one event per this many seconds per camera and type (10)
    CV_BACKEND_DEDUP_SECONDS         Core API's ALERT_DEDUP_SECONDS (60); the cooldown must be shorter
    CV_HUMAN_MODEL_SPEC              metadata file of the person model (weights/yolox_tiny.model.json)
    CV_HUMAN_SCORE_THRESHOLD         person score threshold (0.35); CV_HUMAN_NMS_IOU (0.45)
    CV_DEVICE                        cpu (default) | cuda | tensorrt | auto (TensorRT → CUDA → CPU); a missing provider is an error
    CV_TRACKER                       auto (ByteTrack for real models, IoU for the synthetic one) | iou | bytetrack

Module switches (``--detector auto`` builds the pipeline from them):
    CV_ENABLE_HUMAN_TRACKING         person detection + tracking (default true; required by Re-ID)
    CV_ENABLE_WEAPON_DETECTION       weapon detector (CV_WEAPON_MANIFEST, CV_WEAPON_DEFAULT_THRESHOLD, CV_WEAPON_NMS_IOU)
    CV_ENABLE_FIGHT_DETECTION        clip classifier (CV_FIGHT_MANIFEST, CV_FIGHT_INFER_EVERY, CV_FIGHT_MIN_REPORT_PROBABILITY)
    CV_ENABLE_REID                   cross-camera Re-ID observer (CV_REID_MANIFEST, CV_REID_WINDOW_SECONDS, CV_REID_EVERY_N_FRAMES,
                                     CV_REID_LIKELY_THRESHOLD / CV_REID_UNCERTAIN_THRESHOLD override the manifest's calibrated bands,
                                     CV_REID_OBSERVATIONS_PATH: JSONL file for the observations when CV_REID_SINK=file)
    CV_REID_SINK                     file (default) | core_api: send Re-ID observations to POST /internal/reid/observations
    CV_SERVICE_TOKEN                 the Core API's INTERNAL_SERVICE_TOKEN: AI configuration, worker status, Re-ID intake,
                                     camera scope for --storage-root (decision D-024)
    CV_AI_CONFIG_POLL_SECONDS        how often the camera's effective AI modules are re-read (default 30)
    CV_CONFIG_FILE                   optional YAML with the same keys (without the CV_ prefix); environment variables win
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.rules import RuleConfig
from src.schemas import AlertType


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CV_", env_file=".env", extra="ignore")

    api_base_url: str = "http://127.0.0.1:8000"
    detection_ingest_token: SecretStr = SecretStr("")
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.5, ge=0, le=30)

    sample_fps: float = Field(default=5.0, gt=0, le=60)
    max_decode_failures: int = Field(default=5, ge=0)

    class_map: dict[str, AlertType] = Field(
        default_factory=lambda: {
            "weapon": AlertType.WEAPON,
            "handgun": AlertType.WEAPON,
            "pistol": AlertType.WEAPON,
            "rifle": AlertType.WEAPON,
            "knife": AlertType.WEAPON,
            "fight_violence": AlertType.FIGHT_VIOLENCE,
        }
    )
    class_thresholds: dict[str, float] = Field(default_factory=dict)
    default_threshold: float = Field(default=0.5, ge=0, le=1)
    min_consecutive_frames: int = Field(default=3, ge=1, le=100)
    cooldown_seconds: float = Field(default=10.0, ge=0)
    reset_after_missing_frames: int = Field(default=2, ge=0, le=100)
    backend_dedup_seconds: float = Field(default=60.0, ge=0)

    tracker: Literal["auto", "iou", "bytetrack"] = "auto"
    tracker_iou_threshold: float = Field(default=0.3, gt=0, le=1)
    tracker_max_missed_frames: int = Field(default=10, ge=0)
    bytetrack_high_threshold: float = Field(default=0.5, ge=0, le=1)
    bytetrack_low_threshold: float = Field(default=0.1, ge=0, le=1)
    bytetrack_new_track_threshold: float = Field(default=0.6, ge=0, le=1)
    bytetrack_max_lost_frames: int = Field(default=30, ge=0)  # sampled frames (6 s at 5 fps)

    human_model_spec: Path = Path("weights/yolox_tiny.model.json")
    human_score_threshold: float = Field(default=0.35, gt=0, lt=1)
    human_nms_iou: float = Field(default=0.45, gt=0, le=1)
    device: Literal["cpu", "cuda", "tensorrt", "auto"] = "cpu"

    # Module switches and the trained models (ONNX + manifest, from ml_training/export)
    enable_human_tracking: bool = True
    enable_weapon_detection: bool = False
    weapon_manifest: Path = Path("weights/weapon-yolo11m.manifest.json")
    weapon_default_threshold: float = Field(default=0.4, gt=0, lt=1)
    weapon_class_thresholds: dict[str, float] = Field(default_factory=dict)  # overrides the manifest's calibrated values
    weapon_nms_iou: float | None = Field(default=None, gt=0, le=1)
    enable_fight_detection: bool = False
    fight_manifest: Path = Path("weights/fight-swin3d-t.manifest.json")
    fight_infer_every: int = Field(default=4, ge=1, le=64)
    fight_min_report_probability: float = Field(default=0.05, ge=0, le=1)
    enable_reid: bool = False
    reid_manifest: Path = Path("weights/reid-osnet-x1-0.manifest.json")
    reid_window_seconds: float = Field(default=600.0, gt=0)
    reid_every_n_frames: int = Field(default=5, ge=1, le=100)
    reid_top_k: int = Field(default=5, ge=1, le=50)
    reid_likely_threshold: float | None = Field(default=None, ge=-1, le=1)
    reid_uncertain_threshold: float | None = Field(default=None, ge=-1, le=1)
    reid_observations_path: Path | None = None
    reid_use_faiss: bool = True
    # Decision D-024: the camera's AI configuration and Person Search intake in the Core API.
    service_token: SecretStr = SecretStr("")  # CV_SERVICE_TOKEN = the Core API's INTERNAL_SERVICE_TOKEN
    ai_config_poll_seconds: float = Field(default=30.0, ge=1, le=3600)
    reid_sink: Literal["file", "core_api"] = "file"

    @model_validator(mode="after")
    def _cooldown_fits_backend_window(self) -> PipelineSettings:
        if self.backend_dedup_seconds and self.cooldown_seconds >= self.backend_dedup_seconds:
            raise ValueError(
                "CV_COOLDOWN_SECONDS must be shorter than the backend's ALERT_DEDUP_SECONDS "
                "(CV_BACKEND_DEDUP_SECONDS), or a continuing event would open a second alert."
            )
        return self

    @model_validator(mode="after")
    def _reid_needs_tracking_and_bands_in_order(self) -> PipelineSettings:
        if self.enable_reid and not self.enable_human_tracking:
            raise ValueError("CV_ENABLE_REID needs CV_ENABLE_HUMAN_TRACKING: Re-ID embeds tracked persons.")
        if self.reid_likely_threshold is not None and self.reid_uncertain_threshold is not None and self.reid_uncertain_threshold > self.reid_likely_threshold:
            raise ValueError("CV_REID_UNCERTAIN_THRESHOLD must not exceed CV_REID_LIKELY_THRESHOLD.")
        return self

    @classmethod
    def from_sources(cls, config_file: Path | str | None = None, **overrides: Any) -> PipelineSettings:
        """YAML file values (keys without the ``CV_`` prefix) under environment variables under
        explicit overrides. ``config_file`` defaults to ``CV_CONFIG_FILE``."""
        path = Path(config_file) if config_file else (Path(os.environ["CV_CONFIG_FILE"]) if os.environ.get("CV_CONFIG_FILE") else None)
        values: dict[str, Any] = {}
        if path is not None:
            with path.open(encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} must contain a mapping of settings")
            values = {str(k).lower().removeprefix("cv_"): v for k, v in loaded.items()}
        env = {k[3:].lower(): v for k, v in os.environ.items() if k.startswith("CV_") and k != "CV_CONFIG_FILE"}
        for key in env:
            values.pop(key, None)  # the environment wins over the file; pydantic reads it itself
        return cls(**{**values, **overrides})

    def enabled_modules(self) -> list[str]:
        names = []
        if self.enable_human_tracking:
            names.append("human_tracking")
        if self.enable_weapon_detection:
            names.append("weapon_detection")
        if self.enable_fight_detection:
            names.append("fight_detection")
        if self.enable_reid:
            names.append("reid")
        return names

    def rule_config(self) -> RuleConfig:
        return RuleConfig(
            class_map=dict(self.class_map),
            thresholds=dict(self.class_thresholds),
            default_threshold=self.default_threshold,
            min_consecutive_frames=self.min_consecutive_frames,
            cooldown_seconds=self.cooldown_seconds,
            reset_after_missing_frames=self.reset_after_missing_frames,
        )
