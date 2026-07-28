#!/usr/bin/env python3
"""Screen predeclared literature replacements without qualifying manifests.

This exploratory layer deliberately reuses the frozen long-horizon diagnostics
while writing to a separate result tree.  Passing this screen only makes a
candidate eligible to be frozen in a new manifest; it is not formal
qualification evidence.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from abm_oracle import caputo_abm_full_memory, fractional_system_rhs
from long_horizon_qualification import (
    compare_resolutions,
    diagnose_resolution,
    load_json_object,
    manifest_decision,
    portable_path,
    resolution_passed,
    sha256_file,
    sha256_states,
    validate_criteria,
    write_csv,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
DEFAULT_GRID = HERE / "replacement_candidate_grid_v1.json"
DEFAULT_CRITERIA = HERE / "long_horizon_criteria.json"
DEFAULT_BASELINE_MANIFESTS = HERE / "candidate_manifests.json"
DEFAULT_BASELINE_QUALIFICATION = (
    HERE / "results" / "abm_long_horizon" / "qualification.json"
)
DEFAULT_OUTPUT_DIR = HERE / "results" / "abm_replacement_exploration_v1"


def validate_grid(
    grid: dict[str, Any],
    *,
    grid_path: Path,
    criteria_path: Path,
    baseline_manifest_path: Path,
    baseline_qualification_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify that the predeclared grid still binds the frozen evidence."""

    if grid.get("schema_version") != 1:
        raise ValueError("replacement grid schema_version must be 1")
    if grid.get("status") != "predeclared_before_replacement_exploration":
        raise ValueError("replacement grid must be marked as predeclared")
    if grid.get("evidence_layer") != "exploratory_candidate_search":
        raise ValueError("replacement grid has the wrong evidence layer")

    criteria = load_json_object(criteria_path)
    validate_criteria(criteria)
    baseline = load_json_object(baseline_qualification_path)
    frozen = grid["frozen_inputs"]
    actual_hashes = {
        "criteria_sha256": sha256_file(criteria_path),
        "baseline_manifest_sha256": sha256_file(baseline_manifest_path),
        "baseline_qualification_sha256": sha256_file(
            baseline_qualification_path
        ),
    }
    declared_hashes = {
        key: frozen[key]
        for key in (
            "criteria_sha256",
            "baseline_manifest_sha256",
            "baseline_qualification_sha256",
        )
    }
    if actual_hashes != declared_hashes:
        raise ValueError(
            "replacement grid is stale relative to its frozen inputs: "
            + json.dumps(
                {
                    "declared": declared_hashes,
                    "actual": actual_hashes,
                },
                sort_keys=True,
            )
        )

    baseline_decisions = {
        item["manifest_id"]: item["decision"]
        for item in baseline["manifests"]
    }
    failed_ids = frozen["failed_manifest_ids"]
    if any(
        baseline_decisions.get(manifest_id)
        != "not_qualified_dynamic_screen_failed"
        for manifest_id in failed_ids
    ):
        raise ValueError(
            "replacement grid targets a manifest not failed in the baseline"
        )

    candidates = grid.get("candidates", [])
    if not candidates:
        raise ValueError("replacement grid must contain candidates")
    candidate_ids: set[str] = set()
    replaced_ids: set[str] = set()
    systems: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        replaced_id = candidate["replaces_manifest_id"]
        manifest = candidate["manifest"]
        if candidate_id in candidate_ids:
            raise ValueError(f"duplicate candidate_id: {candidate_id}")
        if replaced_id in replaced_ids:
            raise ValueError(
                "grid must contain exactly one preselected candidate per "
                f"failed manifest: {replaced_id}"
            )
        if manifest["system"] in systems:
            raise ValueError(
                "grid must contain exactly one preselected candidate per "
                f"system: {manifest['system']}"
            )
        if replaced_id not in failed_ids:
            raise ValueError(f"candidate targets undeclared failure: {replaced_id}")
        if not candidate["source"].get("contract_is_explicit"):
            raise ValueError(
                f"{candidate_id}: numerical contract is not explicit"
            )
        if len(manifest["parameters"]) != 3:
            raise ValueError(f"{candidate_id}: expected three parameters")
        if len(manifest["initial_state"]) != 3:
            raise ValueError(f"{candidate_id}: expected three initial states")
        if not 0.0 < float(manifest["q"]) <= 1.0:
            raise ValueError(f"{candidate_id}: q must satisfy 0 < q <= 1")
        if float(manifest["h"]) <= 0.0:
            raise ValueError(f"{candidate_id}: h must be positive")
        candidate_ids.add(candidate_id)
        replaced_ids.add(replaced_id)
        systems.add(manifest["system"])

    if replaced_ids != set(failed_ids):
        raise ValueError(
            "grid must preselect exactly one candidate for every failed manifest"
        )

    binding = {
        "grid_source": portable_path(grid_path),
        "grid_sha256": sha256_file(grid_path),
        "criteria_source": portable_path(criteria_path),
        "criteria_sha256": actual_hashes["criteria_sha256"],
        "baseline_manifest_source": portable_path(baseline_manifest_path),
        "baseline_manifest_sha256": actual_hashes[
            "baseline_manifest_sha256"
        ],
        "baseline_qualification_source": portable_path(
            baseline_qualification_path
        ),
        "baseline_qualification_sha256": actual_hashes[
            "baseline_qualification_sha256"
        ],
        "passed": True,
    }
    return criteria, binding


def explore_grid(
    *,
    grid_path: Path,
    criteria_path: Path,
    baseline_manifest_path: Path,
    baseline_qualification_path: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Run the exact predeclared candidates against frozen diagnostics."""

    grid = load_json_object(grid_path)
    criteria, binding = validate_grid(
        grid,
        grid_path=grid_path,
        criteria_path=criteria_path,
        baseline_manifest_path=baseline_manifest_path,
        baseline_qualification_path=baseline_qualification_path,
    )
    horizon = float(criteria["integration"]["physical_horizon_s"])
    started = datetime.now(timezone.utc)
    total_start = time.perf_counter()
    candidate_results: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []

    for candidate in grid["candidates"]:
        manifest = candidate["manifest"]
        rhs = fractional_system_rhs(
            manifest["system"],
            manifest["parameters"],
        )
        resolution_results: list[dict[str, Any]] = []
        for divisor in criteria["integration"]["resolution_divisors"]:
            h = float(manifest["h"]) / int(divisor)
            steps = int(round(horizon / h))
            if not math.isclose(
                steps * h,
                horizon,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            ):
                raise ValueError(
                    f"{candidate['candidate_id']}: horizon is not an integer "
                    "number of steps"
                )

            run_start = time.perf_counter()
            result = caputo_abm_full_memory(
                rhs,
                manifest["initial_state"],
                q=float(manifest["q"]),
                h=h,
                steps=steps,
            )
            runtime_s = time.perf_counter() - run_start
            diagnostics, blocks, sample_times, sample_states = (
                diagnose_resolution(
                    times=result.times,
                    states=result.states,
                    criteria=criteria,
                )
            )
            trajectory_hash = sha256_states(result.states)
            resolution_results.append(
                {
                    "resolution_divisor": int(divisor),
                    "h": h,
                    "steps": steps,
                    "rhs_evaluations": result.rhs_evaluations,
                    "runtime_s": runtime_s,
                    "trajectory_float64_le_sha256": trajectory_hash,
                    "diagnostics": diagnostics,
                }
            )
            summary_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "replaces_manifest_id": candidate["replaces_manifest_id"],
                    "proposed_manifest_id": manifest["manifest_id"],
                    "system": manifest["system"],
                    "resolution_divisor": divisor,
                    "h": h,
                    "steps": steps,
                    "physical_horizon_s": horizon,
                    "transient_s": criteria["integration"]["transient_s"],
                    "runtime_s": runtime_s,
                    "trajectory_float64_le_sha256": trajectory_hash,
                    "maximum_absolute_state": diagnostics[
                        "observed_boundedness"
                    ]["maximum_absolute_state"],
                    "active_components": diagnostics["activity"][
                        "active_components"
                    ],
                    "minimum_normalized_lag_rmse": diagnostics[
                        "nonperiodicity_screen"
                    ]["minimum_normalized_lag_rmse"],
                    "normalized_permutation_entropy": diagnostics[
                        "nonperiodicity_screen"
                    ]["normalized_permutation_entropy"],
                    "maximum_block_mean_shift_in_global_std": diagnostics[
                        "within_resolution_stability"
                    ]["maximum_block_mean_shift_in_global_std"],
                    "minimum_block_to_global_std_ratio": diagnostics[
                        "within_resolution_stability"
                    ]["minimum_block_to_global_std_ratio"],
                    "maximum_block_to_global_std_ratio": diagnostics[
                        "within_resolution_stability"
                    ]["maximum_block_to_global_std_ratio"],
                    "resolution_passed": resolution_passed(diagnostics),
                }
            )
            for block in blocks:
                block_rows.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "system": manifest["system"],
                        "resolution_divisor": divisor,
                        "h": h,
                        "block_index": block["block_index"],
                        "samples": block["samples"],
                        **{
                            f"{name}_{component}": block[name][component]
                            for name, component in itertools.product(
                                (
                                    "mean",
                                    "standard_deviation",
                                    "mean_shift_in_global_std",
                                    "std_to_global_ratio",
                                ),
                                range(3),
                            )
                        },
                    }
                )
            for sample_time, sample in zip(
                sample_times,
                sample_states,
                strict=True,
            ):
                trajectory_rows.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "system": manifest["system"],
                        "resolution_divisor": divisor,
                        "h": h,
                        "time_s": float(sample_time),
                        "x": float(sample[0]),
                        "y": float(sample[1]),
                        "z": float(sample[2]),
                    }
                )

        cross = compare_resolutions(
            resolution_results[0]["diagnostics"],
            resolution_results[1]["diagnostics"],
            criteria,
        )
        dynamic_decision, reasons = manifest_decision(
            resolution_results,
            cross,
        )
        eligible = (
            dynamic_decision == "qualified_observed_long_horizon_screen"
        )
        for row in summary_rows[-len(resolution_results) :]:
            row["cross_resolution_passed"] = cross["passed"]
            row["replacement_eligible"] = eligible
        candidate_results.append(
            {
                "candidate_id": candidate["candidate_id"],
                "replaces_manifest_id": candidate["replaces_manifest_id"],
                "source": candidate["source"],
                "manifest": manifest,
                "exploratory_dynamic_decision": dynamic_decision,
                "replacement_eligible_for_v2_freeze": eligible,
                "failure_reasons": reasons,
                "resolutions": resolution_results,
                "cross_resolution_stability": cross,
            }
        )

    selected = {
        item["manifest"]["system"]: (
            item["candidate_id"]
            if item["replacement_eligible_for_v2_freeze"]
            else None
        )
        for item in candidate_results
    }
    all_eligible = all(value is not None for value in selected.values())
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "status": (
            "all_failed_systems_have_eligible_replacements"
            if all_eligible
            else "one_or_more_failed_systems_lack_eligible_replacement"
        ),
        "evidence_layer": "exploratory_candidate_search",
        "formal_qualification": False,
        "grid": {
            "grid_id": grid["grid_id"],
            "status": grid["status"],
            "selection_rule": grid["selection_rule"],
            "binding": binding,
        },
        "criteria_snapshot": criteria,
        "execution": {
            "started_utc": started.isoformat(),
            "completed_utc": completed.isoformat(),
            "wall_time_s": time.perf_counter() - total_start,
            "python_version": sys.version.split()[0],
            "numpy_version": np.__version__,
            "platform": platform.platform(),
        },
        "candidates": candidate_results,
        "predeclared_selection": selected,
        "all_failed_systems_have_eligible_replacements": all_eligible,
        "next_step": (
            "Freeze a versioned manifest and repeat independent "
            "implementation validation plus formal qualification."
            if all_eligible
            else "Do not replace any failed system lacking an eligible "
            "candidate; record the blocker without changing thresholds."
        ),
        "does_not_establish": grid["does_not_establish"],
    }
    return report, summary_rows, block_rows, trajectory_rows


def write_outputs(
    output_dir: Path,
    report: dict[str, Any],
    summary_rows: list[dict[str, Any]],
    block_rows: list[dict[str, Any]],
    trajectory_rows: list[dict[str, Any]],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "report": output_dir / "exploration.json",
        "summary": output_dir / "exploration_summary.csv",
        "blocks": output_dir / "exploration_blocks.csv",
        "trajectory": output_dir / "exploration_trajectory_samples.csv",
    }
    paths["report"].write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(paths["summary"], summary_rows)
    write_csv(paths["blocks"], block_rows)
    write_csv(paths["trajectory"], trajectory_rows)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)
    parser.add_argument(
        "--baseline-manifests",
        type=Path,
        default=DEFAULT_BASELINE_MANIFESTS,
    )
    parser.add_argument(
        "--baseline-qualification",
        type=Path,
        default=DEFAULT_BASELINE_QUALIFICATION,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    report, summary, blocks, trajectory = explore_grid(
        grid_path=args.grid.resolve(),
        criteria_path=args.criteria.resolve(),
        baseline_manifest_path=args.baseline_manifests.resolve(),
        baseline_qualification_path=args.baseline_qualification.resolve(),
    )
    paths = write_outputs(
        args.output_dir.resolve(),
        report,
        summary,
        blocks,
        trajectory,
    )
    print(
        f"{report['status']}: {paths['report']} "
        f"({report['execution']['wall_time_s']:.3f} s)"
    )
    return (
        0
        if report["all_failed_systems_have_eligible_replacements"]
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
