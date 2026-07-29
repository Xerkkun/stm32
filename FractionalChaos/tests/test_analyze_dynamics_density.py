import csv
import json
import math
import struct
import sys
from pathlib import Path

import pytest


VALIDATION_ROOT = Path(__file__).parents[1] / "validation"
sys.path.insert(0, str(VALIDATION_ROOT))

import analyze_dynamics_density as module  # noqa: E402


FIELDS = (
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
    "x_bits",
    "y_bits",
    "z_bits",
    "x_raw",
    "y_raw",
    "z_raw",
)


def float_word(value: float) -> str:
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    return f"0x{bits:08X}"


def write_capture(
    path: Path,
    *,
    rows: int = 321,
    gap_at: int | None = None,
    status_at: int | None = None,
    dropped_at: int | None = None,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for index in range(rows):
            sequence = index + 1
            if gap_at is not None and index >= gap_at:
                sequence += 1
            radius = 1.0 + 0.2 * math.cos(2.0 * math.pi * index / 20.0)
            angle = 2.0 * math.pi * index / 17.0
            x = radius * math.cos(angle)
            y = radius * math.sin(angle)
            z = 0.3 * math.sin(2.0 * math.pi * index / 11.0)
            writer.writerow(
                {
                    "board": "f746",
                    "system": "rossler",
                    "method": "m2sfrk",
                    "representation": "float32",
                    "status": int(index == status_at),
                    "sequence": sequence,
                    "cycles": 325,
                    "dropped": int(index == dropped_at),
                    "x": x,
                    "y": y,
                    "z": z,
                    "x_bits": float_word(x),
                    "y_bits": float_word(y),
                    "z_bits": float_word(z),
                    "x_raw": "",
                    "y_raw": "",
                    "z_raw": "",
                }
            )


def test_report_is_deterministic_and_contains_requested_metrics(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "capture.csv"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_capture(capture)

    report = module.analyze_dynamics_density(
        capture,
        discard=10,
        sample_interval=0.05,
    )
    repeated = module.analyze_dynamics_density(
        capture,
        discard=10,
        sample_interval=0.05,
    )
    assert report == repeated
    module.write_report(first, report)
    module.write_report(second, repeated)
    assert first.read_bytes() == second.read_bytes()

    assert report["rows"] == {
        "total": 321,
        "discarded": 10,
        "retained": 311,
    }
    assert report["transport_and_state_validation"]["accepted"] is True
    assert report["state_summary"]["all_finite"] is True
    assert set(
        report["density"]["two_dimensional"]["projections"]
    ) == {"xy", "xz", "yz"}
    assert (
        0.0
        < report["density"]["two_dimensional"][
            "mean_occupancy_fraction"
        ]
        < 1.0
    )
    assert (
        0.0
        <= report["density"]["two_dimensional"][
            "mean_normalized_histogram_entropy"
        ]
        <= 1.0
    )
    assert (
        0.0
        < report["density"]["three_dimensional"][
            "occupancy_fraction"
        ]
        < 1.0
    )
    assert report["sampling"]["diagnostic_stride"] == 1
    assert (
        report["temporal_complexity"]["permutation_entropy"]["order"]
        == 5
    )
    assert (
        report["temporal_complexity"]["permutation_entropy"][
            "delay_s"
        ]
        == 0.1
    )
    assert report["temporal_complexity"]["lag_recurrence"][
        "lag_window_s"
    ] == [1.0, 15.0]
    assert report["radial_peaks"]["count"] == 15
    assert len(report["radial_peaks"]["amplitudes"]) == 15
    assert "chaos" in report["interpretation"]["does_not_establish"]


@pytest.mark.parametrize(
    ("keyword", "message"),
    (
        ("gap_at", "gaps"),
        ("status_at", "solver"),
        ("dropped_at", "dropped"),
    ),
)
def test_invalid_transport_or_solver_state_is_rejected(
    tmp_path: Path,
    keyword: str,
    message: str,
) -> None:
    capture = tmp_path / f"{keyword}.csv"
    write_capture(capture, **{keyword: 100})
    with pytest.raises(ValueError, match=message):
        module.analyze_dynamics_density(
            capture,
            discard=0,
            sample_interval=0.05,
        )


def test_sampling_contract_must_support_exact_diagnostic_period(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "capture.csv"
    write_capture(capture)
    with pytest.raises(ValueError, match="múltiplo entero"):
        module.analyze_dynamics_density(
            capture,
            discard=0,
            sample_interval=0.03,
        )


def test_retained_window_must_cover_recurrence_lags(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "short.csv"
    write_capture(capture, rows=300)
    with pytest.raises(ValueError, match="15 s"):
        module.analyze_dynamics_density(
            capture,
            discard=0,
            sample_interval=0.05,
        )


def test_cli_writes_json(tmp_path: Path, monkeypatch) -> None:
    capture = tmp_path / "capture.csv"
    output = tmp_path / "report.json"
    write_capture(capture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(module.__file__),
            "--capture",
            str(capture),
            "--output",
            str(output),
            "--discard",
            "0",
            "--sample-interval",
            "0.05",
        ],
    )
    assert module.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["rows"]["retained"] == 321
