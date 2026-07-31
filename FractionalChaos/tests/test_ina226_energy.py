from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "ina226_energy.py"
)
SPEC = importlib.util.spec_from_file_location("ina226_energy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def calibration_input() -> dict[str, Any]:
    return {
        "schema": MODULE.CALIBRATION_INPUT_SCHEMA,
        "calibration_id": "cal-test-f746",
        "sensor_id": "ina226_f746",
        "board": "f746",
        "i2c_address": "0x40",
        "identity": {
            "manufacturer_id": "0x5449",
            "die_id": "0x2260",
        },
        "shunt": {"marking": "R100", "nominal_ohms": 0.1},
        "current_points": [
            {"measured_shunt_raw": 0, "reference_current_a": 0.0},
            {"measured_shunt_raw": 4000, "reference_current_a": 0.1},
            {"measured_shunt_raw": 10000, "reference_current_a": 0.25},
            {"measured_shunt_raw": 20000, "reference_current_a": 0.5},
        ],
        "bus_voltage_points": [
            {"measured_bus_raw": 3200, "reference_bus_v": 4.0},
            {"measured_bus_raw": 4000, "reference_bus_v": 5.0},
        ],
        "uncertainty": {
            "expanded_relative_percent_k2": 0.8,
            "method": "synthetic test budget",
        },
        "provenance": {
            "timestamp_utc": "2026-07-29T12:00:00Z",
            "reference_instrument": "synthetic-test-reference",
            "operator": "pytest",
            "temperature_c": 25.0,
        },
    }


def write_calibration(tmp_path: Path, *, post: bool) -> Path:
    calibration = MODULE.build_calibration(calibration_input())
    if post:
        calibration["status"] = "measured_post_campaign"
        calibration["post_campaign_check"] = {
            "timestamp_utc": "2026-07-29T13:00:00Z",
            "reference_current_a": 0.5,
            "observed_current_a": 0.501,
        }
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(calibration, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_capture(
    tmp_path: Path,
    *,
    skip_sequences: set[int] | None = None,
    active_shunt_raw: int = 8000,
    include_clock_reference: bool = False,
    wire_terminal_complete: bool | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    skip_sequences = skip_sequences or set()
    rise_ns = 200_000_000
    fall_ns = 1_300_000_000
    records: list[dict[str, Any]] = [
        {
            "type": "header",
            "schema": MODULE.CAPTURE_SCHEMA,
            "capture_id": "capture-test-f746",
            "sensor_id": "ina226_f746",
            "board": "f746",
            "run_id": "run-test",
            "energy_scope": MODULE.ENERGY_SCOPE,
            "work_units": 10000,
            "work_unit_label": "solver_step",
            "quality_contract": {
                "expected_sample_period_ns": 1_000_000,
                "min_window_samples": 1000,
                "max_gap_periods": 2.0,
                "max_loss_percent": 0.1,
                "min_window_s": 0.5,
                "min_bus_voltage_v": 4.75,
                "saturation_guard_fraction": 0.9,
                "min_idle_samples_each_side": 100,
                "max_idle_drift_percent": 2.0,
            },
        },
    ]
    if wire_terminal_complete is not None:
        records[0]["evidence_status"] = "measured_raw_unvalidated"
        records[0]["publication_ready"] = False
        records[0]["acquisition"] = {
            "wire_protocol": "INA14/1",
            "sample_period_us": 1000,
        }
    if include_clock_reference:
        records.extend(
            [
                {
                    "type": "edge",
                    "timestamp_ns": rise_ns - 20_000_000,
                    "level": 1,
                    "marker_kind": "clock_reference",
                },
                {
                    "type": "edge",
                    "timestamp_ns": rise_ns - 10_000_000,
                    "level": 0,
                    "marker_kind": "clock_reference",
                },
            ]
        )
    records.append(
        {
            "type": "edge",
            "timestamp_ns": rise_ns,
            "level": 1,
            "marker_kind": "energy_window",
        }
    )
    for sequence in range(1501):
        if sequence in skip_sequences:
            continue
        timestamp_ns = sequence * 1_000_000
        active = rise_ns <= timestamp_ns <= fall_ns
        records.append(
            {
                "type": "sample",
                "timestamp_ns": timestamp_ns,
                "sequence": sequence,
                "bus_raw": 4000,
                "shunt_raw": active_shunt_raw if active else 4000,
                "conversion_ready": True,
                "math_overflow": False,
                "i2c_ok": True,
            }
        )
    records.append(
        {
            "type": "edge",
            "timestamp_ns": fall_ns,
            "level": 0,
            "marker_kind": "energy_window",
        }
    )
    if wire_terminal_complete is None:
        records.append({"type": "end"})
    else:
        records.append(
            {
                "type": "end",
                "complete": wire_terminal_complete,
                "stopped": not wire_terminal_complete,
                "timed_out": False,
                "i2c_error_flag": False,
                "edge_queue_overflow": False,
                "timing_or_edge_anomaly": False,
                "i2c_error_count": 0,
                "late_sample_slots": 0,
                "discarded_wire_bytes": 0,
                "crc_failures": 0,
                "sample_count": len(
                    [
                        record
                        for record in records
                        if record["type"] == "sample"
                    ]
                ),
                "energy_edge_count": len(
                    [
                        record
                        for record in records
                        if record["type"] == "edge"
                        and record.get(
                            "marker_kind",
                            "energy_window",
                        )
                        == "energy_window"
                    ]
                ),
                "publication_ready": False,
            }
        )
    path = tmp_path / "capture.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_build_calibration_fits_r100_points() -> None:
    calibration = MODULE.build_calibration(calibration_input())

    assert calibration["identity"]["compatibility_check_only"] is True
    assert calibration["identity"]["authenticity_claimed"] is False
    assert calibration["shunt"]["full_scale_current_a"] == pytest.approx(
        0.8192
    )
    assert calibration["current_from_shunt_voltage"]["slope"] == pytest.approx(
        10.0
    )
    assert calibration["current_from_shunt_voltage"]["offset"] == pytest.approx(
        0.0,
        abs=1e-12,
    )
    assert calibration["bus_voltage_from_register_voltage"][
        "slope"
    ] == pytest.approx(1.0)


def test_integrates_between_gpio_edges_and_subtracts_idle(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=True)
    capture_path = write_capture(tmp_path)

    result = MODULE.integrate_capture(capture_path, calibration_path)

    assert result["measurement_scope"] == {
        "name": MODULE.ENERGY_SCOPE,
        "includes_stlink": True,
        "mcu_core_only": False,
        "idle_corrected_is_still_not_mcu_core_only": True,
    }
    assert result["window"]["duration_s"] == pytest.approx(1.1)
    assert result["energy"]["total_board_energy_j"] == pytest.approx(1.1)
    assert result["energy"]["idle_energy_j"] == pytest.approx(0.55)
    assert result["energy"]["idle_corrected_board_energy_j"] == pytest.approx(
        0.55
    )
    assert result["quality"]["measurement_valid"] is True
    assert result["publication_ready"] is True


def test_missing_records_remain_visible_and_reject_measurement(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=True)
    capture_path = write_capture(
        tmp_path,
        skip_sequences={700, 701},
    )

    result = MODULE.integrate_capture(capture_path, calibration_path)

    assert result["quality"]["observed"]["missing_sequences"] == 2
    assert result["quality"]["flags"]["sequence_loss"] is False
    assert result["quality"]["flags"]["timestamp_gap"] is False
    assert result["quality"]["measurement_valid"] is False
    assert result["publication_ready"] is False


def test_r100_headroom_guard_rejects_near_full_scale_capture(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=True)
    capture_path = write_capture(tmp_path, active_shunt_raw=30000)

    result = MODULE.integrate_capture(capture_path, calibration_path)

    assert result["quality"]["flags"]["shunt_headroom"] is False
    assert result["quality"]["observed"]["saturation_limit_raw"] == math.floor(
        32767 * 0.9
    )
    assert result["publication_ready"] is False


def test_post_campaign_check_is_required_for_publication(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=False)
    capture_path = write_capture(tmp_path)

    result = MODULE.integrate_capture(capture_path, calibration_path)

    assert result["quality"]["measurement_valid"] is True
    assert result["calibration_quality"][
        "post_campaign_check_complete"
    ] is False
    assert result["publication_ready"] is False


def test_ids_cannot_be_promoted_to_authenticity_claim() -> None:
    calibration = MODULE.build_calibration(calibration_input())
    calibration["identity"]["authenticity_claimed"] = True

    with pytest.raises(MODULE.EnergyError, match="autenticidad"):
        MODULE.validate_calibration(calibration)


def test_clock_reference_edges_do_not_replace_energy_window(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=True)
    capture_path = write_capture(
        tmp_path,
        include_clock_reference=True,
    )

    result = MODULE.integrate_capture(capture_path, calibration_path)

    assert result["window"]["rise_timestamp_ns"] == 200_000_000
    assert result["window"]["fall_timestamp_ns"] == 1_300_000_000
    assert result["quality"]["measurement_valid"] is True


def test_ina14_terminal_state_is_part_of_measurement_validity(
    tmp_path: Path,
) -> None:
    calibration_path = write_calibration(tmp_path, post=True)
    complete_capture = write_capture(
        tmp_path / "complete",
        wire_terminal_complete=True,
    )
    stopped_capture = write_capture(
        tmp_path / "stopped",
        wire_terminal_complete=False,
    )

    complete = MODULE.integrate_capture(
        complete_capture,
        calibration_path,
    )
    stopped = MODULE.integrate_capture(
        stopped_capture,
        calibration_path,
    )

    assert complete["quality"]["flags"]["transport_complete"] is True
    assert complete["quality"]["measurement_valid"] is True
    assert stopped["quality"]["flags"]["transport_complete"] is False
    assert stopped["quality"]["measurement_valid"] is False
    assert stopped["publication_ready"] is False
