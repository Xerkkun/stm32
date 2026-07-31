from __future__ import annotations

import sys
from pathlib import Path

import pytest


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT_ROOT))

from host.ina226_capture import (  # noqa: E402
    EDGE_CLOCK_REFERENCE,
    EDGE_LEVEL,
    END_COMPLETE,
    EXPECTED_PERIOD_US,
    FRAME_TYPE_EDGE,
    FRAME_TYPE_END,
    FRAME_TYPE_SAMPLE,
    SAMPLE_CONVERSION_READY,
    SAMPLE_I2C_OK,
    CaptureStream,
    FrameDecoder,
    Ina226Session,
    InaCaptureError,
    _build_cli,
    build_capture_records,
    crc8,
    decode_frame,
    encode_arm,
    encode_frame,
    encode_ina_read,
    encode_ina_status,
    encode_stop,
    parse_armed,
    parse_ina_read,
    validate_firmware_clock_profile,
)


ARMED_LINE = (
    b"ARMED cap-001 F746 INACAP=1 ADDR=0x40 MFG=0x5449 "
    b"DIE=0x2260 PERIOD_US=2000 PRE=128 POST=128 "
    b"TIMEOUT_MS=60000 SHUNT_MOHM=100 SHUNT_MARKING=R100 "
    b"FRAME=INA14/1\n"
)
H755_ARMED_LINE = ARMED_LINE.replace(
    b"F746 INACAP=1 ADDR=0x40",
    b"H755 INACAP=1 ADDR=0x41",
)


class FakeSerial:
    def __init__(
        self,
        *,
        lines: list[bytes] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.lines = list(lines or [])
        self.chunks = list(chunks or [])
        self.writes: list[bytes] = []
        self.flush_count = 0

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def read(self, size: int = 1) -> bytes:
        del size
        return self.chunks.pop(0) if self.chunks else b""

    def flush(self) -> None:
        self.flush_count += 1


def sample_frame(
    sequence: int,
    timestamp_us: int,
    *,
    shunt_raw: int = 100,
) -> bytes:
    return encode_frame(
        FRAME_TYPE_SAMPLE,
        flags=SAMPLE_CONVERSION_READY | SAMPLE_I2C_OK,
        sequence=sequence,
        timestamp_us=timestamp_us,
        value_0=4000,
        value_1=shunt_raw & 0xFFFF,
    )


def test_ascii_commands_are_canonical_and_require_physical_r100() -> None:
    assert encode_ina_status("ch1") == b"INA_STATUS F746\n"
    assert encode_ina_read("ch2") == b"INA_READ H755\n"
    assert encode_stop("cap-001") == b"STOP cap-001\n"
    assert encode_arm(
        "cap-001",
        "f746",
        physically_confirmed_shunt_marking="R100",
    ) == b"ARM cap-001 F746 128 128 60000 R100\n"

    with pytest.raises(InaCaptureError, match="physical inspection"):
        encode_arm(
            "cap-001",
            "F746",
            physically_confirmed_shunt_marking="R010",
        )


def test_frame_codec_preserves_signed_raw_and_crc() -> None:
    payload = sample_frame(17, 123_456, shunt_raw=-321)

    assert len(payload) == 14
    assert payload[-1] == crc8(payload[:-1])
    decoded = decode_frame(payload)
    assert decoded.kind == "sample"
    assert decoded.sequence == 17
    assert decoded.timestamp_us == 123_456
    assert decoded.value_0 == 4000
    assert decoded.shunt_raw == -321


def test_incremental_decoder_resynchronizes_and_reports_crc_failure() -> None:
    valid = sample_frame(3, 10_000)
    corrupted = bytearray(sample_frame(2, 8_000))
    corrupted[9] ^= 0x01
    decoder = FrameDecoder()

    frames = []
    wire = b"\x00\x11" + bytes(corrupted) + valid
    for chunk in (wire[:5], wire[5:19], wire[19:]):
        frames.extend(decoder.feed(chunk))

    assert [frame.sequence for frame in frames] == [3]
    assert decoder.crc_failures == 1
    assert decoder.discarded_wire_bytes >= 3
    assert decoder.pending_bytes == b""


def test_parse_armed_freezes_address_rate_identity_and_r100() -> None:
    response = parse_armed(ARMED_LINE)

    assert response.request_id == "cap-001"
    assert response.channel == "F746"
    assert response.address == 0x40
    assert response.period_us == EXPECTED_PERIOD_US
    assert response.shunt_milliohms == 100

    with pytest.raises(InaCaptureError, match="500 Hz"):
        parse_armed(ARMED_LINE.replace(b"PERIOD_US=2000", b"PERIOD_US=2500"))


def test_parse_calibration_read_keeps_raw_signed_and_status() -> None:
    result = parse_ina_read(
        "INAREAD CHANNEL=H755 ADDR=0x41 TIMESTAMP_US=12345 "
        "BUS_RAW=4001 SHUNT_RAW=-72 CNVR=1 OVF=0"
    )

    assert result == {
        "channel": "H755",
        "address": 0x41,
        "timestamp_us": 12345,
        "bus_raw": 4001,
        "shunt_raw": -72,
        "conversion_ready": True,
        "math_overflow": False,
    }


def test_reusable_session_uses_existing_serial_and_stops_at_binary_end() -> None:
    frames = b"".join(
        [
            sample_frame(0, 1_000),
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_LEVEL,
                sequence=1,
                timestamp_us=2_500,
            ),
            sample_frame(1, 3_000),
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=0,
                sequence=2,
                timestamp_us=4_500,
            ),
            encode_frame(
                FRAME_TYPE_END,
                flags=END_COMPLETE,
                sequence=2,
                timestamp_us=6_000,
            ),
        ]
    )
    fake = FakeSerial(
        lines=[b"STATUS PWRCTL=1 STATE=BOOT_FAIL_OFF\n", ARMED_LINE],
        chunks=[frames[:31], frames[31:]],
    )
    session = Ina226Session(fake)

    armed = session.arm(
        "cap-001",
        "F746",
        physically_confirmed_shunt_marking="R100",
    )
    capture = session.read_capture(host_timeout_s=1.0)

    assert armed == capture.armed
    assert capture.terminal.flags & END_COMPLETE
    assert fake.writes == [b"ARM cap-001 F746 128 128 60000 R100\n"]
    assert fake.flush_count == 1


def test_jsonl_records_label_clock_separately_and_never_promote_capture() -> None:
    armed = parse_armed(ARMED_LINE)
    frames = (
        decode_frame(sample_frame(0, 1_000, shunt_raw=-10)),
        decode_frame(
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_CLOCK_REFERENCE | EDGE_LEVEL,
                sequence=1,
                timestamp_us=1_500,
            )
        ),
        decode_frame(
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_CLOCK_REFERENCE,
                sequence=1,
                timestamp_us=1_700,
            )
        ),
        decode_frame(
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=EDGE_LEVEL,
                sequence=1,
                timestamp_us=2_000,
            )
        ),
        decode_frame(sample_frame(1, 3_000)),
        decode_frame(
            encode_frame(
                FRAME_TYPE_EDGE,
                flags=0,
                sequence=2,
                timestamp_us=4_000,
            )
        ),
        decode_frame(
            encode_frame(
                FRAME_TYPE_END,
                flags=END_COMPLETE,
                sequence=2,
                timestamp_us=5_000,
            )
        ),
    )
    records = build_capture_records(
        CaptureStream(
            armed=armed,
            frames=frames,
            discarded_wire_bytes=0,
            crc_failures=0,
        ),
        capture_id="capture-001",
        run_id="run-001",
        controller_id="UNO_PWR_TEST",
        firmware_clock_profile="f746_216mhz",
        work_units=10_000,
        physically_verified_shunt_marking="R100",
    )

    assert records[0]["publication_ready"] is False
    assert records[0]["timebase"]["controller_id"] == "UNO_PWR_TEST"
    assert records[0]["clock_reference_contract"] == {
        "source": "host_selected_frozen_campaign_profile",
        "profile_is_measurement": False,
        "profile": "f746_216mhz",
        "board": "f746",
        "core_clock_hz": 216_000_000,
        "expected_pulse_s": 0.1,
        "expected_cycles": 21_600_000,
    }
    assert records[0]["acquisition"]["sample_rate_hz"] == 500.0
    assert records[0]["quality_contract"]["min_window_s"] == 2.0
    assert (
        records[0]["quality_contract"][
            "clock_reference_must_finish_before_energy_window"
        ]
        is True
    )
    assert records[1]["shunt_raw"] == -10
    edges = [record for record in records if record["type"] == "edge"]
    assert [record["marker_kind"] for record in edges] == [
        "clock_reference",
        "clock_reference",
        "energy_window",
        "energy_window",
    ]
    assert records[-1]["publication_ready"] is False
    assert records[-1]["clock_edge_count"] == 2
    assert records[-1]["energy_edge_count"] == 2


def test_capture_records_reject_com_port_as_controller_identity() -> None:
    armed = parse_armed(ARMED_LINE)
    frames = (
        decode_frame(sample_frame(0, 1_000)),
        decode_frame(
            encode_frame(
                FRAME_TYPE_END,
                flags=END_COMPLETE,
                sequence=1,
                timestamp_us=2_000,
            )
        ),
    )

    with pytest.raises(InaCaptureError, match="not a COM port"):
        build_capture_records(
            CaptureStream(
                armed=armed,
                frames=frames,
                discarded_wire_bytes=0,
                crc_failures=0,
            ),
            capture_id="capture-001",
            run_id="run-001",
            controller_id="COM12",
            firmware_clock_profile="f746_216mhz",
            work_units=10_000,
            physically_verified_shunt_marking="R100",
        )


def test_capture_records_reject_clock_profile_from_other_channel() -> None:
    armed = parse_armed(ARMED_LINE)
    frames = (
        decode_frame(
            encode_frame(
                FRAME_TYPE_END,
                flags=END_COMPLETE,
                sequence=0,
                timestamp_us=2_000,
            )
        ),
    )

    with pytest.raises(InaCaptureError, match="does not match channel"):
        build_capture_records(
            CaptureStream(
                armed=armed,
                frames=frames,
                discarded_wire_bytes=0,
                crc_failures=0,
            ),
            capture_id="capture-001",
            run_id="run-001",
            controller_id="UNO_PWR_TEST",
            firmware_clock_profile="h755_400mhz",
            work_units=10_000,
            physically_verified_shunt_marking="R100",
        )


def test_h755_capture_header_freezes_current_400_mhz_profile() -> None:
    armed = parse_armed(H755_ARMED_LINE)
    frames = (
        decode_frame(
            encode_frame(
                FRAME_TYPE_END,
                flags=END_COMPLETE,
                sequence=0,
                timestamp_us=2_000,
            )
        ),
    )

    records = build_capture_records(
        CaptureStream(
            armed=armed,
            frames=frames,
            discarded_wire_bytes=0,
            crc_failures=0,
        ),
        capture_id="capture-h755",
        run_id="run-h755",
        controller_id="UNO_PWR_TEST",
        firmware_clock_profile="h755_400mhz",
        work_units=10_000,
        physically_verified_shunt_marking="R100",
    )

    assert records[0]["clock_reference_contract"] == {
        "source": "host_selected_frozen_campaign_profile",
        "profile_is_measurement": False,
        "profile": "h755_400mhz",
        "board": "h755",
        "core_clock_hz": 400_000_000,
        "expected_pulse_s": 0.1,
        "expected_cycles": 40_000_000,
    }


def test_capture_cli_requires_and_validates_firmware_clock_profile() -> None:
    parser = _build_cli()
    common_args = [
        "capture",
        "--port",
        "COM12",
        "--channel",
        "F746",
        "--request-id",
        "capture-001",
        "--capture-id",
        "capture-001",
        "--run-id",
        "run-001",
        "--controller-id",
        "UNO_PWR_TEST",
        "--output",
        "capture.jsonl",
        "--confirm-r100",
    ]

    with pytest.raises(SystemExit):
        parser.parse_args(common_args)

    args = parser.parse_args(
        common_args
        + ["--firmware-clock-profile", "h755_400mhz"]
    )
    with pytest.raises(InaCaptureError, match="does not match channel"):
        validate_firmware_clock_profile(
            args.channel,
            args.firmware_clock_profile,
        )

    args = parser.parse_args(
        common_args
        + ["--firmware-clock-profile", "f746_216mhz"]
    )
    profile = validate_firmware_clock_profile(
        args.channel,
        args.firmware_clock_profile,
    )
    assert profile["expected_cycles"] == 21_600_000


def test_reserved_edge_flags_are_rejected_even_with_valid_crc() -> None:
    payload = encode_frame(
        FRAME_TYPE_EDGE,
        flags=0x20,
        sequence=1,
        timestamp_us=1,
    )
    with pytest.raises(InaCaptureError, match="reserved"):
        decode_frame(payload)
