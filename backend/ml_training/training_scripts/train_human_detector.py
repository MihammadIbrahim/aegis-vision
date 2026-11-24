"""Person detection: fine-tune a YOLO11 model on a person dataset (CrowdHuman, converted to the YOLO
layout) as a **candidate** replacement for the pretrained YOLOX-Tiny the runtime uses today.

    python -m training_scripts.train_human_detector prepare --config configs/human_yolo.yaml
    python -m training_scripts.train_human_detector train --config configs/human_yolo.yaml device=0

Same trainer and metrics as the weapon detector (``_ultralytics_common``). Before switching the
runtime's person model, compare the test metrics and the licence (ultralytics is AGPL-3.0; YOLOX is
Apache-2.0) and record the decision in ``experiments/model_registry.md``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from training_scripts.train_weapon_detector import main as _main


def main(argv: Sequence[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
