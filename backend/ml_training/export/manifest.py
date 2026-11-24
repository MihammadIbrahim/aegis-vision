"""The model manifest written next to every exported ONNX file (``<name>.manifest.json``).

The runtime (``services/cv_inference/src/manifest.py``) reads the same fields, so pre-processing,
output decoding, class labels and calibrated thresholds travel with the weights instead of being
re-typed in the serving code. Keep the two definitions in step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Task = Literal["weapon_detection", "violence_classification", "person_reid", "human_detection"]
OutputFormat = Literal["ultralytics-detect-v1", "ultralytics-rtdetr-v1", "softmax-probabilities", "l2-normalized-embedding"]


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    shape: list[int | str] = Field(description='e.g. ["batch", 3, 640, 640]; "batch" marks the dynamic axis')
    layout: Literal["NCHW", "NCTHW"]
    dtype: Literal["float32"] = "float32"
    color_order: Literal["RGB", "BGR"] = "RGB"
    scale: float = Field(default=1 / 255, description="pixel value multiplier before mean/std")
    mean: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    std: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    letterbox_pad_value: int | None = Field(default=None, description="detectors: letterbox padding grey value")
    clip_len: int | None = None
    target_fps: float | None = None
    resize_short: int | None = None
    crop: int | None = None


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    shape: list[int | str]
    format: OutputFormat
    box_format: Literal["xywh-pixels", "xywh-normalized"] | None = None


class ExportInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opset: int
    dynamic_batch: bool
    simplified: bool
    parity_max_abs_diff: float | None = None
    parity_tolerance: float
    parity_passed: bool
    exported_at: str
    exporter: str


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: Literal[1] = 1
    name: str
    version: str
    task: Task
    architecture: str
    framework: str
    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int
    input: InputSpec
    output: OutputSpec
    class_labels: list[str]
    thresholds: dict[str, float] = Field(default_factory=dict, description="calibrated operating points")
    dataset: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict, description="measured on the held-out test split")
    export: ExportInfo
    license: str
    notes: str = ""

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ModelManifest:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
