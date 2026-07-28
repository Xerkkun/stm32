from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "compare_fixed_captures.py"
)
SPEC = importlib.util.spec_from_file_location(
    "compare_fixed_captures",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIELDS = [
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
]


def write_capture(
    path: Path,
    *,
    board: str,
    rows: list[tuple[int, int, int, int]],
    status: int = 0,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for sequence, x_raw, y_raw, z_raw in rows:
            writer.writerow(
                {
                    "version": 1,
                    "kind": 3,
                    "board": board,
                    "system": "lorenz",
                    "method": "m2sfrk",
                    "representation": "fixed_q14",
                    "status": status,
                    "sequence": sequence,
                    "x_raw": x_raw,
                    "y_raw": y_raw,
                    "z_raw": z_raw,
                }
            )


def test_exact_overlap_passes_with_equal_hashes(tmp_path: Path) -> None:
    left = tmp_path / "f746.csv"
    right = tmp_path / "h755.csv"
    write_capture(
        left,
        board="f746",
        rows=[(1, 10, 20, 30), (2, 11, 21, 31), (3, 12, 22, 32)],
    )
    write_capture(
        right,
        board="h755",
        rows=[(2, 11, 21, 31), (3, 12, 22, 32), (4, 13, 23, 33)],
    )

    report = MODULE.compare_captures(left, right)
    assert report["status"] == "passed"
    assert report["requirements"] == {
        "identities_equal": True,
        "both_fixed_q14": True,
        "all_status_zero": True,
        "nonempty_overlap": True,
        "all_raw_states_equal": True,
    }
    assert report["overlap"]["samples"] == 2
    assert report["overlap"]["sequence_first"] == 2
    assert report["overlap"]["sequence_last"] == 3
    assert report["overlap"]["hashes_equal"] is True
    assert report["left"]["sha256"] != report["right"]["sha256"]


def test_mismatch_and_empty_overlap_fail(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    mismatch = tmp_path / "mismatch.csv"
    disjoint = tmp_path / "disjoint.csv"
    write_capture(left, board="f746", rows=[(8, 1, 2, 3)])
    write_capture(mismatch, board="h755", rows=[(8, 1, 2, 4)])
    write_capture(disjoint, board="h755", rows=[(9, 1, 2, 3)])

    mismatch_report = MODULE.compare_captures(left, mismatch)
    assert mismatch_report["status"] == "failed"
    assert mismatch_report["failures"] == ["raw_state_mismatch"]
    assert mismatch_report["overlap"]["mismatch_count"] == 1

    disjoint_report = MODULE.compare_captures(left, disjoint)
    assert disjoint_report["status"] == "failed"
    assert disjoint_report["failures"] == ["no_sequence_overlap"]


def test_cli_writes_failure_json_for_nonzero_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    output = tmp_path / "report.json"
    write_capture(left, board="f746", rows=[(1, 1, 2, 3)])
    write_capture(
        right,
        board="h755",
        rows=[(1, 1, 2, 3)],
        status=4,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(MODULE_PATH),
            str(left),
            str(right),
            "--output",
            str(output),
        ],
    )
    assert MODULE.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failures"] == ["right_nonzero_status"]
