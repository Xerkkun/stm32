#!/usr/bin/env python3
"""Analyze the 36 dense STM32 series of the selected systems.

The script validates the physical acquisition artifacts, then applies the
``estimate_time_series_lyapunov`` function from the local
``hidden-attractors-fo`` source tree to two non-overlapping windows of every
series.  It also renders reader-facing time-series and attractor figures from
the STM32 captures.  The figures intentionally contain no internal title; the
manuscript caption supplies their context.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAMPAIGN_ROOT = (
    ROOT
    / "validation"
    / "results"
    / "physical_campaign"
    / "stm32_dense_selected_36cells_v1"
    / "runs"
)
DEFAULT_HIDDEN_SOURCE = (
    ROOT.parents[1] / "Hidden Attractors Fractional Order" / "version_2"
)
DEFAULT_OUTPUT = (
    ROOT / "validation" / "results" / "selected_dense_dynamics_v1"
)
EXPECTED_SYSTEMS = ("chen", "liu", "hammouch_mekkaoui")
EXPECTED_METHODS = ("efork3", "gl", "m2sfrk")
EXPECTED_BOARDS = ("f746", "h755")
EXPECTED_REPRESENTATIONS = ("float32", "fixed_q14_q30")
EXPECTED_FRAMES = 12_000
EXPECTED_FIRST_SEQUENCE = 1
EXPECTED_LAST_SEQUENCE = 12_000
WINDOW_SAMPLES = 4_096
ANALYSIS_SEED = 20_260_731
METHOD_LABELS = {
    "efork3": "EFORK3",
    "gl": "GL",
    "m2sfrk": "M2sFRK",
}
BOARD_LABELS = {"f746": "F746", "h755": "H755"}
REPRESENTATION_LABELS = {
    "float32": "float32",
    "fixed_q14_q30": "Q1.14/Q2.30",
}
LYAPUNOV_PARAMETERS: dict[str, Any] = {
    "rosenstein_emb_dim": 5,
    "rosenstein_lag": 5,
    "rosenstein_min_tsep": 10,
    "rosenstein_min_neighbors": 20,
    "rosenstein_trajectory_len": 28,
    "rosenstein_fit": "poly",
    "rosenstein_fit_offset": 8,
    "eckmann_emb_dim": 5,
    "eckmann_matrix_dim": 3,
    "eckmann_min_neighbors": 8,
    "eckmann_min_tsep": 10,
    "random_seed": ANALYSIS_SEED,
}


class SelectedDenseDynamicsError(RuntimeError):
    """Raised when an acquisition or analysis artifact violates the protocol."""


@dataclass(frozen=True)
class DenseSeries:
    """One validated 12,000-state STM32 acquisition."""

    cell_id: str
    system: str
    method: str
    board: str
    representation: str
    q: float
    sample_interval: float
    transient_steps: int
    run_json: Path
    run_json_sha256: str
    capture_csv: Path
    capture_csv_sha256: str
    capture_bin: Path
    capture_bin_sha256: str
    sequences: np.ndarray
    states: np.ndarray
    state_words: np.ndarray


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    """Return a repository-relative path when possible."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def require_equal(observed: Any, expected: Any, label: str, path: Path) -> None:
    """Require exact equality with an artifact-specific diagnostic."""

    if observed != expected:
        raise SelectedDenseDynamicsError(
            f"{path}: {label}={observed!r}, expected {expected!r}"
        )


def parse_state_word(value: str) -> int:
    """Parse one hexadecimal state word from the decoded FCC1 CSV."""

    return int(value, 16)


def load_dense_series(run_json: Path) -> DenseSeries:
    """Load and validate one dense acquisition and its CSV."""

    payload = json.loads(run_json.read_text(encoding="utf-8"))
    require_equal(payload.get("schema"), "fractional-chaos-physical-run-v1", "schema", run_json)
    require_equal(payload.get("endpoint_name"), "dense_timeseries_pilot", "endpoint_name", run_json)
    require_equal(payload.get("experimental_unit"), "dense_buffered_timeseries_acquisition", "experimental_unit", run_json)

    cell = payload.get("cell", {})
    system = str(cell.get("system"))
    method = str(cell.get("method"))
    board = str(cell.get("board"))
    representation = str(cell.get("representation"))
    if system not in EXPECTED_SYSTEMS:
        raise SelectedDenseDynamicsError(f"{run_json}: unexpected system {system}")
    if method not in EXPECTED_METHODS:
        raise SelectedDenseDynamicsError(f"{run_json}: unexpected method {method}")
    if board not in EXPECTED_BOARDS:
        raise SelectedDenseDynamicsError(f"{run_json}: unexpected board {board}")
    if representation not in EXPECTED_REPRESENTATIONS:
        raise SelectedDenseDynamicsError(
            f"{run_json}: unexpected representation {representation}"
        )

    endpoint = payload.get("endpoint", {})
    for label, expected in (
        ("complete", True),
        ("expected_frames", EXPECTED_FRAMES),
        ("received_frames", EXPECTED_FRAMES),
        ("first_sequence", EXPECTED_FIRST_SEQUENCE),
        ("last_sequence", EXPECTED_LAST_SEQUENCE),
        ("sequence_increment", 1),
        ("output_decimation", 1),
    ):
        require_equal(endpoint.get(label), expected, f"endpoint.{label}", run_json)

    transport = payload.get("transport", {})
    for label, expected in (
        ("accepted", True),
        ("valid_frames", EXPECTED_FRAMES),
        ("matching_frames", EXPECTED_FRAMES),
        ("crc_errors", 0),
        ("sequence_gaps", 0),
        ("nonzero_status_frames", 0),
        ("nonzero_dropped_frames", 0),
        ("identity_mismatches", 0),
        ("invalid_headers", 0),
        ("trailing_bytes", 0),
    ):
        require_equal(transport.get(label), expected, f"transport.{label}", run_json)

    capture = payload.get("capture", {})
    csv_path = run_json.parent / str(capture.get("csv_path"))
    binary_path = run_json.parent / str(capture.get("raw_path"))
    if not csv_path.is_file() or not binary_path.is_file():
        raise SelectedDenseDynamicsError(f"{run_json}: capture artifacts are missing")
    csv_sha256 = sha256_file(csv_path)
    binary_sha256 = sha256_file(binary_path)
    require_equal(csv_sha256, capture.get("csv_sha256"), "capture.csv_sha256", run_json)
    require_equal(binary_sha256, capture.get("raw_sha256"), "capture.raw_sha256", run_json)

    expected_csv_representation = (
        "fixed_q14" if representation == "fixed_q14_q30" else "float32"
    )
    expected_csv_method = "gl_caputo" if method == "gl" else method
    expected_kind = 3 if representation == "fixed_q14_q30" else 1
    sequences: list[int] = []
    states: list[tuple[float, float, float]] = []
    state_words: list[tuple[int, int, int]] = []
    with csv_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        for row_number, row in enumerate(reader, start=2):
            identity = (
                row["system"],
                row["method"],
                row["board"],
                row["representation"],
                int(row["kind"]),
            )
            expected_identity = (
                system,
                expected_csv_method,
                board,
                expected_csv_representation,
                expected_kind,
            )
            if identity != expected_identity:
                raise SelectedDenseDynamicsError(
                    f"{csv_path}:{row_number}: identity={identity!r}, "
                    f"expected {expected_identity!r}"
                )
            if int(row["status"]) != 0 or int(row["dropped"]) != 0:
                raise SelectedDenseDynamicsError(
                    f"{csv_path}:{row_number}: nonzero status or dropped counter"
                )
            sequences.append(int(row["sequence"]))
            states.append((float(row["x"]), float(row["y"]), float(row["z"])))
            state_words.append(
                (
                    parse_state_word(row["x_bits"]),
                    parse_state_word(row["y_bits"]),
                    parse_state_word(row["z_bits"]),
                )
            )

    expected_sequences = list(range(EXPECTED_FIRST_SEQUENCE, EXPECTED_LAST_SEQUENCE + 1))
    if sequences != expected_sequences:
        raise SelectedDenseDynamicsError(
            f"{csv_path}: sequence is not exactly 1..{EXPECTED_LAST_SEQUENCE}"
        )
    state_array = np.asarray(states, dtype=float)
    if state_array.shape != (EXPECTED_FRAMES, 3) or not np.all(np.isfinite(state_array)):
        raise SelectedDenseDynamicsError(f"{csv_path}: invalid state matrix")

    system_contract = payload.get("system_contract", {})
    return DenseSeries(
        cell_id=str(cell["cell_id"]),
        system=system,
        method=method,
        board=board,
        representation=representation,
        q=float(system_contract["q"]),
        sample_interval=float(system_contract["dt_s"]),
        transient_steps=int(system_contract["transient_steps"]),
        run_json=run_json,
        run_json_sha256=sha256_file(run_json),
        capture_csv=csv_path,
        capture_csv_sha256=csv_sha256,
        capture_bin=binary_path,
        capture_bin_sha256=binary_sha256,
        sequences=np.asarray(sequences, dtype=np.uint64),
        states=state_array,
        state_words=np.asarray(state_words, dtype=np.uint32),
    )


def discover_series(campaign_root: Path) -> list[DenseSeries]:
    """Discover the final repetition-2 matrix and enforce its exact product."""

    paths = sorted(campaign_root.glob("*__r02__*__dense-timeseries-pilot/run.json"))
    series = [load_dense_series(path) for path in paths]
    by_cell = {item.cell_id: item for item in series}
    if len(series) != len(by_cell):
        raise SelectedDenseDynamicsError("duplicate cell identities in dense matrix")
    expected = {
        f"{system}_{method}_{board}_{'fixed' if representation == 'fixed_q14_q30' else representation}"
        for system in EXPECTED_SYSTEMS
        for method in EXPECTED_METHODS
        for board in EXPECTED_BOARDS
        for representation in EXPECTED_REPRESENTATIONS
    }
    observed = set(by_cell)
    if observed != expected:
        raise SelectedDenseDynamicsError(
            "dense matrix mismatch; "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )
    return [by_cell[cell_id] for cell_id in sorted(by_cell)]


def git_value(source_root: Path, *args: str) -> str:
    """Read a Git provenance value without mutating the source tree."""

    repository_root = next(
        (
            candidate
            for candidate in (source_root.resolve(), *source_root.resolve().parents)
            if (candidate / ".git").exists()
        ),
        None,
    )
    if repository_root is None:
        raise SelectedDenseDynamicsError(
            f"no Git repository contains {source_root.resolve()}"
        )
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={repository_root}", *args],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def analyze_series(
    series: list[DenseSeries],
    hidden_source: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute two physical-time-series diagnostics for every matrix cell."""

    sys.path.insert(0, str(hidden_source.resolve()))
    from hidden_attractors import estimate_time_series_lyapunov

    module_path = (
        hidden_source / "hidden_attractors" / "analysis" / "time_series_lyapunov.py"
    )
    provenance = {
        "source_root": display_path(hidden_source),
        "git_commit": git_value(hidden_source, "rev-parse", "HEAD"),
        "git_status_porcelain": git_value(hidden_source, "status", "--short"),
        "function": "hidden_attractors.estimate_time_series_lyapunov",
        "implementation_path": display_path(module_path),
        "implementation_sha256": sha256_file(module_path),
        "parameters": dict(LYAPUNOV_PARAMETERS),
        "observable": "x",
        "window_samples": WINDOW_SAMPLES,
        "window_starts_after_transient": [0, WINDOW_SAMPLES],
    }

    rows: list[dict[str, Any]] = []
    for item in series:
        available = EXPECTED_FRAMES - item.transient_steps
        if available < 2 * WINDOW_SAMPLES:
            raise SelectedDenseDynamicsError(
                f"{item.cell_id}: {available} post-transient samples cannot "
                f"provide two {WINDOW_SAMPLES}-sample windows"
            )
        for window_index in (0, 1):
            start = item.transient_steps + window_index * WINDOW_SAMPLES
            stop = start + WINDOW_SAMPLES
            signal = item.states[start:stop, 0]
            result = estimate_time_series_lyapunov(
                signal,
                sample_interval=item.sample_interval,
                time_unit="s",
                observable="x",
                **LYAPUNOV_PARAMETERS,
            )
            spectrum = list(result.spectrum)
            if len(spectrum) != 3:
                raise SelectedDenseDynamicsError(
                    f"{item.cell_id}: expected three exponents, got {len(spectrum)}"
                )
            rows.append(
                {
                    "cell_id": item.cell_id,
                    "system": item.system,
                    "method": item.method,
                    "board": item.board,
                    "representation": item.representation,
                    "q": item.q,
                    "sample_interval_s": item.sample_interval,
                    "window_index": window_index + 1,
                    "window_sequence_first": int(item.sequences[start]),
                    "window_sequence_last": int(item.sequences[stop - 1]),
                    "window_samples": WINDOW_SAMPLES,
                    "rosenstein_largest_s_inv": result.largest_exponent,
                    "rosenstein_fit_r2": result.rosenstein_fit_r2,
                    "lambda_1_s_inv": spectrum[0],
                    "lambda_2_s_inv": spectrum[1],
                    "lambda_3_s_inv": spectrum[2],
                    "spectrum_sum_s_inv": result.spectrum_sum,
                    "kaplan_yorke_dimension": result.kaplan_yorke_dimension,
                    "kaplan_yorke_status": result.kaplan_yorke_status,
                    "largest_sign_agrees_with_spectrum": (
                        result.largest_sign_agrees_with_spectrum
                    ),
                    "backend": result.backend,
                    "backend_version": result.backend_version,
                    "warnings": list(result.warnings),
                    "run_json": display_path(item.run_json),
                    "run_json_sha256": item.run_json_sha256,
                    "capture_csv": display_path(item.capture_csv),
                    "capture_csv_sha256": item.capture_csv_sha256,
                    "capture_bin": display_path(item.capture_bin),
                    "capture_bin_sha256": item.capture_bin_sha256,
                }
            )
    return rows, provenance


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    """Write selected fields from *rows* as UTF-8 CSV."""

    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def plot_hardware_time_series(series: list[DenseSeries], output_dir: Path) -> list[Path]:
    """Plot one reusable Chen x(t) file per method."""

    lookup = {
        (item.system, item.method, item.board, item.representation): item
        for item in series
    }
    styles = {
        ("f746", "float32"): ("#111111", "-"),
        ("f746", "fixed_q14_q30"): ("#0072B2", "--"),
        ("h755", "float32"): ("#D55E00", "-."),
        ("h755", "fixed_q14_q30"): ("#009E73", ":"),
    }
    paths: list[Path] = []
    for method in EXPECTED_METHODS:
        fig, ax = plt.subplots(1, 1, figsize=(7.2, 2.05))
        for board in EXPECTED_BOARDS:
            for representation in EXPECTED_REPRESENTATIONS:
                item = lookup[("chen", method, board, representation)]
                start = item.transient_steps
                stop = start + 1_200
                time_axis = np.arange(stop - start, dtype=float) * item.sample_interval
                color, line_style = styles[(board, representation)]
                label = (
                    f"{BOARD_LABELS[board]} "
                    f"{REPRESENTATION_LABELS[representation]}"
                )
                ax.plot(
                    time_axis,
                    item.states[start:stop, 0],
                    color=color,
                    linestyle=line_style,
                    linewidth=0.8,
                    label=label,
                )
        ax.set_ylabel(r"$x(t)$")
        ax.set_xlabel("Model time (s)")
        ax.grid(True, color="#D9D9D9", linewidth=0.35)
        ax.legend(loc="upper right", frameon=False, fontsize=7, ncol=2)
        fig.tight_layout()
        stem = output_dir / f"chen_hardware_time_series_{method}"
        png_path = stem.with_suffix(".png")
        pdf_path = stem.with_suffix(".pdf")
        fig.savefig(png_path, dpi=300, bbox_inches="tight")
        fig.savefig(pdf_path, bbox_inches="tight")
        plt.close(fig)
        paths.extend((png_path, pdf_path))
    return paths


def plot_hardware_attractors(series: list[DenseSeries], output_dir: Path) -> list[Path]:
    """Plot Chen x-y projections by method and board.

    Each panel overlays the floating-point and fixed-point captures so the
    representation comparison does not require separate narrow panels.
    """

    lookup = {
        (item.system, item.method, item.board, item.representation): item
        for item in series
    }
    styles = {
        "float32": ("#111111", "-", "float32"),
        "fixed_q14_q30": ("#0072B2", "--", "Q1.14/Q2.30"),
    }
    paths: list[Path] = []
    for method in EXPECTED_METHODS:
        for board in EXPECTED_BOARDS:
            fig, ax = plt.subplots(1, 1, figsize=(3.65, 2.55))
            for representation in EXPECTED_REPRESENTATIONS:
                item = lookup[("chen", method, board, representation)]
                state = item.states[item.transient_steps :, :]
                color, line_style, label = styles[representation]
                ax.plot(
                    state[:, 0],
                    state[:, 1],
                    color=color,
                    linestyle=line_style,
                    linewidth=0.32,
                    label=label,
                )
            ax.set_box_aspect(0.70)
            ax.set_aspect("equal", adjustable="datalim")
            ax.set_xlabel(r"$x$")
            ax.set_ylabel(r"$y$")
            ax.grid(True, color="#E5E5E5", linewidth=0.25)
            ax.legend(loc="lower right", frameon=False, fontsize=6.5, ncol=2)
            fig.tight_layout()
            stem = output_dir / f"chen_hardware_attractor_{method}_{board}"
            png_path = stem.with_suffix(".png")
            pdf_path = stem.with_suffix(".pdf")
            fig.savefig(png_path, dpi=300, bbox_inches="tight")
            fig.savefig(pdf_path, bbox_inches="tight")
            plt.close(fig)
            paths.extend((png_path, pdf_path))
    return paths


def plot_dky_comparison(rows: list[dict[str, Any]], output_dir: Path) -> list[Path]:
    """Plot primary-window Kaplan--Yorke values for the full 36-cell matrix."""

    primary = [row for row in rows if row["window_index"] == 1]
    fig, axes = plt.subplots(3, 1, figsize=(7.4, 7.0), sharex=True)
    x_values = np.arange(12)
    lane_order = tuple(
        (method, board, representation)
        for method in EXPECTED_METHODS
        for board in EXPECTED_BOARDS
        for representation in EXPECTED_REPRESENTATIONS
    )
    tick_labels = [
        f"{METHOD_LABELS[method]}\n{BOARD_LABELS[board]}\n"
        f"{REPRESENTATION_LABELS[representation]}"
        for method, board, representation in lane_order
    ]
    for panel, system in enumerate(EXPECTED_SYSTEMS):
        ax = axes[panel]
        by_lane = {
            (row["method"], row["board"], row["representation"]): row
            for row in primary
            if row["system"] == system
        }
        values = [
            by_lane[lane]["kaplan_yorke_dimension"] for lane in lane_order
        ]
        ax.plot(x_values, values, marker="o", color="#0072B2", linewidth=0.9)
        ax.set_ylabel(r"$D_{\mathrm{KY}}$")
        ax.text(
            0.012,
            0.92,
            f"({chr(ord('a') + panel)}) "
            f"{system.replace('_', ' ').title()}",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=8,
        )
        ax.grid(True, color="#D9D9D9", linewidth=0.35)
    axes[-1].set_xticks(x_values, tick_labels, rotation=45, ha="right", fontsize=6.5)
    fig.tight_layout()
    paths = [
        output_dir / "selected_hardware_kaplan_yorke.png",
        output_dir / "selected_hardware_kaplan_yorke.pdf",
    ]
    fig.savefig(paths[0], dpi=300, bbox_inches="tight")
    fig.savefig(paths[1], bbox_inches="tight")
    plt.close(fig)
    return paths


def main() -> int:
    """Command-line entry point."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate and analyze the 36 dense STM32 series for Chen, Liu, "
            "and Hammouch-Mekkaoui."
        )
    )
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    parser.add_argument(
        "--hidden-attractors-source",
        type=Path,
        default=DEFAULT_HIDDEN_SOURCE,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    series = discover_series(args.campaign_root.resolve())
    rows, provenance = analyze_series(series, args.hidden_attractors_source.resolve())
    figure_paths = [
        *plot_hardware_time_series(series, output_dir),
        *plot_hardware_attractors(series, output_dir),
        *plot_dky_comparison(rows, output_dir),
    ]

    csv_fields = [
        "cell_id",
        "system",
        "method",
        "board",
        "representation",
        "q",
        "sample_interval_s",
        "window_index",
        "window_sequence_first",
        "window_sequence_last",
        "window_samples",
        "rosenstein_largest_s_inv",
        "rosenstein_fit_r2",
        "lambda_1_s_inv",
        "lambda_2_s_inv",
        "lambda_3_s_inv",
        "spectrum_sum_s_inv",
        "kaplan_yorke_dimension",
        "kaplan_yorke_status",
        "largest_sign_agrees_with_spectrum",
        "backend",
        "backend_version",
        "run_json_sha256",
        "capture_csv_sha256",
        "capture_bin_sha256",
    ]
    write_csv(output_dir / "lyapunov_windows.csv", rows, csv_fields)
    primary_rows = [row for row in rows if row["window_index"] == 1]
    write_csv(output_dir / "lyapunov_primary_window.csv", primary_rows, csv_fields)

    summary = {
        "schema": "fractional-chaos-selected-dense-dynamics-v1",
        "status": "complete",
        "experimental_unit": "one_lossless_12000_state_stm32_acquisition",
        "matrix": {
            "systems": list(EXPECTED_SYSTEMS),
            "methods": list(EXPECTED_METHODS),
            "boards": list(EXPECTED_BOARDS),
            "representations": list(EXPECTED_REPRESENTATIONS),
            "cells": len(series),
            "states_per_cell": EXPECTED_FRAMES,
            "states_total": len(series) * EXPECTED_FRAMES,
        },
        "analysis": {
            "observable": "x",
            "windows_per_cell": 2,
            "window_samples": WINDOW_SAMPLES,
            "rows": len(rows),
            "scope": (
                "finite-data scalar time-series estimates from consecutive "
                "STM32 states"
            ),
        },
        "hidden_attractors_fo": provenance,
        "cases": [
            {
                "cell_id": item.cell_id,
                "system": item.system,
                "method": item.method,
                "board": item.board,
                "representation": item.representation,
                "q": item.q,
                "sample_interval_s": item.sample_interval,
                "transient_steps": item.transient_steps,
                "run_json": display_path(item.run_json),
                "run_json_sha256": item.run_json_sha256,
                "capture_csv": display_path(item.capture_csv),
                "capture_csv_sha256": item.capture_csv_sha256,
                "capture_bin": display_path(item.capture_bin),
                "capture_bin_sha256": item.capture_bin_sha256,
            }
            for item in series
        ],
        "results": rows,
        "figures": [
            {
                "path": display_path(path),
                "sha256": sha256_file(path),
                "internal_title": False,
                "source": "STM32 dense capture",
            }
            for path in figure_paths
        ],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "cells": len(series),
                "states": len(series) * EXPECTED_FRAMES,
                "lyapunov_rows": len(rows),
                "summary": str(summary_path),
                "figures": [str(path) for path in figure_paths],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
