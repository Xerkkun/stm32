#!/usr/bin/env python3
"""Estimate Lyapunov diagnostics from the four retained UART captures.

The script imports the scalar-time-series API from the sibling
``hidden-attractors-fo`` source tree.  It harmonizes every capture to one
retained sample per 1024 integration steps before comparing boards or numeric
representations.  Results remain exploratory because the Lorenz contract was
not promoted by the frozen ABM qualification and the UART streams are heavily
decimated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HIDDEN_SOURCE = (
    ROOT.parents[1] / "Hidden Attractors Fractional Order" / "version_2"
)
DEFAULT_OUTPUT = (
    ROOT / "validation" / "results" / "uart_time_series_lyapunov"
)
HIDDEN_PACKAGE_NAME = "hidden-attractors-fo"
HIDDEN_PYPI_URL = "https://pypi.org/project/hidden-attractors-fo/"
SYSTEM = "lorenz"
METHOD = "m2sfrk"
OBSERVABLE = "x"
MODEL_STEP = 0.005
TARGET_DECIMATION = 1024
SAMPLE_INTERVAL = MODEL_STEP * TARGET_DECIMATION
DISCARD_RAW_ROWS = 64
WINDOW_SAMPLES = 4096
TIME_UNIT = "model_time"
ANALYSIS_SEED = 20260728
MAX_PAIRWISE_MATRIX_BYTES = 256 * 1024 * 1024

ROSENSTEIN_PARAMETERS = {
    "rosenstein_emb_dim": 10,
    "rosenstein_lag": 1,
    "rosenstein_min_tsep": 10,
    "rosenstein_min_neighbors": 20,
    "rosenstein_trajectory_len": 20,
    "rosenstein_fit": "poly",
    "rosenstein_fit_offset": 1,
}
ECKMANN_PARAMETERS = {
    "eckmann_emb_dim": 9,
    "eckmann_matrix_dim": 3,
    "eckmann_min_neighbors": 6,
    "eckmann_min_tsep": 10,
}
SENSITIVITY_PROTOCOLS = (
    {
        "sensitivity_id": "next_nonoverlapping_window",
        "window_offset_samples": WINDOW_SAMPLES,
        "parameter_overrides": {},
    },
    {
        "sensitivity_id": "lag2_theiler20_primary_window",
        "window_offset_samples": 0,
        "parameter_overrides": {
            "rosenstein_lag": 2,
            "rosenstein_min_tsep": 20,
            "eckmann_min_tsep": 20,
        },
    },
)


@dataclass(frozen=True)
class CaptureCase:
    case_id: str
    board: str
    representation: str
    source: str
    raw_decimation: int
    expected_kind: int


CASES = (
    CaptureCase(
        case_id="f746_float32",
        board="f746",
        representation="float32",
        source=(
            "validation/results/hardware_smoke/"
            "f746_lorenz_m2sfrk_float32_dec512_1mbit.csv"
        ),
        raw_decimation=512,
        expected_kind=1,
    ),
    CaptureCase(
        case_id="f746_fixed_q14_q30",
        board="f746",
        representation="fixed_q14",
        source=(
            "validation/results/hardware_smoke/"
            "f746_lorenz_m2sfrk_fixed_dec512_1mbit_telemetry.csv"
        ),
        raw_decimation=512,
        expected_kind=3,
    ),
    CaptureCase(
        case_id="h755_float32",
        board="h755",
        representation="float32",
        source=(
            "validation/results/hardware_smoke/"
            "h755_lorenz_m2sfrk_float32_dec1024_1mbit.csv"
        ),
        raw_decimation=1024,
        expected_kind=1,
    ),
    CaptureCase(
        case_id="h755_fixed_q14_q30",
        board="h755",
        representation="fixed_q14",
        source=(
            "validation/results/hardware_smoke/"
            "h755_lorenz_m2sfrk_fixed_dec512_1mbit.csv"
        ),
        raw_decimation=512,
        expected_kind=3,
    ),
)


class UartLyapunovError(RuntimeError):
    """Raised when a UART capture violates the frozen analysis contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _require_equal(observed: Any, expected: Any, label: str, path: Path) -> None:
    if observed != expected:
        raise UartLyapunovError(
            f"{path}: {label}={observed!r}, expected {expected!r}"
        )


def _parse_int(row: dict[str, str], key: str, path: Path) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise UartLyapunovError(f"{path}: invalid integer field {key!r}") from exc


def _parse_float(row: dict[str, str], key: str, path: Path) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise UartLyapunovError(f"{path}: invalid float field {key!r}") from exc
    if not math.isfinite(value):
        raise UartLyapunovError(f"{path}: non-finite field {key!r}")
    return value


def load_harmonized_window(
    case: CaptureCase,
    *,
    root: Path = ROOT,
    discard_raw_rows: int = DISCARD_RAW_ROWS,
    window_samples: int = WINDOW_SAMPLES,
    window_offset_samples: int = 0,
) -> dict[str, Any]:
    """Load and validate one deterministic, harmonized UART window."""

    if TARGET_DECIMATION % case.raw_decimation != 0:
        raise UartLyapunovError(
            f"{case.case_id}: target decimation is not an integer multiple"
        )
    stride = TARGET_DECIMATION // case.raw_decimation
    path = root / case.source
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise UartLyapunovError(f"cannot open {path}: {exc}") from exc

    sequences: list[int] = []
    values: list[float] = []
    raw_rows = 0
    with stream:
        reader = csv.DictReader(stream)
        required = {
            "kind",
            "board",
            "system",
            "method",
            "representation",
            "status",
            "dropped",
            "sequence",
            OBSERVABLE,
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise UartLyapunovError(f"{path}: missing required UART columns")

        previous_sequence: int | None = None
        for raw_index, row in enumerate(reader):
            raw_rows += 1
            sequence = _parse_int(row, "sequence", path)
            if previous_sequence is not None:
                _require_equal(
                    sequence - previous_sequence,
                    case.raw_decimation,
                    "sequence delta",
                    path,
                )
            previous_sequence = sequence
            _require_equal(_parse_int(row, "kind", path), case.expected_kind, "kind", path)
            _require_equal(row["board"], case.board, "board", path)
            _require_equal(row["system"], SYSTEM, "system", path)
            _require_equal(row["method"], METHOD, "method", path)
            _require_equal(row["representation"], case.representation, "representation", path)
            _require_equal(_parse_int(row, "status", path), 0, "status", path)
            _require_equal(_parse_int(row, "dropped", path), 0, "dropped", path)

            retained_index = raw_index - discard_raw_rows
            if retained_index < 0 or retained_index % stride != 0:
                continue
            harmonized_index = retained_index // stride
            if harmonized_index < window_offset_samples:
                continue
            if len(values) < window_samples:
                sequences.append(sequence)
                values.append(_parse_float(row, OBSERVABLE, path))

    if len(values) != window_samples:
        raise UartLyapunovError(
            f"{path}: retained {len(values)} samples, expected {window_samples}"
        )
    sequence_array = np.asarray(sequences, dtype=np.int64)
    if not np.all(np.diff(sequence_array) == TARGET_DECIMATION):
        raise UartLyapunovError(f"{path}: harmonized sequence is not uniform")

    return {
        "path": path,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "raw_rows": raw_rows,
        "discard_raw_rows": discard_raw_rows,
        "window_offset_samples": window_offset_samples,
        "harmonization_stride": stride,
        "raw_decimation": case.raw_decimation,
        "target_decimation": TARGET_DECIMATION,
        "sample_interval": SAMPLE_INTERVAL,
        "sequence_first": int(sequence_array[0]),
        "sequence_last": int(sequence_array[-1]),
        "model_time_span": float(
            (sequence_array[-1] - sequence_array[0]) * MODEL_STEP
        ),
        "signal": np.asarray(values, dtype=float),
    }


def load_hidden_api(
    source_root: Path,
) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Import the intended local hidden-attractors-fo time-series API."""

    source_root = source_root.resolve()
    package_root = source_root / "hidden_attractors"
    if not package_root.is_dir():
        raise UartLyapunovError(
            f"Hidden Attractors source not found under {source_root}"
        )
    sys.path.insert(0, str(source_root))
    package = importlib.import_module("hidden_attractors")
    analysis = importlib.import_module(
        "hidden_attractors.analysis.time_series_lyapunov"
    )
    estimator = getattr(analysis, "estimate_time_series_lyapunov", None)
    if not callable(estimator):
        raise UartLyapunovError(
            "Hidden Attractors has no callable time-series Lyapunov estimator"
        )
    module_path = Path(analysis.__file__).resolve()
    if source_root not in module_path.parents:
        raise UartLyapunovError(
            f"loaded unexpected Hidden Attractors module: {module_path}"
        )

    try:
        package_version = version(HIDDEN_PACKAGE_NAME)
    except PackageNotFoundError:
        package_version = getattr(package, "__version__", "unknown")
    try:
        backend_version = version("nolds")
    except PackageNotFoundError as exc:
        raise UartLyapunovError("nolds is required for this analysis") from exc

    repo_root = source_root.parent
    revision = "unknown"
    dirty: bool | None = None
    try:
        revision_result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        revision = revision_result.stdout.strip()
        status_result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "status",
                "--porcelain",
                "--",
                str(module_path.relative_to(repo_root)),
                "version_2/hidden_attractors/integrations/external_tools.py",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        dirty = bool(status_result.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass

    return estimator, {
        "package_name": HIDDEN_PACKAGE_NAME,
        "package_version": package_version,
        "pypi_url": HIDDEN_PYPI_URL,
        "availability_statement": (
            "hidden-attractors-fo is distributed on PyPI; this run uses the "
            "recorded local source revision so the time-series API is exact."
        ),
        "source_root": str(source_root),
        "source_revision": revision,
        "time_series_module": str(module_path),
        "time_series_module_sha256": sha256_file(module_path),
        "time_series_source_dirty": dirty,
        "backend": "nolds",
        "backend_version": backend_version,
        "api_function": (
            "hidden_attractors.analysis.estimate_time_series_lyapunov"
        ),
    }


def analyze_cases(
    estimator: Callable[..., Any],
    *,
    root: Path = ROOT,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_index, case in enumerate(CASES):
        capture = load_harmonized_window(case, root=root)
        primary_result = estimator(
            capture["signal"],
            sample_interval=SAMPLE_INTERVAL,
            time_unit=TIME_UNIT,
            observable=OBSERVABLE,
            **ROSENSTEIN_PARAMETERS,
            **ECKMANN_PARAMETERS,
            random_seed=ANALYSIS_SEED + case_index,
            max_pairwise_matrix_bytes=MAX_PAIRWISE_MATRIX_BYTES,
        )
        payload = primary_result.to_dict()
        sensitivity_runs: list[dict[str, Any]] = []
        for sensitivity_index, protocol in enumerate(SENSITIVITY_PROTOCOLS):
            sensitivity_capture = load_harmonized_window(
                case,
                root=root,
                window_offset_samples=int(protocol["window_offset_samples"]),
            )
            parameters = {
                **ROSENSTEIN_PARAMETERS,
                **ECKMANN_PARAMETERS,
                **protocol["parameter_overrides"],
            }
            sensitivity_result = estimator(
                sensitivity_capture["signal"],
                sample_interval=SAMPLE_INTERVAL,
                time_unit=TIME_UNIT,
                observable=OBSERVABLE,
                **parameters,
                random_seed=(
                    ANALYSIS_SEED + 100 + case_index * 10 + sensitivity_index
                ),
                max_pairwise_matrix_bytes=MAX_PAIRWISE_MATRIX_BYTES,
            )
            sensitivity_runs.append(
                {
                    "sensitivity_id": protocol["sensitivity_id"],
                    "window_offset_samples": protocol["window_offset_samples"],
                    "sequence_first": sensitivity_capture["sequence_first"],
                    "sequence_last": sensitivity_capture["sequence_last"],
                    "parameter_overrides": protocol["parameter_overrides"],
                    **sensitivity_result.to_dict(),
                }
            )

        all_results = [payload, *sensitivity_runs]
        largest_values = [
            float(item["largest_exponent"]) for item in all_results
        ]
        spectrum_values = np.asarray(
            [item["spectrum"] for item in all_results],
            dtype=float,
        )
        dimension_values = [
            float(item["kaplan_yorke_dimension"]) for item in all_results
        ]
        sensitivity_summary = {
            "evaluations": len(all_results),
            "largest_exponent_min": min(largest_values),
            "largest_exponent_max": max(largest_values),
            "largest_exponent_all_positive": all(
                value > 0.0 for value in largest_values
            ),
            "spectrum_component_min": np.min(
                spectrum_values,
                axis=0,
            ).tolist(),
            "spectrum_component_max": np.max(
                spectrum_values,
                axis=0,
            ).tolist(),
            "spectrum_largest_all_positive": bool(
                np.all(spectrum_values[:, 0] > 0.0)
            ),
            "spectrum_sum_all_negative": all(
                float(item["spectrum_sum"]) < 0.0 for item in all_results
            ),
            "kaplan_yorke_min": min(dimension_values),
            "kaplan_yorke_max": max(dimension_values),
            "minimum_rosenstein_fit_r2": min(
                float(item["rosenstein_fit_r2"])
                for item in all_results
                if item["rosenstein_fit_r2"] is not None
            ),
            "interpretation": (
                "limited two-window/parameter sensitivity only; not an "
                "uncertainty interval or asymptotic convergence study"
            ),
        }
        rows.append(
            {
                "case_id": case.case_id,
                "board": case.board,
                "representation": case.representation,
                "system": SYSTEM,
                "integration_method": METHOD,
                "fractional_order_q": 0.995,
                "model_step": MODEL_STEP,
                "observable": OBSERVABLE,
                "source": display_path(capture["path"]),
                "source_sha256": capture["sha256"],
                "source_bytes": capture["bytes"],
                "source_raw_rows": capture["raw_rows"],
                "discard_raw_rows": capture["discard_raw_rows"],
                "harmonization_stride": capture["harmonization_stride"],
                "raw_decimation": capture["raw_decimation"],
                "target_decimation": capture["target_decimation"],
                "sample_interval": capture["sample_interval"],
                "window_samples": WINDOW_SAMPLES,
                "sequence_first": capture["sequence_first"],
                "sequence_last": capture["sequence_last"],
                "model_time_span": capture["model_time_span"],
                **payload,
                "sensitivity_runs": sensitivity_runs,
                "sensitivity_summary": sensitivity_summary,
                "claim_eligible": False,
                "interpretation": (
                    "exploratory UART-derived finite-time diagnostic; Lorenz "
                    "failed the frozen ABM qualification"
                ),
            }
        )
    return rows


def save_summary_csv(rows: Sequence[dict[str, Any]], path: Path) -> None:
    columns = (
        "case_id",
        "board",
        "representation",
        "raw_decimation",
        "harmonization_stride",
        "target_decimation",
        "sample_interval",
        "window_samples",
        "largest_exponent",
        "rosenstein_fit_r2",
        "lambda_1",
        "lambda_2",
        "lambda_3",
        "spectrum_sum",
        "kaplan_yorke_dimension",
        "kaplan_yorke_status",
        "largest_sign_agrees_with_spectrum",
        "source_sha256",
        "claim_eligible",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            spectrum = row["spectrum"]
            writer.writerow(
                {
                    **{key: row[key] for key in columns if key in row},
                    "lambda_1": spectrum[0],
                    "lambda_2": spectrum[1],
                    "lambda_3": spectrum[2],
                }
            )


def save_summary_markdown(
    rows: Sequence[dict[str, Any]],
    software: dict[str, Any],
    path: Path,
) -> None:
    lines = [
        "# UART time-series Lyapunov diagnostic",
        "",
        (
            f"Software: `{software['package_name']} {software['package_version']}` "
            f"from the recorded local revision, with `nolds "
            f"{software['backend_version']}`."
        ),
        "",
        (
            "Numerical integration on the boards: M2sFRK, q=0.995, "
            "h=0.005 model-time units. Estimation: scalar Rosenstein LLE and "
            "Eckmann spectrum; Kaplan--Yorke is derived from the ordered "
            "Eckmann spectrum."
        ),
        "",
        (
            "All lanes are harmonized to decimation 1024 "
            "(sample interval 5.12 model-time units), use x, discard 64 raw "
            "UART rows, and analyze the first 4096 harmonized samples."
        ),
        "",
        "| Board | Representation | LLE Rosenstein | Eckmann spectrum | D_KY | R2 |",
        "|---|---|---:|---|---:|---:|",
    ]
    for row in rows:
        spectrum = ", ".join(f"{value:.8g}" for value in row["spectrum"])
        fit_r2 = row["rosenstein_fit_r2"]
        fit_text = "N/A" if fit_r2 is None else f"{fit_r2:.5f}"
        lines.append(
            f"| {row['board'].upper()} | {row['representation']} | "
            f"{row['largest_exponent']:.8g} | [{spectrum}] | "
            f"{row['kaplan_yorke_dimension']:.6f} | {fit_text} |"
        )
    lines.extend(
        [
            "",
            "## Limited sensitivity",
            "",
            "| Board | Representation | LLE range | D_KY range | minimum R2 |",
            "|---|---|---:|---:|---:|",
            *[
                (
                    f"| {row['board'].upper()} | {row['representation']} | "
                    f"{row['sensitivity_summary']['largest_exponent_min']:.6g}"
                    f"--{row['sensitivity_summary']['largest_exponent_max']:.6g} | "
                    f"{row['sensitivity_summary']['kaplan_yorke_min']:.5f}"
                    f"--{row['sensitivity_summary']['kaplan_yorke_max']:.5f} | "
                    f"{row['sensitivity_summary']['minimum_rosenstein_fit_r2']:.4f} |"
                )
                for row in rows
            ],
            "",
            "## Evidence boundary",
            "",
            "- These are finite-time scalar-reconstruction diagnostics.",
            "- The Eckmann spectrum and Kaplan--Yorke dimension are exploratory.",
            "- Heavy UART decimation can alias the reconstructed dynamics.",
            "- Lorenz did not pass the frozen ABM qualification.",
            "- Formal chaos, randomness, hiddenness, and asymptotic-spectrum claims: 0.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def save_spectrum_figure(rows: Sequence[dict[str, Any]], path: Path) -> None:
    labels = [
        f"{row['board'].upper()}\n"
        f"{'float32' if row['representation'] == 'float32' else 'Q14/Q30'}"
        for row in rows
    ]
    x_positions = np.arange(len(rows), dtype=float)
    spectra = np.asarray([row["spectrum"] for row in rows], dtype=float)
    largest = np.asarray([row["largest_exponent"] for row in rows], dtype=float)
    dimensions = np.asarray(
        [row["kaplan_yorke_dimension"] for row in rows],
        dtype=float,
    )

    fig, axes = plt.subplots(1, 3, figsize=(12.2, 3.8))
    colors = ("#0072B2", "#E69F00", "#009E73")
    width = 0.23
    for exponent_index in range(3):
        axes[0].bar(
            x_positions + (exponent_index - 1) * width,
            spectra[:, exponent_index],
            width=width,
            color=colors[exponent_index],
            label=rf"$\lambda_{exponent_index + 1}$",
        )
    axes[0].axhline(0.0, color="black", linewidth=0.8)
    axes[0].set_ylabel(r"Eckmann estimate [model time$^{-1}$]")
    axes[0].set_xticks(x_positions, labels)
    axes[0].legend(frameon=False, ncols=3, fontsize=8)

    axes[1].bar(x_positions, largest, color="#CC79A7")
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_ylabel(r"Rosenstein LLE [model time$^{-1}$]")
    axes[1].set_xticks(x_positions, labels)

    axes[2].scatter(x_positions, dimensions, color="#D55E00", s=45)
    axes[2].plot(x_positions, dimensions, color="#D55E00", linewidth=0.8)
    axes[2].set_ylabel(r"Exploratory Kaplan--Yorke $D_{KY}$")
    axes[2].set_xticks(x_positions, labels)
    axes[2].set_ylim(0.0, 3.15)

    fig.suptitle(
        "UART-derived scalar time-series diagnostics (M2sFRK Lorenz pilot)"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_divergence_figure(rows: Sequence[dict[str, Any]], path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8.6, 6.4), sharex=True)
    for axis, row in zip(axes.ravel(), rows, strict=True):
        trajectory = np.asarray(
            row["rosenstein_divergence_trajectory"],
            dtype=float,
        )
        fit_offset = int(row["rosenstein_parameters"]["fit_offset"])
        fit = np.polyfit(
            trajectory[fit_offset:, 0],
            trajectory[fit_offset:, 1],
            1,
        )
        axis.plot(
            trajectory[:, 0],
            trajectory[:, 1],
            "o-",
            color="#0072B2",
            markersize=3,
            linewidth=0.8,
            label="mean log divergence",
        )
        axis.plot(
            trajectory[fit_offset:, 0],
            np.polyval(fit, trajectory[fit_offset:, 0]),
            color="#D55E00",
            linewidth=1.2,
            label="linear fit",
        )
        axis.set_title(
            f"{row['board'].upper()} / {row['representation']}\n"
            f"$R^2={row['rosenstein_fit_r2']:.4f}$"
        )
        axis.set_xlabel("future retained-sample index k")
        axis.set_ylabel("mean log distance")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncols=2, frameon=False)
    fig.suptitle("Rosenstein finite-window fit diagnostics", y=1.01)
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze harmonized UART time series with Hidden Attractors FO"
    )
    parser.add_argument(
        "--hidden-attractors-source",
        type=Path,
        default=DEFAULT_HIDDEN_SOURCE,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    estimator, software = load_hidden_api(args.hidden_attractors_source)
    rows = analyze_cases(estimator)

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "stm32-uart-time-series-lyapunov-v1",
        "analysis_status": "completed_exploratory_diagnostic",
        "software": software,
        "numerical_contract": {
            "embedded_integrator": "M2sFRK",
            "system": SYSTEM,
            "fractional_order_q": 0.995,
            "model_step": MODEL_STEP,
            "state_representations": ["float32", "fixed_q14_q30"],
            "time_series_estimators": [
                "Rosenstein largest Lyapunov exponent",
                "Eckmann scalar-reconstruction spectrum",
            ],
            "kaplan_yorke_source": "ordered exploratory Eckmann spectrum",
        },
        "sampling_contract": {
            "observable": OBSERVABLE,
            "discard_raw_rows": DISCARD_RAW_ROWS,
            "window_samples": WINDOW_SAMPLES,
            "target_decimation": TARGET_DECIMATION,
            "sample_interval": SAMPLE_INTERVAL,
            "time_unit": TIME_UNIT,
            "harmonization": (
                "dec512 lanes retain every second post-discard row; "
                "the dec1024 lane retains every row"
            ),
        },
        "estimator_contract": {
            **ROSENSTEIN_PARAMETERS,
            **ECKMANN_PARAMETERS,
            "random_seed_base": ANALYSIS_SEED,
            "max_pairwise_matrix_bytes": MAX_PAIRWISE_MATRIX_BYTES,
            "sensitivity_protocols": SENSITIVITY_PROTOCOLS,
            "sensitivity_scope": (
                "primary window, next non-overlapping window, and one "
                "lag/Theiler perturbation; not a formal convergence study"
            ),
        },
        "scientific_claims": {
            "formal_chaos_claims": 0,
            "asymptotic_lyapunov_spectrum_claims": 0,
            "primary_kaplan_yorke_claims": 0,
            "hiddenness_claims": 0,
            "cryptographic_claims": 0,
            "reason": (
                "Lorenz failed the frozen ABM qualification and the retained "
                "UART streams are heavily decimated scalar observations."
            ),
        },
        "cases": rows,
    }

    summary_json = output / "summary.json"
    summary_csv = output / "summary.csv"
    summary_md = output / "summary.md"
    spectrum_figure = output / "uart_lyapunov_spectra_kaplan_yorke.png"
    divergence_figure = output / "rosenstein_divergence_fits.png"
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    save_summary_csv(rows, summary_csv)
    save_summary_markdown(rows, software, summary_md)
    save_spectrum_figure(rows, spectrum_figure)
    save_divergence_figure(rows, divergence_figure)

    print(f"Wrote {summary_json}")
    for row in rows:
        spectrum = ", ".join(f"{value:.8g}" for value in row["spectrum"])
        print(
            f"{row['case_id']}: LLE={row['largest_exponent']:.8g}, "
            f"spectrum=[{spectrum}], "
            f"D_KY={row['kaplan_yorke_dimension']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
