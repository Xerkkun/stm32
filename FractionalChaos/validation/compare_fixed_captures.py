#!/usr/bin/env python3
"""Comprueba paridad física bit a bit entre dos capturas fixed_q14."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FIELDS = {
    "version",
    "kind",
    "board",
    "system",
    "method",
    "representation",
    "status",
    "sequence",
    "x_raw",
    "y_raw",
    "z_raw",
}
COMPARISON_IDENTITY_FIELDS = (
    "version",
    "kind",
    "system",
    "method",
    "representation",
)
RAW_FIELDS = ("x_raw", "y_raw", "z_raw")


class CaptureError(ValueError):
    """La captura no satisface el contrato estructural."""


@dataclass(frozen=True)
class Capture:
    path: Path
    sha256: str
    board: str
    identity: dict[str, str]
    rows: int
    sequence_min: int
    sequence_max: int
    status_counts: dict[str, int]
    states: dict[int, tuple[int, int, int]]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_bounded_int(
    text: str,
    *,
    field: str,
    row_number: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise CaptureError(
            f"fila {row_number}: {field} no es entero"
        ) from error
    if not minimum <= value <= maximum:
        raise CaptureError(
            f"fila {row_number}: {field} fuera de rango"
        )
    return value


def load_capture(path: Path) -> Capture:
    resolved = path.resolve()
    states: dict[int, tuple[int, int, int]] = {}
    status_counts: dict[str, int] = {}
    board: str | None = None
    identity: dict[str, str] | None = None

    with resolved.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise CaptureError("el CSV no contiene encabezado")
        missing = sorted(REQUIRED_FIELDS - set(reader.fieldnames))
        if missing:
            raise CaptureError(
                "faltan columnas obligatorias: " + ", ".join(missing)
            )

        for row_number, row in enumerate(reader, start=2):
            current_board = row["board"]
            current_identity = {
                field: row[field]
                for field in COMPARISON_IDENTITY_FIELDS
            }
            if board is None:
                board = current_board
                identity = current_identity
            elif current_board != board:
                raise CaptureError("la captura mezcla placas")
            elif current_identity != identity:
                raise CaptureError(
                    "la captura mezcla identidades experimentales"
                )

            sequence = parse_bounded_int(
                row["sequence"],
                field="sequence",
                row_number=row_number,
                minimum=0,
                maximum=(1 << 32) - 1,
            )
            if sequence in states:
                raise CaptureError(
                    f"sequence duplicado: {sequence}"
                )
            status = parse_bounded_int(
                row["status"],
                field="status",
                row_number=row_number,
                minimum=0,
                maximum=0xFF,
            )
            status_key = str(status)
            status_counts[status_key] = (
                status_counts.get(status_key, 0) + 1
            )
            states[sequence] = tuple(
                parse_bounded_int(
                    row[field],
                    field=field,
                    row_number=row_number,
                    minimum=-(1 << 31),
                    maximum=(1 << 31) - 1,
                )
                for field in RAW_FIELDS
            )

    if not states or board is None or identity is None:
        raise CaptureError("la captura no contiene muestras")
    return Capture(
        path=resolved,
        sha256=file_sha256(resolved),
        board=board,
        identity=identity,
        rows=len(states),
        sequence_min=min(states),
        sequence_max=max(states),
        status_counts=status_counts,
        states=states,
    )


def capture_metadata(capture: Capture) -> dict[str, object]:
    return {
        "path": str(capture.path),
        "sha256": capture.sha256,
        "board": capture.board,
        "rows": capture.rows,
        "sequence_first": capture.sequence_min,
        "sequence_last": capture.sequence_max,
        "status_counts": capture.status_counts,
    }


def overlap_sha256(
    sequences: list[int],
    states: dict[int, tuple[int, int, int]],
) -> str:
    digest = hashlib.sha256()
    for sequence in sequences:
        digest.update(
            struct.pack("<Iiii", sequence, *states[sequence])
        )
    return digest.hexdigest()


def compare_captures(left_path: Path, right_path: Path) -> dict[str, object]:
    left = load_capture(left_path)
    right = load_capture(right_path)
    failures: list[str] = []

    if left.identity != right.identity:
        failures.append("identity_mismatch")
    if left.identity["representation"] != "fixed_q14":
        failures.append("left_not_fixed_q14")
    if right.identity["representation"] != "fixed_q14":
        failures.append("right_not_fixed_q14")
    if left.status_counts != {"0": left.rows}:
        failures.append("left_nonzero_status")
    if right.status_counts != {"0": right.rows}:
        failures.append("right_nonzero_status")

    overlap = sorted(set(left.states) & set(right.states))
    mismatches: list[dict[str, object]] = []
    mismatch_count = 0
    for sequence in overlap:
        left_state = left.states[sequence]
        right_state = right.states[sequence]
        if left_state != right_state:
            mismatch_count += 1
            if len(mismatches) < 10:
                mismatches.append(
                    {
                        "sequence": sequence,
                        "left_raw": list(left_state),
                        "right_raw": list(right_state),
                    }
                )

    if not overlap:
        failures.append("no_sequence_overlap")
    if mismatch_count:
        failures.append("raw_state_mismatch")

    left_overlap_hash = (
        overlap_sha256(overlap, left.states) if overlap else None
    )
    right_overlap_hash = (
        overlap_sha256(overlap, right.states) if overlap else None
    )
    return {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "comparison": "fixed_q14_physical_bit_parity",
        "identity": left.identity if left.identity == right.identity else {
            "left": left.identity,
            "right": right.identity,
        },
        "left": capture_metadata(left),
        "right": capture_metadata(right),
        "requirements": {
            "identities_equal": left.identity == right.identity,
            "both_fixed_q14": (
                left.identity["representation"] == "fixed_q14"
                and right.identity["representation"] == "fixed_q14"
            ),
            "all_status_zero": (
                left.status_counts == {"0": left.rows}
                and right.status_counts == {"0": right.rows}
            ),
            "nonempty_overlap": bool(overlap),
            "all_raw_states_equal": mismatch_count == 0,
        },
        "overlap": {
            "samples": len(overlap),
            "sequence_first": overlap[0] if overlap else None,
            "sequence_last": overlap[-1] if overlap else None,
            "left_raw_sha256": left_overlap_hash,
            "right_raw_sha256": right_overlap_hash,
            "hashes_equal": (
                left_overlap_hash == right_overlap_hash
                if overlap
                else False
            ),
            "mismatch_count": mismatch_count,
            "first_mismatches": mismatches,
        },
        "failures": failures,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = compare_captures(args.left, args.right)
    except (CaptureError, OSError) as error:
        report = {
            "schema_version": 1,
            "status": "failed",
            "comparison": "fixed_q14_physical_bit_parity",
            "failures": ["invalid_input"],
            "error": str(error),
            "left": {
                "path": str(args.left.resolve()),
                "sha256": (
                    file_sha256(args.left.resolve())
                    if args.left.is_file()
                    else None
                ),
            },
            "right": {
                "path": str(args.right.resolve()),
                "sha256": (
                    file_sha256(args.right.resolve())
                    if args.right.is_file()
                    else None
                ),
            },
        }

    write_report(args.output, report)
    if report["status"] != "passed":
        print(
            f"fixed capture parity failed: {report['failures']}",
            file=sys.stderr,
        )
        return 1

    overlap = report["overlap"]
    assert isinstance(overlap, dict)
    print(
        "fixed capture parity passed: "
        f"{overlap['samples']} overlapping samples"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
