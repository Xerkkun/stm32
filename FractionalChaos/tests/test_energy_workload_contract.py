from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "validation" / "energy_workload_contract_v1.json"
MANIFEST_PATH = ROOT / "validation" / "physical_campaign_selected_v1.json"
ATTEMPTS_PATH = (
    ROOT
    / "validation"
    / "results"
    / "physical_campaign"
    / "stm32_selected_36x30_v1"
    / "aggregates"
    / "benchmark_reset_pilot_matrix_v1"
    / "attempts.csv"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_energy_workload_contract_is_complete_and_frozen() -> None:
    contract = _contract()
    assert contract["schema"] == "fractional-chaos-energy-workload-contract-v1"
    assert (
        contract["status"]
        == "predeclared_before_external_energy_measurement"
    )
    work_unit = contract["work_unit"]
    assert work_unit["base_work_units"] == 10_000
    assert work_unit["timing_values_retained"] == 10_000
    assert work_unit["energy_window_includes_retained_timing_steps"] is True
    assert (
        work_unit["supplemental_steps_use_same_solver_step_cycles_call"] is True
    )
    assert work_unit["uart_inside_energy_window"] is False

    multipliers = contract["multipliers"]
    assert set(multipliers) == {"f746", "h755"}
    for board in ("f746", "h755"):
        assert set(multipliers[board]) == {"efork3", "gl", "m2sfrk"}
        for method in ("efork3", "gl", "m2sfrk"):
            profile = multipliers[board][method]
            assert set(profile) == {"float32", "fixed_q14_q30"}
            assert all(
                isinstance(value, int) and 1 <= value <= 512
                for value in profile.values()
            )

    sizing = contract["sizing_basis"]
    source = ROOT / sizing["source"]
    assert source.is_file()
    assert _sha256(source) == sizing["source_sha256"]
    assert sizing["use"] == "acquisition_duration_sizing_only"
    assert sizing["source_dirty"] is True


def test_selected_pilots_size_every_energy_window_above_minimum() -> None:
    contract = _contract()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    minimum_s = float(contract["capture_requirement"]["minimum_window_s"])
    selected: list[dict[str, str]] = []
    with ATTEMPTS_PATH.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["selected"].lower() == "true":
                selected.append(row)
    assert len(selected) == 36

    observed_windows: list[float] = []
    for row in selected:
        board = row["cell_id"].split("_")[-2]
        representation = (
            "fixed_q14_q30"
            if row["cell_id"].endswith("_fixed")
            else "float32"
        )
        method = next(
            method
            for method in ("efork3", "m2sfrk", "gl")
            if f"_{method}_" in row["cell_id"]
        )
        multiplier = contract["multipliers"][board][method][representation]
        timing_path = ROOT / row["timing_cycles_csv"]
        with timing_path.open(encoding="utf-8", newline="") as stream:
            cycles = sum(
                int(timing["cycles"]) for timing in csv.DictReader(stream)
            )
        clock_hz = int(manifest["boards"][board]["system_clock_hz"])
        observed_windows.append(cycles * multiplier / clock_hz)

    assert min(observed_windows) >= minimum_s

