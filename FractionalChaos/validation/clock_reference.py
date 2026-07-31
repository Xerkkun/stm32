#!/usr/bin/env python3
"""Validate the 100 ms clock-reference pulse in an INA14/1 JSONL capture.

The raw timestamps come from ``Arduino micros()``.  Without a separate,
traceable timebase calibration, the derived core clock is diagnostic only and
must not be promoted as publication-ready evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


CAPTURE_SCHEMA = "fractional-chaos-ina226-capture-v1"
RESULT_SCHEMA = "fractional-chaos-clock-reference-result-v1"
TIMEBASE_CALIBRATION_SCHEMA = (
    "fractional-chaos-arduino-timebase-calibration-v1"
)
WIRE_PROTOCOL = "INA14/1"
TIMEBASE_SOURCE = "arduino_uno_micros"

EXPECTED_PULSE_S = 0.100
DEFAULT_DURATION_TOLERANCE_S = 0.001
MAX_DURATION_TOLERANCE_S = 0.010

FROZEN_FIRMWARE_CLOCK_PROFILES: dict[str, dict[str, Any]] = {
    "f746_216mhz": {
        "board": "f746",
        "core_clock_hz": 216_000_000,
        "expected_pulse_s": EXPECTED_PULSE_S,
        "expected_cycles": 21_600_000,
    },
    "h755_400mhz": {
        "board": "h755",
        "core_clock_hz": 400_000_000,
        "expected_pulse_s": EXPECTED_PULSE_S,
        "expected_cycles": 40_000_000,
    },
}


class ClockReferenceError(RuntimeError):
    """Raised when a capture violates the clock-reference contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ClockReferenceError(message)


def as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClockReferenceError(f"{field} debe ser un entero")
    return value


def as_finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ClockReferenceError(f"{field} no puede ser booleano")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ClockReferenceError(f"{field} debe ser numérico") from exc
    if not math.isfinite(result):
        raise ClockReferenceError(f"{field} debe ser finito")
    return result


def require_nonempty_string(value: Any, field: str) -> str:
    require(
        isinstance(value, str) and bool(value.strip()),
        f"{field} es obligatorio",
    )
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClockReferenceError(
            f"no se pudo leer JSON válido: {path}"
        ) from exc
    require(isinstance(payload, dict), f"{path} debe contener un objeto")
    return payload


def load_ina14_jsonl(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Load the bounded subset of INA14/1 records needed by this validator."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ClockReferenceError(
            f"no se pudo leer la captura: {path}"
        ) from exc

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClockReferenceError(
                f"JSONL inválido en {path}:{line_number}"
            ) from exc
        require(
            isinstance(record, dict),
            f"{path}:{line_number} debe contener un objeto",
        )
        require(
            record.get("type") in {"header", "sample", "edge", "end"},
            f"tipo de registro desconocido en {path}:{line_number}",
        )
        records.append(record)

    require(records, "la captura está vacía")
    require(records[0].get("type") == "header", "header debe ser el primero")
    require(records[-1].get("type") == "end", "end debe ser el último")
    require(
        sum(record.get("type") == "header" for record in records) == 1,
        "la captura debe contener exactamente un header",
    )
    require(
        sum(record.get("type") == "end" for record in records) == 1,
        "la captura debe contener exactamente un end",
    )

    header = records[0]
    terminal = records[-1]
    require(
        header.get("schema") == CAPTURE_SCHEMA,
        f"header.schema debe ser {CAPTURE_SCHEMA}",
    )
    require(
        header.get("evidence_status") == "measured_raw_unvalidated",
        "la captura debe permanecer measured_raw_unvalidated",
    )
    require(
        header.get("publication_ready") is False,
        "el header crudo no puede marcarse publication_ready",
    )
    acquisition = header.get("acquisition")
    require(isinstance(acquisition, dict), "header.acquisition es obligatorio")
    require(
        acquisition.get("wire_protocol") == WIRE_PROTOCOL,
        f"acquisition.wire_protocol debe ser {WIRE_PROTOCOL}",
    )

    timebase = header.get("timebase")
    require(isinstance(timebase, dict), "header.timebase es obligatorio")
    require(
        timebase.get("source") == TIMEBASE_SOURCE,
        f"timebase.source debe ser {TIMEBASE_SOURCE}",
    )
    require(
        as_int(timebase.get("tick_ns"), "timebase.tick_ns") > 0,
        "timebase.tick_ns debe ser positivo",
    )
    require(
        timebase.get("wrap_unwrapped_by_host") is True,
        "la captura debe desenvolver micros() en el host",
    )
    require(
        timebase.get("not_host_wall_clock") is True,
        "la base no puede declararse reloj de pared del host",
    )

    for field in (
        "complete",
        "stopped",
        "timed_out",
        "i2c_error_flag",
        "edge_queue_overflow",
        "timing_or_edge_anomaly",
    ):
        require(
            isinstance(terminal.get(field), bool),
            f"end.{field} debe ser booleano",
        )
    for field in (
        "i2c_error_count",
        "late_sample_slots",
        "discarded_wire_bytes",
        "crc_failures",
        "sample_count",
        "clock_edge_count",
    ):
        require(
            as_int(terminal.get(field), f"end.{field}") >= 0,
            f"end.{field} no puede ser negativo",
        )
    require(
        terminal.get("publication_ready") is False,
        "el registro end crudo no puede marcarse publication_ready",
    )

    return header, records[1:-1], terminal


def validate_timebase_calibration(
    path: Path,
    *,
    capture_timebase: dict[str, Any],
) -> dict[str, Any]:
    """Validate a separately measured and traceable Arduino timebase artifact."""

    payload = load_json(path)
    require(
        payload.get("schema") == TIMEBASE_CALIBRATION_SCHEMA,
        f"schema de calibración debe ser {TIMEBASE_CALIBRATION_SCHEMA}",
    )
    require(
        payload.get("status") == "measured_traceable",
        "la calibración temporal debe ser measured_traceable",
    )
    require(
        payload.get("source") == TIMEBASE_SOURCE,
        f"calibration.source debe ser {TIMEBASE_SOURCE}",
    )
    controller_id = require_nonempty_string(
        payload.get("controller_id"),
        "calibration.controller_id",
    )
    require(
        capture_timebase.get("controller_id") == controller_id,
        "controller_id de captura y calibración no coincide",
    )
    calibration_id = require_nonempty_string(
        payload.get("calibration_id"),
        "calibration.calibration_id",
    )
    require_nonempty_string(
        payload.get("measured_at_utc"),
        "calibration.measured_at_utc",
    )

    reference = payload.get("reference")
    require(
        isinstance(reference, dict),
        "calibration.reference es obligatorio",
    )
    for field in (
        "instrument",
        "serial_number",
        "certificate_id",
        "traceability_chain",
    ):
        require_nonempty_string(
            reference.get(field),
            f"calibration.reference.{field}",
        )

    seconds_per_reported_second = as_finite_float(
        payload.get("seconds_per_reported_second"),
        "calibration.seconds_per_reported_second",
    )
    require(
        0.95 <= seconds_per_reported_second <= 1.05,
        "seconds_per_reported_second fuera del intervalo conservador",
    )
    uncertainty_ppm = as_finite_float(
        payload.get("expanded_uncertainty_ppm_k2"),
        "calibration.expanded_uncertainty_ppm_k2",
    )
    require(
        0.0 <= uncertainty_ppm <= 10_000.0,
        "expanded_uncertainty_ppm_k2 fuera de rango",
    )

    artifact_result: dict[str, Any] = {
        "verified": False,
        "verification_status": "missing_traceability_artifact",
        "path": None,
        "declared_sha256": None,
        "computed_sha256": None,
    }
    artifact = payload.get("traceability_artifact")
    if artifact is not None:
        if not isinstance(artifact, dict):
            artifact_result["verification_status"] = (
                "invalid_traceability_artifact_declaration"
            )
        else:
            artifact_path_value = artifact.get("path")
            declared_sha256 = artifact.get("sha256")
            if (
                not isinstance(artifact_path_value, str)
                or not artifact_path_value.strip()
                or not isinstance(declared_sha256, str)
                or len(declared_sha256) != 64
                or any(
                    character not in "0123456789abcdefABCDEF"
                    for character in declared_sha256
                )
            ):
                artifact_result["verification_status"] = (
                    "invalid_traceability_artifact_declaration"
                )
            else:
                artifact_path = Path(artifact_path_value)
                if not artifact_path.is_absolute():
                    artifact_path = path.parent / artifact_path
                artifact_path = artifact_path.resolve()
                artifact_result["path"] = artifact_path.as_posix()
                artifact_result["declared_sha256"] = declared_sha256.lower()
                if not artifact_path.is_file():
                    artifact_result["verification_status"] = (
                        "traceability_artifact_not_found"
                    )
                else:
                    try:
                        computed_sha256 = sha256_file(artifact_path)
                    except OSError:
                        artifact_result["verification_status"] = (
                            "traceability_artifact_unreadable"
                        )
                    else:
                        artifact_result["computed_sha256"] = computed_sha256
                        if computed_sha256 == declared_sha256.lower():
                            artifact_result["verified"] = True
                            artifact_result["verification_status"] = (
                                "sha256_verified"
                            )
                        else:
                            artifact_result["verification_status"] = (
                                "traceability_artifact_sha256_mismatch"
                            )

    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "calibration_id": calibration_id,
        "controller_id": controller_id,
        "traceable": artifact_result["verified"],
        "seconds_per_reported_second": seconds_per_reported_second,
        "expanded_uncertainty_ppm_k2": uncertainty_ppm,
        "traceability_artifact": artifact_result,
    }


def validate_clock_reference(
    capture_path: Path,
    *,
    expected_cycles: int,
    firmware_profile: str,
    duration_tolerance_s: float = DEFAULT_DURATION_TOLERANCE_S,
    timebase_calibration_path: Path | None = None,
) -> dict[str, Any]:
    """Validate one rising/falling reference pulse and estimate core clock."""

    expected_cycles = as_int(expected_cycles, "expected_cycles")
    duration_tolerance_s = as_finite_float(
        duration_tolerance_s,
        "duration_tolerance_s",
    )
    require(
        0.0 < duration_tolerance_s <= MAX_DURATION_TOLERANCE_S,
        "duration_tolerance_s debe ser positiva y no superar 10 ms",
    )

    capture_path = capture_path.resolve()
    header, body, terminal = load_ina14_jsonl(capture_path)
    board = require_nonempty_string(header.get("board"), "header.board")
    firmware_profile = require_nonempty_string(
        firmware_profile,
        "firmware_profile",
    )
    require(
        firmware_profile in FROZEN_FIRMWARE_CLOCK_PROFILES,
        f"firmware_profile no está congelado: {firmware_profile}",
    )
    frozen_profile = FROZEN_FIRMWARE_CLOCK_PROFILES[firmware_profile]
    require(
        frozen_profile["board"] == board,
        (
            f"firmware_profile={firmware_profile} no corresponde "
            f"a board={board}"
        ),
    )
    header_contract = header.get("clock_reference_contract")
    require(
        isinstance(header_contract, dict),
        "header.clock_reference_contract es obligatorio",
    )
    expected_header_contract = {
        "source": "host_selected_frozen_campaign_profile",
        "profile_is_measurement": False,
        "profile": firmware_profile,
        "board": board,
        "core_clock_hz": frozen_profile["core_clock_hz"],
        "expected_pulse_s": frozen_profile["expected_pulse_s"],
        "expected_cycles": frozen_profile["expected_cycles"],
    }
    require(
        set(header_contract) == set(expected_header_contract),
        "header.clock_reference_contract debe tener el esquema exacto",
    )
    require_nonempty_string(
        header_contract.get("source"),
        "header.clock_reference_contract.source",
    )
    require(
        header_contract.get("profile_is_measurement") is False,
        "header.clock_reference_contract.profile_is_measurement debe ser false",
    )
    require_nonempty_string(
        header_contract.get("profile"),
        "header.clock_reference_contract.profile",
    )
    require_nonempty_string(
        header_contract.get("board"),
        "header.clock_reference_contract.board",
    )
    as_int(
        header_contract.get("core_clock_hz"),
        "header.clock_reference_contract.core_clock_hz",
    )
    as_finite_float(
        header_contract.get("expected_pulse_s"),
        "header.clock_reference_contract.expected_pulse_s",
    )
    as_int(
        header_contract.get("expected_cycles"),
        "header.clock_reference_contract.expected_cycles",
    )
    require(
        header_contract == expected_header_contract,
        (
            "header.clock_reference_contract no coincide con "
            "board, perfil y ciclos congelados"
        ),
    )
    require(
        expected_cycles == frozen_profile["expected_cycles"],
        (
            f"expected_cycles debe ser {frozen_profile['expected_cycles']} "
            f"para firmware_profile={firmware_profile}"
        ),
    )
    clock_edges = [
        record
        for record in body
        if record.get("type") == "edge"
        and record.get("marker_kind") == "clock_reference"
    ]
    require(
        len(clock_edges) == 2,
        "se requieren exactamente dos flancos clock_reference",
    )
    levels = [
        as_int(record.get("level"), f"clock_edges[{index}].level")
        for index, record in enumerate(clock_edges)
    ]
    require(
        levels == [1, 0],
        "clock_reference debe contener subida seguida de bajada",
    )
    rise_ns = as_int(
        clock_edges[0].get("timestamp_ns"),
        "clock_edges[0].timestamp_ns",
    )
    fall_ns = as_int(
        clock_edges[1].get("timestamp_ns"),
        "clock_edges[1].timestamp_ns",
    )
    require(rise_ns >= 0, "el timestamp de subida no puede ser negativo")
    require(
        fall_ns > rise_ns,
        "el flanco de bajada debe ser posterior a la subida",
    )

    timebase = header["timebase"]
    calibration: dict[str, Any] | None = None
    if timebase_calibration_path is not None:
        calibration = validate_timebase_calibration(
            timebase_calibration_path.resolve(),
            capture_timebase=timebase,
        )
    timebase_traceable = (
        calibration is not None and calibration["traceable"] is True
    )
    scale = (
        calibration["seconds_per_reported_second"]
        if timebase_traceable
        else 1.0
    )
    raw_duration_s = (fall_ns - rise_ns) * 1e-9
    measured_duration_s = raw_duration_s * scale
    effective_core_clock_hz = expected_cycles / measured_duration_s
    nominal_core_clock_hz = frozen_profile["core_clock_hz"]
    duration_error_s = measured_duration_s - EXPECTED_PULSE_S
    effective_clock_error_percent = (
        effective_core_clock_hz - nominal_core_clock_hz
    ) / nominal_core_clock_hz * 100.0

    sample_count = sum(record.get("type") == "sample" for record in body)
    observed_clock_edge_count = len(clock_edges)
    transport_flags = {
        "terminal_complete": terminal["complete"],
        "terminal_not_stopped": not terminal["stopped"],
        "terminal_not_timed_out": not terminal["timed_out"],
        "terminal_no_i2c_error": (
            not terminal["i2c_error_flag"]
            and terminal["i2c_error_count"] == 0
        ),
        "terminal_no_edge_overflow": not terminal["edge_queue_overflow"],
        "terminal_no_timing_or_edge_anomaly": (
            not terminal["timing_or_edge_anomaly"]
            and terminal["late_sample_slots"] == 0
        ),
        "wire_alignment": terminal["discarded_wire_bytes"] == 0,
        "wire_crc": terminal["crc_failures"] == 0,
        "sample_count_matches": terminal["sample_count"] == sample_count,
        "clock_edge_count_matches": (
            terminal["clock_edge_count"] == observed_clock_edge_count
        ),
    }
    duration_within_tolerance = (
        abs(duration_error_s) <= duration_tolerance_s
    )
    transport_valid = all(transport_flags.values())
    validation_passed = transport_valid and duration_within_tolerance
    publication_ready = validation_passed and timebase_traceable

    return {
        "schema": RESULT_SCHEMA,
        "schema_version": 1,
        "status": (
            "accepted_traceable_clock_reference"
            if publication_ready
            else (
                "accepted_diagnostic_clock_reference"
                if validation_passed
                else "rejected"
            )
        ),
        "capture": {
            "path": capture_path.as_posix(),
            "sha256": sha256_file(capture_path),
            "capture_id": header.get("capture_id"),
            "run_id": header.get("run_id"),
            "board": board,
        },
        "contract": {
            "wire_protocol": WIRE_PROTOCOL,
            "marker_kind": "clock_reference",
            "required_edge_levels": [1, 0],
            "firmware_profile": firmware_profile,
            "board": board,
            "frozen_core_clock_hz": frozen_profile["core_clock_hz"],
            "expected_cycles": expected_cycles,
            "expected_pulse_s": EXPECTED_PULSE_S,
            "duration_tolerance_s": duration_tolerance_s,
            "maximum_configurable_duration_tolerance_s": (
                MAX_DURATION_TOLERANCE_S
            ),
        },
        "observed": {
            "rise_timestamp_ns": rise_ns,
            "fall_timestamp_ns": fall_ns,
            "raw_duration_s": raw_duration_s,
            "measured_duration_s": measured_duration_s,
            "duration_error_s": duration_error_s,
            "clock_reference_edge_count": observed_clock_edge_count,
            "sample_count": sample_count,
        },
        "clock": {
            "expected_cycles": expected_cycles,
            "nominal_core_clock_hz": nominal_core_clock_hz,
            "effective_core_clock_hz": effective_core_clock_hz,
            "effective_clock_error_percent": effective_clock_error_percent,
            "formula": "expected_cycles / measured_duration_s",
        },
        "timebase": {
            "source": TIMEBASE_SOURCE,
            "traceable_calibration": timebase_traceable,
            "calibration": calibration,
            "calibration_applied": timebase_traceable,
            "diagnostic_only": not timebase_traceable,
        },
        "quality": {
            "transport_flags": transport_flags,
            "transport_valid": transport_valid,
            "duration_within_tolerance": duration_within_tolerance,
            "validation_passed": validation_passed,
        },
        "publication_ready": publication_ready,
        "limitations": [
            (
                "Without a separate traceable Arduino timebase calibration, "
                "effective_core_clock_hz is diagnostic only."
            ),
            (
                "The validator checks the recorded GPIO pulse; it does not "
                "measure the oscillator or generate physical evidence."
            ),
            (
                "clock_reference_contract records a host-selected frozen "
                "campaign profile; it does not attest the flashed binary."
            ),
        ],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida la referencia de reloj INA14/1 de 100 ms",
    )
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--expected-cycles", type=int, required=True)
    parser.add_argument(
        "--firmware-profile",
        required=True,
        choices=sorted(FROZEN_FIRMWARE_CLOCK_PROFILES),
        help="perfil congelado que debe coincidir con el contrato del header",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--duration-tolerance-ms",
        type=float,
        default=DEFAULT_DURATION_TOLERANCE_S * 1000.0,
        help="tolerancia alrededor de 100 ms; máximo permitido: 10 ms",
    )
    parser.add_argument(
        "--timebase-calibration",
        type=Path,
        help="calibración trazable separada de Arduino micros()",
    )
    args = parser.parse_args()

    result = validate_clock_reference(
        args.capture,
        expected_cycles=args.expected_cycles,
        firmware_profile=args.firmware_profile,
        duration_tolerance_s=args.duration_tolerance_ms / 1000.0,
        timebase_calibration_path=args.timebase_calibration,
    )
    write_json(args.output.resolve(), result)
    print(args.output.resolve())
    return 0 if result["quality"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
