#!/usr/bin/env python3
"""Unit checks for the deterministic Q1.14.14 host oracle."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "validation"
    / "fixed_point_comparison.py"
)
SPEC = importlib.util.spec_from_file_location("fixed_point_comparison", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_rounding_and_saturation() -> None:
    arithmetic = MODULE.Arithmetic()
    assert arithmetic.encode(0.5 / MODULE.SCALE) == 1
    assert arithmetic.encode(-0.5 / MODULE.SCALE) == -1
    assert arithmetic.mul(MODULE.SCALE // 2, MODULE.SCALE // 2) == MODULE.SCALE // 4
    half = arithmetic.encode_coefficient(0.5)
    assert arithmetic.mul_coefficient(half, MODULE.SCALE // 2) == MODULE.SCALE // 4
    assert arithmetic.add(MODULE.RAW_MAX, 1) == MODULE.RAW_MAX
    assert arithmetic.saturations == 1


def test_m2_q1_is_midpoint_in_q14() -> None:
    manifest = MODULE.Manifest(
        name="linear",
        q=1.0,
        h=0.125,
        memory=8,
        parameters=(0.0, 0.0, 0.0),
        initial=(0.25, -0.5, 0.75),
    )
    c2, c4 = MODULE.coefficient_set(manifest, "m2sfrk")
    assert c2 == 0.125
    assert c4 == 0.0625


def test_all_candidate_cells_remain_unsaturated_short_horizon() -> None:
    manifests = MODULE.load_manifests(
        MODULE_PATH.with_name("candidate_manifests.json")
    )
    system_ids = {"lorenz": 0, "rossler": 1, "chen": 2}
    for manifest in manifests:
        system = system_ids[manifest.name]
        for method in ("efork3", "gl", "m2sfrk"):
            states, arithmetic = MODULE.simulate_fixed(
                system, manifest, method, 32
            )
            assert len(states) == 33
            assert arithmetic.saturations == 0
            assert arithmetic.coefficient_saturations == 0
            assert arithmetic.nonzero_coefficients_rounded_to_zero == 0


def test_full_memory_coefficients_survive_q30_quantization() -> None:
    manifests = MODULE.load_manifests(
        MODULE_PATH.with_name("candidate_manifests.json")
    )
    for manifest in manifests:
        arithmetic = MODULE.Arithmetic()
        for method in ("efork3", "gl"):
            coefficients = MODULE.coefficient_set(manifest, method)
            source_weights = coefficients[3] if method == "efork3" else (coefficients[1],)
            for lane in source_weights:
                encoded = [
                    arithmetic.encode_coefficient(value)
                    for value in lane
                    if value != 0.0
                ]
                assert all(value != 0 for value in encoded)
    assert arithmetic.nonzero_coefficients_rounded_to_zero == 0
