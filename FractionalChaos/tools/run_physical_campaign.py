#!/usr/bin/env python3
"""Planifica y ejecuta de forma segura la campaña física STM32.

Cada cohorte admitida contiene 36 celdas y 30 repeticiones por celda. La
ejecución primaria se mantiene cerrada hasta que un contrato y preflight
físicos autoricen el ciclo de alimentación externo. Los modos ejecutables son
pilotos claramente etiquetados; nunca se promueven a evidencia primaria.
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
import secrets
import shutil
import socket
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "validation" / "physical_campaign_selected_v1.json"
)
DEFAULT_OUTPUT_ROOT = ROOT / "validation" / "results" / "physical_campaign"
ENERGY_WORKLOAD_CONTRACT_PATH = (
    ROOT / "validation" / "energy_workload_contract_v1.json"
)
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(
    0,
    str(ROOT / "hardware" / "power_cycle_controller_uno"),
)

import decode_uart  # noqa: E402
from host.ina226_capture import (  # noqa: E402
    EDGE_CLOCK_REFERENCE,
    EDGE_LEVEL,
    END_COMPLETE,
    END_EDGE_OVERFLOW,
    END_I2C_ERROR,
    END_STOPPED,
    END_TIMEOUT,
    END_TIMING_OR_EDGE_ANOMALY,
    FROZEN_FIRMWARE_CLOCK_PROFILES,
    CaptureStream as Ina226CaptureStream,
    Ina226Session,
    InaCaptureError,
    build_capture_records as build_ina226_capture_records,
    validate_controller_id as validate_ina226_controller_id,
)


COHORT_CONTRACTS: dict[frozenset[str], dict[str, dict[str, object]]] = {
    frozenset(("lorenz", "rossler", "chen")): {
        "lorenz": {
            "wire_id": 0,
            "manifest_id": "lorenz_caputo_v1",
            "transient_steps": 2000,
        },
        "rossler": {
            "wire_id": 1,
            "manifest_id": "rossler_caputo_v1",
            "transient_steps": 5000,
        },
        "chen": {
            "wire_id": 2,
            "manifest_id": "chen_caputo_v1",
            "transient_steps": 2000,
        },
    },
    frozenset(("chen", "liu", "hammouch_mekkaoui")): {
        "chen": {
            "wire_id": 2,
            "manifest_id": "chen_caputo_v1",
            "transient_steps": 2000,
        },
        "liu": {
            "wire_id": 3,
            "manifest_id": "liu_caputo_v1",
            "transient_steps": 1000,
        },
        "hammouch_mekkaoui": {
            "wire_id": 4,
            "manifest_id": "hammouch_mekkaoui_caputo_v1",
            "transient_steps": 1000,
        },
    },
}
SELECTED_SYSTEMS = frozenset(("chen", "liu", "hammouch_mekkaoui"))
APPROVED_CAMPAIGN_CONTRACT_OVERRIDES: dict[str, dict[str, Any]] = {
    "stm32_dense_rossler_classic_q09877_m2sfrk_4cells_v1": {
        "systems": frozenset(("lorenz", "rossler", "chen")),
        "derivation": {
            "source_manifest": "physical_campaign_manifest.json",
            "paper_matrix_inherited_for_validator": True,
            "dense_scope_is_not_transport_calibration": True,
            "dense_scope_is_not_primary_benchmark": True,
        },
        "system_contracts": {
            "rossler": {
                "wire_id": 1,
                "manifest_id": "rossler_classic_caputo_v2",
                "parameters": [0.2, 0.2, 5.7],
                "q": 0.9877,
                "dt_s": 0.01,
                "memory_s": 10,
                "memory_increments": 1000,
                "history_states": 1001,
                "initial_state_words_hex": [
                    "0x3F800000",
                    "0x00000000",
                    "0x00000000",
                ],
                "transient_steps": 5000,
                "observation_steps": 20000,
            }
        },
    }
}


class CampaignError(RuntimeError):
    """Error de contrato o seguridad de la campaña."""


POWER_CYCLE_PROTOCOL = "fc-power-cycle-v1"
POSTBOOT_HANDSHAKE_PROTOCOL = "fcc1-host-start-v1"
POWER_CHANNELS = {"f746": "F746", "h755": "H755"}
POWER_RELAYS = {"F746": 1, "H755": 2}
CAMPAIGN_FIRMWARE_CLOCK_PROFILES = {
    "f746": "f746_216mhz",
    "h755": "h755_400mhz",
}
POWER_OFF_CONFIRM_TIMEOUT_S = 1.8
POWER_ON_CONFIRM_TIMEOUT_S = 10.0
POWER_HOST_WATCHDOG_MARGIN_S = 1.0
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
POWER_ACK_PATTERN = re.compile(
    r"^ACK (?P<request_id>[A-Za-z0-9_-]{1,64}) "
    r"(?P<channel>F746|H755|ALL) "
    r"OFF_OK=(?P<off_ok>[01]) "
    r"ON_OK=(?P<on_ok>[01]) "
    r"OFF_MV=(?P<off_mv>[0-9]{1,6}) "
    r"ON_MV=(?P<on_mv>[0-9]{1,6})$"
)


def campaign_firmware_clock_binding(
    manifest: Mapping[str, Any],
    board_name: str,
) -> dict[str, Any]:
    """Bind one manifest board to the frozen host/firmware clock profile."""

    try:
        profile_name = CAMPAIGN_FIRMWARE_CLOCK_PROFILES[board_name]
        profile = FROZEN_FIRMWARE_CLOCK_PROFILES[profile_name]
        system_clock_hz = int(
            manifest["boards"][board_name]["system_clock_hz"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignError(
            f"no existe un perfil de reloj congelado para {board_name}"
        ) from exc
    if (
        profile.get("board") != board_name
        or profile.get("channel") != POWER_CHANNELS.get(board_name)
        or profile.get("core_clock_hz") != system_clock_hz
    ):
        raise CampaignError(
            "el reloj del manifiesto no coincide con el perfil de campaña "
            f"{profile_name}: board={board_name}, "
            f"system_clock_hz={system_clock_hz}"
        )
    return {
        "firmware_clock_profile": profile_name,
        "system_clock_hz": system_clock_hz,
        "explicit_h755_clock_480_off": board_name == "h755",
        "clock_reference_contract": {
            "source": "host_selected_frozen_campaign_profile",
            "profile_is_measurement": False,
            "profile": profile_name,
            "board": profile["board"],
            "core_clock_hz": profile["core_clock_hz"],
            "expected_pulse_s": profile["expected_pulse_s"],
            "expected_cycles": profile["expected_cycles"],
        },
    }


def normalize_probe_serial(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()


def normalize_port_name(value: object) -> str:
    return str(value).strip().casefold()


@dataclass(frozen=True)
class SerialPortRecord:
    """Identidad observable de un puerto serie USB."""

    device: str
    serial_number: str | None
    vid: int | None = None
    pid: int | None = None
    hwid: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "serial_number": self.serial_number,
            "vid": self.vid,
            "pid": self.pid,
            "hwid": self.hwid,
        }


@dataclass(frozen=True)
class DeviceSnapshot:
    """Foto de puertos COM y seriales USB asociados a las sondas."""

    ports: tuple[SerialPortRecord, ...]
    probe_serials: frozenset[str]
    backend: str

    def port_for_probe(self, probe_serial: str) -> SerialPortRecord | None:
        expected = normalize_probe_serial(probe_serial)
        matches = [
            record
            for record in self.ports
            if record.serial_number is not None
            and normalize_probe_serial(record.serial_number) == expected
        ]
        if len(matches) > 1:
            raise CampaignError(
                "la enumeración devolvió varios COM para la misma sonda "
                f"{probe_serial}: {[record.device for record in matches]}"
            )
        return matches[0] if matches else None

    def probe_present(self, probe_serial: str) -> bool:
        expected = normalize_probe_serial(probe_serial)
        return expected in self.probe_serials


class PySerialDeviceEnumerator:
    """Enumera COM por pyserial y conserva VID/PID/serial USB.

    El serial USB del VCP de las Nucleo es el serial de la sonda ST-LINK. Un
    ``probe_lister`` independiente puede añadirse sin cambiar el estado de
    adquisición; las pruebas usan esta interfaz para modelar ambos recursos
    por separado.
    """

    def __init__(
        self,
        *,
        comports: Callable[[], Iterable[object]] | None = None,
        probe_lister: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self._comports = comports
        self._probe_lister = probe_lister

    def snapshot(self) -> DeviceSnapshot:
        if self._comports is None:
            try:
                from serial.tools import list_ports  # type: ignore
            except ImportError as exc:
                raise CampaignError(
                    "pyserial es obligatorio para enumerar las placas"
                ) from exc
            comports = list_ports.comports
        else:
            comports = self._comports

        records: list[SerialPortRecord] = []
        derived_probes: set[str] = set()
        for info in comports():
            hwid = str(getattr(info, "hwid", "") or "")
            serial_number = getattr(info, "serial_number", None)
            if not serial_number:
                match = re.search(r"(?:^|\s)SER=([^\s]+)", hwid)
                serial_number = match.group(1) if match else None
            normalized_serial = (
                normalize_probe_serial(serial_number)
                if serial_number
                else None
            )
            if normalized_serial:
                derived_probes.add(normalized_serial)
            records.append(
                SerialPortRecord(
                    device=str(getattr(info, "device", "")),
                    serial_number=normalized_serial,
                    vid=getattr(info, "vid", None),
                    pid=getattr(info, "pid", None),
                    hwid=hwid,
                )
            )

        probes = set(derived_probes)
        backend = "pyserial_list_ports_usb_serial"
        if self._probe_lister is not None:
            probes.update(
                normalize_probe_serial(value)
                for value in self._probe_lister()
            )
            backend += "+independent_probe_lister"
        return DeviceSnapshot(
            ports=tuple(records),
            probe_serials=frozenset(probes),
            backend=backend,
        )


@dataclass(frozen=True)
class PowerCycleConfig:
    controller_port: str
    controller_baud: int
    channels: Mapping[str, str]
    off_ms: int
    timeout_s: float
    poll_interval_s: float
    handshake_timeout_s: float
    port_open_timeout_s: float = 10.0
    off_threshold_mv: int = 200
    on_threshold_mv: int = 3100
    controller_id: str | None = None
    ina226_energy_enabled: bool = False
    ina226_r100_confirmed: bool = False
    ina226_pre_samples: int = 128
    ina226_post_samples: int = 128
    ina226_timeout_ms: int = 60_000

    def channel_for(self, board: str) -> str:
        try:
            channel = str(self.channels[board]).upper()
        except (KeyError, TypeError, ValueError) as exc:
            raise CampaignError(
                f"no hay canal de alimentación para {board}"
            ) from exc
        expected = POWER_CHANNELS.get(board)
        if channel != expected:
            raise CampaignError(
                f"el canal de {board} debe ser {expected}, no {channel}"
            )
        return channel

    def dry_run_metadata(
        self,
        selected_boards: Iterable[str],
    ) -> dict[str, Any]:
        return {
            "protocol": POWER_CYCLE_PROTOCOL,
            "controller_port": self.controller_port,
            "controller_baud": self.controller_baud,
            "controller_id": self.controller_id,
            "channels": {
                board: self.channel_for(board)
                for board in sorted(set(selected_boards))
            },
            "off_ms": self.off_ms,
            "timeout_s": self.timeout_s,
            "poll_interval_s": self.poll_interval_s,
            "off_threshold_mv": self.off_threshold_mv,
            "on_threshold_mv": self.on_threshold_mv,
            "postboot_handshake": POSTBOOT_HANDSHAKE_PROTOCOL,
            "postboot_handshake_timeout_s": self.handshake_timeout_s,
            "reenumerated_port_open_timeout_s": self.port_open_timeout_s,
            "power_removed": False,
            "power_removed_reason": "dry_run_without_physical_ack",
            "ina226_energy": {
                "requested": self.ina226_energy_enabled,
                "status": "planned_unmeasured",
                "publication_ready": False,
                "eligible_as_primary_benchmark": False,
                "r100_physically_confirmed_for_future_run": (
                    self.ina226_r100_confirmed
                ),
                "sample_rate_hz": 500,
                "pre_samples": self.ina226_pre_samples,
                "post_samples": self.ina226_post_samples,
                "timeout_ms": self.ina226_timeout_ms,
                "marker_kinds": {
                    "energy_window": "PE0_D34_to_UNO_D2_or_D3",
                    "clock_reference": "PA0_D32_to_UNO_D4_or_D5",
                },
            },
        }


@dataclass(frozen=True)
class PowerCycleAck:
    request_id: str
    channel: str
    off_ok: bool
    on_ok: bool
    off_mv: int
    on_mv: int
    raw_line: str

    def as_metadata(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "channel": self.channel,
            "off_ok": self.off_ok,
            "on_ok": self.on_ok,
            "off_mv": self.off_mv,
            "on_mv": self.on_mv,
            "raw_line": self.raw_line,
        }


@dataclass(frozen=True)
class PowerCycleResult:
    reopened_port: SerialPortRecord
    request_id: str
    evidence: dict[str, Any]


SerialFactory = Callable[[str, int, float], Any]
SnapshotFactory = Callable[[], DeviceSnapshot]


def default_serial_factory(port: str, baud: int, timeout: float) -> Any:
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise CampaignError(
            "pyserial es obligatorio para ejecutar una adquisición física"
        ) from exc
    return serial.Serial(port, baud, timeout=timeout)


def power_cycle_config_from_args(
    args: argparse.Namespace,
) -> PowerCycleConfig | None:
    controller_port = getattr(args, "power_controller_port", None)
    controller_id_arg = getattr(args, "power_controller_id", None)
    if not controller_port:
        if bool(getattr(args, "ina226_energy", False)) or bool(
            getattr(args, "confirm_ina226_r100", False)
        ) or controller_id_arg:
            raise CampaignError(
                "las opciones INA226/controlador requieren "
                "--power-controller-port"
            )
        return None
    config = PowerCycleConfig(
        controller_port=str(controller_port),
        controller_baud=int(
            getattr(args, "power_controller_baud", 115200)
        ),
        channels=dict(POWER_CHANNELS),
        off_ms=int(getattr(args, "power_off_ms", 2500)),
        timeout_s=float(getattr(args, "power_cycle_timeout", 30.0)),
        poll_interval_s=float(
            getattr(args, "power_cycle_poll_interval", 0.1)
        ),
        handshake_timeout_s=float(
            getattr(args, "postboot_handshake_timeout", 15.0)
        ),
        port_open_timeout_s=float(
            getattr(args, "reenumerated_port_open_timeout", 10.0)
        ),
        controller_id=(
            str(controller_id_arg).strip()
            if controller_id_arg is not None
            else None
        ),
        ina226_energy_enabled=bool(
            getattr(args, "ina226_energy", False)
        ),
        ina226_r100_confirmed=bool(
            getattr(args, "confirm_ina226_r100", False)
        ),
        ina226_pre_samples=int(
            getattr(args, "ina226_pre_samples", 128)
        ),
        ina226_post_samples=int(
            getattr(args, "ina226_post_samples", 128)
        ),
        ina226_timeout_ms=int(
            getattr(args, "ina226_timeout_ms", 60_000)
        ),
    )
    if config.controller_baud <= 0:
        raise CampaignError("--power-controller-baud debe ser positivo")
    if config.controller_id is not None:
        try:
            validate_ina226_controller_id(config.controller_id)
        except InaCaptureError as exc:
            raise CampaignError(f"--power-controller-id inválido: {exc}") from exc
    if not 2000 <= config.off_ms <= 30_000:
        raise CampaignError("--power-off-ms debe estar entre 2000 y 30000")
    minimum_cycle_timeout_s = (
        config.off_ms / 1000.0
        + POWER_OFF_CONFIRM_TIMEOUT_S
        + POWER_ON_CONFIRM_TIMEOUT_S
        + POWER_HOST_WATCHDOG_MARGIN_S
    )
    if config.timeout_s <= minimum_cycle_timeout_s:
        raise CampaignError(
            "--power-cycle-timeout debe ser mayor que "
            f"{minimum_cycle_timeout_s:.3f}s para cubrir off_ms, los "
            "watchdogs OFF/ON del firmware y 1s de margen host"
        )
    if not 0.01 <= config.poll_interval_s <= 1.0:
        raise CampaignError(
            "--power-cycle-poll-interval debe estar entre 0.01 y 1.0"
        )
    if config.handshake_timeout_s <= 0:
        raise CampaignError(
            "--postboot-handshake-timeout debe ser positivo"
        )
    if config.port_open_timeout_s <= 0:
        raise CampaignError(
            "--reenumerated-port-open-timeout debe ser positivo"
        )
    if config.ina226_energy_enabled:
        if config.controller_id is None:
            raise CampaignError(
                "--ina226-energy exige --power-controller-id con la etiqueta "
                "física estable del UNO (por ejemplo UNO_PWR_01)"
            )
        if config.controller_baud != 115_200:
            raise CampaignError(
                "--ina226-energy exige --power-controller-baud 115200"
            )
        if not config.ina226_r100_confirmed:
            raise CampaignError(
                "--ina226-energy exige --confirm-ina226-r100 después de "
                "inspeccionar físicamente el shunt"
            )
        if not 100 <= config.ina226_pre_samples <= 1_000:
            raise CampaignError(
                "--ina226-pre-samples debe estar entre 100 y 1000"
            )
        if not 100 <= config.ina226_post_samples <= 1_000:
            raise CampaignError(
                "--ina226-post-samples debe estar entre 100 y 1000"
            )
        if not 1_000 <= config.ina226_timeout_ms <= 60_000:
            raise CampaignError(
                "--ina226-timeout-ms debe estar entre 1000 y 60000"
            )
    elif config.ina226_r100_confirmed:
        raise CampaignError(
            "--confirm-ina226-r100 sólo es válido con --ina226-energy"
        )
    return config


def validate_power_cycle_config(
    config: PowerCycleConfig,
    manifest: dict[str, Any],
) -> None:
    board_ports = {
        normalize_port_name(board["port"])
        for board in manifest["boards"].values()
    }
    if normalize_port_name(config.controller_port) in board_ports:
        raise CampaignError(
            "el puerto del controlador no puede ser el COM de una Nucleo"
        )
    channels = {
        board: config.channel_for(board)
        for board in manifest["paper_matrix"]["boards"]
    }
    if len(set(channels.values())) != len(channels):
        raise CampaignError("los dos boards deben usar canales distintos")


def parse_power_cycle_ack(
    line: str,
    *,
    request_id: str,
    channel: str,
    off_threshold_mv: int,
    on_threshold_mv: int,
) -> PowerCycleAck:
    match = POWER_ACK_PATTERN.fullmatch(line.strip())
    if match is None:
        raise CampaignError(f"ACK de power-cycle malformado: {line!r}")
    ack = PowerCycleAck(
        request_id=match.group("request_id"),
        channel=match.group("channel"),
        off_ok=match.group("off_ok") == "1",
        on_ok=match.group("on_ok") == "1",
        off_mv=int(match.group("off_mv")),
        on_mv=int(match.group("on_mv")),
        raw_line=line.strip(),
    )
    if ack.request_id != request_id or ack.channel != channel:
        raise CampaignError(
            "ACK de power-cycle no corresponde a la solicitud activa"
        )
    if (
        not ack.off_ok
        or not ack.on_ok
        or ack.off_mv > off_threshold_mv
        or ack.on_mv < on_threshold_mv
    ):
        raise CampaignError(
            "ACK físico rechazado: "
            f"OFF_OK={int(ack.off_ok)} OFF_MV={ack.off_mv}, "
            f"ON_OK={int(ack.on_ok)} ON_MV={ack.on_mv}"
        )
    return ack


def _serial_read(port: Any, maximum: int = 4096) -> bytes:
    waiting = int(getattr(port, "in_waiting", 0) or 0)
    return bytes(port.read(min(max(waiting, 1), maximum)))


def _configure_open_serial(port: Any) -> None:
    try:
        port.set_buffer_size(rx_size=1024 * 1024)
    except (AttributeError, OSError):
        pass
    port.reset_input_buffer()


def perform_external_power_cycle(
    *,
    board_name: str,
    board: Mapping[str, Any],
    run_id: str,
    config: PowerCycleConfig,
    serial_factory: SerialFactory = default_serial_factory,
    snapshot_factory: SnapshotFactory | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    request_id: str | None = None,
    controller_serial: Any | None = None,
    initial_snapshot: DeviceSnapshot | None = None,
) -> PowerCycleResult:
    """Ejecuta y demuestra un ciclo sin promoverlo a evidencia primaria."""

    if snapshot_factory is None:
        enumerator = PySerialDeviceEnumerator()
        snapshot_factory = enumerator.snapshot
    probe_serial = normalize_probe_serial(board["probe_serial"])
    initial = initial_snapshot or snapshot_factory()
    before = initial.port_for_probe(probe_serial)
    if before is None or not initial.probe_present(probe_serial):
        raise CampaignError(
            "no se observó el COM y serial ST-LINK esperados antes del ciclo "
            f"({probe_serial})"
        )
    if before.vid is None or before.pid is None:
        raise CampaignError(
            "la enumeración inicial no expuso VID/PID; no se inicia el ciclo"
        )

    cycle_request_id = request_id or secrets.token_hex(8)
    if REQUEST_ID_PATTERN.fullmatch(cycle_request_id) is None:
        raise CampaignError("request_id de power-cycle no es válido")
    channel = config.channel_for(board_name)
    command = (
        f"CYCLE {cycle_request_id} {channel} {config.off_ms}\n"
    ).encode("ascii")
    controller_lines: list[str] = []
    controller_buffer = bytearray()
    ack: PowerCycleAck | None = None
    com_disappeared_at: float | None = None
    probe_disappeared_at: float | None = None
    com_reappeared_at: float | None = None
    probe_reappeared_at: float | None = None
    after: SerialPortRecord | None = None
    started = clock()
    deadline = started + config.timeout_s

    controller_context = (
        contextlib.nullcontext(controller_serial)
        if controller_serial is not None
        else serial_factory(
            config.controller_port,
            config.controller_baud,
            min(config.poll_interval_s, 0.1),
        )
    )
    with controller_context as controller:
        if controller is None:
            raise CampaignError("serial del controlador no disponible")
        _configure_open_serial(controller)
        written = controller.write(command)
        if written is not None and int(written) != len(command):
            raise CampaignError(
                "el controlador no aceptó la solicitud CYCLE completa"
            )
        flush = getattr(controller, "flush", None)
        if callable(flush):
            flush()

        while True:
            chunk = _serial_read(controller, maximum=1024)
            if chunk:
                controller_buffer.extend(chunk)
                if len(controller_buffer) > 4096:
                    raise CampaignError(
                        "respuesta del controlador excede 4096 bytes"
                    )
                while b"\n" in controller_buffer:
                    raw_line, _, remainder = controller_buffer.partition(b"\n")
                    controller_buffer[:] = remainder
                    line = raw_line.rstrip(b"\r").decode(
                        "ascii", errors="strict"
                    )
                    controller_lines.append(line)
                    if line.startswith(
                        f"ERR {cycle_request_id} {channel} "
                    ):
                        raise CampaignError(
                            f"el controlador rechazó CYCLE: {line}"
                        )
                    if line.startswith("ACK "):
                        ack = parse_power_cycle_ack(
                            line,
                            request_id=cycle_request_id,
                            channel=channel,
                            off_threshold_mv=config.off_threshold_mv,
                            on_threshold_mv=config.on_threshold_mv,
                        )

            snapshot = snapshot_factory()
            now = clock()
            current = snapshot.port_for_probe(probe_serial)
            probe_present = snapshot.probe_present(probe_serial)
            if current is None and com_disappeared_at is None:
                com_disappeared_at = now
            if not probe_present and probe_disappeared_at is None:
                probe_disappeared_at = now
            if (
                com_disappeared_at is not None
                and current is not None
                and com_reappeared_at is None
            ):
                if current.vid != before.vid or current.pid != before.pid:
                    raise CampaignError(
                        "el COM reapareció con VID/PID distinto del inicial"
                    )
                com_reappeared_at = now
                after = current
            if (
                probe_disappeared_at is not None
                and probe_present
                and probe_reappeared_at is None
            ):
                probe_reappeared_at = now

            if (
                ack is not None
                and com_disappeared_at is not None
                and probe_disappeared_at is not None
                and com_reappeared_at is not None
                and probe_reappeared_at is not None
                and after is not None
            ):
                break
            if now >= deadline:
                missing = []
                if ack is None:
                    missing.append("ACK físico válido")
                if com_disappeared_at is None:
                    missing.append("desaparición de COM")
                if probe_disappeared_at is None:
                    missing.append("desaparición de ST-LINK")
                if com_reappeared_at is None:
                    missing.append("reaparición de COM")
                if probe_reappeared_at is None:
                    missing.append("reaparición de ST-LINK")
                raise CampaignError(
                    "timeout de power-cycle; faltó " + ", ".join(missing)
                )
            sleep(config.poll_interval_s)

    finished = clock()
    assert ack is not None
    assert after is not None
    evidence = {
        "mode": "external_power_cycle_controller",
        "protocol": POWER_CYCLE_PROTOCOL,
        "preparation": (
            "program_verify_halt_then_controller_power_cycle_then_"
            "usb_reenumeration_and_postboot_handshake"
        ),
        "requested_run_id": run_id,
        "request_id": cycle_request_id,
        "power_removed": True,
        "power_removed_basis": (
            "validated_controller_ack_plus_stlink_vcp_identity_"
            "disappearance_and_reappearance"
        ),
        "eligible_as_paper_cold_start": False,
        "eligible_as_primary_benchmark": False,
        "controller": {
            "controller_id": config.controller_id,
            "port": config.controller_port,
            "baud": config.controller_baud,
            "channel": channel,
            "relay": POWER_RELAYS[channel],
            "off_ms": config.off_ms,
            "command": command.decode("ascii").rstrip(),
            "lines": controller_lines,
            "ack": ack.as_metadata(),
            "off_threshold_mv": config.off_threshold_mv,
            "on_threshold_mv": config.on_threshold_mv,
        },
        "usb_reenumeration": {
            "backend": initial.backend,
            "probe_serial": probe_serial,
            "probe_identity_source": (
                "stlink_vcp_usb_serial_and_vid_pid"
            ),
            "independent_probe_lister_used": (
                "independent_probe_lister" in initial.backend
            ),
            "com_disappeared": True,
            "probe_disappeared": True,
            "com_reappeared": True,
            "probe_reappeared": True,
            "port_before": before.as_metadata(),
            "port_after": after.as_metadata(),
            "port_changed": (
                normalize_port_name(before.device)
                != normalize_port_name(after.device)
            ),
            "com_disappeared_after_s": com_disappeared_at - started,
            "probe_disappeared_after_s": probe_disappeared_at - started,
            "com_reappeared_after_s": com_reappeared_at - started,
            "probe_reappeared_after_s": probe_reappeared_at - started,
        },
        "cycle_wall_time_s": finished - started,
    }
    return PowerCycleResult(
        reopened_port=after,
        request_id=cycle_request_id,
        evidence=evidence,
    )


def ensure_target_powered_after_controller_open(
    *,
    controller: Any,
    board_name: str,
    board: Mapping[str, Any],
    config: PowerCycleConfig,
    snapshot_factory: SnapshotFactory,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[dict[str, Any], DeviceSnapshot]:
    """Restore a target cut by the classic UNO's DTR reset.

    The controller's reset-safe state opens both NO relay contacts.  If opening
    its COM made the selected board disappear, an explicitly non-campaign
    priming cycle restores it before the measured cycle begins.
    """

    probe_serial = normalize_probe_serial(board["probe_serial"])
    observed = snapshot_factory()
    port = observed.port_for_probe(probe_serial)
    if port is not None and observed.probe_present(probe_serial):
        return (
            {
                "required": False,
                "reason": "target_present_after_controller_open",
                "eligible_as_campaign_repetition": False,
            },
            observed,
        )

    request_id = f"prime-{secrets.token_hex(6)}"
    channel = config.channel_for(board_name)
    command = (
        f"CYCLE {request_id} {channel} {config.off_ms}\n"
    ).encode("ascii")
    controller.reset_input_buffer()
    written = controller.write(command)
    if written is not None and int(written) != len(command):
        raise CampaignError(
            "el controlador no aceptó el CYCLE de cebado completo"
        )
    flush = getattr(controller, "flush", None)
    if callable(flush):
        flush()

    buffer = bytearray()
    lines: list[str] = []
    ack: PowerCycleAck | None = None
    restored_port: SerialPortRecord | None = None
    started = clock()
    deadline = started + config.timeout_s
    while True:
        chunk = _serial_read(controller, maximum=1024)
        if chunk:
            buffer.extend(chunk)
            if len(buffer) > 4096:
                raise CampaignError(
                    "respuesta de cebado excede 4096 bytes"
                )
            while b"\n" in buffer:
                raw_line, _, remainder = buffer.partition(b"\n")
                buffer[:] = remainder
                line = raw_line.rstrip(b"\r").decode(
                    "ascii",
                    errors="strict",
                )
                lines.append(line)
                if line.startswith(f"ERR {request_id} {channel} "):
                    raise CampaignError(
                        f"el controlador rechazó el cebado: {line}"
                    )
                if line.startswith("ACK "):
                    ack = parse_power_cycle_ack(
                        line,
                        request_id=request_id,
                        channel=channel,
                        off_threshold_mv=config.off_threshold_mv,
                        on_threshold_mv=config.on_threshold_mv,
                    )
        current_snapshot = snapshot_factory()
        current = current_snapshot.port_for_probe(probe_serial)
        if (
            current is not None
            and current_snapshot.probe_present(probe_serial)
        ):
            restored_port = current
        now = clock()
        if ack is not None and restored_port is not None:
            return (
                {
                    "required": True,
                    "reason": "classic_uno_open_may_reset_to_fail_off",
                    "request_id": request_id,
                    "channel": channel,
                    "ack": ack.as_metadata(),
                    "restored_port": restored_port.as_metadata(),
                    "controller_lines": lines,
                    "wall_time_s": now - started,
                    "eligible_as_campaign_repetition": False,
                },
                current_snapshot,
            )
        if now >= deadline:
            raise CampaignError(
                "no se pudo restaurar la placa después de abrir el COM "
                "del controlador"
            )
        sleep(config.poll_interval_s)


def open_reenumerated_serial(
    *,
    cycle: PowerCycleResult,
    board: Mapping[str, Any],
    config: PowerCycleConfig,
    serial_factory: SerialFactory,
    snapshot_factory: SnapshotFactory,
    serial_read_timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any, SerialPortRecord, dict[str, Any]]:
    """Abre el VCP renumerado con reintentos acotados por identidad USB."""

    probe_serial = normalize_probe_serial(board["probe_serial"])
    reference = cycle.reopened_port
    current: SerialPortRecord | None = reference
    attempts = 0
    errors: list[str] = []
    started = clock()
    deadline = started + config.port_open_timeout_s
    while True:
        if current is not None:
            if current.vid != reference.vid or current.pid != reference.pid:
                raise CampaignError(
                    "el candidato COM cambió VID/PID antes de abrirse"
                )
            attempts += 1
            candidate_port: Any | None = None
            try:
                candidate_port = serial_factory(
                    current.device,
                    int(board["baud"]),
                    serial_read_timeout_s,
                )
                _configure_open_serial(candidate_port)
                finished = clock()
                return candidate_port, current, {
                    "opened": True,
                    "attempts": attempts,
                    "port": current.as_metadata(),
                    "wall_time_s": finished - started,
                    "transient_errors": errors,
                }
            except CampaignError:
                raise
            except Exception as exc:
                if candidate_port is not None:
                    with contextlib.suppress(Exception):
                        candidate_port.close()
                errors.append(f"{type(exc).__name__}: {exc}")
                if len(errors) > 8:
                    errors = errors[-8:]

        now = clock()
        if now >= deadline:
            detail = errors[-1] if errors else "COM aún no enumerable"
            raise CampaignError(
                "no se pudo abrir el COM renumerado por identidad ST-LINK "
                f"en {config.port_open_timeout_s}s: {detail}"
            )
        sleep(config.poll_interval_s)
        snapshot = snapshot_factory()
        candidate = snapshot.port_for_probe(probe_serial)
        current = (
            candidate
            if candidate is not None
            and snapshot.probe_present(probe_serial)
            else None
        )


def perform_postboot_handshake(
    port: Any,
    *,
    request_id: str,
    identity: tuple[int, int, int, int],
    timeout_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bytes, dict[str, Any]]:
    """Libera el firmware sólo después de reabrir el COM.

    El firmware compatible debe esperar ``START`` y contestar ``READY`` antes
    de emitir FCC1. Encontrar un sync FCC1 antes de READY invalida el intento.
    """

    if REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        raise CampaignError("request_id de handshake no es válido")
    command_line = (
        f"START {request_id} {identity[0]} {identity[1]} "
        f"{identity[2]} {identity[3]}"
    )
    command = (command_line + "\n").encode("ascii")
    written = port.write(command)
    if written is not None and int(written) != len(command):
        raise CampaignError("el puerto STM32 no aceptó START completo")
    flush = getattr(port, "flush", None)
    if callable(flush):
        flush()

    expected_ready = f"READY {request_id}"
    buffer = bytearray()
    raw_response = bytearray()
    ignored_lines: list[str] = []
    started = clock()
    deadline = started + timeout_s
    while True:
        chunk = _serial_read(port)
        if chunk:
            buffer.extend(chunk)
            raw_response.extend(chunk)
            if len(buffer) > 4096:
                raise CampaignError(
                    "handshake postboot excedió 4096 bytes sin READY"
                )
            while True:
                newline = buffer.find(b"\n")
                sync = buffer.find(decode_uart.SYNC)
                if sync >= 0 and (newline < 0 or sync < newline):
                    raise CampaignError(
                        "se recibió FCC1 antes de READY; el firmware no "
                        "esperó la reapertura del COM"
                    )
                if newline < 0:
                    break
                raw_line = bytes(buffer[:newline]).rstrip(b"\r")
                del buffer[: newline + 1]
                try:
                    line = raw_line.decode("ascii", errors="strict")
                except UnicodeDecodeError as exc:
                    raise CampaignError(
                        "respuesta postboot no es ASCII antes de READY"
                    ) from exc
                if line == expected_ready:
                    finished = clock()
                    ready_response_length = (
                        len(raw_response) - len(buffer)
                    )
                    return bytes(buffer), {
                        "protocol": POSTBOOT_HANDSHAKE_PROTOCOL,
                        "request_id": request_id,
                        "command": command_line,
                        "response": line,
                        "ready_after_com_open": True,
                        "fcc1_before_ready": False,
                        "ignored_lines": ignored_lines,
                        "response_bytes": ready_response_length,
                        "post_ready_buffered_bytes": len(buffer),
                        "response_sha256": sha256_bytes(
                            bytes(raw_response[:ready_response_length])
                        ),
                        "wall_time_s": finished - started,
                    }
                if line.startswith("ERROR "):
                    raise CampaignError(
                        f"el firmware rechazó START: {line}"
                    )
                ignored_lines.append(line)
                if len(ignored_lines) > 8:
                    raise CampaignError(
                        "demasiadas líneas antes de READY postboot"
                    )
        now = clock()
        if now >= deadline:
            raise CampaignError(
                f"no llegó {expected_ready!r} en {timeout_s}s"
            )
        sleep(min(0.05, max(0.0, deadline - now)))


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
    system_values = list(matrix.get("systems", []))
    if len(system_values) != len(set(system_values)):
        raise CampaignError("paper_matrix.systems contiene valores duplicados")
    system_set = frozenset(system_values)
    cohort_contracts = COHORT_CONTRACTS.get(system_set)
    if cohort_contracts is None:
        allowed = [
            sorted(systems)
            for systems in sorted(
                COHORT_CONTRACTS,
                key=lambda systems: tuple(sorted(systems)),
            )
        ]
        raise CampaignError(
            "paper_matrix.systems no coincide con una cohorte exacta; "
            f"cohortes admitidas: {allowed}"
        )
    require_exact_set(
        manifest.get("systems", {}).keys(),
        set(cohort_contracts),
        "systems",
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

    expected_contracts = {
        system: dict(contract)
        for system, contract in cohort_contracts.items()
    }
    approved_override = APPROVED_CAMPAIGN_CONTRACT_OVERRIDES.get(campaign_id)
    if approved_override is not None:
        if system_set != approved_override["systems"]:
            raise CampaignError(
                "la campaña derivada aprobada no conserva su cohorte base"
            )
        derivation = manifest.get("derivation", {})
        for field, expected in approved_override["derivation"].items():
            if derivation.get(field) != expected:
                raise CampaignError(
                    f"derivación aprobada no coincide en {field}"
                )
        for system, overrides in approved_override[
            "system_contracts"
        ].items():
            expected_contracts[system].update(overrides)

    for system, expected_contract in expected_contracts.items():
        contract = manifest.get("systems", {}).get(system, {})
        for field, expected in expected_contract.items():
            if contract.get(field) != expected:
                raise CampaignError(
                    f"{field} de {system} no coincide con la cohorte "
                    f"registrada ({expected})"
                )

    if system_set == SELECTED_SYSTEMS:
        selected_reference = manifest.get("selected_system_manifests", {})
        selected_path_value = selected_reference.get("path")
        expected_hash = selected_reference.get("sha256")
        if (
            selected_path_value
            != "validation/selected_system_manifests_v1.json"
            or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        ):
            raise CampaignError(
                "la cohorte seleccionada exige una referencia SHA-256 válida "
                "a selected_system_manifests_v1.json"
            )
        selected_path = (ROOT / selected_path_value).resolve()
        if sha256_file(selected_path) != expected_hash:
            raise CampaignError(
                "el hash de selected_system_manifests_v1.json no coincide"
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
        dense_cells = manifest.get("dense_capture_cells")
        if not isinstance(dense_cells, list) or not dense_cells:
            raise CampaignError(
                "dense_capture_cells debe contener al menos una celda"
            )
        if (
            any(not isinstance(cell_id, str) for cell_id in dense_cells)
            or len(set(dense_cells)) != len(dense_cells)
        ):
            raise CampaignError(
                "dense_capture_cells debe contener identificadores únicos"
            )
        known_cell_ids = {
            cell["cell_id"] for cell in matrix_cells(manifest)
        }
        unknown_dense_cells = sorted(set(dense_cells) - known_cell_ids)
        if unknown_dense_cells:
            raise CampaignError(
                "dense_capture_cells contiene celdas inexistentes: "
                + ", ".join(unknown_dense_cells)
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
    primary_handshake: bool = False,
) -> Path:
    if benchmark_mode and buffered_capture_mode:
        raise CampaignError(
            "benchmark_mode y buffered_capture_mode son mutuamente excluyentes"
        )
    if primary_handshake and not benchmark_mode:
        raise CampaignError(
            "primary_handshake exige benchmark_mode"
        )
    build = manifest["build"]
    if primary_handshake:
        pattern = build.get(
            "primary_handshake_directory_pattern",
            (
                "{root}/{board}/"
                "benchmark-10000-primary-handshake-release"
            ),
        )
    elif benchmark_mode:
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
    allowed_build_root = (ROOT / "build").resolve()
    allowed = (ROOT / Path(build["root"])).resolve()
    try:
        allowed.relative_to(allowed_build_root)
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
    primary_handshake: bool = False,
) -> list[Path]:
    directory = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
        primary_handshake=primary_handshake,
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
    primary_handshake: bool = False,
) -> list[Path]:
    directory = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
        primary_handshake=primary_handshake,
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
    primary_handshake: bool = False,
    energy_work_multiplier: int = 1,
) -> list[str]:
    if primary_handshake and not benchmark_mode:
        raise CampaignError(
            "PrimaryHandshake sólo es válido con BenchmarkMode"
        )
    command = [
        powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "tools" / "build_campaign.ps1"),
        "-BuildRoot",
        str(manifest["build"]["root"]),
        "-Board",
        cell["board"],
        "-Decimation",
        str(decimation),
        "-Target",
        firmware_target(manifest, cell),
    ]
    if benchmark_mode:
        command.append("-BenchmarkMode")
    if primary_handshake:
        command.append("-PrimaryHandshake")
    if energy_work_multiplier != 1:
        if not primary_handshake:
            raise CampaignError(
                "EnergyWorkMultiplier distinto de 1 exige "
                "PrimaryHandshake"
            )
        command.extend(
            (
                "-EnergyWorkMultiplier",
                str(energy_work_multiplier),
            )
        )
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
    primary_handshake: bool = False,
) -> list[str]:
    if reset_only and no_reset:
        raise CampaignError("reset_only y no_reset son mutuamente excluyentes")
    if primary_handshake and not benchmark_mode:
        raise CampaignError(
            "primary_handshake exige benchmark_mode"
        )
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
                primary_handshake=primary_handshake,
            )
        ),
    ]
    if reset_only:
        command.append("-ResetOnly")
    if no_reset:
        command.append("-NoReset")
    return command


def runtime_probe_command(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    output_directory: Path,
) -> list[str]:
    board = manifest["boards"][cell["board"]]
    return [
        powershell_executable(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "tools" / "read_runtime_probe.ps1"),
        "-Board",
        cell["board"],
        "-BuildDirectory",
        str(
            build_directory(
                manifest,
                cell,
                decimation,
                benchmark_mode=True,
                primary_handshake=True,
            )
        ),
        "-Target",
        firmware_target(manifest, cell),
        "-ProbeSerial",
        board["probe_serial"],
        "-OutputDirectory",
        str(output_directory),
        "-PythonExecutable",
        sys.executable,
        "-AllowUnavailablePostPowerCycle",
    ]


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


def load_runtime_probe_artifacts(
    run_directory: Path,
    board: str,
    expected_m7_clock_hz: int,
) -> dict[str, Any]:
    if board not in {"f746", "h755"}:
        raise CampaignError(f"placa FRP1 desconocida: {board}")
    if (
        isinstance(expected_m7_clock_hz, bool)
        or not isinstance(expected_m7_clock_hz, int)
        or expected_m7_clock_hz <= 0
    ):
        raise CampaignError("reloj M7 esperado FRP1 debe ser un entero positivo")
    if board == "h755" and expected_m7_clock_hz % 2 != 0:
        raise CampaignError(
            "reloj M7 esperado H755 debe permitir M4=clock/2 exacto"
        )

    cores = ("f746_m7",) if board == "f746" else ("h755_m7", "h755_m4")
    records = []
    for core in cores:
        raw_path = run_directory / f"runtime_probe_{core}.bin"
        report_path = run_directory / f"runtime_probe_{core}.json"
        location_path = (
            run_directory / f"runtime_probe_{core}_location.json"
        )
        if report_path.is_file():
            try:
                partial = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CampaignError(
                    f"reporte FRP1 inválido para {core}"
                ) from exc
            if (
                partial.get("schema")
                == "fractional-chaos-runtime-probe-unavailable-v1"
                and partial.get("status")
                == "swd_unavailable_post_power_cycle"
            ):
                if (
                    partial.get("core") != core
                    or partial.get("board") != board
                    or partial.get("eligible_as_primary_evidence") is not False
                ):
                    raise CampaignError(
                        f"estado FRP1 post-power-cycle inválido para {core}"
                    )
                records.append(
                    {
                        "core": core,
                        "status": "swd_unavailable_post_power_cycle",
                        "eligible_as_primary_evidence": False,
                        "reason": partial.get("reason", ""),
                        "report_path": report_path.name,
                        "report_sha256": sha256_file(report_path),
                        "location_path": (
                            location_path.name
                            if location_path.is_file()
                            else None
                        ),
                        "location_sha256": (
                            sha256_file(location_path)
                            if location_path.is_file()
                            else None
                        ),
                    }
                )
                continue
        if not raw_path.is_file() or not report_path.is_file():
            raise CampaignError(
                f"faltan artefactos FRP1 para {core}: {run_directory}"
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CampaignError(
                f"reporte FRP1 inválido para {core}"
            ) from exc
        if (
            report.get("schema")
            != "fractional-chaos-runtime-probe-v1"
            or report.get("core") != core
            or report.get("quality", {}).get("complete") is not True
            or report.get("quality", {}).get("stack_probe_overflow")
            is not False
            or report.get("eligible_as_primary_evidence") is not False
        ):
            raise CampaignError(
                f"reporte FRP1 no cumple el contrato para {core}"
            )
        expected_core_clock_hz = (
            expected_m7_clock_hz
            if core != "h755_m4"
            else expected_m7_clock_hz // 2
        )
        record = report.get("record")
        observed_expected_clock_hz = (
            record.get("expected_core_clock_hz")
            if isinstance(record, dict)
            else None
        )
        observed_software_clock_hz = (
            record.get("software_core_clock_hz")
            if isinstance(record, dict)
            else None
        )
        if (
            not isinstance(record, dict)
            or observed_expected_clock_hz != expected_core_clock_hz
            or observed_software_clock_hz != expected_core_clock_hz
        ):
            raise CampaignError(
                f"reloj FRP1 no coincide con el manifiesto para {core}: "
                f"esperado={expected_core_clock_hz}, "
                f"record.expected={observed_expected_clock_hz}, "
                f"record.software={observed_software_clock_hz}"
            )
        raw_sha256 = sha256_file(raw_path)
        if report.get("raw", {}).get("sha256") != raw_sha256:
            raise CampaignError(
                f"hash FRP1 no coincide para {core}"
            )
        records.append(
            {
                "core": core,
                "raw_path": raw_path.name,
                "raw_sha256": raw_sha256,
                "report_path": report_path.name,
                "report_sha256": sha256_file(report_path),
                "location_path": (
                    location_path.name if location_path.is_file() else None
                ),
                "location_sha256": (
                    sha256_file(location_path)
                    if location_path.is_file()
                    else None
                ),
                "stack_high_water_bytes": report["record"][
                    "stack_high_water_bytes"
                ],
                "stack_headroom_bytes": report["quality"][
                    "stack_headroom_bytes"
                ],
                "expected_core_clock_hz": report["record"][
                    "expected_core_clock_hz"
                ],
                "software_core_clock_hz": report["record"][
                    "software_core_clock_hz"
                ],
                "heap_configured_bytes": report["record"][
                    "heap_configured_bytes"
                ],
            }
        )
    unavailable_records = sum(
        record.get("status") == "swd_unavailable_post_power_cycle"
        for record in records
    )
    bundle_status = (
        "swd_unavailable_post_power_cycle"
        if unavailable_records == len(records)
        else (
            "partially_measured_post_power_cycle"
            if unavailable_records
            else "measured_post_arm_on_physical_target"
        )
    )
    return {
        "schema": "fractional-chaos-runtime-probe-bundle-v1",
        "status": bundle_status,
        "records": records,
        "eligible_as_primary_evidence": False,
        "clean_source_required_for_reportable_use": True,
    }


def ensure_build(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    log_path: Path,
    *,
    benchmark_mode: bool = False,
    buffered_capture_mode: bool = False,
    primary_handshake: bool = False,
    energy_work_multiplier: int = 1,
) -> list[Path]:
    run_process(
        build_command(
            manifest,
            cell,
            decimation,
            benchmark_mode=benchmark_mode,
            buffered_capture_mode=buffered_capture_mode,
            primary_handshake=primary_handshake,
            energy_work_multiplier=energy_work_multiplier,
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
        primary_handshake=primary_handshake,
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
            primary_handshake=primary_handshake,
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


def ina226_capture_quality(
    stream: Ina226CaptureStream,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate raw transport and the measured PE0/PA0 windows.

    A clean END frame is necessary but insufficient: the energy window must
    contain at least 1000 measured samples and last at least two seconds.
    """

    if not records or records[0].get("type") != "header":
        raise CampaignError("captura INA226 sin header")
    if records[-1].get("type") != "end":
        raise CampaignError("captura INA226 sin end")
    terminal = stream.terminal
    samples = [
        record for record in records if record.get("type") == "sample"
    ]
    energy_edges = [
        record
        for record in records
        if record.get("type") == "edge"
        and record.get("marker_kind") == "energy_window"
    ]
    clock_edges = [
        record
        for record in records
        if record.get("type") == "edge"
        and record.get("marker_kind") == "clock_reference"
    ]
    energy_edge_shape = (
        len(energy_edges) == 2
        and [int(edge["level"]) for edge in energy_edges] == [1, 0]
        and int(energy_edges[1]["timestamp_ns"])
        > int(energy_edges[0]["timestamp_ns"])
    )
    clock_edge_shape = (
        len(clock_edges) == 2
        and [int(edge["level"]) for edge in clock_edges] == [1, 0]
        and int(clock_edges[1]["timestamp_ns"])
        > int(clock_edges[0]["timestamp_ns"])
    )
    rise_ns = (
        int(energy_edges[0]["timestamp_ns"])
        if energy_edge_shape
        else None
    )
    fall_ns = (
        int(energy_edges[1]["timestamp_ns"])
        if energy_edge_shape
        else None
    )
    active_samples = (
        [
            sample
            for sample in samples
            if rise_ns <= int(sample["timestamp_ns"]) <= fall_ns
        ]
        if rise_ns is not None and fall_ns is not None
        else []
    )
    pre_samples = (
        [
            sample
            for sample in samples
            if int(sample["timestamp_ns"]) < rise_ns
        ]
        if rise_ns is not None
        else []
    )
    post_samples = (
        [
            sample
            for sample in samples
            if int(sample["timestamp_ns"]) > fall_ns
        ]
        if fall_ns is not None
        else []
    )
    window_duration_s = (
        (fall_ns - rise_ns) * 1e-9
        if rise_ns is not None and fall_ns is not None
        else 0.0
    )
    clock_reference_duration_s = (
        (
            int(clock_edges[1]["timestamp_ns"])
            - int(clock_edges[0]["timestamp_ns"])
        )
        * 1e-9
        if clock_edge_shape
        else 0.0
    )
    clock_precedes_energy_window = (
        clock_edge_shape
        and rise_ns is not None
        and int(clock_edges[0]["timestamp_ns"])
        < int(clock_edges[1]["timestamp_ns"])
        < rise_ns
    )
    sequences = [int(sample["sequence"]) for sample in samples]
    timestamps = [int(sample["timestamp_ns"]) for sample in samples]
    flags = {
        "terminal_complete": bool(terminal.flags & END_COMPLETE),
        "terminal_not_stopped": not bool(terminal.flags & END_STOPPED),
        "terminal_not_timed_out": not bool(terminal.flags & END_TIMEOUT),
        "terminal_no_i2c_error": not bool(
            terminal.flags & END_I2C_ERROR
        )
        and terminal.value_0 == 0,
        "terminal_no_edge_overflow": not bool(
            terminal.flags & END_EDGE_OVERFLOW
        ),
        "terminal_no_timing_or_edge_anomaly": not bool(
            terminal.flags & END_TIMING_OR_EDGE_ANOMALY
        )
        and terminal.value_1 == 0,
        "wire_crc": stream.crc_failures == 0,
        "wire_alignment": stream.discarded_wire_bytes == 0,
        "sample_sequence_starts_zero": bool(sequences)
        and sequences[0] == 0,
        "sample_sequence_contiguous": all(
            right == left + 1
            for left, right in zip(sequences, sequences[1:])
        ),
        "terminal_sequence_matches_samples": bool(sequences)
        and terminal.sequence == sequences[-1] + 1
        and terminal.sequence == len(samples) + terminal.value_1,
        "sample_timestamps_strict": all(
            right > left
            for left, right in zip(timestamps, timestamps[1:])
        ),
        "sample_i2c": all(
            bool(sample.get("i2c_ok")) for sample in samples
        ),
        "sample_conversion_ready": all(
            bool(sample.get("conversion_ready")) for sample in samples
        ),
        "sample_no_math_overflow": not any(
            bool(sample.get("math_overflow")) for sample in samples
        ),
        "energy_window_edges": energy_edge_shape,
        "clock_reference_edges": clock_edge_shape,
        "clock_reference_duration": (
            0.09 <= clock_reference_duration_s <= 0.11
        ),
        "clock_precedes_energy_window": clock_precedes_energy_window,
        "pre_idle_samples": len(pre_samples) >= stream.armed.pre_samples,
        "post_idle_samples": len(post_samples) >= stream.armed.post_samples,
        "window_sample_count": len(active_samples) >= 1_000,
        "window_duration": window_duration_s >= 2.0,
    }
    transport_valid = all(flags.values())
    return {
        "schema": "fractional-chaos-ina226-run-quality-v1",
        "status": "accepted_raw_transport" if transport_valid else "rejected",
        "flags": flags,
        "observed": {
            "sample_count": len(samples),
            "active_sample_count": len(active_samples),
            "pre_idle_sample_count": len(pre_samples),
            "post_idle_sample_count": len(post_samples),
            "energy_edge_count": len(energy_edges),
            "clock_reference_edge_count": len(clock_edges),
            "window_duration_s": window_duration_s,
            "clock_reference_duration_s": clock_reference_duration_s,
            "crc_failures": stream.crc_failures,
            "discarded_wire_bytes": stream.discarded_wire_bytes,
            "i2c_error_count": terminal.value_0,
            "late_sample_slots": terminal.value_1,
        },
        "required": {
            "minimum_active_samples": 1_000,
            "minimum_window_duration_s": 2.0,
            "pre_idle_samples": stream.armed.pre_samples,
            "post_idle_samples": stream.armed.post_samples,
            "energy_edge_levels": [1, 0],
            "clock_reference_edge_levels": [1, 0],
            "clock_reference_duration_s": 0.1,
            "clock_reference_tolerance_s": 0.01,
        },
        "transport_valid": transport_valid,
        "publication_ready": False,
        "eligible_as_primary_benchmark": False,
        "eligible_as_paper_energy_evidence": False,
        "physical_preflight_still_required": True,
    }


def load_energy_workload_contract(
    cell: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        payload = json.loads(
            ENERGY_WORKLOAD_CONTRACT_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(
            "no se pudo leer energy_workload_contract_v1.json"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema")
        != "fractional-chaos-energy-workload-contract-v1"
        or payload.get("status")
        != "predeclared_before_external_energy_measurement"
    ):
        raise CampaignError("contrato de workload energético inválido")
    work_unit = payload.get("work_unit")
    requirement = payload.get("capture_requirement")
    if not isinstance(work_unit, dict) or not isinstance(requirement, dict):
        raise CampaignError("contrato energético incompleto")
    base_work_units = int(work_unit.get("base_work_units", 0))
    if (
        base_work_units != 10_000
        or int(work_unit.get("timing_values_retained", 0)) != 10_000
        or work_unit.get("energy_window_includes_retained_timing_steps")
        is not True
        or work_unit.get("supplemental_steps_use_same_solver_step_cycles_call")
        is not True
        or work_unit.get("uart_inside_energy_window") is not False
    ):
        raise CampaignError(
            "el contrato energético no preserva los 10k DWT congelados"
        )
    if (
        int(requirement.get("sample_rate_hz", 0)) != 500
        or int(requirement.get("minimum_window_samples", 0)) != 1_000
        or float(requirement.get("minimum_window_s", 0.0)) != 2.0
    ):
        raise CampaignError(
            "requisitos de captura energética distintos del contrato runner"
        )
    try:
        multiplier = int(
            payload["multipliers"][str(cell["board"])][
                str(cell["method"])
            ][str(cell["representation"])]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CampaignError(
            "la celda no tiene multiplicador energético predeclarado"
        ) from exc
    if multiplier <= 0:
        raise CampaignError("multiplicador energético debe ser positivo")

    sizing = payload.get("sizing_basis")
    if not isinstance(sizing, dict):
        raise CampaignError("sizing_basis energético ausente")
    source_relative = sizing.get("source")
    source_sha256 = sizing.get("source_sha256")
    if (
        not isinstance(source_relative, str)
        or not isinstance(source_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
    ):
        raise CampaignError("procedencia de sizing energético inválida")
    sizing_path = (ROOT / source_relative).resolve()
    try:
        sizing_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise CampaignError(
            "sizing_basis energético escapa del repositorio"
        ) from exc
    if not sizing_path.is_file() or sha256_file(sizing_path) != source_sha256:
        raise CampaignError(
            "el artefacto de sizing energético no coincide con su SHA-256"
        )

    return {
        "schema": payload["schema"],
        "contract_id": payload.get("contract_id"),
        "status": payload["status"],
        "path": ENERGY_WORKLOAD_CONTRACT_PATH.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(ENERGY_WORKLOAD_CONTRACT_PATH),
        "base_work_units": base_work_units,
        "work_unit_label": work_unit.get("label"),
        "multiplier": multiplier,
        "work_units": base_work_units * multiplier,
        "capture_requirement": dict(requirement),
        "sizing_basis": dict(sizing),
        "evidence_boundary": dict(payload.get("evidence_boundary", {})),
    }


def persist_ina226_capture(
    path: Path,
    stream: Ina226CaptureStream,
    *,
    run_id: str,
    controller_id: str,
    firmware_clock_profile: str,
    work_units: int,
    work_unit_label: str,
) -> dict[str, Any]:
    records = build_ina226_capture_records(
        stream,
        capture_id=f"{run_id}__ina226",
        run_id=run_id,
        controller_id=controller_id,
        firmware_clock_profile=firmware_clock_profile,
        work_units=work_units,
        work_unit_label=work_unit_label,
        physically_verified_shunt_marking="R100",
    )
    payload = "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
        for record in records
    ).encode("utf-8")
    atomic_write_bytes(path, payload)
    quality = ina226_capture_quality(stream, records)
    return {
        "schema": "fractional-chaos-ina226-run-artifact-v1",
        "status": quality["status"],
        "raw_jsonl_path": path.name,
        "raw_jsonl_sha256": sha256_bytes(payload),
        "capture_id": f"{run_id}__ina226",
        "controller_id": records[0]["timebase"]["controller_id"],
        "sensor_id": records[0]["sensor_id"],
        "board": records[0]["board"],
        "i2c_address": records[0]["i2c_address"],
        "firmware_clock_profile": firmware_clock_profile,
        "clock_reference_contract": records[0][
            "clock_reference_contract"
        ],
        "work_units": work_units,
        "work_unit_label": records[0]["work_unit_label"],
        "marker_inputs": records[0]["marker_inputs"],
        "quality": quality,
        "publication_ready": False,
        "eligible_as_primary_benchmark": False,
        "eligible_as_paper_energy_evidence": False,
    }


def _capture_after_power_cycle(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    *,
    endpoint: str,
    run_id: str,
    config: PowerCycleConfig,
    reset_evidence_out: dict[str, Any] | None,
    serial_factory: SerialFactory,
    snapshot_factory: SnapshotFactory | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    energy_capture_path: Path | None,
) -> tuple[bytes, list[tuple[int, ...]], FCC1StreamParser, dict[str, float]]:
    board = manifest["boards"][cell["board"]]
    firmware_clock_binding = campaign_firmware_clock_binding(
        manifest,
        cell["board"],
    )
    timeouts = manifest["timeouts_s"]
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

    if snapshot_factory is None:
        enumerator = PySerialDeviceEnumerator()
        snapshot_factory = enumerator.snapshot
    parser = FCC1StreamParser()
    frames: list[tuple[int, ...]] = []
    raw = bytearray()
    first_matching_sequence: int | None = None
    endpoint_reached = False
    first_frame_timeout = float(
        timeouts["endpoint"] if benchmark_mode else timeouts["first_frame"]
    )
    capture_started = clock()
    if config.ina226_energy_enabled and energy_capture_path is None:
        raise CampaignError(
            "la captura INA226 requiere una ruta JSONL de evidencia"
        )
    physical_controller_id: str | None = None
    if config.ina226_energy_enabled:
        if config.controller_id is None:
            raise CampaignError(
                "la captura INA226 requiere la identidad física del UNO"
            )
        try:
            physical_controller_id = validate_ina226_controller_id(
                config.controller_id
            )
        except InaCaptureError as exc:
            raise CampaignError(f"controller_id INA226 inválido: {exc}") from exc
    workload = (
        load_energy_workload_contract(cell)
        if config.ina226_energy_enabled
        else None
    )

    controller_context = serial_factory(
        config.controller_port,
        config.controller_baud,
        min(config.poll_interval_s, 0.1),
    )
    with controller_context as controller:
        # Allow a classic UNO bootloader/reset caused by opening its COM to
        # settle before synchronizing the fail-off state.
        sleep(2.0)
        _configure_open_serial(controller)
        controller_prime, initial_snapshot = (
            ensure_target_powered_after_controller_open(
                controller=controller,
                board_name=cell["board"],
                board=board,
                config=config,
                snapshot_factory=snapshot_factory,
                clock=clock,
                sleep=sleep,
            )
        )
        cycle = perform_external_power_cycle(
            board_name=cell["board"],
            board=board,
            run_id=run_id,
            config=config,
            serial_factory=serial_factory,
            snapshot_factory=snapshot_factory,
            clock=clock,
            sleep=sleep,
            controller_serial=controller,
            initial_snapshot=initial_snapshot,
        )
        cycle.evidence["controller_open_prime"] = controller_prime
        cycle.evidence["power_cycle_repetition_id"] = cell.get(
            "cold_start_id"
        )
        cycle.evidence["contract_authorization"] = (
            "pilot_only_pending_real_preflight_and_manifest_authorization"
        )
        cycle.evidence["postboot_handshake"] = {
            "protocol": POSTBOOT_HANDSHAKE_PROTOCOL,
            "completed": False,
        }
        cycle.evidence["ina226_energy"] = {
            "requested": config.ina226_energy_enabled,
            "status": (
                "pending_arm"
                if config.ina226_energy_enabled
                else "not_requested"
            ),
            "single_controller_serial_owner": True,
            "publication_ready": False,
            "eligible_as_primary_benchmark": False,
            "workload_contract": workload,
        }
        if reset_evidence_out is not None:
            reset_evidence_out.clear()
            reset_evidence_out.update(cycle.evidence)

        opened_port, final_port_record, reopen = open_reenumerated_serial(
            cycle=cycle,
            board=board,
            config=config,
            serial_factory=serial_factory,
            snapshot_factory=snapshot_factory,
            serial_read_timeout_s=float(timeouts["serial_read"]),
            clock=clock,
            sleep=sleep,
        )
        cycle.evidence["port_reopen"] = reopen
        cycle.evidence["usb_reenumeration"][
            "port_after"
        ] = final_port_record.as_metadata()
        cycle.evidence["usb_reenumeration"]["port_changed"] = (
            normalize_port_name(
                cycle.evidence["usb_reenumeration"]["port_before"]["device"]
            )
            != normalize_port_name(final_port_record.device)
        )
        if reset_evidence_out is not None:
            reset_evidence_out.clear()
            reset_evidence_out.update(cycle.evidence)

        with opened_port as port:
            ina_session: Ina226Session | None = None
            energy_stream: Ina226CaptureStream | None = None
            if config.ina226_energy_enabled:
                assert workload is not None
                ina_session = Ina226Session(controller)
                try:
                    armed = ina_session.arm(
                        cycle.request_id,
                        config.channel_for(cell["board"]),
                        pre_samples=config.ina226_pre_samples,
                        post_samples=config.ina226_post_samples,
                        timeout_ms=config.ina226_timeout_ms,
                        physically_confirmed_shunt_marking="R100",
                    )
                except InaCaptureError as exc:
                    raise CampaignError(
                        f"no se pudo armar INA226: {exc}"
                    ) from exc
                cycle.evidence["ina226_energy"].update(
                    {
                        "status": "armed_prefill_pending",
                        "armed": {
                            "request_id": armed.request_id,
                            "channel": armed.channel,
                            "i2c_address": armed.address,
                            "manufacturer_id": armed.manufacturer_id,
                            "die_id": armed.die_id,
                            "sample_period_us": armed.period_us,
                            "pre_samples": armed.pre_samples,
                            "post_samples": armed.post_samples,
                            "timeout_ms": armed.timeout_ms,
                            "shunt_milliohms": armed.shunt_milliohms,
                            "shunt_marking_asserted": armed.shunt_marking,
                            "ids_establish_compatibility_not_authenticity": (
                                True
                            ),
                        },
                    }
                )
                prefill_started = clock()
                prefill_timeout_s = max(
                    5.0,
                    armed.pre_samples * armed.period_us * 1e-6 * 4.0,
                )
                prefill_deadline = prefill_started + prefill_timeout_s
                while (
                    ina_session.sample_frames_received
                    < armed.pre_samples
                ):
                    try:
                        energy_stream = ina_session.poll_capture()
                    except InaCaptureError as exc:
                        raise CampaignError(
                            f"stream INA226 inválido durante prefill: {exc}"
                        ) from exc
                    if energy_stream is not None:
                        break
                    now = clock()
                    if now >= prefill_deadline:
                        raise CampaignError(
                            "INA226 no reunió pre_samples antes de START"
                        )
                    sleep(min(0.002, prefill_deadline - now))
                if energy_stream is not None:
                    assert energy_capture_path is not None
                    assert physical_controller_id is not None
                    artifact = persist_ina226_capture(
                        energy_capture_path,
                        energy_stream,
                        run_id=run_id,
                        controller_id=physical_controller_id,
                        firmware_clock_profile=str(
                            firmware_clock_binding[
                                "firmware_clock_profile"
                            ]
                        ),
                        work_units=int(workload["work_units"]),
                        work_unit_label=str(
                            workload["work_unit_label"]
                        ),
                    )
                    cycle.evidence["ina226_energy"].update(artifact)
                    if reset_evidence_out is not None:
                        reset_evidence_out.clear()
                        reset_evidence_out.update(cycle.evidence)
                    raise CampaignError(
                        "INA226 terminó antes del START; captura rechazada"
                    )
                cycle.evidence["ina226_energy"].update(
                    {
                        "status": "prefill_complete_waiting_start",
                        "prefill_samples_received": (
                            ina_session.sample_frames_received
                        ),
                        "prefill_wall_time_s": clock() - prefill_started,
                    }
                )
                if reset_evidence_out is not None:
                    reset_evidence_out.clear()
                    reset_evidence_out.update(cycle.evidence)

            initial_payload, handshake = perform_postboot_handshake(
                port,
                request_id=cycle.request_id,
                identity=identity,
                timeout_s=config.handshake_timeout_s,
                clock=clock,
                sleep=sleep,
            )
            cycle.evidence["postboot_handshake"] = {
                **handshake,
                "completed": True,
            }
            if ina_session is not None:
                cycle.evidence["ina226_energy"]["status"] = (
                    "capturing_after_ready"
                )
            if reset_evidence_out is not None:
                reset_evidence_out.clear()
                reset_evidence_out.update(cycle.evidence)

            handshake_finished = clock()
            first_frame_deadline = handshake_finished + first_frame_timeout
            endpoint_deadline = (
                handshake_finished + float(timeouts["endpoint"])
            )
            energy_deadline = (
                handshake_finished
                + config.ina226_timeout_ms / 1000.0
                + 5.0
            )

            def consume(chunk: bytes) -> None:
                nonlocal first_matching_sequence, endpoint_reached
                if not chunk:
                    return
                raw.extend(chunk)
                for values in parser.feed(chunk):
                    frames.append(values)
                    if frame_matches_identity(values, identity):
                        sequence = int(values[8])
                        if first_matching_sequence is None:
                            first_matching_sequence = sequence
                        if sequence >= target:
                            endpoint_reached = True

            consume(initial_payload)
            while (
                not endpoint_reached
                or (ina_session is not None and energy_stream is None)
            ):
                consume(_serial_read(port))
                if ina_session is not None and energy_stream is None:
                    try:
                        energy_stream = ina_session.poll_capture()
                    except InaCaptureError as exc:
                        raise CampaignError(
                            f"stream INA226 inválido: {exc}"
                        ) from exc
                now = clock()
                if (
                    first_matching_sequence is None
                    and now >= first_frame_deadline
                ):
                    raise CampaignError(
                        f"no llegó FCC1 válido después de READY antes del "
                        f"watchdog de {first_frame_timeout}s"
                    )
                if not endpoint_reached and now >= endpoint_deadline:
                    raise CampaignError(
                        f"no se alcanzó el endpoint sequence={target} en "
                        f"{timeouts['endpoint']}s"
                    )
                if (
                    ina_session is not None
                    and energy_stream is None
                    and now >= energy_deadline
                ):
                    raise CampaignError(
                        "no llegó END INA226 antes del watchdog host"
                    )
                sleep(0.001)

            if energy_stream is not None:
                assert workload is not None
                assert energy_capture_path is not None
                assert physical_controller_id is not None
                artifact = persist_ina226_capture(
                    energy_capture_path,
                    energy_stream,
                    run_id=run_id,
                    controller_id=physical_controller_id,
                    firmware_clock_profile=str(
                        firmware_clock_binding[
                            "firmware_clock_profile"
                        ]
                    ),
                    work_units=int(workload["work_units"]),
                    work_unit_label=str(workload["work_unit_label"]),
                )
                cycle.evidence["ina226_energy"].update(artifact)
                if reset_evidence_out is not None:
                    reset_evidence_out.clear()
                    reset_evidence_out.update(cycle.evidence)
                if not artifact["quality"]["transport_valid"]:
                    failed_flags = [
                        name
                        for name, passed in artifact["quality"][
                            "flags"
                        ].items()
                        if not passed
                    ]
                    raise CampaignError(
                        "captura INA226 rechazada: "
                        + ", ".join(failed_flags)
                    )

        finished = clock()
    return (
        bytes(raw),
        frames,
        parser,
        {
            "reset_wall_time_s": float(
                cycle.evidence["cycle_wall_time_s"]
            ),
            "capture_wall_time_s": finished - capture_started,
            "postboot_handshake_wall_time_s": float(
                cycle.evidence["postboot_handshake"]["wall_time_s"]
            ),
        },
    )


def capture_after_reset(
    manifest: dict[str, Any],
    cell: dict[str, Any],
    decimation: int,
    process_log: Path,
    *,
    endpoint: str = "transport_pilot",
    run_id: str = "unspecified-run",
    power_cycle_config: PowerCycleConfig | None = None,
    reset_evidence_out: dict[str, Any] | None = None,
    serial_factory: SerialFactory = default_serial_factory,
    snapshot_factory: SnapshotFactory | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    energy_capture_path: Path | None = None,
) -> tuple[bytes, list[tuple[int, ...]], FCC1StreamParser, dict[str, float]]:
    if power_cycle_config is not None:
        if process_log.exists():
            raise CampaignError(
                f"no se sobrescribe el log de reset existente: {process_log}"
            )
        try:
            captured = _capture_after_power_cycle(
                manifest,
                cell,
                decimation,
                endpoint=endpoint,
                run_id=run_id,
                config=power_cycle_config,
                reset_evidence_out=reset_evidence_out,
                serial_factory=serial_factory,
                snapshot_factory=snapshot_factory,
                clock=clock,
                sleep=sleep,
                energy_capture_path=energy_capture_path,
            )
        except Exception as exc:
            atomic_write_json(
                process_log,
                {
                    "schema": "fractional-chaos-power-cycle-log-v1",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "reset_evidence": reset_evidence_out or None,
                },
            )
            raise
        atomic_write_json(
            process_log,
            {
                "schema": "fractional-chaos-power-cycle-log-v1",
                "status": "completed",
                "reset_evidence": reset_evidence_out,
            },
        )
        return captured

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


def pilot_run_id(
    scheduled: dict[str, Any],
    endpoint: str,
    *,
    external_power_cycle: bool = False,
    ina226_energy: bool = False,
) -> str:
    suffixes = {
        "benchmark_reset_pilot": "benchmark-reset-pilot",
        "transport_pilot": "transport-pilot",
        "dense_timeseries_pilot": "dense-timeseries-pilot",
    }
    try:
        suffix = suffixes[endpoint]
    except KeyError as exc:
        raise CampaignError(f"endpoint sin run-id propio: {endpoint}") from exc
    if external_power_cycle:
        suffix = f"{suffix}-external-power-cycle"
    if ina226_energy:
        if not external_power_cycle:
            raise CampaignError(
                "run_id INA226 exige external_power_cycle"
            )
        suffix = f"{suffix}-ina226-energy"
    return f"{scheduled['run_id']}__{suffix}"


def pilot_run_directory(
    output_root: Path,
    campaign_id: str,
    scheduled: dict[str, Any],
    endpoint: str,
    *,
    external_power_cycle: bool = False,
    ina226_energy: bool = False,
) -> Path:
    return (
        output_root
        / campaign_id
        / "runs"
        / pilot_run_id(
            scheduled,
            endpoint,
            external_power_cycle=external_power_cycle,
            ina226_energy=ina226_energy,
        )
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
    *,
    external_power_cycle: bool = False,
    ina226_energy: bool = False,
    controller_id: str | None = None,
) -> dict[str, Any] | None:
    run_directory = pilot_run_directory(
        output_root,
        manifest["campaign_id"],
        scheduled,
        endpoint,
        external_power_cycle=external_power_cycle,
        ina226_energy=ina226_energy,
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

    expected_run_id = pilot_run_id(
        scheduled,
        endpoint,
        external_power_cycle=external_power_cycle,
        ina226_energy=ina226_energy,
    )
    expected_clock_binding = campaign_firmware_clock_binding(
        manifest,
        scheduled["board"],
    )
    build_metadata = result.get("build", {})
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
        build_metadata.get("firmware_clock_profile")
        == expected_clock_binding["firmware_clock_profile"],
        build_metadata.get("system_clock_hz")
        == expected_clock_binding["system_clock_hz"],
        build_metadata.get("explicit_h755_clock_480_off")
        == expected_clock_binding["explicit_h755_clock_480_off"],
    )
    if not all(checks):
        raise CampaignError(
            f"--resume rechazó run.json inconsistente: {result_path}"
        )
    if external_power_cycle:
        reset = result.get("reset", {})
        hardware = result.get("hardware", {})
        handshake = reset.get("postboot_handshake", {})
        if (
            hardware.get("reset_mode")
            != "external_power_cycle_controller"
            or hardware.get("power_removed") is not True
            or reset.get("power_removed") is not True
            or handshake.get("completed") is not True
            or result.get("eligible_as_primary_benchmark") is not False
            or result.get("eligible_as_paper_cold_start") is not False
        ):
            raise CampaignError(
                "--resume rechazó evidencia incompleta de power-cycle: "
                f"{result_path}"
            )
        observed_runtime_probe = load_runtime_probe_artifacts(
            run_directory,
            scheduled["board"],
            int(
                manifest["boards"][scheduled["board"]][
                    "system_clock_hz"
                ]
            ),
        )
        res_probe = result.get("runtime_probe")
        if res_probe != observed_runtime_probe:
            if not (
                isinstance(res_probe, dict)
                and res_probe.get("status") == "swd_unavailable_post_power_cycle"
                and observed_runtime_probe.get("status")
                == "swd_unavailable_post_power_cycle"
            ):
                raise CampaignError(
                    "--resume rechazó bundle FRP1 inconsistente: "
                    f"{result_path}"
                )
    if ina226_energy:
        if controller_id is None:
            raise CampaignError(
                "la validación --resume INA226 requiere controller_id"
            )
        energy = result.get("ina226_energy")
        if not isinstance(energy, dict):
            raise CampaignError(
                f"--resume no encontró evidencia INA226: {result_path}"
            )
        quality = energy.get("quality", {})
        workload = energy.get("workload_contract", {})
        current_workload = load_energy_workload_contract(scheduled)
        if (
            energy.get("status") != "accepted_raw_transport"
            or quality.get("transport_valid") is not True
            or energy.get("publication_ready") is not False
            or energy.get("eligible_as_primary_benchmark") is not False
            or energy.get("controller_id") != controller_id
            or energy.get("firmware_clock_profile")
            != expected_clock_binding["firmware_clock_profile"]
            or energy.get("clock_reference_contract")
            != expected_clock_binding["clock_reference_contract"]
            or workload.get("sha256") != current_workload["sha256"]
            or workload.get("multiplier")
            != current_workload["multiplier"]
            or energy.get("work_units")
            != current_workload["work_units"]
        ):
            raise CampaignError(
                "--resume rechazó contrato/calidad INA226: "
                f"{result_path}"
            )
        energy_path = safe_artifact_path(
            run_directory,
            energy.get("raw_jsonl_path"),
        )
        if (
            not energy_path.is_file()
            or sha256_file(energy_path)
            != energy.get("raw_jsonl_sha256")
        ):
            raise CampaignError(
                f"--resume rechazó hash INA226: {energy_path}"
            )
        try:
            header = next(
                json.loads(line)
                for line in energy_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, StopIteration, json.JSONDecodeError) as exc:
            raise CampaignError(
                f"--resume rechazó header INA226 ilegible: {energy_path}"
            ) from exc
        if (
            not isinstance(header, dict)
            or header.get("type") != "header"
            or header.get("timebase", {}).get("controller_id")
            != controller_id
            or header.get("clock_reference_contract")
            != expected_clock_binding["clock_reference_contract"]
        ):
            raise CampaignError(
                "--resume rechazó header/reloj de la captura INA226: "
                f"{energy_path}"
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
    power_cycle_config: PowerCycleConfig | None = None,
) -> dict[str, Any]:
    cell = dict(scheduled)
    benchmark_mode = endpoint == "benchmark_reset_pilot"
    buffered_capture_mode = endpoint == "dense_timeseries_pilot"
    primary_handshake = power_cycle_config is not None
    if primary_handshake and not benchmark_mode:
        raise CampaignError(
            "el power-cycle externo sólo está implementado para "
            "benchmark_reset_pilot"
        )
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
    external_power_cycle = power_cycle_config is not None
    ina226_energy = bool(
        power_cycle_config is not None
        and power_cycle_config.ina226_energy_enabled
    )
    energy_workload = (
        load_energy_workload_contract(cell) if ina226_energy else None
    )
    current_run_id = pilot_run_id(
        scheduled,
        endpoint,
        external_power_cycle=external_power_cycle,
        ina226_energy=ina226_energy,
    )
    run_directory = pilot_run_directory(
        output_root,
        manifest["campaign_id"],
        scheduled,
        endpoint,
        external_power_cycle=external_power_cycle,
        ina226_energy=ina226_energy,
    )
    if run_directory.exists():
        raise CampaignError(
            f"el run_id ya existe y no se sobrescribe: {run_directory}"
        )

    lock_directory = output_root / ".locks"
    board = manifest["boards"][cell["board"]]
    firmware_clock_binding = campaign_firmware_clock_binding(
        manifest,
        cell["board"],
    )
    build_dir = build_directory(
        manifest,
        cell,
        decimation,
        benchmark_mode=benchmark_mode,
        buffered_capture_mode=buffered_capture_mode,
        primary_handshake=primary_handshake,
    )
    source_metadata = git_metadata(
        exclude_generated_paths=(output_root, build_dir),
    )
    with contextlib.ExitStack() as stack:
        lock_names = [
            "campaign-global",
            f"probe-{board['probe_serial']}",
            f"port-{board['port']}",
            f"build-{build_dir}",
        ]
        if power_cycle_config is not None:
            lock_names.extend(
                (
                    f"controller-{power_cycle_config.controller_port}",
                    "power-channel-"
                    f"{power_cycle_config.channel_for(cell['board'])}",
                )
            )
        for lock_name in lock_names:
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
        reset_evidence: dict[str, Any] = {}
        try:
            images = ensure_build(
                manifest,
                cell,
                decimation,
                run_directory / "build.log",
                benchmark_mode=benchmark_mode,
                buffered_capture_mode=buffered_capture_mode,
                primary_handshake=primary_handshake,
                energy_work_multiplier=(
                    int(energy_workload["multiplier"])
                    if energy_workload is not None
                    else 1
                ),
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
                    primary_handshake=primary_handshake,
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
                run_id=current_run_id,
                power_cycle_config=power_cycle_config,
                reset_evidence_out=reset_evidence,
                energy_capture_path=(
                    run_directory / "energy_capture.raw.jsonl"
                    if power_cycle_config is not None
                    and power_cycle_config.ina226_energy_enabled
                    else None
                ),
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
            if reset_evidence:
                summary["reset"] = reset_evidence
                summary["evidence_level"] = (
                    f"{endpoint}_with_validated_external_power_cycle_"
                    "pilot_only_not_primary"
                )
                summary["eligible_as_primary_benchmark"] = False
                summary["eligible_as_paper_cold_start"] = False
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
            runtime_probe: dict[str, Any] | None = None
            if external_power_cycle and benchmark_mode:
                run_process(
                    runtime_probe_command(
                        manifest,
                        cell,
                        decimation,
                        run_directory,
                    ),
                    run_directory / "runtime_probe.log",
                    float(manifest["timeouts_s"]["flash"]),
                )
                runtime_probe = load_runtime_probe_artifacts(
                    run_directory,
                    cell["board"],
                    int(board["system_clock_hz"]),
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
                    "external_power_cycle_pilot_repetition"
                    if power_cycle_config is not None
                    else (
                        "stlink_hardware_reset_repetition"
                        if benchmark_mode
                        else (
                            "dense_buffered_timeseries_acquisition"
                            if buffered_capture_mode
                            else "transport_pilot_acquisition"
                        )
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
                    "manifest_port": board["port"],
                    "port": (
                        reset_evidence.get("usb_reenumeration", {})
                        .get("port_after", {})
                        .get("device", board["port"])
                    ),
                    "baud": board["baud"],
                    "reset_mode": reset_evidence.get(
                        "mode",
                        manifest["reset_contract"]["implemented_mode"],
                    ),
                    "reset_preparation": reset_evidence.get(
                        "preparation",
                        manifest["reset_contract"]["preparation"],
                    ),
                    "power_removed": bool(
                        reset_evidence.get("power_removed", False)
                    ),
                },
                "build": {
                    "directory": str(build_dir),
                    "target": firmware_target(manifest, cell),
                    "decimation": decimation,
                    "firmware_clock_profile": firmware_clock_binding[
                        "firmware_clock_profile"
                    ],
                    "system_clock_hz": firmware_clock_binding[
                        "system_clock_hz"
                    ],
                    "explicit_h755_clock_480_off": firmware_clock_binding[
                        "explicit_h755_clock_480_off"
                    ],
                    "benchmark_mode": benchmark_mode,
                    "buffered_capture_mode": buffered_capture_mode,
                    "primary_handshake": primary_handshake,
                    "energy_marker": benchmark_mode,
                    "energy_work_multiplier": (
                        int(energy_workload["multiplier"])
                        if energy_workload is not None
                        else 1
                    ),
                    "energy_work_units": (
                        int(energy_workload["work_units"])
                        if energy_workload is not None
                        else 10_000
                    ),
                    "energy_workload_contract": energy_workload,
                    "clock_reference": primary_handshake,
                    "runtime_probe": primary_handshake,
                    "primary_handshake_protocol": (
                        POSTBOOT_HANDSHAKE_PROTOCOL
                        if primary_handshake
                        else None
                    ),
                    "buffered_capture_samples": (
                        manifest["build"]["dense_buffered_capture_samples"]
                        if buffered_capture_mode
                        else None
                    ),
                    "calibration_basis": (
                        (
                            "compile_time_kind4_timing_endpoint_with_"
                            "postboot_start_gate"
                        )
                        if primary_handshake
                        else (
                            "compile_time_kind4_timing_endpoint"
                            if benchmark_mode
                            else (
                                "explicit_dense_capture_contract"
                                if buffered_capture_mode
                                else calibration["basis"]
                            )
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
                            primary_handshake=primary_handshake,
                        )
                    ],
                },
                "source": source_metadata,
                "runtime_probe": runtime_probe,
                "ina226_energy": (
                    reset_evidence.get("ina226_energy")
                    if ina226_energy
                    else None
                ),
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
                    "power_cycle_requested": (
                        power_cycle_config is not None
                    ),
                    "power_removed": bool(
                        reset_evidence.get("power_removed", False)
                    ),
                    "reset_evidence": reset_evidence or None,
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
    power_cycle_config = power_cycle_config_from_args(args)
    if power_cycle_config is not None:
        validate_power_cycle_config(power_cycle_config, manifest)
        if args.endpoint not in (
            "benchmark_reset_pilot",
            "primary_benchmark",
        ):
            raise CampaignError(
                "--power-controller-port sólo admite "
                "benchmark_reset_pilot; primary_benchmark permanece bloqueado"
            )
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
        "evidence_level": (
            "dry_run_external_power_cycle_no_physical_evidence"
            if power_cycle_config is not None
            else endpoint_contract["evidence_level"]
        ),
        "declared_endpoint_evidence_level": endpoint_contract[
            "evidence_level"
        ],
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
        "reset_strategy": (
            "external_power_cycle_controller"
            if power_cycle_config is not None
            else manifest["reset_contract"]["implemented_mode"]
        ),
        "primary_handshake_build": (
            power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
        ),
        "primary_handshake_switch": (
            "-PrimaryHandshake"
            if power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
            else None
        ),
        "energy_marker_build": (
            power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
        ),
        "ina226_energy_requested": bool(
            power_cycle_config is not None
            and power_cycle_config.ina226_energy_enabled
        ),
        "ina226_energy_workloads": (
            [
                load_energy_workload_contract(row)
                for row in {
                    str(candidate["cell_id"]): candidate
                    for candidate in selected
                }.values()
            ]
            if power_cycle_config is not None
            and power_cycle_config.ina226_energy_enabled
            else []
        ),
        "clock_reference_build": (
            power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
        ),
        "runtime_probe_build": (
            power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
        ),
        "primary_handshake_build_directory": (
            str(
                build_directory(
                    manifest,
                    selected[0],
                    int(
                        manifest["build"][
                            "benchmark_unused_decimation_value"
                        ]
                    ),
                    benchmark_mode=True,
                    primary_handshake=True,
                )
            )
            if power_cycle_config is not None
            and args.endpoint == "benchmark_reset_pilot"
            else None
        ),
        "power_cycle": (
            power_cycle_config.dry_run_metadata(
                row["board"] for row in selected
            )
            if power_cycle_config is not None
            else {
                "requested": False,
                "power_removed": False,
            }
        ),
        "primary_benchmark_ready": False,
        "first_run_id": (
            pilot_run_id(
                selected[0],
                args.endpoint,
                external_power_cycle=power_cycle_config is not None,
                ina226_energy=bool(
                    power_cycle_config is not None
                    and power_cycle_config.ina226_energy_enabled
                ),
            )
            if args.endpoint != "primary_benchmark"
            else selected[0]["run_id"]
        ),
        "last_run_id": (
            pilot_run_id(
                selected[-1],
                args.endpoint,
                external_power_cycle=power_cycle_config is not None,
                ina226_energy=bool(
                    power_cycle_config is not None
                    and power_cycle_config.ina226_energy_enabled
                ),
            )
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
                external_power_cycle=power_cycle_config is not None,
                ina226_energy=bool(
                    power_cycle_config is not None
                    and power_cycle_config.ina226_energy_enabled
                ),
                controller_id=(
                    power_cycle_config.controller_id
                    if power_cycle_config is not None
                    else None
                ),
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
            power_cycle_config=power_cycle_config,
        )
        results.append(
            {
                "run_id": result["run_id"],
                "transport_accepted": result["transport"]["accepted"],
                "timing_accepted": result["timing"].get(
                    "accepted_as_solver_timing", False
                ),
                "reset_mode": result["hardware"]["reset_mode"],
                "power_removed": result["hardware"]["power_removed"],
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
        "--power-controller-port",
        help=(
            "activa el controlador externo CYCLE/ACK; no desbloquea "
            "primary_benchmark"
        ),
    )
    run.add_argument(
        "--power-controller-baud",
        type=int,
        default=115200,
    )
    run.add_argument(
        "--power-controller-id",
        help=(
            "etiqueta física estable del UNO (por ejemplo UNO_PWR_01); "
            "no use el nombre COM"
        ),
    )
    run.add_argument(
        "--power-off-ms",
        type=int,
        default=2500,
        help="intervalo apagado solicitado al controlador",
    )
    run.add_argument(
        "--power-cycle-timeout",
        type=float,
        default=30.0,
    )
    run.add_argument(
        "--power-cycle-poll-interval",
        type=float,
        default=0.1,
    )
    run.add_argument(
        "--postboot-handshake-timeout",
        type=float,
        default=15.0,
        help=(
            "watchdog para READY después de reabrir el COM por identidad "
            "ST-LINK"
        ),
    )
    run.add_argument(
        "--reenumerated-port-open-timeout",
        type=float,
        default=10.0,
        help=(
            "watchdog para abrir el COM que reaparece, incluso si cambia "
            "su nombre"
        ),
    )
    run.add_argument(
        "--ina226-energy",
        action="store_true",
        help=(
            "arma una captura INA14/1 en el mismo COM del controlador; "
            "permanece no publicable y no desbloquea primary_benchmark"
        ),
    )
    run.add_argument(
        "--confirm-ina226-r100",
        action="store_true",
        help=(
            "confirma que se inspeccionó físicamente la marca R100 del "
            "módulo seleccionado"
        ),
    )
    run.add_argument(
        "--ina226-pre-samples",
        type=int,
        default=128,
    )
    run.add_argument(
        "--ina226-post-samples",
        type=int,
        default=128,
    )
    run.add_argument(
        "--ina226-timeout-ms",
        type=int,
        default=60_000,
    )
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
