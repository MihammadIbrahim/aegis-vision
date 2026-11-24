"""Shared test helpers: synthetic videos made with OpenCV at test time (no datasets, no weights)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import cv2
import numpy as np
import pytest

from src.schemas import BoundingBox, Detection, Frame

START = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)


def write_video(path: Path, *, frames: int, fps: float = 25.0, size: tuple[int, int] = (64, 48)) -> Path:
    """A plain MJPG video whose frame brightness encodes its index (synthetic pixels only)."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    for i in range(frames):
        writer.write(np.full((size[1], size[0], 3), (i * 5) % 255, np.uint8))
    writer.release()
    return path


@pytest.fixture
def video(tmp_path: Path) -> Callable[..., Path]:
    def make(name: str = "clip.avi", **kwargs: object) -> Path:
        return write_video(tmp_path / name, **kwargs)  # type: ignore[arg-type]

    return make


@pytest.fixture
def camera_id() -> UUID:
    return uuid4()


def frame(camera: UUID, index: int, *, seconds: float | None = None, source: str = "file:test") -> Frame:
    from datetime import timedelta

    return Frame(
        camera_id=camera,
        source_id=source,
        index=index,
        timestamp=START + timedelta(seconds=index / 5 if seconds is None else seconds),
        image=np.zeros((4, 4, 3), np.uint8),
    )


def box(x: float = 10, y: float = 10, w: float = 20, h: float = 20) -> BoundingBox:
    return BoundingBox(x, y, x + w, y + h)


def det(label: str = "weapon", confidence: float = 0.9, b: BoundingBox | None = None) -> Detection:
    return Detection(label=label, confidence=confidence, box=b or box())


# --- Real-model helpers ------------------------------------------------------------------------------

WEIGHTS_SPEC = Path(__file__).resolve().parents[1] / "weights" / "yolox_tiny.model.json"
REAL_MODEL = (WEIGHTS_SPEC.parent / "yolox_tiny.onnx").is_file()
requires_real_model = pytest.mark.skipif(
    not REAL_MODEL, reason="YOLOX-Tiny weights not downloaded (see weights/README.md); real-model test skipped"
)


def person_photo() -> np.ndarray:
    """A real photograph of a person: the public-domain US Navy portrait of Grace Hopper that ships with
    matplotlib's sample data. Not a dataset; no download."""
    import matplotlib.cbook as cbook

    image = cv2.imread(cbook.get_sample_data("grace_hopper.jpg", asfileobj=False))
    assert image is not None
    return image


def write_person_video(path: Path, *, frames: int = 50, fps: float = 25.0) -> Path:
    """The photo, scaled down, sliding slowly across a grey 640×480 canvas: one person, moving."""
    person = cv2.resize(person_photo(), (150, 176))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (640, 480))
    assert writer.isOpened()
    for i in range(frames):
        canvas = np.full((480, 640, 3), 128, np.uint8)
        x = 40 + 4 * i
        canvas[150:326, x : x + 150] = person
        writer.write(canvas)
    writer.release()
    return path
