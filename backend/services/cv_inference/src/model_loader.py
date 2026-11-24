"""Model loading and execution, separate from any detector's pre/post-processing.

``ModelAdapter`` is what a real detector will call. ``OnnxRuntimeAdapter`` is the first backend:
models trained in ``ml_training/`` are exported to ONNX and served here (architecture Part 6).

* **Device:** ``cpu``, ``cuda``, ``tensorrt`` or ``auto``. Asking for ``cuda`` or ``tensorrt`` when that
  execution provider is missing is an error, never a silent CPU fallback; ``auto`` picks the best
  available (TensorRT → CUDA → CPU) and records which in ``providers``. TensorRT keeps CUDA and CPU
  behind it, as ONNX Runtime requires, so unsupported nodes still run on the GPU or CPU.
* **Latency:** every ``run`` is timed, so performance figures come from measurements.

Tested with a tiny generated ONNX graph (``tests/fixtures/``) and, when its weights are present, with
the YOLOX-Tiny person detector (``detectors/human_detector.py``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from src.schemas import ModelInfo

Device = Literal["cpu", "cuda", "tensorrt", "auto"]


class ModelLoadError(Exception):
    """The model file is missing or invalid, or the requested device is unavailable."""


class InferenceError(Exception):
    """The model failed to run on the given inputs."""


@dataclass
class InferenceStats:
    runs: int = 0
    total_ms: float = 0.0
    last_ms: float = 0.0
    samples_ms: list[float] = field(default_factory=list, repr=False)

    def record(self, elapsed_ms: float) -> None:
        self.runs += 1
        self.total_ms += elapsed_ms
        self.last_ms = elapsed_ms
        if len(self.samples_ms) < 10_000:
            self.samples_ms.append(elapsed_ms)

    @property
    def mean_ms(self) -> float:
        return self.total_ms / self.runs if self.runs else 0.0

    def percentile_ms(self, q: float) -> float:
        return float(np.percentile(self.samples_ms, q)) if self.samples_ms else 0.0


class ModelAdapter(Protocol):
    info: ModelInfo
    stats: InferenceStats

    @property
    def input_names(self) -> list[str]: ...

    def run(self, inputs: dict[str, np.ndarray]) -> list[np.ndarray]: ...

    def close(self) -> None: ...


_PROVIDERS = {"cpu": "CPUExecutionProvider", "cuda": "CUDAExecutionProvider", "tensorrt": "TensorrtExecutionProvider"}
# Provider chains per device; ONNX Runtime falls through the list node by node.
_CHAINS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "tensorrt": ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
}


def choose_providers(device: Device, available: list[str]) -> list[str]:
    """The provider chain for ``device``; ``auto`` = TensorRT if present, else CUDA, else CPU."""
    if device == "auto":
        for candidate in ("tensorrt", "cuda", "cpu"):
            if _PROVIDERS[candidate] in available:
                return [p for p in _CHAINS[candidate] if p in available]
        return ["CPUExecutionProvider"]
    if _PROVIDERS[device] not in available:
        raise ModelLoadError(f"The {device} execution provider is not available here (have: {', '.join(available)}).")
    return [p for p in _CHAINS[device] if p in available]


class OnnxRuntimeAdapter:
    def __init__(self, model_path: Path | str, info: ModelInfo, *, device: Device = "cpu") -> None:
        import onnxruntime as ort  # imported here: only processes that serve models need it

        path = Path(model_path)
        if not path.is_file():
            raise ModelLoadError(f"Model file not found: {path.name}")
        chosen = choose_providers(device, list(ort.get_available_providers()))
        try:
            self._session = ort.InferenceSession(str(path), providers=chosen)
        except Exception as error:  # onnxruntime raises its own exception types
            raise ModelLoadError(f"Cannot load model {path.name}: {type(error).__name__}") from error
        self.info = info
        self.stats = InferenceStats()
        self.providers = list(self._session.get_providers())

    @property
    def input_names(self) -> list[str]:
        return [i.name for i in self._session.get_inputs()]

    def input_details(self) -> list[tuple[str, list[int | str | None]]]:
        """(name, shape) of each input, as declared by the model (dynamic dimensions are strings)."""
        return [(i.name, list(i.shape)) for i in self._session.get_inputs()]

    def output_details(self) -> list[tuple[str, list[int | str | None]]]:
        return [(o.name, list(o.shape)) for o in self._session.get_outputs()]

    def run(self, inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        missing = set(self.input_names) - set(inputs)
        if missing:
            raise InferenceError(f"Missing model inputs: {', '.join(sorted(missing))}")
        started = time.perf_counter()
        try:
            outputs = self._session.run(None, inputs)
        except Exception as error:
            raise InferenceError(f"Inference failed: {type(error).__name__}") from error
        self.stats.record((time.perf_counter() - started) * 1000)
        return [np.asarray(o) for o in outputs]

    def close(self) -> None:
        self._session = None  # type: ignore[assignment]
