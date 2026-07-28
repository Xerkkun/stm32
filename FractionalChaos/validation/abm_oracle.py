#!/usr/bin/env python3
"""Independent full-memory Caputo ABM reference integrator.

This module intentionally does not import or call the embedded EFORK3/GL
kernel.  It implements the Diethelm Adams--Bashforth--Moulton PECE scheme in
host ``float64`` and retains every right-hand-side evaluation from the lower
terminal.  It is an oracle for numerical comparison, not a real-time solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
RightHandSide = Callable[[float, FloatArray], npt.ArrayLike]


@dataclass(frozen=True)
class ABMResult:
    """Complete trajectory and execution metadata from one ABM integration."""

    times: FloatArray
    states: FloatArray
    rhs_evaluations: int
    q: float
    h: float
    memory_policy: str = "full_history"
    operator: str = "caputo"
    corrector: str = "single_evaluation_pece"


def _as_finite_vector(values: npt.ArrayLike, *, name: str) -> FloatArray:
    vector = np.atleast_1d(np.asarray(values, dtype=np.float64))
    if vector.ndim != 1 or vector.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional vector")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must contain only finite values")
    return vector


def _evaluate_rhs(
    rhs: RightHandSide,
    time: float,
    state: FloatArray,
    dimension: int,
) -> FloatArray:
    value = _as_finite_vector(rhs(time, state.copy()), name="rhs result")
    if value.shape != (dimension,):
        raise ValueError(
            f"rhs result has shape {value.shape}; expected {(dimension,)}"
        )
    return value


def caputo_abm_full_memory(
    rhs: RightHandSide,
    initial_state: Sequence[float] | FloatArray,
    *,
    q: float,
    h: float,
    steps: int,
) -> ABMResult:
    r"""Integrate a commensurate Caputo system with full-history ABM PECE.

    The solved initial-value problem is

    .. math::

       {}^C D_t^q x(t) = f(t, x(t)), \qquad x(0) = x_0,

    for ``0 < q <= 1``.  The predictor and corrector use the standard
    Diethelm product-integration weights.  No history truncation, restart, or
    embedded-kernel coefficient table is used.
    """

    q = float(q)
    h = float(h)
    if not math.isfinite(q) or not 0.0 < q <= 1.0:
        raise ValueError("q must be finite and satisfy 0 < q <= 1")
    if not math.isfinite(h) or h <= 0.0:
        raise ValueError("h must be finite and positive")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 0:
        raise ValueError("steps must be a non-negative integer")

    x0 = _as_finite_vector(initial_state, name="initial_state")
    dimension = int(x0.size)
    times = np.arange(steps + 1, dtype=np.float64) * h
    states = np.empty((steps + 1, dimension), dtype=np.float64)
    derivatives = np.empty_like(states)
    states[0] = x0
    derivatives[0] = _evaluate_rhs(rhs, 0.0, x0, dimension)
    evaluations = 1

    indices = np.arange(steps + 2, dtype=np.float64)
    powers_q = indices**q
    powers_q1 = indices ** (q + 1.0)
    predictor_scale = (h**q) / math.gamma(q + 1.0)
    corrector_scale = (h**q) / math.gamma(q + 2.0)

    for n in range(steps):
        # b_{j,n+1}, ordered for derivative samples f_0 ... f_n.
        predictor_weights = (
            powers_q[1 : n + 2] - powers_q[0 : n + 1]
        )[::-1]
        predictor = x0 + predictor_scale * (
            predictor_weights @ derivatives[: n + 1]
        )
        predicted_derivative = _evaluate_rhs(
            rhs, float(times[n + 1]), predictor, dimension
        )
        evaluations += 1

        # a_{0,n+1} is the endpoint-specific first weight.  The remaining
        # weights are the second differences for f_1 ... f_n.
        first_weight = (
            powers_q1[n] - (float(n) - q) * powers_q[n + 1]
        )
        if n == 0:
            history = first_weight * derivatives[0]
        else:
            r = np.arange(n, 0, -1, dtype=np.int64)
            interior_weights = (
                powers_q1[r + 1]
                + powers_q1[r - 1]
                - 2.0 * powers_q1[r]
            )
            history = (
                first_weight * derivatives[0]
                + interior_weights @ derivatives[1 : n + 1]
            )

        corrected = x0 + corrector_scale * (
            history + predicted_derivative
        )
        if not np.all(np.isfinite(corrected)):
            raise FloatingPointError(
                f"non-finite corrected state at step {n + 1}"
            )
        states[n + 1] = corrected
        derivatives[n + 1] = _evaluate_rhs(
            rhs, float(times[n + 1]), corrected, dimension
        )
        evaluations += 1

    return ABMResult(
        times=times,
        states=states,
        rhs_evaluations=evaluations,
        q=q,
        h=h,
    )


def fractional_system_rhs(
    system: str,
    parameters: Sequence[float],
) -> RightHandSide:
    """Return the Lorenz, Rossler, or Chen vector field used by the firmware."""

    values = tuple(float(value) for value in parameters)
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        raise ValueError("parameters must contain three finite values")
    normalized = system.strip().lower()
    if normalized not in {"lorenz", "rossler", "chen"}:
        raise ValueError(f"unsupported system: {system}")

    def evaluate(_time: float, state: FloatArray) -> FloatArray:
        x, y, z = (float(value) for value in state)
        p0, p1, p2 = values
        if normalized == "lorenz":
            result = (
                p0 * (y - x),
                x * (p1 - z) - y,
                x * y - p2 * z,
            )
        elif normalized == "rossler":
            result = (
                -y - z,
                x + p0 * y,
                p1 + z * (x - p2),
            )
        else:
            result = (
                p0 * (y - x),
                (p2 - p0) * x - x * z + p2 * y,
                x * y - p1 * z,
            )
        return np.asarray(result, dtype=np.float64)

    return evaluate
