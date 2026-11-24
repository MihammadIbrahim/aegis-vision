"""Kept under the name the architecture document uses; the exporter lives in ``export_to_onnx.py``.

    python -m export.convert_to_onnx fight --checkpoint runs/fight_swin3d_t/best.pt --out export/fight --name fight-swin3d-t --version 1.0.0
"""

from __future__ import annotations

import sys

from export.export_to_onnx import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
