#!/usr/bin/env python3
"""Analyze the four lossless dense UART time-series pilot captures.

This lane is intentionally separate from the legacy decimated-capture
analysis.  It accepts only the four ``dense_timeseries_pilot`` runs for
Lorenz/M2sFRK on F746/H755 in float32/fixed-point form.  Every input CSV must
contain the exact consecutive model-step sequence 1..12000 with zero status
and dropped-frame counters.

The first 2000 states are discarded as the frozen transient.  The scalar
observable x is then analyzed in two non-overlapping 4096-sample windows at
the native model interval h=0.005.  No interpolation, decimation,
harmonization, or host-reception timestamps enter the analysis.

The Lyapunov estimates remain finite-time scalar-reconstruction diagnostics.
They do not repair the failed frozen ABM qualification of the Lorenz case and
do not establish chaos, hiddenness, randomness, or an asymptotic spectrum.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HIDDEN_SOURCE = (
    ROOT.parents[1] / "Hidden Attractors Fractional Order" / "version_2"
)
DEFAULT_OUTPUT = (
    ROOT / "validation" / "results" / "dense_uart_time_series_lyapunov"
)
HIDDEN_PACKAGE_NAME = "hidden-attractors-fo"
HIDDEN_PYPI_URL = "https://pypi.org/project/hidden-attractors-fo/"
NOLDS_LORENZ_EXAMPLE_URL = (
    "https://cschoel.github.io/nolds/nolds.html#nolds.lyap_r"
)

ENDPOINT_NAME = "dense_timeseries_pilot"
SYSTEM = "lorenz"
METHOD = "m2sfrk"
OBSERVABLE = "x"
FRACTIONAL_ORDER_Q = 0.995
MODEL_STEP = 0.005
TIME_UNIT = "model_time"
EXPECTED_FIRST_SEQUENCE = 1
EXPECTED_LAST_SEQUENCE = 12_000
EXPECTED_FRAMES = 12_000
TRANSIENT_STEPS = 2_000
WINDOW_SAMPLES = 4_096
ANALYSIS_SEED = 20_260_728
MAX_PAIRWISE_MATRIX_BYTES = 256 * 1024 * 1024

PRIMARY_PARAMETERS: dict[str, Any] = {
    "rosenstein_emb_dim": 5,
    "rosenstein_lag": 5,
    "rosenstein_min_tsep": 10,
    "rosenstein_min_neighbors": 20,
    "rosenstein_trajectory_len": 28,
    "rosenstein_fit": "poly",
    "rosenstein_fit_offset": 8,
    "eckmann_emb_dim": 5,
    "eckmann_matrix_dim": 5,
    "eckmann_min_neighbors": 8,
    "eckmann_min_tsep": 10,
}
SENSITIVITY_PARAMETERS: dict[str, Any] = {
    **PRIMARY_PARAMETERS,
    "rosenstein_lag": 10,
    "rosenstein_min_tsep": 20,
    "rosenstein_trajectory_len": 56,
    "rosenstein_fit_offset": 16,
    "eckmann_min_tsep": 20,
}


@dataclass(frozen=True)
class DenseCase:
    """Identity contract for one board/representation lane."""

    case_id: str
    board: str
    run_representation: str
    csv_representation: str
    kind: int

    @property
    def key(self) -> tuple[str, str]:
        return self.board, self.run_representation


CASES = (
    DenseCase("f746_float32", "f746", "float32", "float32", 1),
    DenseCase(
        "f746_fixed_q14_q30",
        "f746",
        "fixed_q14_q30",
        "fixed_q14",
        3,
    ),
    DenseCase("h755_float32", "h755", "float32", "float32", 1),
    DenseCase(
        "h755_fixed_q14_q30",
        "h755",
        "fixed_q14_q30",
        "fixed_q14",
        3,
    ),
)
CASE_BY_KEY = {case.key: case for case in CASES}

PROTOCOLS = (
    {
        "analysis_id": "primary_window_1",
        "window_index": 0,
        "window_role": "primary",
        "parameters": PRIMARY_PARAMETERS,
    },
    {
        "analysis_id": "primary_window_2",
        "window_index": 1,
        "window_role": "nonoverlapping_replication",
        "parameters": PRIMARY_PARAMETERS,
    },
    {
        "analysis_id": "parameter_sensitivity_window_1",
        "window_index": 0,
        "window_role": "parameter_sensitivity",
        "parameters": SENSITIVITY_PARAMETERS,
    },
)


class DenseLyapunovError(RuntimeError):
    """Raised when dense acquisition or analysis violates the frozen contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_signal(values: np.ndarray) -> str:
    normalized = np.asarray(values, dtype="<f8")
    return hashlib.sha256(normalized.tobytes(order="C")).hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _require(observed: Any, expected: Any, label: str, path: Path) -> None:
    if observed != expected:
        raise DenseLyapunovError(
            f"{path}: {label}={observed!r}, expected {expected!r}"
        )


def _require_float(
    observed: Any,
    expected: float,
    label: str,
    path: Path,
) -> None:
    try:
        parsed = float(observed)
    except (TypeError, ValueError) as exc:
        raise DenseLyapunovError(f"{path}: invalid {label}") from exc
    if not math.isfinite(parsed) or not math.isclose(
        parsed,
        expected,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise DenseLyapunovError(
            f"{path}: {label}={parsed!r}, expected {expected!r}"
        )


def _parse_int(row: Mapping[str, str], key: str, path: Path) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise DenseLyapunovError(
            f"{path}: invalid integer field {key!r}"
        ) from exc


def _parse_float(row: Mapping[str, str], key: str, path: Path) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise DenseLyapunovError(
            f"{path}: invalid floating-point field {key!r}"
        ) from exc
    if not math.isfinite(value):
        raise DenseLyapunovError(f"{path}: non-finite field {key!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DenseLyapunovError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DenseLyapunovError(f"{path}: root JSON value must be an object")
    return payload


def _safe_artifact_path(run_directory: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DenseLyapunovError(
            f"{run_directory / 'run.json'}: invalid {label}"
        )
    relative = Path(value)
    if relative.is_absolute():
        raise DenseLyapunovError(
            f"{run_directory / 'run.json'}: absolute {label} is forbidden"
        )
    resolved_directory = run_directory.resolve()
    resolved = (run_directory / relative).resolve()
    if resolved != resolved_directory and resolved_directory not in resolved.parents:
        raise DenseLyapunovError(
            f"{run_directory / 'run.json'}: {label} escapes run directory"
        )
    return resolved


def discover_dense_runs(
    campaign_root: Path,
) -> list[tuple[DenseCase, Path, dict[str, Any]]]:
    """Find and identity-check exactly the four dense endpoint run manifests."""

    campaign_root = campaign_root.resolve()
    if not campaign_root.is_dir():
        raise DenseLyapunovError(
            f"campaign root does not exist or is not a directory: {campaign_root}"
        )

    dense: list[tuple[Path, dict[str, Any]]] = []
    for run_path in sorted(campaign_root.rglob("run.json")):
        payload = _read_json(run_path)
        if payload.get("endpoint_name") == ENDPOINT_NAME:
            dense.append((run_path, payload))
    if len(dense) != len(CASES):
        raise DenseLyapunovError(
            f"{campaign_root}: found {len(dense)} {ENDPOINT_NAME} run.json "
            f"files, expected exactly {len(CASES)}"
        )

    discovered: dict[
        tuple[str, str],
        tuple[DenseCase, Path, dict[str, Any]],
    ] = {}
    for run_path, payload in dense:
        cell = payload.get("cell")
        if not isinstance(cell, dict):
            raise DenseLyapunovError(f"{run_path}: missing cell object")
        key = (cell.get("board"), cell.get("representation"))
        case = CASE_BY_KEY.get(key)
        if case is None:
            raise DenseLyapunovError(
                f"{run_path}: unexpected dense lane {key!r}"
            )
        if key in discovered:
            raise DenseLyapunovError(
                f"{run_path}: duplicate dense lane {key!r}"
            )
        discovered[key] = (case, run_path, payload)

    missing = set(CASE_BY_KEY) - set(discovered)
    if missing:
        raise DenseLyapunovError(
            f"{campaign_root}: missing dense lanes {sorted(missing)!r}"
        )
    return [discovered[case.key] for case in CASES]


def _validate_run_contract(
    case: DenseCase,
    run_path: Path,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    _require(
        payload.get("schema"),
        "fractional-chaos-physical-run-v1",
        "schema",
        run_path,
    )
    _require(payload.get("endpoint_name"), ENDPOINT_NAME, "endpoint_name", run_path)

    cell = payload.get("cell", {})
    _require(cell.get("system"), SYSTEM, "cell.system", run_path)
    _require(cell.get("method"), METHOD, "cell.method", run_path)
    _require(cell.get("board"), case.board, "cell.board", run_path)
    _require(
        cell.get("representation"),
        case.run_representation,
        "cell.representation",
        run_path,
    )

    system = payload.get("system_contract", {})
    _require(system.get("manifest_id"), "lorenz_caputo_v1", "system manifest", run_path)
    _require_float(system.get("q"), FRACTIONAL_ORDER_Q, "system q", run_path)
    _require_float(system.get("dt_s"), MODEL_STEP, "system dt_s", run_path)
    _require(
        system.get("transient_steps"),
        TRANSIENT_STEPS,
        "system transient_steps",
        run_path,
    )
    method = payload.get("method_contract", {})
    _require(method.get("wire_name"), METHOD, "method wire_name", run_path)
    representation = payload.get("representation_contract", {})
    _require(
        representation.get("wire_kind"),
        case.kind,
        "representation wire_kind",
        run_path,
    )

    build = payload.get("build", {})
    _require(build.get("decimation"), 1, "build.decimation", run_path)
    _require(
        build.get("buffered_capture_mode"),
        True,
        "build.buffered_capture_mode",
        run_path,
    )
    _require(
        build.get("buffered_capture_samples"),
        EXPECTED_FRAMES,
        "build.buffered_capture_samples",
        run_path,
    )

    endpoint = payload.get("endpoint", {})
    endpoint_expected = {
        "rule": "exact_consecutive_model_step_sequence_window",
        "buffered_capture_mode": True,
        "output_decimation": 1,
        "first_sequence": EXPECTED_FIRST_SEQUENCE,
        "last_sequence": EXPECTED_LAST_SEQUENCE,
        "expected_first_sequence": EXPECTED_FIRST_SEQUENCE,
        "expected_last_sequence": EXPECTED_LAST_SEQUENCE,
        "expected_frames": EXPECTED_FRAMES,
        "received_frames": EXPECTED_FRAMES,
        "sequence_increment": 1,
        "complete": True,
    }
    for key, expected in endpoint_expected.items():
        _require(endpoint.get(key), expected, f"endpoint.{key}", run_path)

    transport = payload.get("transport", {})
    transport_expected = {
        "accepted": True,
        "valid_frames": EXPECTED_FRAMES,
        "matching_frames": EXPECTED_FRAMES,
        "identity_mismatches": 0,
        "sequence_gaps": 0,
        "nonzero_status_frames": 0,
        "nonzero_dropped_frames": 0,
        "crc_errors": 0,
        "invalid_headers": 0,
    }
    for key, expected in transport_expected.items():
        _require(transport.get(key), expected, f"transport.{key}", run_path)

    time_series = payload.get("time_series", {})
    _require(
        time_series.get("accepted_for_dense_series_analysis"),
        True,
        "time_series.accepted_for_dense_series_analysis",
        run_path,
    )
    _require(
        time_series.get("states_buffered_before_uart_drain"),
        EXPECTED_FRAMES,
        "time_series.states_buffered_before_uart_drain",
        run_path,
    )
    _require(
        time_series.get("sample_spacing_model_steps"),
        1,
        "time_series.sample_spacing_model_steps",
        run_path,
    )
    _require_float(
        time_series.get("sample_interval_model_time"),
        MODEL_STEP,
        "time_series.sample_interval_model_time",
        run_path,
    )
    _require(
        time_series.get("host_reception_time_is_model_time"),
        False,
        "time_series.host_reception_time_is_model_time",
        run_path,
    )
    _require(
        payload.get("eligible_as_primary_benchmark"),
        False,
        "eligible_as_primary_benchmark",
        run_path,
    )

    capture = payload.get("capture", {})
    run_directory = run_path.parent
    csv_path = _safe_artifact_path(
        run_directory,
        capture.get("csv_path"),
        "capture.csv_path",
    )
    raw_path = _safe_artifact_path(
        run_directory,
        capture.get("raw_path"),
        "capture.raw_path",
    )
    for path, expected_hash, label in (
        (csv_path, capture.get("csv_sha256"), "capture.csv_sha256"),
        (raw_path, capture.get("raw_sha256"), "capture.raw_sha256"),
    ):
        if not path.is_file():
            raise DenseLyapunovError(f"{run_path}: missing artifact {path}")
        actual_hash = sha256_file(path)
        _require(actual_hash, expected_hash, label, run_path)
    return csv_path, raw_path


def _load_dense_csv(case: DenseCase, path: Path) -> tuple[np.ndarray, np.ndarray]:
    required = {
        "kind",
        "board",
        "system",
        "method",
        "representation",
        "status",
        "dropped",
        "sequence",
        OBSERVABLE,
    }
    sequences: list[int] = []
    signal: list[float] = []
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise DenseLyapunovError(f"cannot open {path}: {exc}") from exc

    with stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise DenseLyapunovError(f"{path}: missing required UART columns")
        for row_index, row in enumerate(reader, start=1):
            _require(_parse_int(row, "kind", path), case.kind, "kind", path)
            _require(row["board"], case.board, "board", path)
            _require(row["system"], SYSTEM, "system", path)
            _require(row["method"], METHOD, "method", path)
            _require(
                row["representation"],
                case.csv_representation,
                "representation",
                path,
            )
            _require(_parse_int(row, "status", path), 0, "status", path)
            _require(_parse_int(row, "dropped", path), 0, "dropped", path)
            sequence = _parse_int(row, "sequence", path)
            _require(sequence, row_index, "sequence", path)
            sequences.append(sequence)
            signal.append(_parse_float(row, OBSERVABLE, path))

    if len(signal) != EXPECTED_FRAMES:
        raise DenseLyapunovError(
            f"{path}: contains {len(signal)} UART rows, expected {EXPECTED_FRAMES}"
        )
    sequence_array = np.asarray(sequences, dtype=np.int64)
    expected = np.arange(
        EXPECTED_FIRST_SEQUENCE,
        EXPECTED_LAST_SEQUENCE + 1,
        dtype=np.int64,
    )
    if not np.array_equal(sequence_array, expected):
        raise DenseLyapunovError(
            f"{path}: sequence must be exactly 1..{EXPECTED_LAST_SEQUENCE}"
        )
    return sequence_array, np.asarray(signal, dtype=float)


def load_dense_capture(
    case: DenseCase,
    run_path: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate one run manifest, its hashes, and its exact UART CSV."""

    csv_path, raw_path = _validate_run_contract(case, run_path, payload)
    sequences, signal = _load_dense_csv(case, csv_path)
    source = payload.get("source", {})
    return {
        "case": case,
        "run_payload": payload,
        "run_path": run_path,
        "run_sha256": sha256_file(run_path),
        "csv_path": csv_path,
        "csv_sha256": sha256_file(csv_path),
        "csv_bytes": csv_path.stat().st_size,
        "raw_path": raw_path,
        "raw_sha256": sha256_file(raw_path),
        "raw_bytes": raw_path.stat().st_size,
        "source_commit": source.get("commit", "unknown"),
        "source_dirty": source.get("dirty"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "sequences": sequences,
        "signal": signal,
    }


def load_dense_campaign(campaign_root: Path) -> list[dict[str, Any]]:
    return [
        load_dense_capture(case, run_path, payload)
        for case, run_path, payload in discover_dense_runs(campaign_root)
    ]


def load_hidden_api(
    source_root: Path,
) -> tuple[Callable[..., Any], dict[str, Any]]:
    """Import the exact local experimental Hidden Attractors FO API."""

    source_root = source_root.resolve()
    if not (source_root / "hidden_attractors").is_dir():
        raise DenseLyapunovError(
            f"Hidden Attractors source not found under {source_root}"
        )
    sys.path.insert(0, str(source_root))
    package = importlib.import_module("hidden_attractors")
    analysis = importlib.import_module(
        "hidden_attractors.analysis.time_series_lyapunov"
    )
    estimator = getattr(analysis, "estimate_time_series_lyapunov", None)
    if not callable(estimator):
        raise DenseLyapunovError(
            "Hidden Attractors has no callable time-series Lyapunov estimator"
        )
    module_path = Path(analysis.__file__).resolve()
    if source_root not in module_path.parents:
        raise DenseLyapunovError(
            f"loaded unexpected Hidden Attractors module: {module_path}"
        )

    try:
        package_version = version(HIDDEN_PACKAGE_NAME)
    except PackageNotFoundError:
        package_version = getattr(package, "__version__", "unknown")
    try:
        backend_version = version("nolds")
    except PackageNotFoundError as exc:
        raise DenseLyapunovError("nolds is required for this analysis") from exc

    repo_root = source_root.parent
    revision = "unknown"
    dirty: bool | None = None
    try:
        revision_process = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "rev-parse",
                "HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        revision = revision_process.stdout.strip()
        status_process = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo_root.as_posix()}",
                "-C",
                str(repo_root),
                "status",
                "--porcelain",
                "--",
                str(module_path.relative_to(repo_root)),
                "version_2/hidden_attractors/integrations/external_tools.py",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        dirty = bool(status_process.stdout.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass

    return estimator, {
        "package_name": HIDDEN_PACKAGE_NAME,
        "package_version": package_version,
        "pypi_url": HIDDEN_PYPI_URL,
        "source_root": str(source_root),
        "source_revision": revision,
        "time_series_source_dirty": dirty,
        "time_series_module": str(module_path),
        "time_series_module_sha256": sha256_file(module_path),
        "api_function": (
            "hidden_attractors.analysis.estimate_time_series_lyapunov"
        ),
        "backend": "nolds",
        "backend_version": backend_version,
        "availability_statement": (
            "hidden-attractors-fo is available from PyPI; this analysis records "
            "the exact local development revision that provides the scalar "
            "time-series API used here."
        ),
    }


def _window(
    capture: dict[str, Any],
    window_index: int,
) -> tuple[np.ndarray, np.ndarray]:
    first = TRANSIENT_STEPS + window_index * WINDOW_SAMPLES
    last = first + WINDOW_SAMPLES
    signal = np.asarray(capture["signal"][first:last], dtype=float)
    sequences = np.asarray(capture["sequences"][first:last], dtype=np.int64)
    if signal.size != WINDOW_SAMPLES:
        raise DenseLyapunovError(
            f"{capture['csv_path']}: incomplete analysis window {window_index}"
        )
    expected_first = first + EXPECTED_FIRST_SEQUENCE
    expected = np.arange(
        expected_first,
        expected_first + WINDOW_SAMPLES,
        dtype=np.int64,
    )
    if not np.array_equal(sequences, expected):
        raise DenseLyapunovError(
            f"{capture['csv_path']}: non-consecutive analysis window "
            f"{window_index}"
        )
    return sequences, signal


def _result_payload(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        payload = result.to_dict()
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        raise DenseLyapunovError("estimator returned no serializable result")
    required = {
        "largest_exponent",
        "spectrum",
        "kaplan_yorke_dimension",
        "spectrum_sum",
        "rosenstein_fit_r2",
        "rosenstein_parameters",
        "eckmann_parameters",
        "rosenstein_divergence_trajectory",
    }
    missing = required - set(payload)
    if missing:
        raise DenseLyapunovError(
            f"estimator result is missing fields: {sorted(missing)!r}"
        )
    try:
        spectrum = np.asarray(payload["spectrum"], dtype=float)
        largest = float(payload["largest_exponent"])
        dimension = float(payload["kaplan_yorke_dimension"])
        spectrum_sum = float(payload["spectrum_sum"])
    except (TypeError, ValueError) as exc:
        raise DenseLyapunovError("estimator returned non-numeric results") from exc
    if (
        spectrum.shape != (5,)
        or not np.all(np.isfinite(spectrum))
        or not all(math.isfinite(value) for value in (largest, dimension, spectrum_sum))
    ):
        raise DenseLyapunovError(
            "estimator must return five finite exponents and finite diagnostics"
        )
    return payload


def analyze_campaign(
    campaign_root: Path,
    estimator: Callable[..., Any],
) -> list[dict[str, Any]]:
    """Validate all captures and evaluate the frozen three-protocol design."""

    captures = load_dense_campaign(campaign_root)
    cases: list[dict[str, Any]] = []
    for case_index, capture in enumerate(captures):
        case: DenseCase = capture["case"]
        evaluations: list[dict[str, Any]] = []
        for protocol_index, protocol in enumerate(PROTOCOLS):
            sequences, signal = _window(
                capture,
                int(protocol["window_index"]),
            )
            parameters = dict(protocol["parameters"])
            result = estimator(
                signal,
                sample_interval=MODEL_STEP,
                time_unit=TIME_UNIT,
                observable=OBSERVABLE,
                **parameters,
                random_seed=(
                    ANALYSIS_SEED + case_index * 10 + protocol_index
                ),
                max_pairwise_matrix_bytes=MAX_PAIRWISE_MATRIX_BYTES,
            )
            payload = _result_payload(result)
            if "n_samples" in payload:
                _require(
                    payload["n_samples"],
                    WINDOW_SAMPLES,
                    "estimator n_samples",
                    capture["csv_path"],
                )
            if "sample_interval" in payload:
                _require_float(
                    payload["sample_interval"],
                    MODEL_STEP,
                    "estimator sample_interval",
                    capture["csv_path"],
                )
            evaluations.append(
                {
                    "analysis_id": protocol["analysis_id"],
                    "window_index": protocol["window_index"],
                    "window_role": protocol["window_role"],
                    "sequence_first": int(sequences[0]),
                    "sequence_last": int(sequences[-1]),
                    "sample_count": int(signal.size),
                    "sample_interval": MODEL_STEP,
                    "model_time_span": float(
                        (sequences[-1] - sequences[0]) * MODEL_STEP
                    ),
                    "signal_sha256_float64_le": sha256_signal(signal),
                    "parameter_contract": parameters,
                    **payload,
                }
            )

        primary_windows = evaluations[:2]
        largest_values = [
            float(item["largest_exponent"]) for item in primary_windows
        ]
        dimension_values = [
            float(item["kaplan_yorke_dimension"]) for item in primary_windows
        ]
        spectra = np.asarray(
            [item["spectrum"] for item in primary_windows],
            dtype=float,
        )
        cases.append(
            {
                "case_id": case.case_id,
                "board": case.board,
                "representation": case.run_representation,
                "csv_representation": case.csv_representation,
                "kind": case.kind,
                "system": SYSTEM,
                "integration_method": METHOD,
                "fractional_order_q": FRACTIONAL_ORDER_Q,
                "model_step": MODEL_STEP,
                "observable": OBSERVABLE,
                "run_json": display_path(capture["run_path"]),
                "run_json_sha256": capture["run_sha256"],
                "capture_csv": display_path(capture["csv_path"]),
                "capture_csv_sha256": capture["csv_sha256"],
                "capture_csv_bytes": capture["csv_bytes"],
                "capture_raw": display_path(capture["raw_path"]),
                "capture_raw_sha256": capture["raw_sha256"],
                "capture_raw_bytes": capture["raw_bytes"],
                "capture_manifest_sha256": capture["manifest_sha256"],
                "firmware_source_commit": capture["source_commit"],
                "firmware_source_dirty": capture["source_dirty"],
                "captured_sequences": [
                    EXPECTED_FIRST_SEQUENCE,
                    EXPECTED_LAST_SEQUENCE,
                ],
                "discarded_transient_sequences": [1, TRANSIENT_STEPS],
                "evaluations": evaluations,
                "two_window_summary": {
                    "largest_exponent_min": min(largest_values),
                    "largest_exponent_max": max(largest_values),
                    "kaplan_yorke_min": min(dimension_values),
                    "kaplan_yorke_max": max(dimension_values),
                    "spectrum_component_min": np.min(
                        spectra,
                        axis=0,
                    ).tolist(),
                    "spectrum_component_max": np.max(
                        spectra,
                        axis=0,
                    ).tolist(),
                    "interpretation": (
                        "two non-overlapping finite windows; not an uncertainty "
                        "interval or asymptotic convergence study"
                    ),
                },
                "claim_eligible": False,
                "interpretation": (
                    "exploratory dense-UART finite-time scalar diagnostic; "
                    "Lorenz failed the frozen ABM qualification"
                ),
            }
        )
    return cases


def save_summary_csv(cases: Sequence[dict[str, Any]], path: Path) -> None:
    columns = (
        "case_id",
        "board",
        "representation",
        "analysis_id",
        "window_role",
        "sequence_first",
        "sequence_last",
        "sample_count",
        "sample_interval",
        "largest_exponent",
        "rosenstein_fit_r2",
        "lambda_1",
        "lambda_2",
        "lambda_3",
        "lambda_4",
        "lambda_5",
        "spectrum_sum",
        "kaplan_yorke_dimension",
        "kaplan_yorke_status",
        "run_json_sha256",
        "capture_csv_sha256",
        "signal_sha256_float64_le",
        "claim_eligible",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for case in cases:
            for evaluation in case["evaluations"]:
                spectrum = evaluation["spectrum"]
                writer.writerow(
                    {
                        "case_id": case["case_id"],
                        "board": case["board"],
                        "representation": case["representation"],
                        "analysis_id": evaluation["analysis_id"],
                        "window_role": evaluation["window_role"],
                        "sequence_first": evaluation["sequence_first"],
                        "sequence_last": evaluation["sequence_last"],
                        "sample_count": evaluation["sample_count"],
                        "sample_interval": evaluation["sample_interval"],
                        "largest_exponent": evaluation["largest_exponent"],
                        "rosenstein_fit_r2": evaluation["rosenstein_fit_r2"],
                        "lambda_1": spectrum[0],
                        "lambda_2": spectrum[1],
                        "lambda_3": spectrum[2],
                        "lambda_4": spectrum[3],
                        "lambda_5": spectrum[4],
                        "spectrum_sum": evaluation["spectrum_sum"],
                        "kaplan_yorke_dimension": evaluation[
                            "kaplan_yorke_dimension"
                        ],
                        "kaplan_yorke_status": evaluation.get(
                            "kaplan_yorke_status",
                            "",
                        ),
                        "run_json_sha256": case["run_json_sha256"],
                        "capture_csv_sha256": case["capture_csv_sha256"],
                        "signal_sha256_float64_le": evaluation[
                            "signal_sha256_float64_le"
                        ],
                        "claim_eligible": case["claim_eligible"],
                    }
                )


def _format_spectrum(values: Sequence[float]) -> str:
    return "[" + ", ".join(f"{float(value):.8g}" for value in values) + "]"


def save_summary_markdown(
    cases: Sequence[dict[str, Any]],
    software: Mapping[str, Any],
    path: Path,
) -> None:
    lines = [
        "# Dense UART time-series Lyapunov diagnostic",
        "",
        (
            f"Software: `{software['package_name']} "
            f"{software['package_version']}` with `nolds "
            f"{software['backend_version']}`; exact local source revision "
            f"`{software['source_revision']}`."
        ),
        "",
        "## Sampling correction at acquisition",
        "",
        (
            "Each board buffers 12000 consecutive solver states before UART "
            "drain. The accepted sequence is exactly 1--12000 with "
            "`status=0`, `dropped=0`, and one row per integration step. "
            "The model-time interval is therefore h=0.005. Host reception "
            "time is not treated as model time."
        ),
        "",
        (
            "The first 2000 states are discarded. Two non-overlapping windows "
            "of 4096 x samples are used (sequences 2001--6096 and "
            "6097--10192). No interpolation, decimation, resampling, or "
            "cross-lane harmonization is performed."
        ),
        "",
        "## Numerical methods",
        "",
        (
            "The embedded trajectory uses M2sFRK for the q=0.995 fractional "
            "Lorenz pilot with h=0.005. Rosenstein estimates a scalar largest "
            "Lyapunov exponent; Eckmann reconstructs a five-component scalar "
            "spectrum; Kaplan--Yorke is algebraic post-processing of that "
            "ordered exploratory spectrum."
        ),
        "",
        (
            "Primary parameters follow the documented nolds Lorenz example: "
            "Rosenstein emb_dim=5, lag=5, min_tsep=10, min_neighbors=20, "
            "trajectory_len=28, fit=poly, fit_offset=8; Eckmann emb_dim=5, "
            "matrix_dim=5, min_neighbors=8, min_tsep=10."
        ),
        "",
        "## Primary window",
        "",
        "| Board | Representation | Rosenstein LLE | Eckmann spectrum | D_KY | R2 |",
        "|---|---|---:|---|---:|---:|",
    ]
    for case in cases:
        result = case["evaluations"][0]
        fit = result["rosenstein_fit_r2"]
        fit_text = "N/A" if fit is None else f"{float(fit):.5f}"
        lines.append(
            f"| {case['board'].upper()} | {case['representation']} | "
            f"{float(result['largest_exponent']):.8g} | "
            f"{_format_spectrum(result['spectrum'])} | "
            f"{float(result['kaplan_yorke_dimension']):.6f} | {fit_text} |"
        )

    lines.extend(
        [
            "",
            "## Non-overlapping window and parameter sensitivity",
            "",
            "| Board | Representation | Analysis | Rosenstein LLE | Eckmann spectrum | D_KY | R2 |",
            "|---|---|---|---:|---|---:|---:|",
        ]
    )
    for case in cases:
        for result in case["evaluations"][1:]:
            fit = result["rosenstein_fit_r2"]
            fit_text = "N/A" if fit is None else f"{float(fit):.5f}"
            lines.append(
                f"| {case['board'].upper()} | {case['representation']} | "
                f"{result['analysis_id']} | "
                f"{float(result['largest_exponent']):.8g} | "
                f"{_format_spectrum(result['spectrum'])} | "
                f"{float(result['kaplan_yorke_dimension']):.6f} | "
                f"{fit_text} |"
            )

    lines.extend(
        [
            "",
            "## Input provenance",
            "",
            "| Case | run.json SHA-256 | capture.csv SHA-256 | capture.bin SHA-256 |",
            "|---|---|---|---|",
        ]
    )
    for case in cases:
        lines.append(
            f"| {case['case_id']} | `{case['run_json_sha256']}` | "
            f"`{case['capture_csv_sha256']}` | "
            f"`{case['capture_raw_sha256']}` |"
        )
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- All estimates are finite-time and reconstructed from scalar x only.",
            "- The Eckmann spectrum and Kaplan--Yorke dimension are exploratory.",
            "- The second window and parameter perturbation are sensitivity checks, not uncertainty intervals.",
            "- Lorenz failed the frozen ABM qualification.",
            "- Formal chaos, asymptotic-spectrum, hiddenness, randomness, and cryptographic claims: 0.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def save_spectrum_figure(
    cases: Sequence[dict[str, Any]],
    path: Path,
) -> None:
    labels = [
        f"{case['board'].upper()} "
        f"{'float32' if case['representation'] == 'float32' else 'Q14/Q30'}"
        for case in cases
    ]
    colors = ("#0072B2", "#E69F00", "#009E73", "#CC79A7")
    fig, axes = plt.subplots(1, 4, figsize=(14.0, 4.3))
    exponent_index = np.arange(1, 6)
    for protocol_index, protocol in enumerate(PROTOCOLS):
        axis = axes[protocol_index]
        for case_index, case in enumerate(cases):
            evaluation = case["evaluations"][protocol_index]
            axis.plot(
                exponent_index,
                evaluation["spectrum"],
                "o-",
                color=colors[case_index],
                linewidth=1.0,
                markersize=4,
                label=labels[case_index],
            )
        axis.axhline(0.0, color="black", linewidth=0.7)
        axis.set_xticks(exponent_index)
        axis.set_xlabel("ordered exponent index")
        axis.set_ylabel(r"$\lambda_i$ [model time$^{-1}$]")
        axis.set_title(protocol["analysis_id"].replace("_", " "))

    x_positions = np.arange(len(PROTOCOLS), dtype=float)
    for case_index, case in enumerate(cases):
        dimensions = [
            float(evaluation["kaplan_yorke_dimension"])
            for evaluation in case["evaluations"]
        ]
        axes[3].plot(
            x_positions,
            dimensions,
            "o-",
            color=colors[case_index],
            linewidth=1.0,
            markersize=4,
            label=labels[case_index],
        )
    axes[3].set_xticks(
        x_positions,
        ("window 1", "window 2", "sensitivity"),
        rotation=20,
        ha="right",
    )
    axes[3].set_ylim(0.0, 5.15)
    axes[3].set_ylabel(r"exploratory Kaplan–Yorke $D_{KY}$")
    axes[3].set_title("Kaplan–Yorke comparison")

    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.925),
        ncols=4,
        frameon=False,
    )
    fig.suptitle(
        "Dense UART scalar-reconstruction spectra and Kaplan–Yorke diagnostics",
        y=0.99,
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.82))
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_divergence_figure(
    cases: Sequence[dict[str, Any]],
    path: Path,
) -> None:
    fig, axes = plt.subplots(
        len(cases),
        len(PROTOCOLS),
        figsize=(11.6, 9.0),
        squeeze=False,
    )
    for case_index, case in enumerate(cases):
        for protocol_index, evaluation in enumerate(case["evaluations"]):
            axis = axes[case_index, protocol_index]
            trajectory = np.asarray(
                evaluation.get(
                    "rosenstein_divergence_time_trajectory",
                    [
                        [float(point[0]) * MODEL_STEP, float(point[1])]
                        for point in evaluation[
                            "rosenstein_divergence_trajectory"
                        ]
                    ],
                ),
                dtype=float,
            )
            fit_offset = int(
                evaluation["rosenstein_parameters"]["fit_offset"]
            )
            fit_x = trajectory[fit_offset:, 0]
            fit_y = trajectory[fit_offset:, 1]
            axis.plot(
                trajectory[:, 0],
                trajectory[:, 1],
                "o-",
                color="#0072B2",
                linewidth=0.8,
                markersize=2.5,
            )
            if fit_x.size >= 2:
                polynomial = np.polyfit(fit_x, fit_y, 1)
                axis.plot(
                    fit_x,
                    np.polyval(polynomial, fit_x),
                    color="#D55E00",
                    linewidth=1.1,
                )
            fit_r2 = evaluation["rosenstein_fit_r2"]
            fit_text = "N/A" if fit_r2 is None else f"{float(fit_r2):.4f}"
            axis.set_title(
                f"{case['board'].upper()} / "
                f"{'float32' if case['representation'] == 'float32' else 'Q14/Q30'}\n"
                f"{evaluation['analysis_id']}, R2={fit_text}",
                fontsize=8,
            )
            axis.set_xlabel("future model time")
            axis.set_ylabel("mean log distance")
    fig.suptitle("Dense UART Rosenstein finite-window fit diagnostics")
    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_primary_divergence_figure(
    cases: Sequence[dict[str, Any]],
    path: Path,
) -> None:
    """Save a paper-scale view of the primary Rosenstein fits.

    When both MCUs reproduce a bit-identical signal, the compact figure plots
    that unique signal once.  Otherwise it keeps the board curves separate so
    a future campaign cannot silently imply cross-board equality.
    """

    representation_order = ("float32", "fixed_q14_q30")
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.7), squeeze=False)
    for axis, representation in zip(
        axes[0],
        representation_order,
        strict=True,
    ):
        group = [
            case
            for case in cases
            if case["representation"] == representation
        ]
        if len(group) != 2:
            raise ValueError(
                f"expected two board cases for {representation}, got "
                f"{len(group)}"
            )
        primary_evaluations = [case["evaluations"][0] for case in group]
        signal_hashes = {
            evaluation["signal_sha256_float64_le"]
            for evaluation in primary_evaluations
        }
        cross_board_identical = len(signal_hashes) == 1
        plotted = (
            [(group[0]["board"], primary_evaluations[0])]
            if cross_board_identical
            else [
                (case["board"], evaluation)
                for case, evaluation in zip(
                    group,
                    primary_evaluations,
                    strict=True,
                )
            ]
        )
        colors = ("#0072B2", "#009E73")
        fit_colors = ("#D55E00", "#CC79A7")
        for plot_index, (board, evaluation) in enumerate(plotted):
            trajectory = np.asarray(
                evaluation["rosenstein_divergence_time_trajectory"],
                dtype=float,
            )
            fit_offset = int(
                evaluation["rosenstein_parameters"]["fit_offset"]
            )
            fit_x = trajectory[fit_offset:, 0]
            fit_y = trajectory[fit_offset:, 1]
            board_label = (
                ""
                if cross_board_identical
                else f"{board.upper()} "
            )
            axis.plot(
                trajectory[:, 0],
                trajectory[:, 1],
                "o-",
                color=colors[plot_index],
                linewidth=0.9,
                markersize=3.0,
                label=f"{board_label}mean log divergence",
            )
            polynomial = np.polyfit(fit_x, fit_y, 1)
            axis.plot(
                fit_x,
                np.polyval(polynomial, fit_x),
                color=fit_colors[plot_index],
                linewidth=1.2,
                label=f"{board_label}reported linear fit",
            )
        evaluation = primary_evaluations[0]
        display_representation = (
            "float32"
            if representation == "float32"
            else "Q14/Q30"
        )
        board_relation = (
            "F746 = H755"
            if cross_board_identical
            else "F746 versus H755"
        )
        axis.set_title(
            f"{display_representation}; {board_relation}\n"
            f"LLE={float(evaluation['largest_exponent']):.6f}, "
            f"$R^2$={float(evaluation['rosenstein_fit_r2']):.5f}"
        )
        axis.set_xlabel("future model time")
        axis.set_ylabel("mean log distance")
        axis.grid(alpha=0.15)

    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.89),
        ncols=2,
        frameon=False,
    )
    fig.suptitle("Primary-window Rosenstein fit diagnostics", y=0.99)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.78))
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def build_summary(
    campaign_root: Path,
    cases: Sequence[dict[str, Any]],
    software: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "stm32-dense-uart-time-series-lyapunov-v1",
        "analysis_status": "completed_exploratory_diagnostic",
        "campaign_root": str(campaign_root.resolve()),
        "analysis_script": display_path(Path(__file__)),
        "analysis_script_sha256": sha256_file(Path(__file__)),
        "software": dict(software),
        "numerical_contract": {
            "embedded_integrator": "M2sFRK",
            "system": SYSTEM,
            "fractional_order_q": FRACTIONAL_ORDER_Q,
            "model_step": MODEL_STEP,
            "state_representations": ["float32", "fixed_q14_q30"],
            "time_series_estimators": [
                "Rosenstein largest Lyapunov exponent",
                "Eckmann five-component scalar-reconstruction spectrum",
            ],
            "kaplan_yorke_source": (
                "ordered exploratory five-component Eckmann spectrum"
            ),
        },
        "sampling_contract": {
            "endpoint_name": ENDPOINT_NAME,
            "required_runs": 4,
            "required_case_ids": [case.case_id for case in CASES],
            "observable": OBSERVABLE,
            "capture_sequence_first": EXPECTED_FIRST_SEQUENCE,
            "capture_sequence_last": EXPECTED_LAST_SEQUENCE,
            "capture_samples": EXPECTED_FRAMES,
            "sequence_increment": 1,
            "discarded_transient_samples": TRANSIENT_STEPS,
            "analysis_windows": [
                {
                    "window_index": 0,
                    "sequence_first": 2001,
                    "sequence_last": 6096,
                    "samples": WINDOW_SAMPLES,
                },
                {
                    "window_index": 1,
                    "sequence_first": 6097,
                    "sequence_last": 10192,
                    "samples": WINDOW_SAMPLES,
                },
            ],
            "sample_interval": MODEL_STEP,
            "time_unit": TIME_UNIT,
            "interpolation": False,
            "decimation": False,
            "resampling": False,
            "cross_lane_harmonization": False,
            "host_reception_time_used": False,
            "spacing_statement": (
                "sampling spacing is corrected at acquisition by buffering "
                "consecutive solver states before UART drain"
            ),
        },
        "estimator_contract": {
            "primary_parameters": PRIMARY_PARAMETERS,
            "sensitivity_parameters": SENSITIVITY_PARAMETERS,
            "primary_parameter_provenance": (
                "documented nolds Lorenz example"
            ),
            "primary_parameter_reference": NOLDS_LORENZ_EXAMPLE_URL,
            "protocols": [
                {
                    key: value
                    for key, value in protocol.items()
                    if key != "parameters"
                }
                for protocol in PROTOCOLS
            ],
            "random_seed_base": ANALYSIS_SEED,
            "max_pairwise_matrix_bytes": MAX_PAIRWISE_MATRIX_BYTES,
        },
        "scientific_claims": {
            "formal_chaos_claims": 0,
            "asymptotic_lyapunov_spectrum_claims": 0,
            "primary_kaplan_yorke_claims": 0,
            "hiddenness_claims": 0,
            "randomness_claims": 0,
            "cryptographic_claims": 0,
            "reason": (
                "Lorenz failed the frozen ABM qualification; all reported "
                "quantities are finite-time scalar-reconstruction diagnostics."
            ),
        },
        "cases": list(cases),
    }


def write_outputs(
    output: Path,
    summary: dict[str, Any],
) -> dict[str, Path]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = summary["cases"]
    software = summary["software"]
    paths = {
        "json": output / "summary.json",
        "csv": output / "summary.csv",
        "markdown": output / "summary.md",
        "spectrum_figure": (
            output / "dense_uart_lyapunov_spectra_kaplan_yorke.png"
        ),
        "divergence_figure": (
            output / "dense_uart_rosenstein_divergence_fits.png"
        ),
        "primary_divergence_figure": (
            output / "dense_uart_rosenstein_primary_fits.png"
        ),
    }
    paths["json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    save_summary_csv(cases, paths["csv"])
    save_summary_markdown(cases, software, paths["markdown"])
    save_spectrum_figure(cases, paths["spectrum_figure"])
    save_divergence_figure(cases, paths["divergence_figure"])
    save_primary_divergence_figure(
        cases,
        paths["primary_divergence_figure"],
    )
    return paths


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze exactly four lossless dense UART Lorenz/M2sFRK captures"
        )
    )
    parser.add_argument(
        "--campaign-root",
        type=Path,
        required=True,
        help="root searched recursively for dense_timeseries_pilot run.json files",
    )
    parser.add_argument(
        "--hidden-attractors-source",
        type=Path,
        default=DEFAULT_HIDDEN_SOURCE,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    estimator, software = load_hidden_api(args.hidden_attractors_source)
    cases = analyze_campaign(args.campaign_root, estimator)
    summary = build_summary(args.campaign_root, cases, software)
    outputs = write_outputs(args.output_dir, summary)
    print(f"Wrote {outputs['json']}")
    for case in cases:
        primary = case["evaluations"][0]
        print(
            f"{case['case_id']}: "
            f"LLE={float(primary['largest_exponent']):.8g}, "
            f"spectrum={_format_spectrum(primary['spectrum'])}, "
            f"D_KY={float(primary['kaplan_yorke_dimension']):.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
