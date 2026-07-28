#!/usr/bin/env python3
"""Unit tests for the ABM long-horizon dynamic-qualification layer."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

from long_horizon_qualification import (  # noqa: E402
    compare_resolutions,
    manifest_decision,
    normalized_permutation_entropy,
    validate_criteria,
)


class ABMLongHorizonQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.criteria = json.loads(
            (VALIDATION_ROOT / "long_horizon_criteria.json").read_text(
                encoding="utf-8"
            )
        )

    def test_predeclared_criteria_are_internally_consistent(self) -> None:
        validate_criteria(self.criteria)

    def test_monotone_series_has_zero_permutation_entropy(self) -> None:
        values = np.arange(100, dtype=np.float64)
        entropy = normalized_permutation_entropy(values, order=5, delay=2)
        self.assertEqual(entropy, 0.0)

    def test_permutation_entropy_rejects_short_series(self) -> None:
        with self.assertRaises(ValueError):
            normalized_permutation_entropy(
                np.asarray([0.0, 1.0]),
                order=5,
                delay=1,
            )

    def test_identical_resolution_summaries_pass_cross_screen(self) -> None:
        diagnostics = {
            "component_summary": {
                "mean": [0.0, 1.0, -1.0],
                "standard_deviation": [1.0, 2.0, 3.0],
                "quantiles": [
                    [-2.0, -3.0, -5.0],
                    [-1.0, 0.0, -2.0],
                    [0.0, 1.0, -1.0],
                    [1.0, 2.0, 0.0],
                    [2.0, 5.0, 3.0],
                ],
            },
            "nonperiodicity_screen": {
                "normalized_permutation_entropy": 0.75
            },
        }
        result = compare_resolutions(
            diagnostics,
            diagnostics,
            self.criteria,
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["maximum_mean_difference_in_pooled_std"], 0.0)
        self.assertEqual(result["maximum_symmetric_std_ratio"], 1.0)

    def test_manifest_decision_keeps_dynamic_failure_separate(self) -> None:
        passing = {
            name: {"passed": True}
            for name in (
                "observed_boundedness",
                "activity",
                "within_resolution_stability",
                "nonperiodicity_screen",
            )
        }
        resolutions = [
            {"resolution_divisor": 2, "diagnostics": passing},
            {"resolution_divisor": 4, "diagnostics": passing},
        ]
        decision, reasons = manifest_decision(
            resolutions,
            {"passed": True},
        )
        self.assertEqual(
            decision,
            "qualified_observed_long_horizon_screen",
        )
        self.assertEqual(reasons, [])

        failing = {
            **passing,
            "nonperiodicity_screen": {"passed": False},
        }
        decision, reasons = manifest_decision(
            [
                resolutions[0],
                {"resolution_divisor": 4, "diagnostics": failing},
            ],
            {"passed": True},
        )
        self.assertEqual(decision, "not_qualified_dynamic_screen_failed")
        self.assertIn("h/4 failed nonperiodicity_screen", reasons)


if __name__ == "__main__":
    unittest.main()
