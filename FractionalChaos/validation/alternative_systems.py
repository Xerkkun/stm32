#!/usr/bin/env python3
"""Published alternative vector fields for the ABM scouting layer.

This module is deliberately separate from :mod:`abm_oracle`.  Adding a new
right-hand side must not invalidate the frozen hashes of the historical
Lorenz--Rossler--Chen qualification evidence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Callable

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
RightHandSide = Callable[[float, FloatArray], npt.ArrayLike]

PARAMETER_COUNTS = {
    "lu": 3,
    "genesio_tesi_simplified": 3,
    "shimizu_morioka": 2,
    "liu": 6,
    "hammouch_mekkaoui": 0,
    "munoz_pacheco_hidden": 1,
    "glucose_insulin": 4,
}


def alternative_system_rhs(
    system: str,
    parameters: Sequence[float],
) -> RightHandSide:
    """Return one of the frozen three-dimensional alternative vector fields."""

    normalized = system.strip().lower()
    if normalized not in PARAMETER_COUNTS:
        raise ValueError(f"unsupported alternative system: {system}")

    values = tuple(float(value) for value in parameters)
    expected = PARAMETER_COUNTS[normalized]
    if len(values) != expected or not all(math.isfinite(value) for value in values):
        raise ValueError(
            f"{normalized} parameters must contain {expected} finite values"
        )

    def evaluate(_time: float, state: FloatArray) -> FloatArray:
        vector = np.asarray(state, dtype=np.float64)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise ValueError("alternative-system state must contain 3 finite values")
        x, y, z = (float(value) for value in vector)

        if normalized == "lu":
            a, b, c = values
            result = (
                a * (y - x),
                c * y - x * z,
                x * y - b * z,
            )
        elif normalized == "genesio_tesi_simplified":
            a, b, c = values
            result = (
                y,
                z,
                -a * x - b * y - c * z + x * x,
            )
        elif normalized == "shimizu_morioka":
            alpha, beta = values
            result = (
                y,
                x - beta * y - x * z,
                -alpha * z + x * x,
            )
        elif normalized == "liu":
            a, b, c, e, k, m = values
            result = (
                -a * x - e * y * y,
                b * y - k * x * z,
                -c * z + m * x * y,
            )
        elif normalized == "hammouch_mekkaoui":
            result = (
                -2.0 * x - y * y,
                -4.0 * x * z + 3.0 * y - z * z,
                4.0 * x * y - 7.0 * z + y * z,
            )
        elif normalized == "munoz_pacheco_hidden":
            (a,) = values
            result = (
                y * z + x * (y - a),
                1.0 - abs(x),
                -x * y - z,
            )
        else:
            a1, a7, a8, a15 = values
            x2 = x * x
            y2 = y * y
            z2 = z * z
            x3 = x2 * x
            y3 = y2 * y
            z3 = z2 * z
            xy = x * y
            yz = y * z
            result = (
                -a1 * x
                + 0.1 * xy
                + 1.09 * y2
                - 1.08 * y3
                + 0.03 * z
                - 0.06 * z2
                + a7 * z3
                - 0.19,
                -a8 * xy
                + 3.84 * x2
                + 1.2 * x3
                + 0.3 * y * (1.0 - y)
                - 1.37 * z
                + 0.3 * z2
                - 0.22 * z3
                - 0.56,
                a15 * y
                - 1.35 * y2
                + 0.5 * y3
                + 0.42 * z
                + 0.15 * yz,
            )
        return np.asarray(result, dtype=np.float64)

    return evaluate
