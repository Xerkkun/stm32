from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest


VALIDATION_ROOT = Path(__file__).parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

import compare_dense_capture_parity as module  # noqa: E402


FIELDS = (
    "version",
    "kind",
    "board",
    "system",
    "method",
    "representation",
    "status",
    "sequence",
    "cycles",
    "dropped",
    "x_bits",
    "y_bits",
    "z_bits",
)


def write_capture(
    path: Path,
    *,
    board: str,
    rows: list[tuple[int, int, int, int]],
    representation: str = "float32",
    kind: int = 1,
    cycles_offset: int = 0,
    status: int = 0,
    dropped: int = 0,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index, (sequence, x_word, y_word, z_word) in enumerate(rows):
            writer.writerow(
                {
                    "version": 1,
                    "kind": kind,
                    "board": board,
                    "system": "rossler",
                    "method": "m2sfrk",
                    "representation": representation,
                    "status": status,
                    "sequence": sequence,
                    "cycles": 300 + cycles_offset + index,
                    "dropped": dropped,
                    "x_bits": f"0x{x_word:08X}",
                    "y_bits": f"0x{y_word:08X}",
                    "z_bits": f"0x{z_word:08X}",
                }
            )


def sample_rows() -> list[tuple[int, int, int, int]]:
    return [
        (1, 0x3F800000, 0x3DCCCCCD, 0x3E4CCCCD),
        (2, 0x3F7FFFFF, 0x3E4CCCCD, 0x3E99999A),
        (3, 0x3F700000, 0x3E99999A, 0x3ECCCCCD),
    ]


def test_exact_words_pass_while_cycles_differ(tmp_path: Path) -> None:
    left = tmp_path / "f746.csv"
    right = tmp_path / "h755.csv"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(
        right,
        board="h755",
        rows=sample_rows(),
        cycles_offset=5000,
    )

    report = module.compare_captures(left, right)
    assert report["status"] == "passed"
    assert report["cycles_compared"] is False
    assert report["requirements"] == {
        "identities_equal": True,
        "boards_distinct": True,
        "both_sequences_consecutive": True,
        "row_counts_equal": True,
        "sequence_vectors_equal": True,
        "all_status_zero": True,
        "all_dropped_zero": True,
        "all_compared_fields_equal": True,
    }
    assert report["result"]["rows_compared"] == 3
    assert report["result"]["mismatch_rows"] == 0
    assert report["result"]["first_disagreement"] is None
    assert report["result"]["comparison_payload_hashes_equal"] is True
    assert report["left"]["sha256"] != report["right"]["sha256"]
    assert (
        report["left"]["comparison_payload_sha256"]
        == report["right"]["comparison_payload_sha256"]
    )


def test_first_word_disagreement_is_fully_reported(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    changed = sample_rows()
    changed[1] = (2, changed[1][1], changed[1][2], 0xDEADBEEF)
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(right, board="h755", rows=changed)

    report = module.compare_captures(left, right)
    assert report["status"] == "failed"
    assert report["failures"] == ["compared_fields_mismatch"]
    assert report["result"]["mismatch_rows"] == 1
    assert report["result"]["mismatch_counts_by_field"]["z_word"] == 1
    assert report["result"]["first_disagreement"] == {
        "row_index_zero_based": 1,
        "csv_row_number": 3,
        "differing_fields": ["z_word"],
        "left": {
            "sequence": 2,
            "x_word": 0x3F7FFFFF,
            "y_word": 0x3E4CCCCD,
            "z_word": 0x3E99999A,
            "status": 0,
            "dropped": 0,
        },
        "right": {
            "sequence": 2,
            "x_word": 0x3F7FFFFF,
            "y_word": 0x3E4CCCCD,
            "z_word": 0xDEADBEEF,
            "status": 0,
            "dropped": 0,
        },
    }


def test_sequence_range_and_identity_mismatch_fail(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    shifted = tmp_path / "shifted.csv"
    fixed = tmp_path / "fixed.csv"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(
        shifted,
        board="h755",
        rows=[
            (sequence + 1, x, y, z)
            for sequence, x, y, z in sample_rows()
        ],
    )
    write_capture(
        fixed,
        board="h755",
        rows=sample_rows(),
        representation="fixed_q14",
        kind=3,
    )

    shifted_report = module.compare_captures(left, shifted)
    assert shifted_report["status"] == "failed"
    assert "sequence_mismatch" in shifted_report["failures"]
    assert shifted_report["result"]["first_disagreement"][
        "differing_fields"
    ] == ["sequence"]

    fixed_report = module.compare_captures(left, fixed)
    assert fixed_report["status"] == "failed"
    assert fixed_report["failures"] == ["identity_mismatch"]


def test_row_count_mismatch_reports_missing_row(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(right, board="h755", rows=sample_rows()[:-1])

    report = module.compare_captures(left, right)
    assert report["status"] == "failed"
    assert report["failures"] == [
        "row_count_mismatch",
        "sequence_mismatch",
        "compared_fields_mismatch",
    ]
    assert report["result"]["rows_compared"] == 2
    assert report["result"]["mismatch_rows"] == 1
    assert report["result"]["first_disagreement"] == {
        "row_index_zero_based": 2,
        "csv_row_number": 4,
        "differing_fields": ["row_presence"],
        "left": {
            "sequence": 3,
            "x_word": 0x3F700000,
            "y_word": 0x3E99999A,
            "z_word": 0x3ECCCCCD,
            "status": 0,
            "dropped": 0,
        },
        "right": None,
    }


def test_same_board_fails(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(right, board="f746", rows=sample_rows())

    report = module.compare_captures(left, right)
    assert report["status"] == "failed"
    assert report["failures"] == ["boards_not_distinct"]


@pytest.mark.parametrize(
    ("status", "dropped", "expected"),
    (
        (4, 0, "right_nonzero_status"),
        (0, 1, "right_nonzero_dropped"),
    ),
)
def test_nonzero_transport_state_fails(
    tmp_path: Path,
    status: int,
    dropped: int,
    expected: str,
) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(
        right,
        board="h755",
        rows=sample_rows(),
        status=status,
        dropped=dropped,
    )
    report = module.compare_captures(left, right)
    assert report["status"] == "failed"
    assert expected in report["failures"]
    assert "compared_fields_mismatch" in report["failures"]


def test_gap_is_rejected_as_invalid_input(tmp_path: Path, monkeypatch) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    output = tmp_path / "report.json"
    rows_with_gap = sample_rows()
    rows_with_gap[2] = (
        4,
        rows_with_gap[2][1],
        rows_with_gap[2][2],
        rows_with_gap[2][3],
    )
    write_capture(left, board="f746", rows=rows_with_gap)
    write_capture(right, board="h755", rows=sample_rows())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(module.__file__),
            str(left),
            str(right),
            "--output",
            str(output),
        ],
    )
    assert module.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failures"] == ["invalid_input"]
    assert "no es consecutiva" in report["error"]


@pytest.mark.parametrize(
    ("malformed_text", "expected_error"),
    (
        (
            "version,kind,board,system,method,representation,status,"
            "sequence,dropped,x_bits,y_bits,z_bits\n"
            "1,1,f746,rossler,m2sfrk,float32,0,1,0,0x1,0x2\n",
            "faltan valores para z_bits",
        ),
        (
            "version,kind,board,system,method,representation,status,"
            "sequence,dropped,x_bits,x_bits,y_bits,z_bits\n",
            "columnas duplicadas en el encabezado: x_bits",
        ),
    ),
)
def test_malformed_csv_still_produces_failure_json(
    tmp_path: Path,
    monkeypatch,
    malformed_text: str,
    expected_error: str,
) -> None:
    left = tmp_path / "malformed.csv"
    right = tmp_path / "right.csv"
    output = tmp_path / "report.json"
    left.write_text(malformed_text, encoding="utf-8")
    write_capture(right, board="h755", rows=sample_rows())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(module.__file__),
            str(left),
            str(right),
            "--output",
            str(output),
        ],
    )

    assert module.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["failures"] == ["invalid_input"]
    assert expected_error in report["error"]


def test_json_output_is_deterministic(tmp_path: Path) -> None:
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_capture(left, board="f746", rows=sample_rows())
    write_capture(right, board="h755", rows=sample_rows())
    module.write_report(first, module.compare_captures(left, right))
    module.write_report(second, module.compare_captures(left, right))
    assert first.read_bytes() == second.read_bytes()
