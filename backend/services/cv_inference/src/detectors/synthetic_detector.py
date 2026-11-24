"""A **synthetic, test-only** detector. It does not look at the image at all: it returns detections
from a fixed script keyed by frame index. It exists to test and develop the pipeline without model
weights or a GPU. Its ``ModelInfo.synthetic`` is True and its name says "synthetic"; it must never be
presented as a real model, and its events carry the ``synthetic-`` prefix in their detection IDs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.schemas import BoundingBox, Detection, Frame, ModelInfo


@dataclass(frozen=True, slots=True)
class ScriptEntry:
    from_frame: int
    to_frame: int  # inclusive
    label: str
    confidence: float
    box: BoundingBox

    def __post_init__(self) -> None:
        if self.from_frame < 0 or self.to_frame < self.from_frame:
            raise ValueError("script frame range is invalid")


class SyntheticScriptedDetector:
    def __init__(self, script: list[ScriptEntry]) -> None:
        self._script = list(script)
        labels = tuple(sorted({entry.label for entry in self._script}))
        self.info = ModelInfo(
            name="synthetic-scripted-detector",
            version="test",
            labels=labels,
            synthetic=True,
            license=None,
            source="tests / development only; not a model",
        )

    @classmethod
    def from_json(cls, path: Path | str) -> SyntheticScriptedDetector:
        """``[{"from_frame": 0, "to_frame": 9, "label": "weapon", "confidence": 0.9,
        "box": [x1, y1, x2, y2]}, …]``"""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            entries = [
                ScriptEntry(
                    from_frame=int(item["from_frame"]),
                    to_frame=int(item["to_frame"]),
                    label=str(item["label"]),
                    confidence=float(item["confidence"]),
                    box=BoundingBox(*(float(v) for v in item["box"])),
                )
                for item in raw
            ]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Invalid synthetic detector script: {error}") from error
        return cls(entries)

    def detect(self, frame: Frame) -> list[Detection]:
        return [
            Detection(label=e.label, confidence=e.confidence, box=e.box)
            for e in self._script
            if e.from_frame <= frame.index <= e.to_frame
        ]

    def close(self) -> None:
        return None
