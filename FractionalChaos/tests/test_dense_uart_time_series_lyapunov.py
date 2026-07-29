"""Tests for the strict dense-UART Lyapunov evidence lane."""

from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "validation" / "analyze_dense_uart_time_series_lyapunov.py"
SPEC = importlib.util.spec_from_file_location(
    "dense_uart_time_series_lyapunov",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _write_csv(path: Path, case: Any) -> None:
    fields = (
        "kind",
        "board",
        "system",
        "method",
        "representation",
        "status",
        "dropped",
        "sequence",
        "x",
    )
    board_offset = 0.2 if case.board == "h755" else 0.0
    representation_offset = (
        0.1 if case.run_representation == "fixed_q14_q30" else 0.0
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for sequence in range(1, module.EXPECTED_FRAMES + 1):
            writer.writerow(
                {
                    "kind": case.kind,
                    "board": case.board,
                    "system": module.SYSTEM,
                    "method": module.METHOD,
                    "representation": case.csv_representation,
                    "status": 0,
                    "dropped": 0,
                    "sequence": sequence,
                    "x": (
                        np.sin(sequence * 0.01)
                        + 0.2 * np.sin(sequence * 0.031)
                        + board_offset
                        + representation_offset
                    ),
                }
            )


def _run_payload(case: Any, csv_path: Path, raw_path: Path) -> dict[str, Any]:
    return {
        "schema": "fractional-chaos-physical-run-v1",
        "run_id": f"test__{case.case_id}__dense-timeseries-pilot",
        "campaign_id": "test_dense",
        "manifest_sha256": "a" * 64,
        "endpoint_name": module.ENDPOINT_NAME,
        "cell": {
            "cell_id": f"lorenz_m2sfrk_{case.board}_"
            f"{'fixed' if case.kind == 3 else 'float32'}",
            "system": module.SYSTEM,
            "method": module.METHOD,
            "board": case.board,
            "representation": case.run_representation,
        },
        "system_contract": {
            "manifest_id": "lorenz_caputo_v1",
            "q": module.FRACTIONAL_ORDER_Q,
            "dt_s": module.MODEL_STEP,
            "transient_steps": module.TRANSIENT_STEPS,
        },
        "method_contract": {"wire_name": module.METHOD},
        "representation_contract": {"wire_kind": case.kind},
        "build": {
            "decimation": 1,
            "buffered_capture_mode": True,
            "buffered_capture_samples": module.EXPECTED_FRAMES,
        },
        "endpoint": {
            "rule": "exact_consecutive_model_step_sequence_window",
            "buffered_capture_mode": True,
            "output_decimation": 1,
            "first_sequence": 1,
            "last_sequence": module.EXPECTED_FRAMES,
            "expected_first_sequence": 1,
            "expected_last_sequence": module.EXPECTED_FRAMES,
            "expected_frames": module.EXPECTED_FRAMES,
            "received_frames": module.EXPECTED_FRAMES,
            "sequence_increment": 1,
            "complete": True,
        },
        "transport": {
            "accepted": True,
            "valid_frames": module.EXPECTED_FRAMES,
            "matching_frames": module.EXPECTED_FRAMES,
            "identity_mismatches": 0,
            "sequence_gaps": 0,
            "nonzero_status_frames": 0,
            "nonzero_dropped_frames": 0,
            "crc_errors": 0,
            "invalid_headers": 0,
        },
        "time_series": {
            "accepted_for_dense_series_analysis": True,
            "states_buffered_before_uart_drain": module.EXPECTED_FRAMES,
            "sample_spacing_model_steps": 1,
            "sample_interval_model_time": module.MODEL_STEP,
            "host_reception_time_is_model_time": False,
        },
        "eligible_as_primary_benchmark": False,
        "capture": {
            "csv_path": csv_path.name,
            "csv_sha256": module.sha256_file(csv_path),
            "raw_path": raw_path.name,
            "raw_sha256": module.sha256_file(raw_path),
        },
        "source": {
            "commit": "1" * 40,
            "dirty": False,
        },
    }


def _write_campaign(root: Path) -> Path:
    for case in module.CASES:
        run_directory = root / "runs" / case.case_id
        run_directory.mkdir(parents=True)
        csv_path = run_directory / "capture.csv"
        raw_path = run_directory / "capture.bin"
        _write_csv(csv_path, case)
        raw_path.write_bytes(f"raw:{case.case_id}".encode("ascii"))
        payload = _run_payload(case, csv_path, raw_path)
        (run_directory / "run.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture(scope="module")
def valid_campaign(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _write_campaign(tmp_path_factory.mktemp("dense_campaign"))


class FakeResult:
    def __init__(self, kwargs: dict[str, Any], signal: np.ndarray) -> None:
        scale = float(np.mean(signal))
        spectrum = [
            0.8 + 0.01 * scale,
            0.05,
            -0.12,
            -0.35,
            -0.9,
        ]
        trajectory_len = int(kwargs["rosenstein_trajectory_len"])
        trajectory = [
            [float(index), -2.0 + 0.02 * index]
            for index in range(trajectory_len)
        ]
        fit_offset = int(kwargs["rosenstein_fit_offset"])
        self.payload = {
            "largest_exponent": 0.7 + 0.01 * scale,
            "spectrum": spectrum,
            "kaplan_yorke_dimension": 3.2,
            "kaplan_yorke_status": (
                "computed_from_exploratory_eckmann_spectrum"
            ),
            "spectrum_sum": sum(spectrum),
            "spectrum_status": (
                "exploratory_nolds_eckmann_scalar_reconstruction"
            ),
            "sample_interval": kwargs["sample_interval"],
            "n_samples": len(signal),
            "rosenstein_fit_r2": 0.95,
            "rosenstein_parameters": {
                "emb_dim": kwargs["rosenstein_emb_dim"],
                "lag": kwargs["rosenstein_lag"],
                "min_tsep": kwargs["rosenstein_min_tsep"],
                "min_neighbors": kwargs["rosenstein_min_neighbors"],
                "trajectory_len": trajectory_len,
                "fit": kwargs["rosenstein_fit"],
                "fit_offset": fit_offset,
            },
            "eckmann_parameters": {
                "emb_dim": kwargs["eckmann_emb_dim"],
                "matrix_dim": kwargs["eckmann_matrix_dim"],
                "min_neighbors": kwargs["eckmann_min_neighbors"],
                "min_tsep": kwargs["eckmann_min_tsep"],
            },
            "rosenstein_divergence_trajectory": trajectory,
            "rosenstein_divergence_time_trajectory": [
                [point[0] * module.MODEL_STEP, point[1]]
                for point in trajectory
            ],
            "warnings": ["test diagnostic only"],
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class RecordingEstimator:
    def __init__(self) -> None:
        self.calls: list[tuple[np.ndarray, dict[str, Any]]] = []

    def __call__(self, signal: Any, **kwargs: Any) -> FakeResult:
        values = np.asarray(signal, dtype=float)
        self.calls.append((values.copy(), dict(kwargs)))
        return FakeResult(kwargs, values)


def test_load_dense_campaign_enforces_exact_native_sequences(
    valid_campaign: Path,
) -> None:
    captures = module.load_dense_campaign(valid_campaign)

    assert [capture["case"].case_id for capture in captures] == [
        case.case_id for case in module.CASES
    ]
    for capture in captures:
        assert capture["sequences"][0] == 1
        assert capture["sequences"][-1] == 12_000
        assert np.all(np.diff(capture["sequences"]) == 1)
        first_sequences, first_signal = module._window(capture, 0)
        second_sequences, second_signal = module._window(capture, 1)
        assert (first_sequences[0], first_sequences[-1]) == (2001, 6096)
        assert (second_sequences[0], second_sequences[-1]) == (6097, 10192)
        assert first_signal.size == second_signal.size == 4096


def test_analyze_campaign_freezes_protocol_and_writes_all_artifacts(
    valid_campaign: Path,
    tmp_path: Path,
) -> None:
    estimator = RecordingEstimator()
    cases = module.analyze_campaign(valid_campaign, estimator)

    assert len(cases) == 4
    assert len(estimator.calls) == 12
    for case in cases:
        assert len(case["evaluations"]) == 3
        assert [
            evaluation["analysis_id"] for evaluation in case["evaluations"]
        ] == [
            "primary_window_1",
            "primary_window_2",
            "parameter_sensitivity_window_1",
        ]
        assert case["evaluations"][0]["sequence_first"] == 2001
        assert case["evaluations"][1]["sequence_first"] == 6097
        assert case["evaluations"][2]["sequence_first"] == 2001
        assert all(
            len(evaluation["spectrum"]) == 5
            for evaluation in case["evaluations"]
        )
        assert case["claim_eligible"] is False

    first_primary = estimator.calls[0][1]
    assert first_primary["sample_interval"] == pytest.approx(0.005)
    assert first_primary["rosenstein_emb_dim"] == 5
    assert first_primary["rosenstein_lag"] == 5
    assert first_primary["rosenstein_min_tsep"] == 10
    assert first_primary["rosenstein_trajectory_len"] == 28
    assert first_primary["rosenstein_fit_offset"] == 8
    assert first_primary["eckmann_matrix_dim"] == 5
    assert first_primary["eckmann_min_tsep"] == 10
    first_sensitivity = estimator.calls[2][1]
    assert first_sensitivity["rosenstein_lag"] == 10
    assert first_sensitivity["rosenstein_min_tsep"] == 20
    assert first_sensitivity["rosenstein_trajectory_len"] == 56
    assert first_sensitivity["rosenstein_fit_offset"] == 16
    assert first_sensitivity["eckmann_min_tsep"] == 20

    software = {
        "package_name": "hidden-attractors-fo",
        "package_version": "1.0.0",
        "backend_version": "0.6.3",
        "source_revision": "2" * 40,
    }
    summary = module.build_summary(valid_campaign, cases, software)
    outputs = module.write_outputs(tmp_path / "outputs", summary)

    assert set(outputs) == {
        "json",
        "csv",
        "markdown",
        "spectrum_figure",
        "divergence_figure",
    }
    assert all(path.is_file() and path.stat().st_size > 0 for path in outputs.values())
    written = json.loads(outputs["json"].read_text(encoding="utf-8"))
    sampling = written["sampling_contract"]
    assert sampling["sample_interval"] == pytest.approx(0.005)
    assert sampling["interpolation"] is False
    assert sampling["decimation"] is False
    assert sampling["resampling"] is False
    assert sampling["cross_lane_harmonization"] is False
    assert written["scientific_claims"]["formal_chaos_claims"] == 0
    with outputs["csv"].open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert all(row["lambda_5"] for row in rows)


def test_discovery_rejects_anything_other_than_four_dense_runs(
    valid_campaign: Path,
    tmp_path: Path,
) -> None:
    copied = tmp_path / "campaign"
    shutil.copytree(valid_campaign, copied)
    (copied / "runs" / module.CASES[-1].case_id / "run.json").unlink()

    with pytest.raises(module.DenseLyapunovError, match="found 3"):
        module.discover_dense_runs(copied)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("sequence", "999", "sequence"),
        ("dropped", "1", "dropped"),
        ("status", "1", "status"),
    ),
)
def test_csv_rejects_spacing_loss_or_transport_flags(
    valid_campaign: Path,
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    copied = tmp_path / f"campaign_{field}"
    shutil.copytree(valid_campaign, copied)
    run_directory = copied / "runs" / module.CASES[0].case_id
    csv_path = run_directory / "capture.csv"
    with csv_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fields = list(rows[0])
    rows[99][field] = replacement
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    run_path = run_directory / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["capture"]["csv_sha256"] = module.sha256_file(csv_path)
    run_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.DenseLyapunovError, match=message):
        module.load_dense_campaign(copied)


def test_frozen_dense_contract_uses_no_post_hoc_spacing_correction() -> None:
    assert module.MODEL_STEP == pytest.approx(0.005)
    assert module.EXPECTED_FRAMES == 12_000
    assert module.TRANSIENT_STEPS == 2_000
    assert module.WINDOW_SAMPLES == 4_096
    assert module.PRIMARY_PARAMETERS == {
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
