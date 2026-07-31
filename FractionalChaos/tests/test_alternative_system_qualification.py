#!/usr/bin/env python3
"""Regression tests for alternative-system qualification and ranking."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

from qualify_alternative_systems import (  # noqa: E402
    continuous_acceptance_margins,
)


class AlternativeSystemQualificationTests(unittest.TestCase):
    def test_cross_entropy_uses_distinct_observed_and_threshold_names(
        self,
    ) -> None:
        diagnostics = {
            "observed_boundedness": {
                "threshold": 1000.0,
                "maximum_absolute_state": 10.0,
            },
            "within_resolution_stability": {
                "thresholds": {
                    "maximum_block_mean_shift_in_global_std": 1.5,
                    "minimum_block_to_global_std_ratio": 0.25,
                    "maximum_block_to_global_std_ratio": 4.0,
                },
                "maximum_block_mean_shift_in_global_std": 0.5,
                "minimum_block_to_global_std_ratio": 0.5,
                "maximum_block_to_global_std_ratio": 2.0,
            },
            "nonperiodicity_screen": {
                "thresholds": {
                    "minimum_normalized_lag_rmse": 0.05,
                    "minimum_normalized_permutation_entropy": 0.25,
                },
                "minimum_normalized_lag_rmse": 0.5,
                "normalized_permutation_entropy": 0.5,
            },
        }
        cross = {
            "thresholds": {
                "maximum_mean_difference_in_pooled_std": 0.75,
                "maximum_symmetric_std_ratio": 2.5,
                "maximum_quantile_difference_in_pooled_std": 1.0,
                "maximum_permutation_entropy_difference": 0.25,
            },
            "maximum_mean_difference_in_pooled_std": 0.25,
            "maximum_symmetric_std_ratio": 1.25,
            "maximum_quantile_difference_in_pooled_std": 0.5,
            "permutation_entropy_difference": 0.05,
        }
        margins = continuous_acceptance_margins(
            [
                {"resolution_divisor": 2, "diagnostics": diagnostics},
                {"resolution_divisor": 4, "diagnostics": diagnostics},
            ],
            cross,
        )
        self.assertEqual(
            margins["cross.permutation_entropy_difference"],
            5.0,
        )


if __name__ == "__main__":
    unittest.main()
