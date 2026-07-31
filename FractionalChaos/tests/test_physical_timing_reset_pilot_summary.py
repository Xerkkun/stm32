from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "summarize_physical_timing_reset_pilot.py"
)
SPEC = importlib.util.spec_from_file_location(
    "summarize_physical_timing_reset_pilot",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def current_report() -> tuple[dict, list[dict]]:
    return MODULE.build_report(MODULE.DEFAULT_CAMPAIGN_ROOT)


def write_cycles(path: Path, count: int = 10_000) -> str:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("timed_index", "cycles"))
        writer.writerows((index, 100 + (index % 5)) for index in range(count))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_expected_matrix_is_exactly_twelve_unique_cells() -> None:
    cells = list(MODULE.expected_cells())

    assert len(cells) == 12
    assert len(set(cells)) == 12
    assert MODULE.expected_reset_repetition(
        "m2sfrk", "h755", "float32"
    ) == 2
    assert MODULE.expected_reset_repetition(
        "m2sfrk", "h755", "fixed_q14_q30"
    ) == 1


def test_current_evidence_has_one_accepted_run_per_cell(
    current_report: tuple[dict, list[dict]],
) -> None:
    report, cells = current_report

    assert report["status"] == "accepted_12_of_12_reset_pilot_cells"
    assert len(cells) == 12
    assert sum(row["n_cycle_values"] for row in cells) == 120_000
    assert all(row["system"] == "chen" for row in cells)
    assert all(row["minimum_cycles"] > 0 for row in cells)
    assert len({row["run_id"] for row in cells}) == 12
    h755_m2_float = next(
        row
        for row in cells
        if (
            row["method"],
            row["board"],
            row["representation"],
        )
        == ("m2sfrk", "h755", "float32")
    )
    assert h755_m2_float["hardware_reset_repetition_id"] == 2
    assert all(
        row["hardware_reset_repetition_id"] == 1
        for row in cells
        if row is not h755_m2_float
    )
    assert report["evidence_boundaries"] == {
        "power_removed": False,
        "reset_mode": "stlink_hardware_reset",
        "reset_repetitions_per_cell": 1,
        "eligible_as_paper_cold_start": False,
        "eligible_as_primary_benchmark": False,
        "inferential_statistics_supported": False,
        "limitations": [
            "N=1 ST-LINK hardware-reset repetition per cell",
            "No physical power cycle was performed",
            "No between-reset variability or inferential claim is supported",
            "This reset pilot is not the preregistered primary benchmark",
            (
                "Raw cycle counts do not by themselves establish "
                "cross-board wall-clock speed"
            ),
        ],
    }


def test_timing_reader_checks_hash_count_indices_and_uint32(
    tmp_path: Path,
) -> None:
    timing_path = tmp_path / "timing_cycles.csv"
    digest = write_cycles(timing_path)

    values = MODULE.read_timing_cycles(timing_path, digest)
    assert len(values) == 10_000
    assert MODULE.cycle_statistics(values)["median_cycles"] == 102.0

    with pytest.raises(MODULE.TimingSummaryError, match="SHA-256"):
        MODULE.read_timing_cycles(timing_path, "0" * 64)

    short_path = tmp_path / "short.csv"
    short_digest = write_cycles(short_path, count=9_999)
    with pytest.raises(MODULE.TimingSummaryError, match="9999 valores"):
        MODULE.read_timing_cycles(short_path, short_digest)

    rows = timing_path.read_text(encoding="utf-8").splitlines()
    rows[11] = "99,101"
    timing_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    bad_index_digest = hashlib.sha256(timing_path.read_bytes()).hexdigest()
    with pytest.raises(MODULE.TimingSummaryError, match="timed_index"):
        MODULE.read_timing_cycles(timing_path, bad_index_digest)


def test_selector_rejects_more_than_one_accepted_run_per_cell(
    tmp_path: Path,
) -> None:
    source_runs = MODULE.select_accepted_runs(MODULE.DEFAULT_CAMPAIGN_ROOT)
    run_root = tmp_path / "runs"
    run_root.mkdir()
    for run_path, payload in source_runs.values():
        copied_dir = run_root / payload["run_id"]
        copied_dir.mkdir()
        (copied_dir / "run.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    duplicate_payload = json.loads(
        json.dumps(source_runs[("gl", "f746", "float32")][1])
    )
    original_id = duplicate_payload["run_id"]
    duplicate_id = original_id.replace(
        "__e43e4c0e55__",
        "__aaaaaaaaaa__",
    )
    assert duplicate_id != original_id
    duplicate_payload["run_id"] = duplicate_id
    duplicate_payload["scheduled_primary_run_id"] = (
        duplicate_id.removesuffix("__benchmark-reset-pilot")
    )
    duplicate_dir = run_root / duplicate_id
    duplicate_dir.mkdir()
    (duplicate_dir / "run.json").write_text(
        json.dumps(duplicate_payload),
        encoding="utf-8",
    )

    with pytest.raises(
        MODULE.TimingSummaryError,
        match="más de una repetición aceptada",
    ):
        MODULE.select_accepted_runs(tmp_path)


def test_outputs_are_labeled_and_hashed(
    current_report: tuple[dict, list[dict]],
    tmp_path: Path,
) -> None:
    report, cells = current_report
    report_copy = json.loads(json.dumps(report))

    MODULE.write_outputs(report_copy, cells, tmp_path)

    summary_path = tmp_path / "summary.json"
    csv_path = tmp_path / "summary.csv"
    figure_path = tmp_path / "timing_cycles_reset_pilot_chen.png"
    assert summary_path.is_file()
    assert csv_path.is_file()
    assert figure_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["evidence_boundaries"]["power_removed"] is False
    assert summary["evidence_boundaries"][
        "eligible_as_primary_benchmark"
    ] is False
    assert summary["artifacts"]["summary.csv"]["sha256"] == hashlib.sha256(
        csv_path.read_bytes()
    ).hexdigest()
    assert summary["artifacts"][
        "timing_cycles_reset_pilot_chen.png"
    ]["sha256"] == hashlib.sha256(figure_path.read_bytes()).hexdigest()
    with csv_path.open("r", newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert {
        (row["method"], row["board"], row["representation"])
        for row in rows
    } == set(MODULE.expected_cells())
