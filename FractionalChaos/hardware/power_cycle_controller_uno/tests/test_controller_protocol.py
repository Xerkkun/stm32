from __future__ import annotations

import sys
from pathlib import Path

import pytest


COMPONENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(COMPONENT_ROOT))

from host.controller_protocol import (  # noqa: E402
    MAX_OFF_MS,
    MIN_OFF_MS,
    ProtocolError,
    encode_cycle,
    encode_status,
    parse_message,
)


def test_encode_status_is_canonical() -> None:
    assert encode_status() == b"STATUS\n"


@pytest.mark.parametrize(
    ("channel", "expected_channel"),
    [
        ("F746", "F746"),
        ("h755", "H755"),
        ("ALL", "ALL"),
        ("CH1", "F746"),
        ("ch2", "H755"),
    ],
)
@pytest.mark.parametrize("off_ms", [MIN_OFF_MS, 2_500, MAX_OFF_MS])
def test_encode_cycle_matches_runner_contract(
    channel: str, expected_channel: str, off_ms: int
) -> None:
    assert encode_cycle("run_0007", channel, off_ms) == (
        f"CYCLE run_0007 {expected_channel} {off_ms}\n".encode("ascii")
    )


@pytest.mark.parametrize(
    ("request_id", "channel", "off_ms"),
    [
        ("bad id", "F746", 2_500),
        ("", "F746", 2_500),
        ("x" * 65, "F746", 2_500),
        ("run-1", "F7", 2_500),
        ("run-1", "F746", MIN_OFF_MS - 1),
        ("run-1", "F746", MAX_OFF_MS + 1),
        ("run-1", "F746", True),
        ("run-1", "F746", 2_500.0),
    ],
)
def test_encode_cycle_rejects_unsafe_or_ambiguous_input(
    request_id: str, channel: str, off_ms: object
) -> None:
    with pytest.raises(ProtocolError):
        encode_cycle(
            request_id,
            channel,
            off_ms,  # type: ignore[arg-type]
        )


def test_parse_success_ack_exact_field_order() -> None:
    message = parse_message(
        b"ACK cell-017 F746 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=3291\r\n"
    )

    assert message.kind == "ACK"
    assert message.request_id == "cell-017"
    assert message.cycle_id == "cell-017"
    assert message.channel == "F746"
    assert message.fields == {
        "OFF_OK": "1",
        "ON_OK": "1",
        "OFF_MV": "12",
        "ON_MV": "3291",
    }


def test_parse_all_ack_uses_worst_case_rail_values() -> None:
    message = parse_message(
        "ACK pair_04 ALL OFF_OK=1 ON_OK=0 OFF_MV=186 ON_MV=2870"
    )

    assert message.kind == "ACK"
    assert message.channel == "ALL"
    assert message.fields["ON_OK"] == "0"


def test_parse_explicit_canonical_error() -> None:
    message = parse_message("ERR run-1 H755 OFF_MS_RANGE\n")

    assert message.kind == "ERR"
    assert message.request_id == "run-1"
    assert message.channel == "H755"
    assert message.fields["CODE"] == "OFF_MS_RANGE"


def test_parse_versioned_status() -> None:
    message = parse_message(
        "STATUS PWRCTL=1 STATE=IDLE RELAY1=OFF RELAY2=ON "
        "SENSE1=OFF SENSE2=ON SENSE1_MV=12 SENSE2_MV=3291 "
        "OFF_MAX_MV=200 ON_MIN_MV=3100 MIN_OFF_MS=2000 "
        "MAX_OFF_MS=30000"
    )

    assert message.kind == "STATUS"
    assert message.version == "PWRCTL/1"
    assert message.fields["STATE"] == "IDLE"
    assert message.fields["PWRCTL"] == "1"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "PWRCTL/1 ACK cycle_id=0 command=STATUS",
        "ACK run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=12",
        "ACK bad.id F746 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=3290",
        "ACK run-1 CH1 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=3290",
        "ACK run-1 F746 ON_OK=1 OFF_OK=1 OFF_MV=12 ON_MV=3290",
        "ACK run-1 F746 OFF_OK=2 ON_OK=1 OFF_MV=12 ON_MV=3290",
        "ACK run-1 F746 OFF_OK=0 ON_OK=1 OFF_MV=500 ON_MV=3290",
        "ACK run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=201 ON_MV=3290",
        "ACK run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=3099",
        "ACK run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=-1 ON_MV=3290",
        "ACK run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=1000000",
        "ACK  run-1 F746 OFF_OK=1 ON_OK=1 OFF_MV=12 ON_MV=3290",
        "ERR run-1 F746",
        "ERR run-1 CH1 OFF_TIMEOUT",
        "ERR run-1 F746 lower_case",
        "STATUS STATE=IDLE",
        "STATUS PWRCTL=1 STATE=IDLE STATE=BOOT",
    ],
)
def test_parse_rejects_malformed_lines(line: str) -> None:
    with pytest.raises(ProtocolError):
        parse_message(line)
