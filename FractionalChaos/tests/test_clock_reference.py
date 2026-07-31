from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "clock_reference.py"
)
SPEC = importlib.util.spec_from_file_location("clock_reference", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIRMWARE_PROFILE_BY_BOARD = {
    "f746": "f746_216mhz",
    "h755": "h755_400mhz",
}


def capture_records(
    *,
    duration_ns: int = 100_000_000,
    clock_levels: tuple[int, ...] = (1, 0),
    controller_id: str | None = None,
    board: str = "f746",
) -> list[dict[str, Any]]:
    rise_ns = 50_000_000
    firmware_profile = FIRMWARE_PROFILE_BY_BOARD[board]
    frozen_profile = MODULE.FROZEN_FIRMWARE_CLOCK_PROFILES[
        firmware_profile
    ]
    header: dict[str, Any] = {
        "type": "header",
        "schema": MODULE.CAPTURE_SCHEMA,
        "capture_id": "capture-clock-test",
        "run_id": "run-clock-test",
        "board": board,
        "evidence_status": "measured_raw_unvalidated",
        "publication_ready": False,
        "clock_reference_contract": {
            "source": "host_selected_frozen_campaign_profile",
            "profile_is_measurement": False,
            "profile": firmware_profile,
            "board": board,
            "core_clock_hz": frozen_profile["core_clock_hz"],
            "expected_pulse_s": MODULE.EXPECTED_PULSE_S,
            "expected_cycles": frozen_profile["expected_cycles"],
        },
        "timebase": {
            "source": MODULE.TIMEBASE_SOURCE,
            "tick_ns": 1_000,
            "wrap_unwrapped_by_host": True,
            "not_host_wall_clock": True,
        },
        "acquisition": {
            "wire_protocol": MODULE.WIRE_PROTOCOL,
            "sample_period_us": 2_000,
        },
    }
    if controller_id is not None:
        header["timebase"]["controller_id"] = controller_id

    records: list[dict[str, Any]] = [
        header,
        {
            "type": "edge",
            "timestamp_ns": 10_000_000,
            "level": 1,
            "marker_kind": "energy_window",
        },
    ]
    for index, level in enumerate(clock_levels):
        records.append(
            {
                "type": "edge",
                "timestamp_ns": (
                    rise_ns if index == 0 else rise_ns + duration_ns + index - 1
                ),
                "level": level,
                "marker_kind": "clock_reference",
            }
        )
    records.extend(
        [
            {
                "type": "sample",
                "timestamp_ns": 20_000_000,
                "sequence": 0,
            },
            {
                "type": "sample",
                "timestamp_ns": 170_000_000,
                "sequence": 1,
            },
            {
                "type": "edge",
                "timestamp_ns": 180_000_000,
                "level": 0,
                "marker_kind": "energy_window",
            },
            {
                "type": "end",
                "complete": True,
                "stopped": False,
                "timed_out": False,
                "i2c_error_flag": False,
                "edge_queue_overflow": False,
                "timing_or_edge_anomaly": False,
                "i2c_error_count": 0,
                "late_sample_slots": 0,
                "discarded_wire_bytes": 0,
                "crc_failures": 0,
                "sample_count": 2,
                "clock_edge_count": len(clock_levels),
                "publication_ready": False,
            },
        ]
    )
    return records


def write_capture(
    tmp_path: Path,
    records: list[dict[str, Any]],
) -> Path:
    path = tmp_path / "clock_capture.jsonl"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def write_timebase_calibration(
    tmp_path: Path,
    *,
    controller_id: str = "uno-clock-test",
    scale: float = 1.0,
    include_traceability_artifact: bool = False,
    declared_artifact_sha256: str | None = None,
) -> Path:
    payload = {
        "schema": MODULE.TIMEBASE_CALIBRATION_SCHEMA,
        "status": "measured_traceable",
        "source": MODULE.TIMEBASE_SOURCE,
        "controller_id": controller_id,
        "calibration_id": "timebase-cal-test",
        "measured_at_utc": "2026-07-29T12:00:00Z",
        "reference": {
            "instrument": "synthetic frequency reference",
            "serial_number": "SYNTHETIC-TEST-ONLY",
            "certificate_id": "SYNTHETIC-CERT-TEST",
            "traceability_chain": "synthetic pytest fixture",
        },
        "seconds_per_reported_second": scale,
        "expanded_uncertainty_ppm_k2": 100.0,
    }
    if include_traceability_artifact:
        artifact_path = tmp_path / "synthetic_calibration_certificate.txt"
        artifact_path.write_text(
            "synthetic pytest traceability artifact; not physical evidence\n",
            encoding="utf-8",
        )
        payload["traceability_artifact"] = {
            "path": artifact_path.name,
            "sha256": (
                declared_artifact_sha256
                if declared_artifact_sha256 is not None
                else MODULE.sha256_file(artifact_path)
            ),
        }
    path = tmp_path / "timebase_calibration.json"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def test_computes_effective_clock_but_stays_diagnostic_without_calibration(
    tmp_path: Path,
) -> None:
    capture = write_capture(tmp_path, capture_records())

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
    )

    assert result["status"] == "accepted_diagnostic_clock_reference"
    assert result["observed"]["measured_duration_s"] == pytest.approx(0.1)
    assert result["clock"]["effective_core_clock_hz"] == pytest.approx(
        216_000_000.0
    )
    assert result["clock"]["formula"] == (
        "expected_cycles / measured_duration_s"
    )
    assert result["quality"]["validation_passed"] is True
    assert result["timebase"]["traceable_calibration"] is False
    assert result["timebase"]["calibration_applied"] is False
    assert result["publication_ready"] is False


def test_h755_current_profile_is_frozen_at_400_mhz(tmp_path: Path) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(board="h755"),
    )

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=40_000_000,
        firmware_profile="h755_400mhz",
    )

    assert result["contract"]["firmware_profile"] == "h755_400mhz"
    assert result["contract"]["frozen_core_clock_hz"] == 400_000_000
    assert result["clock"]["effective_core_clock_hz"] == pytest.approx(
        400_000_000.0
    )


@pytest.mark.parametrize(
    ("board", "expected_cycles"),
    [
        ("f746", 40_000_000),
        ("f746", 1),
        ("h755", 21_600_000),
        ("h755", 48_000_000),
    ],
)
def test_rejects_crossed_or_arbitrary_expected_cycles(
    tmp_path: Path,
    board: str,
    expected_cycles: int,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(board=board),
    )

    with pytest.raises(MODULE.ClockReferenceError, match="expected_cycles"):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=expected_cycles,
            firmware_profile=FIRMWARE_PROFILE_BY_BOARD[board],
        )


def test_rejects_firmware_profile_from_another_board(
    tmp_path: Path,
) -> None:
    capture = write_capture(tmp_path, capture_records(board="f746"))

    with pytest.raises(MODULE.ClockReferenceError, match="no corresponde"):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=40_000_000,
            firmware_profile="h755_400mhz",
        )


def test_requires_raw_clock_reference_contract(tmp_path: Path) -> None:
    records = capture_records()
    del records[0]["clock_reference_contract"]
    capture = write_capture(tmp_path, records)

    with pytest.raises(
        MODULE.ClockReferenceError,
        match="clock_reference_contract",
    ):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "measured_from_firmware"),
        ("profile_is_measurement", True),
        ("profile", "h755_400mhz"),
        ("core_clock_hz", 480_000_000),
        ("expected_pulse_s", 0.2),
        ("expected_cycles", 48_000_000),
        ("unexpected", "not_allowed"),
    ],
)
def test_rejects_tampered_raw_clock_reference_contract(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    records = capture_records()
    records[0]["clock_reference_contract"][field] = value
    capture = write_capture(tmp_path, records)

    with pytest.raises(
        MODULE.ClockReferenceError,
        match="clock_reference_contract",
    ):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
        )


@pytest.mark.parametrize(
    "levels",
    [
        (1,),
        (1, 0, 1),
        (0, 1),
    ],
)
def test_requires_exactly_one_rising_and_one_falling_edge(
    tmp_path: Path,
    levels: tuple[int, ...],
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(clock_levels=levels),
    )

    with pytest.raises(MODULE.ClockReferenceError):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
        )


def test_rejects_duration_outside_conservative_default(
    tmp_path: Path,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(duration_ns=103_000_000),
    )

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
    )

    assert result["status"] == "rejected"
    assert result["quality"]["duration_within_tolerance"] is False
    assert result["quality"]["validation_passed"] is False
    assert result["clock"]["effective_core_clock_hz"] == pytest.approx(
        21_600_000 / 0.103
    )
    assert result["publication_ready"] is False


def test_duration_tolerance_is_configurable_but_capped(
    tmp_path: Path,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(duration_ns=103_000_000),
    )

    accepted = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
        duration_tolerance_s=0.004,
    )
    assert accepted["quality"]["validation_passed"] is True

    with pytest.raises(MODULE.ClockReferenceError):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
            duration_tolerance_s=0.011,
        )


def test_dirty_ina14_terminal_blocks_validation(
    tmp_path: Path,
) -> None:
    records = capture_records()
    records[-1]["crc_failures"] = 1
    capture = write_capture(tmp_path, records)

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
    )

    assert result["quality"]["transport_flags"]["wire_crc"] is False
    assert result["quality"]["transport_valid"] is False
    assert result["quality"]["validation_passed"] is False
    assert result["publication_ready"] is False


def test_traceable_calibration_scales_duration_and_unlocks_result(
    tmp_path: Path,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(controller_id="uno-clock-test"),
    )
    calibration = write_timebase_calibration(
        tmp_path,
        scale=1.0005,
        include_traceability_artifact=True,
    )

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
        timebase_calibration_path=calibration,
    )

    assert result["observed"]["raw_duration_s"] == pytest.approx(0.1)
    assert result["observed"]["measured_duration_s"] == pytest.approx(
        0.10005
    )
    assert result["clock"]["effective_core_clock_hz"] == pytest.approx(
        21_600_000 / 0.10005
    )
    assert result["timebase"]["traceable_calibration"] is True
    assert (
        result["timebase"]["calibration"]["traceability_artifact"][
            "verification_status"
        ]
        == "sha256_verified"
    )
    assert result["publication_ready"] is True


def test_metadata_only_calibration_cannot_unlock_publication(
    tmp_path: Path,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(controller_id="uno-clock-test"),
    )
    calibration = write_timebase_calibration(tmp_path)

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
        timebase_calibration_path=calibration,
    )

    assert result["quality"]["validation_passed"] is True
    assert result["timebase"]["traceable_calibration"] is False
    assert result["timebase"]["calibration_applied"] is False
    assert result["observed"]["measured_duration_s"] == pytest.approx(0.1)
    assert (
        result["timebase"]["calibration"]["traceability_artifact"][
            "verification_status"
        ]
        == "missing_traceability_artifact"
    )
    assert result["publication_ready"] is False


def test_calibration_artifact_sha256_must_match(tmp_path: Path) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(controller_id="uno-clock-test"),
    )
    calibration = write_timebase_calibration(
        tmp_path,
        include_traceability_artifact=True,
        declared_artifact_sha256="0" * 64,
    )

    result = MODULE.validate_clock_reference(
        capture,
        expected_cycles=21_600_000,
        firmware_profile="f746_216mhz",
        timebase_calibration_path=calibration,
    )

    assert result["timebase"]["traceable_calibration"] is False
    assert result["timebase"]["calibration_applied"] is False
    assert (
        result["timebase"]["calibration"]["traceability_artifact"][
            "verification_status"
        ]
        == "traceability_artifact_sha256_mismatch"
    )
    assert result["publication_ready"] is False


def test_calibration_must_match_captured_controller(
    tmp_path: Path,
) -> None:
    capture = write_capture(
        tmp_path,
        capture_records(controller_id="uno-clock-test"),
    )
    calibration = write_timebase_calibration(
        tmp_path,
        controller_id="different-uno",
    )

    with pytest.raises(
        MODULE.ClockReferenceError,
        match="controller_id",
    ):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
            timebase_calibration_path=calibration,
        )


def test_requires_ina14_wire_protocol(tmp_path: Path) -> None:
    records = capture_records()
    records[0]["acquisition"]["wire_protocol"] = "OTHER/1"
    capture = write_capture(tmp_path, records)

    with pytest.raises(MODULE.ClockReferenceError, match="INA14/1"):
        MODULE.validate_clock_reference(
            capture,
            expected_cycles=21_600_000,
            firmware_profile="f746_216mhz",
        )
