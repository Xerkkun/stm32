"""Encoder and strict line parser for the standalone ``PWRCTL/1`` protocol.

The cycle request and ACK grammar is shared with the physical-campaign runner,
but this module contains no serial-port or runner integration. Parser tests do
not prove that a physical power interruption occurred.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Mapping


PROTOCOL_VERSION = "PWRCTL/1"
MIN_OFF_MS = 2_000
MAX_OFF_MS = 30_000
OFF_MAX_MV = 200
ON_MIN_MV = 3_100
VALID_CHANNELS = frozenset({"F746", "H755", "ALL"})
CHANNEL_ALIASES = {"CH1": "F746", "CH2": "H755"}
MAX_REPORTED_MV = 999_999
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProtocolError(ValueError):
    """Raised when a command or controller line violates ``PWRCTL/1``."""


@dataclass(frozen=True)
class ControllerMessage:
    """One validated controller response."""

    kind: str
    request_id: str
    channel: str
    fields: Mapping[str, str]
    version: str = PROTOCOL_VERSION

    @property
    def cycle_id(self) -> str:
        """Expose the wire request identifier under the campaign term."""

        return self.request_id

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "version": self.version,
            "kind": self.kind,
            "request_id": self.request_id,
            "channel": self.channel,
            "fields": dict(self.fields),
        }


def _validate_request_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(
        request_id
    ):
        raise ProtocolError(
            "request_id must match [A-Za-z0-9_-]{1,64}"
        )
    return request_id


def _canonical_channel(channel: str) -> str:
    if not isinstance(channel, str):
        raise ProtocolError("channel must be F746, H755, or ALL")
    normalized = channel.upper()
    normalized = CHANNEL_ALIASES.get(normalized, normalized)
    if normalized not in VALID_CHANNELS:
        raise ProtocolError("channel must be F746, H755, or ALL")
    return normalized


def encode_status() -> bytes:
    """Encode the version-discovery/status command."""

    return b"STATUS\n"


def encode_cycle(request_id: str, channel: str, off_ms: int) -> bytes:
    """Encode a validated cycle request.

    ``bool`` is rejected even though it is an ``int`` subclass in Python.
    """

    valid_request_id = _validate_request_id(request_id)
    canonical_channel = _canonical_channel(channel)
    if isinstance(off_ms, bool) or not isinstance(off_ms, int):
        raise ProtocolError("off_ms must be an integer")
    if not MIN_OFF_MS <= off_ms <= MAX_OFF_MS:
        raise ProtocolError(
            f"off_ms must be in [{MIN_OFF_MS}, {MAX_OFF_MS}]"
        )
    return (
        f"CYCLE {valid_request_id} {canonical_channel} {off_ms}\n"
    ).encode("ascii")


def _decode_ascii(line: str | bytes) -> str:
    if isinstance(line, bytes):
        try:
            decoded = line.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProtocolError("controller line is not ASCII") from error
    elif isinstance(line, str):
        decoded = line
    else:
        raise ProtocolError("controller line must be str or bytes")
    return decoded.rstrip("\r\n")


def _require_canonical_whitespace(payload: str) -> list[str]:
    if not payload:
        raise ProtocolError("controller line is empty")
    if payload != payload.strip() or "\t" in payload or "  " in payload:
        raise ProtocolError("controller line uses non-canonical whitespace")
    return payload.split(" ")


def _parse_flag(token: str, expected_name: str) -> str:
    expected_prefix = f"{expected_name}="
    if not token.startswith(expected_prefix):
        raise ProtocolError(f"expected {expected_name} in canonical order")
    value = token[len(expected_prefix) :]
    if value not in {"0", "1"}:
        raise ProtocolError(f"{expected_name} must be 0 or 1")
    return value


def _parse_millivolts(token: str, expected_name: str) -> str:
    expected_prefix = f"{expected_name}="
    if not token.startswith(expected_prefix):
        raise ProtocolError(f"expected {expected_name} in canonical order")
    value = token[len(expected_prefix) :]
    if not value or not value.isascii() or not value.isdecimal():
        raise ProtocolError(f"{expected_name} must be unsigned decimal")
    if int(value, 10) > MAX_REPORTED_MV:
        raise ProtocolError(f"{expected_name} exceeds wire range")
    return value


def _parse_ack(tokens: list[str]) -> ControllerMessage:
    if len(tokens) != 7:
        raise ProtocolError("ACK must contain exactly seven tokens")
    request_id = _validate_request_id(tokens[1])
    channel = _canonical_channel(tokens[2])
    if channel != tokens[2]:
        raise ProtocolError("ACK channel must be canonical")

    fields = {
        "OFF_OK": _parse_flag(tokens[3], "OFF_OK"),
        "ON_OK": _parse_flag(tokens[4], "ON_OK"),
        "OFF_MV": _parse_millivolts(tokens[5], "OFF_MV"),
        "ON_MV": _parse_millivolts(tokens[6], "ON_MV"),
    }
    if fields["OFF_OK"] == "0" and fields["ON_OK"] != "0":
        raise ProtocolError("ON_OK cannot be 1 when OFF_OK is 0")
    if (
        fields["OFF_OK"] == "1"
        and int(fields["OFF_MV"], 10) > OFF_MAX_MV
    ):
        raise ProtocolError("OFF_OK=1 exceeds the OFF voltage threshold")
    if (
        fields["ON_OK"] == "1"
        and int(fields["ON_MV"], 10) < ON_MIN_MV
    ):
        raise ProtocolError("ON_OK=1 is below the ON voltage threshold")
    return ControllerMessage(
        kind="ACK",
        request_id=request_id,
        channel=channel,
        fields=fields,
    )


def _parse_error(tokens: list[str]) -> ControllerMessage:
    if len(tokens) != 4:
        raise ProtocolError("ERR must contain exactly four tokens")
    request_id = tokens[1]
    channel = tokens[2]
    if request_id != "0":
        _validate_request_id(request_id)
    if channel not in {"0", *VALID_CHANNELS}:
        raise ProtocolError("ERR channel is invalid")
    code = tokens[3]
    if not code or not code.replace("_", "").isalnum() or code != code.upper():
        raise ProtocolError("ERR code must be uppercase ASCII token")
    return ControllerMessage(
        kind="ERR",
        request_id=request_id,
        channel=channel,
        fields={"CODE": code},
    )


def _parse_status(tokens: list[str]) -> ControllerMessage:
    if len(tokens) < 3 or tokens[1] != "PWRCTL=1":
        raise ProtocolError("STATUS line has no PWRCTL=1 version field")
    fields: dict[str, str] = {"PWRCTL": "1"}
    for token in tokens[2:]:
        if token.count("=") != 1:
            raise ProtocolError(f"invalid STATUS field {token!r}")
        key, value = token.split("=", 1)
        if not key or not value or key in fields:
            raise ProtocolError(f"invalid STATUS field {token!r}")
        fields[key] = value
    return ControllerMessage(
        kind="STATUS",
        request_id="0",
        channel="0",
        fields=fields,
    )


def parse_message(line: str | bytes) -> ControllerMessage:
    """Parse one canonical ACK, ERR, or STATUS response."""

    tokens = _require_canonical_whitespace(_decode_ascii(line))
    if tokens[0] == "ACK":
        return _parse_ack(tokens)
    if tokens[0] == "ERR":
        return _parse_error(tokens)
    if tokens[0] == "STATUS":
        return _parse_status(tokens)
    raise ProtocolError(f"unsupported message kind {tokens[0]!r}")


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encode or parse the standalone PWRCTL/1 protocol."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("status", help="print the STATUS command")

    cycle_parser = subparsers.add_parser(
        "cycle", help="print a validated CYCLE command"
    )
    cycle_parser.add_argument("request_id")
    cycle_parser.add_argument("channel", choices=sorted(VALID_CHANNELS))
    cycle_parser.add_argument("off_ms", type=int)

    parse_parser = subparsers.add_parser(
        "parse", help="parse one quoted controller response as JSON"
    )
    parse_parser.add_argument("line")
    return parser


def main() -> int:
    args = _build_cli().parse_args()
    if args.action == "status":
        print(encode_status().decode("ascii"), end="")
        return 0
    if args.action == "cycle":
        print(
            encode_cycle(
                args.request_id,
                args.channel,
                args.off_ms,
            ).decode("ascii"),
            end="",
        )
        return 0

    message = parse_message(args.line)
    print(json.dumps(message.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
