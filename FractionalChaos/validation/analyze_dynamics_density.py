#!/usr/bin/env python3
"""Quantify observed dynamic density in an accepted dense UART capture.

The report is a deterministic descriptive screen.  It does not establish
chaos, a positive Lyapunov exponent, hiddenness, or randomness.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from analyze_capture import (
    VARIABLES,
    load_capture,
    sha256,
    summarize_status_flags,
)
from long_horizon_qualification import (
    lag_recurrence_screen,
    normalized_permutation_entropy,
)


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
DIAGNOSTIC_SAMPLE_PERIOD_S = 0.05
PERMUTATION_ENTROPY_DELAY_S = 0.1
PERMUTATION_ENTROPY_ORDER = 5
RECURRENCE_LAG_MIN_S = 1.0
RECURRENCE_LAG_MAX_S = 15.0
STANDARDIZED_CLIP = 4.0
TWO_DIMENSIONAL_BINS = 64
THREE_DIMENSIONAL_BINS = 32
PROJECTIONS = (("x", "y"), ("x", "z"), ("y", "z"))


def portable_path(path: Path) -> str:
    """Represent project files without embedding a workstation path."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def exact_stride(
    target_interval: float,
    source_interval: float,
    *,
    label: str,
) -> int:
    """Return an integer stride or reject an inexact sampling contract."""

    ratio = target_interval / source_interval
    stride = int(round(ratio))
    if stride < 1 or not math.isclose(
        stride * source_interval,
        target_interval,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise ValueError(
            f"{label}={target_interval:g} debe ser un múltiplo entero de "
            f"sample_interval={source_interval:g}"
        )
    return stride


def validate_dense_capture(
    arrays: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Reject transport gaps, solver flags, dropped rows, or invalid states."""

    sequences = arrays["sequence"].astype(np.int64, copy=False)
    sequence_deltas = np.diff(sequences)
    gap_indices = np.flatnonzero(sequence_deltas != 1)
    status = summarize_status_flags(arrays["status"])
    nonzero_dropped = int(np.count_nonzero(arrays["dropped"]))
    states = np.column_stack([arrays[name] for name in VARIABLES])
    all_finite = bool(np.all(np.isfinite(states)))

    if gap_indices.size:
        first = int(gap_indices[0])
        raise ValueError(
            "la captura contiene gaps o repeticiones de secuencia: "
            f"{int(sequences[first])}->{int(sequences[first + 1])}"
        )
    if nonzero_dropped:
        raise ValueError(
            "la captura contiene filas con dropped distinto de cero"
        )
    if not bool(status["all_samples_ok"]):
        raise ValueError(
            "la captura contiene estados del solver distintos de cero"
        )
    if not all_finite:
        raise ValueError("la captura contiene estados no finitos")

    return {
        "accepted": True,
        "required_sequence_increment": 1,
        "sequence_gap_count": 0,
        "sequence_first": int(sequences[0]),
        "sequence_last": int(sequences[-1]),
        "nonzero_dropped_rows": nonzero_dropped,
        "solver_status": status,
        "all_states_finite": all_finite,
    }


def component_summary(states: np.ndarray) -> dict[str, dict[str, float]]:
    """Return deterministic first-order descriptors for x, y, and z."""

    summaries: dict[str, dict[str, float]] = {}
    for index, name in enumerate(VARIABLES):
        values = states[:, index]
        summaries[name] = {
            "minimum": float(np.min(values)),
            "maximum": float(np.max(values)),
            "peak_to_peak": float(np.ptp(values)),
            "mean": float(np.mean(values)),
            "standard_deviation": float(np.std(values)),
        }
    return summaries


def standardized_states(
    states: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply componentwise z-score and a fixed symmetric clip."""

    means = np.mean(states, axis=0)
    deviations = np.std(states, axis=0)
    scales = np.where(deviations > 0.0, deviations, 1.0)
    standardized = np.clip(
        (states - means) / scales,
        -STANDARDIZED_CLIP,
        STANDARDIZED_CLIP,
    )
    return standardized, means, deviations


def normalized_histogram_entropy(counts: np.ndarray) -> float:
    """Normalize Shannon entropy by the number of available bins."""

    occupied = counts[counts > 0.0]
    if occupied.size == 0:
        return 0.0
    probabilities = occupied / np.sum(occupied)
    entropy = -float(np.sum(probabilities * np.log(probabilities)))
    maximum = math.log(int(counts.size))
    return entropy / maximum if maximum > 0.0 else 0.0


def density_metrics(standardized: np.ndarray) -> dict[str, Any]:
    """Measure fixed-grid 2D and 3D occupancy after standardization."""

    fixed_range = (
        (-STANDARDIZED_CLIP, STANDARDIZED_CLIP),
        (-STANDARDIZED_CLIP, STANDARDIZED_CLIP),
    )
    projections: dict[str, dict[str, float | int]] = {}
    occupancy_values: list[float] = []
    entropy_values: list[float] = []
    name_to_index = {name: index for index, name in enumerate(VARIABLES)}

    for horizontal, vertical in PROJECTIONS:
        counts, _, _ = np.histogram2d(
            standardized[:, name_to_index[horizontal]],
            standardized[:, name_to_index[vertical]],
            bins=TWO_DIMENSIONAL_BINS,
            range=fixed_range,
        )
        occupied_bins = int(np.count_nonzero(counts))
        total_bins = int(counts.size)
        occupancy = occupied_bins / total_bins
        entropy = normalized_histogram_entropy(counts)
        projections[f"{horizontal}{vertical}"] = {
            "occupied_bins": occupied_bins,
            "total_bins": total_bins,
            "occupancy_fraction": occupancy,
            "normalized_histogram_entropy": entropy,
        }
        occupancy_values.append(occupancy)
        entropy_values.append(entropy)

    counts_3d, _ = np.histogramdd(
        standardized,
        bins=(THREE_DIMENSIONAL_BINS,) * 3,
        range=(
            (-STANDARDIZED_CLIP, STANDARDIZED_CLIP),
        )
        * 3,
    )
    occupied_3d = int(np.count_nonzero(counts_3d))
    total_3d = int(counts_3d.size)
    sample_limited_maximum = min(int(standardized.shape[0]), total_3d)

    return {
        "standardization": {
            "method": "componentwise_z_score",
            "clip": [-STANDARDIZED_CLIP, STANDARDIZED_CLIP],
        },
        "two_dimensional": {
            "bins_per_axis": TWO_DIMENSIONAL_BINS,
            "projections": projections,
            "mean_occupancy_fraction": float(np.mean(occupancy_values)),
            "mean_normalized_histogram_entropy": float(
                np.mean(entropy_values)
            ),
            "entropy_definition": (
                "Shannon entropy with natural logarithm, normalized by "
                "log(64^2)"
            ),
        },
        "three_dimensional": {
            "bins_per_axis": THREE_DIMENSIONAL_BINS,
            "occupied_bins": occupied_3d,
            "total_bins": total_3d,
            "occupancy_fraction": occupied_3d / total_3d,
            "sample_limited_occupancy_efficiency": (
                occupied_3d / sample_limited_maximum
            ),
        },
    }


def radial_peak_metrics(
    states: np.ndarray,
    sequences: np.ndarray,
    *,
    sample_interval: float,
) -> dict[str, Any]:
    """Return interior local maxima of r=sqrt(x^2+y^2)."""

    radii = np.hypot(states[:, 0], states[:, 1])
    peak_indices = (
        np.flatnonzero(
            (radii[1:-1] > radii[:-2])
            & (radii[1:-1] >= radii[2:])
        )
        + 1
    )
    amplitudes = radii[peak_indices]
    if amplitudes.size:
        summary: dict[str, float] | None = {
            "minimum": float(np.min(amplitudes)),
            "maximum": float(np.max(amplitudes)),
            "mean": float(np.mean(amplitudes)),
            "standard_deviation": float(np.std(amplitudes)),
        }
    else:
        summary = None

    return {
        "definition": "interior local maxima of sqrt(x^2+y^2)",
        "plateau_policy": "strictly_greater_left_and_greater_or_equal_right",
        "count": int(peak_indices.size),
        "retained_indices": [
            int(index) for index in peak_indices.tolist()
        ],
        "sequences": [
            int(value) for value in sequences[peak_indices].tolist()
        ],
        "model_times_from_first_retained_state_s": [
            float(round(index * sample_interval, 12))
            for index in peak_indices.tolist()
        ],
        "amplitudes": [float(value) for value in amplitudes.tolist()],
        "amplitude_summary": summary,
    }


def analyze_dynamics_density(
    capture_path: Path,
    *,
    discard: int,
    sample_interval: float,
) -> dict[str, Any]:
    """Analyze one accepted capture without altering its samples."""

    if discard < 0:
        raise ValueError("discard no puede ser negativo")
    if not math.isfinite(sample_interval) or sample_interval <= 0.0:
        raise ValueError("sample_interval debe ser positivo y finito")

    arrays, identity = load_capture(capture_path)
    total_rows = int(arrays["sequence"].size)
    if discard >= total_rows:
        raise ValueError("discard elimina todas las filas")
    transport = validate_dense_capture(arrays)

    retained = {
        name: values[discard:] for name, values in arrays.items()
    }
    states = np.column_stack([retained[name] for name in VARIABLES])
    sequences = retained["sequence"].astype(np.int64, copy=False)
    diagnostic_stride = exact_stride(
        DIAGNOSTIC_SAMPLE_PERIOD_S,
        sample_interval,
        label="diagnostic_sample_period",
    )
    entropy_delay = exact_stride(
        PERMUTATION_ENTROPY_DELAY_S,
        DIAGNOSTIC_SAMPLE_PERIOD_S,
        label="permutation_entropy_delay",
    )
    diagnostic_states = states[::diagnostic_stride]
    diagnostic_span = (
        (diagnostic_states.shape[0] - 1)
        * DIAGNOSTIC_SAMPLE_PERIOD_S
    )
    if diagnostic_span + 1.0e-12 < RECURRENCE_LAG_MAX_S:
        raise ValueError(
            "la ventana retenida no cubre los 15 s exigidos para recurrencia"
        )

    summaries = component_summary(states)
    deviations = np.asarray(
        [
            summaries[name]["standard_deviation"]
            for name in VARIABLES
        ],
        dtype=np.float64,
    )
    selected_component = int(np.argmax(deviations))
    permutation_entropy = normalized_permutation_entropy(
        diagnostic_states[:, selected_component],
        order=PERMUTATION_ENTROPY_ORDER,
        delay=entropy_delay,
    )
    recurrence = lag_recurrence_screen(
        diagnostic_states,
        sample_period=DIAGNOSTIC_SAMPLE_PERIOD_S,
        lag_min_s=RECURRENCE_LAG_MIN_S,
        lag_max_s=RECURRENCE_LAG_MAX_S,
    )
    standardized, means, standard_deviations = standardized_states(states)

    return {
        "schema": "fractional-chaos-dynamics-density-v1",
        "source": {
            "capture": portable_path(capture_path),
            "capture_sha256": sha256(capture_path),
        },
        "identity": identity,
        "rows": {
            "total": total_rows,
            "discarded": discard,
            "retained": int(states.shape[0]),
        },
        "sampling": {
            "sample_interval_s": sample_interval,
            "time_origin_sequence": int(sequences[0]),
            "retained_sequence_first": int(sequences[0]),
            "retained_sequence_last": int(sequences[-1]),
            "host_reception_time_used": False,
            "interpolation": False,
            "resampling": False,
            "diagnostic_sample_period_s": (
                DIAGNOSTIC_SAMPLE_PERIOD_S
            ),
            "diagnostic_stride": diagnostic_stride,
            "diagnostic_samples": int(diagnostic_states.shape[0]),
        },
        "transport_and_state_validation": transport,
        "state_summary": {
            "all_finite": True,
            "components": summaries,
            "z_score_means": [
                float(value) for value in means.tolist()
            ],
            "z_score_standard_deviations": [
                float(value)
                for value in standard_deviations.tolist()
            ],
            "zero_variance_components": [
                VARIABLES[index]
                for index, value in enumerate(standard_deviations)
                if value == 0.0
            ],
        },
        "density": density_metrics(standardized),
        "temporal_complexity": {
            "permutation_entropy": {
                "method": "Bandt-Pompe",
                "normalized_value": permutation_entropy,
                "component": VARIABLES[selected_component],
                "order": PERMUTATION_ENTROPY_ORDER,
                "delay_s": PERMUTATION_ENTROPY_DELAY_S,
                "delay_diagnostic_samples": entropy_delay,
            },
            "lag_recurrence": recurrence,
        },
        "radial_peaks": radial_peak_metrics(
            states,
            sequences,
            sample_interval=sample_interval,
        ),
        "interpretation": {
            "evidence_layer": "observed_descriptive_dynamics_screen",
            "statement": (
                "The metrics describe the retained UART state series under "
                "the declared sampling contract."
            ),
            "does_not_establish": [
                "chaos",
                "a positive Lyapunov exponent",
                "hidden-attractor status",
                "randomness",
            ],
        },
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write canonical, repeatable JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discard", type=int, required=True)
    parser.add_argument(
        "--sample-interval",
        type=float,
        required=True,
        help="intervalo de tiempo del modelo entre filas consecutivas",
    )
    args = parser.parse_args()

    try:
        report = analyze_dynamics_density(
            args.capture.resolve(),
            discard=args.discard,
            sample_interval=args.sample_interval,
        )
    except (OSError, KeyError, ValueError) as error:
        parser.error(str(error))

    output = args.output.resolve()
    write_report(output, report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
