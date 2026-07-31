#!/usr/bin/env python3
"""Regression tests for the predeclared ABM replacement exploration."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = PROJECT_ROOT / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

from explore_manifest_replacements import validate_grid  # noqa: E402


class ABMManifestReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grid_path = (
            VALIDATION_ROOT / "replacement_candidate_grid_v1.json"
        )
        cls.criteria_path = VALIDATION_ROOT / "long_horizon_criteria.json"
        cls.manifest_path = VALIDATION_ROOT / "candidate_manifests.json"
        cls.baseline_path = (
            VALIDATION_ROOT
            / "results"
            / "abm_long_horizon"
            / "qualification.json"
        )
        cls.result_path = (
            VALIDATION_ROOT
            / "results"
            / "abm_replacement_exploration_v1"
            / "exploration.json"
        )
        cls.grid = json.loads(cls.grid_path.read_text(encoding="utf-8"))
        cls.criteria = json.loads(
            cls.criteria_path.read_text(encoding="utf-8")
        )
        cls.result = json.loads(cls.result_path.read_text(encoding="utf-8"))

    def test_grid_is_bound_to_unchanged_baseline_evidence(self) -> None:
        criteria, binding = validate_grid(
            self.grid,
            grid_path=self.grid_path,
            criteria_path=self.criteria_path,
            baseline_manifest_path=self.manifest_path,
            baseline_qualification_path=self.baseline_path,
        )
        self.assertEqual(criteria, self.criteria)
        self.assertTrue(binding["passed"])

    def test_stale_binding_is_rejected(self) -> None:
        stale = copy.deepcopy(self.grid)
        stale["frozen_inputs"]["criteria_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_grid(
                stale,
                grid_path=self.grid_path,
                criteria_path=self.criteria_path,
                baseline_manifest_path=self.manifest_path,
                baseline_qualification_path=self.baseline_path,
            )

    def test_exploration_did_not_promote_failed_candidates(self) -> None:
        result = self.result
        self.assertEqual(
            result["evidence_layer"],
            "exploratory_candidate_search",
        )
        self.assertFalse(result["formal_qualification"])
        self.assertFalse(
            result["all_failed_systems_have_eligible_replacements"]
        )
        self.assertEqual(
            result["status"],
            "one_or_more_failed_systems_lack_eligible_replacement",
        )
        self.assertEqual(
            result["predeclared_selection"],
            {"lorenz": None, "rossler": None},
        )
        self.assertEqual(result["criteria_snapshot"], self.criteria)
        for candidate in result["candidates"]:
            self.assertFalse(
                candidate["replacement_eligible_for_v2_freeze"]
            )
            self.assertEqual(
                candidate["exploratory_dynamic_decision"],
                "not_qualified_dynamic_screen_failed",
            )

    def test_recorded_failures_match_frozen_thresholds(self) -> None:
        candidates = {
            item["manifest"]["system"]: item
            for item in self.result["candidates"]
        }
        minimum_ratio = self.criteria["within_resolution_stability"][
            "minimum_block_to_global_std_ratio"
        ]
        maximum_quantile = self.criteria["cross_resolution_stability"][
            "maximum_quantile_difference_in_pooled_std"
        ]

        lorenz = candidates["lorenz"]
        lorenz_by_divisor = {
            item["resolution_divisor"]: item
            for item in lorenz["resolutions"]
        }
        self.assertLess(
            lorenz_by_divisor[2]["diagnostics"][
                "within_resolution_stability"
            ]["minimum_block_to_global_std_ratio"],
            minimum_ratio,
        )
        self.assertTrue(
            lorenz_by_divisor[4]["diagnostics"][
                "within_resolution_stability"
            ]["passed"]
        )
        self.assertGreater(
            lorenz["cross_resolution_stability"][
                "maximum_quantile_difference_in_pooled_std"
            ],
            maximum_quantile,
        )

        rossler = candidates["rossler"]
        for resolution in rossler["resolutions"]:
            self.assertLess(
                resolution["diagnostics"][
                    "within_resolution_stability"
                ]["minimum_block_to_global_std_ratio"],
                minimum_ratio,
            )
        self.assertTrue(rossler["cross_resolution_stability"]["passed"])


if __name__ == "__main__":
    unittest.main()
