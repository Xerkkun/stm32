import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


MODULE_PATH = (
    Path(__file__).parents[1] / "validation" / "analyze_capture.py"
)
SPEC = importlib.util.spec_from_file_location("analyze_capture", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_lsb_extractors_preserve_xyz_order() -> None:
    arrays = {
        "sequence": np.asarray([1], dtype=np.uint32),
        "x": np.asarray([1.25]),
        "y": np.asarray([-0.5]),
        "z": np.asarray([0.75]),
        "x_bits": np.asarray([0x3FA000A1], dtype=np.uint32),
        "y_bits": np.asarray([0xBF0000B2], dtype=np.uint32),
        "z_bits": np.asarray([0x3F4000C3], dtype=np.uint32),
        "x_raw": np.asarray([0x140], dtype=np.int32),
        "y_raw": np.asarray([-0x80], dtype=np.int32),
        "z_raw": np.asarray([0xC0], dtype=np.int32),
    }

    fixed = MODULE.fixed_point_lsb_bytes(
        arrays,
        fractional_bits=8,
        lsb_bits=8,
    )
    raw = MODULE.float_word_lsb_bytes(arrays, lsb_bits=8)
    fixed_raw = MODULE.fixed_raw_lsb_bytes(arrays, lsb_bits=8)

    assert fixed == bytes((0x40, 0x80, 0xC0))
    assert raw == bytes((0xA1, 0xB2, 0xC3))
    assert fixed_raw == bytes((0x40, 0x80, 0xC0))


def test_fixed_projection_uses_ties_away_from_zero() -> None:
    arrays = {
        "sequence": np.asarray([1], dtype=np.uint32),
        "x": np.asarray([0.5 / 256.0]),
        "y": np.asarray([-0.5 / 256.0]),
        "z": np.asarray([1.5 / 256.0]),
    }
    assert MODULE.fixed_point_lsb_bytes(
        arrays, fractional_bits=8, lsb_bits=8
    ) == bytes((1, 0xFF, 2))


def test_status_flags_are_counted_for_capture_rejection() -> None:
    summary = MODULE.summarize_status_flags(
        np.asarray([0x00, 0x04, 0x0C, 0x10, 0x80], dtype=np.uint32)
    )
    assert summary["all_samples_ok"] is False
    assert summary["nonzero_status_samples"] == 4
    assert summary["counts_by_numeric_status"] == {
        "0": 1,
        "4": 1,
        "12": 1,
        "16": 1,
        "128": 1,
    }
    assert summary["samples_by_flag"] == {
        "nonfinite": 0,
        "queue": 0,
        "fixed_state_saturation": 2,
        "fixed_coefficient_saturation": 1,
        "fixed_coefficient_zeroed": 1,
    }
    assert summary["samples_with_unknown_flags"] == 1


def test_load_capture_rejects_mixed_identity(tmp_path: Path) -> None:
    capture = tmp_path / "mixed.csv"
    fieldnames = [
        "board",
        "system",
        "method",
        "status",
        "sequence",
        "cycles",
        "x",
        "y",
        "z",
        "x_bits",
        "y_bits",
        "z_bits",
    ]
    with capture.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for board in ("f746", "h755"):
            writer.writerow(
                {
                    "board": board,
                    "system": "lorenz",
                    "method": "efork3",
                    "status": 0,
                    "sequence": 1,
                    "cycles": 10,
                    "x": 1.0,
                    "y": 2.0,
                    "z": 3.0,
                    "x_bits": "0x3F800000",
                    "y_bits": "0x40000000",
                    "z_bits": "0x40400000",
                }
            )

    try:
        MODULE.load_capture(capture)
    except ValueError as error:
        assert "mezcla" in str(error)
    else:
        raise AssertionError("se esperaba rechazo de identidad mixta")


def test_flagged_capture_is_ineligible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    capture = tmp_path / "flagged.csv"
    output = tmp_path / "evidence"
    fieldnames = [
        "board",
        "system",
        "method",
        "representation",
        "status",
        "sequence",
        "cycles",
        "dropped",
        "x",
        "y",
        "z",
        "x_raw",
        "y_raw",
        "z_raw",
        "x_bits",
        "y_bits",
        "z_bits",
    ]
    with capture.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for sequence, status in ((1, 0), (2, 0x1C)):
            writer.writerow(
                {
                    "board": "f746",
                    "system": "lorenz",
                    "method": "m2sfrk",
                    "representation": "fixed_q14",
                    "status": status,
                    "sequence": sequence,
                    "cycles": 100,
                    "dropped": 0,
                    "x": 1.0,
                    "y": 2.0,
                    "z": 3.0,
                    "x_raw": 16384,
                    "y_raw": 32768,
                    "z_raw": 49152,
                    "x_bits": "0x00004000",
                    "y_bits": "0x00008000",
                    "z_bits": "0x0000C000",
                }
            )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(MODULE_PATH),
            str(capture),
            "--output-dir",
            str(output),
            "--mode",
            "fixed-raw",
            "--expected-decimation",
            "1",
            "--minimum-statistical-bits",
            "1",
            "--sample-interval",
            "0.005",
        ],
    )
    assert MODULE.main() == 0
    metadata_path = next(output.glob("*_analysis.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["solver_status"]["all_samples_ok"] is False
    assert metadata["solver_status"]["samples_by_flag"] == {
        "nonfinite": 0,
        "queue": 0,
        "fixed_state_saturation": 1,
        "fixed_coefficient_saturation": 1,
        "fixed_coefficient_zeroed": 1,
    }
    assert (
        metadata["statistical_eligibility"][
            "eligible_for_configured_screening"
        ]
        is False
    )
    assert (
        metadata["statistical_eligibility"]["reason"]
        == "nonzero_solver_status"
    )
    assert metadata["sampling"] == {
        "sample_interval": 0.005,
        "time_unit": "model_time",
        "time_origin_sequence": 1,
        "host_reception_time_used": False,
        "interpolation": False,
        "resampling": False,
    }
    assert metadata["outputs"]["dynamics_plot_style"] == {
        "color": "#1F4E79",
        "time_series_line_width_points": 1.15,
        "attractor_point_area_points_squared": 2.4,
        "attractor_point_alpha": 0.52,
    }
