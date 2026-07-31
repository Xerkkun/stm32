#!/usr/bin/env python3
"""Compare every portable C RHS with the independent Python equations."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
sys.path.insert(0, str(VALIDATION))

from abm_oracle import fractional_system_rhs  # noqa: E402
from alternative_systems import alternative_system_rhs  # noqa: E402


SYSTEM_NAMES = (
    "lorenz",
    "rossler",
    "chen",
    "liu",
    "hammouch_mekkaoui",
)


def load_parameters() -> dict[str, list[float]]:
    historical = json.loads(
        (
            VALIDATION / "candidate_manifests_rossler_classic_v2.json"
        ).read_text(encoding="utf-8")
    )["manifests"]
    selected_payload = json.loads(
        (
            VALIDATION / "selected_system_manifests_v1.json"
        ).read_text(encoding="utf-8")
    )
    selected = [
        entry["contract"] for entry in selected_payload["systems"]
    ]
    combined = {
        str(item["system"]): [float(value) for value in item["parameters"]]
        for item in historical + selected
    }
    return {name: combined[name] for name in SYSTEM_NAMES}


def right_hand_side(name: str, parameters: list[float]):
    if name in {"lorenz", "rossler", "chen"}:
        return fractional_system_rhs(name, parameters)
    return alternative_system_rhs(name, parameters)


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: rhs_contract_check.py RHS_DUMP_EXECUTABLE",
            file=sys.stderr,
        )
        return 2

    completed = subprocess.run(
        [str(Path(sys.argv[1]).resolve())],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    rows = list(csv.DictReader(completed.stdout.splitlines()))
    expected_rows = len(SYSTEM_NAMES) * 12
    if len(rows) != expected_rows:
        raise AssertionError(
            f"expected {expected_rows} RHS rows, got {len(rows)}"
        )

    parameters = load_parameters()
    failures: list[str] = []
    for row in rows:
        system_id = int(row["system"])
        name = SYSTEM_NAMES[system_id]
        state = [float(row[axis]) for axis in ("x", "y", "z")]
        observed = [float(row[axis]) for axis in ("dx", "dy", "dz")]
        expected = right_hand_side(name, parameters[name])(0.0, state)

        if int(row["status"]) != 0:
            failures.append(
                f"{name} case={row['case']}: C status={row['status']}"
            )
            continue
        for component, (got, wanted) in enumerate(
            zip(observed, expected, strict=True)
        ):
            tolerance = 3.0e-6 * max(1.0, abs(float(wanted)))
            if (
                not math.isfinite(got)
                or abs(got - float(wanted)) > tolerance
            ):
                failures.append(
                    f"{name} case={row['case']} component={component}: "
                    f"C={got:.9g}, Python={float(wanted):.17g}, "
                    f"tol={tolerance:.3g}"
                )

    if failures:
        print("RHS C/Python contract failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(
        "RHS C/Python contract passed: "
        f"{len(rows)} evaluations across {len(SYSTEM_NAMES)} systems"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
