#!/usr/bin/env python3
"""Independent bit-exact oracle for the mixed Q14/Q30 C kernel."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path

Q14_SCALE = 1 << 14
Q14_MIN = -(1 << 28)
Q14_MAX = (1 << 28) - 1
Q30_SCALE = 1 << 30
Q30_MIN = -(1 << 31)
Q30_MAX = (1 << 31) - 1
METHODS = ("efork3", "gl", "m2sfrk")


def round_away(value: float) -> int:
    return (
        math.floor(value + 0.5)
        if value >= 0.0
        else math.ceil(value - 0.5)
    )


class Q14Arithmetic:
    def __init__(self) -> None:
        self.saturations = 0

    def sat(self, value: int) -> int:
        if value > Q14_MAX:
            self.saturations += 1
            return Q14_MAX
        if value < Q14_MIN:
            self.saturations += 1
            return Q14_MIN
        return value

    def encode(self, value: float) -> int:
        return self.sat(round_away(value * Q14_SCALE))

    def add(self, left: int, right: int) -> int:
        return self.sat(left + right)

    def sub(self, left: int, right: int) -> int:
        return self.sat(left - right)

    def mul(self, left: int, right: int) -> int:
        product = left * right
        rounded = (abs(product) + (Q14_SCALE // 2)) // Q14_SCALE
        return self.sat(-rounded if product < 0 else rounded)

    def mul_coefficient(self, coefficient: int, value: int) -> int:
        product = coefficient * value
        rounded = (abs(product) + (Q30_SCALE // 2)) // Q30_SCALE
        return self.sat(-rounded if product < 0 else rounded)


class Q30Encoder:
    def __init__(self) -> None:
        self.saturations = 0
        self.zeroed_nonzero = 0

    def encode(self, value: float) -> int:
        raw = round_away(value * Q30_SCALE)
        if raw > Q30_MAX:
            self.saturations += 1
            raw = Q30_MAX
        elif raw < Q30_MIN:
            self.saturations += 1
            raw = Q30_MIN
        if value != 0.0 and raw == 0:
            self.zeroed_nonzero += 1
        return raw


def rhs(
    arithmetic: Q14Arithmetic,
    system: int,
    parameters: tuple[int, int, int],
    state: tuple[int, int, int],
) -> tuple[int, int, int]:
    x, y, z = state
    p0, p1, p2 = parameters
    a = arithmetic
    if system == 0:
        return (
            a.mul(p0, a.sub(y, x)),
            a.sub(a.mul(x, a.sub(p1, z)), y),
            a.sub(a.mul(x, y), a.mul(p2, z)),
        )
    if system == 1:
        return (
            a.sub(-y, z),
            a.add(x, a.mul(p0, y)),
            a.add(p1, a.mul(z, a.sub(x, p2))),
        )
    return (
        a.mul(p0, a.sub(y, x)),
        a.add(
            a.sub(a.mul(a.sub(p2, p0), x), a.mul(x, z)),
            a.mul(p2, y),
        ),
        a.sub(a.mul(x, y), a.mul(p1, z)),
    )


def efork_coefficients(
    q: float,
) -> tuple[
    tuple[float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    gamma_1 = math.gamma(1.0 + q)
    gamma_2 = math.gamma(1.0 + (2.0 * q))
    gamma_3 = math.gamma(1.0 + (3.0 * q))
    denominator = (2.0 * gamma_2 * gamma_2) - gamma_3
    c2 = (1.0 / (2.0 * gamma_1)) ** (1.0 / q)
    c3 = (1.0 / (4.0 * gamma_1)) ** (1.0 / q)
    a21 = 1.0 / (2.0 * gamma_1 * gamma_1)
    a31 = (
        (gamma_1 * gamma_1 * gamma_2)
        + (2.0 * gamma_2 * gamma_2)
        - gamma_3
    ) / (4.0 * gamma_1 * gamma_1 * denominator)
    a32 = -gamma_2 / (4.0 * denominator)
    w1 = (
        (8.0 * gamma_1**3 * gamma_2**2)
        - (6.0 * gamma_1**3 * gamma_3)
        + (gamma_2 * gamma_3)
    ) / (gamma_1 * gamma_2 * gamma_3)
    w2 = (
        2.0
        * gamma_1
        * gamma_1
        * ((4.0 * gamma_2 * gamma_2) - gamma_3)
        / (gamma_2 * gamma_3)
    )
    w3 = (
        -8.0
        * gamma_1
        * gamma_1
        * denominator
        / (gamma_2 * gamma_3)
    )
    return (c2, c3), (a21, a31, a32), (w1, w2, w3)


def prepare_coefficients(
    manifest: dict[str, object],
    method: str,
    encoder: Q30Encoder,
) -> tuple[object, ...]:
    q = float(manifest["q"])
    h = float(manifest["h"])
    memory = int(manifest["memory_increments"])
    h_to_q = h**q

    if method == "efork3":
        stages, stage_coefficients, output_weights = efork_coefficients(q)
        exponent = 1.0 - q
        inverse_gamma = 1.0 / math.gamma(2.0 - q)
        history_weights = tuple(
            tuple(
                encoder.encode(
                    (
                        (lag + 1.0 + offset) ** exponent
                        - (lag + offset) ** exponent
                    )
                    * inverse_gamma
                )
                for lag in range(memory)
            )
            for offset in (0.0, stages[0], stages[1])
        )
        return (
            encoder.encode(h_to_q),
            tuple(encoder.encode(value) for value in stage_coefficients),
            tuple(encoder.encode(value) for value in output_weights),
            history_weights,
        )
    if method == "gl":
        weight = 1.0
        history_weights = [encoder.encode(weight)]
        for index in range(1, memory + 1):
            weight *= 1.0 - ((q + 1.0) / index)
            history_weights.append(encoder.encode(weight))
        return encoder.encode(h_to_q), tuple(history_weights)

    gamma_1 = math.gamma(q + 1.0)
    gamma_2 = math.gamma((2.0 * q) + 1.0)
    return (
        encoder.encode(h_to_q / gamma_1),
        encoder.encode((h_to_q * gamma_1) / gamma_2),
    )


def simulate(
    system: int,
    manifest: dict[str, object],
    method: str,
) -> dict[int, tuple[int, int, int, int, int, int]]:
    arithmetic = Q14Arithmetic()
    encoder = Q30Encoder()
    parameters = tuple(
        arithmetic.encode(float(value))
        for value in manifest["parameters"]  # type: ignore[union-attr]
    )
    initial = tuple(
        arithmetic.encode(float(value))
        for value in manifest["initial_state"]  # type: ignore[union-attr]
    )
    state = initial
    history: list[tuple[int, int, int]] = []
    memory = int(manifest["memory_increments"])
    coefficients = prepare_coefficients(manifest, method, encoder)
    checkpoints: dict[int, tuple[int, int, int, int, int, int]] = {}
    a = arithmetic

    for step in range(1, 33):
        if method == "efork3":
            h_to_q, stage_a, output_w, weights = coefficients
            histories: list[tuple[int, int, int]] = []
            for stage in range(3):
                total = [0, 0, 0]
                for lag, increment in enumerate(reversed(history)):
                    for component in range(3):
                        total[component] = a.add(
                            total[component],
                            a.mul_coefficient(
                                weights[stage][lag],  # type: ignore[index]
                                increment[component],
                            ),
                        )
                histories.append(tuple(total))

            f1 = rhs(a, system, parameters, state)
            k1 = tuple(
                a.sub(
                    a.mul_coefficient(h_to_q, f1[component]),
                    histories[0][component],
                )
                for component in range(3)
            )
            x2 = tuple(
                a.add(
                    state[component],
                    a.mul_coefficient(stage_a[0], k1[component]),
                )
                for component in range(3)
            )
            f2 = rhs(a, system, parameters, x2)
            k2 = tuple(
                a.sub(
                    a.mul_coefficient(h_to_q, f2[component]),
                    histories[1][component],
                )
                for component in range(3)
            )
            x3 = tuple(
                a.add(
                    a.add(
                        a.mul_coefficient(Q30_SCALE, state[component]),
                        a.mul_coefficient(stage_a[1], k1[component]),
                    ),
                    a.mul_coefficient(stage_a[2], k2[component]),
                )
                for component in range(3)
            )
            f3 = rhs(a, system, parameters, x3)
            k3 = tuple(
                a.sub(
                    a.mul_coefficient(h_to_q, f3[component]),
                    histories[2][component],
                )
                for component in range(3)
            )
            next_state = tuple(
                a.add(
                    a.add(
                        a.add(
                            a.mul_coefficient(Q30_SCALE, state[component]),
                            a.mul_coefficient(
                                output_w[0], k1[component]
                            ),
                        ),
                        a.mul_coefficient(output_w[1], k2[component]),
                    ),
                    a.mul_coefficient(output_w[2], k3[component]),
                )
                for component in range(3)
            )
            history.append(
                tuple(
                    a.sub(next_state[index], state[index])
                    for index in range(3)
                )
            )
        elif method == "gl":
            h_to_q, weights = coefficients
            total = [0, 0, 0]
            for lag, deviation in enumerate(reversed(history)):
                for component in range(3):
                    total[component] = a.add(
                        total[component],
                        a.mul_coefficient(
                            weights[lag + 1],  # type: ignore[index]
                            deviation[component],
                        ),
                    )
            derivative = rhs(a, system, parameters, state)
            next_u = tuple(
                a.sub(
                    a.mul_coefficient(
                        h_to_q, derivative[component]
                    ),
                    total[component],
                )
                for component in range(3)
            )
            next_state = tuple(
                a.add(initial[component], next_u[component])
                for component in range(3)
            )
            history.append(next_u)
        else:
            c2, c4 = coefficients
            derivative = rhs(a, system, parameters, state)
            predictor = tuple(
                a.add(
                    state[component],
                    a.mul_coefficient(c4, derivative[component]),
                )
                for component in range(3)
            )
            predicted_derivative = rhs(
                a, system, parameters, predictor
            )
            next_state = tuple(
                a.add(
                    state[component],
                    a.mul_coefficient(
                        c2, predicted_derivative[component]
                    ),
                )
                for component in range(3)
            )

        if len(history) > memory:
            history.pop(0)
        state = next_state
        if step in (1, 32):
            checkpoints[step] = (
                state[0],
                state[1],
                state[2],
                arithmetic.saturations,
                encoder.saturations,
                encoder.zeroed_nonzero,
            )
    return checkpoints


def read_c_dump(executable: Path) -> dict[
    tuple[int, int, int],
    tuple[int, int, int, int, int, int],
]:
    completed = subprocess.run(
        [str(executable)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rows = csv.DictReader(completed.stdout.splitlines())
    actual = {}
    for row in rows:
        key = (
            int(row["system"]),
            int(row["method"]),
            int(row["step"]),
        )
        if key in actual:
            raise AssertionError(f"duplicate C checkpoint: {key}")
        actual[key] = (
            int(row["x_raw"]),
            int(row["y_raw"]),
            int(row["z_raw"]),
            int(row["state_saturations"]),
            int(row["coefficient_saturations"]),
            int(row["zeroed_nonzero_coefficients"]),
        )
    return actual


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: fixed_reference_check.py DUMP_EXECUTABLE "
            "CANDIDATE_MANIFESTS",
            file=sys.stderr,
        )
        return 2

    executable = Path(sys.argv[1]).resolve()
    manifest_path = Path(sys.argv[2]).resolve()
    manifests = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )["manifests"]
    actual = read_c_dump(executable)
    expected = {}
    for system, manifest in enumerate(manifests):
        for method_index, method in enumerate(METHODS):
            for step, record in simulate(
                system, manifest, method
            ).items():
                expected[(system, method_index, step)] = record

    errors = []
    for key in sorted(set(actual) | set(expected)):
        if key not in actual:
            errors.append(f"missing C checkpoint {key}")
        elif key not in expected:
            errors.append(f"unexpected C checkpoint {key}")
        elif actual[key] != expected[key]:
            errors.append(
                f"{key}: C={actual[key]} Python={expected[key]}"
            )
    if errors:
        print("fixed_reference_check: bit parity failed", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    if len(actual) != 18:
        print(
            f"fixed_reference_check: expected 18 checkpoints, "
            f"got {len(actual)}",
            file=sys.stderr,
        )
        return 1

    print(
        "fixed_reference_check: 18 bit-exact checkpoints passed "
        "(9 cells; steps 1 and 32)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
