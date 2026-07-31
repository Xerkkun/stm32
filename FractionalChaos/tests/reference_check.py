#!/usr/bin/env python3
"""Independent short-horizon check of the portable float32 C kernel."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Manifest:
    q: float
    h: float
    memory: int
    parameters: tuple[float, ...]
    initial: tuple[float, float, float]


def load_manifests() -> tuple[Manifest, ...]:
    historical_path = (
        Path(__file__).resolve().parents[1]
        / "validation"
        / "candidate_manifests_rossler_classic_v2.json"
    )
    selected_path = (
        Path(__file__).resolve().parents[1]
        / "validation"
        / "selected_system_manifests_v1.json"
    )
    historical = json.loads(
        historical_path.read_text(encoding="utf-8")
    )["manifests"]
    selected_payload = json.loads(
        selected_path.read_text(encoding="utf-8")
    )
    selected = [
        entry["contract"] for entry in selected_payload["systems"]
    ]
    additions = [
        item
        for item in selected
        if item["system"] in {"liu", "hammouch_mekkaoui"}
    ]
    return tuple(
        Manifest(
            q=float(item["q"]),
            h=float(item["h"]),
            memory=int(item["memory_increments"]),
            parameters=tuple(float(value) for value in item["parameters"]),
            initial=tuple(float(value) for value in item["initial_state"]),
        )
        for item in historical + additions
    )


MANIFESTS = load_manifests()


def rhs(system: int, parameters: tuple[float, ...], state: tuple[float, ...]):
    x, y, z = state
    if system == 0:
        p0, p1, p2 = parameters
        return p0 * (y - x), x * (p1 - z) - y, x * y - p2 * z
    if system == 1:
        p0, p1, p2 = parameters
        return -y - z, x + p0 * y, p1 + z * (x - p2)
    if system == 2:
        p0, p1, p2 = parameters
        return (
            p0 * (y - x),
            (p2 - p0) * x - x * z + p2 * y,
            x * y - p1 * z,
        )
    if system == 3:
        p0, p1, p2, p3, p4, p5 = parameters
        return (
            -p0 * x - p3 * y * y,
            p1 * y - p4 * x * z,
            -p2 * z + p5 * x * y,
        )
    if system == 4:
        return (
            -2.0 * x - y * y,
            -4.0 * x * z + 3.0 * y - z * z,
            4.0 * x * y - 7.0 * z + y * z,
        )
    raise ValueError(f"unsupported system id: {system}")


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
    w1 = (
        8.0 * g1**3 * g2**2 - 6.0 * g1**3 * g3 + g2 * g3
    ) / (g1 * g2 * g3)
    w2 = 2.0 * g1 * g1 * (4.0 * g2 * g2 - g3) / (g2 * g3)
    w3 = -8.0 * g1 * g1 * denominator / (g2 * g3)
    return (c2, c3), (a21, a31, a32), (w1, w2, w3)


def efork_reference(system: int, manifest: Manifest, steps: int):
    q = manifest.q
    hq = manifest.h**q
    stages, a, w = efork_coefficients(q)
    offsets = (0.0, stages[0], stages[1])
    exponent = 1.0 - q
    inverse_gamma = 1.0 / math.gamma(2.0 - q)
    weights = [
        [
            ((lag + 1.0 + offset) ** exponent - (lag + offset) ** exponent)
            * inverse_gamma
            for lag in range(min(steps, manifest.memory))
        ]
        for offset in offsets
    ]
    state = manifest.initial
    increments: list[tuple[float, float, float]] = []

    for _ in range(steps):
        valid = min(len(increments), manifest.memory)
        histories = []
        for stage in range(3):
            total = [0.0, 0.0, 0.0]
            for lag in range(valid):
                delta = increments[-1 - lag]
                weight = weights[stage][lag]
                for component in range(3):
                    total[component] += weight * delta[component]
            histories.append(tuple(total))

        f1 = rhs(system, manifest.parameters, state)
        k1 = tuple(hq * f1[i] - histories[0][i] for i in range(3))
        x2 = tuple(state[i] + a[0] * k1[i] for i in range(3))
        f2 = rhs(system, manifest.parameters, x2)
        k2 = tuple(hq * f2[i] - histories[1][i] for i in range(3))
        x3 = tuple(state[i] + a[1] * k1[i] + a[2] * k2[i] for i in range(3))
        f3 = rhs(system, manifest.parameters, x3)
        k3 = tuple(hq * f3[i] - histories[2][i] for i in range(3))
        next_state = tuple(
            state[i] + w[0] * k1[i] + w[1] * k2[i] + w[2] * k3[i]
            for i in range(3)
        )
        increments.append(tuple(next_state[i] - state[i] for i in range(3)))
        if len(increments) > manifest.memory:
            increments.pop(0)
        state = next_state
    return state


def gl_reference(system: int, manifest: Manifest, steps: int):
    weights = [1.0]
    for index in range(1, manifest.memory + 1):
        weights.append(weights[-1] * (1.0 - (manifest.q + 1.0) / index))

    hq = manifest.h**manifest.q
    state = manifest.initial
    deviations: list[tuple[float, float, float]] = []
    for _ in range(steps):
        valid = min(len(deviations), manifest.memory)
        history = [0.0, 0.0, 0.0]
        for lag in range(valid):
            deviation = deviations[-1 - lag]
            weight = weights[lag + 1]
            for component in range(3):
                history[component] += weight * deviation[component]
        derivative = rhs(system, manifest.parameters, state)
        next_u = tuple(
            hq * derivative[i] - history[i] for i in range(3)
        )
        state = tuple(manifest.initial[i] + next_u[i] for i in range(3))
        deviations.append(next_u)
        if len(deviations) > manifest.memory:
            deviations.pop(0)
    return state


def m2sfrk_reference(system: int, manifest: Manifest, steps: int):
    hq = manifest.h**manifest.q
    c2 = hq / math.gamma(manifest.q + 1.0)
    c4 = (
        hq
        * math.gamma(manifest.q + 1.0)
        / math.gamma(2.0 * manifest.q + 1.0)
    )
    state = manifest.initial
    for _ in range(steps):
        derivative = rhs(system, manifest.parameters, state)
        predictor = tuple(
            state[index] + c4 * derivative[index]
            for index in range(3)
        )
        predicted_derivative = rhs(
            system, manifest.parameters, predictor
        )
        state = tuple(
            state[index] + c2 * predicted_derivative[index]
            for index in range(3)
        )
    return state


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: reference_check.py <reference_dump>", file=sys.stderr)
        return 2

    completed = subprocess.run(
        [sys.argv[1], "16"],
        check=True,
        capture_output=True,
        text=True,
    )

    observed = {}
    for line in completed.stdout.splitlines():
        fields = line.split(",")
        key = int(fields[0]), int(fields[1])
        observed[key] = tuple(float(value) for value in fields[3:6])

    failures = 0
    for system, manifest in enumerate(MANIFESTS):
        for method in range(3):
            if method == 0:
                reference = efork_reference(system, manifest, 16)
            elif method == 1:
                reference = gl_reference(system, manifest, 16)
            else:
                reference = m2sfrk_reference(system, manifest, 16)
            actual = observed[(system, method)]
            for component, (got, expected) in enumerate(zip(actual, reference)):
                tolerance = 5.0e-4 * max(1.0, abs(expected))
                if abs(got - expected) > tolerance:
                    print(
                        f"mismatch system={system} method={method} "
                        f"component={component}: {got} vs {expected}",
                        file=sys.stderr,
                    )
                    failures += 1

    if failures:
        return 1
    print("independent Python short-horizon reference passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
