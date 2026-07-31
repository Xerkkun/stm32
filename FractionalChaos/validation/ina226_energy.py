#!/usr/bin/env python3
"""Validate INA226 calibration/captures and integrate board energy.

This module only turns measured raw records into auditable artifacts.  It does
not manufacture calibration points, infer missing GPIO edges, or reinterpret
whole-board VBUS measurements as MCU-core energy.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Sequence


CALIBRATION_SCHEMA = "fractional-chaos-ina226-calibration-v1"
CALIBRATION_INPUT_SCHEMA = "fractional-chaos-ina226-calibration-input-v1"
CAPTURE_SCHEMA = "fractional-chaos-ina226-capture-v1"
RESULT_SCHEMA = "fractional-chaos-ina226-energy-result-v1"
ENERGY_SCOPE = "whole_nucleo_vbus_including_stlink"

INA226_MANUFACTURER_ID = 0x5449
INA226_DIE_ID = 0x2260
INA226_BUS_LSB_V = 1.25e-3
INA226_SHUNT_LSB_V = 2.5e-6
INA226_SHUNT_FULL_SCALE_V = 81.92e-3
R100_OHMS = 0.1


class EnergyError(RuntimeError):
    """Raised when an energy artifact violates its frozen contract."""


@dataclass(frozen=True)
class Sample:
    timestamp_ns: int
    sequence: int
    bus_raw: int
    shunt_raw: int
    conversion_ready: bool
    math_overflow: bool
    i2c_ok: bool


@dataclass(frozen=True)
class Edge:
    timestamp_ns: int
    level: int
    marker_kind: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EnergyError(message)


def as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise EnergyError(f"{field} no puede ser booleano")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise EnergyError(f"{field} no es un entero válido") from exc
    raise EnergyError(f"{field} no es un entero")


def as_finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise EnergyError(f"{field} no puede ser booleano")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise EnergyError(f"{field} no es numérico") from exc
    if not math.isfinite(result):
        raise EnergyError(f"{field} debe ser finito")
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnergyError(f"no se pudo leer JSON válido: {path}") from exc
    require(isinstance(payload, dict), f"{path} debe contener un objeto JSON")
    return payload


def fit_line(
    points: Sequence[tuple[float, float]],
    *,
    label: str,
) -> dict[str, float | int]:
    """Fit y = slope*x + offset without external numerical dependencies."""

    require(len(points) >= 2, f"{label} requiere al menos dos puntos")
    xs = [as_finite_float(point[0], f"{label}.x") for point in points]
    ys = [as_finite_float(point[1], f"{label}.y") for point in points]
    x_mean = fmean(xs)
    y_mean = fmean(ys)
    denominator = math.fsum((x - x_mean) ** 2 for x in xs)
    require(denominator > 0.0, f"{label} requiere valores x distintos")
    slope = math.fsum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(xs, ys)
    ) / denominator
    offset = y_mean - slope * x_mean
    residual = math.fsum(
        (y - (slope * x + offset)) ** 2
        for x, y in zip(xs, ys)
    )
    total = math.fsum((y - y_mean) ** 2 for y in ys)
    r_squared = 1.0 if total == 0.0 and residual == 0.0 else (
        1.0 - residual / total if total > 0.0 else 0.0
    )
    require(slope > 0.0, f"{label} produjo una pendiente no positiva")
    return {
        "slope": slope,
        "offset": offset,
        "r_squared": r_squared,
        "point_count": len(points),
    }


def build_calibration(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a pre-campaign calibration from explicitly measured points."""

    require(
        payload.get("schema") == CALIBRATION_INPUT_SCHEMA,
        f"schema de entrada debe ser {CALIBRATION_INPUT_SCHEMA}",
    )
    sensor_id = payload.get("sensor_id")
    board = payload.get("board")
    require(isinstance(sensor_id, str) and sensor_id, "sensor_id es obligatorio")
    require(board in {"f746", "h755"}, "board debe ser f746 o h755")

    address = as_int(payload.get("i2c_address"), "i2c_address")
    require(0x40 <= address <= 0x4F, "i2c_address debe estar entre 0x40 y 0x4F")
    identity = payload.get("identity")
    require(isinstance(identity, dict), "identity es obligatorio")
    manufacturer_id = as_int(
        identity.get("manufacturer_id"),
        "identity.manufacturer_id",
    )
    die_id = as_int(identity.get("die_id"), "identity.die_id")
    require(
        manufacturer_id == INA226_MANUFACTURER_ID,
        "Manufacturer ID no coincide con INA226",
    )
    require(die_id == INA226_DIE_ID, "Die ID no coincide con INA226")

    shunt = payload.get("shunt")
    require(isinstance(shunt, dict), "shunt es obligatorio")
    require(shunt.get("marking") == "R100", "el shunt debe estar marcado R100")
    shunt_ohms = as_finite_float(shunt.get("nominal_ohms"), "shunt.nominal_ohms")
    require(
        math.isclose(shunt_ohms, R100_OHMS, rel_tol=0.0, abs_tol=1e-9),
        "el shunt R100 debe declarar 0.1 ohm",
    )

    current_rows = payload.get("current_points")
    require(
        isinstance(current_rows, list) and len(current_rows) >= 4,
        "current_points requiere al menos cuatro mediciones",
    )
    current_points: list[tuple[float, float]] = []
    reference_currents: list[float] = []
    for index, row in enumerate(current_rows):
        require(isinstance(row, dict), f"current_points[{index}] debe ser objeto")
        raw = as_int(row.get("measured_shunt_raw"), f"current_points[{index}].measured_shunt_raw")
        require(-32768 <= raw <= 32767, "measured_shunt_raw fuera de int16")
        reference = as_finite_float(
            row.get("reference_current_a"),
            f"current_points[{index}].reference_current_a",
        )
        current_points.append((raw * INA226_SHUNT_LSB_V, reference))
        reference_currents.append(reference)
    require(
        min(abs(value) for value in reference_currents) <= 0.001,
        "current_points debe incluir un punto de cero",
    )
    require(
        max(reference_currents) >= 0.5,
        "current_points debe alcanzar al menos 0.5 A",
    )

    bus_rows = payload.get("bus_voltage_points")
    require(
        isinstance(bus_rows, list) and len(bus_rows) >= 2,
        "bus_voltage_points requiere al menos dos mediciones",
    )
    bus_points: list[tuple[float, float]] = []
    for index, row in enumerate(bus_rows):
        require(isinstance(row, dict), f"bus_voltage_points[{index}] debe ser objeto")
        raw = as_int(row.get("measured_bus_raw"), f"bus_voltage_points[{index}].measured_bus_raw")
        require(0 <= raw <= 0xFFFF, "measured_bus_raw fuera de uint16")
        reference = as_finite_float(
            row.get("reference_bus_v"),
            f"bus_voltage_points[{index}].reference_bus_v",
        )
        bus_points.append((raw * INA226_BUS_LSB_V, reference))

    uncertainty = payload.get("uncertainty")
    require(isinstance(uncertainty, dict), "uncertainty es obligatorio")
    expanded = as_finite_float(
        uncertainty.get("expanded_relative_percent_k2"),
        "uncertainty.expanded_relative_percent_k2",
    )
    require(expanded >= 0.0, "la incertidumbre no puede ser negativa")
    require(
        isinstance(uncertainty.get("method"), str)
        and bool(uncertainty["method"].strip()),
        "uncertainty.method es obligatorio",
    )

    provenance = payload.get("provenance")
    require(isinstance(provenance, dict), "provenance es obligatorio")
    for field in ("timestamp_utc", "reference_instrument", "operator"):
        require(
            isinstance(provenance.get(field), str)
            and bool(provenance[field].strip()),
            f"provenance.{field} es obligatorio",
        )
    temperature_c = as_finite_float(
        provenance.get("temperature_c"),
        "provenance.temperature_c",
    )

    result: dict[str, Any] = {
        "schema": CALIBRATION_SCHEMA,
        "schema_version": 1,
        "status": "measured_pre_campaign",
        "calibration_id": payload.get("calibration_id"),
        "sensor_id": sensor_id,
        "board": board,
        "i2c_address": address,
        "identity": {
            "manufacturer_id": manufacturer_id,
            "die_id": die_id,
            "compatibility_check_only": True,
            "authenticity_claimed": False,
        },
        "shunt": {
            "marking": "R100",
            "nominal_ohms": R100_OHMS,
            "full_scale_current_a": INA226_SHUNT_FULL_SCALE_V / R100_OHMS,
        },
        "register_scaling": {
            "bus_lsb_v": INA226_BUS_LSB_V,
            "shunt_lsb_v": INA226_SHUNT_LSB_V,
        },
        "current_from_shunt_voltage": fit_line(
            current_points,
            label="current_from_shunt_voltage",
        ),
        "bus_voltage_from_register_voltage": fit_line(
            bus_points,
            label="bus_voltage_from_register_voltage",
        ),
        "uncertainty": {
            **uncertainty,
            "expanded_relative_percent_k2": expanded,
        },
        "pre_campaign": {
            **provenance,
            "temperature_c": temperature_c,
            "current_points_sha256": canonical_sha256(current_rows),
            "bus_voltage_points_sha256": canonical_sha256(bus_rows),
        },
        "post_campaign_check": None,
        "source_measurements_sha256": canonical_sha256(payload),
    }
    require(
        isinstance(result["calibration_id"], str)
        and bool(result["calibration_id"].strip()),
        "calibration_id es obligatorio",
    )
    validate_calibration(result)
    return result


def validate_calibration(payload: dict[str, Any]) -> dict[str, Any]:
    require(
        payload.get("schema") == CALIBRATION_SCHEMA,
        f"schema de calibración debe ser {CALIBRATION_SCHEMA}",
    )
    require(
        payload.get("status") in {
            "measured_pre_campaign",
            "measured_post_campaign",
        },
        "la calibración debe proceder de mediciones",
    )
    require(
        isinstance(payload.get("calibration_id"), str)
        and bool(payload["calibration_id"].strip()),
        "calibration_id es obligatorio",
    )
    require(
        isinstance(payload.get("sensor_id"), str)
        and bool(payload["sensor_id"].strip()),
        "sensor_id es obligatorio",
    )
    require(payload.get("board") in {"f746", "h755"}, "board inválido")
    address = as_int(payload.get("i2c_address"), "i2c_address")
    require(0x40 <= address <= 0x4F, "i2c_address fuera de rango")

    identity = payload.get("identity")
    require(isinstance(identity, dict), "identity es obligatorio")
    require(
        as_int(identity.get("manufacturer_id"), "manufacturer_id")
        == INA226_MANUFACTURER_ID,
        "Manufacturer ID no coincide con INA226",
    )
    require(
        as_int(identity.get("die_id"), "die_id") == INA226_DIE_ID,
        "Die ID no coincide con INA226",
    )
    require(
        identity.get("compatibility_check_only") is True,
        "los IDs sólo pueden declararse como prueba de compatibilidad",
    )
    require(
        identity.get("authenticity_claimed") is False,
        "los IDs no demuestran autenticidad",
    )

    shunt = payload.get("shunt")
    require(isinstance(shunt, dict), "shunt es obligatorio")
    require(shunt.get("marking") == "R100", "se requiere shunt R100")
    require(
        math.isclose(
            as_finite_float(shunt.get("nominal_ohms"), "nominal_ohms"),
            R100_OHMS,
            rel_tol=0.0,
            abs_tol=1e-9,
        ),
        "nominal_ohms debe ser 0.1",
    )

    for name in (
        "current_from_shunt_voltage",
        "bus_voltage_from_register_voltage",
    ):
        fit = payload.get(name)
        require(isinstance(fit, dict), f"{name} es obligatorio")
        require(
            as_finite_float(fit.get("slope"), f"{name}.slope") > 0.0,
            f"{name}.slope debe ser positiva",
        )
        as_finite_float(fit.get("offset"), f"{name}.offset")
        r_squared = as_finite_float(
            fit.get("r_squared"),
            f"{name}.r_squared",
        )
        require(0.0 <= r_squared <= 1.0, f"{name}.r_squared fuera de rango")
        require(
            as_int(fit.get("point_count"), f"{name}.point_count") >= 2,
            f"{name}.point_count insuficiente",
        )

    uncertainty = payload.get("uncertainty")
    require(isinstance(uncertainty, dict), "uncertainty es obligatorio")
    expanded = as_finite_float(
        uncertainty.get("expanded_relative_percent_k2"),
        "expanded_relative_percent_k2",
    )
    require(expanded >= 0.0, "expanded_relative_percent_k2 inválida")

    post = payload.get("post_campaign_check")
    drift_percent: float | None = None
    if post is not None:
        require(isinstance(post, dict), "post_campaign_check debe ser objeto")
        reference = as_finite_float(
            post.get("reference_current_a"),
            "post_campaign_check.reference_current_a",
        )
        observed = as_finite_float(
            post.get("observed_current_a"),
            "post_campaign_check.observed_current_a",
        )
        require(reference > 0.0, "reference_current_a debe ser positiva")
        drift_percent = abs(observed - reference) / reference * 100.0

    return {
        "expanded_relative_percent_k2": expanded,
        "post_campaign_check_complete": post is not None,
        "post_campaign_drift_percent": drift_percent,
    }


def load_capture(
    path: Path,
) -> tuple[
    dict[str, Any],
    list[Sample],
    list[Edge],
    dict[str, Any] | None,
]:
    header: dict[str, Any] | None = None
    samples: list[Sample] = []
    edges: list[Edge] = []
    terminal: dict[str, Any] | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EnergyError(f"no se pudo leer la captura: {path}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EnergyError(
                f"JSONL inválido en {path}:{line_number}"
            ) from exc
        require(
            isinstance(record, dict),
            f"{path}:{line_number} debe contener un objeto",
        )
        kind = record.get("type")
        require(
            terminal is None,
            f"{path}:{line_number} contiene datos después de end",
        )
        if kind == "header":
            require(header is None, "la captura contiene más de un header")
            require(not samples and not edges, "header debe ser el primer registro")
            header = record
        elif kind == "sample":
            require(header is not None, "sample apareció antes del header")
            bus_raw = as_int(record.get("bus_raw"), "sample.bus_raw")
            shunt_raw = as_int(record.get("shunt_raw"), "sample.shunt_raw")
            require(0 <= bus_raw <= 0xFFFF, "sample.bus_raw fuera de uint16")
            require(
                -32768 <= shunt_raw <= 32767,
                "sample.shunt_raw fuera de int16",
            )
            require(
                isinstance(record.get("conversion_ready"), bool),
                "sample.conversion_ready debe ser booleano",
            )
            require(
                isinstance(record.get("math_overflow"), bool),
                "sample.math_overflow debe ser booleano",
            )
            i2c_ok = record.get("i2c_ok", True)
            require(
                isinstance(i2c_ok, bool),
                "sample.i2c_ok debe ser booleano",
            )
            samples.append(
                Sample(
                    timestamp_ns=as_int(
                        record.get("timestamp_ns"),
                        "sample.timestamp_ns",
                    ),
                    sequence=as_int(record.get("sequence"), "sample.sequence"),
                    bus_raw=bus_raw,
                    shunt_raw=shunt_raw,
                    conversion_ready=record["conversion_ready"],
                    math_overflow=record["math_overflow"],
                    i2c_ok=i2c_ok,
                )
            )
        elif kind == "edge":
            require(header is not None, "edge apareció antes del header")
            level = as_int(record.get("level"), "edge.level")
            require(level in {0, 1}, "edge.level debe ser cero o uno")
            marker_kind = record.get("marker_kind", "energy_window")
            require(
                marker_kind in {"energy_window", "clock_reference"},
                "edge.marker_kind debe ser energy_window o clock_reference",
            )
            edges.append(
                Edge(
                    timestamp_ns=as_int(
                        record.get("timestamp_ns"),
                        "edge.timestamp_ns",
                    ),
                    level=level,
                    marker_kind=marker_kind,
                )
            )
        elif kind == "end":
            require(header is not None, "end apareció antes del header")
            terminal = record
        else:
            raise EnergyError(
                f"tipo de registro desconocido en {path}:{line_number}"
            )

    require(header is not None, "la captura no contiene header")
    require(
        header.get("schema") == CAPTURE_SCHEMA,
        f"schema de captura debe ser {CAPTURE_SCHEMA}",
    )
    require(len(samples) >= 2, "la captura no contiene suficientes muestras")
    require(
        all(
            right.timestamp_ns > left.timestamp_ns
            for left, right in zip(samples, samples[1:])
        ),
        "timestamps de muestras no son estrictamente crecientes",
    )
    require(
        all(
            right.sequence > left.sequence
            for left, right in zip(samples, samples[1:])
        ),
        "secuencias de muestras no son estrictamente crecientes",
    )
    energy_edges = [
        edge for edge in edges if edge.marker_kind == "energy_window"
    ]
    require(
        len(energy_edges) == 2
        and energy_edges[0].level == 1
        and energy_edges[1].level == 0
        and energy_edges[1].timestamp_ns > energy_edges[0].timestamp_ns,
        "se requiere exactamente un flanco ascendente y uno descendente",
    )
    acquisition = header.get("acquisition")
    if (
        isinstance(acquisition, dict)
        and acquisition.get("wire_protocol") == "INA14/1"
    ):
        require(
            header.get("evidence_status") == "measured_raw_unvalidated",
            "captura INA14/1 debe permanecer measured_raw_unvalidated",
        )
        require(
            header.get("publication_ready") is False,
            "header crudo no puede marcarse publication_ready",
        )
        require(
            as_int(
                acquisition.get("sample_period_us"),
                "acquisition.sample_period_us",
            )
            * 1000
            == as_int(
                header.get("quality_contract", {}).get(
                    "expected_sample_period_ns"
                ),
                "quality_contract.expected_sample_period_ns",
            ),
            "periodo de adquisición y contrato de calidad no coinciden",
        )
        require(terminal is not None, "captura INA14/1 sin registro end")
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
        ):
            require(
                as_int(terminal.get(field), f"end.{field}") >= 0,
                f"end.{field} no puede ser negativo",
            )
        require(
            terminal.get("publication_ready") is False,
            "la captura cruda no puede marcarse publication_ready",
        )
        require(
            as_int(terminal.get("sample_count"), "end.sample_count")
            == len(samples),
            "end.sample_count no coincide con los frames",
        )
        require(
            as_int(
                terminal.get("energy_edge_count"),
                "end.energy_edge_count",
            )
            == len(energy_edges),
            "end.energy_edge_count no coincide con los frames",
        )
    return header, samples, energy_edges, terminal


def calibrated_point(
    sample: Sample,
    calibration: dict[str, Any],
) -> tuple[float, float, float]:
    current_fit = calibration["current_from_shunt_voltage"]
    bus_fit = calibration["bus_voltage_from_register_voltage"]
    shunt_v = sample.shunt_raw * INA226_SHUNT_LSB_V
    register_bus_v = sample.bus_raw * INA226_BUS_LSB_V
    current_a = (
        as_finite_float(current_fit["slope"], "current slope") * shunt_v
        + as_finite_float(current_fit["offset"], "current offset")
    )
    bus_v = (
        as_finite_float(bus_fit["slope"], "bus slope") * register_bus_v
        + as_finite_float(bus_fit["offset"], "bus offset")
    )
    return bus_v, current_a, bus_v * current_a


def interpolate_power(
    timestamp_ns: int,
    samples: Sequence[Sample],
    powers: Sequence[float],
) -> float:
    timestamps = [sample.timestamp_ns for sample in samples]
    index = bisect.bisect_left(timestamps, timestamp_ns)
    if index < len(samples) and timestamps[index] == timestamp_ns:
        return powers[index]
    require(
        0 < index < len(samples),
        "las muestras no encierran ambos flancos GPIO",
    )
    left = samples[index - 1]
    right = samples[index]
    fraction = (
        (timestamp_ns - left.timestamp_ns)
        / (right.timestamp_ns - left.timestamp_ns)
    )
    return powers[index - 1] + fraction * (
        powers[index] - powers[index - 1]
    )


def trapezoid_energy(
    timestamps_ns: Sequence[int],
    powers_w: Sequence[float],
) -> float:
    require(
        len(timestamps_ns) == len(powers_w) and len(powers_w) >= 2,
        "la integración requiere al menos dos puntos alineados",
    )
    return math.fsum(
        0.5
        * (powers_w[index] + powers_w[index + 1])
        * ((timestamps_ns[index + 1] - timestamps_ns[index]) * 1e-9)
        for index in range(len(powers_w) - 1)
    )


def integrate_capture(
    capture_path: Path,
    calibration_path: Path,
) -> dict[str, Any]:
    header, samples, edges, terminal = load_capture(capture_path)
    calibration = load_json(calibration_path)
    calibration_quality = validate_calibration(calibration)

    for field in ("capture_id", "sensor_id", "run_id"):
        require(
            isinstance(header.get(field), str) and bool(header[field].strip()),
            f"header.{field} es obligatorio",
        )
    require(header.get("board") in {"f746", "h755"}, "header.board inválido")
    require(
        header.get("sensor_id") == calibration.get("sensor_id"),
        "sensor_id de captura y calibración no coincide",
    )
    require(
        header.get("board") == calibration.get("board"),
        "board de captura y calibración no coincide",
    )
    require(
        header.get("energy_scope") == ENERGY_SCOPE,
        f"energy_scope debe ser {ENERGY_SCOPE}",
    )
    work_units = as_int(header.get("work_units"), "header.work_units")
    require(work_units > 0, "header.work_units debe ser positivo")
    require(
        isinstance(header.get("work_unit_label"), str)
        and bool(header["work_unit_label"].strip()),
        "header.work_unit_label es obligatorio",
    )

    contract = header.get("quality_contract")
    require(isinstance(contract, dict), "header.quality_contract es obligatorio")
    expected_period_ns = as_int(
        contract.get("expected_sample_period_ns"),
        "expected_sample_period_ns",
    )
    min_samples = as_int(contract.get("min_window_samples"), "min_window_samples")
    max_gap_periods = as_finite_float(
        contract.get("max_gap_periods"),
        "max_gap_periods",
    )
    max_loss_percent = as_finite_float(
        contract.get("max_loss_percent"),
        "max_loss_percent",
    )
    min_window_s = as_finite_float(
        contract.get("min_window_s"),
        "min_window_s",
    )
    min_bus_v = as_finite_float(
        contract.get("min_bus_voltage_v"),
        "min_bus_voltage_v",
    )
    saturation_guard = as_finite_float(
        contract.get("saturation_guard_fraction"),
        "saturation_guard_fraction",
    )
    min_idle_each_side = as_int(
        contract.get("min_idle_samples_each_side"),
        "min_idle_samples_each_side",
    )
    max_idle_drift_percent = as_finite_float(
        contract.get("max_idle_drift_percent"),
        "max_idle_drift_percent",
    )
    require(expected_period_ns > 0, "expected_sample_period_ns debe ser positivo")
    require(min_samples >= 1000, "min_window_samples no puede ser menor que 1000")
    require(
        0.0 < max_gap_periods <= 2.0,
        "max_gap_periods debe ser mayor que cero y no superar 2",
    )
    require(
        0.0 < max_loss_percent <= 0.1,
        "max_loss_percent debe ser positivo y no superar 0.1",
    )
    require(min_window_s >= 0.5, "min_window_s no puede ser menor que 0.5")
    require(min_bus_v >= 4.75, "min_bus_voltage_v no puede ser menor que 4.75")
    require(
        0.0 < saturation_guard <= 0.9,
        "saturation_guard_fraction no puede superar 0.9",
    )
    require(min_idle_each_side >= 1, "se requieren muestras de reposo")
    require(
        max_idle_drift_percent >= 0.0,
        "max_idle_drift_percent no puede ser negativo",
    )

    rise_ns = edges[0].timestamp_ns
    fall_ns = edges[1].timestamp_ns
    timestamps = [sample.timestamp_ns for sample in samples]
    first_inside = bisect.bisect_left(timestamps, rise_ns)
    first_after = bisect.bisect_right(timestamps, fall_ns)
    pre_samples = samples[:first_inside]
    active_samples = samples[first_inside:first_after]
    post_samples = samples[first_after:]
    require(
        len(pre_samples) >= min_idle_each_side,
        "faltan muestras de reposo anteriores al flanco",
    )
    require(
        len(post_samples) >= min_idle_each_side,
        "faltan muestras de reposo posteriores al flanco",
    )
    require(
        pre_samples[-1].timestamp_ns <= rise_ns
        and post_samples[0].timestamp_ns >= fall_ns,
        "la captura no encierra la ventana GPIO",
    )

    calibrated = [
        calibrated_point(sample, calibration)
        for sample in samples
    ]
    bus_values = [value[0] for value in calibrated]
    current_values = [value[1] for value in calibrated]
    powers = [value[2] for value in calibrated]

    integration_times = [rise_ns]
    integration_powers = [interpolate_power(rise_ns, samples, powers)]
    for sample, power in zip(active_samples, powers[first_inside:first_after]):
        if rise_ns < sample.timestamp_ns < fall_ns:
            integration_times.append(sample.timestamp_ns)
            integration_powers.append(power)
    integration_times.append(fall_ns)
    integration_powers.append(interpolate_power(fall_ns, samples, powers))

    window_s = (fall_ns - rise_ns) * 1e-9
    total_energy_j = trapezoid_energy(
        integration_times,
        integration_powers,
    )
    pre_idle_power_w = fmean(powers[:first_inside])
    post_idle_power_w = fmean(powers[first_after:])
    idle_power_w = 0.5 * (pre_idle_power_w + post_idle_power_w)
    idle_energy_j = idle_power_w * window_s
    corrected_energy_j = total_energy_j - idle_energy_j
    idle_denominator = max(abs(idle_power_w), 1e-15)
    idle_drift_percent = (
        abs(post_idle_power_w - pre_idle_power_w)
        / idle_denominator
        * 100.0
    )

    quality_samples = (
        [pre_samples[-1]]
        + list(active_samples)
        + [post_samples[0]]
    )
    sequence_missing = math.fsum(
        max(0, right.sequence - left.sequence - 1)
        for left, right in zip(quality_samples, quality_samples[1:])
    )
    expected_total = len(quality_samples) + int(sequence_missing)
    loss_percent = (
        100.0 * int(sequence_missing) / expected_total
        if expected_total
        else 0.0
    )
    gaps_ns = [
        right.timestamp_ns - left.timestamp_ns
        for left, right in zip(quality_samples, quality_samples[1:])
    ]
    max_observed_gap_ns = max(gaps_ns)
    max_observed_gap_periods = max_observed_gap_ns / expected_period_ns
    max_abs_shunt_raw = max(
        abs(sample.shunt_raw)
        for sample in quality_samples
    )
    saturation_limit_raw = math.floor(32767 * saturation_guard)
    min_window_bus_v = min(
        bus_values[first_inside:first_after]
        or [
            interpolate_power(rise_ns, samples, bus_values),
            interpolate_power(fall_ns, samples, bus_values),
        ]
    )
    flags = {
        "window_sample_count": len(active_samples) >= min_samples,
        "window_duration": window_s >= min_window_s,
        "sequence_loss": loss_percent < max_loss_percent,
        "timestamp_gap": max_observed_gap_periods <= max_gap_periods,
        "conversion_ready": all(
            sample.conversion_ready for sample in quality_samples
        ),
        "math_overflow": not any(
            sample.math_overflow for sample in quality_samples
        ),
        "i2c_transactions": all(
            sample.i2c_ok for sample in quality_samples
        ),
        "shunt_headroom": max_abs_shunt_raw <= saturation_limit_raw,
        "bus_voltage": min_window_bus_v >= min_bus_v,
        "idle_stability": idle_drift_percent <= max_idle_drift_percent,
        "positive_corrected_energy": corrected_energy_j > 0.0,
    }
    acquisition = header.get("acquisition")
    if (
        isinstance(acquisition, dict)
        and acquisition.get("wire_protocol") == "INA14/1"
    ):
        require(terminal is not None, "captura INA14/1 sin end")
        flags["transport_complete"] = (
            terminal["complete"]
            and not terminal["stopped"]
            and not terminal["timed_out"]
            and not terminal["i2c_error_flag"]
            and not terminal["edge_queue_overflow"]
            and not terminal["timing_or_edge_anomaly"]
            and as_int(
                terminal["i2c_error_count"],
                "end.i2c_error_count",
            )
            == 0
            and as_int(
                terminal["late_sample_slots"],
                "end.late_sample_slots",
            )
            == 0
            and as_int(
                terminal["discarded_wire_bytes"],
                "end.discarded_wire_bytes",
            )
            == 0
            and as_int(
                terminal["crc_failures"],
                "end.crc_failures",
            )
            == 0
        )
    measurement_valid = all(flags.values())
    uncertainty_pass = (
        calibration_quality["expanded_relative_percent_k2"] <= 1.0
    )
    drift = calibration_quality["post_campaign_drift_percent"]
    post_check_pass = (
        drift is not None and drift <= 0.5
    )
    publication_ready = (
        measurement_valid and uncertainty_pass and post_check_pass
    )

    return {
        "schema": RESULT_SCHEMA,
        "schema_version": 1,
        "evidence_level": "calibrated_whole_board_energy_measurement",
        "capture": {
            "path": capture_path.as_posix(),
            "sha256": sha256_file(capture_path),
            "capture_id": header["capture_id"],
            "run_id": header["run_id"],
            "sensor_id": header["sensor_id"],
            "board": header["board"],
        },
        "calibration": {
            "path": calibration_path.as_posix(),
            "sha256": sha256_file(calibration_path),
            "calibration_id": calibration["calibration_id"],
        },
        "measurement_scope": {
            "name": ENERGY_SCOPE,
            "includes_stlink": True,
            "mcu_core_only": False,
            "idle_corrected_is_still_not_mcu_core_only": True,
        },
        "window": {
            "rise_timestamp_ns": rise_ns,
            "fall_timestamp_ns": fall_ns,
            "duration_s": window_s,
            "samples_inside": len(active_samples),
            "work_units": work_units,
            "work_unit_label": header["work_unit_label"],
        },
        "energy": {
            "total_board_energy_j": total_energy_j,
            "pre_idle_power_w": pre_idle_power_w,
            "post_idle_power_w": post_idle_power_w,
            "idle_power_w": idle_power_w,
            "idle_energy_j": idle_energy_j,
            "idle_corrected_board_energy_j": corrected_energy_j,
            "total_board_energy_per_work_unit_j": (
                total_energy_j / work_units
            ),
            "idle_corrected_board_energy_per_work_unit_j": (
                corrected_energy_j / work_units
            ),
            "integration": "trapezoidal_between_measured_gpio_edges",
        },
        "quality": {
            "contract": contract,
            "observed": {
                "missing_sequences": int(sequence_missing),
                "loss_percent": loss_percent,
                "max_gap_ns": max_observed_gap_ns,
                "max_gap_periods": max_observed_gap_periods,
                "max_abs_shunt_raw": max_abs_shunt_raw,
                "saturation_limit_raw": saturation_limit_raw,
                "min_bus_voltage_v": min_window_bus_v,
                "idle_drift_percent": idle_drift_percent,
            },
            "flags": flags,
            "measurement_valid": measurement_valid,
        },
        "calibration_quality": {
            **calibration_quality,
            "uncertainty_at_most_1_percent": uncertainty_pass,
            "post_campaign_drift_at_most_0_5_percent": post_check_pass,
        },
        "publication_ready": publication_ready,
        "limitations": [
            "The measurement covers the complete NUCLEO VBUS path, including ST-LINK.",
            "Idle correction does not isolate MCU-core energy.",
            "Per-work-unit energy includes loop and GPIO-window overhead inside the measured interval.",
            "Publication readiness additionally requires the post-campaign calibration check.",
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
        description="Calibración e integración auditables para INA226 R100",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fit_parser = subparsers.add_parser(
        "fit-calibration",
        help="ajusta una calibración a partir de puntos medidos",
    )
    fit_parser.add_argument("--input", type=Path, required=True)
    fit_parser.add_argument("--output", type=Path, required=True)

    integrate_parser = subparsers.add_parser(
        "integrate",
        help="integra una captura JSONL delimitada por GPIO",
    )
    integrate_parser.add_argument("--capture", type=Path, required=True)
    integrate_parser.add_argument("--calibration", type=Path, required=True)
    integrate_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "fit-calibration":
        result = build_calibration(load_json(args.input.resolve()))
    else:
        result = integrate_capture(
            args.capture.resolve(),
            args.calibration.resolve(),
        )
    write_json(args.output.resolve(), result)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
