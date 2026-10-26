"""
HealthPlus AI v31 — FastAPI pipeline.

Delegates A–D to medmodel/LayerABCD (improved A, two-compartment B,
TVMCD C with SahaPlus weights, logistic D).

Copyright (c) 2026 SVT Systems
All rights reserved.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LAYERABCD = Path(__file__).resolve().parent.parent / "LayerABCD"
if str(_LAYERABCD) not in sys.path:
    sys.path.insert(0, str(_LAYERABCD))

from layer_a_absorption_improved import nutrients_data  # noqa: E402
from layer_c_damage import CLUSTER_NAMES  # noqa: E402
from original_pipeline import run_original_pipeline  # noqa: E402

NUTRIENT_IDS = [n[0] for n in nutrients_data]
CLUSTERS = list(CLUSTER_NAMES)


def run_pipeline(payload: dict) -> dict:
    return run_original_pipeline(payload)
