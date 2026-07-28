#!/usr/bin/env python3
"""Deterministic Q1.14.14 reference and float/fixed comparison artifacts.

This is an independent host oracle for the representation factor.  It applies
the same Q14 rounding after every visible elementary operation, counts every
saturation, and never silently wraps.  It is intentionally separate from the
portable float32 firmware kernel until the arithmetic contract is frozen.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

FRACTION_BITS = 14
SCALE = 1 << FRACTION_BITS
RAW_MIN = -(1 << 28)
RAW_MAX = (1 << 28) - 1
COEFFICIENT_FRACTION_BITS = 30
COEFFICIENT_SCALE = 1 << COEFFICIENT_FRACTION_BITS
COEFFICIENT_RAW_MIN = -(1 << 31)
COEFFICIENT_RAW_MAX = (1 << 31) - 1


@dataclass
class Arithmetic:
    saturations: int = 0
    coefficient_saturations: int = 0
    nonzero_coefficients_rounded_to_zero: int = 0

    def sat(self, value: int) -> int:
        if value > RAW_MAX:
            self.saturations += 1
            return RAW_MAX
        if value < RAW_MIN:
            self.saturations += 1
            return RAW_MIN
        return value

    def encode(self, value: float) -> int:
        scaled = value * SCALE
        rounded = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
        return self.sat(int(rounded))

    @staticmethod
    def decode(value: int) -> float:
        return value / SCALE

    def add(self, left: int, right: int) -> int:
        return self.sat(left + right)

    def sub(self, left: int, right: int) -> int:
        return self.sat(left - right)

    def mul(self, left: int, right: int) -> int:
        product = left * right
        magnitude = abs(product)
        rounded = (magnitude + (SCALE // 2)) // SCALE
        return self.sat(-rounded if product < 0 else rounded)

    def encode_coefficient(self, value: float) -> int:
        scaled = value * COEFFICIENT_SCALE
        rounded = (
            math.floor(scaled + 0.5)
            if scaled >= 0.0
            else math.ceil(scaled - 0.5)
        )
        if rounded > COEFFICIENT_RAW_MAX:
            self.coefficient_saturations += 1
            result = COEFFICIENT_RAW_MAX
        elif rounded < COEFFICIENT_RAW_MIN:
            self.coefficient_saturations += 1
            result = COEFFICIENT_RAW_MIN
        else:
            result = int(rounded)
        if value != 0.0 and result == 0:
            self.nonzero_coefficients_rounded_to_zero += 1
        return result

    def mul_coefficient(self, coefficient: int, value: int) -> int:
        product = coefficient * value
        magnitude = abs(product)
        rounded = (
            magnitude + (COEFFICIENT_SCALE // 2)
        ) // COEFFICIENT_SCALE
        return self.sat(-rounded if product < 0 else rounded)


@dataclass(frozen=True)
class Manifest:
    name: str
    q: float
    h: float
    memory: int
    parameters: tuple[float, float, float]
    initial: tuple[float, float, float]


def load_manifests(path: Path) -> tuple[Manifest, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        Manifest(
            name=item["system"],
            q=float(item["q"]),
            h=float(item["h"]),
            memory=int(item["memory_increments"]),
            parameters=tuple(float(v) for v in item["parameters"]),
            initial=tuple(float(v) for v in item["initial_state"]),
        )
        for item in payload["manifests"]
    )


def rhs_float(system: int, p: tuple[float, ...], s: tuple[float, ...]):
    x, y, z = s
    p0, p1, p2 = p
    if system == 0:
        return p0 * (y - x), x * (p1 - z) - y, x * y - p2 * z
    if system == 1:
        return -y - z, x + p0 * y, p1 + z * (x - p2)
    return p0 * (y - x), (p2 - p0) * x - x * z + p2 * y, x * y - p1 * z


def rhs_fixed(
    arithmetic: Arithmetic,
    system: int,
    p: tuple[int, ...],
    s: tuple[int, ...],
) -> tuple[int, int, int]:
    x, y, z = s
    p0, p1, p2 = p
    a = arithmetic
    if system == 0:
        return (
            a.mul(p0, a.sub(y, x)),
            a.sub(a.mul(x, a.sub(p1, z)), y),
            a.sub(a.mul(x, y), a.mul(p2, z)),
        )
    if system == 1:
        return (
            a.sub(a.sub(0, y), z),
            a.add(x, a.mul(p0, y)),
            a.add(p1, a.mul(z, a.sub(x, p2))),
        )
    return (
        a.mul(p0, a.sub(y, x)),
        a.add(a.sub(a.mul(a.sub(p2, p0), x), a.mul(x, z)), a.mul(p2, y)),
        a.sub(a.mul(x, y), a.mul(p1, z)),
    )


def efork_coefficients(q: float):
    g1 = math.gamma(1.0 + q)
    g2 = math.gamma(1.0 + 2.0 * q)
    g3 = math.gamma(1.0 + 3.0 * q)
    denominator = 2.0 * g2 * g2 - g3
    c2 = (1.0 / (2.0 * g1)) ** (1.0 / q)
    c3 = (1.0 / (4.0 * g1)) ** (1.0 / q)
    a21 = 1.0 / (2.0 * g1 * g1)
    a31 = (g1 * g1 * g2 + 2.0 * g2 * g2 - g3) / (
        4.0 * g1 * g1 * denominator
    )
    a32 = -g2 / (4.0 * denominator)
    w1 = (8.0 * g1**3 * g2**2 - 6.0 * g1**3 * g3 + g2 * g3) / (
        g1 * g2 * g3
    )
    w2 = 2.0 * g1 * g1 * (4.0 * g2 * g2 - g3) / (g2 * g3)
    w3 = -8.0 * g1 * g1 * denominator / (g2 * g3)
    return (c2, c3), (a21, a31, a32), (w1, w2, w3)


def coefficient_set(manifest: Manifest, method: str):
    hq = manifest.h**manifest.q
    if method == "efork3":
        stages, a, w = efork_coefficients(manifest.q)
        exponent = 1.0 - manifest.q
        inverse_gamma = 1.0 / math.gamma(2.0 - manifest.q)
        weights = tuple(
            tuple(
                ((lag + 1.0 + offset) ** exponent - (lag + offset) ** exponent)
                * inverse_gamma
                for lag in range(manifest.memory)
            )
            for offset in (0.0, stages[0], stages[1])
        )
        return hq, a, w, weights
    if method == "gl":
        weights = [1.0]
        for index in range(1, manifest.memory + 1):
            weights.append(weights[-1] * (1.0 - (manifest.q + 1.0) / index))
        return hq, tuple(weights)
    if method == "m2sfrk":
        c2 = hq / math.gamma(manifest.q + 1.0)
        c4 = hq * math.gamma(manifest.q + 1.0) / math.gamma(2.0 * manifest.q + 1.0)
        return c2, c4
    raise ValueError(method)


def simulate_fixed(
    system: int,
    manifest: Manifest,
    method: str,
    steps: int,
) -> tuple[np.ndarray, Arithmetic]:
    a = Arithmetic()
    p = tuple(a.encode(v) for v in manifest.parameters)
    initial = tuple(a.encode(v) for v in manifest.initial)
    state = initial
    series = [state]
    history: list[tuple[int, int, int]] = []
    coefficients = coefficient_set(manifest, method)

    if method == "efork3":
        hq = a.encode_coefficient(coefficients[0])
        stage_a = tuple(a.encode_coefficient(v) for v in coefficients[1])
        output_w = tuple(a.encode_coefficient(v) for v in coefficients[2])
        weights = tuple(
            tuple(a.encode_coefficient(v) for v in stage)
            for stage in coefficients[3]
        )
    elif method == "gl":
        hq = a.encode_coefficient(coefficients[0])
        weights = tuple(a.encode_coefficient(v) for v in coefficients[1])
    else:
        c2, c4 = (a.encode_coefficient(v) for v in coefficients)

    for _ in range(steps):
        if method == "efork3":
            histories = []
            for stage in range(3):
                total = [0, 0, 0]
                for lag, delta in enumerate(reversed(history)):
                    for component in range(3):
                        total[component] = a.add(
                            total[component],
                            a.mul_coefficient(
                                weights[stage][lag], delta[component]
                            ),
                        )
                histories.append(tuple(total))
            f1 = rhs_fixed(a, system, p, state)
            k1 = tuple(
                a.sub(a.mul_coefficient(hq, f1[i]), histories[0][i])
                for i in range(3)
            )
            x2 = tuple(
                a.add(state[i], a.mul_coefficient(stage_a[0], k1[i]))
                for i in range(3)
            )
            f2 = rhs_fixed(a, system, p, x2)
            k2 = tuple(
                a.sub(a.mul_coefficient(hq, f2[i]), histories[1][i])
                for i in range(3)
            )
            x3 = tuple(
                a.add(
                    a.add(
                        state[i],
                        a.mul_coefficient(stage_a[1], k1[i]),
                    ),
                    a.mul_coefficient(stage_a[2], k2[i]),
                )
                for i in range(3)
            )
            f3 = rhs_fixed(a, system, p, x3)
            k3 = tuple(
                a.sub(a.mul_coefficient(hq, f3[i]), histories[2][i])
                for i in range(3)
            )
            next_state = tuple(
                a.add(
                    a.add(
                        a.add(
                            state[i],
                            a.mul_coefficient(output_w[0], k1[i]),
                        ),
                        a.mul_coefficient(output_w[1], k2[i]),
                    ),
                    a.mul_coefficient(output_w[2], k3[i]),
                )
                for i in range(3)
            )
            delta = tuple(a.sub(next_state[i], state[i]) for i in range(3))
            history.append(delta)
        elif method == "gl":
            totals = [0, 0, 0]
            for lag, deviation in enumerate(reversed(history)):
                for component in range(3):
                    totals[component] = a.add(
                        totals[component],
                        a.mul_coefficient(
                            weights[lag + 1], deviation[component]
                        ),
                    )
            derivative = rhs_fixed(a, system, p, state)
            next_u = tuple(
                a.sub(a.mul_coefficient(hq, derivative[i]), totals[i])
                for i in range(3)
            )
            next_state = tuple(a.add(initial[i], next_u[i]) for i in range(3))
            history.append(next_u)
        else:
            derivative = rhs_fixed(a, system, p, state)
            predictor = tuple(
                a.add(state[i], a.mul_coefficient(c4, derivative[i]))
                for i in range(3)
            )
            predicted_derivative = rhs_fixed(a, system, p, predictor)
            next_state = tuple(
                a.add(
                    state[i],
                    a.mul_coefficient(c2, predicted_derivative[i]),
                )
                for i in range(3)
            )

        if len(history) > manifest.memory:
            history.pop(0)
        state = next_state
        series.append(state)

    return np.asarray(series, dtype=np.int64), a


def simulate_float(
    system: int,
    manifest: Manifest,
    method: str,
    steps: int,
) -> np.ndarray:
    state = manifest.initial
    initial = state
    series = [state]
    history: list[tuple[float, float, float]] = []
    coefficients = coefficient_set(manifest, method)
    for _ in range(steps):
        if method == "efork3":
            hq, stage_a, output_w, weights = coefficients
            histories = []
            for stage in range(3):
                total = [0.0, 0.0, 0.0]
                for lag, delta in enumerate(reversed(history)):
                    for component in range(3):
                        total[component] += weights[stage][lag] * delta[component]
                histories.append(tuple(total))
            f1 = rhs_float(system, manifest.parameters, state)
            k1 = tuple(hq * f1[i] - histories[0][i] for i in range(3))
            x2 = tuple(state[i] + stage_a[0] * k1[i] for i in range(3))
            f2 = rhs_float(system, manifest.parameters, x2)
            k2 = tuple(hq * f2[i] - histories[1][i] for i in range(3))
            x3 = tuple(
                state[i] + stage_a[1] * k1[i] + stage_a[2] * k2[i] for i in range(3)
            )
            f3 = rhs_float(system, manifest.parameters, x3)
            k3 = tuple(hq * f3[i] - histories[2][i] for i in range(3))
            next_state = tuple(
                state[i] + output_w[0] * k1[i] + output_w[1] * k2[i]
                + output_w[2] * k3[i] for i in range(3)
            )
            history.append(tuple(next_state[i] - state[i] for i in range(3)))
        elif method == "gl":
            hq, weights = coefficients
            total = [0.0, 0.0, 0.0]
            for lag, deviation in enumerate(reversed(history)):
                for component in range(3):
                    total[component] += weights[lag + 1] * deviation[component]
            derivative = rhs_float(system, manifest.parameters, state)
            next_u = tuple(hq * derivative[i] - total[i] for i in range(3))
            next_state = tuple(initial[i] + next_u[i] for i in range(3))
            history.append(next_u)
        else:
            c2, c4 = coefficients
            derivative = rhs_float(system, manifest.parameters, state)
            predictor = tuple(state[i] + c4 * derivative[i] for i in range(3))
            derivative2 = rhs_float(system, manifest.parameters, predictor)
            next_state = tuple(state[i] + c2 * derivative2[i] for i in range(3))
        if len(history) > manifest.memory:
            history.pop(0)
        state = next_state
        series.append(state)
    return np.asarray(series, dtype=np.float64)


def write_artifacts(output: Path, name: str, fixed_raw: np.ndarray, floating: np.ndarray,
                    manifest: Manifest, method: str, arithmetic: Arithmetic) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    fixed = fixed_raw.astype(np.float64) / SCALE
    error = fixed - floating
    stem = f"{name}_{method}_float64_vs_mixed_q14_q30"

    with (output / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("step", "float_x", "float_y", "float_z",
                         "fixed_x", "fixed_y", "fixed_z", "error_norm"))
        for index in range(len(floating)):
            writer.writerow((index, *floating[index], *fixed[index],
                             float(np.linalg.norm(error[index]))))

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    for component, label in enumerate("xyz"):
        axes[component].plot(
            floating[:, component],
            label="float64 formula reference",
            lw=1.1,
        )
        axes[component].plot(fixed[:, component], label="Q1.14.14", lw=0.9, alpha=0.8)
        axes[component].set_ylabel(label)
        axes[component].grid(alpha=0.25)
    axes[0].legend()
    axes[-1].set_xlabel("step")
    fig.suptitle(
        f"{manifest.name} / {method}: float64 formula vs state Q14 / coefficients Q30"
    )
    fig.tight_layout()
    fig.savefig(output / f"{stem}_timeseries.png", dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for axis, (left, right, labels) in zip(
        axes, ((0, 1, "xy"), (0, 2, "xz"), (1, 2, "yz"))
    ):
        axis.scatter(floating[:, left], floating[:, right], s=3, label="float", alpha=0.5)
        axis.scatter(fixed[:, left], fixed[:, right], s=3, label="Q14", alpha=0.5)
        axis.set_xlabel(labels[0])
        axis.set_ylabel(labels[1])
        axis.grid(alpha=0.2)
    axes[0].legend()
    fig.suptitle(f"{manifest.name} / {method}: short transient projections")
    fig.tight_layout()
    fig.savefig(output / f"{stem}_trajectory_projections.png", dpi=180)
    plt.close(fig)

    lsb = bytes(
        int(value) & 0xFF
        for row in fixed_raw[1:]
        for value in row
    )
    bit_path = output / f"{stem}_lsb8_xyz.bin"
    bit_path.write_bytes(lsb)
    digest = hashlib.sha256(lsb).hexdigest()

    component_rmse = np.sqrt(np.mean(error * error, axis=0))
    component_scale = np.sqrt(np.mean(floating * floating, axis=0))
    normalized_component_rmse = component_rmse / np.maximum(
        component_scale, np.finfo(np.float64).eps
    )
    arithmetic_contract_passed = (
        arithmetic.saturations == 0
        and arithmetic.coefficient_saturations == 0
        and arithmetic.nonzero_coefficients_rounded_to_zero == 0
    )
    metrics = {
        "system": manifest.name,
        "method": method,
        "state_and_parameter_representation": "Q1.14.14",
        "coefficient_and_weight_representation": "Q1.30",
        "rounding": "nearest, ties away from zero",
        "overflow": "saturating",
        "saturation_count": arithmetic.saturations,
        "coefficient_saturation_count": arithmetic.coefficient_saturations,
        "nonzero_coefficients_rounded_to_zero": (
            arithmetic.nonzero_coefficients_rounded_to_zero
        ),
        "arithmetic_contract_passed": arithmetic_contract_passed,
        "eligible_as_primary_hardware_evidence": False,
        "ineligibility_reason": (
            "host short-transient diagnostic; not a physical-board run"
        ),
        "steps": len(floating) - 1,
        "h": manifest.h,
        "q": manifest.q,
        "physical_horizon": (len(floating) - 1) * manifest.h,
        "rmse": float(np.sqrt(np.mean(error * error))),
        "component_rmse": component_rmse.tolist(),
        "normalized_component_rmse": normalized_component_rmse.tolist(),
        "max_abs_error": float(np.max(np.abs(error))),
        "final_error_norm": float(np.linalg.norm(error[-1])),
        "initial_float": floating[0].tolist(),
        "initial_fixed_decoded": fixed[0].tolist(),
        "initial_quantization_error": error[0].tolist(),
        "lsb_extractor": "8 LSB from x, then y, then z; 24 bits/iteration",
        "bitstream_includes_initial_state": False,
        "bitstream_bytes": len(lsb),
        "bitstream_sha256": digest,
    }
    (output / f"{stem}.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument(
        "--manifests",
        type=Path,
        default=Path(__file__).with_name("candidate_manifests.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "results" / "fixed_point_comparison",
    )
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")

    manifests = load_manifests(args.manifests)
    system_ids = {"lorenz": 0, "rossler": 1, "chen": 2}
    manifest_names = [manifest.name for manifest in manifests]
    if len(manifest_names) != len(set(manifest_names)):
        parser.error("los manifiestos contienen sistemas duplicados")
    unknown_systems = sorted(set(manifest_names) - set(system_ids))
    if unknown_systems:
        parser.error(
            "sistemas no soportados: " + ", ".join(unknown_systems)
        )
    summary = []
    for manifest in manifests:
        system = system_ids[manifest.name]
        for method in ("efork3", "gl", "m2sfrk"):
            fixed_raw, arithmetic = simulate_fixed(system, manifest, method, args.steps)
            floating = simulate_float(system, manifest, method, args.steps)
            summary.append(
                write_artifacts(
                    args.output, manifest.name, fixed_raw, floating,
                    manifest, method, arithmetic
                )
            )
    (args.output / "summary.json").write_text(
        json.dumps(
            {
                "scope": (
                    "independent host comparison of a float64 formula and "
                    "mixed fixed arithmetic; not float32 firmware or hardware evidence"
                ),
                "state_format": "Q1.14.14",
                "coefficient_and_weight_format": "Q1.30",
                "cells": summary,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    failures = sum(not item["arithmetic_contract_passed"] for item in summary)
    print(
        f"wrote {len(summary)} float/fixed comparisons; "
        f"arithmetic-contract failures={failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
