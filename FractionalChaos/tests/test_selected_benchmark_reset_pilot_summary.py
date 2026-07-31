#!/usr/bin/env python3
"""Pruebas del agregado físico seleccionado de 36 celdas."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "validation"
    / "summarize_selected_benchmark_reset_pilot.py"
)
SPEC = importlib.util.spec_from_file_location(
    "summarize_selected_benchmark_reset_pilot",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def aggregate():
    return MODULE.build_report()


def test_live_snapshot_selects_exact_matrix_and_retains_failures(
    aggregate,
) -> None:
    report, cells, attempts = aggregate
    assert report["status"] == (
        "accepted_36_of_36_selected_reset_pilot_cells"
    )
    assert len(cells) == 36
    assert len({cell["cell_id"] for cell in cells}) == 36
    assert sum(cell["n_cycle_values"] for cell in cells) == 360_000
    assert Counter(
        cell["hardware_reset_repetition_id"] for cell in cells
    ) == Counter({4: 33, 5: 3})
    assert {
        cell["cell_id"]
        for cell in cells
        if cell["hardware_reset_repetition_id"] == 5
    } == MODULE.R05_REPLACEMENT_CELLS
    assert all(cell["source_dirty"] is True for cell in cells)
    assert len({cell["source_commit"] for cell in cells}) == 1

    inventory = report["attempt_inventory"]
    assert inventory == {
        "total_attempts": 45,
        "accepted_attempts": 40,
        "failed_attempts": 5,
        "selected_attempts": 36,
        "accepted_attempts_not_selected": 4,
        "failed_attempts_included_without_exclusion": True,
    }
    failures = [
        attempt
        for attempt in attempts
        if attempt["artifact_kind"] == "recorded_failure"
    ]
    assert len(failures) == 5
    assert all(attempt["selected"] is False for attempt in failures)
    assert all(attempt["error"] for attempt in failures)
    assert {
        attempt["cell_id"]
        for attempt in failures
        if attempt["selection_role"]
        == "failed_r04_retained_replaced_by_r05"
    } == MODULE.R05_REPLACEMENT_CELLS


def test_every_selected_capture_and_hash_was_validated(aggregate) -> None:
    _report, cells, attempts = aggregate
    selected_ids = {
        attempt["run_id"]
        for attempt in attempts
        if attempt["selected"]
    }
    assert selected_ids == {cell["run_id"] for cell in cells}
    for cell in cells:
        for key in (
            "run_json_sha256",
            "capture_bin_sha256",
            "capture_csv_sha256",
            "timing_cycles_sha256",
        ):
            assert len(cell[key]) == 64
            int(cell[key], 16)
        assert cell["minimum_cycles"] > 0
        assert cell["maximum_cycles"] >= cell["minimum_cycles"]


def test_evidence_boundaries_forbid_primary_or_inferential_claims(
    aggregate,
) -> None:
    report, _cells, _attempts = aggregate
    boundaries = report["evidence_boundaries"]
    assert boundaries["power_removed"] is False
    assert boundaries["n_experimental_units_per_cell"] == 1
    assert boundaries["eligible_as_primary_benchmark"] is False
    assert boundaries["source_dirty_values"] == [True]
    assert boundaries["clean_source_provenance"] is False
    assert boundaries["inferential_statistics_supported"] is False
    assert boundaries["between_reset_variability_supported"] is False
    assert len(boundaries["prohibitions"]) >= 5


def test_transport_contract_mutation_is_rejected() -> None:
    manifest = MODULE.load_manifest()
    specs = MODULE.expected_cell_specs(manifest)
    run_path = next(
        (
            MODULE.DEFAULT_CAMPAIGN_ROOT
            / "runs"
        ).glob(
            "stm32_selected_36x30_v1__r04__chen_efork3_f746_float32__"
            "*__benchmark-reset-pilot/run.json"
        )
    )
    payload = MODULE.load_json_object(run_path)
    payload["transport"]["crc_errors"] = 1
    with pytest.raises(
        MODULE.SelectedPilotSummaryError,
        match=r"transport\.crc_errors",
    ):
        MODULE.validate_successful_attempt(
            run_path,
            payload,
            specs["chen_efork3_f746_float32"],
            4,
            manifest,
        )


def test_source_provenance_type_mutation_is_rejected() -> None:
    manifest = MODULE.load_manifest()
    specs = MODULE.expected_cell_specs(manifest)
    run_path = next(
        (
            MODULE.DEFAULT_CAMPAIGN_ROOT
            / "runs"
        ).glob(
            "stm32_selected_36x30_v1__r04__chen_efork3_f746_float32__"
            "*__benchmark-reset-pilot/run.json"
        )
    )
    payload = MODULE.load_json_object(run_path)
    payload["source"]["dirty"] = "true"
    with pytest.raises(
        MODULE.SelectedPilotSummaryError,
        match=r"source\.dirty debe ser booleano",
    ):
        MODULE.validate_successful_attempt(
            run_path,
            payload,
            specs["chen_efork3_f746_float32"],
            4,
            manifest,
        )


def test_binary_crc_mutation_is_rejected(tmp_path: Path) -> None:
    manifest = MODULE.load_manifest()
    specs = MODULE.expected_cell_specs(manifest)
    source = next(
        (
            MODULE.DEFAULT_CAMPAIGN_ROOT
            / "runs"
        ).glob(
            "stm32_selected_36x30_v1__r04__chen_efork3_f746_float32__"
            "*__benchmark-reset-pilot/capture.bin"
        )
    )
    raw = bytearray(source.read_bytes())
    raw[24] ^= 1
    damaged = tmp_path / "capture.bin"
    damaged.write_bytes(raw)
    with pytest.raises(
        MODULE.SelectedPilotSummaryError,
        match="CRC inválido",
    ):
        MODULE.parse_capture_binary(
            damaged,
            specs["chen_efork3_f746_float32"],
            manifest,
        )


def test_outputs_are_hash_linked_and_reproducible(
    aggregate,
    tmp_path: Path,
) -> None:
    report, cells, attempts = aggregate
    output = tmp_path / "aggregate"
    first_report = copy.deepcopy(report)
    paths = MODULE.write_outputs(first_report, cells, attempts, output)
    first_bytes = {
        name: path.read_bytes() for name, path in paths.items()
    }

    persisted = json.loads(paths["summary"].read_text(encoding="utf-8"))
    for name in ("cells.csv", "attempts.csv", "report.md"):
        artifact = persisted["artifacts"][name]
        path = output / name
        assert artifact["sha256"] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        assert artifact["bytes"] == path.stat().st_size

    second_report = copy.deepcopy(report)
    paths = MODULE.write_outputs(second_report, cells, attempts, output)
    assert {
        name: path.read_bytes() for name, path in paths.items()
    } == first_bytes


def test_persisted_default_outputs_match_current_inputs(aggregate) -> None:
    _report, cells, attempts = aggregate
    output = MODULE.DEFAULT_OUTPUT_DIR
    persisted = json.loads(
        (output / "summary.json").read_text(encoding="utf-8")
    )
    assert persisted["cells"] == cells
    assert persisted["attempts"] == attempts
    assert persisted["provenance"]["generator_sha256"] == hashlib.sha256(
        SCRIPT.read_bytes()
    ).hexdigest()
    for name in ("cells.csv", "attempts.csv", "report.md"):
        path = output / name
        assert persisted["artifacts"][name]["sha256"] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
