#!/usr/bin/env python3
"""Tests for the independently versioned alternative vector fields."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

VALIDATION_ROOT = Path(__file__).resolve().parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

from alternative_systems import alternative_system_rhs  # noqa: E402


class AlternativeSystemRightHandSideTests(unittest.TestCase):
    def test_lu_vector_field(self) -> None:
        rhs = alternative_system_rhs("lu", [36.0, 3.0, 20.0])
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [36.0, 37.0, -7.0],
        )

    def test_genesio_tesi_vector_field(self) -> None:
        rhs = alternative_system_rhs(
            "genesio_tesi_simplified", [1.05, 1.1, 0.45]
        )
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [2.0, 3.0, -3.6],
        )

    def test_shimizu_morioka_vector_field(self) -> None:
        rhs = alternative_system_rhs("shimizu_morioka", [0.45, 0.75])
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [2.0, -3.5, -0.35],
        )

    def test_liu_vector_field(self) -> None:
        rhs = alternative_system_rhs(
            "liu", [1.0, 2.5, 5.0, 1.0, 4.0, 4.0]
        )
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [-5.0, -7.0, -7.0],
        )

    def test_hammouch_mekkaoui_vector_field(self) -> None:
        rhs = alternative_system_rhs("hammouch_mekkaoui", [])
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [-6.0, -15.0, -7.0],
        )

    def test_munoz_pacheco_hidden_vector_field(self) -> None:
        rhs = alternative_system_rhs("munoz_pacheco_hidden", [0.35])
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [7.65, 0.0, -5.0],
        )

    def test_glucose_insulin_vector_field(self) -> None:
        rhs = alternative_system_rhs(
            "glucose_insulin", [1.3, 2.01, 0.22, 0.3]
        )
        np.testing.assert_allclose(
            rhs(0.0, np.asarray([1.0, 2.0, 3.0])),
            [48.25, -3.91, 1.36],
        )

    def test_invalid_parameter_arity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            alternative_system_rhs("shimizu_morioka", [0.45, 0.75, 1.0])
        with self.assertRaises(ValueError):
            alternative_system_rhs("glucose_insulin", [1.3, 2.01, 0.22])

    def test_unknown_system_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            alternative_system_rhs("unknown", [1.0])


if __name__ == "__main__":
    unittest.main()
