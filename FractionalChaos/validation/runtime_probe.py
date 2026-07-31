#!/usr/bin/env python3
"""Locate and validate FRP1 runtime-resource records.

The firmware writes these records only after the benchmark transport has
drained. Decoding a synthetic file or locating an ELF symbol is preparation,
not physical evidence. A reportable artifact still needs the raw target-memory
upload, its hash, the ELF hash, probe identity, and clean source provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Any, Sequence


MAGIC = 0x31505246
VERSION = 1
SENTINEL = 0xA5A5A5A5
STATUS_ARMED = 0x01
STATUS_COMPLETE = 0x02
STATUS_OVERFLOW = 0x04
STATUS_INVALID = 0x08
RECORD = struct.Struct("<21I")
SCHEMA = "fractional-chaos-runtime-probe-v1"

FIELD_NAMES = (
    "magic",
    "version",
    "core_id",
    "status",
    "region_start",
    "region_end",
    "paint_end",
    "region_bytes",
    "painted_bytes",
    "reserved_top_bytes",
    "untouched_bytes",
    "stack_high_water_bytes",
    "expected_core_clock_hz",
    "software_core_clock_hz",
    "solver_bytes",
    "capture_bytes",
    "dma_bytes",
    "shared_bytes",
    "heap_configured_bytes",
    "sentinel_word",
    "checksum",
)

CORE_NAMES = {
    1: "f746_m7",
    2: "h755_m7",
    3: "h755_m4",
}
SYMBOLS = {
    "f746_m7": "g_fc_f746_runtime_probe",
    "h755_m7": "g_fc_h755_m7_runtime_probe",
    "h755_m4": "g_fc_h755_m4_runtime_probe",
}


class RuntimeProbeError(RuntimeError):
    """Raised when an FRP1 artifact violates its contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeProbeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fnv1a32(payload: bytes) -> int:
    value = 2166136261
    for byte in payload:
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def parse_record_bytes(payload: bytes) -> dict[str, int]:
    require(
        len(payload) == RECORD.size,
        f"FRP1 requiere exactamente {RECORD.size} bytes",
    )
    values = dict(zip(FIELD_NAMES, RECORD.unpack(payload)))
    require(values["magic"] == MAGIC, "magic FRP1 inválido")
    require(values["version"] == VERSION, "versión FRP1 inválida")
    require(values["core_id"] in CORE_NAMES, "core_id FRP1 desconocido")
    require(
        values["status"] & STATUS_COMPLETE,
        "el runtime probe todavía no está completo",
    )
    require(
        not values["status"] & STATUS_ARMED,
        "el runtime probe quedó únicamente armado",
    )
    require(
        not values["status"] & STATUS_INVALID,
        "el runtime probe marcó layout inválido",
    )
    require(
        not values["status"] & STATUS_OVERFLOW,
        "el stack agotó la región de watermark",
    )
    require(values["sentinel_word"] == SENTINEL, "sentinela FRP1 inválido")
    require(
        values["checksum"] == fnv1a32(payload[:-4]),
        "checksum FRP1 inválido",
    )
    require(
        values["region_start"] < values["paint_end"]
        <= values["region_end"],
        "direcciones FRP1 incoherentes",
    )
    require(
        values["region_bytes"]
        == values["region_end"] - values["region_start"],
        "region_bytes FRP1 incoherente",
    )
    require(
        values["painted_bytes"] + values["reserved_top_bytes"]
        == values["region_bytes"],
        "partición de región FRP1 incoherente",
    )
    require(
        values["untouched_bytes"] <= values["painted_bytes"],
        "untouched_bytes FRP1 fuera de rango",
    )
    require(
        values["stack_high_water_bytes"]
        == values["reserved_top_bytes"]
        + values["painted_bytes"]
        - values["untouched_bytes"],
        "stack_high_water_bytes FRP1 incoherente",
    )
    require(
        values["software_core_clock_hz"]
        == values["expected_core_clock_hz"],
        "SystemCoreClock no coincide con el reloj esperado",
    )
    require(
        values["heap_configured_bytes"] == 0,
        "el build reportable no debe configurar heap dinámico",
    )
    return values


def build_report(
    raw_path: Path,
    *,
    elf_path: Path | None = None,
    probe_serial: str | None = None,
) -> dict[str, Any]:
    payload = raw_path.read_bytes()
    values = parse_record_bytes(payload)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "evidence_level": "decoded_target_memory_runtime_probe",
        "core": CORE_NAMES[values["core_id"]],
        "record": values,
        "quality": {
            "complete": True,
            "stack_probe_overflow": False,
            "software_clock_matches_expected": True,
            "dynamic_heap_configured": False,
            "stack_headroom_bytes": (
                values["region_bytes"]
                - values["stack_high_water_bytes"]
            ),
        },
        "raw": {
            "path": raw_path.as_posix(),
            "bytes": len(payload),
            "sha256": sha256_file(raw_path),
        },
        "provenance": {
            "probe_serial": probe_serial,
            "elf_path": elf_path.as_posix() if elf_path else None,
            "elf_sha256": sha256_file(elf_path) if elf_path else None,
            "clean_source_required_for_reportable_use": True,
            "clean_source_verified_here": False,
        },
        "eligible_as_primary_evidence": False,
        "limitations": [
            "The sentinel begins after early startup and therefore measures post-arm stack use.",
            "The guard makes the reported high-water mark conservative.",
            "SystemCoreClock agreement is a software consistency check; the external PA0/D32 pulse supplies the independent clock check.",
        ],
    }
    return report


def locate_symbol(nm_output: str, symbol: str) -> dict[str, int]:
    pattern = re.compile(
        rf"^([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+\w\s+{re.escape(symbol)}$"
    )
    matches = []
    for line in nm_output.splitlines():
        match = pattern.match(line.strip())
        if match:
            matches.append((int(match.group(1), 16), int(match.group(2), 16)))
    require(len(matches) == 1, f"se esperaba un símbolo único {symbol}")
    address, size = matches[0]
    require(size == RECORD.size, f"{symbol} debe medir {RECORD.size} bytes")
    return {"address": address, "size": size}


def run_nm(nm: Path, elf: Path, symbol: str) -> dict[str, int]:
    completed = subprocess.run(
        [str(nm), "-S", "--defined-only", str(elf)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    require(completed.returncode == 0, f"nm falló: {completed.stderr.strip()}")
    return locate_symbol(completed.stdout, symbol)


def physical_debug_address(core: str, linked_address: int) -> int:
    if core == "h755_m4":
        require(
            0x10000000 <= linked_address < 0x10048000,
            "símbolo CM4 fuera del alias esperado de SRAM D2",
        )
        return linked_address + 0x20000000
    return linked_address


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida registros FRP1")
    subparsers = parser.add_subparsers(dest="command", required=True)

    decode = subparsers.add_parser("decode")
    decode.add_argument("--input", type=Path, required=True)
    decode.add_argument("--output", type=Path, required=True)
    decode.add_argument("--elf", type=Path)
    decode.add_argument("--probe-serial")

    locate = subparsers.add_parser("locate")
    locate.add_argument("--elf", type=Path, required=True)
    locate.add_argument("--nm", type=Path, required=True)
    locate.add_argument("--core", choices=tuple(SYMBOLS), required=True)
    locate.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "decode":
        report = build_report(
            args.input.resolve(),
            elf_path=args.elf.resolve() if args.elf else None,
            probe_serial=args.probe_serial,
        )
    else:
        location = run_nm(
            args.nm.resolve(),
            args.elf.resolve(),
            SYMBOLS[args.core],
        )
        report = {
            "schema": SCHEMA,
            "status": "prepared_symbol_location_not_measurement",
            "core": args.core,
            "symbol": SYMBOLS[args.core],
            "linked_address": location["address"],
            "physical_debug_address": physical_debug_address(
                args.core,
                location["address"],
            ),
            "size": location["size"],
            "elf": {
                "path": args.elf.resolve().as_posix(),
                "sha256": sha256_file(args.elf.resolve()),
            },
            "cubeprogrammer_upload_arguments": [
                "-c",
                "port=SWD",
                "sn=<PROBE_SERIAL>",
                "mode=HOTPLUG",
                "-u",
                f"0x{physical_debug_address(args.core, location['address']):08X}",
                str(location["size"]),
                "<OUTPUT.bin>",
            ],
            "template_is_physical_evidence": False,
        }
    write_json(args.output.resolve(), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
