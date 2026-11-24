"""The model manifest written by ``ml_training/export/export_to_onnx.py`` next to each ONNX file
(``<name>.manifest.json``). It carries what the runtime must know to use the weights correctly:
pre-processing, output format, class labels, calibrated thresholds, and the file's checksum.

This is a mirror of ``ml_training/export/manifest.py``; keep the two in step. A detector refuses to
start when the manifest is invalid, the file is missing, or the checksum does not match: the wrong
model is never run silently.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.model_loader import Device, ModelLoadError, OnnxRuntimeAdapter
from src.schemas import ModelInfo

Task = Literal["weapon_detection", "violence_classification", "person_reid", "human_detection"]
OutputFormat = Literal["ultralytics-detect-v1", "ultralytics-rtdetr-v1", "softmax-probabilities", "l2-normalized-embedding"]


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    shape: list[int | str]
    layout: Literal["NCHW", "NCTHW"]
    dtype: Literal["float32"] = "float32"
    color_order: Literal["RGB", "BGR"] = "RGB"
    scale: float = 1 / 255
    mean: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    std: list[float] = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    letterbox_pad_value: int | None = None
    clip_len: int | None = None
    target_fps: float | None = None
    resize_short: int | None = None
    crop: int | None = None

    @property
    def height(self) -> int:
        return int(self.shape[-2])

    @property
    def width(self) -> int:
        return int(self.shape[-1])


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    shape: list[int | str]
    format: OutputFormat
    box_format: Literal["xywh-pixels", "xywh-normalized"] | None = None


class ExportInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opset: int
    dynamic_batch: bool
    simplified: bool
    parity_max_abs_diff: float | None = None
    parity_tolerance: float
    parity_passed: bool
    exported_at: str
    exporter: str


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
    thresholds: dict[str, float] = Field(default_factory=dict)
    dataset: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    export: ExportInfo
    license: str
    notes: str = ""

    @classmethod
    def load(cls, path: Path | str) -> ModelManifest:
        try:
            return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            raise ModelLoadError(f"Model manifest not found or unreadable: {Path(path).name}") from error
        except ValidationError as error:
            raise ModelLoadError(f"Invalid model manifest {Path(path).name}: {error}") from error

    def model_info(self) -> ModelInfo:
        return ModelInfo(
            name=self.name,
            version=self.version,
            labels=tuple(self.class_labels),
            synthetic=False,
            license=self.license,
            source=f"{self.architecture}; trained {self.training.get('finished_at') or 'n/a'}, commit {self.training.get('commit') or 'n/a'}",
        )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model(manifest_path: Path | str, *, expected_task: Task, device: Device = "cpu", verify_checksum: bool = True) -> tuple[ModelManifest, OnnxRuntimeAdapter]:
    """Validates the manifest and the file, then opens an ONNX Runtime session on ``device``."""
    path = Path(manifest_path)
    manifest = ModelManifest.load(path)
    if manifest.task != expected_task:
        raise ModelLoadError(f"{path.name} describes a {manifest.task} model, not {expected_task}.")
    if not manifest.export.parity_passed:
        raise ModelLoadError(f"{manifest.name} failed its export parity check; re-export it before use.")
    model_file = path.parent / manifest.file
    if not model_file.is_file():
        raise ModelLoadError(f"Model file {manifest.file} not found next to {path.name}. See weights/README.md.")
    if verify_checksum and sha256_of(model_file) != manifest.sha256:
        raise ModelLoadError(f"Checksum mismatch for {manifest.file}: not the model the manifest describes.")
    adapter = OnnxRuntimeAdapter(model_file, manifest.model_info(), device=device)
    inputs = adapter.input_details()
    if [name for name, _ in inputs] != [manifest.input.name]:
        raise ModelLoadError(f"Model inputs {[n for n, _ in inputs]} do not match the manifest ({manifest.input.name}).")
    actual = list(inputs[0][1])
    expected = list(manifest.input.shape)
    for a, e in zip(actual[1:], expected[1:], strict=True):
        if isinstance(e, int) and a != e:
            raise ModelLoadError(f"Model input shape {actual} does not match the manifest {expected}.")
    return manifest, adapter
