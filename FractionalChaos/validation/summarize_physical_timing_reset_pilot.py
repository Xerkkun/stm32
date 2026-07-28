#!/usr/bin/env python3
"""Aggregate the accepted Chen ST-LINK hardware-reset timing pilots.

This script deliberately reads only ``run.json`` and ``timing_cycles.csv``
from the physical campaign.  It summarizes one accepted hardware-reset
repetition per Chen cell; it does not turn those pilots into power-cycle
evidence or into the primary benchmark.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ID = "stm32_primary_36x30_v1"
DEFAULT_CAMPAIGN_ROOT = (
    ROOT
    / "validation"
    / "results"
    / "physical_campaign"
    / CAMPAIGN_ID
)
DEFAULT_OUTPUT = (
    ROOT
    / "validation"
    / "results"
    / "physical_timing_reset_pilot_chen"
)
SYSTEM = "chen"
METHODS = ("efork3", "gl", "m2sfrk")
BOARDS = ("f746", "h755")
REPRESENTATIONS = ("float32", "fixed_q14_q30")
EXPECTED_CYCLE_VALUES = 10_000
EXPECTED_BLOCKS = 2_500
UINT32_MAX = (1 << 32) - 1

METHOD_CONTRACTS = {
    "efork3": {"wire_id": 0, "wire_name": "efork3"},
    "gl": {"wire_id": 1, "wire_name": "gl_caputo"},
    "m2sfrk": {"wire_id": 2, "wire_name": "m2sfrk"},
}
REPRESENTATION_CONTRACTS = {
    "float32": {
        "decoder_name": "float32",
        "flash_name": "float32",
        "target_suffix": "",
        "wire_kind": 1,
    },
    "fixed_q14_q30": {
        "decoder_name": "fixed_q14",
        "flash_name": "fixed",
        "target_suffix": "_fixed",
        "wire_kind": 3,
    },
}
BOARD_IDENTITIES = {
    "f746": {
        "board_name": "NUCLEO-F746ZG",
        "mcu": "STM32F746ZG",
        "port": "COM7",
        "probe_serial": "066AFF504955657867165348",
    },
    "h755": {
        "board_name": "NUCLEO-H755ZI-Q",
        "mcu": "STM32H755ZI",
        "port": "COM6",
        "probe_serial": "003700344142501220353451",
    },
}


class TimingSummaryError(RuntimeError):
    """Raised when reset-pilot evidence violates the frozen contract."""


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


def expected_cells() -> Iterable[tuple[str, str, str]]:
    """Yield the frozen method/board/representation matrix."""

    for method in METHODS:
        for board in BOARDS:
            for representation in REPRESENTATIONS:
                yield method, board, representation


def expected_reset_repetition(
    method: str,
    board: str,
    representation: str,
) -> int:
    """Return the single accepted repetition in the frozen pilot snapshot."""

    if (method, board, representation) == ("m2sfrk", "h755", "float32"):
        return 2
    return 1


def source_cell_id(
    method: str,
    board: str,
    representation: str,
) -> str:
    suffix = "float32" if representation == "float32" else "fixed"
    return f"{SYSTEM}_{method}_{board}_{suffix}"


def build_target(
    method: str,
    board: str,
    representation: str,
) -> str:
    prefix = "f746" if board == "f746" else "h755_m7"
    suffix = "_fixed" if representation == "fixed_q14_q30" else ""
    return f"{prefix}_{SYSTEM}_{method}{suffix}"


def require_equal(
    observed: Any,
    expected: Any,
    label: str,
    run_path: Path,
) -> None:
    if observed != expected:
        raise TimingSummaryError(
            f"{run_path}: {label}={observed!r}, se esperaba {expected!r}"
        )


def require_zero(
    payload: dict[str, Any],
    keys: Sequence[str],
    label: str,
    run_path: Path,
) -> None:
    for key in keys:
        require_equal(payload.get(key), 0, f"{label}.{key}", run_path)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TimingSummaryError(f"no se pudo leer {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TimingSummaryError(f"{path}: la raíz JSON debe ser un objeto")
    return payload


def is_accepted_chen_reset_pilot(payload: dict[str, Any]) -> bool:
    cell = payload.get("cell")
    endpoint = payload.get("endpoint")
    timing = payload.get("timing")
    transport = payload.get("transport")
    return (
        isinstance(cell, dict)
        and cell.get("system") == SYSTEM
        and payload.get("endpoint_name") == "benchmark_reset_pilot"
        and isinstance(endpoint, dict)
        and endpoint.get("complete") is True
        and isinstance(timing, dict)
        and timing.get("accepted_as_solver_timing") is True
        and isinstance(transport, dict)
        and transport.get("accepted") is True
    )


def validate_run_identity(
    payload: dict[str, Any],
    run_path: Path,
) -> tuple[str, str, str]:
    """Validate all identities and evidence-boundary fields in one run."""

    cell = payload.get("cell")
    endpoint = payload.get("endpoint")
    timing = payload.get("timing")
    transport = payload.get("transport")
    reset = payload.get("reset")
    hardware = payload.get("hardware")
    method_contract = payload.get("method_contract")
    representation_contract = payload.get("representation_contract")
    system_contract = payload.get("system_contract")
    build = payload.get("build")
    capture = payload.get("capture")
    for label, value in (
        ("cell", cell),
        ("endpoint", endpoint),
        ("timing", timing),
        ("transport", transport),
        ("reset", reset),
        ("hardware", hardware),
        ("method_contract", method_contract),
        ("representation_contract", representation_contract),
        ("system_contract", system_contract),
        ("build", build),
        ("capture", capture),
    ):
        if not isinstance(value, dict):
            raise TimingSummaryError(f"{run_path}: falta el objeto {label}")

    method = str(cell.get("method"))
    board = str(cell.get("board"))
    representation = str(cell.get("representation"))
    identity = (method, board, representation)
    if identity not in set(expected_cells()):
        raise TimingSummaryError(
            f"{run_path}: identidad Chen fuera de la matriz: {identity}"
        )

    require_equal(payload.get("schema"), "fractional-chaos-physical-run-v1",
                  "schema", run_path)
    require_equal(payload.get("campaign_id"), CAMPAIGN_ID, "campaign_id",
                  run_path)
    require_equal(cell.get("system"), SYSTEM, "cell.system", run_path)
    require_equal(
        cell.get("cell_id"),
        source_cell_id(method, board, representation),
        "cell.cell_id",
        run_path,
    )
    repetition = expected_reset_repetition(method, board, representation)
    require_equal(
        cell.get("cold_start_id"),
        repetition,
        "cell.cold_start_id",
        run_path,
    )
    require_equal(
        reset.get("hardware_reset_repetition_id"),
        repetition,
        "reset.hardware_reset_repetition_id",
        run_path,
    )

    run_id = payload.get("run_id")
    require_equal(run_id, run_path.parent.name, "run_id", run_path)
    expected_pattern = re.compile(
        rf"^{re.escape(CAMPAIGN_ID)}__r{repetition:02d}__"
        rf"{re.escape(source_cell_id(method, board, representation))}__"
        rf"[0-9a-f]{{10}}__benchmark-reset-pilot$"
    )
    if not isinstance(run_id, str) or expected_pattern.fullmatch(run_id) is None:
        raise TimingSummaryError(f"{run_path}: run_id no cumple el contrato")
    require_equal(
        payload.get("scheduled_primary_run_id"),
        run_id.removesuffix("__benchmark-reset-pilot"),
        "scheduled_primary_run_id",
        run_path,
    )

    require_equal(payload.get("endpoint_name"), "benchmark_reset_pilot",
                  "endpoint_name", run_path)
    require_equal(endpoint.get("complete"), True, "endpoint.complete",
                  run_path)
    require_equal(endpoint.get("frame_kind"), 4, "endpoint.frame_kind",
                  run_path)
    require_equal(endpoint.get("values_per_frame"), 4,
                  "endpoint.values_per_frame", run_path)
    require_equal(endpoint.get("expected_blocks"), EXPECTED_BLOCKS,
                  "endpoint.expected_blocks", run_path)
    require_equal(endpoint.get("received_blocks"), EXPECTED_BLOCKS,
                  "endpoint.received_blocks", run_path)
    require_equal(endpoint.get("expected_first_sequence"), 0,
                  "endpoint.expected_first_sequence", run_path)
    require_equal(endpoint.get("first_sequence"), 0,
                  "endpoint.first_sequence", run_path)
    require_equal(endpoint.get("expected_last_sequence"), 9996,
                  "endpoint.expected_last_sequence", run_path)
    require_equal(endpoint.get("last_sequence"), 9996,
                  "endpoint.last_sequence", run_path)
    require_equal(
        endpoint.get("rule"),
        "complete_kind4_block_set_not_duration",
        "endpoint.rule",
        run_path,
    )

    require_equal(timing.get("accepted_as_solver_timing"), True,
                  "timing.accepted_as_solver_timing", run_path)
    require_equal(timing.get("received_cycle_values"),
                  EXPECTED_CYCLE_VALUES,
                  "timing.received_cycle_values", run_path)
    require_equal(timing.get("required_cycle_values"),
                  EXPECTED_CYCLE_VALUES,
                  "timing.required_cycle_values", run_path)
    require_equal(timing.get("all_cycles_nonzero"), True,
                  "timing.all_cycles_nonzero", run_path)
    require_equal(timing.get("solver_only_window"), True,
                  "timing.solver_only_window", run_path)
    require_equal(timing.get("uart_active_during_timed_window"), False,
                  "timing.uart_active_during_timed_window", run_path)
    require_equal(timing.get("warmup_steps"), 2000,
                  "timing.warmup_steps", run_path)

    require_equal(transport.get("accepted"), True, "transport.accepted",
                  run_path)
    require_equal(transport.get("valid_frames"), EXPECTED_BLOCKS,
                  "transport.valid_frames", run_path)
    require_equal(transport.get("matching_frames"), EXPECTED_BLOCKS,
                  "transport.matching_frames", run_path)
    require_zero(
        transport,
        (
            "crc_errors",
            "identity_mismatches",
            "invalid_headers",
            "noise_bytes",
            "nonzero_dropped_frames",
            "nonzero_status_frames",
            "trailing_bytes",
        ),
        "transport",
        run_path,
    )

    require_equal(
        payload.get("evidence_level"),
        "solver_only_timing_under_stlink_hardware_reset",
        "evidence_level",
        run_path,
    )
    require_equal(
        payload.get("experimental_unit"),
        "stlink_hardware_reset_repetition",
        "experimental_unit",
        run_path,
    )
    require_equal(payload.get("eligible_as_paper_cold_start"), False,
                  "eligible_as_paper_cold_start", run_path)
    require_equal(payload.get("eligible_as_primary_benchmark"), False,
                  "eligible_as_primary_benchmark", run_path)
    require_equal(reset.get("mode"), "stlink_hardware_reset", "reset.mode",
                  run_path)
    require_equal(reset.get("power_removed"), False, "reset.power_removed",
                  run_path)
    require_equal(reset.get("eligible_as_paper_cold_start"), False,
                  "reset.eligible_as_paper_cold_start", run_path)
    require_equal(hardware.get("reset_mode"), "stlink_hardware_reset",
                  "hardware.reset_mode", run_path)
    require_equal(hardware.get("power_removed"), False,
                  "hardware.power_removed", run_path)
    for key, expected in BOARD_IDENTITIES[board].items():
        require_equal(hardware.get(key), expected, f"hardware.{key}",
                      run_path)
    require_equal(hardware.get("baud"), 921600, "hardware.baud", run_path)

    for key, expected in METHOD_CONTRACTS[method].items():
        require_equal(method_contract.get(key), expected,
                      f"method_contract.{key}", run_path)
    for key, expected in REPRESENTATION_CONTRACTS[representation].items():
        require_equal(representation_contract.get(key), expected,
                      f"representation_contract.{key}", run_path)
    require_equal(system_contract.get("manifest_id"), "chen_caputo_v1",
                  "system_contract.manifest_id", run_path)
    require_equal(system_contract.get("wire_id"), 2,
                  "system_contract.wire_id", run_path)

    require_equal(build.get("benchmark_mode"), True, "build.benchmark_mode",
                  run_path)
    require_equal(build.get("target"),
                  build_target(method, board, representation),
                  "build.target", run_path)
    require_equal(capture.get("timing_cycles_path"), "timing_cycles.csv",
                  "capture.timing_cycles_path", run_path)
    timing_hash = capture.get("timing_cycles_sha256")
    if (
        not isinstance(timing_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", timing_hash) is None
    ):
        raise TimingSummaryError(
            f"{run_path}: capture.timing_cycles_sha256 no es SHA-256"
        )
    return identity


def select_accepted_runs(
    campaign_root: Path,
) -> dict[tuple[str, str, str], tuple[Path, dict[str, Any]]]:
    """Select exactly one accepted run for every frozen Chen cell."""

    run_root = campaign_root.resolve() / "runs"
    if not run_root.is_dir():
        raise TimingSummaryError(f"no existe el directorio de runs: {run_root}")
    selected: dict[
        tuple[str, str, str],
        tuple[Path, dict[str, Any]],
    ] = {}
    for run_path in sorted(run_root.glob("*/run.json")):
        payload = load_json_object(run_path)
        if not is_accepted_chen_reset_pilot(payload):
            continue
        identity = validate_run_identity(payload, run_path)
        if identity in selected:
            previous = selected[identity][0]
            raise TimingSummaryError(
                f"más de una repetición aceptada para {identity}: "
                f"{previous} y {run_path}"
            )
        selected[identity] = (run_path, payload)

    expected = set(expected_cells())
    missing = sorted(expected - set(selected))
    extra = sorted(set(selected) - expected)
    if missing or extra or len(selected) != 12:
        raise TimingSummaryError(
            f"matriz aceptada incompleta: missing={missing}, extra={extra}, "
            f"selected={len(selected)}"
        )
    return selected


def read_timing_cycles(path: Path, expected_sha256: str) -> list[int]:
    """Read, hash, and validate exactly 10,000 indexed uint32 cycle values."""

    if not path.is_file():
        raise TimingSummaryError(f"falta el CSV de ciclos: {path}")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != expected_sha256:
        raise TimingSummaryError(
            f"{path}: SHA-256 {observed_sha256} no coincide con "
            f"{expected_sha256}"
        )
    try:
        with path.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != ("timed_index", "cycles"):
                raise TimingSummaryError(
                    f"{path}: cabecera inesperada {reader.fieldnames}"
                )
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise TimingSummaryError(f"no se pudo leer {path}: {exc}") from exc

    if len(rows) != EXPECTED_CYCLE_VALUES:
        raise TimingSummaryError(
            f"{path}: contiene {len(rows)} valores, se esperaban "
            f"{EXPECTED_CYCLE_VALUES}"
        )
    values: list[int] = []
    for expected_index, row in enumerate(rows):
        try:
            observed_index = int(row["timed_index"], 10)
            cycles = int(row["cycles"], 10)
        except (KeyError, TypeError, ValueError) as exc:
            raise TimingSummaryError(
                f"{path}: fila {expected_index + 2} no es entera"
            ) from exc
        if observed_index != expected_index:
            raise TimingSummaryError(
                f"{path}: timed_index={observed_index}, se esperaba "
                f"{expected_index}"
            )
        if not 1 <= cycles <= UINT32_MAX:
            raise TimingSummaryError(
                f"{path}: cycles={cycles} fuera de uint32 positivo"
            )
        values.append(cycles)
    return values


def linear_quantile(sorted_values: Sequence[int], probability: float) -> float:
    """Return a deterministic type-7/linear empirical quantile."""

    if not sorted_values:
        raise TimingSummaryError("no hay valores para calcular el cuantil")
    if not 0.0 <= probability <= 1.0:
        raise TimingSummaryError(f"probabilidad inválida: {probability}")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return (
        float(sorted_values[lower]) * (1.0 - fraction)
        + float(sorted_values[upper]) * fraction
    )


def cycle_statistics(values: Sequence[int]) -> dict[str, float | int]:
    if len(values) != EXPECTED_CYCLE_VALUES:
        raise TimingSummaryError(
            f"se requieren {EXPECTED_CYCLE_VALUES} ciclos para resumir"
        )
    ordered = sorted(values)
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    quantiles = {
        probability: round(linear_quantile(ordered, probability), 6)
        for probability in (0.05, 0.25, 0.50, 0.75, 0.95, 0.99)
    }
    return {
        "n_cycle_values": len(values),
        "minimum_cycles": ordered[0],
        "p05_cycles": quantiles[0.05],
        "p25_cycles": quantiles[0.25],
        "median_cycles": quantiles[0.50],
        "mean_cycles": round(mean, 6),
        "p75_cycles": quantiles[0.75],
        "p95_cycles": quantiles[0.95],
        "p99_cycles": quantiles[0.99],
        "maximum_cycles": ordered[-1],
        "population_standard_deviation_cycles": round(
            math.sqrt(variance), 6
        ),
    }


def build_report(
    campaign_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    campaign_root = campaign_root.resolve()
    selected = select_accepted_runs(campaign_root)
    cells: list[dict[str, Any]] = []
    for method, board, representation in expected_cells():
        run_path, payload = selected[(method, board, representation)]
        timing_path = run_path.parent / "timing_cycles.csv"
        expected_hash = payload["capture"]["timing_cycles_sha256"]
        values = read_timing_cycles(timing_path, expected_hash)
        stats = cycle_statistics(values)
        source = payload.get("source", {})
        hardware = payload["hardware"]
        cells.append(
            {
                "cell_key": (
                    f"{SYSTEM}|{method}|{board}|{representation}"
                ),
                "source_cell_id": payload["cell"]["cell_id"],
                "system": SYSTEM,
                "method": method,
                "board": board,
                "representation": representation,
                "hardware_reset_repetition_id": payload["reset"][
                    "hardware_reset_repetition_id"
                ],
                "run_id": payload["run_id"],
                "run_json": display_path(run_path),
                "run_json_sha256": sha256_file(run_path),
                "timing_cycles_csv": display_path(timing_path),
                "timing_cycles_sha256": expected_hash,
                "source_commit": source.get("commit"),
                "source_dirty": source.get("dirty"),
                "source_status_sha256": source.get("status_sha256"),
                "build_target": payload["build"]["target"],
                "firmware_images": payload["build"].get("images"),
                "board_name": hardware["board_name"],
                "mcu": hardware["mcu"],
                "probe_serial": hardware["probe_serial"],
                "port": hardware["port"],
                "baud": hardware["baud"],
                **stats,
            }
        )

    report = {
        "schema_version": 1,
        "status": "accepted_12_of_12_reset_pilot_cells",
        "scope": (
            "Chen solver-only cycle-count pilots after ST-LINK hardware reset"
        ),
        "evidence_layer": (
            "solver_only_timing_under_stlink_hardware_reset"
        ),
        "experimental_unit": "stlink_hardware_reset_repetition",
        "campaign_id": CAMPAIGN_ID,
        "selection_contract": {
            "system": SYSTEM,
            "methods": list(METHODS),
            "boards": list(BOARDS),
            "representations": list(REPRESENTATIONS),
            "accepted_runs_per_cell": 1,
            "selected_cells": 12,
            "cycle_values_per_cell": EXPECTED_CYCLE_VALUES,
            "total_cycle_values": 12 * EXPECTED_CYCLE_VALUES,
            "h755_m2sfrk_float32_selected_repetition": 2,
            "all_other_selected_repetitions": 1,
            "inputs_read": ["run.json", "timing_cycles.csv"],
            "quantile_definition": (
                "linear interpolation at (n-1)*p (empirical type 7)"
            ),
            "standard_deviation_definition": "population (ddof=0)",
        },
        "evidence_boundaries": {
            "power_removed": False,
            "reset_mode": "stlink_hardware_reset",
            "reset_repetitions_per_cell": 1,
            "eligible_as_paper_cold_start": False,
            "eligible_as_primary_benchmark": False,
            "inferential_statistics_supported": False,
            "limitations": [
                "N=1 ST-LINK hardware-reset repetition per cell",
                "No physical power cycle was performed",
                "No between-reset variability or inferential claim is supported",
                "This reset pilot is not the preregistered primary benchmark",
                (
                    "Raw cycle counts do not by themselves establish "
                    "cross-board wall-clock speed"
                ),
            ],
        },
        "provenance": {
            "generator": display_path(Path(__file__)),
            "generator_sha256": sha256_file(Path(__file__)),
            "campaign_root": display_path(campaign_root),
            "source_files_other_than_run_json_and_timing_csv_read": False,
        },
        "cells": cells,
    }
    return report, cells


def write_csv(cells: Sequence[dict[str, Any]], output_path: Path) -> None:
    fields = (
        "cell_key",
        "source_cell_id",
        "system",
        "method",
        "board",
        "representation",
        "hardware_reset_repetition_id",
        "n_cycle_values",
        "minimum_cycles",
        "p05_cycles",
        "p25_cycles",
        "median_cycles",
        "mean_cycles",
        "p75_cycles",
        "p95_cycles",
        "p99_cycles",
        "maximum_cycles",
        "population_standard_deviation_cycles",
        "run_id",
        "run_json",
        "run_json_sha256",
        "timing_cycles_csv",
        "timing_cycles_sha256",
        "build_target",
        "board_name",
        "mcu",
        "probe_serial",
        "port",
        "baud",
        "source_commit",
        "source_dirty",
    )
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(cells)


def render_figure(cells: Sequence[dict[str, Any]], output_path: Path) -> None:
    method_labels = {
        "efork3": "EFORK3",
        "gl": "GL",
        "m2sfrk": "M2sFRK",
    }
    representation_style = {
        "float32": {
            "label": "float32",
            "color": "#0072B2",
            "marker": "o",
            "offset": -0.12,
        },
        "fixed_q14_q30": {
            "label": "fixed Q14/Q30",
            "color": "#D55E00",
            "marker": "s",
            "offset": 0.12,
        },
    }
    lookup = {
        (row["method"], row["board"], row["representation"]): row
        for row in cells
    }
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(13.2, 5.8),
        sharey=True,
        constrained_layout=False,
    )
    for axis, method in zip(axes, METHODS):
        for representation in REPRESENTATIONS:
            style = representation_style[representation]
            x_values = []
            medians = []
            lower_errors = []
            upper_errors = []
            for board_index, board in enumerate(BOARDS):
                row = lookup[(method, board, representation)]
                median = float(row["median_cycles"])
                low = float(row["p05_cycles"])
                high = float(row["p95_cycles"])
                x_values.append(board_index + style["offset"])
                medians.append(median)
                lower_errors.append(median - low)
                upper_errors.append(high - median)
            axis.errorbar(
                x_values,
                medians,
                yerr=[lower_errors, upper_errors],
                color=style["color"],
                marker=style["marker"],
                markersize=7,
                linewidth=1.7,
                capsize=4,
                label=style["label"],
            )
            for x_value, median in zip(x_values, medians):
                axis.annotate(
                    f"{median:,.0f}",
                    (x_value, median),
                    xytext=(0, 8),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color=style["color"],
                )
        axis.set_title(method_labels[method], fontweight="bold")
        axis.set_xticks(range(len(BOARDS)), ("F746", "H755"))
        axis.set_xlim(-0.32, 1.32)
        axis.set_xlabel("Board")
        axis.set_yscale("log")
        axis.grid(which="both", axis="y", alpha=0.28, linewidth=0.7)
        axis.set_axisbelow(True)
    axes[0].set_ylabel("DWT cycles per solver step (log scale)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.895),
        ncol=2,
        frameon=False,
    )
    fig.suptitle(
        "Chen solver timing after ST-LINK hardware reset",
        fontsize=15,
        fontweight="bold",
        y=0.98,
    )
    fig.text(
        0.5,
        0.925,
        "Median and P05–P95 across 10,000 timed steps; "
        "one reset repetition per cell",
        ha="center",
        fontsize=10,
    )
    fig.text(
        0.5,
        0.015,
        "Reset pilot only (N=1 per cell): no power cycle, "
        "no inferential claim, not the primary benchmark.",
        ha="center",
        fontsize=9,
        color="#444444",
    )
    fig.subplots_adjust(
        left=0.075,
        right=0.985,
        bottom=0.14,
        top=0.80,
        wspace=0.12,
    )
    fig.savefig(
        output_path,
        dpi=200,
        facecolor="white",
        metadata={
            "Software": "FractionalChaos reset-pilot timing aggregator",
            "Title": "Chen ST-LINK hardware-reset timing pilot",
        },
    )
    plt.close(fig)


def write_outputs(
    report: dict[str, Any],
    cells: Sequence[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "summary.csv"
    figure_path = output_dir / "timing_cycles_reset_pilot_chen.png"
    json_path = output_dir / "summary.json"
    write_csv(cells, csv_path)
    render_figure(cells, figure_path)
    report["artifacts"] = {
        csv_path.name: {
            "sha256": sha256_file(csv_path),
            "bytes": csv_path.stat().st_size,
        },
        figure_path.name: {
            "sha256": sha256_file(figure_path),
            "bytes": figure_path.stat().st_size,
        },
    }
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=DEFAULT_CAMPAIGN_ROOT,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report, cells = build_report(args.campaign_root)
    output_dir = args.output_dir.resolve()
    write_outputs(report, cells, output_dir)
    print(output_dir / "summary.json")
    print(
        json.dumps(
            {
                "status": report["status"],
                "cells": len(cells),
                "cycle_values": sum(
                    int(cell["n_cycle_values"]) for cell in cells
                ),
                "reset_mode": report["evidence_boundaries"]["reset_mode"],
                "power_removed": False,
                "eligible_as_primary_benchmark": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
