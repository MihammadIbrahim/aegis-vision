"""The Re-ID appearance model for Person Search by photo (decision D-024).

The same ONNX model and manifest format as the vision workers (``services/cv_inference``, task
``person_reid``): a person photo becomes a 512-number unit vector, compared with the observations the
workers recorded. The model comes from ``ml_training`` (GPU training, not part of this repository's
built artefacts); until ``REID_MODEL_MANIFEST`` points to one, photo search answers 503
``REID_MODEL_UNAVAILABLE`` and search by an observation still works.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIMENSION = 512


class ReIdModelUnavailable(Exception):
    """No usable Re-ID model is configured on this server."""


@dataclass(frozen=True, slots=True)
class Thresholds:
    likely: float
    uncertain: float


class OnnxReIdEmbedder:
    def __init__(self, manifest_path: str | Path) -> None:
        try:
            import numpy as np
            import onnxruntime as ort  # type: ignore[import-untyped]
        except ImportError as error:  # pragma: no cover - depends on the installation
            raise ReIdModelUnavailable("onnxruntime and numpy are needed for photo search.") from error
        path = Path(manifest_path)
        try:
            manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ReIdModelUnavailable("The Re-ID model manifest cannot be read.") from error
        if manifest.get("task") != "person_reid":
            raise ReIdModelUnavailable("The manifest does not describe a Re-ID model.")
        model = path.parent / str(manifest.get("file", ""))
        try:
            data = model.read_bytes()
        except OSError as error:
            raise ReIdModelUnavailable("The Re-ID model file is missing.") from error
        if hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            raise ReIdModelUnavailable("The Re-ID model file does not match its manifest.")
        spec = manifest["input"]
        self._np = np
        self._session = ort.InferenceSession(data, providers=["CPUExecutionProvider"])
        self._input = str(spec["name"])
        self._size = (int(spec["width"]), int(spec["height"]))
        self._mean = np.asarray(spec.get("mean", [0.485, 0.456, 0.406]), np.float32)
        self._std = np.asarray(spec.get("std", [0.229, 0.224, 0.225]), np.float32)
        self._scale = float(spec.get("scale", 1 / 255))
        self.version = f"{manifest.get('name', 'reid')}-{manifest.get('version', '?')}"
        thresholds = manifest.get("thresholds", {})
        self.thresholds = Thresholds(
            float(thresholds.get("likely_match", 0.75)), float(thresholds.get("uncertain", 0.55))
        )
        if int(manifest["output"]["shape"][-1]) != DIMENSION:
            raise ReIdModelUnavailable(f"The Re-ID model must give {DIMENSION} numbers.")

    def embed(self, image_bytes: bytes) -> list[float]:
        from PIL import Image

        np = self._np
        with Image.open(io.BytesIO(image_bytes)) as image:
            rgb = image.convert("RGB").resize(self._size, Image.Resampling.BILINEAR)
            array = (np.asarray(rgb, dtype=np.float32) * self._scale - self._mean) / self._std
        tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None])
        vector = np.asarray(self._session.run(None, {self._input: tensor})[0], dtype=np.float32).reshape(-1)
        if vector.shape[0] != DIMENSION or not np.isfinite(vector).all():
            raise ReIdModelUnavailable("The Re-ID model returned an unexpected result.")
        norm = float(np.linalg.norm(vector))
        return [float(v) for v in (vector / norm if norm > 0 else vector)]


_CACHE: dict[str, OnnxReIdEmbedder] = {}


def load_embedder(manifest_path: str | None) -> OnnxReIdEmbedder:
    if not manifest_path:
        raise ReIdModelUnavailable("No Re-ID model is configured (REID_MODEL_MANIFEST).")
    if manifest_path not in _CACHE:
        _CACHE[manifest_path] = OnnxReIdEmbedder(manifest_path)
    return _CACHE[manifest_path]
