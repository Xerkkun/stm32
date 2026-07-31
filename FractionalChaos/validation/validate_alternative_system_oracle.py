#!/usr/bin/env python3
"""Validate ABM use with the frozen alternative-system candidate cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from abm_oracle import caputo_abm_full_memory
from alternative_systems import alternative_system_rhs
from validate_abm_oracle import (
    validate_constant,
    validate_manufactured_power,
)

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFESTS = HERE / "alternative_system_manifests_v1.json"
DEFAULT_GRID = HERE / "alternative_system_candidate_grid_v1.json"
DEFAULT_OUTPUT = (
    HERE / "results" / "alternative_system_oracle_validation_v1.json"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(HERE.parent).as_posix()
    except ValueError:
        return str(path.resolve())


def normalized_rmse(
    reference: np.ndarray,
    candidate: np.ndarray,
) -> list[float]:
    scale = np.maximum(np.ptp(reference, axis=0), 1.0e-12)
    rmse = np.sqrt(np.mean((candidate - reference) ** 2, axis=0))
    return [float(value) for value in rmse / scale]


def run_candidate_smoke(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    physical_horizon_s = 1.0
    cases: list[dict[str, Any]] = []

    for manifest in payload["manifests"]:
        trajectories: list[np.ndarray] = []
        error: str | None = None
        try:
            for divisor in (1, 2, 4):
                h = float(manifest["h"]) / divisor
                steps = int(round(physical_horizon_s / h))
                result = caputo_abm_full_memory(
                    alternative_system_rhs(
                        manifest["system"],
                        manifest["parameters"],
                    ),
                    manifest["initial_state"],
                    q=float(manifest["q"]),
                    h=h,
                    steps=steps,
                )
                trajectories.append(result.states)
        except (FloatingPointError, ValueError, OverflowError) as exc:
            error = f"{type(exc).__name__}: {exc}"

        row: dict[str, Any] = {
            "manifest_id": manifest["manifest_id"],
            "candidate_id": manifest["candidate_id"],
            "system": manifest["system"],
            "physical_horizon_s": physical_horizon_s,
            "completed_resolutions": len(trajectories),
            "error": error,
        }
        if len(trajectories) == 3:
            row.update(
                {
                    "all_finite": all(
                        bool(np.all(np.isfinite(trajectory)))
                        for trajectory in trajectories
                    ),
                    "steps": [
                        int(trajectory.shape[0] - 1)
                        for trajectory in trajectories
                    ],
                    "max_abs_state_h_over_4": float(
                        np.max(np.abs(trajectories[2]))
                    ),
                    "normalized_rmse_h_vs_h_over_2": normalized_rmse(
                        trajectories[1][::2],
                        trajectories[0],
                    ),
                    "normalized_rmse_h_over_2_vs_h_over_4": normalized_rmse(
                        trajectories[2][::2],
                        trajectories[1],
                    ),
                }
            )
        else:
            row["all_finite"] = False
        cases.append(row)

    return {
        "status": "short_horizon_smoke_only_not_manifest_qualification",
        "purpose": (
            "Exercise every frozen alternative vector field at h, h/2, and "
            "h/4 without claiming long-horizon boundedness or nonperiodicity."
        ),
        "cases": cases,
    }


def build_report(manifest_path: Path, grid_path: Path) -> dict[str, Any]:
    constant = validate_constant()
    manufactured = validate_manufactured_power()
    algorithm_passed = bool(
        constant["passed"] and manufactured["passed"]
    )
    return {
        "schema_version": 1,
        "status": (
            "passed_alternative_system_abm_validation"
            if algorithm_passed
            else "failed_alternative_system_abm_validation"
        ),
        "scope": (
            "Independent validation of the full-memory commensurate Caputo "
            "ABM implementation plus a non-qualifying smoke run of the "
            "frozen alternative vector fields."
        ),
        "provenance": {
            "abm_oracle_source": "validation/abm_oracle.py",
            "abm_oracle_source_sha256": sha256_file(HERE / "abm_oracle.py"),
            "alternative_rhs_source": "validation/alternative_systems.py",
            "alternative_rhs_source_sha256": sha256_file(
                HERE / "alternative_systems.py"
            ),
            "candidate_manifest_source": portable_path(manifest_path),
            "candidate_manifest_sha256": sha256_file(manifest_path),
            "candidate_grid_source": portable_path(grid_path),
            "candidate_grid_sha256": sha256_file(grid_path),
        },
        "algorithm_validation": {
            "passed": algorithm_passed,
            "constant": constant,
            "manufactured_power": manufactured,
        },
        "candidate_smoke": run_candidate_smoke(manifest_path),
        "does_not_establish": [
            "long-horizon qualification of any candidate",
            "a proof of chaos",
            "equivalence to an embedded finite-memory implementation",
            "STM32 timing, energy, or randomness",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    parser.add_argument("--candidate-grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest_path = args.manifests.resolve()
    grid_path = args.candidate_grid.resolve()
    output_path = args.output.resolve()
    report = build_report(manifest_path, grid_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{report['status']}: {output_path}")
    return 0 if report["algorithm_validation"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
