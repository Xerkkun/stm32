#!/usr/bin/env python3
"""Puerta S0: integridad de la cohorte Chen--Liu--Hammouch--Mekkaoui."""

from __future__ import annotations

import hashlib
import itertools
import json
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELECTED_PATH = ROOT / "validation" / "selected_system_manifests_v1.json"
CAMPAIGN_PATH = ROOT / "validation" / "physical_campaign_selected_v1.json"
HISTORICAL_CAMPAIGN_PATH = (
    ROOT / "validation" / "physical_campaign_manifest.json"
)
HISTORICAL_CAMPAIGN_SHA256 = (
    "2aef3cac443e6b7f77dd24f0b7d952fc643562239aec77454b5b5b00fbbb100d"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def manifest_in(payload: dict[str, Any], manifest_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in payload["manifests"]
        if item["manifest_id"] == manifest_id
    ]
    assert len(matches) == 1
    return matches[0]


def float32_word(value: float) -> str:
    word = struct.unpack("<I", struct.pack("<f", value))[0]
    return f"0x{word:08X}"


def test_s0_selected_contracts_and_provenance_are_frozen() -> None:
    selected = load_json(SELECTED_PATH)
    assert selected["schema"] == (
        "fractional-chaos-selected-system-manifests-v1"
    )
    assert selected["cohort_id"] == "chen_liu_hammouch_mekkaoui_v1"
    assert selected["operator_contract"] == {
        "operator": "caputo",
        "commensurate_order": True,
        "lower_terminal": 0.0,
        "prehistory": "x(t<0)=x0",
        "embedded_memory_policy": "finite_window",
        "embedded_memory_seconds": 10.0,
    }

    decision = selected["selection_decision"]
    assert sha256_file(ROOT / decision["path"]) == decision["sha256"]

    systems = selected["systems"]
    assert [
        (item["contract"]["system"], item["wire_id"])
        for item in systems
    ] == [
        ("chen", 2),
        ("liu", 3),
        ("hammouch_mekkaoui", 4),
    ]

    comparable_keys = {
        "manifest_id",
        "candidate_id",
        "system",
        "parameters",
        "initial_state",
        "q",
        "h",
        "memory_seconds",
        "memory_increments",
    }
    for selected_system in systems:
        contract = selected_system["contract"]
        provenance = selected_system["provenance"]
        assert (
            canonical_sha256(contract)
            == selected_system["contract_canonical_sha256"]
        )

        source_path = ROOT / provenance["source_manifest_path"]
        assert (
            sha256_file(source_path)
            == provenance["source_manifest_sha256"]
        )
        source_manifest = manifest_in(
            load_json(source_path), contract["manifest_id"]
        )
        for key in comparable_keys & contract.keys():
            assert source_manifest[key] == contract[key]

        qualification_path = ROOT / provenance["qualification_path"]
        assert (
            sha256_file(qualification_path)
            == provenance["qualification_sha256"]
        )
        qualified = manifest_in(
            load_json(qualification_path), contract["manifest_id"]
        )
        assert qualified["decision"] == provenance["qualification_decision"]
        assert qualified["decision"] == (
            "qualified_observed_long_horizon_screen"
        )
        for key in comparable_keys & contract.keys():
            assert qualified["manifest"][key] == contract[key]
        observed_hashes = {
            str(resolution["h"]): resolution[
                "trajectory_float64_le_sha256"
            ]
            for resolution in qualified["resolutions"]
        }
        assert observed_hashes == provenance[
            "trajectory_float64_le_sha256_by_step"
        ]
        if "minimum_normalized_continuous_margin" in provenance:
            assert qualified["selection"][
                "minimum_normalized_continuous_margin"
            ] == provenance["minimum_normalized_continuous_margin"]
            assert qualified["selection"][
                "selected_for_formal_followup"
            ] is True


def test_s0_selected_campaign_is_exactly_36_cells_and_30_blocks() -> None:
    assert (
        sha256_file(HISTORICAL_CAMPAIGN_PATH)
        == HISTORICAL_CAMPAIGN_SHA256
    )
    selected = load_json(SELECTED_PATH)
    campaign = load_json(CAMPAIGN_PATH)

    selected_reference = campaign["selected_system_manifests"]
    assert selected_reference["path"] == (
        "validation/selected_system_manifests_v1.json"
    )
    assert selected_reference["sha256"] == sha256_file(SELECTED_PATH)

    selected_by_name = {
        item["contract"]["system"]: item for item in selected["systems"]
    }
    assert campaign["paper_matrix"]["systems"] == [
        "chen",
        "liu",
        "hammouch_mekkaoui",
    ]
    assert set(campaign["systems"]) == set(selected_by_name)
    for name, system in campaign["systems"].items():
        selected_system = selected_by_name[name]
        contract = selected_system["contract"]
        assert system["wire_id"] == selected_system["wire_id"]
        assert system["manifest_id"] == contract["manifest_id"]
        assert system["contract_canonical_sha256"] == (
            selected_system["contract_canonical_sha256"]
        )
        assert system["q"] == contract["q"]
        assert system["dt_s"] == contract["h"]
        assert system["memory_s"] == contract["memory_seconds"]
        assert system["memory_increments"] == contract["memory_increments"]
        assert system["history_states"] == contract["memory_increments"] + 1
        assert system["initial_state_words_hex"] == [
            float32_word(value) for value in contract["initial_state"]
        ]
        assert system["transient_steps"] == round(10.0 / contract["h"])
        assert system["observation_steps"] == round(40.0 / contract["h"])

    matrix = campaign["paper_matrix"]
    cells = list(
        itertools.product(
            matrix["systems"],
            matrix["methods"],
            matrix["representations"],
            matrix["boards"],
        )
    )
    assert len(cells) == 36
    assert len(set(cells)) == 36
    assert campaign["schedule"]["cold_starts_per_cell"] == 30
    assert len(cells) * campaign["schedule"]["cold_starts_per_cell"] == 1080

    historical = load_json(HISTORICAL_CAMPAIGN_PATH)
    for board in ("f746", "h755"):
        for field in ("wire_id", "probe_serial", "port", "baud"):
            assert campaign["boards"][board][field] == (
                historical["boards"][board][field]
            )

    assert campaign["endpoints"]["primary_benchmark"]["ready"] is False
    assert campaign["reset_contract"]["power_removed"] is False
    assert (
        campaign["reset_contract"]["eligible_as_paper_cold_start"] is False
    )
    assert campaign["calibrated_cells"] == {}
    assert campaign["safety"]["uncalibrated_cells_may_execute"] is False
