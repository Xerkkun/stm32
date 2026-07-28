#!/usr/bin/env python3
"""Collect link-time Flash/RAM evidence from the 36 Release images.

The inventory uses ``arm-none-eabi-size -B`` and never infers stack or dynamic
heap use.  For H755 cells it reports the CM7 solver image, the common CM4 UART
image, and their sum, while preserving both component hashes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "validation" / "results" / "resource_usage"
SYSTEMS = ("lorenz", "rossler", "chen")
METHODS = ("efork3", "gl", "m2sfrk")
REPRESENTATIONS = ("float32", "fixed_q14_q30")
SIZE_ROW = re.compile(
    r"^\s*(?P<text>\d+)\s+"
    r"(?P<data>\d+)\s+"
    r"(?P<bss>\d+)\s+"
    r"(?P<dec>\d+)\s+"
    r"(?P<hex>[0-9a-fA-F]+)\s+"
    r"(?P<filename>.+?)\s*$"
)


class ResourceError(RuntimeError):
    """Raised when the resource inventory cannot be made reproducibly."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_size_output(output: str) -> dict[str, int]:
    """Parse one Berkeley-format GNU size row."""

    matches = [
        match
        for line in output.splitlines()
        if (match := SIZE_ROW.match(line)) is not None
    ]
    if len(matches) != 1:
        raise ResourceError(
            f"se esperaba una fila de arm-none-eabi-size, se obtuvieron "
            f"{len(matches)}"
        )
    match = matches[0]
    result = {
        key: int(match.group(key), 10)
        for key in ("text", "data", "bss", "dec")
    }
    if result["dec"] != result["text"] + result["data"] + result["bss"]:
        raise ResourceError("la columna dec no coincide con text+data+bss")
    result["flash_bytes"] = result["text"] + result["data"]
    result["static_ram_bytes"] = result["data"] + result["bss"]
    return result


def discover_size(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.resolve()
        if not candidate.is_file():
            raise ResourceError(f"no existe la herramienta size: {candidate}")
        return candidate

    on_path = shutil.which("arm-none-eabi-size")
    if on_path:
        return Path(on_path).resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        bundle_root = (
            Path(local_app_data)
            / "STM32Cube"
            / "bundles"
            / "gnu-tools-for-stm32"
        )
        candidates = sorted(
            bundle_root.glob("*/bin/arm-none-eabi-size.exe"),
            key=lambda path: path.parent.parent.name,
            reverse=True,
        )
        if candidates:
            return candidates[0].resolve()

    raise ResourceError(
        "arm-none-eabi-size no está en PATH ni en el bundle local STM32Cube"
    )


def tool_version(size_tool: Path) -> str:
    completed = subprocess.run(
        [str(size_tool), "--version"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ResourceError(
            f"falló {size_tool.name} --version: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def measure_image(size_tool: Path, elf: Path) -> dict[str, Any]:
    elf = elf.resolve()
    if not elf.is_file():
        raise ResourceError(f"falta la imagen Release: {elf}")
    completed = subprocess.run(
        [str(size_tool), "-B", str(elf)],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ResourceError(
            f"falló size para {elf}: {completed.stderr.strip()}"
        )
    map_path = elf.with_suffix(".map")
    if not map_path.is_file():
        raise ResourceError(f"falta el mapa enlazado: {map_path}")
    relative_elf = elf.relative_to(ROOT)
    relative_map = map_path.resolve().relative_to(ROOT)
    return {
        "elf": relative_elf.as_posix(),
        "elf_sha256": sha256_file(elf),
        "map": relative_map.as_posix(),
        "map_sha256": sha256_file(map_path),
        **parse_size_output(completed.stdout),
    }


def target_name(
    board: str,
    system: str,
    method: str,
    representation: str,
) -> str:
    prefix = "f746" if board == "f746" else "h755_m7"
    suffix = "_fixed" if representation == "fixed_q14_q30" else ""
    return f"{prefix}_{system}_{method}{suffix}"


def expected_cells() -> Iterable[dict[str, str]]:
    for system in SYSTEMS:
        for method in METHODS:
            for board in ("f746", "h755"):
                for representation in REPRESENTATIONS:
                    yield {
                        "system": system,
                        "method": method,
                        "board": board,
                        "representation": representation,
                    }


def collect(size_tool: Path) -> dict[str, Any]:
    f746_root = ROOT / "build" / "f746-release"
    h755_root = ROOT / "build" / "h755-release"
    cm4 = measure_image(size_tool, h755_root / "h755_m4_uart.elf")
    cells: list[dict[str, Any]] = []

    for identity in expected_cells():
        target = target_name(**identity)
        build_root = f746_root if identity["board"] == "f746" else h755_root
        primary = measure_image(size_tool, build_root / f"{target}.elf")
        companion = cm4 if identity["board"] == "h755" else None
        total_flash = primary["flash_bytes"]
        total_ram = primary["static_ram_bytes"]
        if companion is not None:
            total_flash += companion["flash_bytes"]
            total_ram += companion["static_ram_bytes"]
        cells.append(
            {
                "cell_id": "_".join(
                    (
                        identity["system"],
                        identity["method"],
                        identity["board"],
                        identity["representation"],
                    )
                ),
                **identity,
                "target": target,
                "solver_image": primary,
                "common_cm4_uart_image": companion,
                "board_total_flash_bytes": total_flash,
                "board_total_static_ram_bytes": total_ram,
            }
        )

    if len(cells) != 36:
        raise ResourceError(f"se midieron {len(cells)} celdas, no 36")
    return {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "scope": (
            "Link-time resource inventory of the 36 non-benchmark Release "
            "images in the primary matrix"
        ),
        "evidence_level": "static_linker_inventory",
        "tool": {
            "path": str(size_tool),
            "sha256": sha256_file(size_tool),
            "version_output": tool_version(size_tool),
            "command": "arm-none-eabi-size -B <image.elf>",
        },
        "accounting": {
            "flash_bytes": "text + data",
            "static_ram_bytes": "data + bss",
            "h755_board_total": (
                "CM7 solver image plus the common CM4 UART companion image"
            ),
            "benchmark_buffers_included": False,
        },
        "does_not_measure": [
            "dynamic stack high-water mark",
            "heap high-water mark",
            "runtime energy",
            "execution time",
            "physical-board memory faults",
        ],
        "common_h755_cm4_uart_image": cm4,
        "cells": cells,
    }


def write_outputs(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "resource_usage.json"
    csv_path = output_dir / "resource_usage.csv"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rows = []
    for cell in report["cells"]:
        solver = cell["solver_image"]
        cm4 = cell["common_cm4_uart_image"]
        rows.append(
            {
                "cell_id": cell["cell_id"],
                "system": cell["system"],
                "method": cell["method"],
                "board": cell["board"],
                "representation": cell["representation"],
                "solver_text_bytes": solver["text"],
                "solver_data_bytes": solver["data"],
                "solver_bss_bytes": solver["bss"],
                "solver_flash_bytes": solver["flash_bytes"],
                "solver_static_ram_bytes": solver["static_ram_bytes"],
                "cm4_flash_bytes": cm4["flash_bytes"] if cm4 else 0,
                "cm4_static_ram_bytes": cm4["static_ram_bytes"] if cm4 else 0,
                "board_total_flash_bytes": cell["board_total_flash_bytes"],
                "board_total_static_ram_bytes": (
                    cell["board_total_static_ram_bytes"]
                ),
                "solver_elf_sha256": solver["elf_sha256"],
                "solver_map_sha256": solver["map_sha256"],
            }
        )
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventario reproducible Flash/RAM de 36 imágenes Release"
    )
    parser.add_argument("--size-tool", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    size_tool = discover_size(args.size_tool)
    report = collect(size_tool)
    write_outputs(report, args.output_dir.resolve())
    print(args.output_dir.resolve() / "resource_usage.json")
    print(
        json.dumps(
            {
                "cells": len(report["cells"]),
                "cm4_companion_sha256": report[
                    "common_h755_cm4_uart_image"
                ]["elf_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
