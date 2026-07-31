#!/usr/bin/env python3
"""Pruebas sin hardware del endpoint UART denso y acotado."""

from __future__ import annotations

import copy
import json
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_physical_campaign as campaign  # noqa: E402


DENSE_MANIFEST = ROOT / "validation" / "dense_uart_capture_manifest.json"
OTHER_SYSTEMS_DENSE_MANIFEST = (
    ROOT / "validation" / "dense_uart_rossler_chen_capture_manifest.json"
)
ROSSLER_CLASSIC_DENSE_MANIFEST = (
    ROOT / "validation" / "dense_uart_rossler_classic_capture_manifest.json"
)
DENSE_CELLS = {
    "lorenz_m2sfrk_f746_float32",
    "lorenz_m2sfrk_f746_fixed",
    "lorenz_m2sfrk_h755_float32",
    "lorenz_m2sfrk_h755_fixed",
}


def wire_frame(*, sequence: int, cycles: int = 321) -> bytes:
    prefix = struct.pack(
        "<I6BH6I",
        0x31434346,
        1,
        1,
        1,
        0,
        2,
        0,
        24,
        sequence,
        cycles,
        0,
        0x3DCCCCCD,
        0x3DCCCCCD,
        0x3DCCCCCD,
    )
    return prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)


def dense_manifest() -> tuple[dict, str]:
    return campaign.load_manifest(DENSE_MANIFEST)


def dense_cell() -> dict[str, str]:
    return {
        "cell_id": "lorenz_m2sfrk_f746_float32",
        "system": "lorenz",
        "method": "m2sfrk",
        "board": "f746",
        "representation": "float32",
    }


def parsed_frames(sequences: range | list[int]):
    parser = campaign.FCC1StreamParser()
    frames = []
    payload = b"".join(wire_frame(sequence=value) for value in sequences)
    for offset in range(0, len(payload), 4096):
        frames.extend(parser.feed(payload[offset : offset + 4096]))
    return parser, frames


def test_dense_manifest_is_derived_and_scopes_exactly_four_cells() -> None:
    manifest, digest = dense_manifest()
    schedule = campaign.generate_schedule(manifest, digest)
    campaign.validate_schedule(schedule, manifest)
    plan = campaign.plan_payload(manifest, digest, schedule)

    assert manifest["campaign_id"] == "stm32_dense_lorenz_m2sfrk_4cells_v1"
    assert set(manifest["dense_capture_cells"]) == DENSE_CELLS
    assert manifest["derivation"]["paper_matrix_inherited_for_validator"] is True
    assert plan["matrix_cells"] == 36
    assert plan["dense_capture_cell_count"] == 4
    assert plan["dense_capture_eligible_as_primary_benchmark"] is False
    dense_run = next(
        row
        for row in schedule
        if row["cell_id"] == "lorenz_m2sfrk_f746_float32"
    )
    assert campaign.pilot_run_id(
        dense_run, "dense_timeseries_pilot"
    ).endswith("__dense-timeseries-pilot")


def test_other_systems_dense_manifest_scopes_rossler_and_chen() -> None:
    manifest, digest = campaign.load_manifest(OTHER_SYSTEMS_DENSE_MANIFEST)
    schedule = campaign.generate_schedule(manifest, digest)
    campaign.validate_schedule(schedule, manifest)
    dense_cells = set(manifest["dense_capture_cells"])

    assert manifest["campaign_id"] == (
        "stm32_dense_rossler_chen_m2sfrk_f746_v1"
    )
    assert dense_cells == {
        "rossler_m2sfrk_f746_float32",
        "rossler_m2sfrk_f746_fixed",
        "chen_m2sfrk_f746_float32",
        "chen_m2sfrk_f746_fixed",
    }
    assert {
        row["system"]
        for row in schedule
        if row["cell_id"] in dense_cells
    } == {"rossler", "chen"}


def test_rossler_classic_dense_manifest_is_scoped_and_preserves_history() -> None:
    raw = json.loads(ROSSLER_CLASSIC_DENSE_MANIFEST.read_text(encoding="utf-8"))
    manifest, digest = campaign.load_manifest(ROSSLER_CLASSIC_DENSE_MANIFEST)
    base, _base_digest = campaign.load_manifest(
        ROOT / "validation" / "physical_campaign_manifest.json"
    )
    historical, _historical_digest = campaign.load_manifest(
        OTHER_SYSTEMS_DENSE_MANIFEST
    )
    schedule = campaign.generate_schedule(manifest, digest)
    campaign.validate_schedule(schedule, manifest)
    plan = campaign.plan_payload(manifest, digest, schedule)

    assert raw["extends"] == "physical_campaign_manifest.json"
    assert manifest["campaign_id"] == (
        "stm32_dense_rossler_classic_q09877_m2sfrk_4cells_v1"
    )
    assert set(manifest["dense_capture_cells"]) == {
        "rossler_m2sfrk_f746_float32",
        "rossler_m2sfrk_f746_fixed",
        "rossler_m2sfrk_h755_float32",
        "rossler_m2sfrk_h755_fixed",
    }
    assert plan["matrix_cells"] == 36
    assert plan["dense_capture_cell_count"] == 4
    assert {
        row["system"]
        for row in schedule
        if row["cell_id"] in manifest["dense_capture_cells"]
    } == {"rossler"}

    rossler = manifest["systems"]["rossler"]
    assert rossler["manifest_id"] == "rossler_classic_caputo_v2"
    assert rossler["parameters"] == [0.2, 0.2, 5.7]
    assert rossler["q"] == pytest.approx(0.9877)
    assert rossler["dt_s"] == pytest.approx(0.01)
    assert rossler["memory_s"] == 10
    assert rossler["memory_increments"] == 1000
    assert rossler["history_states"] == 1001
    assert rossler["initial_state_words_hex"] == [
        "0x3F800000",
        "0x00000000",
        "0x00000000",
    ]
    assert rossler["transient_steps"] == 5000
    assert rossler["observation_steps"] == 20_000

    endpoint = manifest["endpoints"]["dense_timeseries_pilot"]
    assert endpoint["required_frames"] == 12_000
    assert endpoint["first_sequence"] == 1
    assert endpoint["last_sequence"] == 12_000
    assert endpoint["sequence_increment"] == 1
    assert manifest["safety"][
        "maximum_dense_timeseries_pilot_runs_per_invocation"
    ] == 2

    assert manifest["systems"]["lorenz"] == base["systems"]["lorenz"]
    assert manifest["systems"]["chen"] == base["systems"]["chen"]
    assert base["systems"]["rossler"]["manifest_id"] == "rossler_caputo_v1"
    assert base["systems"]["rossler"]["q"] == pytest.approx(0.97)
    assert historical["systems"]["rossler"] == base["systems"]["rossler"]
    assert historical["campaign_id"] == (
        "stm32_dense_rossler_chen_m2sfrk_f746_v1"
    )


def test_rossler_classic_contract_is_rejected_outside_approved_campaign() -> None:
    manifest, _digest = campaign.load_manifest(
        ROSSLER_CLASSIC_DENSE_MANIFEST
    )
    unapproved = copy.deepcopy(manifest)
    unapproved["campaign_id"] = "stm32_unapproved_rossler_classic_v2"

    with pytest.raises(
        campaign.CampaignError,
        match="manifest_id de rossler no coincide",
    ):
        campaign.validate_manifest(unapproved)


def test_dense_build_profile_is_dedicated_and_enables_buffering() -> None:
    manifest, _digest = dense_manifest()
    cell = dense_cell()
    directory = campaign.build_directory(
        manifest, cell, 1, buffered_capture_mode=True
    )
    command = campaign.build_command(
        manifest, cell, 1, buffered_capture_mode=True
    )
    flash = campaign.flash_command(
        manifest, cell, 1, buffered_capture_mode=True
    )

    assert directory == (
        ROOT
        / "build"
        / "campaign"
        / "f746"
        / "dense-12000-decim-1-release"
    ).resolve()
    assert "-BufferedCaptureMode" in command
    assert command[command.index("-BufferedCaptureSamples") + 1] == "12000"
    assert command[command.index("-Decimation") + 1] == "1"
    assert str(directory) in flash


def test_git_metadata_excludes_only_declared_generated_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        stdout = "f3484c2\n" if "rev-parse" in command else ""
        return SimpleNamespace(returncode=0, stdout=stdout)

    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    output = ROOT / "validation" / "results" / "dense_uart_capture"
    build = ROOT / "build" / "campaign" / "f746" / "dense"
    metadata = campaign.git_metadata(
        exclude_generated_paths=(output, build),
    )

    status_command = next(command for command in commands if "status" in command)
    assert ":(exclude)validation/results/dense_uart_capture/**" in status_command
    assert ":(exclude)build/campaign/f746/dense/**" in status_command
    assert metadata["dirty"] is False
    assert metadata["commit"] == "f3484c2"
    assert metadata["excluded_generated_paths"] == [
        "validation/results/dense_uart_capture",
        "build/campaign/f746/dense",
    ]


def test_dense_summary_requires_exact_sequences_1_through_12000() -> None:
    manifest, _digest = dense_manifest()
    parser, frames = parsed_frames(range(1, 12_001))
    summary = campaign.summarize_dense_capture(
        manifest, dense_cell(), frames, parser
    )

    assert summary["endpoint"]["complete"] is True
    assert summary["endpoint"]["received_frames"] == 12_000
    assert summary["transport"]["accepted"] is True
    assert summary["transport"]["sequence_gaps"] == 0
    assert summary["time_series"]["sample_spacing_model_steps"] == 1
    assert summary["time_series"]["sample_interval_model_time"] == 0.005
    assert summary["time_series"]["host_reception_time_is_model_time"] is False
    assert summary["eligible_as_primary_benchmark"] is False
    assert summary["timing"]["accepted_as_solver_timing"] is False

    missing = list(range(1, 12_001))
    missing.remove(6000)
    parser_missing, frames_missing = parsed_frames(missing)
    rejected = campaign.summarize_dense_capture(
        manifest, dense_cell(), frames_missing, parser_missing
    )
    assert rejected["endpoint"]["complete"] is False
    assert rejected["transport"]["accepted"] is False
    assert rejected["transport"]["sequence_gaps"] == 1


def test_dense_capture_opens_and_flushes_uart_before_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, _digest = dense_manifest()
    short_manifest = copy.deepcopy(manifest)
    short_manifest["endpoints"]["dense_timeseries_pilot"][
        "last_sequence"
    ] = 3
    payload = bytearray(
        b"".join(wire_frame(sequence=value) for value in range(1, 4))
    )
    events: list[object] = []

    class FakeSerial:
        def __init__(self, *_args, **_kwargs):
            events.append("uart_open")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def set_buffer_size(self, **_kwargs):
            return None

        def reset_input_buffer(self):
            events.append("uart_flush")

        @property
        def in_waiting(self):
            return len(payload)

        def read(self, size: int):
            chunk = bytes(payload[:size])
            del payload[:size]
            return chunk

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            events.append(("reset_start", command))

        def poll(self):
            return 0

        def kill(self):
            raise AssertionError("el reset simulado no debe terminarse")

        def wait(self, **_kwargs):
            return 0

    def fake_flash_command(*_args, **kwargs):
        events.append(("flash_command", kwargs))
        return ["fake-reset"]

    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=FakeSerial)
    )
    monkeypatch.setattr(campaign.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(campaign, "flash_command", fake_flash_command)

    _raw, frames, _parser, _durations = campaign.capture_after_reset(
        short_manifest,
        dense_cell(),
        1,
        tmp_path / "reset.log",
        endpoint="dense_timeseries_pilot",
    )

    assert events[0:2] == ["uart_open", "uart_flush"]
    assert events[2][0] == "flash_command"
    assert events[2][1]["reset_only"] is True
    assert events[2][1]["buffered_capture_mode"] is True
    assert events[3][0] == "reset_start"
    assert [frame[8] for frame in frames] == [1, 2, 3]


def test_dense_execution_is_limited_to_two_runs_before_hardware(
    tmp_path: Path,
) -> None:
    manifest, _digest = dense_manifest()
    args = SimpleNamespace(
        manifest=DENSE_MANIFEST,
        output_root=tmp_path,
        endpoint="dense_timeseries_pilot",
        cell=["lorenz_m2sfrk_f746_float32"],
        board=None,
        cold_start=None,
        max_runs=3,
        execute=True,
        resume=False,
        confirm_campaign_id=manifest["campaign_id"],
        allow_pilot_only=True,
        break_stale_locks=False,
    )
    with pytest.raises(campaign.CampaignError, match="máximo 2"):
        campaign.command_run(args)

    args.execute = False
    args.max_runs = 1
    args.cell = ["chen_gl_f746_float32"]
    with pytest.raises(campaign.CampaignError, match="celdas inexistentes"):
        campaign.command_run(args)


def test_dense_dry_run_keeps_evidence_separate_from_calibration(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    args = SimpleNamespace(
        manifest=DENSE_MANIFEST,
        output_root=tmp_path,
        endpoint="dense_timeseries_pilot",
        cell=["lorenz_m2sfrk_f746_float32"],
        board=None,
        cold_start=1,
        max_runs=1,
        execute=False,
        resume=False,
        confirm_campaign_id=None,
        allow_pilot_only=False,
        break_stale_locks=False,
    )
    assert campaign.command_run(args) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["dense_contract_selected_runs"] == 1
    assert summary["transport_calibrated_selected_runs"] == 1
    assert summary["buffered_capture_mode"] is True
    assert summary["eligible_as_primary_benchmark"] is False
    assert summary["evidence_level"] == (
        "dense_consecutive_state_series_under_stlink_hardware_reset"
    )
