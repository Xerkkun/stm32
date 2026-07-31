#!/usr/bin/env python3
"""INA226/1 acquisition codec and single-owner serial session.

The ASCII ``ARM`` command changes an already-open PWRCTL serial session into
the fixed-size binary ``INA14/1`` stream.  Its terminal END frame changes the
controller back to ASCII mode.  The reusable :class:`Ina226Session` never
opens or closes the serial port, allowing the physical-campaign runner to keep
one owner for CYCLE/ACK, ARM, and acquisition.

Raw capture output is auditable input, not a calibrated result.  This module
never marks a capture publication-ready and never treats an asserted R100
token as proof that the physical shunt was inspected.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol


WIRE_PROTOCOL = "INA14/1"
CAPTURE_SCHEMA = "fractional-chaos-ina226-capture-v1"
ENERGY_SCOPE = "whole_nucleo_vbus_including_stlink"
SERIAL_BAUD = 115_200
FRAME_SIZE = 14
FRAME_SYNC = b"\xA5\x5A"
FRAME_TYPE_MASK = 0xC0
FRAME_FLAG_MASK = 0x3F
FRAME_TYPE_SAMPLE = 0x40
FRAME_TYPE_EDGE = 0x80
FRAME_TYPE_END = 0xC0

SAMPLE_CONVERSION_READY = 1 << 0
SAMPLE_MATH_OVERFLOW = 1 << 1
SAMPLE_ENERGY_HIGH = 1 << 2
SAMPLE_I2C_OK = 1 << 3
SAMPLE_ALLOWED_FLAGS = (
    SAMPLE_CONVERSION_READY
    | SAMPLE_MATH_OVERFLOW
    | SAMPLE_ENERGY_HIGH
    | SAMPLE_I2C_OK
)

EDGE_LEVEL = 1 << 0
EDGE_CLOCK_REFERENCE = 1 << 1
EDGE_ALLOWED_FLAGS = EDGE_LEVEL | EDGE_CLOCK_REFERENCE

END_COMPLETE = 1 << 0
END_STOPPED = 1 << 1
END_TIMEOUT = 1 << 2
END_I2C_ERROR = 1 << 3
END_EDGE_OVERFLOW = 1 << 4
END_TIMING_OR_EDGE_ANOMALY = 1 << 5

MIN_IDLE_SAMPLES = 100
MAX_IDLE_SAMPLES = 1_000
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 60_000
EXPECTED_PERIOD_US = 2_000
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CONTROLLER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
COM_PORT_ID_PATTERN = re.compile(r"^COM[0-9]+$", re.IGNORECASE)
CHANNELS = {
    "F746": {
        "board": "f746",
        "sensor_id": "ina226_f746",
        "address": 0x40,
    },
    "H755": {
        "board": "h755",
        "sensor_id": "ina226_h755",
        "address": 0x41,
    },
}
FROZEN_FIRMWARE_CLOCK_PROFILES = {
    "f746_216mhz": {
        "channel": "F746",
        "board": "f746",
        "core_clock_hz": 216_000_000,
        "expected_pulse_s": 0.1,
        "expected_cycles": 21_600_000,
    },
    "h755_400mhz": {
        "channel": "H755",
        "board": "h755",
        "core_clock_hz": 400_000_000,
        "expected_pulse_s": 0.1,
        "expected_cycles": 40_000_000,
    },
}


class InaCaptureError(RuntimeError):
    """Raised when the acquisition transport violates its frozen contract."""


def validate_controller_id(value: str) -> str:
    """Require a stable physical label, never the transient serial port name."""

    if not isinstance(value, str):
        raise InaCaptureError("controller_id must be a string")
    controller_id = value.strip()
    if CONTROLLER_ID_PATTERN.fullmatch(controller_id) is None:
        raise InaCaptureError(
            "controller_id must be a 3-64 character physical label using "
            "letters, digits, '.', '_' or '-'"
        )
    if COM_PORT_ID_PATTERN.fullmatch(controller_id) is not None:
        raise InaCaptureError(
            "controller_id must be a physical label, not a COM port"
        )
    return controller_id


class SerialLike(Protocol):
    """Minimum interface used by :class:`Ina226Session`."""

    def write(self, data: bytes) -> int: ...

    def read(self, size: int = 1) -> bytes: ...

    def readline(self) -> bytes: ...

    def flush(self) -> None: ...


@dataclass(frozen=True)
class ArmedResponse:
    request_id: str
    channel: str
    address: int
    manufacturer_id: int
    die_id: int
    period_us: int
    pre_samples: int
    post_samples: int
    timeout_ms: int
    shunt_milliohms: int
    shunt_marking: str
    frame_protocol: str


@dataclass(frozen=True)
class BinaryFrame:
    kind: str
    flags: int
    sequence: int
    timestamp_us: int
    value_0: int
    value_1: int

    @property
    def shunt_raw(self) -> int:
        """Interpret sample payload word 1 as signed INA226 shunt raw."""

        value = self.value_1
        return value - 0x10000 if value & 0x8000 else value


@dataclass(frozen=True)
class CaptureStream:
    armed: ArmedResponse
    frames: tuple[BinaryFrame, ...]
    discarded_wire_bytes: int
    crc_failures: int

    @property
    def terminal(self) -> BinaryFrame:
        if not self.frames or self.frames[-1].kind != "end":
            raise InaCaptureError("capture has no terminal END frame")
        return self.frames[-1]


def _validate_request_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(
        request_id
    ):
        raise InaCaptureError(
            "request_id must match [A-Za-z0-9_-]{1,64}"
        )
    return request_id


def _canonical_channel(channel: str) -> str:
    if not isinstance(channel, str):
        raise InaCaptureError("channel must be F746 or H755")
    canonical = channel.upper()
    canonical = {"CH1": "F746", "CH2": "H755"}.get(
        canonical, canonical
    )
    if canonical not in CHANNELS:
        raise InaCaptureError("channel must be F746 or H755")
    return canonical


def validate_firmware_clock_profile(
    channel: str,
    firmware_clock_profile: str,
) -> dict[str, Any]:
    """Bind an operator-selected clock profile to one physical channel."""

    canonical_channel = _canonical_channel(channel)
    if (
        not isinstance(firmware_clock_profile, str)
        or firmware_clock_profile not in FROZEN_FIRMWARE_CLOCK_PROFILES
    ):
        raise InaCaptureError(
            "firmware_clock_profile must be one of: "
            + ", ".join(sorted(FROZEN_FIRMWARE_CLOCK_PROFILES))
        )
    profile = FROZEN_FIRMWARE_CLOCK_PROFILES[firmware_clock_profile]
    if profile["channel"] != canonical_channel:
        raise InaCaptureError(
            f"firmware_clock_profile={firmware_clock_profile} does not "
            f"match channel={canonical_channel}"
        )
    return profile


def encode_ina_status(channel: str) -> bytes:
    """Encode an identity/configuration query without entering binary mode."""

    return f"INA_STATUS {_canonical_channel(channel)}\n".encode("ascii")


def encode_ina_read(channel: str) -> bytes:
    """Encode one raw-register read for measured calibration work."""

    return f"INA_READ {_canonical_channel(channel)}\n".encode("ascii")


def encode_arm(
    request_id: str,
    channel: str,
    *,
    pre_samples: int = 128,
    post_samples: int = 128,
    timeout_ms: int = 60_000,
    physically_confirmed_shunt_marking: str,
) -> bytes:
    """Encode ARM and require an explicit physical R100 assertion.

    The token is recorded for auditability but cannot itself verify hardware.
    """

    valid_request_id = _validate_request_id(request_id)
    canonical_channel = _canonical_channel(channel)
    for value, label, lower, upper in (
        (
            pre_samples,
            "pre_samples",
            MIN_IDLE_SAMPLES,
            MAX_IDLE_SAMPLES,
        ),
        (
            post_samples,
            "post_samples",
            MIN_IDLE_SAMPLES,
            MAX_IDLE_SAMPLES,
        ),
        (timeout_ms, "timeout_ms", MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise InaCaptureError(f"{label} must be an integer")
        if not lower <= value <= upper:
            raise InaCaptureError(
                f"{label} must be in [{lower}, {upper}]"
            )
    if physically_confirmed_shunt_marking != "R100":
        raise InaCaptureError(
            "ARM requires physical inspection and the exact R100 marking"
        )
    return (
        f"ARM {valid_request_id} {canonical_channel} "
        f"{pre_samples} {post_samples} {timeout_ms} R100\n"
    ).encode("ascii")


def encode_stop(request_id: str) -> bytes:
    """Encode the only command accepted while the binary stream is active."""

    return f"STOP {_validate_request_id(request_id)}\n".encode("ascii")


def crc8(data: bytes | bytearray | memoryview) -> int:
    """CRC-8/ATM (polynomial 0x07, init 0) used by INA14/1."""

    checksum = 0
    for value in data:
        checksum ^= value
        for _ in range(8):
            checksum = (
                ((checksum << 1) ^ 0x07) & 0xFF
                if checksum & 0x80
                else (checksum << 1) & 0xFF
            )
    return checksum


def encode_frame(
    frame_type: int,
    *,
    flags: int,
    sequence: int,
    timestamp_us: int,
    value_0: int = 0,
    value_1: int = 0,
) -> bytes:
    """Encode one frame; public primarily for simulators and tests."""

    if frame_type not in {
        FRAME_TYPE_SAMPLE,
        FRAME_TYPE_EDGE,
        FRAME_TYPE_END,
    }:
        raise InaCaptureError("invalid frame type")
    for value, label, upper in (
        (flags, "flags", FRAME_FLAG_MASK),
        (sequence, "sequence", 0xFFFF),
        (timestamp_us, "timestamp_us", 0xFFFFFFFF),
        (value_0, "value_0", 0xFFFF),
        (value_1, "value_1", 0xFFFF),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise InaCaptureError(f"{label} must be an integer")
        if not 0 <= value <= upper:
            raise InaCaptureError(f"{label} is outside its wire range")
    frame = bytearray(
        struct.pack(
            "<2sBHIHH",
            FRAME_SYNC,
            frame_type | flags,
            sequence,
            timestamp_us,
            value_0,
            value_1,
        )
    )
    frame.append(crc8(frame))
    if len(frame) != FRAME_SIZE:
        raise AssertionError("INA14/1 frame encoder size drifted")
    return bytes(frame)


def decode_frame(payload: bytes) -> BinaryFrame:
    """Decode and validate exactly one fixed-size INA14/1 frame."""

    if len(payload) != FRAME_SIZE:
        raise InaCaptureError("INA14/1 frame must contain exactly 14 bytes")
    if payload[:2] != FRAME_SYNC:
        raise InaCaptureError("INA14/1 sync word mismatch")
    if crc8(payload[:-1]) != payload[-1]:
        raise InaCaptureError("INA14/1 CRC mismatch")
    _, kind_and_flags, sequence, timestamp_us, value_0, value_1 = (
        struct.unpack("<2sBHIHH", payload[:-1])
    )
    frame_type = kind_and_flags & FRAME_TYPE_MASK
    flags = kind_and_flags & FRAME_FLAG_MASK
    if frame_type == FRAME_TYPE_SAMPLE:
        kind = "sample"
        allowed_flags = SAMPLE_ALLOWED_FLAGS
    elif frame_type == FRAME_TYPE_EDGE:
        kind = "edge"
        allowed_flags = EDGE_ALLOWED_FLAGS
        if value_0 != 0 or value_1 != 0:
            raise InaCaptureError("edge payload words must be zero")
    elif frame_type == FRAME_TYPE_END:
        kind = "end"
        allowed_flags = FRAME_FLAG_MASK
    else:
        raise InaCaptureError("INA14/1 frame type is reserved")
    if flags & ~allowed_flags:
        raise InaCaptureError(f"{kind} frame contains reserved flags")
    return BinaryFrame(
        kind=kind,
        flags=flags,
        sequence=sequence,
        timestamp_us=timestamp_us,
        value_0=value_0,
        value_1=value_1,
    )


class FrameDecoder:
    """Incremental decoder that resynchronizes after visible wire corruption."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.discarded_wire_bytes = 0
        self.crc_failures = 0

    @property
    def pending_bytes(self) -> bytes:
        return bytes(self._buffer)

    def feed(self, data: bytes) -> list[BinaryFrame]:
        if not isinstance(data, bytes):
            raise InaCaptureError("binary stream chunks must be bytes")
        self._buffer.extend(data)
        frames: list[BinaryFrame] = []
        while True:
            sync_index = self._buffer.find(FRAME_SYNC)
            if sync_index < 0:
                keep = 1 if self._buffer.endswith(FRAME_SYNC[:1]) else 0
                discard = len(self._buffer) - keep
                if discard > 0:
                    del self._buffer[:discard]
                    self.discarded_wire_bytes += discard
                break
            if sync_index > 0:
                del self._buffer[:sync_index]
                self.discarded_wire_bytes += sync_index
            if len(self._buffer) < FRAME_SIZE:
                break
            candidate = bytes(self._buffer[:FRAME_SIZE])
            if crc8(candidate[:-1]) != candidate[-1]:
                del self._buffer[0]
                self.discarded_wire_bytes += 1
                self.crc_failures += 1
                continue
            frames.append(decode_frame(candidate))
            del self._buffer[:FRAME_SIZE]
        return frames


def _decode_ascii_line(line: str | bytes) -> str:
    if isinstance(line, bytes):
        try:
            decoded = line.decode("ascii")
        except UnicodeDecodeError as error:
            raise InaCaptureError("controller response is not ASCII") from error
    elif isinstance(line, str):
        decoded = line
    else:
        raise InaCaptureError("controller response must be str or bytes")
    return decoded.rstrip("\r\n")


def _parse_fields(tokens: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for token in tokens:
        if token.count("=") != 1:
            raise InaCaptureError(f"invalid response field {token!r}")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise InaCaptureError(f"invalid response field {token!r}")
        fields[key] = value
    return fields


def parse_armed(line: str | bytes) -> ArmedResponse:
    """Parse the ASCII boundary line immediately preceding binary frames."""

    payload = _decode_ascii_line(line)
    if (
        not payload
        or payload != payload.strip()
        or "\t" in payload
        or "  " in payload
    ):
        raise InaCaptureError("ARMED line uses non-canonical whitespace")
    tokens = payload.split(" ")
    if len(tokens) < 4 or tokens[0] != "ARMED":
        raise InaCaptureError("expected ARMED response")
    request_id = _validate_request_id(tokens[1])
    channel = _canonical_channel(tokens[2])
    if channel != tokens[2]:
        raise InaCaptureError("ARMED channel must be canonical")
    fields = _parse_fields(tokens[3:])
    expected_keys = {
        "INACAP",
        "ADDR",
        "MFG",
        "DIE",
        "PERIOD_US",
        "PRE",
        "POST",
        "TIMEOUT_MS",
        "SHUNT_MOHM",
        "SHUNT_MARKING",
        "FRAME",
    }
    if set(fields) != expected_keys:
        raise InaCaptureError("ARMED response fields do not match INA14/1")
    try:
        response = ArmedResponse(
            request_id=request_id,
            channel=channel,
            address=int(fields["ADDR"], 0),
            manufacturer_id=int(fields["MFG"], 0),
            die_id=int(fields["DIE"], 0),
            period_us=int(fields["PERIOD_US"], 10),
            pre_samples=int(fields["PRE"], 10),
            post_samples=int(fields["POST"], 10),
            timeout_ms=int(fields["TIMEOUT_MS"], 10),
            shunt_milliohms=int(fields["SHUNT_MOHM"], 10),
            shunt_marking=fields["SHUNT_MARKING"],
            frame_protocol=fields["FRAME"],
        )
    except ValueError as error:
        raise InaCaptureError("ARMED contains an invalid integer") from error
    expected_address = int(CHANNELS[channel]["address"])
    if fields["INACAP"] != "1":
        raise InaCaptureError("unsupported INACAP protocol version")
    if response.address != expected_address:
        raise InaCaptureError("ARMED address does not match channel contract")
    if response.manufacturer_id != 0x5449 or response.die_id != 0x2260:
        raise InaCaptureError("ARMED identity is not INA226-compatible")
    if response.period_us != EXPECTED_PERIOD_US:
        raise InaCaptureError("ARMED sample period is not the audited 500 Hz")
    if not MIN_IDLE_SAMPLES <= response.pre_samples <= MAX_IDLE_SAMPLES:
        raise InaCaptureError("ARMED PRE is outside the capture contract")
    if not MIN_IDLE_SAMPLES <= response.post_samples <= MAX_IDLE_SAMPLES:
        raise InaCaptureError("ARMED POST is outside the capture contract")
    if not MIN_TIMEOUT_MS <= response.timeout_ms <= MAX_TIMEOUT_MS:
        raise InaCaptureError(
            "ARMED TIMEOUT_MS is outside the capture contract"
        )
    if response.shunt_milliohms != 100 or response.shunt_marking != "R100":
        raise InaCaptureError("ARMED does not carry the R100 contract")
    if response.frame_protocol != WIRE_PROTOCOL:
        raise InaCaptureError("unsupported binary frame protocol")
    return response


def parse_ina_status(line: str | bytes) -> dict[str, str]:
    payload = _decode_ascii_line(line)
    tokens = payload.split(" ")
    if not tokens or tokens[0] != "INASTATUS":
        raise InaCaptureError("expected INASTATUS response")
    fields = _parse_fields(tokens[1:])
    if fields.get("INACAP") != "1" or fields.get("FRAME") != WIRE_PROTOCOL:
        raise InaCaptureError("unsupported INA acquisition status")
    return fields


def parse_ina_read(line: str | bytes) -> dict[str, int | str | bool]:
    payload = _decode_ascii_line(line)
    tokens = payload.split(" ")
    if not tokens or tokens[0] != "INAREAD":
        raise InaCaptureError("expected INAREAD response")
    fields = _parse_fields(tokens[1:])
    expected = {
        "CHANNEL",
        "ADDR",
        "TIMESTAMP_US",
        "BUS_RAW",
        "SHUNT_RAW",
        "CNVR",
        "OVF",
    }
    if set(fields) != expected:
        raise InaCaptureError("INAREAD response fields do not match contract")
    channel = _canonical_channel(fields["CHANNEL"])
    if channel != fields["CHANNEL"]:
        raise InaCaptureError("INAREAD channel must be canonical")
    if fields["CNVR"] not in {"0", "1"} or fields["OVF"] not in {"0", "1"}:
        raise InaCaptureError("INAREAD flags must be 0 or 1")
    try:
        result: dict[str, int | str | bool] = {
            "channel": channel,
            "address": int(fields["ADDR"], 0),
            "timestamp_us": int(fields["TIMESTAMP_US"], 10),
            "bus_raw": int(fields["BUS_RAW"], 10),
            "shunt_raw": int(fields["SHUNT_RAW"], 10),
            "conversion_ready": fields["CNVR"] == "1",
            "math_overflow": fields["OVF"] == "1",
        }
    except ValueError as error:
        raise InaCaptureError("INAREAD contains an invalid integer") from error
    if result["address"] != CHANNELS[channel]["address"]:
        raise InaCaptureError("INAREAD address does not match channel")
    if not 0 <= int(result["timestamp_us"]) <= 0xFFFFFFFF:
        raise InaCaptureError("INAREAD timestamp is outside uint32")
    if not 0 <= int(result["bus_raw"]) <= 0xFFFF:
        raise InaCaptureError("INAREAD bus raw is outside uint16")
    if not -32768 <= int(result["shunt_raw"]) <= 32767:
        raise InaCaptureError("INAREAD shunt raw is outside int16")
    return result


class Ina226Session:
    """Operate INA acquisition on a serial object owned by the caller.

    The caller may use the same serial object for PWRCTL CYCLE/ACK before
    :meth:`arm`.  Once ``ARMED`` is returned, only ``STOP <request_id>`` may be
    transmitted until :meth:`read_capture` consumes the terminal END frame.
    """

    def __init__(self, serial_port: SerialLike) -> None:
        self.serial = serial_port
        self._armed: ArmedResponse | None = None
        self._decoder: FrameDecoder | None = None
        self._frames: list[BinaryFrame] = []

    def _write(self, payload: bytes) -> None:
        written = self.serial.write(payload)
        if written != len(payload):
            raise InaCaptureError("serial write was incomplete")
        self.serial.flush()

    def _read_ascii_response(self, expected_prefix: str) -> bytes:
        for _ in range(12):
            line = self.serial.readline()
            if not line:
                continue
            stripped = line.rstrip(b"\r\n")
            if stripped.startswith(b"ERR "):
                raise InaCaptureError(
                    f"controller rejected command: "
                    f"{_decode_ascii_line(stripped)}"
                )
            if stripped.startswith(expected_prefix.encode("ascii")):
                return line
            if stripped.startswith(b"STATUS "):
                continue
            raise InaCaptureError(
                f"unexpected ASCII controller line: "
                f"{_decode_ascii_line(stripped)!r}"
            )
        raise InaCaptureError(
            f"controller did not return {expected_prefix} response"
        )

    def status(self, channel: str) -> dict[str, str]:
        if self._armed is not None:
            raise InaCaptureError("binary capture is already active")
        self._write(encode_ina_status(channel))
        return parse_ina_status(self._read_ascii_response("INASTATUS "))

    def read_calibration_point(
        self,
        channel: str,
    ) -> dict[str, int | str | bool]:
        """Read raw registers once; the reference instrument remains external."""

        if self._armed is not None:
            raise InaCaptureError("binary capture is already active")
        self._write(encode_ina_read(channel))
        return parse_ina_read(self._read_ascii_response("INAREAD "))

    def arm(
        self,
        request_id: str,
        channel: str,
        *,
        pre_samples: int = 128,
        post_samples: int = 128,
        timeout_ms: int = 60_000,
        physically_confirmed_shunt_marking: str,
    ) -> ArmedResponse:
        if self._armed is not None:
            raise InaCaptureError("binary capture is already active")
        command = encode_arm(
            request_id,
            channel,
            pre_samples=pre_samples,
            post_samples=post_samples,
            timeout_ms=timeout_ms,
            physically_confirmed_shunt_marking=(
                physically_confirmed_shunt_marking
            ),
        )
        self._write(command)
        armed = parse_armed(self._read_ascii_response("ARMED "))
        if (
            armed.request_id != request_id
            or armed.channel != _canonical_channel(channel)
            or armed.pre_samples != pre_samples
            or armed.post_samples != post_samples
            or armed.timeout_ms != timeout_ms
        ):
            raise InaCaptureError("ARMED response does not echo the request")
        self._armed = armed
        self._decoder = FrameDecoder()
        self._frames = []
        return armed

    def stop(self) -> None:
        if self._armed is None:
            raise InaCaptureError("no binary capture is active")
        self._write(encode_stop(self._armed.request_id))

    @property
    def sample_frames_received(self) -> int:
        return sum(frame.kind == "sample" for frame in self._frames)

    def poll_capture(
        self,
        *,
        maximum: int = 1_024,
    ) -> CaptureStream | None:
        """Consume currently available stream bytes without owning the loop.

        This is the runner integration point: alternate it with reads from the
        Nucleo COM so neither OS serial buffer must hold an entire benchmark.
        """

        if self._armed is None or self._decoder is None:
            raise InaCaptureError("ARM must succeed before polling frames")
        if maximum < FRAME_SIZE:
            raise InaCaptureError("maximum must hold at least one frame")
        waiting = int(getattr(self.serial, "in_waiting", 0) or 0)
        chunk = self.serial.read(min(max(waiting, 1), maximum))
        if not chunk:
            return None
        decoded = self._decoder.feed(bytes(chunk))
        self._frames.extend(decoded)
        end_positions = [
            index
            for index, frame in enumerate(self._frames)
            if frame.kind == "end"
        ]
        if not end_positions:
            return None
        if end_positions != [len(self._frames) - 1]:
            self._clear_capture_state()
            raise InaCaptureError("frames appeared after terminal END")
        if self._decoder.pending_bytes:
            self._clear_capture_state()
            raise InaCaptureError("wire bytes remained after terminal END")
        stream = CaptureStream(
            armed=self._armed,
            frames=tuple(self._frames),
            discarded_wire_bytes=self._decoder.discarded_wire_bytes,
            crc_failures=self._decoder.crc_failures,
        )
        self._clear_capture_state()
        return stream

    def _clear_capture_state(self) -> None:
        self._armed = None
        self._decoder = None
        self._frames = []

    def read_capture(
        self,
        *,
        host_timeout_s: float | None = None,
        chunk_size: int = 64,
    ) -> CaptureStream:
        if self._armed is None:
            raise InaCaptureError("ARM must succeed before reading frames")
        if chunk_size < FRAME_SIZE:
            raise InaCaptureError("chunk_size must hold at least one frame")
        armed = self._armed
        timeout_s = (
            host_timeout_s
            if host_timeout_s is not None
            else armed.timeout_ms / 1000.0 + 5.0
        )
        if timeout_s <= 0:
            raise InaCaptureError("host_timeout_s must be positive")

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            completed = self.poll_capture(maximum=chunk_size)
            if completed is not None:
                return completed
        self._clear_capture_state()
        raise InaCaptureError("timed out before terminal INA14/1 END frame")


class _TimestampUnwrapper:
    def __init__(self) -> None:
        self._last_raw: int | None = None
        self._epoch = 0

    def nanoseconds(self, raw_us: int) -> int:
        wrapped = False
        if (
            self._last_raw is not None
            and raw_us < self._last_raw
            and self._last_raw - raw_us > 0x80000000
        ):
            self._epoch += 1
            wrapped = True
        if (
            self._last_raw is None
            or wrapped
            or raw_us > self._last_raw
        ):
            self._last_raw = raw_us
        return ((self._epoch << 32) + raw_us) * 1_000


def build_capture_records(
    stream: CaptureStream,
    *,
    capture_id: str,
    run_id: str,
    controller_id: str,
    firmware_clock_profile: str,
    work_units: int,
    work_unit_label: str = "solver_step",
    physically_verified_shunt_marking: str,
) -> list[dict[str, Any]]:
    """Convert measured wire frames into the JSONL consumed by energy.py."""

    for value, label in (
        (capture_id, "capture_id"),
        (run_id, "run_id"),
        (work_unit_label, "work_unit_label"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise InaCaptureError(f"{label} must be a non-empty string")
    if isinstance(work_units, bool) or not isinstance(work_units, int):
        raise InaCaptureError("work_units must be an integer")
    if work_units <= 0:
        raise InaCaptureError("work_units must be positive")
    if physically_verified_shunt_marking != "R100":
        raise InaCaptureError(
            "JSONL creation requires a physically verified R100 marking"
        )
    physical_controller_id = validate_controller_id(controller_id)

    channel = _canonical_channel(stream.armed.channel)
    channel_metadata = CHANNELS[channel]
    clock_profile = validate_firmware_clock_profile(
        channel,
        firmware_clock_profile,
    )
    header: dict[str, Any] = {
        "type": "header",
        "schema": CAPTURE_SCHEMA,
        "schema_version": 1,
        "capture_id": capture_id,
        "run_id": run_id,
        "sensor_id": channel_metadata["sensor_id"],
        "board": channel_metadata["board"],
        "i2c_address": stream.armed.address,
        "energy_scope": ENERGY_SCOPE,
        "work_units": work_units,
        "work_unit_label": work_unit_label,
        "evidence_status": "measured_raw_unvalidated",
        "publication_ready": False,
        "clock_reference_contract": {
            "source": "host_selected_frozen_campaign_profile",
            "profile_is_measurement": False,
            "profile": firmware_clock_profile,
            "board": clock_profile["board"],
            "core_clock_hz": clock_profile["core_clock_hz"],
            "expected_pulse_s": clock_profile["expected_pulse_s"],
            "expected_cycles": clock_profile["expected_cycles"],
        },
        "timebase": {
            "source": "arduino_uno_micros",
            "controller_id": physical_controller_id,
            "tick_ns": 1_000,
            "wrap_unwrapped_by_host": True,
            "not_host_wall_clock": True,
        },
        "acquisition": {
            "wire_protocol": WIRE_PROTOCOL,
            "serial_baud": SERIAL_BAUD,
            "sample_period_us": stream.armed.period_us,
            "sample_rate_hz": 1_000_000 / stream.armed.period_us,
            "one_sensor_at_a_time": True,
            "ina226_mode": "continuous_shunt_and_bus",
            "shunt_conversion_us": 140,
            "bus_conversion_us": 140,
            "averaging": 1,
            "manufacturer_id": stream.armed.manufacturer_id,
            "die_id": stream.armed.die_id,
            "ids_establish_compatibility_not_authenticity": True,
            "shunt_marking_physically_verified": "R100",
            "shunt_nominal_ohms": 0.1,
        },
        "marker_inputs": {
            "energy_window": (
                "D2_from_F746_PE0_D34"
                if stream.armed.channel == "F746"
                else "D3_from_H755_PE0_D34"
            ),
            "clock_reference": (
                "D4_from_F746_PA0_D32"
                if stream.armed.channel == "F746"
                else "D5_from_H755_PA0_D32"
            ),
        },
        "quality_contract": {
            "expected_sample_period_ns": stream.armed.period_us * 1_000,
            "min_window_samples": 1_000,
            "max_gap_periods": 2.0,
            "max_loss_percent": 0.1,
            "min_window_s": 2.0,
            "clock_reference_must_finish_before_energy_window": True,
            "min_bus_voltage_v": 4.75,
            "saturation_guard_fraction": 0.9,
            "min_idle_samples_each_side": MIN_IDLE_SAMPLES,
            "max_idle_drift_percent": 2.0,
        },
    }
    records: list[dict[str, Any]] = [header]
    unwrapper = _TimestampUnwrapper()
    sample_count = 0
    energy_edge_count = 0
    clock_edge_count = 0
    terminal: BinaryFrame | None = None
    for frame in stream.frames:
        timestamp_ns = unwrapper.nanoseconds(frame.timestamp_us)
        if frame.kind == "sample":
            sample_count += 1
            records.append(
                {
                    "type": "sample",
                    "timestamp_ns": timestamp_ns,
                    "sequence": frame.sequence,
                    "bus_raw": frame.value_0,
                    "shunt_raw": frame.shunt_raw,
                    "conversion_ready": bool(
                        frame.flags & SAMPLE_CONVERSION_READY
                    ),
                    "math_overflow": bool(
                        frame.flags & SAMPLE_MATH_OVERFLOW
                    ),
                    "i2c_ok": bool(frame.flags & SAMPLE_I2C_OK),
                    "energy_marker_level": (
                        1 if frame.flags & SAMPLE_ENERGY_HIGH else 0
                    ),
                    "wire_timestamp_us": frame.timestamp_us,
                }
            )
        elif frame.kind == "edge":
            is_clock = bool(frame.flags & EDGE_CLOCK_REFERENCE)
            marker_kind = (
                "clock_reference" if is_clock else "energy_window"
            )
            if is_clock:
                clock_edge_count += 1
            else:
                energy_edge_count += 1
            records.append(
                {
                    "type": "edge",
                    "timestamp_ns": timestamp_ns,
                    "sequence_at_interrupt": frame.sequence,
                    "level": 1 if frame.flags & EDGE_LEVEL else 0,
                    "marker_kind": marker_kind,
                    "wire_timestamp_us": frame.timestamp_us,
                }
            )
        elif frame.kind == "end":
            terminal = frame

    if terminal is None:
        raise InaCaptureError("capture has no END frame")
    records.append(
        {
            "type": "end",
            "timestamp_ns": unwrapper.nanoseconds(terminal.timestamp_us),
            "complete": bool(terminal.flags & END_COMPLETE),
            "stopped": bool(terminal.flags & END_STOPPED),
            "timed_out": bool(terminal.flags & END_TIMEOUT),
            "i2c_error_flag": bool(terminal.flags & END_I2C_ERROR),
            "edge_queue_overflow": bool(
                terminal.flags & END_EDGE_OVERFLOW
            ),
            "timing_or_edge_anomaly": bool(
                terminal.flags & END_TIMING_OR_EDGE_ANOMALY
            ),
            "i2c_error_count": terminal.value_0,
            "late_sample_slots": terminal.value_1,
            "sample_count": sample_count,
            "energy_edge_count": energy_edge_count,
            "clock_edge_count": clock_edge_count,
            "discarded_wire_bytes": stream.discarded_wire_bytes,
            "crc_failures": stream.crc_failures,
            "publication_ready": False,
        }
    )
    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
            stream.write("\n")


def _open_serial(port: str) -> Any:
    try:
        import serial  # type: ignore
    except ImportError as error:
        raise InaCaptureError(
            "pyserial is required by the standalone CLI"
        ) from error
    return serial.Serial(
        port,
        SERIAL_BAUD,
        timeout=0.2,
        write_timeout=2.0,
    )


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "INA226 raw acquisition through the PWRCTL Arduino UNO. "
            "Opening an UNO port can reset it; campaign code should reuse "
            "Ina226Session with its already-open controller serial object."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser(
        "status", help="read INA226 identity/configuration"
    )
    status_parser.add_argument("--port", required=True)
    status_parser.add_argument(
        "--channel", required=True, choices=sorted(CHANNELS)
    )

    read_parser = subparsers.add_parser(
        "read",
        help="collect raw register points for comparison to a reference meter",
    )
    read_parser.add_argument("--port", required=True)
    read_parser.add_argument(
        "--channel", required=True, choices=sorted(CHANNELS)
    )
    read_parser.add_argument("--samples", type=int, default=16)
    read_parser.add_argument("--interval-ms", type=int, default=10)

    capture_parser = subparsers.add_parser(
        "capture",
        help="arm one sensor and wait for energy/clock marker edges",
    )
    capture_parser.add_argument("--port", required=True)
    capture_parser.add_argument(
        "--channel", required=True, choices=sorted(CHANNELS)
    )
    capture_parser.add_argument("--request-id", required=True)
    capture_parser.add_argument("--capture-id", required=True)
    capture_parser.add_argument("--run-id", required=True)
    capture_parser.add_argument(
        "--firmware-clock-profile",
        required=True,
        choices=sorted(FROZEN_FIRMWARE_CLOCK_PROFILES),
        help="frozen firmware clock profile matching the selected channel",
    )
    capture_parser.add_argument(
        "--controller-id",
        required=True,
        help="stable physical label on the UNO, for example UNO_PWR_01",
    )
    capture_parser.add_argument("--work-units", type=int, default=10_000)
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--pre-samples", type=int, default=128)
    capture_parser.add_argument("--post-samples", type=int, default=128)
    capture_parser.add_argument("--timeout-ms", type=int, default=60_000)
    capture_parser.add_argument(
        "--confirm-r100",
        action="store_true",
        required=True,
        help="assert that the selected module was physically inspected as R100",
    )
    return parser


def main() -> int:
    args = _build_cli().parse_args()
    try:
        if args.command == "capture":
            validate_firmware_clock_profile(
                args.channel,
                args.firmware_clock_profile,
            )
        with _open_serial(args.port) as serial_port:
            # A classic UNO usually resets when DTR is asserted on open.
            time.sleep(2.0)
            session = Ina226Session(serial_port)
            if args.command == "status":
                print(
                    json.dumps(
                        session.status(args.channel),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0

            if args.command == "read":
                if not 1 <= args.samples <= 1_000:
                    raise InaCaptureError("--samples must be in [1, 1000]")
                if not 0 <= args.interval_ms <= 60_000:
                    raise InaCaptureError(
                        "--interval-ms must be in [0, 60000]"
                    )
                points = []
                for index in range(args.samples):
                    points.append(
                        session.read_calibration_point(args.channel)
                    )
                    if index + 1 < args.samples:
                        time.sleep(args.interval_ms / 1000.0)
                print(
                    json.dumps(
                        {
                            "status": "measured_raw_unreferenced",
                            "publication_ready": False,
                            "reference_value_must_be_recorded_separately": True,
                            "points": points,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0

            armed = session.arm(
                args.request_id,
                args.channel,
                pre_samples=args.pre_samples,
                post_samples=args.post_samples,
                timeout_ms=args.timeout_ms,
                physically_confirmed_shunt_marking="R100",
            )
            print(
                f"ARMED {armed.channel} at 500 Hz; "
                "trigger the Nucleo START handshake now.",
                file=sys.stderr,
            )
            capture = session.read_capture()
            records = build_capture_records(
                capture,
                capture_id=args.capture_id,
                run_id=args.run_id,
                controller_id=args.controller_id,
                firmware_clock_profile=args.firmware_clock_profile,
                work_units=args.work_units,
                physically_verified_shunt_marking="R100",
            )
            write_jsonl(args.output.resolve(), records)
            print(args.output.resolve())
            return 0 if capture.terminal.flags & END_COMPLETE else 2
    except InaCaptureError as error:
        print(f"INA capture error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
