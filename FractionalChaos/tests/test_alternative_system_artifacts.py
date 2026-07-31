#!/usr/bin/env python3
"""Integrity tests for the frozen alternative-system qualification artifacts."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
MANIFEST_PATH = VALIDATION_ROOT / "alternative_system_manifests_v1.json"
GRID_PATH = VALIDATION_ROOT / "alternative_system_candidate_grid_v1.json"
CRITERIA_PATH = VALIDATION_ROOT / "alternative_system_criteria_v1.json"
HISTORICAL_CRITERIA_PATH = VALIDATION_ROOT / "long_horizon_criteria.json"
ORACLE_REPORT_PATH = (
    VALIDATION_ROOT
    / "results"
    / "alternative_system_oracle_validation_v1.json"
)
QUALIFICATION_PATH = (
    VALIDATION_ROOT
    / "results"
    / "alternative_system_qualification_v1"
    / "qualification.json"
)
CORRECTION_MANIFEST_PATH = (
    VALIDATION_ROOT / "lu_literature_correction_manifests_v2.json"
)
CORRECTION_GRID_PATH = (
    VALIDATION_ROOT / "lu_literature_correction_grid_v2.json"
)
CORRECTION_CRITERIA_PATH = (
    VALIDATION_ROOT / "lu_literature_correction_criteria_v2.json"
)
CORRECTION_ORACLE_REPORT_PATH = (
    VALIDATION_ROOT / "results" / "lu_literature_correction_oracle_v2.json"
)
CORRECTION_QUALIFICATION_PATH = (
    VALIDATION_ROOT
    / "results"
    / "lu_literature_correction_v2"
    / "qualification.json"
)
ROUND2_MANIFEST_PATH = (
    VALIDATION_ROOT / "alternative_system_round2_manifests_v1.json"
)
ROUND2_GRID_PATH = (
    VALIDATION_ROOT / "alternative_system_round2_grid_v1.json"
)
ROUND2_CRITERIA_PATH = (
    VALIDATION_ROOT / "alternative_system_round2_criteria_v1.json"
)
ROUND2_ORACLE_REPORT_PATH = (
    VALIDATION_ROOT / "results" / "alternative_system_round2_oracle_v1.json"
)
ROUND2_QUALIFICATION_PATH = (
    VALIDATION_ROOT
    / "results"
    / "alternative_system_round2_v1"
    / "qualification.json"
)

EXPECTED_IDS = [
    "lu_caputo_v1",
    "genesio_tesi_simplified_caputo_v1",
    "shimizu_morioka_caputo_v1",
    "liu_caputo_v1",
]
EXPECTED_INPUT_HASHES = {
    "manifest": (
        "26c0b74e20a1aed734110a1efe0e37f0bf0d8450bb899e7ead57bee270b822fc"
    ),
    "grid": (
        "ae8f455ad8d8b8ca0c5e8d38c0515198814f02d4f2c1a32fb79cfff0b0a4a415"
    ),
}
EXPECTED_TRAJECTORY_HASHES = {
    "lu_caputo_v1": [
        "56fbd7486eea52a45ea6a2a326d5e34a5e4f78de231992a8c509b7781942d688",
        "172cdd77f55d2c47372b6a69d35bc3d2af843628f1212d7de4179aab97dfb9e4",
    ],
    "genesio_tesi_simplified_caputo_v1": [
        "e449076b49d018b6f6d0bfdcec66a0af246e5a4202e270819b9bf8eae8f38a0d",
        "f53e07a21491039cfaa54feb26db538e40c683e3c72ad087d0a887e44d1cdbe1",
    ],
    "shimizu_morioka_caputo_v1": [
        "391573bca135bbb7a0b264d72596ab5b55acab125bae1c86d86f0898e30e5fe0",
        "73c5774ef5a2d7f2f68ecaa9b445f17c38f5544da6d98863c86605ff01910ac0",
    ],
    "liu_caputo_v1": [
        "05cc918f1ef18410a0cd8caf22b54eb904888cec19c180df52a3c7643f4bf4c9",
        "0c6218f6c20cc243e66163a27cbbad11feb901195cfa622477f3d8a3ea1531da",
    ],
}
EXPECTED_CORRECTION_INPUT_HASHES = {
    "manifest": (
        "b5d27062d8d8e790c962bcf27fe8cc7a86b26ad54ca467e8e849d32bd0146453"
    ),
    "grid": (
        "e432183a9e309b7a673bfd29f86549e00c1cffa2a1e3f6c56781a8b7317a9fe5"
    ),
}
EXPECTED_CORRECTION_TRAJECTORY_HASHES = [
    "6413a348cf5d447c08ba66027be81849f1a4ea1d1fc43986f0086b603116ea7d",
    "0be46094c49a9b15e41b880d52b6376a50252926b35f4a116317abc0e65829f6",
]
EXPECTED_ROUND2_INPUT_HASHES = {
    "manifest": (
        "ebfad80fbcdbb5a0438527deca98b0aed59979c30b26f15e69ce6a2ca4d18c39"
    ),
    "grid": (
        "513646675c44857837c34778ef8fc4ca2ba48a50f8cc70eedd58c55154e24951"
    ),
}
EXPECTED_ROUND2_TRAJECTORY_HASHES = {
    "hammouch_mekkaoui_caputo_v1": [
        "830e00d9f8a47a7e5343ca9465713948b71ce9f1fb4a5dd8ccd9be1303e15b01",
        "f65185ebe3aaaa9b4a9a5e5bcd22a7f9ef290f47675898dfbc7489e128c55bd4",
    ],
    "munoz_pacheco_hidden_caputo_v1": [
        "fe2951a8262e0e9826ae56bdd7177444d559ff65566c4ca082cfab7c13b8d25a",
        "f50a9be284f56c2addedd7dff479b658b7b9302be9007166960ec401ff3571dd",
    ],
    "glucose_insulin_caputo_v1": [
        "d8d8f5cf7bb20f010649bdfd1864d4e9760d2d098633a96e900d8086b3fbe1dc",
        "19a0645a2fffee2206634f90291447e415f2d4c3c1c1033aa49a8cbd69db8f9f",
    ],
}


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AlternativeSystemArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_document = load_json(MANIFEST_PATH)
        cls.grid = load_json(GRID_PATH)
        cls.criteria = load_json(CRITERIA_PATH)
        cls.historical_criteria = load_json(HISTORICAL_CRITERIA_PATH)
        cls.oracle_report = load_json(ORACLE_REPORT_PATH)
        cls.qualification = load_json(QUALIFICATION_PATH)
        cls.results_by_id = {
            item["manifest_id"]: item
            for item in cls.qualification["manifests"]
        }

    def test_frozen_inputs_and_provenance_chain_are_current(self) -> None:
        manifest_hash = sha256_file(MANIFEST_PATH)
        grid_hash = sha256_file(GRID_PATH)
        oracle_source_hash = sha256_file(VALIDATION_ROOT / "abm_oracle.py")
        rhs_source_hash = sha256_file(
            VALIDATION_ROOT / "alternative_systems.py"
        )
        qualification_source_hash = sha256_file(
            VALIDATION_ROOT / "qualify_alternative_systems.py"
        )

        self.assertEqual(manifest_hash, EXPECTED_INPUT_HASHES["manifest"])
        self.assertEqual(grid_hash, EXPECTED_INPUT_HASHES["grid"])
        self.assertEqual(
            self.criteria["inputs"]["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            self.criteria["inputs"]["candidate_grid_sha256"],
            grid_hash,
        )

        expected_provenance = {
            "abm_oracle_source_sha256": oracle_source_hash,
            "alternative_rhs_source_sha256": rhs_source_hash,
            "candidate_manifest_sha256": manifest_hash,
            "candidate_grid_sha256": grid_hash,
        }
        for name, expected in expected_provenance.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.oracle_report["provenance"][name],
                    expected,
                )
                self.assertEqual(
                    self.qualification["provenance"][name],
                    expected,
                )

        self.assertEqual(
            self.qualification["provenance"][
                "qualification_source_sha256"
            ],
            qualification_source_hash,
        )
        self.assertEqual(
            self.qualification["criteria"]["source_sha256"],
            sha256_file(CRITERIA_PATH),
        )
        self.assertEqual(
            self.qualification["criteria_snapshot"],
            self.criteria,
        )
        prerequisite = self.qualification[
            "implementation_validation_prerequisite"
        ]
        self.assertTrue(prerequisite["passed"], prerequisite)
        self.assertTrue(
            all(prerequisite["checks"].values()),
            prerequisite["checks"],
        )
        self.assertEqual(
            self.oracle_report["status"],
            "passed_alternative_system_abm_validation",
        )
        self.assertTrue(self.oracle_report["algorithm_validation"]["passed"])

    def test_candidate_order_and_unchanged_thresholds_are_explicit(
        self,
    ) -> None:
        manifest_ids = [
            item["manifest_id"]
            for item in self.manifest_document["manifests"]
        ]
        grid_ids = [
            item["manifest"]["manifest_id"]
            for item in self.grid["candidates"]
        ]
        oracle_ids = [
            item["manifest_id"]
            for item in self.oracle_report["candidate_smoke"]["cases"]
        ]
        qualification_ids = [
            item["manifest_id"]
            for item in self.qualification["manifests"]
        ]
        self.assertEqual(manifest_ids, EXPECTED_IDS)
        self.assertEqual(grid_ids, EXPECTED_IDS)
        self.assertEqual(
            self.criteria["inputs"]["manifest_ids"],
            EXPECTED_IDS,
        )
        self.assertEqual(oracle_ids, EXPECTED_IDS)
        self.assertEqual(qualification_ids, EXPECTED_IDS)

        for section in (
            "integration",
            "observed_boundedness",
            "nonperiodicity_screen",
            "within_resolution_stability",
            "cross_resolution_stability",
        ):
            with self.subTest(section=section):
                self.assertEqual(
                    self.criteria[section],
                    self.historical_criteria[section],
                )
        self.assertTrue(self.qualification["selection"]["thresholds_unchanged"])

    def test_decisions_selection_and_trajectory_hashes_are_frozen(
        self,
    ) -> None:
        decisions = {
            manifest_id: result["decision"]
            for manifest_id, result in self.results_by_id.items()
        }
        self.assertEqual(
            decisions,
            {
                "lu_caputo_v1": "qualified_observed_long_horizon_screen",
                "genesio_tesi_simplified_caputo_v1": (
                    "qualified_observed_long_horizon_screen"
                ),
                "shimizu_morioka_caputo_v1": (
                    "not_qualified_dynamic_screen_failed"
                ),
                "liu_caputo_v1": "qualified_observed_long_horizon_screen",
            },
        )
        self.assertEqual(
            self.qualification["qualified_manifest_ids"],
            [
                "lu_caputo_v1",
                "genesio_tesi_simplified_caputo_v1",
                "liu_caputo_v1",
            ],
        )
        self.assertEqual(
            self.qualification["selection"]["selected_manifest_ids"],
            ["lu_caputo_v1", "liu_caputo_v1"],
        )
        self.assertEqual(
            self.qualification["selection"]["actual_selection_count"],
            2,
        )

        ranked = self.qualification["selection"]["eligible_ranked"]
        self.assertEqual(
            [item["manifest_id"] for item in ranked],
            [
                "lu_caputo_v1",
                "liu_caputo_v1",
                "genesio_tesi_simplified_caputo_v1",
            ],
        )
        self.assertTrue(
            all(
                item["minimum_normalized_continuous_margin"] > 1.0
                for item in ranked
            )
        )

        for manifest_id, expected_hashes in (
            EXPECTED_TRAJECTORY_HASHES.items()
        ):
            result = self.results_by_id[manifest_id]
            observed_hashes = [
                item["trajectory_float64_le_sha256"]
                for item in result["resolutions"]
            ]
            with self.subTest(manifest_id=manifest_id):
                self.assertEqual(observed_hashes, expected_hashes)

    def test_shimizu_morioka_failure_is_not_overstated(self) -> None:
        result = self.results_by_id["shimizu_morioka_caputo_v1"]
        self.assertEqual(
            result["failure_reasons"],
            [
                "h/2 failed within_resolution_stability",
                "h/2 failed nonperiodicity_screen",
                "h/4 failed within_resolution_stability",
                "h/4 failed nonperiodicity_screen",
            ],
        )
        self.assertTrue(result["cross_resolution_stability"]["passed"])
        for resolution in result["resolutions"]:
            diagnostics = resolution["diagnostics"]
            self.assertTrue(diagnostics["observed_boundedness"]["passed"])
            self.assertTrue(diagnostics["activity"]["passed"])
            self.assertFalse(
                diagnostics["within_resolution_stability"]["passed"]
            )
            self.assertFalse(
                diagnostics["nonperiodicity_screen"]["passed"]
            )


class LuLiteratureCorrectionArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_document = load_json(CORRECTION_MANIFEST_PATH)
        cls.grid = load_json(CORRECTION_GRID_PATH)
        cls.criteria = load_json(CORRECTION_CRITERIA_PATH)
        cls.historical_criteria = load_json(HISTORICAL_CRITERIA_PATH)
        cls.oracle_report = load_json(CORRECTION_ORACLE_REPORT_PATH)
        cls.qualification = load_json(CORRECTION_QUALIFICATION_PATH)
        cls.result = cls.qualification["manifests"][0]

    def test_correction_reason_and_hash_chain_are_frozen(self) -> None:
        manifest_hash = sha256_file(CORRECTION_MANIFEST_PATH)
        grid_hash = sha256_file(CORRECTION_GRID_PATH)
        self.assertEqual(
            manifest_hash,
            EXPECTED_CORRECTION_INPUT_HASHES["manifest"],
        )
        self.assertEqual(grid_hash, EXPECTED_CORRECTION_INPUT_HASHES["grid"])
        self.assertEqual(
            self.criteria["inputs"]["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            self.criteria["inputs"]["candidate_grid_sha256"],
            grid_hash,
        )
        self.assertEqual(
            self.grid["evidence_layer"],
            "literature_contract_correction",
        )
        self.assertEqual(
            self.grid["supersedes_for_formal_followup"]["manifest_id"],
            "lu_caputo_v1",
        )

        expected_provenance = {
            "abm_oracle_source_sha256": sha256_file(
                VALIDATION_ROOT / "abm_oracle.py"
            ),
            "alternative_rhs_source_sha256": sha256_file(
                VALIDATION_ROOT / "alternative_systems.py"
            ),
            "candidate_manifest_sha256": manifest_hash,
            "candidate_grid_sha256": grid_hash,
        }
        for name, expected in expected_provenance.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.oracle_report["provenance"][name],
                    expected,
                )
                self.assertEqual(
                    self.qualification["provenance"][name],
                    expected,
                )
        self.assertEqual(
            self.qualification["provenance"][
                "qualification_source_sha256"
            ],
            sha256_file(VALIDATION_ROOT / "qualify_alternative_systems.py"),
        )
        self.assertEqual(
            self.qualification["criteria"]["source_sha256"],
            sha256_file(CORRECTION_CRITERIA_PATH),
        )
        self.assertEqual(
            self.qualification["criteria_snapshot"],
            self.criteria,
        )
        prerequisite = self.qualification[
            "implementation_validation_prerequisite"
        ]
        self.assertTrue(prerequisite["passed"], prerequisite)
        self.assertTrue(
            all(prerequisite["checks"].values()),
            prerequisite["checks"],
        )

    def test_correction_reuses_thresholds_without_relaxation(self) -> None:
        for section in (
            "integration",
            "observed_boundedness",
            "nonperiodicity_screen",
            "within_resolution_stability",
            "cross_resolution_stability",
        ):
            with self.subTest(section=section):
                self.assertEqual(
                    self.criteria[section],
                    self.historical_criteria[section],
                )
        self.assertTrue(self.qualification["selection"]["thresholds_unchanged"])
        self.assertEqual(
            self.qualification["selection"]["requested_promotion_count"],
            1,
        )

    def test_corrected_lu_is_preserved_as_a_negative_result(self) -> None:
        self.assertEqual(self.result["manifest_id"], "lu_caputo_v2")
        self.assertEqual(
            self.result["decision"],
            "not_qualified_dynamic_screen_failed",
        )
        self.assertEqual(
            self.result["failure_reasons"],
            ["h/4 failed nonperiodicity_screen"],
        )
        self.assertEqual(
            self.qualification["qualified_manifest_ids"],
            [],
        )
        self.assertEqual(
            self.qualification["selection"]["selected_manifest_ids"],
            [],
        )
        self.assertEqual(
            [
                item["trajectory_float64_le_sha256"]
                for item in self.result["resolutions"]
            ],
            EXPECTED_CORRECTION_TRAJECTORY_HASHES,
        )
        h_over_2, h_over_4 = self.result["resolutions"]
        self.assertTrue(
            h_over_2["diagnostics"]["nonperiodicity_screen"]["passed"]
        )
        self.assertFalse(
            h_over_4["diagnostics"]["nonperiodicity_screen"]["passed"]
        )
        self.assertLess(
            h_over_4["diagnostics"]["nonperiodicity_screen"][
                "minimum_normalized_lag_rmse"
            ],
            self.criteria["nonperiodicity_screen"][
                "minimum_normalized_lag_rmse"
            ],
        )
        self.assertTrue(self.result["cross_resolution_stability"]["passed"])
        for resolution in self.result["resolutions"]:
            diagnostics = resolution["diagnostics"]
            self.assertTrue(diagnostics["observed_boundedness"]["passed"])
            self.assertTrue(diagnostics["activity"]["passed"])
            self.assertTrue(
                diagnostics["within_resolution_stability"]["passed"]
            )


class AlternativeSystemRound2ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_document = load_json(ROUND2_MANIFEST_PATH)
        cls.grid = load_json(ROUND2_GRID_PATH)
        cls.criteria = load_json(ROUND2_CRITERIA_PATH)
        cls.historical_criteria = load_json(HISTORICAL_CRITERIA_PATH)
        cls.oracle_report = load_json(ROUND2_ORACLE_REPORT_PATH)
        cls.qualification = load_json(ROUND2_QUALIFICATION_PATH)
        cls.results_by_id = {
            item["manifest_id"]: item
            for item in cls.qualification["manifests"]
        }

    def test_round2_inputs_and_hash_chain_are_frozen(self) -> None:
        manifest_hash = sha256_file(ROUND2_MANIFEST_PATH)
        grid_hash = sha256_file(ROUND2_GRID_PATH)
        self.assertEqual(manifest_hash, EXPECTED_ROUND2_INPUT_HASHES["manifest"])
        self.assertEqual(grid_hash, EXPECTED_ROUND2_INPUT_HASHES["grid"])
        self.assertEqual(
            self.criteria["inputs"]["candidate_manifest_sha256"],
            manifest_hash,
        )
        self.assertEqual(
            self.criteria["inputs"]["candidate_grid_sha256"],
            grid_hash,
        )

        expected_provenance = {
            "abm_oracle_source_sha256": sha256_file(
                VALIDATION_ROOT / "abm_oracle.py"
            ),
            "alternative_rhs_source_sha256": sha256_file(
                VALIDATION_ROOT / "alternative_systems.py"
            ),
            "candidate_manifest_sha256": manifest_hash,
            "candidate_grid_sha256": grid_hash,
        }
        for name, expected in expected_provenance.items():
            with self.subTest(name=name):
                self.assertEqual(
                    self.oracle_report["provenance"][name],
                    expected,
                )
                self.assertEqual(
                    self.qualification["provenance"][name],
                    expected,
                )
        self.assertEqual(
            self.qualification["provenance"][
                "qualification_source_sha256"
            ],
            sha256_file(VALIDATION_ROOT / "qualify_alternative_systems.py"),
        )
        self.assertEqual(
            self.qualification["criteria"]["source_sha256"],
            sha256_file(ROUND2_CRITERIA_PATH),
        )
        self.assertEqual(
            self.qualification["criteria_snapshot"],
            self.criteria,
        )
        prerequisite = self.qualification[
            "implementation_validation_prerequisite"
        ]
        self.assertTrue(prerequisite["passed"], prerequisite)
        self.assertTrue(
            all(prerequisite["checks"].values()),
            prerequisite["checks"],
        )

    def test_round2_reuses_thresholds_and_candidate_order(self) -> None:
        expected_ids = [
            "hammouch_mekkaoui_caputo_v1",
            "munoz_pacheco_hidden_caputo_v1",
            "glucose_insulin_caputo_v1",
        ]
        self.assertEqual(
            [
                item["manifest_id"]
                for item in self.manifest_document["manifests"]
            ],
            expected_ids,
        )
        self.assertEqual(
            [
                item["manifest"]["manifest_id"]
                for item in self.grid["candidates"]
            ],
            expected_ids,
        )
        self.assertEqual(
            self.criteria["inputs"]["manifest_ids"],
            expected_ids,
        )
        self.assertEqual(
            [
                item["manifest_id"]
                for item in self.oracle_report["candidate_smoke"]["cases"]
            ],
            expected_ids,
        )
        self.assertEqual(
            list(self.results_by_id),
            expected_ids,
        )
        for section in (
            "integration",
            "observed_boundedness",
            "nonperiodicity_screen",
            "within_resolution_stability",
            "cross_resolution_stability",
        ):
            with self.subTest(section=section):
                self.assertEqual(
                    self.criteria[section],
                    self.historical_criteria[section],
                )
        self.assertTrue(self.qualification["selection"]["thresholds_unchanged"])

    def test_round2_qualification_and_one_slot_selection_are_frozen(
        self,
    ) -> None:
        self.assertEqual(
            self.qualification["qualified_manifest_ids"],
            [
                "hammouch_mekkaoui_caputo_v1",
                "munoz_pacheco_hidden_caputo_v1",
                "glucose_insulin_caputo_v1",
            ],
        )
        for manifest_id, result in self.results_by_id.items():
            with self.subTest(manifest_id=manifest_id):
                self.assertEqual(
                    result["decision"],
                    "qualified_observed_long_horizon_screen",
                )
                self.assertEqual(result["failure_reasons"], [])
                self.assertTrue(result["cross_resolution_stability"]["passed"])
                for resolution in result["resolutions"]:
                    diagnostics = resolution["diagnostics"]
                    for screen in (
                        "observed_boundedness",
                        "activity",
                        "within_resolution_stability",
                        "nonperiodicity_screen",
                    ):
                        self.assertTrue(diagnostics[screen]["passed"])
                self.assertEqual(
                    [
                        resolution["trajectory_float64_le_sha256"]
                        for resolution in result["resolutions"]
                    ],
                    EXPECTED_ROUND2_TRAJECTORY_HASHES[manifest_id],
                )

        selection = self.qualification["selection"]
        self.assertEqual(selection["requested_promotion_count"], 1)
        self.assertEqual(selection["actual_selection_count"], 1)
        self.assertEqual(
            selection["selected_manifest_ids"],
            ["hammouch_mekkaoui_caputo_v1"],
        )
        ranked = selection["eligible_ranked"]
        self.assertEqual(
            [item["manifest_id"] for item in ranked],
            [
                "hammouch_mekkaoui_caputo_v1",
                "glucose_insulin_caputo_v1",
                "munoz_pacheco_hidden_caputo_v1",
            ],
        )
        self.assertAlmostEqual(
            ranked[0]["minimum_normalized_continuous_margin"],
            1.5087763574251667,
        )
        self.assertAlmostEqual(
            ranked[1]["minimum_normalized_continuous_margin"],
            1.321576237912829,
        )
        self.assertAlmostEqual(
            ranked[2]["minimum_normalized_continuous_margin"],
            1.0389452980140916,
        )


if __name__ == "__main__":
    unittest.main()
