"""Runs several detectors on each frame and concatenates their detections, so the person detector,
the weapon detector and the fight classifier can be switched on independently (``CV_ENABLE_*``)
without changing the pipeline. Labels stay the models' own; ``EventRules.class_map`` decides which
become alerts (persons never do). With a camera's AI configuration, ``runtime.GatedDetector`` calls each of
``detectors`` itself and skips the models of modules the camera may not run (decision D-028)."""

from __future__ import annotations

from collections.abc import Sequence

from src.detectors.base_detector import Detector
from src.schemas import Detection, Frame, ModelInfo


class CompositeDetector:
    def __init__(self, detectors: Sequence[Detector]) -> None:
        if not detectors:
            raise ValueError("at least one detector is required")
        self.detectors = list(detectors)
        labels: list[str] = []
        for detector in self.detectors:
            labels += [label for label in detector.info.labels if label not in labels]
        self.info = ModelInfo(
            name="+".join(d.info.name for d in self.detectors),
            version=";".join(f"{d.info.name}={d.info.version}" for d in self.detectors),
            labels=tuple(labels),
            synthetic=any(d.info.synthetic for d in self.detectors),
            license="; ".join(f"{d.info.name}: {d.info.license}" for d in self.detectors if d.info.license),
            source="; ".join(f"{d.info.name}: {d.info.source}" for d in self.detectors if d.info.source),
        )

    def detect(self, frame: Frame) -> list[Detection]:
        detections: list[Detection] = []
        for detector in self.detectors:
            detections += detector.detect(frame)
        return detections

    def close(self) -> None:
        for detector in self.detectors:
            detector.close()
