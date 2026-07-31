#!/usr/bin/env python3
"""Unit tests for the ABM long-horizon dynamic-qualification layer."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

import numpy as np

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

V2_MANIFEST_PATH = (
    VALIDATION_ROOT / "candidate_manifests_rossler_classic_v2.json"
)
V2_CRITERIA_PATH = (
    VALIDATION_ROOT / "long_horizon_criteria_rossler_classic_v2.json"
)
V2_ORACLE_REPORT_PATH = (
    VALIDATION_ROOT
    / "results"
    / "abm_oracle_validation_rossler_classic_v2.json"
)
V2_QUALIFICATION_PATH = (
    VALIDATION_ROOT
    / "results"
    / "abm_long_horizon_rossler_classic_v2"
    / "qualification.json"
)

HISTORICAL_PATHS_AND_SHA256 = {
    VALIDATION_ROOT / "candidate_manifests.json": (
        "2c1b2ea27dc796920f993b5e748afd24524549839fad9ca846704ffbeaaecf11"
    ),
    VALIDATION_ROOT / "long_horizon_criteria.json": (
        "a605769988aceb85368472b0ffb42b06c8503962d867a53ec73e4b4954012881"
    ),
    VALIDATION_ROOT / "results" / "abm_oracle_validation.json": (
        "52d596dd5b01e74b28407f9645fdf2be219fdef5199f8a57a79ab81f64f56f79"
    ),
    VALIDATION_ROOT
    / "results"
    / "abm_long_horizon"
    / "qualification.json": (
        "dcf8b38a3a736d0bb9497509b53ee1ff44a85faaba7167fac02102761774f9b9"
    ),
}

from long_horizon_qualification import (  # noqa: E402
    compare_resolutions,
    manifest_decision,
    normalized_permutation_entropy,
    validate_criteria,
)


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


class RosslerClassicV2FormalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_document = load_json(V2_MANIFEST_PATH)
        cls.criteria = load_json(V2_CRITERIA_PATH)
        cls.oracle_report = load_json(V2_ORACLE_REPORT_PATH)
        cls.qualification = load_json(V2_QUALIFICATION_PATH)

        cls.manifests = {
            item["manifest_id"]: item
            for item in cls.manifest_document["manifests"]
        }
        cls.qualified_manifests = {
            item["manifest_id"]: item
            for item in cls.qualification["manifests"]
        }

    def test_v2_hash_chain_and_manifest_ids_are_coherent(self) -> None:
        manifest_hash = sha256_file(V2_MANIFEST_PATH)
        criteria_hash = sha256_file(V2_CRITERIA_PATH)
        oracle_report_hash = sha256_file(V2_ORACLE_REPORT_PATH)
        oracle_source_hash = sha256_file(
            VALIDATION_ROOT / "abm_oracle.py"
        )
        qualification_source_hash = sha256_file(
            VALIDATION_ROOT / "long_horizon_qualification.py"
        )

        expected_ids = [
            item["manifest_id"]
            for item in self.manifest_document["manifests"]
        ]
        self.assertEqual(
            expected_ids,
            [
                "lorenz_caputo_v1",
                "rossler_classic_caputo_v2",
                "chen_caputo_v1",
            ],
        )
        self.assertEqual(
            self.criteria["inputs"]["manifest_ids"],
            expected_ids,
        )
        self.assertEqual(
            [
                case["manifest_id"]
                for case in self.oracle_report["candidate_smoke"]["cases"]
            ],
            expected_ids,
        )
        self.assertEqual(
            [
                item["manifest_id"]
                for item in self.qualification["manifests"]
            ],
            expected_ids,
        )

        rossler = self.manifests["rossler_classic_caputo_v2"]
        self.assertEqual(
            self.manifest_document["status"],
            "active_firmware_contract",
        )
        self.assertEqual(rossler["system"], "rossler")
        self.assertEqual(rossler["parameters"], [0.2, 0.2, 5.7])
        self.assertEqual(rossler["initial_state"], [1.0, 0.0, 0.0])
        self.assertEqual(rossler["q"], 0.9877)
        self.assertEqual(rossler["h"], 0.01)
        self.assertEqual(rossler["memory_seconds"], 10.0)
        self.assertEqual(rossler["memory_increments"], 1000)

        oracle_provenance = self.oracle_report["provenance"]
        qualification_provenance = self.qualification["provenance"]
        prerequisite = self.qualification[
            "implementation_validation_prerequisite"
        ]

        self.assertEqual(
            self.criteria["inputs"]["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            oracle_provenance["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            qualification_provenance["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            prerequisite["current_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            self.qualification["criteria"]["source_sha256"],
            criteria_hash,
        )
        self.assertEqual(
            prerequisite["source_sha256"],
            oracle_report_hash,
        )
        self.assertEqual(
            oracle_provenance["oracle_source_sha256"],
            oracle_source_hash,
        )
        self.assertEqual(
            qualification_provenance["oracle_source_sha256"],
            oracle_source_hash,
        )
        self.assertEqual(
            prerequisite["current_oracle_sha256"],
            oracle_source_hash,
        )
        self.assertEqual(
            qualification_provenance["qualification_source_sha256"],
            qualification_source_hash,
        )
        self.assertEqual(
            self.qualification["criteria"]["criteria_id"],
            self.criteria["criteria_id"],
        )
        self.assertEqual(
            self.qualification["criteria_snapshot"],
            self.criteria,
        )
        self.assertEqual(
            self.oracle_report["status"],
            "passed_abm_implementation_validation",
        )
        self.assertTrue(self.oracle_report["algorithm_validation"]["passed"])
        self.assertTrue(prerequisite["passed"], prerequisite)
        self.assertTrue(
            all(prerequisite["checks"].values()),
            prerequisite["checks"],
        )

        oracle_cases = {
            case["manifest_id"]: case
            for case in self.oracle_report["candidate_smoke"]["cases"]
        }
        for manifest_id, manifest in self.manifests.items():
            with self.subTest(manifest_id=manifest_id):
                qualified = self.qualified_manifests[manifest_id]
                self.assertEqual(qualified["manifest"], manifest)
                self.assertEqual(qualified["system"], manifest["system"])
                self.assertEqual(
                    oracle_cases[manifest_id]["system"],
                    manifest["system"],
                )

    def test_rossler_v2_fails_only_within_resolution_stability(
        self,
    ) -> None:
        rossler = self.qualified_manifests[
            "rossler_classic_caputo_v2"
        ]
        self.assertEqual(
            rossler["decision"],
            "not_qualified_dynamic_screen_failed",
        )
        self.assertEqual(
            rossler["failure_reasons"],
            [
                "h/2 failed within_resolution_stability",
                "h/4 failed within_resolution_stability",
            ],
        )
        self.assertTrue(
            rossler["cross_resolution_stability"]["passed"],
            rossler["cross_resolution_stability"],
        )
        self.assertTrue(
            all(
                rossler["cross_resolution_stability"]["checks"].values()
            )
        )

        required_screens = (
            "observed_boundedness",
            "activity",
            "within_resolution_stability",
            "nonperiodicity_screen",
        )
        self.assertEqual(
            [
                resolution["resolution_divisor"]
                for resolution in rossler["resolutions"]
            ],
            [2, 4],
        )
        for resolution in rossler["resolutions"]:
            divisor = resolution["resolution_divisor"]
            diagnostics = resolution["diagnostics"]
            failed = [
                name
                for name in required_screens
                if not diagnostics[name]["passed"]
            ]
            with self.subTest(resolution_divisor=divisor):
                self.assertEqual(
                    failed,
                    ["within_resolution_stability"],
                )
                self.assertTrue(
                    diagnostics["observed_boundedness"]["passed"]
                )
                self.assertTrue(diagnostics["activity"]["passed"])
                self.assertTrue(
                    diagnostics["nonperiodicity_screen"]["passed"]
                )
                self.assertEqual(
                    diagnostics["within_resolution_stability"]["checks"],
                    {
                        "maximum_mean_shift": True,
                        "maximum_std_ratio": True,
                        "minimum_std_ratio": False,
                    },
                )
                self.assertIn(
                    f"h/{divisor} failed within_resolution_stability",
                    rossler["failure_reasons"],
                )

    def test_chen_remains_qualified(self) -> None:
        chen = self.qualified_manifests["chen_caputo_v1"]
        self.assertEqual(
            chen["decision"],
            "qualified_observed_long_horizon_screen",
        )
        self.assertEqual(chen["failure_reasons"], [])
        self.assertTrue(chen["cross_resolution_stability"]["passed"])
        self.assertTrue(
            all(chen["cross_resolution_stability"]["checks"].values())
        )
        for resolution in chen["resolutions"]:
            diagnostics = resolution["diagnostics"]
            for screen in (
                "observed_boundedness",
                "activity",
                "within_resolution_stability",
                "nonperiodicity_screen",
            ):
                with self.subTest(
                    resolution_divisor=resolution["resolution_divisor"],
                    screen=screen,
                ):
                    self.assertTrue(diagnostics[screen]["passed"])

    def test_v1_historical_artifacts_remain_frozen(self) -> None:
        for path, expected_hash in HISTORICAL_PATHS_AND_SHA256.items():
            with self.subTest(path=path):
                self.assertEqual(sha256_file(path), expected_hash)

        historical_manifest_path = (
            VALIDATION_ROOT / "candidate_manifests.json"
        )
        historical_criteria_path = (
            VALIDATION_ROOT / "long_horizon_criteria.json"
        )
        historical_oracle_path = (
            VALIDATION_ROOT / "results" / "abm_oracle_validation.json"
        )
        historical_qualification_path = (
            VALIDATION_ROOT
            / "results"
            / "abm_long_horizon"
            / "qualification.json"
        )
        historical_manifests = load_json(historical_manifest_path)
        historical_criteria = load_json(historical_criteria_path)
        historical_oracle = load_json(historical_oracle_path)
        historical_qualification = load_json(
            historical_qualification_path
        )

        expected_ids = [
            "lorenz_caputo_v1",
            "rossler_caputo_v1",
            "chen_caputo_v1",
        ]
        self.assertEqual(
            [
                manifest["manifest_id"]
                for manifest in historical_manifests["manifests"]
            ],
            expected_ids,
        )
        self.assertEqual(
            historical_criteria["inputs"]["manifest_ids"],
            expected_ids,
        )
        self.assertEqual(
            [
                item["manifest_id"]
                for item in historical_qualification["manifests"]
            ],
            expected_ids,
        )

        historical_manifest_hash = HISTORICAL_PATHS_AND_SHA256[
            historical_manifest_path
        ]
        self.assertEqual(
            historical_criteria["inputs"]["candidate_manifest_sha256"],
            historical_manifest_hash,
        )
        self.assertEqual(
            historical_oracle["provenance"][
                "candidate_manifest_sha256"
            ],
            historical_manifest_hash,
        )
        self.assertEqual(
            historical_qualification["provenance"][
                "candidate_manifest_sha256"
            ],
            historical_manifest_hash,
        )
        self.assertEqual(
            historical_qualification["criteria"]["source_sha256"],
            HISTORICAL_PATHS_AND_SHA256[historical_criteria_path],
        )
        self.assertEqual(
            historical_qualification[
                "implementation_validation_prerequisite"
            ]["source_sha256"],
            HISTORICAL_PATHS_AND_SHA256[historical_oracle_path],
        )

        historical_by_id = {
            item["manifest_id"]: item
            for item in historical_manifests["manifests"]
        }
        historical_rossler = historical_by_id["rossler_caputo_v1"]
        self.assertEqual(historical_rossler["parameters"], [0.2, 0.2, 6.0])
        self.assertEqual(
            historical_rossler["initial_state"],
            [0.5, 1.5, 0.1],
        )
        self.assertEqual(historical_rossler["q"], 0.97)

        historical_results = {
            item["manifest_id"]: item
            for item in historical_qualification["manifests"]
        }
        self.assertEqual(
            historical_results["chen_caputo_v1"]["decision"],
            "qualified_observed_long_horizon_screen",
        )


if __name__ == "__main__":
    unittest.main()
