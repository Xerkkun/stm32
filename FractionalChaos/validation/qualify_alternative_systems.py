#!/usr/bin/env python3
"""Qualify the frozen alternative-system cohort with full-memory Caputo ABM."""

from __future__ import annotations

import argparse
import csv
import hashlib
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

from abm_oracle import caputo_abm_full_memory
from alternative_systems import alternative_system_rhs
from long_horizon_qualification import (
    compare_resolutions,
    diagnose_resolution,
    manifest_decision,
    resolution_passed,
    sha256_states,
    validate_criteria,
)

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFESTS = HERE / "alternative_system_manifests_v1.json"
DEFAULT_GRID = HERE / "alternative_system_candidate_grid_v1.json"
DEFAULT_CRITERIA = HERE / "alternative_system_criteria_v1.json"
DEFAULT_IMPLEMENTATION_REPORT = (
    HERE / "results" / "alternative_system_oracle_validation_v1.json"
)
DEFAULT_OUTPUT_DIR = (
    HERE / "results" / "alternative_system_qualification_v1"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(HERE.parent).as_posix()
    except ValueError:
        return str(path.resolve())


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_prerequisite(
    report: dict[str, Any],
    *,
    report_path: Path,
    manifest_path: Path,
    grid_path: Path,
    criteria: dict[str, Any],
) -> dict[str, Any]:
    provenance = report.get("provenance", {})
    current = {
        "abm_oracle_source_sha256": sha256_file(HERE / "abm_oracle.py"),
        "alternative_rhs_source_sha256": sha256_file(
            HERE / "alternative_systems.py"
        ),
        "candidate_manifest_sha256": sha256_file(manifest_path),
        "candidate_grid_sha256": sha256_file(grid_path),
    }
    checks = {
        "status_passed": (
            report.get("status")
            == "passed_alternative_system_abm_validation"
        ),
        "algorithm_validation_passed": bool(
            report.get("algorithm_validation", {}).get("passed")
        ),
        **{
            f"{name}_matches_report": provenance.get(name) == value
            for name, value in current.items()
        },
        "manifest_matches_predeclared_criteria": (
            current["candidate_manifest_sha256"]
            == criteria["inputs"]["candidate_manifest_sha256"]
        ),
        "grid_matches_predeclared_criteria": (
            current["candidate_grid_sha256"]
            == criteria["inputs"]["candidate_grid_sha256"]
        ),
    }
    return {
        "source": portable_path(report_path),
        "source_sha256": sha256_file(report_path),
        "current": current,
        "checks": checks,
        "passed": all(checks.values()),
    }


def upper_margin(limit: float, observed: float) -> float:
    return limit / max(abs(observed), 1.0e-15)


def lower_margin(observed: float, limit: float) -> float:
    return observed / limit


def continuous_acceptance_margins(
    resolutions: list[dict[str, Any]],
    cross: dict[str, Any],
) -> dict[str, float]:
    margins: dict[str, float] = {}
    for resolution in resolutions:
        divisor = resolution["resolution_divisor"]
        diagnostics = resolution["diagnostics"]
        bounded = diagnostics["observed_boundedness"]
        within = diagnostics["within_resolution_stability"]
        nonperiodic = diagnostics["nonperiodicity_screen"]
        prefix = f"h_over_{divisor}"
        margins[f"{prefix}.boundedness"] = upper_margin(
            float(bounded["threshold"]),
            float(bounded["maximum_absolute_state"]),
        )
        margins[f"{prefix}.block_mean_shift"] = upper_margin(
            float(
                within["thresholds"][
                    "maximum_block_mean_shift_in_global_std"
                ]
            ),
            float(within["maximum_block_mean_shift_in_global_std"]),
        )
        margins[f"{prefix}.minimum_block_std_ratio"] = lower_margin(
            float(within["minimum_block_to_global_std_ratio"]),
            float(
                within["thresholds"]["minimum_block_to_global_std_ratio"]
            ),
        )
        margins[f"{prefix}.maximum_block_std_ratio"] = upper_margin(
            float(
                within["thresholds"]["maximum_block_to_global_std_ratio"]
            ),
            float(within["maximum_block_to_global_std_ratio"]),
        )
        margins[f"{prefix}.lag_rmse"] = lower_margin(
            float(nonperiodic["minimum_normalized_lag_rmse"]),
            float(
                nonperiodic["thresholds"][
                    "minimum_normalized_lag_rmse"
                ]
            ),
        )
        margins[f"{prefix}.permutation_entropy"] = lower_margin(
            float(nonperiodic["normalized_permutation_entropy"]),
            float(
                nonperiodic["thresholds"][
                    "minimum_normalized_permutation_entropy"
                ]
            ),
        )

    cross_mapping = {
        "mean_difference": (
            "maximum_mean_difference_in_pooled_std",
            "maximum_mean_difference_in_pooled_std",
        ),
        "standard_deviation_ratio": (
            "maximum_symmetric_std_ratio",
            "maximum_symmetric_std_ratio",
        ),
        "quantile_difference": (
            "maximum_quantile_difference_in_pooled_std",
            "maximum_quantile_difference_in_pooled_std",
        ),
        "permutation_entropy_difference": (
            "permutation_entropy_difference",
            "maximum_permutation_entropy_difference",
        ),
    }
    for label, (observed_field, threshold_field) in cross_mapping.items():
        margins[f"cross.{label}"] = upper_margin(
            float(cross["thresholds"][threshold_field]),
            float(cross[observed_field]),
        )
    return margins


def build_selection(
    manifest_results: list[dict[str, Any]],
    criteria: dict[str, Any],
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for item in manifest_results:
        if item["decision"] != "qualified_observed_long_horizon_screen":
            item["selection"] = {
                "eligible": False,
                "minimum_normalized_continuous_margin": None,
                "selected_for_formal_followup": False,
            }
            continue
        margins = continuous_acceptance_margins(
            item["resolutions"],
            item["cross_resolution_stability"],
        )
        score = min(margins.values())
        candidate = {
            "manifest_id": item["manifest_id"],
            "candidate_id": item["manifest"]["candidate_id"],
            "minimum_normalized_continuous_margin": score,
            "continuous_margins": margins,
        }
        eligible.append(candidate)
        item["selection"] = {
            "eligible": True,
            **candidate,
            "selected_for_formal_followup": False,
        }

    eligible.sort(
        key=lambda row: (
            -float(row["minimum_normalized_continuous_margin"]),
            str(row["candidate_id"]),
        )
    )
    count = int(criteria["selection_rule"]["promotion_count"])
    selected_ids = {
        row["manifest_id"] for row in eligible[:count]
    }
    for item in manifest_results:
        item["selection"]["selected_for_formal_followup"] = (
            item["manifest_id"] in selected_ids
        )
    return {
        "rule": criteria["selection_rule"],
        "eligible_ranked": eligible,
        "selected_manifest_ids": [
            row["manifest_id"]
            for row in eligible[:count]
        ],
        "requested_promotion_count": count,
        "actual_selection_count": min(count, len(eligible)),
        "thresholds_unchanged": True,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_qualification(
    *,
    manifest_path: Path,
    grid_path: Path,
    criteria_path: Path,
    implementation_report_path: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    criteria = load_json_object(criteria_path)
    validate_criteria(criteria)
    manifest_payload = load_json_object(manifest_path)
    grid = load_json_object(grid_path)
    implementation_report = load_json_object(implementation_report_path)
    prerequisite = validate_prerequisite(
        implementation_report,
        report_path=implementation_report_path,
        manifest_path=manifest_path,
        grid_path=grid_path,
        criteria=criteria,
    )
    if not prerequisite["passed"]:
        raise RuntimeError(
            "alternative-system prerequisite is stale or failed: "
            + json.dumps(prerequisite["checks"], sort_keys=True)
        )

    expected_ids = criteria["inputs"]["manifest_ids"]
    manifests_by_id = {
        item["manifest_id"]: item
        for item in manifest_payload["manifests"]
    }
    if list(manifests_by_id) != expected_ids:
        raise ValueError(
            "manifest order/identity differs from predeclared criteria"
        )

    started = datetime.now(timezone.utc)
    total_start = time.perf_counter()
    manifest_results: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []
    horizon = float(criteria["integration"]["physical_horizon_s"])

    for manifest_id in expected_ids:
        manifest = manifests_by_id[manifest_id]
        rhs = alternative_system_rhs(
            manifest["system"],
            manifest["parameters"],
        )
        resolutions: list[dict[str, Any]] = []
        integration_failures: list[str] = []

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
                    f"{manifest_id}: horizon is not an integer number of steps"
                )

            run_start = time.perf_counter()
            try:
                result = caputo_abm_full_memory(
                    rhs,
                    manifest["initial_state"],
                    q=float(manifest["q"]),
                    h=h,
                    steps=steps,
                )
                (
                    diagnostics,
                    blocks,
                    sampled_times,
                    sampled_states,
                ) = diagnose_resolution(
                    times=result.times,
                    states=result.states,
                    criteria=criteria,
                )
            except (FloatingPointError, ValueError, OverflowError) as exc:
                runtime_s = time.perf_counter() - run_start
                failure = (
                    f"h/{divisor} integration_failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                integration_failures.append(failure)
                resolutions.append(
                    {
                        "resolution_divisor": int(divisor),
                        "h": h,
                        "steps": steps,
                        "runtime_s": runtime_s,
                        "integration_status": "failed",
                        "error": failure,
                    }
                )
                summary_rows.append(
                    {
                        "manifest_id": manifest_id,
                        "candidate_id": manifest["candidate_id"],
                        "system": manifest["system"],
                        "resolution_divisor": divisor,
                        "h": h,
                        "steps": steps,
                        "physical_horizon_s": horizon,
                        "transient_s": criteria["integration"]["transient_s"],
                        "runtime_s": runtime_s,
                        "trajectory_float64_le_sha256": "",
                        "all_finite": False,
                        "maximum_absolute_state": "",
                        "active_components": "",
                        "minimum_normalized_lag_rmse": "",
                        "lag_at_minimum_s": "",
                        "normalized_permutation_entropy": "",
                        "maximum_block_mean_shift_in_global_std": "",
                        "minimum_block_to_global_std_ratio": "",
                        "maximum_block_to_global_std_ratio": "",
                        "resolution_passed": False,
                        "cross_resolution_passed": False,
                        "manifest_decision": "not_qualified_integration_failed",
                    }
                )
                continue

            runtime_s = time.perf_counter() - run_start
            trajectory_hash = sha256_states(result.states)
            resolutions.append(
                {
                    "resolution_divisor": int(divisor),
                    "h": h,
                    "steps": steps,
                    "rhs_evaluations": result.rhs_evaluations,
                    "runtime_s": runtime_s,
                    "trajectory_float64_le_sha256": trajectory_hash,
                    "integration_status": "completed",
                    "diagnostics": diagnostics,
                }
            )
            summary_rows.append(
                {
                    "manifest_id": manifest_id,
                    "candidate_id": manifest["candidate_id"],
                    "system": manifest["system"],
                    "resolution_divisor": divisor,
                    "h": h,
                    "steps": steps,
                    "physical_horizon_s": horizon,
                    "transient_s": criteria["integration"]["transient_s"],
                    "runtime_s": runtime_s,
                    "trajectory_float64_le_sha256": trajectory_hash,
                    "all_finite": diagnostics["observed_boundedness"][
                        "checks"
                    ]["all_finite"],
                    "maximum_absolute_state": diagnostics[
                        "observed_boundedness"
                    ]["maximum_absolute_state"],
                    "active_components": diagnostics["activity"][
                        "active_components"
                    ],
                    "minimum_normalized_lag_rmse": diagnostics[
                        "nonperiodicity_screen"
                    ]["minimum_normalized_lag_rmse"],
                    "lag_at_minimum_s": diagnostics[
                        "nonperiodicity_screen"
                    ]["lag_at_minimum_s"],
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
                    "cross_resolution_passed": "",
                    "manifest_decision": "",
                }
            )
            for block in blocks:
                block_rows.append(
                    {
                        "manifest_id": manifest_id,
                        "candidate_id": manifest["candidate_id"],
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
                sampled_times,
                sampled_states,
                strict=True,
            ):
                trajectory_rows.append(
                    {
                        "manifest_id": manifest_id,
                        "candidate_id": manifest["candidate_id"],
                        "system": manifest["system"],
                        "resolution_divisor": divisor,
                        "h": h,
                        "time_s": float(sample_time),
                        "x": float(sample[0]),
                        "y": float(sample[1]),
                        "z": float(sample[2]),
                    }
                )

        if integration_failures:
            cross: dict[str, Any] = {
                "passed": False,
                "not_evaluated_reason": "one_or_more_integrations_failed",
            }
            decision = "not_qualified_integration_failed"
            reasons = integration_failures
        else:
            cross = compare_resolutions(
                resolutions[0]["diagnostics"],
                resolutions[1]["diagnostics"],
                criteria,
            )
            decision, reasons = manifest_decision(resolutions, cross)

        for row in summary_rows[-len(resolutions) :]:
            row["cross_resolution_passed"] = cross["passed"]
            row["manifest_decision"] = decision
        manifest_results.append(
            {
                "manifest_id": manifest_id,
                "candidate_id": manifest["candidate_id"],
                "system": manifest["system"],
                "manifest": manifest,
                "decision": decision,
                "failure_reasons": reasons,
                "resolutions": resolutions,
                "cross_resolution_stability": cross,
            }
        )

    selection = build_selection(manifest_results, criteria)
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "status": "alternative_system_qualification_completed",
        "evidence_layer": "dynamic_manifest_qualification",
        "scope": criteria["scope"],
        "criteria": {
            "source": portable_path(criteria_path),
            "source_sha256": sha256_file(criteria_path),
            "criteria_id": criteria["criteria_id"],
            "status": criteria["status"],
            "decision_rule": criteria["decision_rule"],
        },
        "criteria_snapshot": criteria,
        "candidate_grid_snapshot": grid,
        "implementation_validation_prerequisite": prerequisite,
        "provenance": {
            "qualification_source": portable_path(Path(__file__)),
            "qualification_source_sha256": sha256_file(Path(__file__)),
            "abm_oracle_source": portable_path(HERE / "abm_oracle.py"),
            "abm_oracle_source_sha256": sha256_file(
                HERE / "abm_oracle.py"
            ),
            "alternative_rhs_source": portable_path(
                HERE / "alternative_systems.py"
            ),
            "alternative_rhs_source_sha256": sha256_file(
                HERE / "alternative_systems.py"
            ),
            "candidate_manifest_source": portable_path(manifest_path),
            "candidate_manifest_sha256": sha256_file(manifest_path),
            "candidate_grid_source": portable_path(grid_path),
            "candidate_grid_sha256": sha256_file(grid_path),
        },
        "execution": {
            "started_utc": started.isoformat(),
            "completed_utc": completed.isoformat(),
            "wall_time_s": time.perf_counter() - total_start,
            "python_version": sys.version.split()[0],
            "numpy_version": np.__version__,
            "platform": platform.platform(),
        },
        "manifests": manifest_results,
        "selection": selection,
        "qualified_manifest_ids": [
            item["manifest_id"]
            for item in manifest_results
            if item["decision"]
            == "qualified_observed_long_horizon_screen"
        ],
        "does_not_establish": criteria["does_not_establish"],
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
        "report": output_dir / "qualification.json",
        "summary": output_dir / "qualification_summary.csv",
        "blocks": output_dir / "qualification_blocks.csv",
        "trajectory": output_dir / "qualification_trajectory_samples.csv",
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
    parser.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    parser.add_argument("--candidate-grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)
    parser.add_argument(
        "--implementation-report",
        type=Path,
        default=DEFAULT_IMPLEMENTATION_REPORT,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    report, summary, blocks, trajectory = run_qualification(
        manifest_path=args.manifests.resolve(),
        grid_path=args.candidate_grid.resolve(),
        criteria_path=args.criteria.resolve(),
        implementation_report_path=args.implementation_report.resolve(),
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
