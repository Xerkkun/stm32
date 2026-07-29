#!/usr/bin/env python3
"""Planifica y ejecuta de forma segura la campaña física STM32.

El plan primario contiene 36 celdas y 30 reinicios por celda.  La ejecución
primaria se mantiene cerrada mientras FCC1 sólo exporte un valor de ciclos por
muestra decimada.  El modo ejecutable actual es un piloto de transporte
claramente etiquetado; nunca se promueve a evidencia primaria de rendimiento.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "validation" / "physical_campaign_manifest.json"
DEFAULT_OUTPUT_ROOT = ROOT / "validation" / "results" / "physical_campaign"
sys.path.insert(0, str(ROOT / "tools"))

import decode_uart  # noqa: E402


class CampaignError(RuntimeError):
    """Error de contrato o seguridad de la campaña."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8")
    atomic_write_bytes(path, payload + b"\n")


def atomic_write_csv(
    path: Path,
    rows: Sequence[dict[str, Any]],
    fieldnames: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def merge_manifest_overrides(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = merge_manifest_overrides(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"No se pudo leer el manifiesto {path}: {exc}") from exc
    if manifest.get("schema") == (
        "fractional-chaos-physical-campaign-derived-v1"
    ):
        base_name = manifest.get("extends")
        overrides = manifest.get("overrides")
        if not isinstance(base_name, str) or not isinstance(overrides, dict):
            raise CampaignError(
                "un manifiesto derivado exige extends y overrides"
            )
        base_path = (path.parent / base_name).resolve()
        allowed_root = (ROOT / "validation").resolve()
        try:
            base_path.relative_to(allowed_root)
        except ValueError as exc:
            raise CampaignError(
                f"manifiesto base fuera de {allowed_root}: {base_path}"
            ) from exc
        if base_path == path.resolve():
            raise CampaignError("un manifiesto derivado no puede extenderse a sí mismo")
        base, _base_sha256 = load_manifest(base_path)
        manifest = merge_manifest_overrides(base, overrides)
    validate_manifest(manifest)
    return manifest, sha256_bytes(canonical_json_bytes(manifest))


def require_exact_set(
    actual: Iterable[str],
    expected: set[str],
    label: str,
) -> None:
    values = list(actual)
    if len(values) != len(set(values)):
        raise CampaignError(f"{label} contiene valores duplicados")
    if set(values) != expected:
        raise CampaignError(
            f"{label} debe ser {sorted(expected)}, no {sorted(values)}"
        )


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "fractional-chaos-physical-campaign-v1":
        raise CampaignError("schema de campaña desconocido")
    campaign_id = manifest.get("campaign_id")
    if not isinstance(campaign_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]{5,79}", campaign_id
    ):
        raise CampaignError("campaign_id no es válido")

    matrix = manifest.get("paper_matrix", {})
    require_exact_set(
        matrix.get("systems", []),
        {"lorenz", "rossler", "chen"},
        "paper_matrix.systems",
    )
    require_exact_set(
        matrix.get("methods", []),
        {"efork3", "gl", "m2sfrk"},
        "paper_matrix.methods",
    )
    require_exact_set(
        matrix.get("representations", []),
        {"float32", "fixed_q14_q30"},
        "paper_matrix.representations",
    )
    require_exact_set(
        matrix.get("boards", []),
        {"f746", "h755"},
        "paper_matrix.boards",
    )
    if manifest.get("schedule", {}).get("cold_starts_per_cell") != 30:
        raise CampaignError("la campaña primaria exige 30 cold starts por celda")
    if (
        manifest.get("schedule", {}).get("randomization")
        != "randomized_complete_blocks"
    ):
        raise CampaignError("la aleatorización debe usar bloques completos")
    if (
        manifest.get("schedule", {}).get("randomization_algorithm")
        != "sha256_rank_v1"
    ):
        raise CampaignError("algoritmo de aleatorización desconocido")

    expected_transients = {"lorenz": 2000, "rossler": 5000, "chen": 2000}
    for system, expected_transient in expected_transients.items():
        contract = manifest.get("systems", {}).get(system, {})
        if contract.get("transient_steps") != expected_transient:
            raise CampaignError(
                f"transient_steps de {system} no coincide con el firmware "
                f"benchmark ({expected_transient})"
            )

    boards = manifest.get("boards", {})
    ports = [boards[name].get("port") for name in matrix["boards"]]
    probes = [boards[name].get("probe_serial") for name in matrix["boards"]]
    if len(set(ports)) != len(ports) or any(not port for port in ports):
        raise CampaignError("cada placa debe tener un puerto serie explícito y único")
    if len(set(probes)) != len(probes) or any(not probe for probe in probes):
        raise CampaignError("cada placa debe tener un ST-LINK serial explícito y único")

    endpoints = manifest.get("endpoints", {})
    primary = endpoints.get("primary_benchmark", {})
    if primary.get("required_cycle_values_per_cold_start") != 10_000:
        raise CampaignError("primary_benchmark debe exigir 10000 valores de ciclos")
    if primary.get("ready") is not False:
        raise CampaignError(
            "el reset ST-LINK no permite marcar primary_benchmark como listo"
        )
    benchmark_pilot = endpoints.get("benchmark_reset_pilot", {})
    if (
        benchmark_pilot.get("ready") is not True
        or benchmark_pilot.get("frame_kind") != 4
        or benchmark_pilot.get("required_cycle_values") != 10_000
    ):
        raise CampaignError("contrato benchmark_reset_pilot inválido")
    dense_pilot = endpoints.get("dense_timeseries_pilot")
    if dense_pilot is not None:
        if (
            dense_pilot.get("ready") is not True
            or dense_pilot.get("buffered_capture_mode") is not True
            or dense_pilot.get("output_decimation") != 1
            or dense_pilot.get("required_frames") != 12_000
            or dense_pilot.get("first_sequence") != 1
            or dense_pilot.get("last_sequence") != 12_000
            or dense_pilot.get("sequence_increment") != 1
            or dense_pilot.get("eligible_as_primary_benchmark") is not False
        ):
            raise CampaignError("contrato dense_timeseries_pilot inválido")
        require_exact_set(
            manifest.get("dense_capture_cells", []),
            {
                "lorenz_m2sfrk_f746_float32",
                "lorenz_m2sfrk_f746_fixed",
                "lorenz_m2sfrk_h755_float32",
                "lorenz_m2sfrk_h755_fixed",
            },
            "dense_capture_cells",
        )
        build = manifest.get("build", {})
        if (
            build.get("dense_buffered_capture_samples") != 12_000
            or not isinstance(build.get("dense_directory_pattern"), str)
            or "{buffered_capture_samples}"
            not in build["dense_directory_pattern"]
        ):
            raise CampaignError(
                "el build denso debe fijar 12000 muestras y un directorio "
                "dedicado"
            )
        if manifest.get("safety", {}).get(
            "maximum_dense_timeseries_pilot_runs_per_invocation"
        ) != 2:
            raise CampaignError(
                "dense_timeseries_pilot debe admitir como máximo 2 runs"
            )
    if manifest.get("reset_contract", {}).get(
        "eligible_as_paper_cold_start"
    ) is not False:
        raise CampaignError(
            "el reset ST-LINK actual no puede declararse power-cycle cold start"
        )

    all_ids = {cell["cell_id"] for cell in matrix_cells(manifest)}
    for cell_id, calibration in manifest.get("calibrated_cells", {}).items():
        if cell_id not in all_ids:
            raise CampaignError(f"calibración para celda inexistente: {cell_id}")
        decimation = calibration.get("decimation")
        if not isinstance(decimation, int) or decimation <= 0:
            raise CampaignError(f"decimación inválida para {cell_id}")


def campaign_cell_id(
    system: str,
    method: str,
    board: str,
    representation: str,
) -> str:
    representation_token = (
        "fixed" if representation == "fixed_q14_q30" else representation
    )
    return f"{system}_{method}_{board}_{representation_token}"


def matrix_cells(manifest: dict[str, Any]) -> list[dict[str, str]]:
    matrix = manifest["paper_matrix"]
    cells: list[dict[str, str]] = []
    for system in matrix["systems"]:
        for method in matrix["methods"]:
            for board in matrix["boards"]:
                for representation in matrix["representations"]:
                    cells.append(
                        {
                            "cell_id": campaign_cell_id(
                                system, method, board, representation
                            ),
                            "system": system,
                            "method": method,
                            "board": board,
                            "representation": representation,
                        }
                    )
    if len(cells) != 36:
        raise CampaignError(f"la matriz produjo {len(cells)} celdas, no 36")
    return cells


def primary_run_id(
    campaign_id: str,
    cell_id: str,
    cold_start_id: int,
    seed: int,
) -> str:
    identity = {
        "campaign_id": campaign_id,
        "cell_id": cell_id,
        "cold_start_id": cold_start_id,
        "seed": seed,
    }
    suffix = sha256_bytes(canonical_json_bytes(identity))[:10]
    return (
        f"{campaign_id}__r{cold_start_id:02d}__{cell_id}__{suffix}"
    )


def generate_schedule(
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> list[dict[str, Any]]:
    schedule_contract = manifest["schedule"]
    repetitions = schedule_contract["cold_starts_per_cell"]
    seed = schedule_contract["seed"]
    cells = matrix_cells(manifest)
    calibrations = manifest.get("calibrated_cells", {})
    schedule: list[dict[str, Any]] = []
    order_index = 0

    for cold_start_id in range(1, repetitions + 1):
        block = [dict(cell) for cell in cells]
        block.sort(
            key=lambda cell: sha256_bytes(
                (
                    f"{seed}:{cold_start_id}:{cell['cell_id']}"
                ).encode("ascii")
            )
        )
        for cell in block:
            order_index += 1
            calibration = calibrations.get(cell["cell_id"])
            schedule.append(
                {
                    "order_index": order_index,
                    "block_id": f"block-{cold_start_id:02d}",
                    "cold_start_id": cold_start_id,
                    **cell,
                    "calibration_status": (
                        "accepted_pilot" if calibration else "pending"
                    ),
                    "decimation": (
                        calibration["decimation"] if calibration else None
                    ),
                    "run_id": primary_run_id(
                        manifest["campaign_id"],
                        cell["cell_id"],
                        cold_start_id,
                        seed,
                    ),
                    "manifest_sha256": manifest_sha256,
                }
            )
    return schedule


def validate_schedule(
    schedule: Sequence[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    cells = {cell["cell_id"] for cell in matrix_cells(manifest)}
    repetitions = manifest["schedule"]["cold_starts_per_cell"]
    expected_count = len(cells) * repetitions
    if len(schedule) != expected_count:
        raise CampaignError(
            f"la agenda tiene {len(schedule)} ejecuciones, no {expected_count}"
        )
    run_ids = [row["run_id"] for row in schedule]
    if len(run_ids) != len(set(run_ids)):
        raise CampaignError("la agenda contiene run_id duplicados")
    for repetition in range(1, repetitions + 1):
        block_cells = {
            row["cell_id"]
            for row in schedule
            if row["cold_start_id"] == repetition
        }
        if block_cells != cells:
            raise CampaignError(
                f"el bloque {repetition} no contiene exactamente las 36 celdas"
            )


def plan_payload(
    manifest: dict[str, Any],
    manifest_sha256: str,
    schedule: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    calibrated = sum(
        row["calibration_status"] == "accepted_pilot"
        for row in schedule[:36]
    )
    payload = {
        "schema": "fractional-chaos-physical-campaign-plan-v1",
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": manifest_sha256,
        "matrix_cells": 36,
        "cold_starts_per_cell": manifest["schedule"][
            "cold_starts_per_cell"
        ],
        "scheduled_runs": len(schedule),
        "randomization": manifest["schedule"]["randomization"],
        "randomization_algorithm": manifest["schedule"][
            "randomization_algorithm"
        ],
        "seed": manifest["schedule"]["seed"],
        "calibrated_cells": calibrated,
        "uncalibrated_cells": 36 - calibrated,
        "primary_benchmark_ready": False,
        "primary_blocking_reasons": manifest["endpoints"][
            "primary_benchmark"
        ]["blocking_reasons"],
        "executable_endpoint": "benchmark_reset_pilot",
        "pilot_evidence_level": manifest["endpoints"][
            "benchmark_reset_pilot"
        ][
            "evidence_level"
        ],
        "schedule_sha256": sha256_bytes(canonical_json_bytes(schedule)),
    }
    dense_contract = manifest.get("endpoints", {}).get(
        "dense_timeseries_pilot"
    )
    if dense_contract is not None:
        payload.update(
            {
                "dense_timeseries_pilot_ready": True,
                "dense_capture_cells": list(manifest["dense_capture_cells"]),
                "dense_capture_cell_count": len(
                    manifest["dense_capture_cells"]
                ),
                "dense_capture_evidence_level": dense_contract[
                    "evidence_level"
                ],
                "dense_capture_eligible_as_primary_benchmark": False,
            }
        )
    return payload


SCHEDULE_FIELDS = (
    "order_index",
    "block_id",
    "cold_start_id",
    "run_id",
    "cell_id",
    "system",
    "method",
    "board",
    "representation",
    "decimation",
    "calibration_status",
    "manifest_sha256",
)


def write_plan(
    output_root: Path,
    manifest: dict[str, Any],
    manifest_sha256: str,
    schedule: Sequence[dict[str, Any]],
) -> tuple[Path, Path]:
    plan_directory = output_root / manifest["campaign_id"] / "plan"
    plan_path = plan_directory / "campaign_plan.json"
    schedule_path = plan_directory / "campaign_schedule.csv"
    atomic_write_json(
        plan_path,
        plan_payload(manifest, manifest_sha256, schedule),
    )
    atomic_write_csv(schedule_path, list(schedule), SCHEDULE_FIELDS)
    return plan_path, schedule_path


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class AtomicLock:
    """Lock JSON creado de forma atómica; nunca rompe un lock vivo."""

    def __init__(
        self,
        directory: Path,
        name: str,
        *,
        run_id: str,
        break_stale: bool = False,
    ) -> None:
        self.directory = directory
        self.path = directory / f"{safe_name(name)}.lock"
        self.run_id = run_id
        self.break_stale = break_stale
        self.acquired = False

    def _owner(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _stale(self, owner: dict[str, Any]) -> bool:
        if owner.get("host") != socket.gethostname():
            return False
        pid = owner.get("pid")
        return isinstance(pid, int) and not process_is_alive(pid)

    def __enter__(self) -> "AtomicLock":
        self.directory.mkdir(parents=True, exist_ok=True)
        owner = {
            "schema": "fractional-chaos-lock-v1",
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_utc": utc_now(),
            "run_id": self.run_id,
        }
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError as exc:
                current = self._owner()
                if (
                    attempt == 0
                    and self.break_stale
                    and self._stale(current)
                ):
                    self.path.unlink()
                    continue
                raise CampaignError(
                    f"recurso bloqueado por {self.path}: {current or 'lock ilegible'}"
                ) from exc
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(owner, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.acquired = True
            return self
        raise CampaignError(f"no se pudo adquirir {self.path}")

    def __exit__(self, *_args: object) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.acquired = False


@dataclass
class ParserCounters:
    bytes_received: int = 0
    valid_frames: int = 0
    crc_errors: int = 0
    invalid_headers: int = 0
    noise_bytes: int = 0


class FCC1StreamParser:
    """Parser incremental FCC1 que conserva contadores de descarte."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.counters = ParserCounters()

    def feed(self, chunk: bytes) -> list[tuple[int, ...]]:
        self.counters.bytes_received += len(chunk)
        self.buffer.extend(chunk)
        frames: list[tuple[int, ...]] = []

        while True:
            start = self.buffer.find(decode_uart.SYNC)
            if start < 0:
                removable = max(0, len(self.buffer) - len(decode_uart.SYNC) + 1)
                if removable:
                    del self.buffer[:removable]
                    self.counters.noise_bytes += removable
                break
            if start:
                del self.buffer[:start]
                self.counters.noise_bytes += start
            if len(self.buffer) < decode_uart.FRAME.size:
                break

            raw = bytes(self.buffer[: decode_uart.FRAME.size])
            values = decode_uart.FRAME.unpack(raw)
            expected_crc = values[-1]
            actual_crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
            if expected_crc != actual_crc:
                self.counters.crc_errors += 1
                del self.buffer[0]
                self.counters.noise_bytes += 1
                continue
            if not decode_uart.valid_header(values):
                self.counters.invalid_headers += 1
                del self.buffer[0]
                self.counters.noise_bytes += 1
                continue

            del self.buffer[: decode_uart.FRAME.size]
            self.counters.valid_frames += 1
            frames.append(values)
        return frames

    @property
    def trailing_bytes(self) -> int:
        return len(self.buffer)


def build_directory(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> Path:
    if benchmark_mode and buffered_capture_mode:
        raise CampaignError(
            "benchmark_mode y buffered_capture_mode son mutuamente excluyentes"
        )
    build = manifest["build"]
    if benchmark_mode:
        pattern = build["benchmark_directory_pattern"]
    elif buffered_capture_mode:
        pattern = build["dense_directory_pattern"]
    else:
        pattern = build["directory_pattern"]
    relative = pattern.format(
        root=build["root"],
        board=cell["board"],
        decimation=decimation,
        buffered_capture_samples=build.get(
            "dense_buffered_capture_samples", 12_000
        ),
    )
    path = (ROOT / Path(relative)).resolve()
    allowed = (ROOT / "build" / "campaign").resolve()
    try:
        path.relative_to(allowed)
    except ValueError as exc:
        raise CampaignError(f"build dir fuera de {allowed}: {path}") from exc
    return path


def firmware_target(
    manifest: dict[str, Any],
    cell: dict[str, Any],
) -> str:
    suffix = manifest["representations"][cell["representation"]][
        "target_suffix"
    ]
    if cell["board"] == "f746":
        return f"f746_{cell['system']}_{cell['method']}{suffix}"
    return f"h755_m7_{cell['system']}_{cell['method']}{suffix}"


def image_paths(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> list[Path]:
    directory = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
    )
    paths = [directory / f"{firmware_target(manifest, cell)}.hex"]
    if cell["board"] == "h755":
        paths.append(directory / "h755_m4_uart.hex")
    return paths


def map_paths(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> list[Path]:
    directory = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
    )
    paths = [directory / f"{firmware_target(manifest, cell)}.map"]
    if cell["board"] == "h755":
        paths.append(directory / "h755_m4_uart.map")
    return paths


def powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable is None:
        raise CampaignError("no se encontró powershell.exe")
    return executable


def build_command(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> list[str]:
    command = [
        powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "tools" / "build_campaign.ps1"),
        "-Board",
        cell["board"],
        "-Decimation",
        str(decimation),
        "-Target",
        firmware_target(manifest, cell),
    ]
    if benchmark_mode:
        command.append("-BenchmarkMode")
    if buffered_capture_mode:
        command.extend(
            (
                "-BufferedCaptureMode",
                "-BufferedCaptureSamples",
                str(manifest["build"]["dense_buffered_capture_samples"]),
            )
        )
    return command


def flash_command(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    reset_only: bool = False,
    no_reset: bool = False,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> list[str]:
    if reset_only and no_reset:
        raise CampaignError("reset_only y no_reset son mutuamente excluyentes")
    board = manifest["boards"][cell["board"]]
    representation = manifest["representations"][cell["representation"]]
    command = [
        powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "tools" / "flash.ps1"),
        "-Board",
        cell["board"],
        "-System",
        cell["system"],
        "-Method",
        cell["method"],
        "-Representation",
        representation["flash_name"],
        "-ProbeSerial",
        board["probe_serial"],
        "-BuildDirectory",
        str(
            build_directory(
                manifest,
                cell,
                decimation,
                benchmark_mode=benchmark_mode,
                buffered_capture_mode=buffered_capture_mode,
            )
        ),
    ]
    if reset_only:
        command.append("-ResetOnly")
    if no_reset:
        command.append("-NoReset")
    return command


def run_process(
    command: Sequence[str],
    log_path: Path,
    timeout_s: float,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        try:
            completed = subprocess.run(
                list(command),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CampaignError(
                f"timeout de {timeout_s}s en {' '.join(command[:6])}"
            ) from exc
    if completed.returncode != 0:
        raise CampaignError(
            f"el proceso terminó con código {completed.returncode}; vea {log_path}"
        )


def ensure_build(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    log_path: Path,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
) -> list[Path]:
    run_process(
        build_command(
            manifest,
            cell,
            decimation,
            benchmark_mode=benchmark_mode,
            buffered_capture_mode=buffered_capture_mode,
        ),
        log_path,
        float(manifest["timeouts_s"]["build"]),
    )
    images = image_paths(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
    )
    missing = [path for path in images if not path.is_file()]
    missing.extend(
        path
        for path in map_paths(
            manifest,
            cell,
            decimation,
            benchmark_mode=benchmark_mode,
            buffered_capture_mode=buffered_capture_mode,
        )
        if not path.is_file()
    )
    if missing:
        raise CampaignError(f"faltan imágenes después del build: {missing}")
    return images


def expected_identity(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    *,
    endpoint: str = "transport_pilot",
) -> tuple[int, int, int, int]:
    kind = (
        manifest["endpoints"]["benchmark_reset_pilot"]["frame_kind"]
        if endpoint == "benchmark_reset_pilot"
        else manifest["representations"][cell["representation"]]["wire_kind"]
    )
    return (
        kind,
        manifest["boards"][cell["board"]]["wire_id"],
        manifest["systems"][cell["system"]]["wire_id"],
        manifest["methods"][cell["method"]]["wire_id"],
    )


def frame_matches_identity(
    values: tuple[int, ...],
    identity: tuple[int, int, int, int],
) -> bool:
    return (values[2], values[3], values[4], values[5]) == identity


def sequence_endpoint(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
) -> int:
    transient = manifest["systems"][cell["system"]]["transient_steps"]
    timed = manifest["endpoints"]["transport_pilot"][
        "timed_steps_after_warmup"
    ]
    requested = transient + timed
    return int(math.ceil(requested / decimation) * decimation)


def capture_after_reset(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    process_log: Path,
    *,
    endpoint: str = "transport_pilot",
) -> tuple[bytes, list[tuple[int, ...]], FCC1StreamParser, dict[str, float]]:
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise CampaignError(
            "pyserial es obligatorio para ejecutar una adquisición física"
        ) from exc

    board = manifest["boards"][cell["board"]]
    timeouts = manifest["timeouts_s"]
    parser = FCC1StreamParser()
    frames: list[tuple[int, ...]] = []
    raw = bytearray()
    benchmark_mode = endpoint == "benchmark_reset_pilot"
    buffered_capture_mode = endpoint == "dense_timeseries_pilot"
    identity = expected_identity(manifest, cell, endpoint=endpoint)
    if benchmark_mode:
        target = manifest["endpoints"]["benchmark_reset_pilot"][
            "last_sequence"
        ]
    elif buffered_capture_mode:
        target = manifest["endpoints"]["dense_timeseries_pilot"][
            "last_sequence"
        ]
    else:
        target = sequence_endpoint(manifest, cell, decimation)
    first_matching_sequence: int | None = None
    endpoint_reached = False
    first_frame_timeout = float(
        timeouts["endpoint"] if benchmark_mode else timeouts["first_frame"]
    )
    process_started = time.monotonic()
    process_finished: float | None = None
    first_frame_deadline: float | None = None
    endpoint_deadline: float | None = None

    process_log.parent.mkdir(parents=True, exist_ok=True)
    with serial.Serial(
        board["port"],
        board["baud"],
        timeout=float(timeouts["serial_read"]),
    ) as port, process_log.open("wb") as log:
        try:
            port.set_buffer_size(rx_size=1024 * 1024)
        except (AttributeError, OSError):
            pass
        port.reset_input_buffer()
        process = subprocess.Popen(
            flash_command(
                manifest,
                cell,
                decimation,
                reset_only=True,
                benchmark_mode=benchmark_mode,
                buffered_capture_mode=buffered_capture_mode,
            ),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        reset_deadline = process_started + float(timeouts["flash"])

        try:
            while True:
                now = time.monotonic()
                return_code = process.poll()
                if return_code is None and now >= reset_deadline:
                    process.kill()
                    process.wait(timeout=5)
                    raise CampaignError(
                        f"timeout de reset ({timeouts['flash']}s)"
                    )
                if return_code is not None and process_finished is None:
                    process_finished = now
                    if return_code != 0:
                        raise CampaignError(
                            f"reset terminó con código {return_code}; "
                            f"vea {process_log}"
                        )
                    first_frame_deadline = now + first_frame_timeout
                    endpoint_deadline = now + float(timeouts["endpoint"])

                waiting = getattr(port, "in_waiting", 0)
                chunk = port.read(min(max(waiting, 1), 4096))
                if chunk:
                    raw.extend(chunk)
                    for values in parser.feed(chunk):
                        frames.append(values)
                        if frame_matches_identity(values, identity):
                            sequence = int(values[8])
                            if first_matching_sequence is None:
                                first_matching_sequence = sequence
                            if sequence >= target:
                                endpoint_reached = True

                now = time.monotonic()
                if process_finished is not None:
                    if (
                        first_matching_sequence is None
                        and first_frame_deadline is not None
                        and now >= first_frame_deadline
                    ):
                        raise CampaignError(
                            f"no llegó FCC1 válido antes del watchdog "
                            f"de {first_frame_timeout}s"
                        )
                    if (
                        not endpoint_reached
                        and endpoint_deadline is not None
                        and now >= endpoint_deadline
                    ):
                        raise CampaignError(
                            f"no se alcanzó el endpoint sequence={target} en "
                            f"{timeouts['endpoint']}s"
                        )
                    if endpoint_reached:
                        break
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    finished = time.monotonic()
    return (
        bytes(raw),
        frames,
        parser,
        {
            "reset_wall_time_s": (
                (process_finished or finished) - process_started
            ),
            "capture_wall_time_s": finished - process_started,
        },
    )


def summarize_dense_capture(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    frames: Sequence[tuple[int, ...]],
    parser: FCC1StreamParser,
) -> dict[str, Any]:
    contract = manifest["endpoints"]["dense_timeseries_pilot"]
    identity = expected_identity(
        manifest, cell, endpoint="dense_timeseries_pilot"
    )
    matching = [
        values for values in frames if frame_matches_identity(values, identity)
    ]
    identity_mismatches = len(frames) - len(matching)
    sequences = [int(values[8]) for values in matching]
    expected_sequences = list(
        range(
            int(contract["first_sequence"]),
            int(contract["last_sequence"]) + 1,
            int(contract["sequence_increment"]),
        )
    )
    nonzero_status = sum(int(values[6]) != 0 for values in matching)
    nonzero_dropped = sum(int(values[10]) != 0 for values in matching)
    exact_sequence_window = sequences == expected_sequences
    transport_accepted = bool(
        exact_sequence_window
        and len(matching) == int(contract["required_frames"])
        and identity_mismatches == 0
        and nonzero_status == 0
        and nonzero_dropped == 0
        and parser.counters.crc_errors == 0
        and parser.counters.invalid_headers == 0
    )
    cycles = [int(values[9]) for values in matching]
    return {
        "endpoint": {
            "rule": "exact_consecutive_model_step_sequence_window",
            "buffered_capture_mode": True,
            "output_decimation": 1,
            "first_sequence": sequences[0] if sequences else None,
            "last_sequence": sequences[-1] if sequences else None,
            "expected_first_sequence": int(contract["first_sequence"]),
            "expected_last_sequence": int(contract["last_sequence"]),
            "expected_frames": int(contract["required_frames"]),
            "received_frames": len(matching),
            "sequence_increment": int(contract["sequence_increment"]),
            "complete": exact_sequence_window,
            "watchdog_s": manifest["timeouts_s"]["endpoint"],
        },
        "transport": {
            "accepted": transport_accepted,
            "valid_frames": len(frames),
            "matching_frames": len(matching),
            "identity_mismatches": identity_mismatches,
            "sequence_gaps": sum(
                current - previous != 1
                for previous, current in zip(sequences, sequences[1:])
            ),
            "nonzero_status_frames": nonzero_status,
            "nonzero_dropped_frames": nonzero_dropped,
            "crc_errors": parser.counters.crc_errors,
            "invalid_headers": parser.counters.invalid_headers,
            "noise_bytes": parser.counters.noise_bytes,
            "trailing_bytes": parser.trailing_bytes,
            "bytes_received": parser.counters.bytes_received,
        },
        "time_series": {
            "accepted_for_dense_series_analysis": transport_accepted,
            "states_buffered_before_uart_drain": int(
                contract["required_frames"]
            ),
            "sample_spacing_model_steps": 1,
            "sample_interval_model_time": manifest["systems"][
                cell["system"]
            ]["dt_s"],
            "host_reception_time_is_model_time": False,
        },
        "timing": {
            "reported_cycle_values": len(cycles),
            "all_reported_cycles_nonzero": bool(cycles)
            and all(value > 0 for value in cycles),
            "accepted_as_solver_timing": False,
            "eligible_as_primary_benchmark": False,
            "ineligibility_reason": (
                "dense_timeseries_pilot is a state-series acquisition "
                "endpoint, not a randomized primary timing repetition"
            ),
        },
        "evidence_level": contract["evidence_level"],
        "eligible_as_primary_benchmark": False,
        "eligible_as_paper_cold_start": False,
    }


def summarize_capture(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    frames: Sequence[tuple[int, ...]],
    parser: FCC1StreamParser,
) -> dict[str, Any]:
    identity = expected_identity(manifest, cell)
    matching = [
        values for values in frames if frame_matches_identity(values, identity)
    ]
    identity_mismatches = len(frames) - len(matching)
    sequences = [int(values[8]) for values in matching]
    target = sequence_endpoint(manifest, cell, decimation)
    transient = manifest["systems"][cell["system"]]["transient_steps"]
    sequence_gaps = sum(
        current - previous != decimation
        for previous, current in zip(sequences, sequences[1:])
    )
    nonzero_status = sum(int(values[6]) != 0 for values in matching)
    nonzero_dropped = sum(int(values[10]) != 0 for values in matching)
    timed_cycle_values = [
        int(values[9])
        for values in matching
        if transient < int(values[8]) <= target
    ]
    transport_accepted = bool(
        matching
        and sequences[0] == decimation
        and sequences[-1] >= target
        and sequence_gaps == 0
        and nonzero_status == 0
        and nonzero_dropped == 0
        and identity_mismatches == 0
        and parser.counters.crc_errors == 0
        and parser.counters.invalid_headers == 0
    )
    return {
        "endpoint": {
            "rule": "sequence_not_duration",
            "transient_steps": transient,
            "timed_steps_requested": 10_000,
            "target_sequence": target,
            "last_sequence": sequences[-1] if sequences else None,
            "watchdog_s": manifest["timeouts_s"]["endpoint"],
            "reached": bool(sequences and sequences[-1] >= target),
        },
        "transport": {
            "accepted": transport_accepted,
            "valid_frames": len(frames),
            "matching_frames": len(matching),
            "identity_mismatches": identity_mismatches,
            "first_sequence": sequences[0] if sequences else None,
            "expected_first_sequence": decimation,
            "sequence_gaps": sequence_gaps,
            "nonzero_status_frames": nonzero_status,
            "nonzero_dropped_frames": nonzero_dropped,
            "crc_errors": parser.counters.crc_errors,
            "invalid_headers": parser.counters.invalid_headers,
            "noise_bytes": parser.counters.noise_bytes,
            "trailing_bytes": parser.trailing_bytes,
            "bytes_received": parser.counters.bytes_received,
        },
        "timing": {
            "required_cycle_values_for_primary": 10_000,
            "reported_decimated_cycle_values_after_warmup": len(
                timed_cycle_values
            ),
            "all_reported_cycles_nonzero": bool(timed_cycle_values)
            and all(value > 0 for value in timed_cycle_values),
            "eligible_as_primary_benchmark": False,
            "ineligibility_reason": (
                "FCC1 v1 carries one cycle value per decimated state; "
                "the required 10000 per cold start are not preserved"
            ),
        },
        "evidence_level": "transport_and_decimated_cycle_subsample_only",
        "eligible_as_paper_cold_start": False,
    }


def timing_values_from_frames(
    frames: Sequence[tuple[int, ...]],
) -> list[int]:
    values: list[int] = []
    for frame in frames:
        values.extend(
            (
                int(frame[9]),
                int(frame[11]),
                int(frame[12]),
                int(frame[13]),
            )
        )
    return values


def summarize_benchmark_capture(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    frames: Sequence[tuple[int, ...]],
    parser: FCC1StreamParser,
) -> tuple[dict[str, Any], list[int]]:
    contract = manifest["endpoints"]["benchmark_reset_pilot"]
    identity = expected_identity(
        manifest, cell, endpoint="benchmark_reset_pilot"
    )
    matching = [
        values for values in frames if frame_matches_identity(values, identity)
    ]
    identity_mismatches = len(frames) - len(matching)
    sequences = [int(values[8]) for values in matching]
    expected_sequences = list(
        range(
            int(contract["first_sequence"]),
            int(contract["last_sequence"]) + 1,
            int(contract["sequence_increment"]),
        )
    )
    cycle_values = timing_values_from_frames(matching)
    nonzero_status = sum(int(values[6]) != 0 for values in matching)
    nonzero_dropped = sum(int(values[10]) != 0 for values in matching)
    complete_blocks = sequences == expected_sequences
    transport_accepted = bool(
        complete_blocks
        and identity_mismatches == 0
        and nonzero_status == 0
        and nonzero_dropped == 0
        and parser.counters.crc_errors == 0
        and parser.counters.invalid_headers == 0
    )
    timing_accepted = bool(
        transport_accepted
        and len(cycle_values) == int(contract["required_cycle_values"])
        and all(value > 0 for value in cycle_values)
    )
    summary = {
        "endpoint": {
            "rule": "complete_kind4_block_set_not_duration",
            "frame_kind": int(contract["frame_kind"]),
            "values_per_frame": int(contract["values_per_frame"]),
            "first_sequence": sequences[0] if sequences else None,
            "last_sequence": sequences[-1] if sequences else None,
            "expected_first_sequence": int(contract["first_sequence"]),
            "expected_last_sequence": int(contract["last_sequence"]),
            "expected_blocks": len(expected_sequences),
            "received_blocks": len(matching),
            "complete": complete_blocks,
            "watchdog_s": manifest["timeouts_s"]["endpoint"],
        },
        "transport": {
            "accepted": transport_accepted,
            "valid_frames": len(frames),
            "matching_frames": len(matching),
            "identity_mismatches": identity_mismatches,
            "nonzero_status_frames": nonzero_status,
            "nonzero_dropped_frames": nonzero_dropped,
            "crc_errors": parser.counters.crc_errors,
            "invalid_headers": parser.counters.invalid_headers,
            "noise_bytes": parser.counters.noise_bytes,
            "trailing_bytes": parser.trailing_bytes,
            "bytes_received": parser.counters.bytes_received,
        },
        "timing": {
            "warmup_steps": manifest["systems"][cell["system"]][
                "transient_steps"
            ],
            "required_cycle_values": int(contract["required_cycle_values"]),
            "received_cycle_values": len(cycle_values),
            "all_cycles_nonzero": bool(cycle_values)
            and all(value > 0 for value in cycle_values),
            "solver_only_window": True,
            "uart_active_during_timed_window": False,
            "accepted_as_solver_timing": timing_accepted,
        },
        "reset": {
            "mode": manifest["reset_contract"]["implemented_mode"],
            "preparation": manifest["reset_contract"]["preparation"],
            "hardware_reset_repetition_id": cell.get("cold_start_id"),
            "power_removed": False,
            "eligible_as_paper_cold_start": False,
        },
        "evidence_level": contract["evidence_level"],
        "eligible_as_primary_benchmark": False,
        "eligible_as_paper_cold_start": False,
    }
    return summary, cycle_values


def git_metadata(
    *,
    exclude_generated_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status_arguments = [
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
    ]
    excluded: list[str] = []
    for path in exclude_generated_paths:
        try:
            relative = path.resolve().relative_to(ROOT.resolve())
        except ValueError:
            continue
        relative_text = relative.as_posix().rstrip("/")
        if not relative_text:
            continue
        excluded.append(relative_text)
        status_arguments.append(f":(exclude){relative_text}/**")

    status = git(*status_arguments)
    return {
        "commit": git("rev-parse", "HEAD") or None,
        "dirty": bool(status),
        "status_sha256": sha256_bytes(status.encode("utf-8")),
        "status_scope": "source tree excluding generated run and build paths",
        "excluded_generated_paths": excluded,
    }


def pilot_run_id(scheduled: dict[str, Any], endpoint: str) -> str:
    suffixes = {
        "benchmark_reset_pilot": "benchmark-reset-pilot",
        "transport_pilot": "transport-pilot",
        "dense_timeseries_pilot": "dense-timeseries-pilot",
    }
    try:
        suffix = suffixes[endpoint]
    except KeyError as exc:
        raise CampaignError(f"endpoint sin run-id propio: {endpoint}") from exc
    return f"{scheduled['run_id']}__{suffix}"


def pilot_run_directory(
    output_root: Path,
    campaign_id: str,
    scheduled: dict[str, Any],
    endpoint: str,
) -> Path:
    return (
        output_root
        / campaign_id
        / "runs"
        / pilot_run_id(scheduled, endpoint)
    )


def safe_artifact_path(run_directory: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise CampaignError("run.json contiene una ruta de artefacto inválida")
    candidate = (run_directory / relative).resolve()
    try:
        candidate.relative_to(run_directory.resolve())
    except ValueError as exc:
        raise CampaignError(
            f"artefacto fuera del run directory: {relative}"
        ) from exc
    return candidate


def validate_completed_run(
    output_root: Path,
    manifest: dict[str, Any],
    manifest_sha256: str,
    scheduled: dict[str, Any],
    endpoint: str,
) -> dict[str, Any] | None:
    run_directory = pilot_run_directory(
        output_root, manifest["campaign_id"], scheduled, endpoint
    )
    if not run_directory.exists():
        return None
    result_path = run_directory / "run.json"
    if not result_path.is_file():
        raise CampaignError(
            f"--resume encontró un run incompleto sin run.json: {run_directory}"
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"run.json ilegible: {result_path}") from exc

    expected_run_id = pilot_run_id(scheduled, endpoint)
    checks = (
        result.get("schema") == "fractional-chaos-physical-run-v1",
        result.get("run_id") == expected_run_id,
        result.get("campaign_id") == manifest["campaign_id"],
        result.get("manifest_sha256") == manifest_sha256,
        result.get("endpoint_name") == endpoint,
        result.get("cell", {}).get("cell_id") == scheduled["cell_id"],
        result.get("cell", {}).get("cold_start_id")
        == scheduled["cold_start_id"],
        result.get("transport", {}).get("accepted") is True,
    )
    if not all(checks):
        raise CampaignError(
            f"--resume rechazó run.json inconsistente: {result_path}"
        )
    if endpoint == "benchmark_reset_pilot" and result.get("timing", {}).get(
        "accepted_as_solver_timing"
    ) is not True:
        raise CampaignError(
            f"--resume rechazó timing incompleto: {result_path}"
        )
    if endpoint == "dense_timeseries_pilot":
        dense_checks = (
            result.get("endpoint", {}).get("complete") is True,
            result.get("time_series", {}).get(
                "accepted_for_dense_series_analysis"
            )
            is True,
            result.get("eligible_as_primary_benchmark") is False,
        )
        if not all(dense_checks):
            raise CampaignError(
                f"--resume rechazó captura densa incompleta: {result_path}"
            )

    capture = result.get("capture", {})
    raw_path = safe_artifact_path(run_directory, capture.get("raw_path"))
    if (
        not raw_path.is_file()
        or sha256_file(raw_path) != capture.get("raw_sha256")
    ):
        raise CampaignError(
            f"--resume rechazó hash de captura: {raw_path}"
        )
    if endpoint == "benchmark_reset_pilot":
        timing_path = safe_artifact_path(
            run_directory, capture.get("timing_cycles_path")
        )
        if (
            not timing_path.is_file()
            or sha256_file(timing_path)
            != capture.get("timing_cycles_sha256")
        ):
            raise CampaignError(
                f"--resume rechazó hash de ciclos: {timing_path}"
            )
    if endpoint == "dense_timeseries_pilot":
        csv_path = safe_artifact_path(
            run_directory, capture.get("csv_path")
        )
        if (
            not csv_path.is_file()
            or sha256_file(csv_path) != capture.get("csv_sha256")
        ):
            raise CampaignError(
                f"--resume rechazó hash CSV denso: {csv_path}"
            )
    return result


def execute_pilot(
    manifest: dict[str, Any],
    manifest_sha256: str,
    scheduled: dict[str, Any],
    output_root: Path,
    *,
    endpoint: str,
    break_stale_locks: bool,
) -> dict[str, Any]:
    cell = dict(scheduled)
    benchmark_mode = endpoint == "benchmark_reset_pilot"
    buffered_capture_mode = endpoint == "dense_timeseries_pilot"
    calibration = manifest["calibrated_cells"].get(cell["cell_id"])
    if buffered_capture_mode and cell["cell_id"] not in set(
        manifest["dense_capture_cells"]
    ):
        raise CampaignError(
            f"{cell['cell_id']} no pertenece a dense_capture_cells"
        )
    if not benchmark_mode and not buffered_capture_mode and calibration is None:
        raise CampaignError(
            f"{cell['cell_id']} no tiene decimación físicamente calibrada"
        )
    if benchmark_mode:
        decimation = int(
            manifest["build"]["benchmark_unused_decimation_value"]
        )
    elif buffered_capture_mode:
        decimation = 1
    else:
        decimation = int(calibration["decimation"])
    current_run_id = pilot_run_id(scheduled, endpoint)
    run_directory = pilot_run_directory(
        output_root,
        manifest["campaign_id"],
        scheduled,
        endpoint,
    )
    if run_directory.exists():
        raise CampaignError(
            f"el run_id ya existe y no se sobrescribe: {run_directory}"
        )

    lock_directory = output_root / ".locks"
    board = manifest["boards"][cell["board"]]
    build_dir = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
    )
    source_metadata = git_metadata(
        exclude_generated_paths=(output_root, build_dir),
    )
    with contextlib.ExitStack() as stack:
        for lock_name in (
            "campaign-global",
            f"probe-{board['probe_serial']}",
            f"port-{board['port']}",
            f"build-{build_dir}",
        ):
            stack.enter_context(
                AtomicLock(
                    lock_directory,
                    lock_name,
                    run_id=current_run_id,
                    break_stale=break_stale_locks,
                )
            )

        run_directory.mkdir(parents=True, exist_ok=False)
        started_utc = utc_now()
        try:
            images = ensure_build(
                manifest,
                cell,
                decimation,
                run_directory / "build.log",
                benchmark_mode=benchmark_mode,
                buffered_capture_mode=buffered_capture_mode,
            )
            program_started = time.monotonic()
            run_process(
                flash_command(
                    manifest,
                    cell,
                    decimation,
                    no_reset=True,
                    benchmark_mode=benchmark_mode,
                    buffered_capture_mode=buffered_capture_mode,
                ),
                run_directory / "flash.log",
                float(manifest["timeouts_s"]["flash"]),
            )
            program_wall_time = time.monotonic() - program_started
            raw, frames, parser, durations = capture_after_reset(
                manifest,
                cell,
                decimation,
                run_directory / "reset.log",
                endpoint=endpoint,
            )
            durations["program_wall_time_s"] = program_wall_time
            if benchmark_mode:
                summary, timing_values = summarize_benchmark_capture(
                    manifest, cell, frames, parser
                )
            elif buffered_capture_mode:
                summary = summarize_dense_capture(
                    manifest, cell, frames, parser
                )
                timing_values = []
            else:
                summary = summarize_capture(
                    manifest, cell, decimation, frames, parser
                )
                timing_values = []
            rows = list(decode_uart.rows(iter(frames)))
            raw_path = run_directory / "capture.bin"
            csv_path = run_directory / "capture.csv"
            atomic_write_bytes(raw_path, raw)
            if rows:
                atomic_write_csv(csv_path, rows, tuple(rows[0].keys()))
            else:
                atomic_write_csv(
                    csv_path,
                    [],
                    (
                        "version",
                        "kind",
                        "board",
                        "system",
                        "method",
                        "representation",
                        "sequence",
                        "cycles",
                    ),
                )
            timing_path: Path | None = None
            if timing_values:
                timing_path = run_directory / "timing_cycles.csv"
                atomic_write_csv(
                    timing_path,
                    [
                        {"timed_index": index, "cycles": cycles}
                        for index, cycles in enumerate(timing_values)
                    ],
                    ("timed_index", "cycles"),
                )
            result = {
                "schema": "fractional-chaos-physical-run-v1",
                "run_id": current_run_id,
                "scheduled_campaign_run_id": scheduled["run_id"],
                "scheduled_primary_run_id": (
                    None if buffered_capture_mode else scheduled["run_id"]
                ),
                "campaign_id": manifest["campaign_id"],
                "manifest_sha256": manifest_sha256,
                "started_utc": started_utc,
                "finished_utc": utc_now(),
                "endpoint_name": endpoint,
                "experimental_unit": (
                    "stlink_hardware_reset_repetition"
                    if benchmark_mode
                    else (
                        "dense_buffered_timeseries_acquisition"
                        if buffered_capture_mode
                        else "transport_pilot_acquisition"
                    )
                ),
                "cell": {
                    key: cell[key]
                    for key in (
                        "cell_id",
                        "system",
                        "method",
                        "board",
                        "representation",
                        "cold_start_id",
                        "block_id",
                        "order_index",
                    )
                },
                "system_contract": manifest["systems"][cell["system"]],
                "method_contract": manifest["methods"][cell["method"]],
                "representation_contract": manifest["representations"][
                    cell["representation"]
                ],
                "hardware": {
                    "board_name": board["board_name"],
                    "mcu": board["mcu"],
                    "probe_serial": board["probe_serial"],
                    "port": board["port"],
                    "baud": board["baud"],
                    "reset_mode": manifest["reset_contract"][
                        "implemented_mode"
                    ],
                    "reset_preparation": manifest["reset_contract"][
                        "preparation"
                    ],
                    "power_removed": False,
                },
                "build": {
                    "directory": str(build_dir),
                    "target": firmware_target(manifest, cell),
                    "decimation": decimation,
                    "benchmark_mode": benchmark_mode,
                    "buffered_capture_mode": buffered_capture_mode,
                    "buffered_capture_samples": (
                        manifest["build"]["dense_buffered_capture_samples"]
                        if buffered_capture_mode
                        else None
                    ),
                    "calibration_basis": (
                        "compile_time_kind4_timing_endpoint"
                        if benchmark_mode
                        else (
                            "explicit_dense_capture_contract"
                            if buffered_capture_mode
                            else calibration["basis"]
                        )
                    ),
                    "images": [
                        {
                            "path": str(path),
                            "sha256": sha256_file(path),
                        }
                        for path in images
                    ],
                    "maps": [
                        {
                            "path": str(path),
                            "sha256": sha256_file(path),
                        }
                        for path in map_paths(
                            manifest,
                            cell,
                            decimation,
                            benchmark_mode=benchmark_mode,
                            buffered_capture_mode=buffered_capture_mode,
                        )
                    ],
                },
                "source": source_metadata,
                "capture": {
                    "raw_path": raw_path.name,
                    "raw_sha256": sha256_bytes(raw),
                    "csv_path": csv_path.name,
                    "csv_sha256": sha256_file(csv_path),
                    "timing_cycles_path": (
                        timing_path.name if timing_path is not None else None
                    ),
                    "timing_cycles_sha256": (
                        sha256_file(timing_path)
                        if timing_path is not None
                        else None
                    ),
                    **durations,
                },
                **summary,
            }
            atomic_write_json(run_directory / "run.json", result)
            accepted = (
                result["timing"]["accepted_as_solver_timing"]
                if benchmark_mode
                else result["transport"]["accepted"]
            )
            if not accepted:
                raise CampaignError(
                    f"la captura no cumplió el contrato de {endpoint}; "
                    f"vea {run_directory / 'run.json'}"
                )
            return result
        except Exception as exc:
            atomic_write_json(
                run_directory / "failure.json",
                {
                    "schema": "fractional-chaos-physical-run-failure-v1",
                    "run_id": current_run_id,
                    "started_utc": started_utc,
                    "failed_utc": utc_now(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "eligible_as_evidence": False,
                },
            )
            raise


def select_runs(
    schedule: Sequence[dict[str, Any]],
    *,
    cells: Sequence[str],
    board: str | None,
    cold_start: int | None,
    max_runs: int | None,
) -> list[dict[str, Any]]:
    selected = [
        row
        for row in schedule
        if (not cells or row["cell_id"] in cells)
        and (board is None or row["board"] == board)
        and (
            cold_start is None
            or row["cold_start_id"] == cold_start
        )
    ]
    if cells:
        found = {row["cell_id"] for row in selected}
        missing = set(cells) - found
        if missing:
            raise CampaignError(f"celdas inexistentes en la agenda: {sorted(missing)}")
    if max_runs is not None:
        if max_runs <= 0:
            raise CampaignError("--max-runs debe ser positivo")
        selected = selected[:max_runs]
    return selected


def command_plan(args: argparse.Namespace) -> int:
    manifest, manifest_sha256 = load_manifest(args.manifest)
    schedule = generate_schedule(manifest, manifest_sha256)
    validate_schedule(schedule, manifest)
    plan_path, schedule_path = write_plan(
        args.output_root, manifest, manifest_sha256, schedule
    )
    summary = plan_payload(manifest, manifest_sha256, schedule)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"plan: {plan_path}")
    print(f"schedule: {schedule_path}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    manifest, manifest_sha256 = load_manifest(args.manifest)
    schedule = generate_schedule(manifest, manifest_sha256)
    validate_schedule(schedule, manifest)
    selectable_schedule = schedule
    if args.endpoint == "dense_timeseries_pilot":
        if "dense_timeseries_pilot" not in manifest.get("endpoints", {}):
            raise CampaignError(
                "el manifiesto no declara dense_timeseries_pilot"
            )
        dense_cells = set(manifest["dense_capture_cells"])
        selectable_schedule = [
            row for row in schedule if row["cell_id"] in dense_cells
        ]
    selected = select_runs(
        selectable_schedule,
        cells=args.cell,
        board=args.board,
        cold_start=args.cold_start,
        max_runs=(
            None if args.execute and args.resume else args.max_runs
        ),
    )
    if not selected:
        raise CampaignError("ninguna ejecución coincide con los filtros")

    endpoint_contract = manifest["endpoints"][args.endpoint]
    dry_summary = {
        "campaign_id": manifest["campaign_id"],
        "endpoint": args.endpoint,
        "evidence_level": endpoint_contract["evidence_level"],
        "eligible_as_primary_benchmark": False,
        "execute": bool(args.execute),
        "resume": bool(args.resume),
        "selected_runs": len(selected),
        "selected_cells": sorted({row["cell_id"] for row in selected}),
        "transport_calibrated_selected_runs": sum(
            row["calibration_status"] == "accepted_pilot"
            for row in selected
        ),
        "dense_contract_selected_runs": (
            len(selected) if args.endpoint == "dense_timeseries_pilot" else 0
        ),
        "buffered_capture_mode": (
            args.endpoint == "dense_timeseries_pilot"
        ),
        "primary_benchmark_ready": False,
        "first_run_id": (
            pilot_run_id(selected[0], args.endpoint)
            if args.endpoint != "primary_benchmark"
            else selected[0]["run_id"]
        ),
        "last_run_id": (
            pilot_run_id(selected[-1], args.endpoint)
            if args.endpoint != "primary_benchmark"
            else selected[-1]["run_id"]
        ),
    }
    if not args.execute:
        print(json.dumps(dry_summary, ensure_ascii=False, indent=2))
        return 0

    if args.confirm_campaign_id != manifest["campaign_id"]:
        raise CampaignError(
            "--execute exige --confirm-campaign-id "
            f"{manifest['campaign_id']}"
        )
    if args.endpoint == "primary_benchmark":
        reasons = "; ".join(
            manifest["endpoints"]["primary_benchmark"]["blocking_reasons"]
        )
        raise CampaignError(f"primary_benchmark bloqueado: {reasons}")
    if not args.allow_pilot_only:
        raise CampaignError(
            f"{args.endpoint} exige --allow-pilot-only para reconocer "
            "que no es evidencia primaria"
        )
    maximum_key = {
        "benchmark_reset_pilot": (
            "maximum_benchmark_reset_pilot_runs_per_invocation"
        ),
        "transport_pilot": "maximum_transport_pilot_runs_per_invocation",
        "dense_timeseries_pilot": (
            "maximum_dense_timeseries_pilot_runs_per_invocation"
        ),
    }[args.endpoint]
    maximum = manifest["safety"][maximum_key]
    if args.max_runs is None or args.max_runs > maximum:
        raise CampaignError(
            f"un piloto físico exige --max-runs y admite como máximo {maximum}"
        )
    resumed: list[dict[str, Any]] = []
    if args.resume:
        pending: list[dict[str, Any]] = []
        for scheduled in selected:
            completed = validate_completed_run(
                args.output_root,
                manifest,
                manifest_sha256,
                scheduled,
                args.endpoint,
            )
            if completed is None:
                pending.append(scheduled)
            else:
                resumed.append(
                    {
                        "run_id": completed["run_id"],
                        "status": "skipped_valid_completed_run",
                        "transport_accepted": True,
                        "timing_accepted": completed["timing"].get(
                            "accepted_as_solver_timing", False
                        ),
                        "eligible_as_primary_benchmark": False,
                    }
                )
        selected = pending[: args.max_runs]
        if not selected:
            print(
                json.dumps(
                    {"completed": resumed},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    if args.endpoint == "transport_pilot":
        uncalibrated = [
            row["cell_id"]
            for row in selected
            if row["calibration_status"] != "accepted_pilot"
        ]
        if uncalibrated:
            raise CampaignError(
                "no se ejecutan celdas sin calibración de transporte: "
                f"{sorted(set(uncalibrated))}"
            )

    results = list(resumed)
    for scheduled in selected:
        result = execute_pilot(
            manifest,
            manifest_sha256,
            scheduled,
            args.output_root,
            endpoint=args.endpoint,
            break_stale_locks=args.break_stale_locks,
        )
        results.append(
            {
                "run_id": result["run_id"],
                "transport_accepted": result["transport"]["accepted"],
                "timing_accepted": result["timing"].get(
                    "accepted_as_solver_timing", False
                ),
                "eligible_as_primary_benchmark": False,
            }
        )
    print(json.dumps({"completed": results}, ensure_ascii=False, indent=2))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Agenda reproducible 3 sistemas x 3 métodos x 2 aritméticas "
            "x 2 placas, con 30 cold starts por celda."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"manifiesto (predeterminado: {DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"salidas (predeterminado: {DEFAULT_OUTPUT_ROOT})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser(
        "plan",
        help="genera y valida la agenda de 1080 ejecuciones sin tocar hardware",
    )
    plan.set_defaults(handler=command_plan)

    run = subparsers.add_parser(
        "run",
        help="dry-run por defecto; --execute sólo habilita pilotos acotados",
    )
    run.add_argument(
        "--endpoint",
        choices=(
            "primary_benchmark",
            "benchmark_reset_pilot",
            "transport_pilot",
            "dense_timeseries_pilot",
        ),
        default="primary_benchmark",
    )
    run.add_argument("--cell", action="append", default=[])
    run.add_argument("--board", choices=("f746", "h755"))
    repetition = run.add_mutually_exclusive_group()
    repetition.add_argument(
        "--reset-repetition",
        dest="cold_start",
        type=int,
        help=(
            "selecciona el identificador de repetición; el piloto se etiqueta "
            "como reset hardware, no como power-cycle cold start"
        ),
    )
    repetition.add_argument(
        "--cold-start",
        dest="cold_start",
        type=int,
        help="alias histórico del campo de agenda del paper",
    )
    run.add_argument("--max-runs", type=int)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-campaign-id")
    run.add_argument("--allow-pilot-only", action="store_true")
    run.add_argument(
        "--resume",
        action="store_true",
        help=(
            "omite run_id ya aceptados sólo tras validar run.json y hashes; "
            "un directorio parcial o inconsistente detiene la ejecución"
        ),
    )
    run.add_argument("--break-stale-locks", action="store_true")
    run.set_defaults(handler=command_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()
    try:
        return int(args.handler(args))
    except CampaignError as exc:
        parser.exit(2, f"error de campaña: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
