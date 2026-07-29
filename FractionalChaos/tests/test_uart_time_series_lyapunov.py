"""Tests for the UART-derived time-series Lyapunov evidence lane."""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "validation" / "analyze_uart_time_series_lyapunov.py"
SPEC = importlib.util.spec_from_file_location("uart_time_series_lyapunov", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_capture(path: Path, *, gap_at: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "kind",
        "board",
        "system",
        "method",
        "representation",
        "status",
        "dropped",
        "sequence",
        "x",
    ]
    sequence = 512
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index in range(12):
            if gap_at is not None and index == gap_at:
                sequence += 512
            writer.writerow(
                {
                    "kind": 1,
                    "board": "f746",
                    "system": "lorenz",
                    "method": "m2sfrk",
                    "representation": "float32",
                    "status": 0,
                    "dropped": 0,
                    "sequence": sequence,
                    "x": np.sin(index),
                }
            )
            sequence += 512


def test_load_harmonized_window_uses_sequence_not_cycles(tmp_path: Path) -> None:
    case = module.CASES[0]
    capture_path = tmp_path / case.source
    write_capture(capture_path)

    result = module.load_harmonized_window(
        case,
        root=tmp_path,
        discard_raw_rows=2,
        window_samples=4,
    )

    assert result["raw_decimation"] == 512
    assert result["harmonization_stride"] == 2
    assert result["target_decimation"] == 1024
    assert np.allclose(result["signal"], np.sin([2, 4, 6, 8]))
    assert result["sequence_last"] - result["sequence_first"] == 3 * 1024
    assert result["model_time_span"] == pytest.approx(15.36)


def test_load_harmonized_window_rejects_sequence_gap(tmp_path: Path) -> None:
    case = module.CASES[0]
    capture_path = tmp_path / case.source
    write_capture(capture_path, gap_at=5)

    with pytest.raises(module.UartLyapunovError, match="sequence delta"):
        module.load_harmonized_window(
            case,
            root=tmp_path,
            discard_raw_rows=2,
            window_samples=4,
        )


def test_frozen_analysis_contract_is_cross_board_comparable() -> None:
    assert module.TARGET_DECIMATION == 1024
    assert module.SAMPLE_INTERVAL == pytest.approx(5.12)
    assert module.WINDOW_SAMPLES == 4096
    assert module.ROSENSTEIN_PARAMETERS["rosenstein_lag"] == 1
    assert module.ROSENSTEIN_PARAMETERS["rosenstein_fit"] == "poly"
    assert module.ECKMANN_PARAMETERS["eckmann_matrix_dim"] == 3
    assert module.ECKMANN_PARAMETERS["eckmann_min_tsep"] > 0
