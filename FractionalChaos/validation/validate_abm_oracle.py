#!/usr/bin/env python3
"""Validate the independent full-memory Caputo ABM oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from abm_oracle import caputo_abm_full_memory, fractional_system_rhs

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFESTS = HERE / "candidate_manifests.json"
DEFAULT_OUTPUT = HERE / "results" / "abm_oracle_validation.json"


def _power_derivative(q: float, exponent: float, time: float) -> float:
    if time == 0.0:
        return 0.0
    coefficient = math.gamma(exponent + 1.0) / math.gamma(
        exponent + 1.0 - q
    )
    return coefficient * time ** (exponent - q)


def validate_constant() -> dict[str, Any]:
    initial = np.asarray([1.25, -2.0, 0.125], dtype=np.float64)

    def zero_rhs(_time: float, state: np.ndarray) -> np.ndarray:
        return np.zeros_like(state)

    result = caputo_abm_full_memory(
        zero_rhs, initial, q=0.73, h=1.0 / 64.0, steps=64
    )
    max_error = float(np.max(np.abs(result.states - initial)))
    return {
        "case": "constant",
        "q": result.q,
        "h": result.h,
        "steps": 64,
        "max_abs_error": max_error,
        "threshold": 1.0e-14,
        "passed": max_error <= 1.0e-14,
    }


def validate_manufactured_power() -> dict[str, Any]:
    q_values = (0.5, 0.8, 0.995)
    exponent = 4.0
    step_counts = (40, 80, 160, 320)
    cases: list[dict[str, Any]] = []
    passed = True

    for q in q_values:
        rows: list[dict[str, float | int]] = []
        previous_error: float | None = None
        for steps in step_counts:
            h = 1.0 / steps

            def rhs(time: float, state: np.ndarray) -> np.ndarray:
                exact = time**exponent
                forcing = _power_derivative(q, exponent, time) + exact
                return np.asarray([-state[0] + forcing], dtype=np.float64)

            result = caputo_abm_full_memory(
                rhs, [0.0], q=q, h=h, steps=steps
            )
            exact = result.times**exponent
            max_error = float(
                np.max(np.abs(result.states[:, 0] - exact))
            )
            observed_order = None
            if previous_error is not None:
                observed_order = math.log(previous_error / max_error, 2.0)
            rows.append(
                {
                    "steps": steps,
                    "h": h,
                    "max_abs_error": max_error,
                    "observed_order": observed_order,
                }
            )
            previous_error = max_error

        errors = [float(row["max_abs_error"]) for row in rows]
        monotone = all(
            errors[index + 1] < errors[index]
            for index in range(len(errors) - 1)
        )
        final_order = float(rows[-1]["observed_order"])
        expected_order = min(2.0, 1.0 + q)
        order_floor = expected_order - 0.35
        case_passed = monotone and final_order >= order_floor
        passed = passed and case_passed
        cases.append(
            {
                "q": q,
                "power": exponent,
                "expected_asymptotic_order": expected_order,
                "accepted_order_floor": order_floor,
                "monotone_error_reduction": monotone,
                "passed": case_passed,
                "meshes": rows,
            }
        )

    return {
        "case": "manufactured_power_state_dependent",
        "exact_solution": "x(t)=t^4",
        "equation": "CaputoD(q)x=-x+CaputoD(q)t^4+t^4",
        "passed": passed,
        "cases": cases,
    }


def _normalized_rmse(reference: np.ndarray, candidate: np.ndarray) -> list[float]:
    scale = np.maximum(np.ptp(reference, axis=0), 1.0e-12)
    rmse = np.sqrt(np.mean((candidate - reference) ** 2, axis=0))
    return [float(value) for value in rmse / scale]


def run_candidate_smoke(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    physical_horizon = 1.0

    for manifest in payload["manifests"]:
        trajectories = []
        for divisor in (1, 2, 4):
            h = float(manifest["h"]) / divisor
            steps = int(round(physical_horizon / h))
            result = caputo_abm_full_memory(
                fractional_system_rhs(
                    manifest["system"], manifest["parameters"]
                ),
                manifest["initial_state"],
                q=float(manifest["q"]),
                h=h,
                steps=steps,
            )
            trajectories.append(result.states)

        medium_on_fine = trajectories[1]
        fine = trajectories[2][::2]
        coarse_on_medium = trajectories[0]
        medium = trajectories[1][::2]
        cases.append(
            {
                "manifest_id": manifest["manifest_id"],
                "system": manifest["system"],
                "physical_horizon_s": physical_horizon,
                "steps": [
                    int(trajectory.shape[0] - 1)
                    for trajectory in trajectories
                ],
                "all_finite": all(
                    bool(np.all(np.isfinite(trajectory)))
                    for trajectory in trajectories
                ),
                "max_abs_state_h_over_4": float(
                    np.max(np.abs(trajectories[2]))
                ),
                "normalized_rmse_h_vs_h_over_2": _normalized_rmse(
                    medium, coarse_on_medium
                ),
                "normalized_rmse_h_over_2_vs_h_over_4": _normalized_rmse(
                    fine, medium_on_fine
                ),
            }
        )

    return {
        "status": "short_horizon_smoke_only_not_manifest_qualification",
        "purpose": (
            "Exercise all three candidate vector fields and h, h/2, h/4 "
            "without claiming long-horizon boundedness or nonperiodicity."
        ),
        "cases": cases,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return path.relative_to(HERE.parent).as_posix()
    except ValueError:
        return str(path)


def build_report(manifest_path: Path) -> dict[str, Any]:
    constant = validate_constant()
    manufactured = validate_manufactured_power()
    passed = bool(constant["passed"] and manufactured["passed"])
    return {
        "schema_version": 1,
        "status": (
            "passed_abm_implementation_validation"
            if passed
            else "failed_abm_implementation_validation"
        ),
        "scope": (
            "Independent host validation of the full-memory commensurate "
            "Caputo ABM PECE implementation."
        ),
        "does_not_establish": [
            "chaos",
            "hidden attractors",
            "long-horizon manifest qualification",
            "STM32 timing, energy, or randomness",
        ],
        "arithmetic": {
            "state_dtype": "IEEE-754 float64",
            "embedded_comparison_dtype": "IEEE-754 float32",
            "claim": "higher precision than the embedded primary arithmetic",
        },
        "method": {
            "name": "Diethelm Adams-Bashforth-Moulton PECE",
            "operator": "Caputo",
            "memory_policy": "full_history",
            "corrector_evaluations": 1,
        },
        "provenance": {
            "oracle_source": "validation/abm_oracle.py",
            "oracle_source_sha256": _sha256(HERE / "abm_oracle.py"),
            "candidate_manifest_source": _portable_path(manifest_path),
            "candidate_manifest_sha256": _sha256(manifest_path),
        },
        "algorithm_validation": {
            "passed": passed,
            "constant": constant,
            "manufactured_power": manufactured,
        },
        "candidate_smoke": run_candidate_smoke(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifests", type=Path, default=DEFAULT_MANIFESTS
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest_path = args.manifests.resolve()
    output_path = args.output.resolve()
    report = build_report(manifest_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{report['status']}: {output_path}")
    return 0 if report["algorithm_validation"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
