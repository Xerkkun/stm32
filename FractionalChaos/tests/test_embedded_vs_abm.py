from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


VALIDATION = Path(__file__).parents[1] / "validation"
sys.path.insert(0, str(VALIDATION))
MODULE_PATH = VALIDATION / "compare_embedded_to_abm.py"
SPEC = importlib.util.spec_from_file_location(
    "compare_embedded_to_abm",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_error_metrics_are_componentwise_and_exact() -> None:
    reference = np.asarray(
        [[0.0, 0.0, 0.0], [2.0, 4.0, 8.0]], dtype=np.float64
    )
    candidate = reference + np.asarray(
        [[0.0, 0.0, 0.0], [2.0, -2.0, 4.0]], dtype=np.float64
    )

    metrics = MODULE.error_metrics(reference, candidate)

    assert metrics["component_rmse"] == pytest.approx(
        [np.sqrt(2.0), np.sqrt(2.0), np.sqrt(8.0)]
    )
    assert metrics["normalized_component_rmse"] == pytest.approx(
        [np.sqrt(2.0) / 2.0, np.sqrt(2.0) / 4.0, np.sqrt(8.0) / 8.0]
    )
    assert metrics["maximum_absolute_component_error"] == 4.0
    assert metrics["final_state_error_norm"] == pytest.approx(
        np.sqrt(24.0)
    )


def test_group_trajectory_rejects_missing_step() -> None:
    rows = [
        {
            "system": "0",
            "method": "0",
            "representation": "float32",
            "step": "0",
            "x": "0",
            "y": "0",
            "z": "0",
            "x_raw": "0",
            "y_raw": "0",
            "z_raw": "0",
            "status": "0",
            "state_saturations": "0",
            "coefficient_saturations": "0",
            "zeroed_nonzero_coefficients": "0",
        }
    ]
    with pytest.raises(MODULE.ComparisonError, match="secuencia incompleta"):
        MODULE.group_trajectory(
            rows,
            system=0,
            method=0,
            representation="float32",
            steps=1,
        )
