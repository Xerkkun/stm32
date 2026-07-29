#!/usr/bin/env python3
"""Genera evidencia gráfica y tramas LSB a partir de una captura FCC1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


VARIABLES = ("x", "y", "z")
BIT_RASTER_WIDTH = 512
BIT_RASTER_MAX_HEIGHT_INCHES = 5.5
STATUS_FLAGS = (
    (0x01, "nonfinite"),
    (0x02, "queue"),
    (0x04, "fixed_state_saturation"),
    (0x08, "fixed_coefficient_saturation"),
    (0x10, "fixed_coefficient_zeroed"),
)
STATUS_KNOWN_MASK = 0x1F


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_capture(path: Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    columns: dict[str, list[float | int]] = {
        "sequence": [],
        "cycles": [],
        "dropped": [],
        "status": [],
        "x": [],
        "y": [],
        "z": [],
        "x_bits": [],
        "y_bits": [],
        "z_bits": [],
        "x_raw": [],
        "y_raw": [],
        "z_raw": [],
    }
    identity: dict[str, str] | None = None

    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            current = {
                name: row[name] for name in ("board", "system", "method")
            }
            current["representation"] = row.get("representation", "float32")
            if identity is None:
                identity = current
            elif current != identity:
                raise ValueError("la captura mezcla placa, sistema o método")
            columns["status"].append(int(row["status"]))
            columns["sequence"].append(int(row["sequence"]))
            columns["cycles"].append(int(row["cycles"]))
            columns["dropped"].append(int(row.get("dropped", "0")))
            for variable in VARIABLES:
                value = float(row[variable])
                if not math.isfinite(value):
                    raise ValueError(f"{variable} contiene un valor no finito")
                columns[variable].append(value)
                columns[f"{variable}_bits"].append(
                    int(row[f"{variable}_bits"], 16)
                )
                raw_text = row.get(f"{variable}_raw", "")
                columns[f"{variable}_raw"].append(
                    int(raw_text) if raw_text else 0
                )

    if identity is None or not columns["sequence"]:
        raise ValueError("la captura no contiene muestras válidas")

    arrays = {
        name: np.asarray(
            values,
            dtype=(
                np.float64
                if name in VARIABLES
                else np.int32
                if name.endswith("_raw")
                else np.uint32
            ),
        )
        for name, values in columns.items()
    }
    return arrays, identity


def summarize_status_flags(status: np.ndarray) -> dict[str, object]:
    values = status.astype(np.uint32, copy=False)
    unique_status, status_occurrences = np.unique(
        values,
        return_counts=True,
    )
    return {
        "all_samples_ok": bool(np.all(values == 0)),
        "counts_by_numeric_status": {
            str(int(value)): int(count)
            for value, count in zip(
                unique_status,
                status_occurrences,
                strict=True,
            )
        },
        "nonzero_status_samples": int(np.count_nonzero(values)),
        "samples_by_flag": {
            name: int(np.count_nonzero(values & flag))
            for flag, name in STATUS_FLAGS
        },
        "samples_with_unknown_flags": int(
            np.count_nonzero(
                values
                & np.uint32(~STATUS_KNOWN_MASK & 0xFFFFFFFF)
            )
        ),
    }


def fixed_point_lsb_bytes(
    arrays: dict[str, np.ndarray],
    *,
    fractional_bits: int,
    lsb_bits: int,
) -> bytes:
    if fractional_bits < lsb_bits:
        raise ValueError("fractional_bits debe ser al menos lsb_bits")
    if not 1 <= lsb_bits <= 8:
        raise ValueError("lsb_bits debe estar entre 1 y 8")

    scale = float(1 << fractional_bits)
    mask = (1 << lsb_bits) - 1
    output = bytearray()
    for sample_index in range(arrays["sequence"].size):
        for variable in VARIABLES:
            scaled = arrays[variable][sample_index] * scale
            quantized = (
                math.floor(scaled + 0.5)
                if scaled >= 0.0
                else math.ceil(scaled - 0.5)
            )
            output.append(quantized & mask)
    return bytes(output)


def fixed_raw_lsb_bytes(
    arrays: dict[str, np.ndarray],
    *,
    lsb_bits: int,
) -> bytes:
    if not 1 <= lsb_bits <= 8:
        raise ValueError("lsb_bits debe estar entre 1 y 8")
    mask = (1 << lsb_bits) - 1
    output = bytearray()
    for sample_index in range(arrays["sequence"].size):
        for variable in VARIABLES:
            output.append(int(arrays[f"{variable}_raw"][sample_index]) & mask)
    return bytes(output)


def float_word_lsb_bytes(
    arrays: dict[str, np.ndarray],
    *,
    lsb_bits: int,
) -> bytes:
    if not 1 <= lsb_bits <= 8:
        raise ValueError("lsb_bits debe estar entre 1 y 8")
    mask = (1 << lsb_bits) - 1
    output = bytearray()
    for sample_index in range(arrays["sequence"].size):
        for variable in VARIABLES:
            output.append(
                int(arrays[f"{variable}_bits"][sample_index]) & mask
            )
    return bytes(output)


def save_time_series(
    arrays: dict[str, np.ndarray],
    output: Path,
    *,
    maximum_samples: int,
    sample_interval: float | None = None,
) -> None:
    sample_count = min(maximum_samples, int(arrays["sequence"].size))
    window = slice(0, sample_count)
    if sample_interval is None:
        horizontal = arrays["sequence"][window]
        horizontal_label = "Integration sequence"
        title_suffix = "transmitted states"
    else:
        horizontal = (
            arrays["sequence"][window].astype(np.float64)
            - float(arrays["sequence"][0])
        ) * sample_interval
        horizontal_label = "Model time from first retained state"
        title_suffix = (
            f"consecutive states, $\\Delta t={sample_interval:g}$"
        )
    figure, axes = plt.subplots(3, 1, sharex=True, figsize=(9, 7))
    for axis, variable in zip(axes, VARIABLES, strict=True):
        axis.plot(
            horizontal,
            arrays[variable][window],
            linewidth=0.65,
        )
        axis.set_ylabel(variable)
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel(horizontal_label)
    figure.suptitle(
        f"Post-transient window: {sample_count} {title_suffix}"
    )
    figure.tight_layout()
    figure.savefig(output, dpi=220)
    plt.close(figure)


def save_attractor(
    arrays: dict[str, np.ndarray],
    output: Path,
) -> None:
    pairs = (("x", "y"), ("x", "z"), ("y", "z"))
    figure, axes = plt.subplots(1, 3, figsize=(12, 4))
    for axis, (horizontal, vertical) in zip(axes, pairs, strict=True):
        axis.scatter(
            arrays[horizontal],
            arrays[vertical],
            s=0.7,
            alpha=0.35,
            linewidths=0,
            rasterized=True,
        )
        axis.set_xlabel(horizontal)
        axis.set_ylabel(vertical)
        axis.grid(alpha=0.15)
    figure.tight_layout()
    figure.savefig(output, dpi=220)
    plt.close(figure)


def save_bit_raster(
    payload: bytes,
    output: Path,
    *,
    lsb_bits: int,
    width: int = BIT_RASTER_WIDTH,
) -> int:
    bytes_array = np.frombuffer(payload, dtype=np.uint8)
    bit_positions = np.arange(lsb_bits - 1, -1, -1, dtype=np.uint8)
    bits = ((bytes_array[:, None] >> bit_positions) & 1).reshape(-1)
    rows = math.ceil(bits.size / width)
    padded = np.zeros(rows * width, dtype=np.uint8)
    padded[: bits.size] = bits

    # The full bitstream is always rendered.  Only the physical figure height
    # is capped so million-bit rasters remain legible in a paper instead of
    # producing a multi-page portrait image.
    figure_height = min(
        BIT_RASTER_MAX_HEIGHT_INCHES,
        max(2.2, rows / 100),
    )
    figure, axis = plt.subplots(figsize=(10, figure_height))
    axis.imshow(
        padded.reshape(rows, width),
        cmap="binary",
        aspect="auto",
        interpolation="nearest",
        vmin=0,
        vmax=1,
    )
    axis.set_xlabel(f"Bits per row: {width}")
    axis.set_ylabel("Row")
    figure.tight_layout()
    figure.savefig(output, dpi=220)
    plt.close(figure)
    return int(bits.size)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("fixed-point", "fixed-raw", "float-word"),
        default="fixed-point",
    )
    parser.add_argument("--fractional-bits", type=int)
    parser.add_argument("--lsb-bits", type=int, default=8)
    parser.add_argument("--discard", type=int, default=0)
    parser.add_argument("--time-series-samples", type=int, default=1024)
    parser.add_argument(
        "--sample-interval",
        type=float,
        help=(
            "intervalo temporal del modelo entre secuencias consecutivas; "
            "si se omite, el eje horizontal conserva el número de secuencia"
        ),
    )
    parser.add_argument("--expected-decimation", type=int)
    parser.add_argument(
        "--minimum-statistical-bits",
        type=int,
        default=1_000_000,
        help=(
            "mínimo de bits exigido para declarar elegibilidad estadística; "
            "la integridad del transporte se informa por separado"
        ),
    )
    args = parser.parse_args()

    if args.discard < 0:
        parser.error("--discard no puede ser negativo")
    if args.time_series_samples <= 0:
        parser.error("--time-series-samples debe ser positivo")
    if args.sample_interval is not None and args.sample_interval <= 0.0:
        parser.error("--sample-interval debe ser positivo")
    if args.expected_decimation is not None and args.expected_decimation <= 0:
        parser.error("--expected-decimation debe ser positivo")
    if args.minimum_statistical_bits <= 0:
        parser.error("--minimum-statistical-bits debe ser positivo")
    if args.mode == "fixed-point" and args.fractional_bits is None:
        parser.error("--fractional-bits es obligatorio en modo fixed-point")
    if args.mode in ("float-word", "fixed-raw") and args.fractional_bits is not None:
        parser.error("--fractional-bits no aplica a float-word ni fixed-raw")

    arrays, identity = load_capture(args.capture)
    if args.discard >= arrays["sequence"].size:
        parser.error("--discard elimina todas las muestras")
    arrays = {
        name: values[args.discard :] for name, values in arrays.items()
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "_".join(identity[name] for name in ("board", "system", "method"))
    time_series_path = args.output_dir / f"{prefix}_time_series.png"
    attractor_path = args.output_dir / f"{prefix}_attractor.png"
    bitstream_path = args.output_dir / f"{prefix}_{args.mode}_lsb.bin"
    raster_path = args.output_dir / f"{prefix}_{args.mode}_lsb_raster.png"
    metadata_path = args.output_dir / f"{prefix}_analysis.json"

    if args.mode == "fixed-point":
        payload = fixed_point_lsb_bytes(
            arrays,
            fractional_bits=args.fractional_bits,
            lsb_bits=args.lsb_bits,
        )
        representation = {
            "mode": "signed_fixed_point_projection",
            "fractional_bits": args.fractional_bits,
            "rounding": "nearest_ties_away_from_zero",
        }
    elif args.mode == "fixed-raw":
        if identity["representation"] != "fixed_q14":
            parser.error("fixed-raw requiere una captura marcada fixed_q14")
        payload = fixed_raw_lsb_bytes(arrays, lsb_bits=args.lsb_bits)
        representation = {
            "mode": "raw_signed_fixed_point_word",
            "format": "Q1.14.14",
            "fractional_bits": 14,
            "rounding": "already_applied_in_firmware",
        }
    else:
        payload = float_word_lsb_bytes(arrays, lsb_bits=args.lsb_bits)
        representation = {
            "mode": "raw_ieee754_binary32_word",
            "warning": (
                "No equivale a extraer LSB de una implementación "
                "aritmética en punto fijo."
            ),
        }

    bitstream_path.write_bytes(payload)
    save_time_series(
        arrays,
        time_series_path,
        maximum_samples=args.time_series_samples,
        sample_interval=args.sample_interval,
    )
    save_attractor(arrays, attractor_path)
    bit_count = save_bit_raster(
        payload,
        raster_path,
        lsb_bits=args.lsb_bits,
    )

    sequence_deltas = np.diff(arrays["sequence"].astype(np.int64))
    sequence_gap_count = (
        int(np.count_nonzero(sequence_deltas != args.expected_decimation))
        if args.expected_decimation is not None
        else None
    )
    transport_complete = (
        sequence_gap_count == 0
        and int(np.max(arrays["dropped"])) == 0
        if args.expected_decimation is not None
        else False
    )
    solver_status = summarize_status_flags(arrays["status"])
    all_status_ok = bool(solver_status["all_samples_ok"])
    statistically_eligible = (
        transport_complete
        and all_status_ok
        and bit_count >= args.minimum_statistical_bits
    )
    nonzero_cycle_count = int(np.count_nonzero(arrays["cycles"]))
    performance_available = nonzero_cycle_count > 0
    metadata = {
        "schema_version": 4,
        "capture": str(args.capture.resolve()),
        "capture_sha256": sha256(args.capture),
        **identity,
        "discarded_valid_samples": args.discard,
        "samples_used": int(arrays["sequence"].size),
        "sequence_first": int(arrays["sequence"][0]),
        "sequence_last": int(arrays["sequence"][-1]),
        "sampling": {
            "sample_interval": args.sample_interval,
            "time_unit": (
                "model_time" if args.sample_interval is not None else None
            ),
            "time_origin_sequence": (
                int(arrays["sequence"][0])
                if args.sample_interval is not None
                else None
            ),
            "host_reception_time_used": False,
            "interpolation": False,
            "resampling": False,
        },
        "transport": {
            "expected_decimation": args.expected_decimation,
            "sequence_gap_count": sequence_gap_count,
            "maximum_reported_dropped": int(np.max(arrays["dropped"])),
            "transport_complete": transport_complete,
        },
        "solver_status": solver_status,
        "performance_cycles_per_step": {
            "available": performance_available,
            "nonzero_measurements": nonzero_cycle_count,
            "minimum": int(np.min(arrays["cycles"])),
            "median": float(np.median(arrays["cycles"])),
            "mean": float(np.mean(arrays["cycles"])),
            "p95": float(np.percentile(arrays["cycles"], 95)),
            "p99": float(np.percentile(arrays["cycles"], 99)),
            "maximum": int(np.max(arrays["cycles"])),
            "scope": (
                "DWT cycles for one integration step; UART and decimation "
                "are outside the measured interval"
            ),
            "reason_if_unavailable": (
                None
                if performance_available
                else "all reported DWT cycle counts are zero"
            ),
        },
        "statistical_eligibility": {
            "minimum_required_bits": args.minimum_statistical_bits,
            "observed_bits": bit_count,
            "eligible_for_configured_screening": statistically_eligible,
            "scope": (
                "transport integrity and configured bit-count threshold only; "
                "each statistical battery must enforce its own requirements"
            ),
            "is_statistical_test_result": False,
            "reason": (
                "eligible"
                if statistically_eligible
                else (
                    "incomplete_transport"
                    if not transport_complete
                    else (
                        "nonzero_solver_status"
                        if not all_status_ok
                        else "insufficient_bit_count"
                    )
                )
            ),
        },
        "extraction": {
            **representation,
            "variables": list(VARIABLES),
            "variable_order": "x_then_y_then_z_per_sample",
            "lsb_bits_per_variable": args.lsb_bits,
            "bits_per_sample": 3 * args.lsb_bits,
            "stored_byte_order": (
                "one low-bit field per byte; unused high bits are zero"
            ),
            "raster_bit_order_within_field": "most_to_least_significant",
        },
        "outputs": {
            "time_series": time_series_path.name,
            "time_series_samples_plotted": min(
                args.time_series_samples,
                int(arrays["sequence"].size),
            ),
            "attractor": attractor_path.name,
            "bitstream": bitstream_path.name,
            "bitstream_sha256": sha256(bitstream_path),
            "bit_count": bit_count,
            "bit_raster": raster_path.name,
            "bit_raster_bits_per_row": BIT_RASTER_WIDTH,
            "bit_raster_contains_complete_stream": True,
            "bit_raster_max_figure_height_inches": (
                BIT_RASTER_MAX_HEIGHT_INCHES
            ),
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(metadata_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
