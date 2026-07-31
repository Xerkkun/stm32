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
    status: int = 0,
    words: tuple[int, int, int] = (0x3F800000, 0xC0000000, 0x3F000000),
) -> bytes:
    prefix = struct.pack(
        "<I6BH6I",
        0x31434346,
        version,
        kind,
        board,
        system,
        method,
        status,
        24,
        0x01020304,
        0x11223344,
        0xAABBCCDD,
        *words,
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

    def test_selected_system_wire_ids_are_decoded(self) -> None:
        for wire_id, expected in (
            (2, "chen"),
            (3, "liu"),
            (4, "hammouch_mekkaoui"),
        ):
            with self.subTest(wire_id=wire_id):
                values = list(
                    decode_uart.iter_frames(
                        io.BytesIO(frame(system=wire_id))
                    )
                )
                self.assertEqual(len(values), 1)
                row = list(decode_uart.rows(iter(values)))[0]
                self.assertEqual(row["system"], expected)

    def test_fixed_q14_frame_preserves_raw_words(self) -> None:
        status = (
            decode_uart.STATUS_FIXED_STATE_SATURATION
            | decode_uart.STATUS_FIXED_COEFFICIENT_SATURATION
            | decode_uart.STATUS_FIXED_COEFFICIENT_ZEROED
        )
        payload = frame(
            kind=decode_uart.FRAME_STATE_FIXED,
            status=status,
            words=(0x00004000, 0xFFFF8000, 0x00002000),
        )
        values = list(decode_uart.iter_frames(io.BytesIO(payload)))
        row = list(decode_uart.rows(iter(values)))[0]
        self.assertEqual(row["representation"], "fixed_q14")
        self.assertEqual((row["x"], row["y"], row["z"]), (1.0, -2.0, 0.5))
        self.assertEqual(
            (row["x_raw"], row["y_raw"], row["z_raw"]),
            (16384, -32768, 8192),
        )
        self.assertEqual(row["status"], 0x1C)
        self.assertEqual(
            row["status_flags"],
            "fixed_state_saturation|fixed_coefficient_saturation|"
            "fixed_coefficient_zeroed",
        )
        self.assertEqual(row["fixed_state_saturation"], 1)
        self.assertEqual(row["fixed_coefficient_saturation"], 1)
        self.assertEqual(row["fixed_coefficient_zeroed"], 1)

    def test_timing_block_preserves_four_cycle_values(self) -> None:
        payload = frame(
            kind=decode_uart.FRAME_TIMING_BLOCK,
            words=(102, 103, 104),
        )
        values = list(decode_uart.iter_frames(io.BytesIO(payload)))
        row = list(decode_uart.rows(iter(values)))[0]
        self.assertEqual(row["representation"], "cycle_block_u32")
        self.assertEqual(
            (
                row["cycle_0"],
                row["cycle_1"],
                row["cycle_2"],
                row["cycle_3"],
            ),
            (0x11223344, 102, 103, 104),
        )
        self.assertEqual((row["x"], row["y"], row["z"]), ("", "", ""))

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
            + frame(status=0x80)
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
