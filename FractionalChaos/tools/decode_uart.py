#!/usr/bin/env python3
"""Decodifica las tramas binarias FCC1 emitidas por ambas placas."""

from __future__ import annotations

import argparse
import csv
import io
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import BinaryIO, Iterator


SYNC = b"FCC1"
FRAME = struct.Struct("<I6BH7I")
SYSTEMS = {0: "lorenz", 1: "rossler", 2: "chen"}
METHODS = {0: "efork3", 1: "gl_caputo", 2: "m2sfrk"}
BOARDS = {1: "f746", 2: "h755"}
FRAME_STATE_FLOAT = 1
FRAME_STATE_FIXED = 3
FRAME_TIMING_BLOCK = 4
STATUS_NONFINITE = 0x01
STATUS_QUEUE = 0x02
STATUS_FIXED_STATE_SATURATION = 0x04
STATUS_FIXED_COEFFICIENT_SATURATION = 0x08
STATUS_FIXED_COEFFICIENT_ZEROED = 0x10
STATUS_KNOWN_MASK = 0x1F
STATUS_FLAGS = (
    (STATUS_NONFINITE, "nonfinite"),
    (STATUS_QUEUE, "queue"),
    (STATUS_FIXED_STATE_SATURATION, "fixed_state_saturation"),
    (
        STATUS_FIXED_COEFFICIENT_SATURATION,
        "fixed_coefficient_saturation",
    ),
    (
        STATUS_FIXED_COEFFICIENT_ZEROED,
        "fixed_coefficient_zeroed",
    ),
)


def valid_header(values: tuple[int, ...]) -> bool:
    return (
        values[1] == 1
        and values[2]
        in (FRAME_STATE_FLOAT, FRAME_STATE_FIXED, FRAME_TIMING_BLOCK)
        and values[3] in BOARDS
        and values[4] in SYSTEMS
        and values[5] in METHODS
        and (values[6] & ~STATUS_KNOWN_MASK) == 0
        and values[7] == 24
    )


def iter_frames(
    stream: BinaryIO,
    *,
    live: bool = False,
    deadline: float | None = None,
) -> Iterator[tuple[int, ...]]:
    buffer = bytearray()
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break

        chunk = stream.read(4096)
        if chunk:
            buffer.extend(chunk)
        elif not live and not buffer:
            break

        while True:
            start = buffer.find(SYNC)
            if start < 0:
                if len(buffer) > len(SYNC) - 1:
                    del buffer[: -(len(SYNC) - 1)]
                break
            if start:
                del buffer[:start]
            if len(buffer) < FRAME.size:
                break

            raw = bytes(buffer[: FRAME.size])
            values = FRAME.unpack(raw)
            expected = values[-1]
            actual = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
            if expected != actual or not valid_header(values):
                del buffer[0]
                continue

            del buffer[: FRAME.size]
            yield values

        if not chunk and not live:
            break


def word_to_float(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def word_to_signed(word: int) -> int:
    return struct.unpack("<i", struct.pack("<I", word))[0]


def status_names(status: int) -> tuple[str, ...]:
    return tuple(name for flag, name in STATUS_FLAGS if status & flag)


def rows(frames: Iterator[tuple[int, ...]]) -> Iterator[dict[str, object]]:
    for values in frames:
        (
            _sync,
            version,
            kind,
            board,
            system,
            method,
            status,
            payload_bytes,
            sequence,
            cycles,
            dropped,
            x_bits,
            y_bits,
            z_bits,
            crc32,
        ) = values
        fixed = kind == FRAME_STATE_FIXED
        timing = kind == FRAME_TIMING_BLOCK
        raw_values = tuple(word_to_signed(word) for word in (x_bits, y_bits, z_bits))
        if timing:
            decoded_values: tuple[float | str, ...] = ("", "", "")
        elif fixed:
            decoded_values = tuple(value / 16384.0 for value in raw_values)
        else:
            decoded_values = tuple(
                word_to_float(word) for word in (x_bits, y_bits, z_bits)
            )
        decoded_status = status_names(status)
        yield {
            "version": version,
            "kind": kind,
            "board": BOARDS.get(board, f"unknown_{board}"),
            "system": SYSTEMS.get(system, f"unknown_{system}"),
            "method": METHODS.get(method, f"unknown_{method}"),
            "representation": (
                "cycle_block_u32"
                if timing
                else ("fixed_q14" if fixed else "float32")
            ),
            "status": status,
            "status_flags": "|".join(decoded_status) if decoded_status else "ok",
            "fixed_state_saturation": int(
                bool(status & STATUS_FIXED_STATE_SATURATION)
            ),
            "fixed_coefficient_saturation": int(
                bool(status & STATUS_FIXED_COEFFICIENT_SATURATION)
            ),
            "fixed_coefficient_zeroed": int(
                bool(status & STATUS_FIXED_COEFFICIENT_ZEROED)
            ),
            "payload_bytes": payload_bytes,
            "sequence": sequence,
            "cycles": cycles,
            "dropped": dropped,
            "x": decoded_values[0],
            "y": decoded_values[1],
            "z": decoded_values[2],
            "x_raw": raw_values[0] if fixed else "",
            "y_raw": raw_values[1] if fixed else "",
            "z_raw": raw_values[2] if fixed else "",
            "cycle_0": cycles if timing else "",
            "cycle_1": x_bits if timing else "",
            "cycle_2": y_bits if timing else "",
            "cycle_3": z_bits if timing else "",
            "x_bits": f"0x{x_bits:08X}",
            "y_bits": f"0x{y_bits:08X}",
            "z_bits": f"0x{z_bits:08X}",
            "crc32": f"0x{crc32:08X}",
        }


def open_input(args: argparse.Namespace) -> tuple[BinaryIO, object | None]:
    if args.input:
        return args.input.open("rb"), None

    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Para leer un puerto COM instala pyserial; "
            "la decodificación de archivos no requiere dependencias."
        ) from exc

    port = serial.Serial(args.port, args.baud, timeout=0.25)
    return port, port


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="captura UART binaria")
    source.add_argument("--port", help="puerto serie, por ejemplo COM7")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--output", type=Path, help="CSV; stdout si se omite")
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="duración de captura serie; 0 significa hasta Ctrl+C",
    )
    args = parser.parse_args()
    if args.seconds < 0.0:
        parser.error("--seconds debe ser cero o un valor positivo")

    input_stream, serial_port = open_input(args)
    output_stream: io.TextIOBase
    close_output = False
    if args.output:
        output_stream = args.output.open("w", newline="", encoding="utf-8")
        close_output = True
    else:
        output_stream = sys.stdout

    fieldnames = [
        "version",
        "kind",
        "board",
        "system",
        "method",
        "representation",
        "status",
        "status_flags",
        "fixed_state_saturation",
        "fixed_coefficient_saturation",
        "fixed_coefficient_zeroed",
        "payload_bytes",
        "sequence",
        "cycles",
        "dropped",
        "x",
        "y",
        "z",
        "x_raw",
        "y_raw",
        "z_raw",
        "cycle_0",
        "cycle_1",
        "cycle_2",
        "cycle_3",
        "x_bits",
        "y_bits",
        "z_bits",
        "crc32",
    ]
    writer = csv.DictWriter(output_stream, fieldnames=fieldnames)
    writer.writeheader()

    started = time.monotonic()
    deadline = (
        started + args.seconds
        if serial_port is not None and args.seconds
        else None
    )
    try:
        for row in rows(
            iter_frames(
                input_stream,
                live=serial_port is not None,
                deadline=deadline,
            )
        ):
            writer.writerow(row)
            output_stream.flush()
    except KeyboardInterrupt:
        pass
    finally:
        input_stream.close()
        if close_output:
            output_stream.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
