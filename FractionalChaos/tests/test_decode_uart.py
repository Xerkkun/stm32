#!/usr/bin/env python3
"""Pruebas del decodificador FCC1 sin requerir un puerto serie."""

from __future__ import annotations

import io
import struct
import sys
import time
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import decode_uart  # noqa: E402


def frame(
    *,
    version: int = 1,
    kind: int = 1,
    board: int = 2,
    system: int = 2,
    method: int = 1,
) -> bytes:
    prefix = struct.pack(
        "<I6BH6I",
        0x31434346,
        version,
        kind,
        board,
        system,
        method,
        0,
        24,
        0x01020304,
        0x11223344,
        0xAABBCCDD,
        0x3F800000,
        0xC0000000,
        0x3F000000,
    )
    return prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)


class TimedStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = iter(chunks)

    def read(self, _size: int) -> bytes:
        try:
            return next(self.chunks)
        except StopIteration:
            return b""


class DecoderTests(unittest.TestCase):
    def test_golden_frame_and_rows(self) -> None:
        values = list(decode_uart.iter_frames(io.BytesIO(frame())))
        self.assertEqual(len(values), 1)
        row = list(decode_uart.rows(iter(values)))[0]
        self.assertEqual(row["board"], "h755")
        self.assertEqual(row["system"], "chen")
        self.assertEqual(row["method"], "gl_caputo")
        self.assertEqual(row["sequence"], 0x01020304)
        self.assertEqual(row["cycles"], 0x11223344)
        self.assertEqual(row["x"], 1.0)
        self.assertEqual(row["y"], -2.0)
        self.assertEqual(row["z"], 0.5)
        self.assertEqual(row["crc32"], "0xEDA10E9E")

    def test_resynchronizes_and_discards_invalid_data(self) -> None:
        damaged = bytearray(frame())
        damaged[28] ^= 1
        payload = b"ruido" + bytes(damaged) + b"\x46\x43" + frame()
        values = list(decode_uart.iter_frames(io.BytesIO(payload)))
        self.assertEqual(len(values), 1)

    def test_rejects_crc_valid_but_unknown_headers(self) -> None:
        payload = (
            frame(version=2)
            + frame(kind=2)
            + frame(board=9)
            + frame(system=9)
            + frame(method=9)
        )
        self.assertEqual(
            list(decode_uart.iter_frames(io.BytesIO(payload))),
            [],
        )

    def test_live_stream_survives_an_idle_serial_timeout(self) -> None:
        stream = TimedStream([b"", frame()])
        values = list(
            decode_uart.iter_frames(
                stream,
                live=True,
                deadline=time.monotonic() + 0.1,
            )
        )
        self.assertEqual(len(values), 1)


if __name__ == "__main__":
    unittest.main()
