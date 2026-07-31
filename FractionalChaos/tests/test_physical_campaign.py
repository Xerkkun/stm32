#!/usr/bin/env python3
"""Pruebas sin hardware para el runner de la campaña física."""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_uart  # noqa: E402
import run_physical_campaign as campaign  # noqa: E402
from host.ina226_capture import (  # noqa: E402
    EDGE_CLOCK_REFERENCE,
    EDGE_LEVEL,
    END_COMPLETE,
    FRAME_TYPE_EDGE,
    FRAME_TYPE_END,
    FRAME_TYPE_SAMPLE,
    SAMPLE_CONVERSION_READY,
    SAMPLE_I2C_OK,
    CaptureStream as InaCaptureStream,
    FrameDecoder as InaFrameDecoder,
    build_capture_records as build_ina_records,
    encode_frame as ina226_frame,
    parse_armed as parse_ina_armed,
)


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


def historical_manifest() -> tuple[dict[str, object], str]:
    return campaign.load_manifest(
        ROOT / "validation" / "physical_campaign_manifest.json"
    )


def selected_manifest() -> tuple[dict[str, object], str]:
    return campaign.load_manifest(
        ROOT / "validation" / "physical_campaign_selected_v1.json"
    )


def manifest() -> tuple[dict[str, object], str]:
    """Conserva el contrato histórico para los pilotos ya registrados."""

    return historical_manifest()


def test_manifest_expands_to_exact_36_by_30_schedule() -> None:
    for load in (historical_manifest, selected_manifest):
        data, digest = load()
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
    for load in (historical_manifest, selected_manifest):
        data, digest = load()
        first = campaign.generate_schedule(data, digest)
        second = campaign.generate_schedule(data, digest)
        assert first == second
        assert campaign.plan_payload(data, digest, first)["schedule_sha256"] == (
            campaign.plan_payload(data, digest, second)["schedule_sha256"]
        )


def test_default_manifest_is_the_exact_selected_cohort() -> None:
    assert campaign.DEFAULT_MANIFEST == (
        ROOT / "validation" / "physical_campaign_selected_v1.json"
    )
    data, _digest = selected_manifest()
    assert data["paper_matrix"]["systems"] == [
        "chen",
        "liu",
        "hammouch_mekkaoui",
    ]
    assert {
        name: contract["wire_id"]
        for name, contract in data["systems"].items()
    } == {
        "chen": 2,
        "liu": 3,
        "hammouch_mekkaoui": 4,
    }
    assert data["schedule"]["cold_starts_per_cell"] == 30
    assert data["calibrated_cells"] == {}
    assert data["endpoints"]["primary_benchmark"]["ready"] is False
    assert data["reset_contract"]["power_removed"] is False


def test_manifest_rejects_mixed_historical_and_selected_cohorts() -> None:
    data, _digest = selected_manifest()
    copied = json.loads(json.dumps(data))
    copied["paper_matrix"]["systems"] = ["chen", "liu", "rossler"]
    copied["systems"]["rossler"] = copied["systems"].pop("hammouch_mekkaoui")
    try:
        campaign.validate_manifest(copied)
    except campaign.CampaignError as exc:
        assert "cohorte exacta" in str(exc)
    else:
        raise AssertionError("se aceptó una mezcla de cohortes")


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
    assert build[build.index("-BuildRoot") + 1] == "build/campaign"
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

    handshake_directory = campaign.build_directory(
        data,
        cell,
        512,
        benchmark_mode=True,
        primary_handshake=True,
    )
    handshake_build = campaign.build_command(
        data,
        cell,
        512,
        benchmark_mode=True,
        primary_handshake=True,
    )
    handshake_flash = campaign.flash_command(
        data,
        cell,
        512,
        benchmark_mode=True,
        primary_handshake=True,
        no_reset=True,
    )
    assert handshake_directory.name == (
        "benchmark-10000-primary-handshake-release"
    )
    assert "-BenchmarkMode" in handshake_build
    assert "-PrimaryHandshake" in handshake_build
    assert str(handshake_directory) in handshake_flash
    with pytest.raises(campaign.CampaignError, match="BenchmarkMode"):
        campaign.build_command(
            data,
            cell,
            512,
            primary_handshake=True,
        )


def test_selected_build_and_flash_commands_use_new_system_names() -> None:
    data, _digest = selected_manifest()
    cell = {
        "cell_id": "hammouch_mekkaoui_m2sfrk_h755_fixed",
        "system": "hammouch_mekkaoui",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "fixed_q14_q30",
    }
    directory = campaign.build_directory(
        data, cell, 512, benchmark_mode=True
    )
    build = campaign.build_command(
        data, cell, 512, benchmark_mode=True
    )
    flash = campaign.flash_command(
        data, cell, 512, benchmark_mode=True
    )

    assert directory == (
        ROOT
        / "build"
        / "campaign-selected"
        / "h755"
        / "benchmark-10000-release"
    ).resolve()
    assert "h755_m7_hammouch_mekkaoui_m2sfrk_fixed" in build
    assert build[build.index("-BuildRoot") + 1] == (
        "build/campaign-selected"
    )
    assert "-System" in flash
    assert "hammouch_mekkaoui" in flash
    assert "003700344142501220353451" in flash


def test_runtime_probe_command_is_scoped_to_handshake_build(
    tmp_path: Path,
) -> None:
    data, _digest = selected_manifest()
    cell = {
        "cell_id": "chen_m2sfrk_h755_float32",
        "system": "chen",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "float32",
    }
    command = campaign.runtime_probe_command(data, cell, 512, tmp_path)
    assert command[command.index("-Board") + 1] == "h755"
    assert command[command.index("-Target") + 1] == (
        "h755_m7_chen_m2sfrk"
    )
    assert command[command.index("-ProbeSerial") + 1] == (
        "003700344142501220353451"
    )
    assert (
        Path(command[command.index("-BuildDirectory") + 1]).name
        == "benchmark-10000-primary-handshake-release"
    )
    assert command[command.index("-PythonExecutable") + 1] == sys.executable


def write_runtime_probe_fixture(
    directory: Path,
    core: str,
    *,
    expected_clock_hz: int,
    software_clock_hz: int | None = None,
) -> bytes:
    raw = (core.encode("ascii") * 84)[:84]
    raw_path = directory / f"runtime_probe_{core}.bin"
    report_path = directory / f"runtime_probe_{core}.json"
    location_path = directory / f"runtime_probe_{core}_location.json"
    raw_path.write_bytes(raw)
    report_path.write_text(
        json.dumps(
            {
                "schema": "fractional-chaos-runtime-probe-v1",
                "core": core,
                "record": {
                    "stack_high_water_bytes": 1024,
                    "expected_core_clock_hz": expected_clock_hz,
                    "software_core_clock_hz": (
                        expected_clock_hz
                        if software_clock_hz is None
                        else software_clock_hz
                    ),
                    "heap_configured_bytes": 0,
                },
                "quality": {
                    "complete": True,
                    "stack_probe_overflow": False,
                    "stack_headroom_bytes": 31_744,
                },
                "raw": {"sha256": campaign.sha256_bytes(raw)},
                "eligible_as_primary_evidence": False,
            }
        ),
        encoding="utf-8",
    )
    location_path.write_text("{}", encoding="utf-8")
    return raw


def test_runtime_probe_bundle_preserves_artifact_hashes(
    tmp_path: Path,
) -> None:
    raw = write_runtime_probe_fixture(
        tmp_path,
        "f746_m7",
        expected_clock_hz=216_000_000,
    )

    bundle = campaign.load_runtime_probe_artifacts(
        tmp_path,
        "f746",
        216_000_000,
    )
    assert bundle["eligible_as_primary_evidence"] is False
    assert bundle["records"][0]["raw_sha256"] == (
        campaign.sha256_bytes(raw)
    )
    assert bundle["records"][0]["stack_high_water_bytes"] == 1024
    assert (
        bundle["records"][0]["expected_core_clock_hz"]
        == 216_000_000
    )


@pytest.mark.parametrize(
    ("core", "field", "wrong_clock_hz"),
    (
        ("h755_m7", "expected_core_clock_hz", 480_000_000),
        ("h755_m7", "software_core_clock_hz", 480_000_000),
        ("h755_m4", "expected_core_clock_hz", 240_000_000),
        ("h755_m4", "software_core_clock_hz", 240_000_000),
    ),
)
def test_runtime_probe_bundle_rejects_clock_mismatch(
    tmp_path: Path,
    core: str,
    field: str,
    wrong_clock_hz: int,
) -> None:
    write_runtime_probe_fixture(
        tmp_path,
        "h755_m7",
        expected_clock_hz=400_000_000,
    )
    write_runtime_probe_fixture(
        tmp_path,
        "h755_m4",
        expected_clock_hz=200_000_000,
    )
    report_path = tmp_path / f"runtime_probe_{core}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["record"][field] = wrong_clock_hz
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(campaign.CampaignError, match="reloj FRP1"):
        campaign.load_runtime_probe_artifacts(
            tmp_path,
            "h755",
            400_000_000,
        )


def test_build_script_scopes_optional_root_and_selected_target_names() -> None:
    script = (ROOT / "tools" / "build_campaign.ps1").read_text(
        encoding="utf-8"
    )
    assert "[string]$BuildRoot = 'build/campaign'" in script
    assert "BuildRoot debe permanecer dentro de $allowedBuildRoot" in script
    assert "liu|hammouch_mekkaoui" in script
    assert "'-DFC_H755_CLOCK_480=OFF'" in script
    assert "'-DFC_H755_LDO_MODIFICATION_CONFIRMED=OFF'" in script
    assert "'-DFC_H755_SMOKE_DIAGNOSTICS=OFF'" in script


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
    clock_binding = campaign.campaign_firmware_clock_binding(
        data,
        scheduled["board"],
    )
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
        "build": {
            "firmware_clock_profile": clock_binding[
                "firmware_clock_profile"
            ],
            "system_clock_hz": clock_binding["system_clock_hz"],
            "explicit_h755_clock_480_off": clock_binding[
                "explicit_h755_clock_480_off"
            ],
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
    original_build = dict(result["build"])
    mismatched_build_values = {
        "firmware_clock_profile": "h755_400mhz"
        if scheduled["board"] == "f746"
        else "f746_216mhz",
        "system_clock_hz": original_build["system_clock_hz"] + 1,
        "explicit_h755_clock_480_off": not original_build[
            "explicit_h755_clock_480_off"
        ],
    }
    for field, mismatched_value in mismatched_build_values.items():
        result["build"][field] = mismatched_value
        (run_directory / "run.json").write_text(
            json.dumps(result),
            encoding="utf-8",
        )
        with pytest.raises(campaign.CampaignError, match="inconsistente"):
            campaign.validate_completed_run(
                tmp_path, data, digest, scheduled, endpoint
            )
        result["build"] = dict(original_build)
    (run_directory / "run.json").write_text(
        json.dumps(result),
        encoding="utf-8",
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeLineSerial:
    def __init__(self, response_factory=None) -> None:
        self.response_factory = response_factory
        self.response = bytearray()
        self.writes: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def reset_input_buffer(self) -> None:
        self.response.clear()

    def set_buffer_size(self, **_kwargs) -> None:
        return None

    def write(self, payload: bytes) -> int:
        self.writes.append(bytes(payload))
        if self.response_factory is not None:
            self.response.extend(self.response_factory(payload))
        return len(payload)

    def flush(self) -> None:
        return None

    @property
    def in_waiting(self) -> int:
        return len(self.response)

    def read(self, size: int) -> bytes:
        chunk = bytes(self.response[:size])
        del self.response[:size]
        return chunk

    def readline(self) -> bytes:
        newline = self.response.find(b"\n")
        if newline < 0:
            return b""
        chunk = bytes(self.response[: newline + 1])
        del self.response[: newline + 1]
        return chunk


def port_record(
    device: str,
    serial_number: str,
    *,
    pid: int,
) -> campaign.SerialPortRecord:
    return campaign.SerialPortRecord(
        device=device,
        serial_number=serial_number,
        vid=0x0483,
        pid=pid,
        hwid=f"USB VID:PID=0483:{pid:04X} SER={serial_number}",
    )


def snapshot(
    *ports: campaign.SerialPortRecord,
) -> campaign.DeviceSnapshot:
    return campaign.DeviceSnapshot(
        ports=ports,
        probe_serials=frozenset(
            campaign.normalize_probe_serial(port.serial_number)
            for port in ports
            if port.serial_number
        ),
        backend="fake_ports_and_probes",
    )


def test_external_power_cycle_tracks_two_boards_and_com_rename() -> None:
    f746_serial = "066AFF504955657867165348"
    h755_serial = "003700344142501220353451"
    f746 = port_record("COM7", f746_serial, pid=0x374B)
    h755_before = port_record("COM6", h755_serial, pid=0x374F)
    h755_after = port_record("COM11", h755_serial, pid=0x374F)
    snapshots = [
        snapshot(f746, h755_before),
        snapshot(f746),
        snapshot(f746, h755_after),
    ]
    snapshot_index = 0

    def next_snapshot() -> campaign.DeviceSnapshot:
        nonlocal snapshot_index
        selected = snapshots[min(snapshot_index, len(snapshots) - 1)]
        snapshot_index += 1
        return selected

    controller = FakeLineSerial(
        lambda _payload: (
            b"ACK req-h755 H755 OFF_OK=1 ON_OK=1 "
            b"OFF_MV=45 ON_MV=3298\n"
        )
    )
    clock = FakeClock()
    config = campaign.PowerCycleConfig(
        controller_port="COM9",
        controller_baud=115200,
        channels={"f746": "F746", "h755": "H755"},
        off_ms=2500,
        timeout_s=5.0,
        poll_interval_s=0.1,
        handshake_timeout_s=2.0,
    )
    result = campaign.perform_external_power_cycle(
        board_name="h755",
        board={
            "probe_serial": h755_serial,
            "port": "COM6",
        },
        run_id="scheduled-run",
        config=config,
        serial_factory=lambda *_args: controller,
        snapshot_factory=next_snapshot,
        clock=clock,
        sleep=clock.sleep,
        request_id="req-h755",
    )

    assert controller.writes == [b"CYCLE req-h755 H755 2500\n"]
    assert result.reopened_port.device == "COM11"
    assert result.evidence["power_removed"] is True
    assert result.evidence["eligible_as_paper_cold_start"] is False
    assert result.evidence["eligible_as_primary_benchmark"] is False
    assert result.evidence["controller"]["relay"] == 2
    assert result.evidence["usb_reenumeration"]["port_changed"] is True
    assert result.evidence["usb_reenumeration"]["probe_disappeared"] is True


def test_power_removed_rejects_ack_without_electrical_off_state() -> None:
    with pytest.raises(campaign.CampaignError, match="ACK físico rechazado"):
        campaign.parse_power_cycle_ack(
            "ACK req-f746 F746 OFF_OK=1 ON_OK=1 "
            "OFF_MV=450 ON_MV=3301",
            request_id="req-f746",
            channel="F746",
            off_threshold_mv=200,
            on_threshold_mv=3100,
        )


def test_power_cycle_timeout_covers_firmware_watchdogs_and_host_margin() -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_f746_float32",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
            "--power-off-ms",
            "30000",
            "--power-cycle-timeout",
            "42.8",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    with pytest.raises(campaign.CampaignError, match="watchdogs OFF/ON"):
        args.handler(args)


def test_power_cycle_ack_alone_cannot_replace_usb_disappearance() -> None:
    serial_number = "066AFF504955657867165348"
    connected = snapshot(
        port_record("COM7", serial_number, pid=0x374B)
    )
    controller = FakeLineSerial(
        lambda _payload: (
            b"ACK req-f746 F746 OFF_OK=1 ON_OK=1 "
            b"OFF_MV=30 ON_MV=3310\n"
        )
    )
    clock = FakeClock()
    config = campaign.PowerCycleConfig(
        controller_port="COM9",
        controller_baud=115200,
        channels={"f746": "F746", "h755": "H755"},
        off_ms=1000,
        timeout_s=0.25,
        poll_interval_s=0.1,
        handshake_timeout_s=1.0,
    )

    with pytest.raises(
        campaign.CampaignError,
        match="desaparición de COM",
    ):
        campaign.perform_external_power_cycle(
            board_name="f746",
            board={"probe_serial": serial_number, "port": "COM7"},
            run_id="scheduled-run",
            config=config,
            serial_factory=lambda *_args: controller,
            snapshot_factory=lambda: connected,
            clock=clock,
            sleep=clock.sleep,
            request_id="req-f746",
        )


def test_postboot_handshake_releases_fcc1_only_after_ready() -> None:
    payload = wire_frame(sequence=0, kind=decode_uart.FRAME_TIMING_BLOCK)
    serial_port = FakeLineSerial(
        lambda _payload: b"READY boot-token\n" + payload
    )
    clock = FakeClock()
    initial, evidence = campaign.perform_postboot_handshake(
        serial_port,
        request_id="boot-token",
        identity=(decode_uart.FRAME_TIMING_BLOCK, 1, 0, 2),
        timeout_s=1.0,
        clock=clock,
        sleep=clock.sleep,
    )

    assert serial_port.writes == [
        b"START boot-token 4 1 0 2\n"
    ]
    assert initial == payload
    assert evidence["ready_after_com_open"] is True
    assert evidence["fcc1_before_ready"] is False
    assert evidence["response_sha256"] == campaign.sha256_bytes(
        b"READY boot-token\n"
    )


def test_postboot_handshake_rejects_fcc1_before_ready() -> None:
    serial_port = FakeLineSerial(lambda _payload: wire_frame(sequence=0))
    clock = FakeClock()
    with pytest.raises(campaign.CampaignError, match="FCC1 antes de READY"):
        campaign.perform_postboot_handshake(
            serial_port,
            request_id="boot-token",
            identity=(1, 1, 0, 2),
            timeout_s=1.0,
            clock=clock,
            sleep=clock.sleep,
        )


def test_capture_reopens_renumbered_com_before_start(
    tmp_path: Path,
) -> None:
    data, _digest = selected_manifest()
    short_manifest = json.loads(json.dumps(data))
    short_manifest["endpoints"]["benchmark_reset_pilot"][
        "last_sequence"
    ] = 0
    f746_serial = data["boards"]["f746"]["probe_serial"]
    h755_serial = data["boards"]["h755"]["probe_serial"]
    f746 = port_record("COM7", f746_serial, pid=0x374B)
    h755_before = port_record("COM6", h755_serial, pid=0x374F)
    h755_after = port_record("COM11", h755_serial, pid=0x374F)
    snapshots = [
        snapshot(f746, h755_before),
        snapshot(f746),
        snapshot(f746, h755_after),
    ]
    snapshot_index = 0

    def next_snapshot() -> campaign.DeviceSnapshot:
        nonlocal snapshot_index
        selected = snapshots[min(snapshot_index, len(snapshots) - 1)]
        snapshot_index += 1
        return selected

    controller = FakeLineSerial(
        lambda payload: (
            (
                f"ACK {payload.decode('ascii').split()[1]} H755 "
                "OFF_OK=1 ON_OK=1 OFF_MV=20 ON_MV=3290\n"
            ).encode("ascii")
        )
    )
    timing_frame = wire_frame(
        sequence=0,
        kind=decode_uart.FRAME_TIMING_BLOCK,
        board=2,
        system=2,
        method=2,
    )
    target = FakeLineSerial(
        lambda payload: (
            (
                f"READY {payload.decode('ascii').split()[1]}\n"
            ).encode("ascii")
            + timing_frame
        )
    )
    opened: list[str] = []
    target_open_attempts = 0

    def serial_factory(port: str, _baud: int, _timeout: float):
        nonlocal target_open_attempts
        opened.append(port)
        if port == "COM9":
            return controller
        if port == "COM11":
            target_open_attempts += 1
            if target_open_attempts == 1:
                raise OSError("driver todavía inicializando")
            return target
        raise AssertionError(f"puerto inesperado: {port}")

    clock = FakeClock()
    config = campaign.PowerCycleConfig(
        controller_port="COM9",
        controller_baud=115200,
        channels={"f746": "F746", "h755": "H755"},
        off_ms=2500,
        timeout_s=5.0,
        poll_interval_s=0.1,
        handshake_timeout_s=2.0,
    )
    evidence: dict[str, object] = {}
    cell = {
        "cell_id": "chen_m2sfrk_h755_float32",
        "system": "chen",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "float32",
        "cold_start_id": 1,
    }

    raw, frames, _parser, _durations = campaign.capture_after_reset(
        short_manifest,
        cell,
        512,
        tmp_path / "reset.log",
        endpoint="benchmark_reset_pilot",
        run_id="pilot-run",
        power_cycle_config=config,
        reset_evidence_out=evidence,
        serial_factory=serial_factory,
        snapshot_factory=next_snapshot,
        clock=clock,
        sleep=clock.sleep,
    )

    assert opened == ["COM9", "COM11", "COM11"]
    assert target.writes[0].startswith(b"START ")
    assert raw == timing_frame
    assert [frame[8] for frame in frames] == [0]
    assert evidence["power_removed"] is True
    assert evidence["postboot_handshake"]["completed"] is True
    assert evidence["port_reopen"]["attempts"] == 2
    assert evidence["port_reopen"]["transient_errors"]
    assert (
        evidence["usb_reenumeration"]["port_after"]["device"] == "COM11"
    )
    reset_log = json.loads((tmp_path / "reset.log").read_text())
    assert reset_log["status"] == "completed"
    assert reset_log["reset_evidence"]["power_removed"] is True


def test_controller_open_fail_off_is_primed_before_measured_cycle(
    tmp_path: Path,
) -> None:
    data, _digest = selected_manifest()
    short_manifest = json.loads(json.dumps(data))
    short_manifest["endpoints"]["benchmark_reset_pilot"][
        "last_sequence"
    ] = 0
    f746_serial = data["boards"]["f746"]["probe_serial"]
    h755_serial = data["boards"]["h755"]["probe_serial"]
    f746 = port_record("COM7", f746_serial, pid=0x374B)
    h755 = port_record("COM6", h755_serial, pid=0x374F)
    snapshots = [
        snapshot(f746),
        snapshot(f746, h755),
        snapshot(f746),
        snapshot(f746, h755),
    ]
    snapshot_index = 0

    def next_snapshot() -> campaign.DeviceSnapshot:
        nonlocal snapshot_index
        selected = snapshots[min(snapshot_index, len(snapshots) - 1)]
        snapshot_index += 1
        return selected

    controller = FakeLineSerial(
        lambda payload: (
            (
                f"ACK {payload.decode('ascii').split()[1]} H755 "
                "OFF_OK=1 ON_OK=1 OFF_MV=20 ON_MV=3290\n"
            ).encode("ascii")
        )
    )
    timing_frame = wire_frame(
        sequence=0,
        kind=decode_uart.FRAME_TIMING_BLOCK,
        board=2,
        system=2,
        method=2,
    )
    target = FakeLineSerial(
        lambda payload: (
            f"READY {payload.decode('ascii').split()[1]}\n".encode("ascii")
            + timing_frame
        )
    )
    opened: list[str] = []

    def serial_factory(port: str, _baud: int, _timeout: float):
        opened.append(port)
        return controller if port == "COM9" else target

    config = campaign.PowerCycleConfig(
        controller_port="COM9",
        controller_baud=115200,
        channels={"f746": "F746", "h755": "H755"},
        off_ms=2500,
        timeout_s=5.0,
        poll_interval_s=0.1,
        handshake_timeout_s=2.0,
    )
    evidence: dict[str, object] = {}
    clock = FakeClock()

    campaign.capture_after_reset(
        short_manifest,
        {
            "cell_id": "chen_m2sfrk_h755_float32",
            "system": "chen",
            "method": "m2sfrk",
            "board": "h755",
            "representation": "float32",
            "cold_start_id": 1,
        },
        512,
        tmp_path / "reset.log",
        endpoint="benchmark_reset_pilot",
        run_id="primed-run",
        power_cycle_config=config,
        reset_evidence_out=evidence,
        serial_factory=serial_factory,
        snapshot_factory=next_snapshot,
        clock=clock,
        sleep=clock.sleep,
    )

    assert opened.count("COM9") == 1
    assert len(controller.writes) == 2
    assert all(payload.startswith(b"CYCLE ") for payload in controller.writes)
    assert evidence["controller_open_prime"]["required"] is True
    assert (
        evidence["controller_open_prime"]["eligible_as_campaign_repetition"]
        is False
    )


def complete_ina226_stream(
    *,
    active_count: int = 1_000,
    sample_at_fall: bool = False,
) -> bytes:
    frames: list[bytes] = []

    def sample(sequence: int, timestamp_us: int) -> bytes:
        return ina226_frame(
            FRAME_TYPE_SAMPLE,
            flags=SAMPLE_CONVERSION_READY | SAMPLE_I2C_OK,
            sequence=sequence,
            timestamp_us=timestamp_us,
            value_0=4000,
            value_1=1200,
        )

    for sequence in range(128):
        frames.append(sample(sequence, sequence * 2_000))
    frames.extend(
        [
            ina226_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_CLOCK_REFERENCE | EDGE_LEVEL,
                sequence=128,
                timestamp_us=300_000,
            ),
            ina226_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_CLOCK_REFERENCE,
                sequence=128,
                timestamp_us=400_000,
            ),
            ina226_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_LEVEL,
                sequence=128,
                timestamp_us=500_000,
            ),
        ]
    )
    for offset in range(active_count):
        frames.append(
            sample(128 + offset, 500_000 + offset * 2_000)
        )
    fall_sequence = 128 + active_count
    fall_timestamp_us = 500_000 + active_count * 2_000
    frames.append(
        ina226_frame(
            FRAME_TYPE_EDGE,
            flags=0,
            sequence=fall_sequence,
            timestamp_us=fall_timestamp_us,
        )
    )
    next_sequence = fall_sequence
    if sample_at_fall:
        frames.append(sample(next_sequence, fall_timestamp_us))
        next_sequence += 1
    for offset in range(128):
        frames.append(
            sample(
                next_sequence + offset,
                fall_timestamp_us + 2_000 + offset * 2_000,
            )
        )
    terminal_sequence = next_sequence + 128
    frames.append(
        ina226_frame(
            FRAME_TYPE_END,
            flags=END_COMPLETE,
            sequence=terminal_sequence,
            timestamp_us=fall_timestamp_us + 260_000,
        )
    )
    return b"".join(frames)


def test_energy_capture_reuses_controller_serial_and_persists_jsonl(
    tmp_path: Path,
) -> None:
    data, _digest = selected_manifest()
    short_manifest = json.loads(json.dumps(data))
    short_manifest["endpoints"]["benchmark_reset_pilot"][
        "last_sequence"
    ] = 0
    f746_serial = data["boards"]["f746"]["probe_serial"]
    h755_serial = data["boards"]["h755"]["probe_serial"]
    f746 = port_record("COM7", f746_serial, pid=0x374B)
    h755_before = port_record("COM6", h755_serial, pid=0x374F)
    h755_after = port_record("COM11", h755_serial, pid=0x374F)
    snapshots = [
        snapshot(f746, h755_before),
        snapshot(f746),
        snapshot(f746, h755_after),
    ]
    snapshot_index = 0

    def next_snapshot() -> campaign.DeviceSnapshot:
        nonlocal snapshot_index
        selected = snapshots[min(snapshot_index, len(snapshots) - 1)]
        snapshot_index += 1
        return selected

    binary_capture = complete_ina226_stream()

    def controller_response(payload: bytes) -> bytes:
        tokens = payload.decode("ascii").split()
        if tokens[0] == "CYCLE":
            return (
                f"ACK {tokens[1]} H755 OFF_OK=1 ON_OK=1 "
                "OFF_MV=20 ON_MV=3290\n"
            ).encode("ascii")
        if tokens[0] == "ARM":
            return (
                (
                    f"ARMED {tokens[1]} H755 INACAP=1 ADDR=0x41 "
                    "MFG=0x5449 DIE=0x2260 PERIOD_US=2000 "
                    "PRE=128 POST=128 TIMEOUT_MS=60000 "
                    "SHUNT_MOHM=100 SHUNT_MARKING=R100 "
                    "FRAME=INA14/1\n"
                ).encode("ascii")
                + binary_capture
            )
        raise AssertionError(f"comando inesperado: {tokens[0]}")

    controller = FakeLineSerial(controller_response)
    timing_frame = wire_frame(
        sequence=0,
        kind=decode_uart.FRAME_TIMING_BLOCK,
        board=2,
        system=2,
        method=2,
    )
    target = FakeLineSerial(
        lambda payload: (
            f"READY {payload.decode('ascii').split()[1]}\n".encode("ascii")
            + timing_frame
        )
    )
    opened: list[str] = []

    def serial_factory(port: str, _baud: int, _timeout: float):
        opened.append(port)
        if port == "COM9":
            return controller
        if port == "COM11":
            return target
        raise AssertionError(f"puerto inesperado: {port}")

    config = campaign.PowerCycleConfig(
        controller_port="COM9",
        controller_baud=115200,
        channels={"f746": "F746", "h755": "H755"},
        off_ms=2500,
        timeout_s=5.0,
        poll_interval_s=0.1,
        handshake_timeout_s=2.0,
        controller_id="UNO_PWR_TEST",
        ina226_energy_enabled=True,
        ina226_r100_confirmed=True,
    )
    cell = {
        "cell_id": "chen_m2sfrk_h755_float32",
        "system": "chen",
        "method": "m2sfrk",
        "board": "h755",
        "representation": "float32",
        "cold_start_id": 1,
    }
    evidence: dict[str, object] = {}
    energy_path = tmp_path / "energy_capture.raw.jsonl"
    clock = FakeClock()

    raw, frames, _parser, _durations = campaign.capture_after_reset(
        short_manifest,
        cell,
        512,
        tmp_path / "reset.log",
        endpoint="benchmark_reset_pilot",
        run_id="pilot-energy-run",
        power_cycle_config=config,
        reset_evidence_out=evidence,
        serial_factory=serial_factory,
        snapshot_factory=next_snapshot,
        clock=clock,
        sleep=clock.sleep,
        energy_capture_path=energy_path,
    )

    assert opened.count("COM9") == 1
    assert [payload.split(b" ", 1)[0] for payload in controller.writes] == [
        b"CYCLE",
        b"ARM",
    ]
    assert target.writes[0].startswith(b"START ")
    assert raw == timing_frame
    assert [frame[8] for frame in frames] == [0]
    energy = evidence["ina226_energy"]
    assert energy["status"] == "accepted_raw_transport"
    assert energy["quality"]["transport_valid"] is True
    assert energy["quality"]["observed"]["active_sample_count"] == 1_000
    assert energy["quality"]["observed"]["window_duration_s"] == pytest.approx(
        2.0
    )
    assert energy["workload_contract"]["multiplier"] == 512
    assert energy["work_units"] == 5_120_000
    assert energy["controller_id"] == "UNO_PWR_TEST"
    assert energy["firmware_clock_profile"] == "h755_400mhz"
    assert energy["clock_reference_contract"]["core_clock_hz"] == 400_000_000
    assert energy["publication_ready"] is False
    assert energy_path.is_file()
    jsonl = [
        json.loads(line)
        for line in energy_path.read_text(encoding="utf-8").splitlines()
    ]
    assert jsonl[0]["publication_ready"] is False
    assert jsonl[0]["timebase"]["controller_id"] == "UNO_PWR_TEST"
    assert jsonl[0]["clock_reference_contract"] == energy[
        "clock_reference_contract"
    ]
    assert {
        record.get("marker_kind")
        for record in jsonl
        if record["type"] == "edge"
    } == {"energy_window", "clock_reference"}


def test_energy_quality_rejects_clean_but_short_window() -> None:
    decoder = InaFrameDecoder()
    frames = decoder.feed(complete_ina226_stream(active_count=10))
    armed = parse_ina_armed(
        "ARMED short H755 INACAP=1 ADDR=0x41 MFG=0x5449 "
        "DIE=0x2260 PERIOD_US=2000 PRE=128 POST=128 "
        "TIMEOUT_MS=60000 SHUNT_MOHM=100 SHUNT_MARKING=R100 "
        "FRAME=INA14/1"
    )
    stream = InaCaptureStream(
        armed=armed,
        frames=tuple(frames),
        discarded_wire_bytes=decoder.discarded_wire_bytes,
        crc_failures=decoder.crc_failures,
    )
    records = build_ina_records(
        stream,
        capture_id="short",
        run_id="short",
        controller_id="UNO_PWR_TEST",
        firmware_clock_profile="h755_400mhz",
        work_units=10_000,
        work_unit_label="instrumented_solver_step",
        physically_verified_shunt_marking="R100",
    )

    quality = campaign.ina226_capture_quality(stream, records)

    assert quality["flags"]["terminal_complete"] is True
    assert quality["flags"]["wire_crc"] is True
    assert quality["flags"]["window_sample_count"] is False
    assert quality["flags"]["window_duration"] is False
    assert quality["transport_valid"] is False
    assert quality["publication_ready"] is False


def test_sample_in_same_micros_tick_as_fall_remains_in_energy_window() -> None:
    decoder = InaFrameDecoder()
    frames = decoder.feed(complete_ina226_stream(sample_at_fall=True))
    armed = parse_ina_armed(
        "ARMED boundary H755 INACAP=1 ADDR=0x41 MFG=0x5449 "
        "DIE=0x2260 PERIOD_US=2000 PRE=128 POST=128 "
        "TIMEOUT_MS=60000 SHUNT_MOHM=100 SHUNT_MARKING=R100 "
        "FRAME=INA14/1"
    )
    stream = InaCaptureStream(
        armed=armed,
        frames=tuple(frames),
        discarded_wire_bytes=decoder.discarded_wire_bytes,
        crc_failures=decoder.crc_failures,
    )
    records = build_ina_records(
        stream,
        capture_id="boundary",
        run_id="boundary",
        controller_id="UNO_PWR_TEST",
        firmware_clock_profile="h755_400mhz",
        work_units=10_000,
        work_unit_label="instrumented_solver_step",
        physically_verified_shunt_marking="R100",
    )

    quality = campaign.ina226_capture_quality(stream, records)

    assert quality["transport_valid"] is True
    assert quality["observed"]["active_sample_count"] == 1_001
    assert quality["observed"]["post_idle_sample_count"] == 128


def test_clock_reference_must_finish_before_energy_window() -> None:
    decoder = InaFrameDecoder()
    frames = decoder.feed(complete_ina226_stream())
    armed = parse_ina_armed(
        "ARMED marker-order H755 INACAP=1 ADDR=0x41 MFG=0x5449 "
        "DIE=0x2260 PERIOD_US=2000 PRE=128 POST=128 "
        "TIMEOUT_MS=60000 SHUNT_MOHM=100 SHUNT_MARKING=R100 "
        "FRAME=INA14/1"
    )
    stream = InaCaptureStream(
        armed=armed,
        frames=tuple(frames),
        discarded_wire_bytes=decoder.discarded_wire_bytes,
        crc_failures=decoder.crc_failures,
    )
    records = build_ina_records(
        stream,
        capture_id="marker-order",
        run_id="marker-order",
        controller_id="UNO_PWR_TEST",
        firmware_clock_profile="h755_400mhz",
        work_units=10_000,
        work_unit_label="instrumented_solver_step",
        physically_verified_shunt_marking="R100",
    )
    for record in records:
        if record.get("marker_kind") == "clock_reference":
            record["timestamp_ns"] += 1_000_000_000

    quality = campaign.ina226_capture_quality(stream, records)

    assert quality["flags"]["clock_reference_duration"] is True
    assert quality["flags"]["clock_precedes_energy_window"] is False
    assert quality["transport_valid"] is False


def test_power_cycle_dry_run_does_not_claim_power_removed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_f746_float32",
            "--reset-repetition",
            "1",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    assert args.handler(args) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["reset_strategy"] == "external_power_cycle_controller"
    assert dry_run["primary_handshake_build"] is True
    assert dry_run["energy_marker_build"] is True
    assert dry_run["clock_reference_build"] is True
    assert dry_run["runtime_probe_build"] is True
    assert dry_run["power_cycle"]["channels"] == {"f746": "F746"}
    assert dry_run["power_cycle"]["power_removed"] is False
    assert (
        dry_run["power_cycle"]["power_removed_reason"]
        == "dry_run_without_physical_ack"
    )
    assert dry_run["primary_benchmark_ready"] is False
    assert dry_run["first_run_id"].endswith(
        "__benchmark-reset-pilot-external-power-cycle"
    )


def test_ina226_dry_run_freezes_workload_and_distinct_run_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_h755_float32",
            "--reset-repetition",
            "1",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
            "--power-controller-id",
            "UNO_PWR_TEST",
            "--ina226-energy",
            "--confirm-ina226-r100",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    assert args.handler(args) == 0
    dry_run = json.loads(capsys.readouterr().out)

    assert dry_run["ina226_energy_requested"] is True
    assert dry_run["power_cycle"]["ina226_energy"]["publication_ready"] is False
    workload = dry_run["ina226_energy_workloads"][0]
    assert workload["multiplier"] == 512
    assert workload["work_units"] == 5_120_000
    assert workload["sha256"] == campaign.sha256_file(
        campaign.ENERGY_WORKLOAD_CONTRACT_PATH
    )
    assert dry_run["first_run_id"].endswith(
        "__benchmark-reset-pilot-external-power-cycle-ina226-energy"
    )
    assert dry_run["primary_benchmark_ready"] is False


def test_ina226_requires_explicit_r100_confirmation() -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_h755_float32",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
            "--power-controller-id",
            "UNO_PWR_TEST",
            "--ina226-energy",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    with pytest.raises(campaign.CampaignError, match="confirm-ina226-r100"):
        args.handler(args)


def test_ina226_requires_stable_physical_controller_id() -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_h755_float32",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
            "--ina226-energy",
            "--confirm-ina226-r100",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    with pytest.raises(campaign.CampaignError, match="power-controller-id"):
        args.handler(args)


def test_ina226_rejects_com_port_as_controller_identity() -> None:
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "benchmark_reset_pilot",
            "--cell",
            "chen_m2sfrk_h755_float32",
            "--max-runs",
            "1",
            "--power-controller-port",
            "COM9",
            "--power-controller-id",
            "COM9",
            "--ina226-energy",
            "--confirm-ina226-r100",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    with pytest.raises(campaign.CampaignError, match="not a COM port"):
        args.handler(args)


def test_resume_rehashes_ina226_artifact_and_workload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, digest = selected_manifest()
    scheduled = next(
        row
        for row in campaign.generate_schedule(data, digest)
        if row["cell_id"] == "chen_m2sfrk_h755_float32"
        and row["cold_start_id"] == 1
    )
    endpoint = "benchmark_reset_pilot"
    run_directory = campaign.pilot_run_directory(
        tmp_path,
        data["campaign_id"],
        scheduled,
        endpoint,
        external_power_cycle=True,
        ina226_energy=True,
    )
    run_directory.mkdir(parents=True)
    raw = b"fcc1"
    timing = b"timed_index,cycles\n0,321\n"
    controller_id = "UNO_PWR_TEST"
    clock_binding = campaign.campaign_firmware_clock_binding(
        data,
        scheduled["board"],
    )
    energy_raw = (
        json.dumps(
            {
                "type": "header",
                "publication_ready": False,
                "timebase": {"controller_id": controller_id},
                "clock_reference_contract": clock_binding[
                    "clock_reference_contract"
                ],
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    (run_directory / "capture.bin").write_bytes(raw)
    (run_directory / "timing_cycles.csv").write_bytes(timing)
    (run_directory / "energy_capture.raw.jsonl").write_bytes(energy_raw)
    workload = campaign.load_energy_workload_contract(scheduled)
    runtime_probe = {"schema": "test-runtime-probe"}
    result = {
        "schema": "fractional-chaos-physical-run-v1",
        "run_id": campaign.pilot_run_id(
            scheduled,
            endpoint,
            external_power_cycle=True,
            ina226_energy=True,
        ),
        "campaign_id": data["campaign_id"],
        "manifest_sha256": digest,
        "endpoint_name": endpoint,
        "cell": {
            "cell_id": scheduled["cell_id"],
            "cold_start_id": scheduled["cold_start_id"],
        },
        "build": {
            "firmware_clock_profile": clock_binding[
                "firmware_clock_profile"
            ],
            "system_clock_hz": clock_binding["system_clock_hz"],
            "explicit_h755_clock_480_off": clock_binding[
                "explicit_h755_clock_480_off"
            ],
        },
        "transport": {"accepted": True},
        "timing": {"accepted_as_solver_timing": True},
        "hardware": {
            "reset_mode": "external_power_cycle_controller",
            "power_removed": True,
        },
        "reset": {
            "power_removed": True,
            "postboot_handshake": {"completed": True},
        },
        "eligible_as_primary_benchmark": False,
        "eligible_as_paper_cold_start": False,
        "runtime_probe": runtime_probe,
        "ina226_energy": {
            "status": "accepted_raw_transport",
            "quality": {"transport_valid": True},
            "publication_ready": False,
            "eligible_as_primary_benchmark": False,
            "controller_id": controller_id,
            "firmware_clock_profile": clock_binding[
                "firmware_clock_profile"
            ],
            "clock_reference_contract": clock_binding[
                "clock_reference_contract"
            ],
            "raw_jsonl_path": "energy_capture.raw.jsonl",
            "raw_jsonl_sha256": campaign.sha256_bytes(energy_raw),
            "workload_contract": workload,
            "work_units": workload["work_units"],
        },
        "capture": {
            "raw_path": "capture.bin",
            "raw_sha256": campaign.sha256_bytes(raw),
            "timing_cycles_path": "timing_cycles.csv",
            "timing_cycles_sha256": campaign.sha256_bytes(timing),
        },
    }
    (run_directory / "run.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        campaign,
        "load_runtime_probe_artifacts",
        lambda *_args, **_kwargs: runtime_probe,
    )

    accepted = campaign.validate_completed_run(
        tmp_path,
        data,
        digest,
        scheduled,
        endpoint,
        external_power_cycle=True,
        ina226_energy=True,
        controller_id=controller_id,
    )
    assert accepted is not None

    original_energy_contract = dict(
        result["ina226_energy"]["clock_reference_contract"]
    )
    result["ina226_energy"]["clock_reference_contract"][
        "core_clock_hz"
    ] = 480_000_000
    (run_directory / "run.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    with pytest.raises(campaign.CampaignError, match="contrato/calidad"):
        campaign.validate_completed_run(
            tmp_path,
            data,
            digest,
            scheduled,
            endpoint,
            external_power_cycle=True,
            ina226_energy=True,
            controller_id=controller_id,
        )
    result["ina226_energy"][
        "clock_reference_contract"
    ] = original_energy_contract

    bad_header = json.loads(energy_raw.decode("utf-8"))
    bad_header["clock_reference_contract"]["core_clock_hz"] = 480_000_000
    bad_energy_raw = (
        json.dumps(bad_header, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    (run_directory / "energy_capture.raw.jsonl").write_bytes(bad_energy_raw)
    result["ina226_energy"]["raw_jsonl_sha256"] = campaign.sha256_bytes(
        bad_energy_raw
    )
    (run_directory / "run.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    with pytest.raises(campaign.CampaignError, match="captura INA226"):
        campaign.validate_completed_run(
            tmp_path,
            data,
            digest,
            scheduled,
            endpoint,
            external_power_cycle=True,
            ina226_energy=True,
            controller_id=controller_id,
        )

    (run_directory / "energy_capture.raw.jsonl").write_bytes(energy_raw)
    result["ina226_energy"]["raw_jsonl_sha256"] = campaign.sha256_bytes(
        energy_raw
    )
    (run_directory / "run.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    (run_directory / "energy_capture.raw.jsonl").write_bytes(b"tampered")
    with pytest.raises(campaign.CampaignError, match="hash INA226"):
        campaign.validate_completed_run(
            tmp_path,
            data,
            digest,
            scheduled,
            endpoint,
            external_power_cycle=True,
            ina226_energy=True,
            controller_id=controller_id,
        )


def test_external_controller_never_bypasses_primary_guard() -> None:
    data, _digest = selected_manifest()
    parser = campaign.make_parser()
    args = parser.parse_args(
        [
            "run",
            "--endpoint",
            "primary_benchmark",
            "--cell",
            "chen_m2sfrk_f746_float32",
            "--reset-repetition",
            "1",
            "--max-runs",
            "1",
            "--execute",
            "--confirm-campaign-id",
            data["campaign_id"],
            "--allow-pilot-only",
            "--power-controller-port",
            "COM9",
        ]
    )
    args.manifest = args.manifest.resolve()
    args.output_root = args.output_root.resolve()

    with pytest.raises(
        campaign.CampaignError,
        match="primary_benchmark bloqueado",
    ):
        args.handler(args)
