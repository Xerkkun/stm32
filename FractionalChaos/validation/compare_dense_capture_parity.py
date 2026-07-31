#!/usr/bin/env python3
"""Compare exact dense-UART state words between two physical boards.

The comparison includes sequence, x/y/z words, solver status, and dropped
count.  DWT cycle counts are intentionally excluded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
REQUIRED_FIELDS = {
    "version",
    "kind",
    "board",
    "system",
    "method",
    "representation",
    "status",
    "sequence",
    "dropped",
    "x_bits",
    "y_bits",
    "z_bits",
}
IDENTITY_FIELDS = (
    "version",
    "kind",
    "system",
    "method",
    "representation",
)
COMPARISON_FIELDS = (
    "sequence",
    "x_word",
    "y_word",
    "z_word",
    "status",
    "dropped",
)
WORD_FIELDS = ("x_bits", "y_bits", "z_bits")


class CaptureError(ValueError):
    """Raised when a CSV violates the dense-capture input contract."""


@dataclass(frozen=True)
class ComparedRow:
    sequence: int
    x_word: int
    y_word: int
    z_word: int
    status: int
    dropped: int

    def as_dict(self) -> dict[str, int]:
        return {
            field: int(getattr(self, field))
            for field in COMPARISON_FIELDS
        }


@dataclass(frozen=True)
class Capture:
    path: Path
    sha256: str
    board: str
    identity: dict[str, str]
    rows: tuple[ComparedRow, ...]
    status_counts: dict[str, int]
    dropped_counts: dict[str, int]
    comparison_payload_sha256: str


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_decimal_uint(
    text: str,
    *,
    field: str,
    row_number: int,
    maximum: int,
) -> int:
    try:
        value = int(text, 10)
    except ValueError as error:
        raise CaptureError(
            f"fila {row_number}: {field} no es entero decimal"
        ) from error
    if not 0 <= value <= maximum:
        raise CaptureError(
            f"fila {row_number}: {field} fuera de rango"
        )
    return value


def parse_word(text: str, *, field: str, row_number: int) -> int:
    normalized = text.strip()
    base = 16 if normalized.lower().startswith("0x") else 10
    try:
        value = int(normalized, base)
    except ValueError as error:
        raise CaptureError(
            f"fila {row_number}: {field} no es una palabra válida"
        ) from error
    if not 0 <= value <= 0xFFFFFFFF:
        raise CaptureError(
            f"fila {row_number}: {field} fuera de uint32"
        )
    return value


def canonical_payload_sha256(rows: tuple[ComparedRow, ...]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            struct.pack(
                "<IIIIII",
                row.sequence,
                row.x_word,
                row.y_word,
                row.z_word,
                row.status,
                row.dropped,
            )
        )
    return digest.hexdigest()


def load_capture(path: Path) -> Capture:
    resolved = path.resolve()
    rows: list[ComparedRow] = []
    status_counts: dict[str, int] = {}
    dropped_counts: dict[str, int] = {}
    board: str | None = None
    identity: dict[str, str] | None = None

    with resolved.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise CaptureError("el CSV no contiene encabezado")
        duplicate_fields = sorted(
            field
            for field, count in Counter(reader.fieldnames).items()
            if count > 1
        )
        if duplicate_fields:
            raise CaptureError(
                "columnas duplicadas en el encabezado: "
                + ", ".join(duplicate_fields)
            )
        missing = sorted(REQUIRED_FIELDS - set(reader.fieldnames))
        if missing:
            raise CaptureError(
                "faltan columnas obligatorias: " + ", ".join(missing)
            )

        for row_number, row in enumerate(reader, start=2):
            missing_values = sorted(
                field
                for field in REQUIRED_FIELDS
                if row.get(field) is None
            )
            if missing_values:
                raise CaptureError(
                    f"fila {row_number}: faltan valores para "
                    + ", ".join(missing_values)
                )
            if None in row:
                raise CaptureError(
                    f"fila {row_number}: contiene columnas sin encabezado"
                )
            current_board = row["board"].strip()
            current_identity = {
                field: row[field].strip()
                for field in IDENTITY_FIELDS
            }
            if not current_board or any(
                not value for value in current_identity.values()
            ):
                raise CaptureError(
                    f"fila {row_number}: identidad incompleta"
                )
            if board is None:
                board = current_board
                identity = current_identity
            elif current_board != board:
                raise CaptureError("la captura mezcla placas")
            elif current_identity != identity:
                raise CaptureError(
                    "la captura mezcla identidades experimentales"
                )

            sequence = parse_decimal_uint(
                row["sequence"],
                field="sequence",
                row_number=row_number,
                maximum=0xFFFFFFFF,
            )
            status = parse_decimal_uint(
                row["status"],
                field="status",
                row_number=row_number,
                maximum=0xFFFFFFFF,
            )
            dropped = parse_decimal_uint(
                row["dropped"],
                field="dropped",
                row_number=row_number,
                maximum=0xFFFFFFFF,
            )
            words = tuple(
                parse_word(
                    row[field],
                    field=field,
                    row_number=row_number,
                )
                for field in WORD_FIELDS
            )
            rows.append(
                ComparedRow(
                    sequence=sequence,
                    x_word=words[0],
                    y_word=words[1],
                    z_word=words[2],
                    status=status,
                    dropped=dropped,
                )
            )
            status_key = str(status)
            dropped_key = str(dropped)
            status_counts[status_key] = (
                status_counts.get(status_key, 0) + 1
            )
            dropped_counts[dropped_key] = (
                dropped_counts.get(dropped_key, 0) + 1
            )

    if not rows or board is None or identity is None:
        raise CaptureError("la captura no contiene muestras")

    for index in range(1, len(rows)):
        previous = rows[index - 1].sequence
        current = rows[index].sequence
        if current != previous + 1:
            raise CaptureError(
                "la secuencia no es consecutiva: "
                f"{previous}->{current} en la fila CSV {index + 2}"
            )

    frozen_rows = tuple(rows)
    return Capture(
        path=resolved,
        sha256=file_sha256(resolved),
        board=board,
        identity=identity,
        rows=frozen_rows,
        status_counts=status_counts,
        dropped_counts=dropped_counts,
        comparison_payload_sha256=canonical_payload_sha256(frozen_rows),
    )


def capture_metadata(capture: Capture) -> dict[str, Any]:
    return {
        "path": portable_path(capture.path),
        "sha256": capture.sha256,
        "board": capture.board,
        "rows": len(capture.rows),
        "sequence_first": capture.rows[0].sequence,
        "sequence_last": capture.rows[-1].sequence,
        "sequence_increment": 1,
        "status_counts": capture.status_counts,
        "dropped_counts": capture.dropped_counts,
        "comparison_payload_sha256": (
            capture.comparison_payload_sha256
        ),
    }


def first_disagreement(
    left: Capture,
    right: Capture,
) -> tuple[int, dict[str, Any] | None, dict[str, int]]:
    mismatch_rows = 0
    first: dict[str, Any] | None = None
    field_counts = {field: 0 for field in COMPARISON_FIELDS}
    maximum_rows = max(len(left.rows), len(right.rows))

    for index in range(maximum_rows):
        left_row = left.rows[index] if index < len(left.rows) else None
        right_row = right.rows[index] if index < len(right.rows) else None
        if left_row is None or right_row is None:
            mismatch_rows += 1
            if first is None:
                first = {
                    "row_index_zero_based": index,
                    "csv_row_number": index + 2,
                    "differing_fields": ["row_presence"],
                    "left": (
                        left_row.as_dict()
                        if left_row is not None
                        else None
                    ),
                    "right": (
                        right_row.as_dict()
                        if right_row is not None
                        else None
                    ),
                }
            continue

        differing = [
            field
            for field in COMPARISON_FIELDS
            if getattr(left_row, field) != getattr(right_row, field)
        ]
        if not differing:
            continue
        mismatch_rows += 1
        for field in differing:
            field_counts[field] += 1
        if first is None:
            first = {
                "row_index_zero_based": index,
                "csv_row_number": index + 2,
                "differing_fields": differing,
                "left": left_row.as_dict(),
                "right": right_row.as_dict(),
            }

    return mismatch_rows, first, field_counts


def compare_captures(
    left_path: Path,
    right_path: Path,
) -> dict[str, Any]:
    left = load_capture(left_path)
    right = load_capture(right_path)
    mismatch_rows, first, field_counts = first_disagreement(left, right)

    identities_equal = left.identity == right.identity
    boards_distinct = left.board != right.board
    row_counts_equal = len(left.rows) == len(right.rows)
    sequence_vectors_equal = (
        row_counts_equal
        and all(
            left_row.sequence == right_row.sequence
            for left_row, right_row in zip(
                left.rows,
                right.rows,
                strict=True,
            )
        )
    )
    all_status_zero = (
        left.status_counts == {"0": len(left.rows)}
        and right.status_counts == {"0": len(right.rows)}
    )
    all_dropped_zero = (
        left.dropped_counts == {"0": len(left.rows)}
        and right.dropped_counts == {"0": len(right.rows)}
    )
    all_compared_fields_equal = mismatch_rows == 0

    failures: list[str] = []
    if not identities_equal:
        failures.append("identity_mismatch")
    if not boards_distinct:
        failures.append("boards_not_distinct")
    if not row_counts_equal:
        failures.append("row_count_mismatch")
    if not sequence_vectors_equal:
        failures.append("sequence_mismatch")
    if left.status_counts != {"0": len(left.rows)}:
        failures.append("left_nonzero_status")
    if right.status_counts != {"0": len(right.rows)}:
        failures.append("right_nonzero_status")
    if left.dropped_counts != {"0": len(left.rows)}:
        failures.append("left_nonzero_dropped")
    if right.dropped_counts != {"0": len(right.rows)}:
        failures.append("right_nonzero_dropped")
    if not all_compared_fields_equal:
        failures.append("compared_fields_mismatch")

    return {
        "schema": "fractional-chaos-dense-capture-parity-v1",
        "status": "passed" if not failures else "failed",
        "comparison": "cross_board_exact_dense_uart_word_parity",
        "comparison_fields": list(COMPARISON_FIELDS),
        "cycles_compared": False,
        "identity": (
            left.identity
            if identities_equal
            else {
                "left": left.identity,
                "right": right.identity,
            }
        ),
        "left": capture_metadata(left),
        "right": capture_metadata(right),
        "requirements": {
            "identities_equal": identities_equal,
            "boards_distinct": boards_distinct,
            "both_sequences_consecutive": True,
            "row_counts_equal": row_counts_equal,
            "sequence_vectors_equal": sequence_vectors_equal,
            "all_status_zero": all_status_zero,
            "all_dropped_zero": all_dropped_zero,
            "all_compared_fields_equal": all_compared_fields_equal,
        },
        "result": {
            "rows_compared": min(len(left.rows), len(right.rows)),
            "mismatch_rows": mismatch_rows,
            "mismatch_counts_by_field": field_counts,
            "first_disagreement": first,
            "comparison_payload_hashes_equal": (
                left.comparison_payload_sha256
                == right.comparison_payload_sha256
            ),
        },
        "failures": failures,
        "interpretation": {
            "evidence_layer": "observed_cross_board_dense_uart_parity",
            "statement": (
                "The report compares the declared words and transport-state "
                "fields for the captured row interval."
            ),
            "does_not_establish": [
                "cycle-count equality",
                "timing equivalence",
                "equivalence outside the captured interval",
                "chaos",
                "randomness",
            ],
        },
    }


def input_metadata(path: Path) -> dict[str, str | None]:
    resolved = path.resolve()
    return {
        "path": portable_path(resolved),
        "sha256": file_sha256(resolved) if resolved.is_file() else None,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        report = compare_captures(args.left, args.right)
    except (CaptureError, OSError) as error:
        report = {
            "schema": "fractional-chaos-dense-capture-parity-v1",
            "status": "failed",
            "comparison": "cross_board_exact_dense_uart_word_parity",
            "comparison_fields": list(COMPARISON_FIELDS),
            "cycles_compared": False,
            "left": input_metadata(args.left),
            "right": input_metadata(args.right),
            "failures": ["invalid_input"],
            "error": str(error),
        }

    write_report(args.output.resolve(), report)
    if report["status"] != "passed":
        print(
            f"dense capture parity failed: {report['failures']}",
            file=sys.stderr,
        )
        return 1

    result = report["result"]
    assert isinstance(result, dict)
    print(
        "dense capture parity passed: "
        f"{result['rows_compared']} exact rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
