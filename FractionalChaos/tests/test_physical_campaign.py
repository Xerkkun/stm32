#!/usr/bin/env python3
"""Pruebas sin hardware para el runner de la campaña física."""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_uart  # noqa: E402
import run_physical_campaign as campaign  # noqa: E402


def wire_frame(
    *,
    sequence: int,
    cycles: int = 321,
    dropped: int = 0,
    kind: int = 1,
    board: int = 1,
    system: int = 0,
    method: int = 2,
    status: int = 0,
    words: tuple[int, int, int] = (
        0x3DCCCCCD,
        0x3DCCCCCD,
        0x3DCCCCCD,
    ),
) -> bytes:
    prefix = struct.pack(
        "<I6BH6I",
        0x31434346,
        1,
        kind,
        board,
        system,
        method,
        status,
        24,
        sequence,
        cycles,
        dropped,
        *words,
    )
    return prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)


def manifest() -> tuple[dict[str, object], str]:
    return campaign.load_manifest(
        ROOT / "validation" / "physical_campaign_manifest.json"
    )


def test_manifest_expands_to_exact_36_by_30_schedule() -> None:
    data, digest = manifest()
    cells = campaign.matrix_cells(data)
    schedule = campaign.generate_schedule(data, digest)
    campaign.validate_schedule(schedule, data)

    assert len(cells) == 36
    assert len(schedule) == 1080
    assert len({row["run_id"] for row in schedule}) == 1080
    assert {
        row["cell_id"]
        for row in schedule
        if row["cold_start_id"] == 1
    } == {cell["cell_id"] for cell in cells}
    assert all(
        sum(row["cell_id"] == cell["cell_id"] for row in schedule) == 30
        for cell in cells
    )


def test_schedule_and_run_ids_are_deterministic() -> None:
    data, digest = manifest()
    first = campaign.generate_schedule(data, digest)
    second = campaign.generate_schedule(data, digest)
    assert first == second
    assert campaign.plan_payload(data, digest, first)["schedule_sha256"] == (
        campaign.plan_payload(data, digest, second)["schedule_sha256"]
    )


def test_only_four_pilot_cells_have_transport_calibration() -> None:
    data, digest = manifest()
    schedule = campaign.generate_schedule(data, digest)
    calibrated_cells = {
        row["cell_id"]
        for row in schedule
        if row["calibration_status"] == "accepted_pilot"
    }
    assert calibrated_cells == {
        "lorenz_m2sfrk_f746_float32",
        "lorenz_m2sfrk_f746_fixed",
        "lorenz_m2sfrk_h755_float32",
        "lorenz_m2sfrk_h755_fixed",
    }


def test_incremental_parser_reports_crc_failure_and_resynchronizes() -> None:
    damaged = bytearray(wire_frame(sequence=512))
    damaged[20] ^= 0x01
    parser = campaign.FCC1StreamParser()
    frames = []
    payload = b"boot" + bytes(damaged) + wire_frame(sequence=1024)
    for offset in range(0, len(payload), 7):
        frames.extend(parser.feed(payload[offset : offset + 7]))

    assert [values[8] for values in frames] == [1024]
    assert parser.counters.valid_frames == 1
    assert parser.counters.crc_errors == 1
    assert parser.counters.noise_bytes > 0


def test_transport_summary_never_promotes_decimated_cycles_to_primary() -> None:
    data, _digest = manifest()
    cell = {
        "cell_id": "lorenz_m2sfrk_f746_float32",
        "system": "lorenz",
        "method": "m2sfrk",
        "board": "f746",
        "representation": "float32",
    }
    parser = campaign.FCC1StreamParser()
    frames = parser.feed(
        b"".join(
            wire_frame(sequence=sequence)
            for sequence in range(512, 12288 + 1, 512)
        )
    )
    summary = campaign.summarize_capture(data, cell, 512, frames, parser)

    assert summary["transport"]["accepted"] is True
    assert summary["endpoint"]["target_sequence"] == 12288
    assert (
        summary["timing"]["reported_decimated_cycle_values_after_warmup"]
        < 10_000
    )
    assert summary["timing"]["eligible_as_primary_benchmark"] is False
    assert summary["eligible_as_paper_cold_start"] is False


def test_benchmark_kind4_preserves_exactly_10000_cycle_values() -> None:
    data, _digest = manifest()
    cell = {
        "cell_id": "lorenz_m2sfrk_h755_fixed",
        "system": "lorenz",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "fixed_q14_q30",
    }
    parser = campaign.FCC1StreamParser()
    payload = bytearray()
    expected_cycles = []
    for index in range(0, 10_000, 4):
        block = (1000 + index, 1001 + index, 1002 + index, 1003 + index)
        expected_cycles.extend(block)
        payload.extend(
            wire_frame(
                sequence=index,
                cycles=block[0],
                words=block[1:],
                kind=decode_uart.FRAME_TIMING_BLOCK,
                board=2,
            )
        )
    frames = parser.feed(bytes(payload))
    summary, cycles = campaign.summarize_benchmark_capture(
        data, cell, frames, parser
    )

    assert cycles == expected_cycles
    assert len(cycles) == 10_000
    assert summary["endpoint"]["received_blocks"] == 2500
    assert summary["transport"]["accepted"] is True
    assert summary["timing"]["accepted_as_solver_timing"] is True
    assert summary["eligible_as_primary_benchmark"] is False
    assert summary["reset"]["eligible_as_paper_cold_start"] is False


def test_build_and_flash_commands_are_scoped_to_cell_resources() -> None:
    data, _digest = manifest()
    cell = {
        "cell_id": "lorenz_m2sfrk_h755_float32",
        "system": "lorenz",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "float32",
    }
    directory = campaign.build_directory(data, cell, 1024)
    build = campaign.build_command(data, cell, 1024)
    flash = campaign.flash_command(data, cell, 1024)

    assert directory == (
        ROOT / "build" / "campaign" / "h755" / "decim-1024-release"
    ).resolve()
    assert "h755_m7_lorenz_m2sfrk" in build
    assert "003700344142501220353451" in flash
    assert str(directory) in flash
    assert "COM6" not in flash  # El puerto se abre en Python, no en el flasher.

    benchmark_directory = campaign.build_directory(
        data, cell, 512, benchmark_mode=True
    )
    benchmark_build = campaign.build_command(
        data, cell, 512, benchmark_mode=True
    )
    benchmark_flash = campaign.flash_command(
        data, cell, 512, benchmark_mode=True
    )
    benchmark_program = campaign.flash_command(
        data, cell, 512, benchmark_mode=True, no_reset=True
    )
    benchmark_reset = campaign.flash_command(
        data, cell, 512, benchmark_mode=True, reset_only=True
    )
    assert benchmark_directory.name == "benchmark-10000-release"
    assert "-BenchmarkMode" in benchmark_build
    assert str(benchmark_directory) in benchmark_flash
    assert "-NoReset" in benchmark_program
    assert "-ResetOnly" in benchmark_reset


def test_manifest_rejects_primary_ready_without_power_cycle_contract() -> None:
    data, _digest = manifest()
    copied = json.loads(json.dumps(data))
    copied["endpoints"]["primary_benchmark"]["ready"] = True
    try:
        campaign.validate_manifest(copied)
    except campaign.CampaignError as exc:
        assert "reset ST-LINK" in str(exc)
    else:
        raise AssertionError("se aceptó un endpoint primario no implementado")


def test_resume_accepts_only_complete_hashed_run(tmp_path: Path) -> None:
    data, digest = manifest()
    scheduled = campaign.generate_schedule(data, digest)[0]
    endpoint = "benchmark_reset_pilot"
    run_directory = campaign.pilot_run_directory(
        tmp_path, data["campaign_id"], scheduled, endpoint
    )
    run_directory.mkdir(parents=True)
    raw = b"raw-fcc1"
    timing = b"timed_index,cycles\n0,123\n"
    (run_directory / "capture.bin").write_bytes(raw)
    (run_directory / "timing_cycles.csv").write_bytes(timing)
    result = {
        "schema": "fractional-chaos-physical-run-v1",
        "run_id": campaign.pilot_run_id(scheduled, endpoint),
        "campaign_id": data["campaign_id"],
        "manifest_sha256": digest,
        "endpoint_name": endpoint,
        "cell": {
            "cell_id": scheduled["cell_id"],
            "cold_start_id": scheduled["cold_start_id"],
        },
        "transport": {"accepted": True},
        "timing": {"accepted_as_solver_timing": True},
        "capture": {
            "raw_path": "capture.bin",
            "raw_sha256": campaign.sha256_bytes(raw),
            "timing_cycles_path": "timing_cycles.csv",
            "timing_cycles_sha256": campaign.sha256_bytes(timing),
        },
    }
    (run_directory / "run.json").write_text(
        json.dumps(result), encoding="utf-8"
    )

    assert (
        campaign.validate_completed_run(
            tmp_path, data, digest, scheduled, endpoint
        )["run_id"]
        == result["run_id"]
    )
    (run_directory / "timing_cycles.csv").write_bytes(b"tampered")
    try:
        campaign.validate_completed_run(
            tmp_path, data, digest, scheduled, endpoint
        )
    except campaign.CampaignError as exc:
        assert "hash de ciclos" in str(exc)
    else:
        raise AssertionError("--resume aceptó un artefacto alterado")
