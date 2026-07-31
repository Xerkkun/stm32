#!/usr/bin/env python3
"""Compare the actual portable C float/fixed trajectories with the ABM oracle.

This is a one-second host-side short-horizon comparison.  It executes the
portable C kernels used by the STM32 targets, but it is not a physical-board
timing or energy result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from abm_oracle import caputo_abm_full_memory, fractional_system_rhs
from alternative_systems import alternative_system_rhs


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_MANIFESTS = HERE / "selected_system_manifests_v1.json"
DEFAULT_OUTPUT = (
    HERE / "results" / "embedded_vs_abm_selected_short_horizon_v1"
)
SYSTEM_IDS = {
    "lorenz": 0,
    "rossler": 1,
    "chen": 2,
    "liu": 3,
    "hammouch_mekkaoui": 4,
}
SUPPORTED_COHORTS = {
    ("lorenz", "rossler", "chen"),
    ("chen", "liu", "hammouch_mekkaoui"),
}
KERNEL_SYSTEM_COUNT = len(SYSTEM_IDS)
METHOD_NAMES = ("efork3", "gl", "m2sfrk")
REPRESENTATIONS = ("float32", "fixed_q14_q30")
FIELDS = (
    "system",
    "method",
    "representation",
    "step",
    "x",
    "y",
    "z",
    "x_raw",
    "y_raw",
    "z_raw",
    "status",
    "state_saturations",
    "coefficient_saturations",
    "zeroed_nonzero_coefficients",
)


class ComparisonError(RuntimeError):
    """Raised when the C dump violates the frozen comparison contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifests = payload.get("manifests")
    if manifests is None and isinstance(payload.get("systems"), list):
        manifests = [
            entry.get("contract")
            for entry in payload["systems"]
            if isinstance(entry, dict)
        ]
    if not isinstance(manifests, list) or len(manifests) != 3:
        raise ComparisonError("se requieren exactamente tres manifiestos")
    if not all(isinstance(item, dict) for item in manifests):
        raise ComparisonError("cada manifiesto debe ser un objeto JSON")
    systems = tuple(str(item.get("system")) for item in manifests)
    if systems not in SUPPORTED_COHORTS:
        raise ComparisonError(
            "la cohorte debe ser una de "
            f"{sorted(SUPPORTED_COHORTS)}, no {systems}"
        )
    return manifests  # type: ignore[return-value]


def run_dump(executable: Path, steps: int) -> list[dict[str, str]]:
    executable = executable.resolve()
    if not executable.is_file():
        raise ComparisonError(f"no existe el ejecutable C: {executable}")
    completed = subprocess.run(
        [str(executable), str(steps)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise ComparisonError(
            f"el dump C terminó con {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    reader = csv.DictReader(completed.stdout.splitlines())
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ComparisonError(
            f"cabecera C inesperada: {reader.fieldnames}"
        )
    rows = list(reader)
    expected = KERNEL_SYSTEM_COUNT * 3 * 2 * (steps + 1)
    if len(rows) != expected:
        raise ComparisonError(
            f"el dump contiene {len(rows)} filas, se esperaban {expected}"
        )
    return rows


def group_trajectory(
    rows: list[dict[str, str]],
    *,
    system: int,
    method: int,
    representation: str,
    steps: int,
) -> tuple[np.ndarray, dict[str, int]]:
    selected = [
        row
        for row in rows
        if int(row["system"]) == system
        and int(row["method"]) == method
        and row["representation"] == representation
        and int(row["step"]) <= steps
    ]
    observed_steps = [int(row["step"]) for row in selected]
    if observed_steps != list(range(steps + 1)):
        raise ComparisonError(
            f"secuencia incompleta system={system}, method={method}, "
            f"representation={representation}"
        )
    if any(int(row["status"]) != 0 for row in selected):
        raise ComparisonError("el kernel C reportó un estado distinto de cero")
    states = np.asarray(
        [
            [float(row["x"]), float(row["y"]), float(row["z"])]
            for row in selected
        ],
        dtype=np.float64,
    )
    final = selected[-1]
    diagnostics = {
        key: int(final[key])
        for key in (
            "state_saturations",
            "coefficient_saturations",
            "zeroed_nonzero_coefficients",
        )
    }
    return states, diagnostics


def error_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
) -> dict[str, Any]:
    if reference.shape != candidate.shape:
        raise ComparisonError(
            f"formas incompatibles: {reference.shape} y {candidate.shape}"
        )
    error = candidate - reference
    component_rmse = np.sqrt(np.mean(error * error, axis=0))
    scale = np.maximum(np.ptp(reference, axis=0), 1.0e-12)
    normalized = component_rmse / scale
    error_norm = np.linalg.norm(error, axis=1)
    return {
        "rmse": float(np.sqrt(np.mean(error * error))),
        "component_rmse": [float(value) for value in component_rmse],
        "normalized_component_rmse": [
            float(value) for value in normalized
        ],
        "maximum_absolute_component_error": float(np.max(np.abs(error))),
        "maximum_state_error_norm": float(np.max(error_norm)),
        "final_state_error_norm": float(error_norm[-1]),
        "initial_state_error_norm": float(error_norm[0]),
    }


def build_report(
    *,
    executable: Path,
    manifest_path: Path,
    horizon_s: float,
    resolution_divisor: int,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, Any]],
]:
    manifests = load_manifest(manifest_path)
    maximum_steps = max(
        int(round(horizon_s / float(item["h"])))
        for item in manifests
    )
    dump_rows = run_dump(executable, maximum_steps)
    cells: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []

    for manifest in manifests:
        system_name = str(manifest["system"])
        system = SYSTEM_IDS[system_name]
        base_h = float(manifest["h"])
        steps = int(round(horizon_s / base_h))
        if abs((steps * base_h) - horizon_s) > 1.0e-12:
            raise ComparisonError("el horizonte no es múltiplo del paso")
        if system_name in {"lorenz", "rossler", "chen"}:
            right_hand_side = fractional_system_rhs(
                system_name, manifest["parameters"]
            )
        else:
            right_hand_side = alternative_system_rhs(
                system_name, manifest["parameters"]
            )
        reference_result = caputo_abm_full_memory(
            right_hand_side,
            manifest["initial_state"],
            q=float(manifest["q"]),
            h=base_h / resolution_divisor,
            steps=steps * resolution_divisor,
        )
        reference = reference_result.states[::resolution_divisor]
        if reference.shape[0] != steps + 1:
            raise ComparisonError("falló la alineación temporal ABM")
        reference_rows.extend(
            {
                "system": system_name,
                "step": step,
                "time_s": step * base_h,
                "abm_h": base_h / resolution_divisor,
                "x": float(state[0]),
                "y": float(state[1]),
                "z": float(state[2]),
            }
            for step, state in enumerate(reference)
        )

        trajectories: dict[tuple[int, str], np.ndarray] = {}
        for method in range(3):
            for representation in REPRESENTATIONS:
                candidate, diagnostics = group_trajectory(
                    dump_rows,
                    system=system,
                    method=method,
                    representation=representation,
                    steps=steps,
                )
                trajectories[(method, representation)] = candidate
                cells.append(
                    {
                        "system": system_name,
                        "method": METHOD_NAMES[method],
                        "representation": representation,
                        "h": base_h,
                        "q": float(manifest["q"]),
                        "steps": steps,
                        "physical_horizon_s": horizon_s,
                        "reference": {
                            "method": "full-memory Caputo ABM PECE",
                            "h": base_h / resolution_divisor,
                            "resolution_divisor": resolution_divisor,
                        },
                        "metrics_vs_abm": error_metrics(
                            reference, candidate
                        ),
                        "diagnostics": diagnostics,
                        "arithmetic_contract_passed": (
                            representation == "float32"
                            or all(value == 0 for value in diagnostics.values())
                        ),
                    }
                )

        for method in range(3):
            floating = trajectories[(method, "float32")]
            fixed = trajectories[(method, "fixed_q14_q30")]
            pair = next(
                item
                for item in cells
                if item["system"] == system_name
                and item["method"] == METHOD_NAMES[method]
                and item["representation"] == "fixed_q14_q30"
            )
            pair["metrics_fixed_vs_float32"] = error_metrics(
                floating, fixed
            )

    report = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "status": (
            "completed_no_arithmetic_contract_failures"
            if all(item["arithmetic_contract_passed"] for item in cells)
            else "completed_with_arithmetic_contract_failures"
        ),
        "scope": (
            "One-second host execution of the actual portable C float32 and "
            "mixed fixed-point kernels compared with a full-memory float64 "
            "Caputo ABM reference at h/4"
        ),
        "evidence_layer": "host_short_horizon_numerical_comparison",
        "does_not_establish": [
            "long-horizon chaos",
            "physical-board timing or energy",
            "bitstream randomness",
            "equivalence between different fractional operators",
        ],
        "provenance": {
            "dump_executable": str(executable.resolve()),
            "dump_executable_sha256": sha256_file(executable.resolve()),
            "dump_source": "tests/embedded_trajectory_dump.c",
            "dump_source_sha256": sha256_file(
                ROOT / "tests" / "embedded_trajectory_dump.c"
            ),
            "float_kernel_source_sha256": sha256_file(
                ROOT / "common" / "fractional_chaos.c"
            ),
            "fixed_kernel_source_sha256": sha256_file(
                ROOT / "common" / "fractional_chaos_fixed.c"
            ),
            "abm_source_sha256": sha256_file(HERE / "abm_oracle.py"),
            "alternative_rhs_source_sha256": sha256_file(
                HERE / "alternative_systems.py"
            ),
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": sha256_file(manifest_path.resolve()),
        },
        "configuration": {
            "physical_horizon_s": horizon_s,
            "abm_resolution_divisor": resolution_divisor,
            "candidate_arithmetic": list(REPRESENTATIONS),
        },
        "cells": cells,
    }
    return report, cells, dump_rows, reference_rows


def write_outputs(
    report: dict[str, Any],
    cells: list[dict[str, Any]],
    dump_rows: list[dict[str, str]],
    reference_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_path = output_dir / "embedded_trajectories.csv"
    with dump_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(dump_rows)
    reference_path = output_dir / "abm_reference_h_over_4.csv"
    with reference_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("system", "step", "time_s", "abm_h", "x", "y", "z"),
        )
        writer.writeheader()
        writer.writerows(reference_rows)
    rows = []
    for cell in cells:
        metrics = cell["metrics_vs_abm"]
        pair = cell.get("metrics_fixed_vs_float32")
        rows.append(
            {
                "system": cell["system"],
                "method": cell["method"],
                "representation": cell["representation"],
                "h": cell["h"],
                "q": cell["q"],
                "steps": cell["steps"],
                "physical_horizon_s": cell["physical_horizon_s"],
                "rmse_vs_abm": metrics["rmse"],
                "maximum_absolute_component_error_vs_abm": metrics[
                    "maximum_absolute_component_error"
                ],
                "final_state_error_norm_vs_abm": metrics[
                    "final_state_error_norm"
                ],
                "maximum_normalized_component_rmse_vs_abm": max(
                    metrics["normalized_component_rmse"]
                ),
                "rmse_fixed_vs_float32": (
                    pair["rmse"] if pair is not None else ""
                ),
                "state_saturations": cell["diagnostics"][
                    "state_saturations"
                ],
                "coefficient_saturations": cell["diagnostics"][
                    "coefficient_saturations"
                ],
                "zeroed_nonzero_coefficients": cell["diagnostics"][
                    "zeroed_nonzero_coefficients"
                ],
                "arithmetic_contract_passed": cell[
                    "arithmetic_contract_passed"
                ],
            }
        )
    with (output_dir / "comparison.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metrics_path = output_dir / "comparison.csv"
    report["artifacts"] = {
        path.name: {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in (dump_path, reference_path, metrics_path)
    }
    (output_dir / "comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-executable", type=Path, required=True)
    parser.add_argument("--manifests", type=Path, default=DEFAULT_MANIFESTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--horizon-s", type=float, default=1.0)
    parser.add_argument("--abm-resolution-divisor", type=int, default=4)
    args = parser.parse_args()
    if args.horizon_s <= 0.0:
        parser.error("--horizon-s debe ser positivo")
    if args.abm_resolution_divisor < 2:
        parser.error("--abm-resolution-divisor debe ser al menos 2")

    report, cells, dump_rows, reference_rows = build_report(
        executable=args.dump_executable,
        manifest_path=args.manifests.resolve(),
        horizon_s=float(args.horizon_s),
        resolution_divisor=int(args.abm_resolution_divisor),
    )
    write_outputs(
        report,
        cells,
        dump_rows,
        reference_rows,
        args.output_dir.resolve(),
    )
    print(args.output_dir.resolve() / "comparison.json")
    print(json.dumps({"status": report["status"], "cells": len(cells)}))
    return (
        0
        if report["status"] == "completed_no_arithmetic_contract_failures"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
