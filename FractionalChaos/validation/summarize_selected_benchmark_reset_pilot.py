#!/usr/bin/env python3
"""Aggregate the selected 36-cell STM32 benchmark-reset pilot matrix.

The frozen snapshot selects repetition r04 for every cell except the three
F746 fixed-point M2sFRK cells, whose failed r04 attempts are retained and whose
accepted replacements are r05. Every attempt remains visible. The resulting
statistics describe one ST-LINK hardware-reset repetition per cell and are not
primary power-cycle evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import struct
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_ID = "stm32_selected_36x30_v1"
ENDPOINT_NAME = "benchmark_reset_pilot"
MANIFEST_PATH = ROOT / "validation" / "physical_campaign_selected_v1.json"
DEFAULT_CAMPAIGN_ROOT = (
    ROOT
    / "validation"
    / "results"
    / "physical_campaign"
    / CAMPAIGN_ID
)
DEFAULT_OUTPUT_DIR = (
    DEFAULT_CAMPAIGN_ROOT
    / "aggregates"
    / "benchmark_reset_pilot_matrix_v1"
)
EXPECTED_CELLS = 36
EXPECTED_BLOCKS = 2_500
EXPECTED_CYCLE_VALUES = 10_000
FRAME = struct.Struct("<I6BH7I")
SYNC_WORD = 0x31434346
UINT32_MAX = (1 << 32) - 1
RUN_ID_PATTERN = re.compile(
    rf"^{re.escape(CAMPAIGN_ID)}__r(?P<repetition>[0-9]{{2}})__"
    rf"(?P<cell>.+)__(?P<token>[0-9a-f]{{10}})__"
    rf"{re.escape(ENDPOINT_NAME.replace('_', '-'))}$"
)
R05_REPLACEMENT_CELLS = frozenset(
    (
        "chen_m2sfrk_f746_fixed",
        "liu_m2sfrk_f746_fixed",
        "hammouch_mekkaoui_m2sfrk_f746_fixed",
    )
)
ZERO_TRANSPORT_FIELDS = (
    "crc_errors",
    "identity_mismatches",
    "invalid_headers",
    "noise_bytes",
    "nonzero_dropped_frames",
    "nonzero_status_frames",
    "trailing_bytes",
)
CAPTURE_CSV_FIELDS = (
    "version",
    "kind",
    "board",
    "system",
    "method",
    "representation",
    "status",
    "status_flags",
    "fixed_state_saturation",
    "fixed_coefficient_saturation",
    "fixed_coefficient_zeroed",
    "payload_bytes",
    "sequence",
    "cycles",
    "dropped",
    "x",
    "y",
    "z",
    "x_raw",
    "y_raw",
    "z_raw",
    "cycle_0",
    "cycle_1",
    "cycle_2",
    "cycle_3",
    "x_bits",
    "y_bits",
    "z_bits",
    "crc32",
)


class SelectedPilotSummaryError(RuntimeError):
    """Raised when the selected physical snapshot violates its contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelectedPilotSummaryError(
            f"no se pudo leer {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SelectedPilotSummaryError(
            f"{path}: la raíz JSON debe ser un objeto"
        )
    return value


def require_equal(
    observed: Any,
    expected: Any,
    label: str,
    source: Path,
) -> None:
    if observed != expected:
        raise SelectedPilotSummaryError(
            f"{source}: {label}={observed!r}, se esperaba {expected!r}"
        )


def require_object(
    payload: dict[str, Any],
    key: str,
    source: Path,
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SelectedPilotSummaryError(
            f"{source}: falta el objeto {key}"
        )
    return value


def require_sha256(value: Any, label: str, source: Path) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SelectedPilotSummaryError(
            f"{source}: {label} no es un SHA-256 minúsculo"
        )
    return value


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest = load_json_object(path)
    require_equal(
        manifest.get("schema"),
        "fractional-chaos-physical-campaign-v1",
        "schema",
        path,
    )
    require_equal(manifest.get("campaign_id"), CAMPAIGN_ID, "campaign_id", path)
    require_equal(
        manifest.get("endpoints", {})
        .get("primary_benchmark", {})
        .get("ready"),
        False,
        "endpoints.primary_benchmark.ready",
        path,
    )
    require_equal(
        manifest.get("reset_contract", {}).get("power_removed"),
        False,
        "reset_contract.power_removed",
        path,
    )
    return manifest


def cell_id(
    system: str,
    method: str,
    board: str,
    representation: str,
) -> str:
    representation_token = (
        "fixed" if representation == "fixed_q14_q30" else representation
    )
    return f"{system}_{method}_{board}_{representation_token}"


def build_target(spec: dict[str, str]) -> str:
    prefix = "f746" if spec["board"] == "f746" else "h755_m7"
    suffix = "_fixed" if spec["representation"] == "fixed_q14_q30" else ""
    return (
        f"{prefix}_{spec['system']}_{spec['method']}{suffix}"
    )


def expected_cell_specs(
    manifest: dict[str, Any],
) -> dict[str, dict[str, str]]:
    matrix = manifest["paper_matrix"]
    specs: dict[str, dict[str, str]] = {}
    for system in matrix["systems"]:
        for method in matrix["methods"]:
            for board in matrix["boards"]:
                for representation in matrix["representations"]:
                    identifier = cell_id(
                        system, method, board, representation
                    )
                    specs[identifier] = {
                        "cell_id": identifier,
                        "system": system,
                        "method": method,
                        "board": board,
                        "representation": representation,
                    }
    if len(specs) != EXPECTED_CELLS:
        raise SelectedPilotSummaryError(
            f"el manifiesto produce {len(specs)} celdas, no {EXPECTED_CELLS}"
        )
    return specs


def expected_repetition(identifier: str) -> int:
    return 5 if identifier in R05_REPLACEMENT_CELLS else 4


def parse_run_id(run_id: str, source: Path) -> tuple[int, str]:
    match = RUN_ID_PATTERN.fullmatch(run_id)
    if match is None:
        raise SelectedPilotSummaryError(
            f"{source}: run_id fuera del contrato: {run_id}"
        )
    repetition = int(match.group("repetition"), 10)
    if not 1 <= repetition <= 30:
        raise SelectedPilotSummaryError(
            f"{source}: repetición fuera de 1..30: {repetition}"
        )
    return repetition, match.group("cell")


def artifact_inventory(directory: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file():
            artifacts.append(
                {
                    "path": display_path(path),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    return artifacts


def require_artifact_hash(
    directory: Path,
    relative_name: Any,
    expected_sha256: Any,
    label: str,
    source: Path,
) -> Path:
    if not isinstance(relative_name, str) or Path(relative_name).name != relative_name:
        raise SelectedPilotSummaryError(
            f"{source}: {label}.path debe ser un nombre local"
        )
    expected = require_sha256(expected_sha256, f"{label}.sha256", source)
    path = directory / relative_name
    if not path.is_file():
        raise SelectedPilotSummaryError(f"{source}: falta {path}")
    observed = sha256_file(path)
    if observed != expected:
        raise SelectedPilotSummaryError(
            f"{path}: SHA-256 {observed} no coincide con {expected}"
        )
    return path


def parse_capture_binary(
    path: Path,
    spec: dict[str, str],
    manifest: dict[str, Any],
) -> tuple[list[tuple[int, int, int, int]], list[int], list[int]]:
    raw = path.read_bytes()
    expected_bytes = EXPECTED_BLOCKS * FRAME.size
    if len(raw) != expected_bytes:
        raise SelectedPilotSummaryError(
            f"{path}: {len(raw)} bytes, se esperaban {expected_bytes}"
        )

    expected_board = manifest["boards"][spec["board"]]["wire_id"]
    expected_system = manifest["systems"][spec["system"]]["wire_id"]
    expected_method = manifest["methods"][spec["method"]]["wire_id"]
    blocks: list[tuple[int, int, int, int]] = []
    crc_values: list[int] = []
    cycles: list[int] = []
    for block_index in range(EXPECTED_BLOCKS):
        offset = block_index * FRAME.size
        frame_bytes = raw[offset : offset + FRAME.size]
        values = FRAME.unpack(frame_bytes)
        expected_sequence = block_index * 4
        header_expectations = (
            (values[0], SYNC_WORD, "sync"),
            (values[1], 1, "version"),
            (values[2], 4, "kind"),
            (values[3], expected_board, "board"),
            (values[4], expected_system, "system"),
            (values[5], expected_method, "method"),
            (values[6], 0, "status"),
            (values[7], 24, "payload_bytes"),
            (values[8], expected_sequence, "sequence"),
            (values[10], 0, "dropped"),
        )
        for observed, expected, label in header_expectations:
            if observed != expected:
                raise SelectedPilotSummaryError(
                    f"{path}: bloque {block_index}, {label}={observed}, "
                    f"se esperaba {expected}"
                )
        expected_crc = zlib.crc32(frame_bytes[:-4]) & UINT32_MAX
        if values[14] != expected_crc:
            raise SelectedPilotSummaryError(
                f"{path}: bloque {block_index}, CRC inválido"
            )
        block = (values[9], values[11], values[12], values[13])
        if any(value == 0 for value in block):
            raise SelectedPilotSummaryError(
                f"{path}: bloque {block_index} contiene ciclos cero"
            )
        blocks.append(block)
        crc_values.append(values[14])
        cycles.extend(block)

    if len(cycles) != EXPECTED_CYCLE_VALUES:
        raise SelectedPilotSummaryError(
            f"{path}: se extrajeron {len(cycles)} ciclos"
        )
    return blocks, cycles, crc_values


def read_timing_cycles(path: Path) -> list[int]:
    try:
        with path.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != ("timed_index", "cycles"):
                raise SelectedPilotSummaryError(
                    f"{path}: cabecera inesperada {reader.fieldnames}"
                )
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SelectedPilotSummaryError(
            f"no se pudo leer {path}: {exc}"
        ) from exc
    if len(rows) != EXPECTED_CYCLE_VALUES:
        raise SelectedPilotSummaryError(
            f"{path}: contiene {len(rows)} filas, se esperaban "
            f"{EXPECTED_CYCLE_VALUES}"
        )

    values: list[int] = []
    for expected_index, row in enumerate(rows):
        try:
            observed_index = int(row["timed_index"], 10)
            cycles = int(row["cycles"], 10)
        except (KeyError, TypeError, ValueError) as exc:
            raise SelectedPilotSummaryError(
                f"{path}: fila inválida en {expected_index}"
            ) from exc
        if observed_index != expected_index:
            raise SelectedPilotSummaryError(
                f"{path}: índice {observed_index}, se esperaba {expected_index}"
            )
        if not 1 <= cycles <= UINT32_MAX:
            raise SelectedPilotSummaryError(
                f"{path}: ciclos fuera de uint32 no cero en {expected_index}"
            )
        values.append(cycles)
    return values


def validate_capture_csv(
    path: Path,
    spec: dict[str, str],
    blocks: Sequence[tuple[int, int, int, int]],
    crc_values: Sequence[int],
) -> None:
    try:
        with path.open("r", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != CAPTURE_CSV_FIELDS:
                raise SelectedPilotSummaryError(
                    f"{path}: cabecera inesperada"
                )
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SelectedPilotSummaryError(
            f"no se pudo leer {path}: {exc}"
        ) from exc
    if len(rows) != EXPECTED_BLOCKS:
        raise SelectedPilotSummaryError(
            f"{path}: contiene {len(rows)} filas, se esperaban {EXPECTED_BLOCKS}"
        )

    method_name = {
        "efork3": "efork3",
        "gl": "gl_caputo",
        "m2sfrk": "m2sfrk",
    }[spec["method"]]
    for index, (row, block, crc_value) in enumerate(
        zip(rows, blocks, crc_values)
    ):
        expected_values = {
            "version": "1",
            "kind": "4",
            "board": spec["board"],
            "system": spec["system"],
            "method": method_name,
            "representation": "cycle_block_u32",
            "status": "0",
            "status_flags": "ok",
            "fixed_state_saturation": "0",
            "fixed_coefficient_saturation": "0",
            "fixed_coefficient_zeroed": "0",
            "payload_bytes": "24",
            "sequence": str(index * 4),
            "cycles": str(block[0]),
            "dropped": "0",
            "cycle_0": str(block[0]),
            "cycle_1": str(block[1]),
            "cycle_2": str(block[2]),
            "cycle_3": str(block[3]),
            "crc32": f"0x{crc_value:08X}",
        }
        for field, expected in expected_values.items():
            if row.get(field) != expected:
                raise SelectedPilotSummaryError(
                    f"{path}: fila {index}, {field}={row.get(field)!r}, "
                    f"se esperaba {expected!r}"
                )


def linear_quantile(sorted_values: Sequence[int], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return (
        float(sorted_values[lower]) * (1.0 - fraction)
        + float(sorted_values[upper]) * fraction
    )


def cycle_statistics(values: Sequence[int]) -> dict[str, float | int]:
    if len(values) != EXPECTED_CYCLE_VALUES:
        raise SelectedPilotSummaryError(
            f"se requieren {EXPECTED_CYCLE_VALUES} ciclos"
        )
    ordered = sorted(values)
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    return {
        "n_cycle_values": len(values),
        "minimum_cycles": ordered[0],
        "p05_cycles": round(linear_quantile(ordered, 0.05), 6),
        "p25_cycles": round(linear_quantile(ordered, 0.25), 6),
        "median_cycles": round(linear_quantile(ordered, 0.50), 6),
        "mean_cycles": round(mean, 6),
        "p75_cycles": round(linear_quantile(ordered, 0.75), 6),
        "p95_cycles": round(linear_quantile(ordered, 0.95), 6),
        "p99_cycles": round(linear_quantile(ordered, 0.99), 6),
        "maximum_cycles": ordered[-1],
        "population_standard_deviation_cycles": round(
            math.sqrt(variance), 6
        ),
    }


def validate_successful_attempt(
    run_path: Path,
    payload: dict[str, Any],
    spec: dict[str, str],
    repetition: int,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, float | int]]:
    source = run_path
    directory = run_path.parent
    cell = require_object(payload, "cell", source)
    endpoint = require_object(payload, "endpoint", source)
    timing = require_object(payload, "timing", source)
    transport = require_object(payload, "transport", source)
    reset = require_object(payload, "reset", source)
    hardware = require_object(payload, "hardware", source)
    capture = require_object(payload, "capture", source)
    build = require_object(payload, "build", source)
    source_metadata = require_object(payload, "source", source)

    require_equal(
        payload.get("schema"),
        "fractional-chaos-physical-run-v1",
        "schema",
        source,
    )
    require_equal(payload.get("campaign_id"), CAMPAIGN_ID, "campaign_id", source)
    require_equal(payload.get("run_id"), directory.name, "run_id", source)
    parsed_repetition, parsed_cell = parse_run_id(directory.name, source)
    require_equal(parsed_repetition, repetition, "run_id.repetition", source)
    require_equal(parsed_cell, spec["cell_id"], "run_id.cell_id", source)
    require_equal(
        payload.get("scheduled_primary_run_id"),
        directory.name.removesuffix("__benchmark-reset-pilot"),
        "scheduled_primary_run_id",
        source,
    )
    require_equal(
        payload.get("scheduled_campaign_run_id"),
        directory.name.removesuffix("__benchmark-reset-pilot"),
        "scheduled_campaign_run_id",
        source,
    )
    require_equal(
        payload.get("manifest_sha256"),
        canonical_json_sha256(manifest),
        "manifest_sha256",
        source,
    )

    for field in ("cell_id", "system", "method", "board", "representation"):
        require_equal(cell.get(field), spec[field], f"cell.{field}", source)
    require_equal(cell.get("cold_start_id"), repetition, "cell.cold_start_id", source)
    require_equal(cell.get("block_id"), f"block-{repetition:02d}", "cell.block_id", source)
    require_equal(
        reset.get("hardware_reset_repetition_id"),
        repetition,
        "reset.hardware_reset_repetition_id",
        source,
    )

    require_equal(payload.get("endpoint_name"), ENDPOINT_NAME, "endpoint_name", source)
    endpoint_expected = {
        "complete": True,
        "expected_blocks": EXPECTED_BLOCKS,
        "received_blocks": EXPECTED_BLOCKS,
        "frame_kind": 4,
        "values_per_frame": 4,
        "expected_first_sequence": 0,
        "first_sequence": 0,
        "expected_last_sequence": 9996,
        "last_sequence": 9996,
        "rule": "complete_kind4_block_set_not_duration",
    }
    for field, expected in endpoint_expected.items():
        require_equal(endpoint.get(field), expected, f"endpoint.{field}", source)

    timing_expected = {
        "accepted_as_solver_timing": True,
        "received_cycle_values": EXPECTED_CYCLE_VALUES,
        "required_cycle_values": EXPECTED_CYCLE_VALUES,
        "all_cycles_nonzero": True,
        "solver_only_window": True,
        "uart_active_during_timed_window": False,
        "warmup_steps": manifest["systems"][spec["system"]][
            "transient_steps"
        ],
    }
    for field, expected in timing_expected.items():
        require_equal(timing.get(field), expected, f"timing.{field}", source)

    require_equal(transport.get("accepted"), True, "transport.accepted", source)
    require_equal(
        transport.get("valid_frames"),
        EXPECTED_BLOCKS,
        "transport.valid_frames",
        source,
    )
    require_equal(
        transport.get("matching_frames"),
        EXPECTED_BLOCKS,
        "transport.matching_frames",
        source,
    )
    for field in ZERO_TRANSPORT_FIELDS:
        require_equal(transport.get(field), 0, f"transport.{field}", source)

    require_equal(
        payload.get("evidence_level"),
        "solver_only_timing_under_stlink_hardware_reset",
        "evidence_level",
        source,
    )
    require_equal(
        payload.get("experimental_unit"),
        "stlink_hardware_reset_repetition",
        "experimental_unit",
        source,
    )
    require_equal(
        payload.get("eligible_as_paper_cold_start"),
        False,
        "eligible_as_paper_cold_start",
        source,
    )
    require_equal(
        payload.get("eligible_as_primary_benchmark"),
        False,
        "eligible_as_primary_benchmark",
        source,
    )
    for owner_name, owner in (("reset", reset), ("hardware", hardware)):
        require_equal(
            owner.get("power_removed"),
            False,
            f"{owner_name}.power_removed",
            source,
        )
    require_equal(reset.get("mode"), "stlink_hardware_reset", "reset.mode", source)
    require_equal(
        reset.get("eligible_as_paper_cold_start"),
        False,
        "reset.eligible_as_paper_cold_start",
        source,
    )
    require_equal(
        hardware.get("reset_mode"),
        "stlink_hardware_reset",
        "hardware.reset_mode",
        source,
    )
    for field in ("board_name", "mcu", "probe_serial", "port", "baud"):
        require_equal(
            hardware.get(field),
            manifest["boards"][spec["board"]][field],
            f"hardware.{field}",
            source,
        )

    require_equal(
        payload.get("system_contract"),
        manifest["systems"][spec["system"]],
        "system_contract",
        source,
    )
    require_equal(
        payload.get("method_contract"),
        manifest["methods"][spec["method"]],
        "method_contract",
        source,
    )
    require_equal(
        payload.get("representation_contract"),
        manifest["representations"][spec["representation"]],
        "representation_contract",
        source,
    )
    require_equal(build.get("benchmark_mode"), True, "build.benchmark_mode", source)
    require_equal(
        build.get("buffered_capture_mode"),
        False,
        "build.buffered_capture_mode",
        source,
    )
    require_equal(build.get("target"), build_target(spec), "build.target", source)
    source_dirty = source_metadata.get("dirty")
    if not isinstance(source_dirty, bool):
        raise SelectedPilotSummaryError(
            f"{source}: source.dirty debe ser booleano"
        )
    source_commit = source_metadata.get("commit")
    if not isinstance(source_commit, str) or re.fullmatch(
        r"[0-9a-f]{40}", source_commit
    ) is None:
        raise SelectedPilotSummaryError(
            f"{source}: source.commit debe ser un hash Git de 40 caracteres"
        )
    source_status_sha256 = require_sha256(
        source_metadata.get("status_sha256"),
        "source.status_sha256",
        source,
    )

    raw_path = require_artifact_hash(
        directory,
        capture.get("raw_path"),
        capture.get("raw_sha256"),
        "capture.raw",
        source,
    )
    capture_csv_path = require_artifact_hash(
        directory,
        capture.get("csv_path"),
        capture.get("csv_sha256"),
        "capture.csv",
        source,
    )
    timing_path = require_artifact_hash(
        directory,
        capture.get("timing_cycles_path"),
        capture.get("timing_cycles_sha256"),
        "capture.timing_cycles",
        source,
    )
    blocks, binary_cycles, crc_values = parse_capture_binary(
        raw_path, spec, manifest
    )
    timing_cycles = read_timing_cycles(timing_path)
    if timing_cycles != binary_cycles:
        raise SelectedPilotSummaryError(
            f"{timing_path}: no coincide con los ciclos de {raw_path}"
        )
    validate_capture_csv(
        capture_csv_path, spec, blocks, crc_values
    )
    stats = cycle_statistics(binary_cycles)

    attempt = {
        "run_id": directory.name,
        "cell_id": spec["cell_id"],
        "system": spec["system"],
        "method": spec["method"],
        "board": spec["board"],
        "representation": spec["representation"],
        "hardware_reset_repetition_id": repetition,
        "artifact_kind": "accepted_run",
        "validation_status": "passed_reset_pilot_contract",
        "eligible_as_reset_pilot_evidence": True,
        "eligible_as_primary_benchmark": False,
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "source_status_sha256": source_status_sha256,
        "run_json": display_path(run_path),
        "run_json_sha256": sha256_file(run_path),
        "capture_bin": display_path(raw_path),
        "capture_bin_sha256": sha256_file(raw_path),
        "capture_csv": display_path(capture_csv_path),
        "capture_csv_sha256": sha256_file(capture_csv_path),
        "timing_cycles_csv": display_path(timing_path),
        "timing_cycles_sha256": sha256_file(timing_path),
        "artifact_files": artifact_inventory(directory),
    }
    return attempt, stats


def validate_failure_attempt(
    failure_path: Path,
    payload: dict[str, Any],
    spec: dict[str, str],
    repetition: int,
) -> dict[str, Any]:
    source = failure_path
    directory = failure_path.parent
    require_equal(
        payload.get("schema"),
        "fractional-chaos-physical-run-failure-v1",
        "schema",
        source,
    )
    require_equal(payload.get("run_id"), directory.name, "run_id", source)
    parsed_repetition, parsed_cell = parse_run_id(directory.name, source)
    require_equal(parsed_repetition, repetition, "run_id.repetition", source)
    require_equal(parsed_cell, spec["cell_id"], "run_id.cell_id", source)
    require_equal(
        payload.get("eligible_as_evidence"),
        False,
        "eligible_as_evidence",
        source,
    )
    for field in ("error", "error_type", "started_utc", "failed_utc"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise SelectedPilotSummaryError(
                f"{source}: {field} debe ser texto no vacío"
            )
    return {
        "run_id": directory.name,
        "cell_id": spec["cell_id"],
        "system": spec["system"],
        "method": spec["method"],
        "board": spec["board"],
        "representation": spec["representation"],
        "hardware_reset_repetition_id": repetition,
        "artifact_kind": "recorded_failure",
        "validation_status": "failure_record_validated",
        "eligible_as_reset_pilot_evidence": False,
        "eligible_as_primary_benchmark": False,
        "failure_json": display_path(failure_path),
        "failure_json_sha256": sha256_file(failure_path),
        "error_type": payload["error_type"],
        "error": payload["error"],
        "started_utc": payload["started_utc"],
        "failed_utc": payload["failed_utc"],
        "artifact_files": artifact_inventory(directory),
    }


def scan_attempts(
    campaign_root: Path,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    run_root = campaign_root.resolve() / "runs"
    if not run_root.is_dir():
        raise SelectedPilotSummaryError(
            f"no existe el directorio de runs: {run_root}"
        )
    specs = expected_cell_specs(manifest)
    attempts: list[dict[str, Any]] = []
    successful: dict[tuple[str, int], dict[str, Any]] = {}
    for directory in sorted(
        (path for path in run_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
    ):
        repetition, identifier = parse_run_id(directory.name, directory)
        spec = specs.get(identifier)
        if spec is None:
            raise SelectedPilotSummaryError(
                f"{directory}: celda fuera de la matriz seleccionada"
            )
        run_path = directory / "run.json"
        failure_path = directory / "failure.json"
        if run_path.is_file() == failure_path.is_file():
            raise SelectedPilotSummaryError(
                f"{directory}: se exige exactamente run.json o failure.json"
            )
        if run_path.is_file():
            payload = load_json_object(run_path)
            attempt, stats = validate_successful_attempt(
                run_path, payload, spec, repetition, manifest
            )
            key = (identifier, repetition)
            if key in successful:
                raise SelectedPilotSummaryError(
                    f"intento aceptado duplicado para {key}"
                )
            attempt["_cycle_statistics"] = stats
            successful[key] = attempt
        else:
            payload = load_json_object(failure_path)
            attempt = validate_failure_attempt(
                failure_path, payload, spec, repetition
            )
        attempts.append(attempt)
    if not attempts:
        raise SelectedPilotSummaryError("no se encontraron intentos")
    return attempts, successful


def selection_role(attempt: dict[str, Any]) -> str:
    identifier = attempt["cell_id"]
    repetition = attempt["hardware_reset_repetition_id"]
    if attempt["artifact_kind"] == "accepted_run":
        if repetition == expected_repetition(identifier):
            return (
                "selected_r05_replacement"
                if repetition == 5
                else "selected_preferred_r04"
            )
        return "accepted_prior_attempt_not_selected"
    if identifier in R05_REPLACEMENT_CELLS and repetition == 4:
        return "failed_r04_retained_replaced_by_r05"
    return "failed_attempt_retained"


def select_matrix(
    attempts: list[dict[str, Any]],
    successful: dict[tuple[str, int], dict[str, Any]],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    specs = expected_cell_specs(manifest)
    failures = {
        (attempt["cell_id"], attempt["hardware_reset_repetition_id"])
        for attempt in attempts
        if attempt["artifact_kind"] == "recorded_failure"
    }
    selected_attempts: list[dict[str, Any]] = []
    for identifier in specs:
        repetition = expected_repetition(identifier)
        attempt = successful.get((identifier, repetition))
        if attempt is None:
            raise SelectedPilotSummaryError(
                f"falta intento aceptado {identifier} r{repetition:02d}"
            )
        if (
            identifier in R05_REPLACEMENT_CELLS
            and (identifier, 4) not in failures
        ):
            raise SelectedPilotSummaryError(
                f"{identifier}: r05 exige conservar el failure.json de r04"
            )
        selected_attempts.append(attempt)

    if len(selected_attempts) != EXPECTED_CELLS:
        raise SelectedPilotSummaryError(
            f"se seleccionaron {len(selected_attempts)} celdas"
        )
    repetitions = Counter(
        attempt["hardware_reset_repetition_id"]
        for attempt in selected_attempts
    )
    if repetitions != Counter({4: 33, 5: 3}):
        raise SelectedPilotSummaryError(
            f"repeticiones seleccionadas inesperadas: {dict(repetitions)}"
        )

    selected_ids = {attempt["run_id"] for attempt in selected_attempts}
    for attempt in attempts:
        attempt["selected"] = attempt["run_id"] in selected_ids
        attempt["selection_role"] = selection_role(attempt)
    return selected_attempts


def selected_cell_record(attempt: dict[str, Any]) -> dict[str, Any]:
    stats = attempt["_cycle_statistics"]
    return {
        "cell_key": "|".join(
            (
                attempt["system"],
                attempt["method"],
                attempt["board"],
                attempt["representation"],
            )
        ),
        "cell_id": attempt["cell_id"],
        "system": attempt["system"],
        "method": attempt["method"],
        "board": attempt["board"],
        "representation": attempt["representation"],
        "hardware_reset_repetition_id": attempt[
            "hardware_reset_repetition_id"
        ],
        "selection_role": selection_role(attempt),
        "source_commit": attempt["source_commit"],
        "source_dirty": attempt["source_dirty"],
        "source_status_sha256": attempt["source_status_sha256"],
        "run_id": attempt["run_id"],
        "run_json": attempt["run_json"],
        "run_json_sha256": attempt["run_json_sha256"],
        "capture_bin": attempt["capture_bin"],
        "capture_bin_sha256": attempt["capture_bin_sha256"],
        "capture_csv": attempt["capture_csv"],
        "capture_csv_sha256": attempt["capture_csv_sha256"],
        "timing_cycles_csv": attempt["timing_cycles_csv"],
        "timing_cycles_sha256": attempt["timing_cycles_sha256"],
        **stats,
    }


def public_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in attempt.items()
        if not key.startswith("_")
    }


def build_report(
    campaign_root: Path = DEFAULT_CAMPAIGN_ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = load_manifest(manifest_path)
    attempts, successful = scan_attempts(campaign_root, manifest)
    selected_attempts = select_matrix(attempts, successful, manifest)
    selected_cells = [
        selected_cell_record(attempt) for attempt in selected_attempts
    ]
    public_attempts = [public_attempt(attempt) for attempt in attempts]
    kind_counts = Counter(
        attempt["artifact_kind"] for attempt in public_attempts
    )
    selected_repetitions = Counter(
        cell["hardware_reset_repetition_id"] for cell in selected_cells
    )
    selected_source_dirty_values = sorted(
        {cell["source_dirty"] for cell in selected_cells}
    )

    report = {
        "schema": (
            "fractional-chaos-selected-benchmark-reset-pilot-summary-v1"
        ),
        "schema_version": 1,
        "status": "accepted_36_of_36_selected_reset_pilot_cells",
        "campaign_id": CAMPAIGN_ID,
        "endpoint_name": ENDPOINT_NAME,
        "scope": (
            "Selected-system solver-only cycle-count matrix under ST-LINK "
            "hardware reset"
        ),
        "evidence_layer": (
            "solver_only_timing_under_stlink_hardware_reset"
        ),
        "experimental_unit": "stlink_hardware_reset_repetition",
        "selection_contract": {
            "expected_cells": EXPECTED_CELLS,
            "selected_runs_per_cell": 1,
            "preferred_repetition": 4,
            "r05_replacement_cells": sorted(R05_REPLACEMENT_CELLS),
            "selected_by_repetition": {
                str(key): selected_repetitions[key]
                for key in sorted(selected_repetitions)
            },
            "cycle_values_per_cell": EXPECTED_CYCLE_VALUES,
            "total_cycle_values": (
                EXPECTED_CELLS * EXPECTED_CYCLE_VALUES
            ),
            "selection_is_post_failure_snapshot_policy": True,
            "selection_rationale": (
                "Use accepted r04 for 33 cells; retain the three failed "
                "F746 fixed M2sFRK r04 attempts and use their accepted r05 "
                "replacements."
            ),
            "quantile_definition": (
                "linear interpolation at (n-1)*p (empirical type 7)"
            ),
            "standard_deviation_definition": "population (ddof=0)",
        },
        "attempt_inventory": {
            "total_attempts": len(public_attempts),
            "accepted_attempts": kind_counts["accepted_run"],
            "failed_attempts": kind_counts["recorded_failure"],
            "selected_attempts": len(selected_cells),
            "accepted_attempts_not_selected": (
                kind_counts["accepted_run"] - len(selected_cells)
            ),
            "failed_attempts_included_without_exclusion": True,
        },
        "evidence_boundaries": {
            "power_removed": False,
            "reset_mode": "stlink_hardware_reset",
            "reset_repetitions_per_selected_cell": 1,
            "n_experimental_units_per_cell": 1,
            "eligible_as_paper_cold_start": False,
            "eligible_as_primary_benchmark": False,
            "source_dirty_values": selected_source_dirty_values,
            "clean_source_provenance": not any(
                selected_source_dirty_values
            ),
            "inferential_statistics_supported": False,
            "between_reset_variability_supported": False,
            "formal_cross_system_performance_claim_supported": False,
            "prohibitions": [
                "No presentar estos pilotos por reset hardware como arranques en frío con ciclo de alimentación.",
                "No reportar estadística inferencial ni incertidumbre entre resets a partir de N=1 por celda.",
                "No promover esta instantánea al benchmark primario prerregistrado.",
                "No presentar estos pilotos como procedentes de un árbol fuente limpio: los 36 run.json registran source.dirty=true.",
                "No tratar los 10 000 ciclos de una ejecución como 10 000 unidades experimentales independientes.",
                "No inferir energía, aleatoriedad, fidelidad numérica ni caos formal a partir de los conteos de ciclos.",
            ],
        },
        "provenance": {
            "generator": display_path(Path(__file__)),
            "generator_sha256": sha256_file(Path(__file__)),
            "campaign_root": display_path(campaign_root),
            "manifest": display_path(manifest_path),
            "manifest_file_sha256": sha256_file(manifest_path),
            "manifest_canonical_sha256": canonical_json_sha256(manifest),
            "inputs_read": [
                "run.json",
                "failure.json",
                "capture.bin",
                "capture.csv",
                "timing_cycles.csv",
                "failure and execution logs for artifact inventory",
            ],
        },
        "cells": selected_cells,
        "attempts": public_attempts,
    }
    return report, selected_cells, public_attempts


CELL_CSV_FIELDS = (
    "cell_key",
    "cell_id",
    "system",
    "method",
    "board",
    "representation",
    "hardware_reset_repetition_id",
    "selection_role",
    "source_commit",
    "source_dirty",
    "source_status_sha256",
    "n_cycle_values",
    "minimum_cycles",
    "p05_cycles",
    "p25_cycles",
    "median_cycles",
    "mean_cycles",
    "p75_cycles",
    "p95_cycles",
    "p99_cycles",
    "maximum_cycles",
    "population_standard_deviation_cycles",
    "run_id",
    "run_json",
    "run_json_sha256",
    "capture_bin",
    "capture_bin_sha256",
    "capture_csv",
    "capture_csv_sha256",
    "timing_cycles_csv",
    "timing_cycles_sha256",
)
ATTEMPT_CSV_FIELDS = (
    "run_id",
    "cell_id",
    "system",
    "method",
    "board",
    "representation",
    "hardware_reset_repetition_id",
    "artifact_kind",
    "validation_status",
    "selected",
    "selection_role",
    "eligible_as_reset_pilot_evidence",
    "eligible_as_primary_benchmark",
    "source_commit",
    "source_dirty",
    "source_status_sha256",
    "run_json",
    "run_json_sha256",
    "failure_json",
    "failure_json_sha256",
    "capture_bin",
    "capture_bin_sha256",
    "capture_csv",
    "capture_csv_sha256",
    "timing_cycles_csv",
    "timing_cycles_sha256",
    "error_type",
    "error",
)


def write_csv(
    rows: Sequence[dict[str, Any]],
    fields: Sequence[str],
    path: Path,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def markdown_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(
    report: dict[str, Any],
    cells: Sequence[dict[str, Any]],
    attempts: Sequence[dict[str, Any]],
) -> str:
    inventory = report["attempt_inventory"]
    boundaries = report["evidence_boundaries"]
    lines = [
        "# Matriz física seleccionada: piloto por reset ST-LINK",
        "",
        f"Estado: `{report['status']}`.",
        "",
        (
            f"Se validaron **{len(cells)}/36 celdas** con una repetición "
            "aceptada por celda y 10 000 valores de ciclos por repetición."
        ),
        (
            f"El inventario conserva {inventory['total_attempts']} intentos: "
            f"{inventory['accepted_attempts']} aceptados y "
            f"{inventory['failed_attempts']} fallidos."
        ),
        "",
        "## Política de selección congelada",
        "",
        "- Se usa `r04` para 33 celdas.",
        (
            "- Se usa `r05` sólo para `chen_m2sfrk_f746_fixed`, "
            "`liu_m2sfrk_f746_fixed` y "
            "`hammouch_mekkaoui_m2sfrk_f746_fixed`."
        ),
        "- Los tres `r04` fallidos correspondientes permanecen en el inventario.",
        "",
        "## Límites de evidencia",
        "",
        (
            "**N=1 reset ST-LINK por celda; no hubo corte y restauración "
            "de alimentación. Los 36 registros tienen `source.dirty=true`. "
            "Este agregado no es el benchmark primario.**"
        ),
        "",
    ]
    for prohibition in boundaries["prohibitions"]:
        lines.append(f"- {prohibition}")

    lines.extend(
        [
            "",
            "## Celdas seleccionadas",
            "",
            (
                "| Sistema | Método | Placa | Representación | ID de repetición seleccionada | "
                "Media | Mediana | P95 | Desv. poblacional |"
            ),
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in cells:
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_escape(row["system"]),
                    markdown_escape(row["method"]),
                    markdown_escape(row["board"]),
                    markdown_escape(row["representation"]),
                    str(row["hardware_reset_repetition_id"]),
                    str(row["mean_cycles"]),
                    str(row["median_cycles"]),
                    str(row["p95_cycles"]),
                    str(row["population_standard_deviation_cycles"]),
                )
            )
            + " |"
        )

    failures = [
        attempt
        for attempt in attempts
        if attempt["artifact_kind"] == "recorded_failure"
    ]
    lines.extend(
        [
            "",
            "## Intentos fallidos retenidos",
            "",
            "| Run | Celda | Rep. | Tipo | Error |",
            "|---|---|---:|---|---|",
        ]
    )
    for attempt in failures:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{markdown_escape(attempt['run_id'])}`",
                    markdown_escape(attempt["cell_id"]),
                    str(attempt["hardware_reset_repetition_id"]),
                    markdown_escape(attempt["error_type"]),
                    markdown_escape(attempt["error"]),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Los hashes y el inventario completo de archivos de cada intento "
            "se encuentran en `summary.json`; `attempts.csv` conserva una "
            "fila por intento.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    cells: Sequence[dict[str, Any]],
    attempts: Sequence[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cell_csv = output_dir / "cells.csv"
    attempt_csv = output_dir / "attempts.csv"
    markdown = output_dir / "report.md"
    summary_json = output_dir / "summary.json"

    write_csv(cells, CELL_CSV_FIELDS, cell_csv)
    write_csv(attempts, ATTEMPT_CSV_FIELDS, attempt_csv)
    markdown.write_text(
        render_markdown(report, cells, attempts),
        encoding="utf-8",
        newline="\n",
    )
    report["artifacts"] = {
        path.name: {
            "path": display_path(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in (cell_csv, attempt_csv, markdown)
    }
    summary_json.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "summary": summary_json,
        "cells": cell_csv,
        "attempts": attempt_csv,
        "markdown": markdown,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=DEFAULT_CAMPAIGN_ROOT,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    report, cells, attempts = build_report(
        args.campaign_root.resolve(),
        args.manifest.resolve(),
    )
    paths = write_outputs(
        report,
        cells,
        attempts,
        args.output_dir.resolve(),
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "selected_cells": len(cells),
                "attempts": len(attempts),
                "failed_attempts": report["attempt_inventory"][
                    "failed_attempts"
                ],
                "summary": str(paths["summary"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
