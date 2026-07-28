#!/usr/bin/env python3
"""Qualify candidate manifests with long-horizon full-memory Caputo ABM runs.

This is a dynamic-screen layer.  It consumes, but never replaces, the
separate manufactured-solution validation of the ABM implementation.
"""

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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from abm_oracle import caputo_abm_full_memory, fractional_system_rhs

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFESTS = HERE / "candidate_manifests.json"
DEFAULT_CRITERIA = HERE / "long_horizon_criteria.json"
DEFAULT_IMPLEMENTATION_REPORT = HERE / "results" / "abm_oracle_validation.json"
DEFAULT_OUTPUT_DIR = HERE / "results" / "abm_long_horizon"


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 of a file."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_states(states: np.ndarray) -> str:
    """Hash a trajectory under a portable little-endian float64 contract."""

    canonical = np.ascontiguousarray(states, dtype="<f8")
    return hashlib.sha256(canonical.tobytes(order="C")).hexdigest()


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


def validate_criteria(criteria: dict[str, Any]) -> None:
    """Reject incomplete or internally inconsistent predeclared criteria."""

    if criteria.get("schema_version") != 1:
        raise ValueError("criteria schema_version must be 1")
    if criteria.get("status") != "predeclared_before_first_qualification_run":
        raise ValueError("criteria must be marked as predeclared")

    integration = criteria["integration"]
    divisors = integration["resolution_divisors"]
    if divisors != [2, 4]:
        raise ValueError("resolution_divisors must be the frozen [2, 4]")
    horizon = float(integration["physical_horizon_s"])
    transient = float(integration["transient_s"])
    sample_period = float(integration["diagnostic_sample_period_s"])
    if not (horizon > transient > 0.0 and sample_period > 0.0):
        raise ValueError("horizon, transient, or diagnostic sample period invalid")

    boundedness = criteria["observed_boundedness"]
    if float(boundedness["maximum_absolute_state"]) <= 0.0:
        raise ValueError("maximum_absolute_state must be positive")
    if int(boundedness["minimum_active_components"]) not in (1, 2, 3):
        raise ValueError("minimum_active_components must be in [1, 3]")

    nonperiodicity = criteria["nonperiodicity_screen"]
    lag_min, lag_max = (float(value) for value in nonperiodicity["lag_window_s"])
    if not 0.0 < lag_min < lag_max < (horizon - transient):
        raise ValueError("nonperiodicity lag window is outside the analysis span")
    order = int(nonperiodicity["permutation_entropy_order"])
    if not 3 <= order <= 7:
        raise ValueError("permutation entropy order must be in [3, 7]")

    blocks = int(
        criteria["within_resolution_stability"]["post_transient_blocks"]
    )
    if blocks < 2:
        raise ValueError("at least two post-transient blocks are required")


def validate_implementation_prerequisite(
    report: dict[str, Any],
    *,
    report_path: Path,
    oracle_path: Path,
    manifest_path: Path,
    expected_manifest_sha256: str,
) -> dict[str, Any]:
    """Verify the independent implementation-validation evidence and hashes."""

    current_oracle_hash = sha256_file(oracle_path)
    current_manifest_hash = sha256_file(manifest_path)
    provenance = report.get("provenance", {})
    checks = {
        "status_passed": (
            report.get("status") == "passed_abm_implementation_validation"
        ),
        "algorithm_validation_passed": bool(
            report.get("algorithm_validation", {}).get("passed")
        ),
        "oracle_hash_matches_report": (
            provenance.get("oracle_source_sha256") == current_oracle_hash
        ),
        "manifest_hash_matches_report": (
            provenance.get("candidate_manifest_sha256")
            == current_manifest_hash
        ),
        "manifest_hash_matches_predeclared_criteria": (
            current_manifest_hash == expected_manifest_sha256
        ),
    }
    return {
        "evidence_layer": "abm_implementation_validation",
        "source": portable_path(report_path),
        "source_sha256": sha256_file(report_path),
        "current_oracle_sha256": current_oracle_hash,
        "current_manifest_sha256": current_manifest_hash,
        "checks": checks,
        "passed": all(checks.values()),
    }


def normalized_permutation_entropy(
    values: np.ndarray,
    *,
    order: int,
    delay: int,
) -> float:
    """Return Bandt--Pompe permutation entropy normalized to [0, 1]."""

    series = np.asarray(values, dtype=np.float64)
    if series.ndim != 1:
        raise ValueError("permutation entropy input must be one-dimensional")
    if order < 2 or delay < 1:
        raise ValueError("invalid permutation entropy order or delay")
    count = series.size - (order - 1) * delay
    if count <= 0:
        raise ValueError("series is too short for permutation entropy")

    patterns: Counter[tuple[int, ...]] = Counter()
    offsets = np.arange(order, dtype=np.int64) * delay
    for index in range(count):
        pattern = tuple(
            int(value)
            for value in np.argsort(
                series[index + offsets],
                kind="stable",
            )
        )
        patterns[pattern] += 1

    probabilities = np.asarray(
        [frequency / count for frequency in patterns.values()],
        dtype=np.float64,
    )
    entropy = -float(np.sum(probabilities * np.log(probabilities)))
    maximum = math.log(math.factorial(order))
    return entropy / maximum if maximum > 0.0 else 0.0


def component_summary(states: np.ndarray, quantiles: Iterable[float]) -> dict[str, Any]:
    probabilities = np.asarray(tuple(quantiles), dtype=np.float64)
    return {
        "mean": [float(value) for value in np.mean(states, axis=0)],
        "standard_deviation": [
            float(value) for value in np.std(states, axis=0)
        ],
        "minimum": [float(value) for value in np.min(states, axis=0)],
        "maximum": [float(value) for value in np.max(states, axis=0)],
        "peak_to_peak": [float(value) for value in np.ptp(states, axis=0)],
        "quantile_probabilities": [float(value) for value in probabilities],
        "quantiles": [
            [float(value) for value in row]
            for row in np.quantile(states, probabilities, axis=0)
        ],
    }


def block_stability(
    states: np.ndarray,
    *,
    block_count: int,
    max_mean_shift: float,
    min_std_ratio: float,
    max_std_ratio: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global_mean = np.mean(states, axis=0)
    global_std = np.maximum(np.std(states, axis=0), 1.0e-15)
    rows: list[dict[str, Any]] = []
    maximum_shift = 0.0
    minimum_ratio = math.inf
    maximum_ratio = 0.0

    for index, block in enumerate(np.array_split(states, block_count)):
        block_mean = np.mean(block, axis=0)
        block_std = np.std(block, axis=0)
        shifts = np.abs(block_mean - global_mean) / global_std
        ratios = block_std / global_std
        maximum_shift = max(maximum_shift, float(np.max(shifts)))
        minimum_ratio = min(minimum_ratio, float(np.min(ratios)))
        maximum_ratio = max(maximum_ratio, float(np.max(ratios)))
        rows.append(
            {
                "block_index": index,
                "samples": int(block.shape[0]),
                "mean": [float(value) for value in block_mean],
                "standard_deviation": [
                    float(value) for value in block_std
                ],
                "mean_shift_in_global_std": [
                    float(value) for value in shifts
                ],
                "std_to_global_ratio": [
                    float(value) for value in ratios
                ],
            }
        )

    checks = {
        "maximum_mean_shift": maximum_shift <= max_mean_shift,
        "minimum_std_ratio": minimum_ratio >= min_std_ratio,
        "maximum_std_ratio": maximum_ratio <= max_std_ratio,
    }
    return (
        {
            "maximum_block_mean_shift_in_global_std": maximum_shift,
            "minimum_block_to_global_std_ratio": minimum_ratio,
            "maximum_block_to_global_std_ratio": maximum_ratio,
            "thresholds": {
                "maximum_block_mean_shift_in_global_std": max_mean_shift,
                "minimum_block_to_global_std_ratio": min_std_ratio,
                "maximum_block_to_global_std_ratio": max_std_ratio,
            },
            "checks": checks,
            "passed": all(checks.values()),
        },
        rows,
    )


def lag_recurrence_screen(
    states: np.ndarray,
    *,
    sample_period: float,
    lag_min_s: float,
    lag_max_s: float,
) -> dict[str, Any]:
    scale = np.maximum(np.std(states, axis=0), 1.0e-15)
    first_lag = max(1, int(math.ceil(lag_min_s / sample_period)))
    last_lag = min(
        states.shape[0] - 1,
        int(math.floor(lag_max_s / sample_period)),
    )
    if first_lag > last_lag:
        raise ValueError("lag window contains no samples")

    minimum_rmse = math.inf
    minimum_lag = first_lag
    for lag in range(first_lag, last_lag + 1):
        normalized = (states[lag:] - states[:-lag]) / scale
        rmse = float(np.sqrt(np.mean(normalized * normalized)))
        if rmse < minimum_rmse:
            minimum_rmse = rmse
            minimum_lag = lag
    return {
        "minimum_normalized_lag_rmse": minimum_rmse,
        "lag_at_minimum_s": minimum_lag * sample_period,
        "lag_window_s": [lag_min_s, lag_max_s],
    }


def diagnose_resolution(
    *,
    times: np.ndarray,
    states: np.ndarray,
    criteria: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], np.ndarray, np.ndarray]:
    integration = criteria["integration"]
    bounded = criteria["observed_boundedness"]
    nonperiodic = criteria["nonperiodicity_screen"]
    within = criteria["within_resolution_stability"]
    cross = criteria["cross_resolution_stability"]
    transient = float(integration["transient_s"])
    target_period = float(integration["diagnostic_sample_period_s"])

    transient_index = int(np.searchsorted(times, transient, side="left"))
    post_times = times[transient_index:]
    post_states = states[transient_index:]
    if post_states.shape[0] < 2:
        raise ValueError("post-transient trajectory is empty")

    native_h = float(times[1] - times[0])
    diagnostic_stride = int(round(target_period / native_h))
    if diagnostic_stride < 1 or not math.isclose(
        diagnostic_stride * native_h,
        target_period,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            "diagnostic_sample_period_s must be an integer multiple of h"
        )
    sampled_times = post_times[::diagnostic_stride]
    sampled_states = post_states[::diagnostic_stride]

    all_finite = bool(np.all(np.isfinite(states)))
    maximum_absolute_state = float(np.max(np.abs(states)))
    summary = component_summary(post_states, cross["quantiles"])
    standard_deviation = np.asarray(summary["standard_deviation"])
    peak_to_peak = np.asarray(summary["peak_to_peak"])
    active_mask = (
        standard_deviation
        >= float(bounded["minimum_component_standard_deviation"])
    ) & (
        peak_to_peak >= float(bounded["minimum_component_peak_to_peak"])
    )
    active_components = int(np.count_nonzero(active_mask))

    bounded_checks = {
        "all_finite": all_finite,
        "maximum_absolute_state": (
            maximum_absolute_state
            <= float(bounded["maximum_absolute_state"])
        ),
    }
    activity_checks = {
        "minimum_active_components": (
            active_components >= int(bounded["minimum_active_components"])
        )
    }

    block_result, block_rows = block_stability(
        post_states,
        block_count=int(within["post_transient_blocks"]),
        max_mean_shift=float(
            within["maximum_block_mean_shift_in_global_std"]
        ),
        min_std_ratio=float(within["minimum_block_to_global_std_ratio"]),
        max_std_ratio=float(within["maximum_block_to_global_std_ratio"]),
    )

    selected_component = int(np.argmax(standard_deviation))
    entropy_delay = int(
        round(
            float(nonperiodic["permutation_entropy_delay_s"])
            / target_period
        )
    )
    entropy = normalized_permutation_entropy(
        sampled_states[:, selected_component],
        order=int(nonperiodic["permutation_entropy_order"]),
        delay=entropy_delay,
    )
    lag_result = lag_recurrence_screen(
        sampled_states,
        sample_period=target_period,
        lag_min_s=float(nonperiodic["lag_window_s"][0]),
        lag_max_s=float(nonperiodic["lag_window_s"][1]),
    )
    nonperiodicity_checks = {
        "minimum_normalized_lag_rmse": (
            lag_result["minimum_normalized_lag_rmse"]
            >= float(nonperiodic["minimum_normalized_lag_rmse"])
        ),
        "minimum_normalized_permutation_entropy": (
            entropy
            >= float(nonperiodic["minimum_normalized_permutation_entropy"])
        ),
    }

    return (
        {
            "analysis_window_s": [
                float(post_times[0]),
                float(post_times[-1]),
            ],
            "post_transient_samples": int(post_states.shape[0]),
            "diagnostic_sample_period_s": target_period,
            "diagnostic_samples": int(sampled_states.shape[0]),
            "component_summary": summary,
            "observed_boundedness": {
                "maximum_absolute_state": maximum_absolute_state,
                "threshold": float(bounded["maximum_absolute_state"]),
                "checks": bounded_checks,
                "passed": all(bounded_checks.values()),
            },
            "activity": {
                "active_components": active_components,
                "minimum_required": int(bounded["minimum_active_components"]),
                "checks": activity_checks,
                "passed": all(activity_checks.values()),
            },
            "within_resolution_stability": block_result,
            "nonperiodicity_screen": {
                **lag_result,
                "normalized_permutation_entropy": entropy,
                "permutation_entropy_component": selected_component,
                "permutation_entropy_order": int(
                    nonperiodic["permutation_entropy_order"]
                ),
                "permutation_entropy_delay_samples": entropy_delay,
                "thresholds": {
                    "minimum_normalized_lag_rmse": float(
                        nonperiodic["minimum_normalized_lag_rmse"]
                    ),
                    "minimum_normalized_permutation_entropy": float(
                        nonperiodic[
                            "minimum_normalized_permutation_entropy"
                        ]
                    ),
                },
                "checks": nonperiodicity_checks,
                "passed": all(nonperiodicity_checks.values()),
                "interpretation": (
                    "Observed no-near-period recurrence and complexity screen; "
                    "not a proof of chaos."
                ),
            },
        },
        block_rows,
        sampled_times,
        sampled_states,
    )


def compare_resolutions(
    coarse: dict[str, Any],
    fine: dict[str, Any],
    criteria: dict[str, Any],
) -> dict[str, Any]:
    limits = criteria["cross_resolution_stability"]
    coarse_summary = coarse["component_summary"]
    fine_summary = fine["component_summary"]
    coarse_mean = np.asarray(coarse_summary["mean"])
    fine_mean = np.asarray(fine_summary["mean"])
    coarse_std = np.asarray(coarse_summary["standard_deviation"])
    fine_std = np.asarray(fine_summary["standard_deviation"])
    pooled_std = np.maximum(
        np.sqrt(0.5 * (coarse_std**2 + fine_std**2)),
        1.0e-15,
    )

    mean_difference = np.abs(coarse_mean - fine_mean) / pooled_std
    symmetric_std_ratio = np.maximum(
        coarse_std / np.maximum(fine_std, 1.0e-15),
        fine_std / np.maximum(coarse_std, 1.0e-15),
    )
    coarse_quantiles = np.asarray(coarse_summary["quantiles"])
    fine_quantiles = np.asarray(fine_summary["quantiles"])
    quantile_difference = np.abs(coarse_quantiles - fine_quantiles) / pooled_std
    entropy_difference = abs(
        float(
            coarse["nonperiodicity_screen"][
                "normalized_permutation_entropy"
            ]
        )
        - float(
            fine["nonperiodicity_screen"][
                "normalized_permutation_entropy"
            ]
        )
    )

    maxima = {
        "maximum_mean_difference_in_pooled_std": float(
            np.max(mean_difference)
        ),
        "maximum_symmetric_std_ratio": float(
            np.max(symmetric_std_ratio)
        ),
        "maximum_quantile_difference_in_pooled_std": float(
            np.max(quantile_difference)
        ),
        "permutation_entropy_difference": entropy_difference,
    }
    checks = {
        "mean_difference": (
            maxima["maximum_mean_difference_in_pooled_std"]
            <= float(limits["maximum_mean_difference_in_pooled_std"])
        ),
        "standard_deviation_ratio": (
            maxima["maximum_symmetric_std_ratio"]
            <= float(limits["maximum_symmetric_std_ratio"])
        ),
        "quantile_difference": (
            maxima["maximum_quantile_difference_in_pooled_std"]
            <= float(limits["maximum_quantile_difference_in_pooled_std"])
        ),
        "permutation_entropy_difference": (
            entropy_difference
            <= float(limits["maximum_permutation_entropy_difference"])
        ),
    }
    return {
        **maxima,
        "mean_difference_in_pooled_std_by_component": [
            float(value) for value in mean_difference
        ],
        "symmetric_std_ratio_by_component": [
            float(value) for value in symmetric_std_ratio
        ],
        "thresholds": {
            key: limits[key]
            for key in (
                "maximum_mean_difference_in_pooled_std",
                "maximum_symmetric_std_ratio",
                "maximum_quantile_difference_in_pooled_std",
                "maximum_permutation_entropy_difference",
            )
        },
        "checks": checks,
        "passed": all(checks.values()),
        "interpretation": (
            "Distributional h/2 versus h/4 stability; pointwise trajectory "
            "agreement is intentionally not required at long horizon."
        ),
    }


def resolution_passed(diagnostics: dict[str, Any]) -> bool:
    return all(
        bool(diagnostics[name]["passed"])
        for name in (
            "observed_boundedness",
            "activity",
            "within_resolution_stability",
            "nonperiodicity_screen",
        )
    )


def manifest_decision(
    resolutions: list[dict[str, Any]],
    cross_resolution: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    for row in resolutions:
        divisor = row["resolution_divisor"]
        diagnostics = row["diagnostics"]
        for name in (
            "observed_boundedness",
            "activity",
            "within_resolution_stability",
            "nonperiodicity_screen",
        ):
            if not diagnostics[name]["passed"]:
                reasons.append(f"h/{divisor} failed {name}")
    if not cross_resolution["passed"]:
        reasons.append("h/2 versus h/4 failed cross_resolution_stability")
    if reasons:
        return "not_qualified_dynamic_screen_failed", reasons
    return "qualified_observed_long_horizon_screen", []


def run_qualification(
    *,
    manifest_path: Path,
    criteria_path: Path,
    implementation_report_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    criteria = load_json_object(criteria_path)
    validate_criteria(criteria)
    manifests_payload = load_json_object(manifest_path)
    implementation_report = load_json_object(implementation_report_path)
    prerequisite = validate_implementation_prerequisite(
        implementation_report,
        report_path=implementation_report_path,
        oracle_path=HERE / "abm_oracle.py",
        manifest_path=manifest_path,
        expected_manifest_sha256=criteria["inputs"][
            "candidate_manifest_sha256"
        ],
    )
    if not prerequisite["passed"]:
        raise RuntimeError(
            "ABM implementation-validation prerequisite is stale or failed: "
            + json.dumps(prerequisite["checks"], sort_keys=True)
        )

    expected_ids = criteria["inputs"]["manifest_ids"]
    manifests_by_id = {
        manifest["manifest_id"]: manifest
        for manifest in manifests_payload["manifests"]
    }
    if list(manifests_by_id) != expected_ids:
        raise ValueError(
            "manifest order/identity differs from predeclared criteria"
        )

    started = datetime.now(timezone.utc)
    total_start = time.perf_counter()
    manifest_results: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    block_csv_rows: list[dict[str, Any]] = []
    trajectory_rows: list[dict[str, Any]] = []
    horizon = float(criteria["integration"]["physical_horizon_s"])

    for manifest_id in expected_ids:
        manifest = manifests_by_id[manifest_id]
        rhs = fractional_system_rhs(
            manifest["system"],
            manifest["parameters"],
        )
        resolution_results: list[dict[str, Any]] = []
        for divisor in criteria["integration"]["resolution_divisors"]:
            h = float(manifest["h"]) / int(divisor)
            steps_float = horizon / h
            steps = int(round(steps_float))
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
            result = caputo_abm_full_memory(
                rhs,
                manifest["initial_state"],
                q=float(manifest["q"]),
                h=h,
                steps=steps,
            )
            runtime_s = time.perf_counter() - run_start
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
            trajectory_hash = sha256_states(result.states)
            resolution_row = {
                "resolution_divisor": int(divisor),
                "h": h,
                "steps": steps,
                "rhs_evaluations": result.rhs_evaluations,
                "runtime_s": runtime_s,
                "trajectory_float64_le_sha256": trajectory_hash,
                "diagnostics": diagnostics,
            }
            resolution_results.append(resolution_row)

            summary_rows.append(
                {
                    "manifest_id": manifest_id,
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
                }
            )
            for block in blocks:
                block_csv_rows.append(
                    {
                        "manifest_id": manifest_id,
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
        decision, reasons = manifest_decision(resolution_results, cross)
        for row in summary_rows[-len(resolution_results) :]:
            row["cross_resolution_passed"] = cross["passed"]
            row["manifest_decision"] = decision
        manifest_results.append(
            {
                "manifest_id": manifest_id,
                "system": manifest["system"],
                "manifest": manifest,
                "decision": decision,
                "failure_reasons": reasons,
                "resolutions": resolution_results,
                "cross_resolution_stability": cross,
            }
        )

    all_qualified = all(
        item["decision"] == "qualified_observed_long_horizon_screen"
        for item in manifest_results
    )
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "status": (
            "all_manifests_qualified_observed_long_horizon_screen"
            if all_qualified
            else "one_or_more_manifests_not_qualified"
        ),
        "evidence_layer": "dynamic_manifest_qualification",
        "scope": criteria["scope"],
        "implementation_validation_is_separate": True,
        "implementation_validation_prerequisite": prerequisite,
        "criteria": {
            "source": portable_path(criteria_path),
            "source_sha256": sha256_file(criteria_path),
            "criteria_id": criteria["criteria_id"],
            "status": criteria["status"],
            "decision_rule": criteria["decision_rule"],
        },
        "provenance": {
            "qualification_source": portable_path(Path(__file__)),
            "qualification_source_sha256": sha256_file(Path(__file__)),
            "oracle_source": portable_path(HERE / "abm_oracle.py"),
            "oracle_source_sha256": sha256_file(HERE / "abm_oracle.py"),
            "candidate_manifest_source": portable_path(manifest_path),
            "candidate_manifest_sha256": sha256_file(manifest_path),
        },
        "execution": {
            "started_utc": started.isoformat(),
            "completed_utc": completed.isoformat(),
            "wall_time_s": time.perf_counter() - total_start,
            "python_version": sys.version.split()[0],
            "numpy_version": np.__version__,
            "platform": platform.platform(),
        },
        "criteria_snapshot": criteria,
        "manifests": manifest_results,
        "all_manifests_qualified": all_qualified,
        "does_not_establish": criteria["does_not_establish"],
    }
    return report, summary_rows, block_csv_rows, trajectory_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


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
    return 0 if report["all_manifests_qualified"] else 1


if __name__ == "__main__":
    sys.exit(main())
