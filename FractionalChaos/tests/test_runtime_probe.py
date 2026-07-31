from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "validation" / "runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("runtime_probe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runtime_probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_probe
SPEC.loader.exec_module(runtime_probe)


def make_record(*, status: int = 2, software_clock: int = 216_000_000) -> bytes:
    values = [
        runtime_probe.MAGIC,
        runtime_probe.VERSION,
        1,
        status,
        0x20044000,
        0x2004C000,
        0x2004BF00,
        0x8000,
        0x7F00,
        0x100,
        0x7E00,
        0x200,
        216_000_000,
        software_clock,
        48_192,
        0,
        64,
        0,
        0,
        runtime_probe.SENTINEL,
        0,
    ]
    raw = bytearray(struct.pack("<21I", *values))
    struct.pack_into("<I", raw, len(raw) - 4, runtime_probe.fnv1a32(raw[:-4]))
    return bytes(raw)


def test_parse_complete_record() -> None:
    parsed = runtime_probe.parse_record_bytes(make_record())
    assert parsed["stack_high_water_bytes"] == 0x200
    assert parsed["heap_configured_bytes"] == 0


@pytest.mark.parametrize(
    "payload,match",
    [
        (make_record(status=runtime_probe.STATUS_ARMED), "no está completo"),
        (
            make_record(status=runtime_probe.STATUS_COMPLETE | runtime_probe.STATUS_OVERFLOW),
            "agotó",
        ),
        (make_record(software_clock=215_000_000), "no coincide"),
    ],
)
def test_rejects_nonreportable_record(payload: bytes, match: str) -> None:
    with pytest.raises(runtime_probe.RuntimeProbeError, match=match):
        runtime_probe.parse_record_bytes(payload)


def test_rejects_corrupt_checksum() -> None:
    payload = bytearray(make_record())
    payload[20] ^= 0x01
    with pytest.raises(runtime_probe.RuntimeProbeError, match="checksum"):
        runtime_probe.parse_record_bytes(bytes(payload))


def test_locates_exact_symbol_and_translates_cm4_alias() -> None:
    output = """
10000260 00000054 B g_fc_h755_m4_runtime_probe
10046000 B __stack_probe_start__
"""
    location = runtime_probe.locate_symbol(
        output,
        "g_fc_h755_m4_runtime_probe",
    )
    assert location == {"address": 0x10000260, "size": 84}
    assert (
        runtime_probe.physical_debug_address("h755_m4", location["address"])
        == 0x30000260
    )


def test_build_report_preserves_evidence_boundary(tmp_path: Path) -> None:
    raw = tmp_path / "probe.bin"
    raw.write_bytes(make_record())
    report = runtime_probe.build_report(raw)
    assert report["quality"]["complete"] is True
    assert report["eligible_as_primary_evidence"] is False
    assert report["provenance"]["clean_source_verified_here"] is False
