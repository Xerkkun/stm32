#!/usr/bin/env python3
"""Acquire long physical M2sFRK streams for the three selected systems.

Each run programs one STM32 cell, applies a hardware reset, receives 44,000
valid FCC1 state frames at a decimation of 512 model steps, verifies transport
integrity, and extracts a post-transient bitstream with the existing
``analyze_capture.py`` implementation.  The resulting 12 streams cover three
systems, two boards, and two numerical representations without concatenating
or recycling finite inputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import decode_uart  # noqa: E402
import run_physical_campaign as campaign  # noqa: E402


MANIFEST_PATH = ROOT / "validation" / "physical_campaign_selected_v1.json"
OUTPUT_ROOT = (
    ROOT / "validation" / "results" / "selected_statistical_streams_v1"
)
CAPTURE_ROOT = OUTPUT_ROOT / "captures"
ANALYSIS_ROOT = OUTPUT_ROOT / "analysis"
BATTERY_MANIFEST_PATH = OUTPUT_ROOT / "statistical_battery_manifest.json"
SOURCE_BATTERY_MANIFEST = (
    ROOT / "validation" / "statistical_battery_manifest.json"
)
DECIMATION_BY_BOARD = {"f746": 512, "h755": 1024}
TARGET_FRAMES = 44_000
DISCARD_FRAMES = 64
EXPECTED_BITS = (TARGET_FRAMES - DISCARD_FRAMES) * 24
MINIMUM_STATISTICAL_BITS = 1_000_000
CAPTURE_TIMEOUT_S = 300.0
FIRST_FRAME_TIMEOUT_S = 45.0
SYSTEMS = ("chen", "liu", "hammouch_mekkaoui")
BOARDS = ("f746", "h755")
REPRESENTATIONS = ("float32", "fixed_q14_q30")


class StatisticalAcquisitionError(RuntimeError):
    """Raised when a long physical acquisition violates its protocol."""


def display_path(path: Path) -> str:
    """Return a repository-relative path when possible."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def expected_cell_ids() -> tuple[str, ...]:
    """Return the exact 12-cell M2sFRK statistical matrix."""

    return tuple(
        f"{system}_m2sfrk_{board}_"
        f"{'fixed' if representation == 'fixed_q14_q30' else representation}"
        for system in SYSTEMS
        for board in BOARDS
        for representation in REPRESENTATIONS
    )


def cell_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Index the physical campaign matrix by cell identifier."""

    return {
        str(cell["cell_id"]): cell
        for cell in campaign.matrix_cells(manifest)
        if cell["cell_id"] in expected_cell_ids()
    }


def validate_completed_run(run_path: Path) -> dict[str, Any]:
    """Revalidate one completed statistical acquisition and all hashes."""

    payload = json.loads(run_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "fractional-chaos-selected-statistical-stream-v1":
        raise StatisticalAcquisitionError(f"{run_path}: invalid schema")
    if payload.get("status") != "complete":
        raise StatisticalAcquisitionError(f"{run_path}: run is not complete")
    expected_decimation = DECIMATION_BY_BOARD[str(payload.get("board"))]
    if payload.get("decimation") != expected_decimation:
        raise StatisticalAcquisitionError(f"{run_path}: decimation mismatch")
    if payload.get("matching_frames") != TARGET_FRAMES:
        raise StatisticalAcquisitionError(f"{run_path}: frame-count mismatch")
    if payload.get("post_discard_bits") != EXPECTED_BITS:
        raise StatisticalAcquisitionError(f"{run_path}: bit-count mismatch")

    for entry_name in ("capture_bin", "capture_csv", "analysis", "bitstream"):
        entry = payload.get(entry_name, {})
        path = ROOT / str(entry.get("path"))
        if not path.is_file():
            raise StatisticalAcquisitionError(
                f"{run_path}: missing {entry_name} artifact {path}"
            )
        observed = campaign.sha256_file(path)
        if observed != entry.get("sha256"):
            raise StatisticalAcquisitionError(
                f"{run_path}: {entry_name} SHA-256 mismatch"
            )
    return payload


def capture_frames(
    manifest: dict[str, Any],
    cell: dict[str, str],
    reset_log: Path,
    decimation: int,
) -> tuple[list[tuple[int, ...]], campaign.FCC1StreamParser, dict[str, float]]:
    """Reset one programmed board and capture exactly ``TARGET_FRAMES`` states."""

    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise StatisticalAcquisitionError("pyserial is required") from exc

    board = manifest["boards"][cell["board"]]
    identity = campaign.expected_identity(manifest, cell)
    parser = campaign.FCC1StreamParser()
    matching: list[tuple[int, ...]] = []
    capture_started = time.monotonic()
    process_finished: float | None = None
    first_matching_at: float | None = None

    with serial.Serial(
        board["port"],
        board["baud"],
        timeout=float(manifest["timeouts_s"]["serial_read"]),
    ) as port, reset_log.open("wb") as log:
        try:
            port.set_buffer_size(rx_size=4 * 1024 * 1024)
        except (AttributeError, OSError):
            pass
        port.reset_input_buffer()
        process = subprocess.Popen(
            campaign.flash_command(
                manifest,
                cell,
                decimation,
                reset_only=True,
            ),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            while len(matching) < TARGET_FRAMES:
                now = time.monotonic()
                return_code = process.poll()
                if return_code is not None and process_finished is None:
                    process_finished = now
                    if return_code != 0:
                        raise StatisticalAcquisitionError(
                            f"hardware reset returned {return_code}; see {reset_log}"
                        )
                if process_finished is None and now - capture_started > float(
                    manifest["timeouts_s"]["flash"]
                ):
                    process.kill()
                    process.wait(timeout=5)
                    raise StatisticalAcquisitionError("hardware reset timed out")

                waiting = getattr(port, "in_waiting", 0)
                chunk = port.read(min(max(waiting, 1), 16_384))
                if chunk:
                    for frame in parser.feed(chunk):
                        if campaign.frame_matches_identity(frame, identity):
                            if first_matching_at is None:
                                first_matching_at = time.monotonic()
                            matching.append(frame)
                            if len(matching) >= TARGET_FRAMES:
                                break

                now = time.monotonic()
                if (
                    process_finished is not None
                    and first_matching_at is None
                    and now - process_finished > FIRST_FRAME_TIMEOUT_S
                ):
                    raise StatisticalAcquisitionError(
                        "no matching FCC1 frame arrived after reset"
                    )
                if now - capture_started > CAPTURE_TIMEOUT_S:
                    raise StatisticalAcquisitionError(
                        f"capture did not reach {TARGET_FRAMES} frames in "
                        f"{CAPTURE_TIMEOUT_S:.0f} s"
                    )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    durations = {
        "capture_wall_time_s": time.monotonic() - capture_started,
        "first_matching_frame_after_start_s": (
            first_matching_at - capture_started
            if first_matching_at is not None
            else float("nan")
        ),
    }
    return matching[:TARGET_FRAMES], parser, durations


def validate_frames(
    frames: list[tuple[int, ...]],
    parser: campaign.FCC1StreamParser,
    cell_id: str,
    decimation: int,
) -> list[dict[str, Any]]:
    """Verify solver, counter, and sequence integrity for one physical stream."""

    if len(frames) != TARGET_FRAMES:
        raise StatisticalAcquisitionError(
            f"{cell_id}: received {len(frames)} matching frames"
        )
    rows = list(decode_uart.rows(iter(frames)))
    sequences = [int(row["sequence"]) for row in rows]
    if any(int(row["status"]) != 0 for row in rows):
        raise StatisticalAcquisitionError(f"{cell_id}: nonzero solver status")
    if any(int(row["dropped"]) != 0 for row in rows):
        raise StatisticalAcquisitionError(f"{cell_id}: dropped-frame counter")
    if any(
        right - left != decimation
        for left, right in zip(sequences, sequences[1:])
    ):
        raise StatisticalAcquisitionError(
            f"{cell_id}: sequence increments are not exactly {decimation}"
        )
    counters = parser.counters
    # Acquisition stops as soon as the requested complete frame is decoded.
    # Serial.read() may already have returned a prefix of the following frame;
    # that suffix is expected at the capture boundary and is never serialized.
    trailing_bytes = parser.trailing_bytes
    invalid_trailing = trailing_bytes < 0 or trailing_bytes >= decode_uart.FRAME.size
    if counters.crc_errors or counters.invalid_headers or invalid_trailing:
        raise StatisticalAcquisitionError(
            f"{cell_id}: parser integrity failure "
            f"(crc={counters.crc_errors}, headers={counters.invalid_headers}, "
            f"trailing={trailing_bytes}, frame_size={decode_uart.FRAME.size})"
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write decoded FCC1 rows."""

    if not rows:
        raise StatisticalAcquisitionError("cannot write an empty capture")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_analysis(
    capture_csv: Path,
    analysis_dir: Path,
    cell: dict[str, str],
    sample_interval: float,
    decimation: int,
) -> tuple[Path, Path, dict[str, Any]]:
    """Use the existing extraction code to create one post-transient stream."""

    analysis_dir.mkdir(parents=True, exist_ok=False)
    extraction_mode = (
        "fixed-raw"
        if cell["representation"] == "fixed_q14_q30"
        else "fixed-point"
    )
    command = [
        sys.executable,
        str(ROOT / "validation" / "analyze_capture.py"),
        str(capture_csv),
        "--output-dir",
        str(analysis_dir),
        "--mode",
        extraction_mode,
        "--lsb-bits",
        "8",
        "--discard",
        str(DISCARD_FRAMES),
        "--time-series-samples",
        "1024",
        "--sample-interval",
        f"{sample_interval:.17g}",
        "--expected-decimation",
        str(decimation),
        "--minimum-statistical-bits",
        str(MINIMUM_STATISTICAL_BITS),
    ]
    if extraction_mode == "fixed-point":
        command.extend(("--fractional-bits", "14"))
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    analysis_paths = sorted(analysis_dir.glob("*_analysis.json"))
    bitstream_paths = sorted(analysis_dir.glob("*_lsb.bin"))
    if len(analysis_paths) != 1 or len(bitstream_paths) != 1:
        raise StatisticalAcquisitionError(
            f"{analysis_dir}: expected one analysis JSON and one bitstream"
        )
    analysis = json.loads(analysis_paths[0].read_text(encoding="utf-8"))
    eligibility = analysis.get("statistical_eligibility", {})
    if eligibility.get("eligible_for_configured_screening") is not True:
        raise StatisticalAcquisitionError(
            f"{analysis_paths[0]}: bitstream did not meet the length contract"
        )
    if int(analysis["outputs"]["bit_count"]) != EXPECTED_BITS:
        raise StatisticalAcquisitionError(
            f"{analysis_paths[0]}: expected {EXPECTED_BITS} bits"
        )
    if (
        campaign.sha256_file(bitstream_paths[0])
        != analysis["outputs"]["bitstream_sha256"]
    ):
        raise StatisticalAcquisitionError(
            f"{analysis_paths[0]}: bitstream SHA-256 mismatch"
        )
    analysis["analysis_command"] = command
    analysis["analysis_stdout"] = completed.stdout.strip()
    analysis["analysis_stderr"] = completed.stderr.strip()
    return analysis_paths[0], bitstream_paths[0], analysis


def acquire_one(
    manifest: dict[str, Any],
    manifest_sha256: str,
    cell: dict[str, str],
) -> dict[str, Any]:
    """Acquire, validate, analyze, and atomically publish one long stream."""

    cell_id = str(cell["cell_id"])
    final_dir = CAPTURE_ROOT / cell_id
    final_run = final_dir / "run.json"
    if final_run.is_file():
        return validate_completed_run(final_run)
    if final_dir.exists():
        raise StatisticalAcquisitionError(
            f"partial final directory requires inspection: {final_dir}"
        )

    existing_partials = sorted(CAPTURE_ROOT.glob(f".partial-{cell_id}-*"))
    if len(existing_partials) > 1:
        raise StatisticalAcquisitionError(
            f"multiple partial work directories require inspection: "
            f"{existing_partials}"
        )
    recovering_capture = len(existing_partials) == 1
    partial_dir = (
        existing_partials[0]
        if recovering_capture
        else CAPTURE_ROOT / f".partial-{cell_id}-{os.getpid()}"
    )
    if not recovering_capture:
        partial_dir.mkdir(parents=True)
    analysis_dir = ANALYSIS_ROOT / cell_id
    if analysis_dir.exists():
        if recovering_capture and not any(analysis_dir.iterdir()):
            analysis_dir.rmdir()
        else:
            raise StatisticalAcquisitionError(
                "analysis directory already exists without a completed run: "
                f"{analysis_dir}"
            )

    started_utc = campaign.utc_now()
    try:
        decimation = DECIMATION_BY_BOARD[cell["board"]]
        build_dir = campaign.build_directory(manifest, cell, decimation)
        if recovering_capture:
            capture_bin = partial_dir / "capture.bin"
            capture_csv = partial_dir / "capture.csv"
            if not capture_bin.is_file() or not capture_csv.is_file():
                raise StatisticalAcquisitionError(
                    f"{partial_dir}: partial capture artifacts are incomplete"
                )
            parser = campaign.FCC1StreamParser()
            parsed_frames = parser.feed(capture_bin.read_bytes())
            identity = campaign.expected_identity(manifest, cell)
            frames = [
                frame
                for frame in parsed_frames
                if campaign.frame_matches_identity(frame, identity)
            ]
            rows = validate_frames(frames, parser, cell_id, decimation)
            images = campaign.image_paths(manifest, cell, decimation)
            missing_images = [path for path in images if not path.is_file()]
            if missing_images:
                raise StatisticalAcquisitionError(
                    f"{partial_dir}: missing build images {missing_images}"
                )
            durations = {
                "capture_wall_time_s": None,
                "first_matching_frame_after_start_s": None,
                "recovered_from_integrity_checked_partial": True,
            }
        else:
            images = campaign.ensure_build(
                manifest,
                cell,
                decimation,
                partial_dir / "build.log",
            )
            campaign.run_process(
                campaign.flash_command(
                    manifest,
                    cell,
                    decimation,
                    no_reset=True,
                ),
                partial_dir / "flash.log",
                float(manifest["timeouts_s"]["flash"]),
            )
            frames, parser, durations = capture_frames(
                manifest,
                cell,
                partial_dir / "reset.log",
                decimation,
            )
            rows = validate_frames(frames, parser, cell_id, decimation)
            raw_bytes = b"".join(
                decode_uart.FRAME.pack(*frame) for frame in frames
            )
            capture_bin = partial_dir / "capture.bin"
            capture_csv = partial_dir / "capture.csv"
            capture_bin.write_bytes(raw_bytes)
            write_csv(capture_csv, rows)

        contract = manifest["systems"][cell["system"]]
        sample_interval = float(contract["dt_s"]) * decimation
        analysis_path, bitstream_path, analysis = run_analysis(
            capture_csv,
            analysis_dir,
            cell,
            sample_interval,
            decimation,
        )
        sequence_first = int(rows[0]["sequence"])
        sequence_last = int(rows[-1]["sequence"])
        payload = {
            "schema": "fractional-chaos-selected-statistical-stream-v1",
            "status": "complete",
            "cell_id": cell_id,
            "system": cell["system"],
            "method": cell["method"],
            "board": cell["board"],
            "representation": cell["representation"],
            "manifest": {
                "path": display_path(MANIFEST_PATH),
                "sha256": manifest_sha256,
            },
            "started_utc": started_utc,
            "finished_utc": campaign.utc_now(),
            "reset_mode": "stlink_hardware_reset",
            "decimation": decimation,
            "matching_frames": TARGET_FRAMES,
            "discarded_frames": DISCARD_FRAMES,
            "post_discard_bits": EXPECTED_BITS,
            "sequence_first": sequence_first,
            "sequence_last": sequence_last,
            "sequence_increment": decimation,
            "sample_interval_model_time_s": sample_interval,
            "transport": {
                "crc_errors": parser.counters.crc_errors,
                "invalid_headers": parser.counters.invalid_headers,
                "noise_bytes": parser.counters.noise_bytes,
                "trailing_bytes": parser.trailing_bytes,
                "nonzero_status_frames": 0,
                "nonzero_dropped_frames": 0,
            },
            "durations": durations,
            "build": {
                "directory": display_path(build_dir),
                "images": [
                    {
                        "path": display_path(path),
                        "sha256": campaign.sha256_file(path),
                    }
                    for path in images
                ],
            },
            "capture_bin": {
                "path": f"__FINAL__/{capture_bin.name}",
                "sha256": campaign.sha256_file(capture_bin),
                "bytes": capture_bin.stat().st_size,
            },
            "capture_csv": {
                "path": f"__FINAL__/{capture_csv.name}",
                "sha256": campaign.sha256_file(capture_csv),
                "bytes": capture_csv.stat().st_size,
            },
            "analysis": {
                "path": display_path(analysis_path),
                "sha256": campaign.sha256_file(analysis_path),
            },
            "bitstream": {
                "path": display_path(bitstream_path),
                "sha256": campaign.sha256_file(bitstream_path),
                "bytes": bitstream_path.stat().st_size,
                "bits": int(analysis["outputs"]["bit_count"]),
                "extraction_mode": analysis["extraction"]["mode"],
            },
        }
        failure_path = partial_dir / "failure.json"
        if failure_path.is_file():
            failure_path.unlink()
        partial_dir.rename(final_dir)
        for field in ("capture_bin", "capture_csv"):
            payload[field]["path"] = display_path(
                final_dir / Path(payload[field]["path"]).name
            )
        analysis["capture"] = str((final_dir / "capture.csv").resolve())
        if len(analysis["analysis_command"]) >= 3:
            analysis["analysis_command"][2] = str(
                (final_dir / "capture.csv").resolve()
            )
        campaign.atomic_write_json(analysis_path, analysis)
        payload["analysis"]["sha256"] = campaign.sha256_file(analysis_path)
        campaign.atomic_write_json(final_run, payload)
        return validate_completed_run(final_run)
    except Exception:
        if partial_dir.exists():
            failure = {
                "schema": "fractional-chaos-selected-statistical-stream-failure-v1",
                "cell_id": cell_id,
                "started_utc": started_utc,
                "failed_utc": campaign.utc_now(),
            }
            campaign.atomic_write_json(partial_dir / "failure.json", failure)
        raise


def build_battery_manifest(completed: list[dict[str, Any]]) -> None:
    """Write the input manifest consumed by ``run_statistical_batteries.py``."""

    source = json.loads(SOURCE_BATTERY_MANIFEST.read_text(encoding="utf-8"))
    streams = []
    for run in sorted(completed, key=lambda item: item["cell_id"]):
        streams.append(
            {
                "id": run["cell_id"],
                "system": run["system"],
                "method": run["method"],
                "board": run["board"],
                "representation": run["representation"],
                "analysis": run["analysis"]["path"],
                "bitstream": run["bitstream"]["path"],
                "expected_bits": run["post_discard_bits"],
                "expected_sha256": run["bitstream"]["sha256"],
            }
        )
    payload = {
        "schema_version": 2,
        "scope": (
            "Twelve physical M2sFRK streams from Chen, Liu, and "
            "Hammouch-Mekkaoui on F746/H755 in float32 and fixed-point form"
        ),
        "alpha": source["alpha"],
        "input_contract": source["input_contract"],
        "tool_provenance": source["tool_provenance"],
        "streams": streams,
    }
    campaign.atomic_write_json(BATTERY_MANIFEST_PATH, payload)


def main() -> int:
    """Command-line entry point."""

    parser = argparse.ArgumentParser(
        description="Acquire long physical bitstreams for the selected systems"
    )
    parser.add_argument("--cell", action="append", default=[])
    parser.add_argument(
        "--execute",
        action="store_true",
        help="program and reset the selected physical board",
    )
    args = parser.parse_args()

    manifest, manifest_sha256 = campaign.load_manifest(MANIFEST_PATH)
    cells = cell_by_id(manifest)
    expected = set(expected_cell_ids())
    if set(cells) != expected:
        raise StatisticalAcquisitionError("selected cell matrix is incomplete")
    selected = args.cell or list(expected_cell_ids())
    unknown = sorted(set(selected) - expected)
    if unknown:
        parser.error(f"unknown statistical cells: {unknown}")

    CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
    ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    if not args.execute:
        print(
            json.dumps(
                {
                    "execute": False,
                    "cells": selected,
                    "decimation_by_board": DECIMATION_BY_BOARD,
                    "target_frames": TARGET_FRAMES,
                    "post_discard_bits": EXPECTED_BITS,
                },
                indent=2,
            )
        )
        return 0

    for cell_id in selected:
        print(f"acquiring {cell_id}", flush=True)
        result = acquire_one(manifest, manifest_sha256, cells[cell_id])
        print(
            json.dumps(
                {
                    "cell_id": cell_id,
                    "status": result["status"],
                    "frames": result["matching_frames"],
                    "bits": result["post_discard_bits"],
                    "bitstream_sha256": result["bitstream"]["sha256"],
                }
            ),
            flush=True,
        )

    completed = []
    for cell_id in expected_cell_ids():
        run_path = CAPTURE_ROOT / cell_id / "run.json"
        if run_path.is_file():
            completed.append(validate_completed_run(run_path))
    build_battery_manifest(completed)
    print(
        json.dumps(
            {
                "completed_streams": len(completed),
                "expected_streams": len(expected),
                "battery_manifest": str(BATTERY_MANIFEST_PATH),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
