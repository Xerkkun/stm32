#!/usr/bin/env python3
"""Regression tests for the independent full-memory Caputo ABM oracle."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

from abm_oracle import caputo_abm_full_memory  # noqa: E402
from validate_abm_oracle import (  # noqa: E402
    validate_constant,
    validate_manufactured_power,
)


class ABMOracleTests(unittest.TestCase):
    def test_constant_solution_is_preserved(self) -> None:
        self.assertTrue(validate_constant()["passed"])

    def test_manufactured_power_converges(self) -> None:
        report = validate_manufactured_power()
        self.assertTrue(report["passed"], report)

    def test_vector_rhs_shape_is_checked(self) -> None:
        with self.assertRaises(ValueError):
            caputo_abm_full_memory(
                lambda _time, _state: np.asarray([0.0, 0.0]),
                [1.0],
                q=0.8,
                h=0.1,
                steps=1,
            )

    def test_invalid_contract_is_rejected(self) -> None:
        for q in (0.0, -0.1, 1.01, float("nan")):
            with self.subTest(q=q), self.assertRaises(ValueError):
                caputo_abm_full_memory(
                    lambda _time, state: np.zeros_like(state),
                    [1.0],
                    q=q,
                    h=0.1,
                    steps=1,
                )


if __name__ == "__main__":
    unittest.main()
