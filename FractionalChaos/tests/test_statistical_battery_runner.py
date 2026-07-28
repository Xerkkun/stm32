from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "run_statistical_batteries.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_statistical_batteries",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_basic_diagnostics_is_descriptive_and_exact(tmp_path: Path) -> None:
    stream = tmp_path / "pattern.bin"
    stream.write_bytes(b"\x00\xff")

    result = MODULE.basic_diagnostics(stream)

    assert result["classification"] == (
        "descriptive_diagnostic_not_a_battery"
    )
    assert result["is_statistical_battery_result"] is False
    assert result["bits"] == 16
    assert result["zeros"] == 8
    assert result["ones"] == 8
    assert result["ones_fraction"] == 0.5
    assert result["bit_runs"] == 2
    assert result["longest_zero_run"] == 8
    assert result["longest_one_run"] == 8
    assert result["byte_entropy_bits_per_byte"] == 1.0
    assert result["no_pass_fail_conclusion"] is True


def test_parse_practrand_preserves_tool_evaluation() -> None:
    stdout = """
  BCFN(2+0,13-9U)                   R=  -1.0  p = 0.621     normal
  FPF/16:all                        R=  +4.6  p =  2.5e-3   normalish
  [Low1/8]Gap-16:C                  R=  +0.2  p~= 0.575     normal
"""
    results = MODULE.parse_practrand_output(stdout)

    assert [item["test"] for item in results] == [
        "BCFN(2+0,13-9U)",
        "FPF/16:all",
        "[Low1/8]Gap-16:C",
    ]
    assert results[1]["p_value_text"] == "2.5e-3"
    assert results[1]["evaluation"] == "normalish"
    assert results[2]["p_relation"] == "~="


def test_parse_nist_results_counts_p_values_below_alpha(
    tmp_path: Path,
) -> None:
    algorithm = tmp_path / "AlgorithmTesting"
    for name in MODULE.NIST_TEST_DIRECTORIES:
        (algorithm / name).mkdir(parents=True)
    (algorithm / "Frequency" / "results.txt").write_text(
        "0.500000\n", encoding="utf-8"
    )
    (algorithm / "Serial" / "results.txt").write_text(
        "0.009000\n0.100000\n", encoding="utf-8"
    )
    (algorithm / "Serial" / "stats.txt").write_text(
        "diagnostic detail\n", encoding="utf-8"
    )
    (algorithm / "RandomExcursions" / "results.txt").write_text(
        "0.000000\n" * 8, encoding="utf-8"
    )
    final_report = algorithm / "finalAnalysisReport.txt"
    final_report.write_text(
        (
            "  0   0   0   0   0   1   0   0   0   0"
            "     ----       1/1       Frequency\n"
            "  1   0   0   0   0   0   0   0   0   0"
            "     ----       0/1 *     Serial\n"
            "  0   1   0   0   0   0   0   0   0   0"
            "     ----       1/1       Serial\n"
            "  0   0   0   0   0   0   0   0   0   0"
            "     ----     ------     RandomExcursions\n"
        ),
        encoding="utf-8",
    )

    result = MODULE.parse_nist_results(algorithm, final_report, 0.01)

    assert result["p_value_count"] == 3
    assert result["p_values_below_alpha_count"] == 1
    assert result["tests_with_p_values_below_alpha"] == ["Serial"]
    assert result["final_report_failed_rows_count"] == 1
    assert result["inapplicable_tests"] == ["RandomExcursions"]
    assert len(
        result["results_placeholders_excluded_as_inapplicable"][
            "RandomExcursions"
        ]
    ) == 8
    assert result["results_final_report_reconciled"] is True
    assert "Serial/stats.txt" in result["detailed_text"]


def test_parse_testu01_output_preserves_reported_prefix() -> None:
    stdout = """
========= Summary results of Rabbit =========

 Number of statistics: 40
 Total CPU time: 00:00:00.00
 Number of bits: 1062592
 All tests were passed
"""

    result = MODULE.parse_testu01_output(stdout, "rabbit")

    assert result["battery"] == "Rabbit"
    assert result["reported_bits"] == 1_062_592
    assert result["reported_statistics"] == 40
    assert result["tool_reported_all_tests_passed"] is True
    assert result["tool_reported_conclusion"] == "all_tests_passed"


def test_canonical_nist_results_exclude_not_applicable_placeholders() -> None:
    evidence_root = (
        Path(__file__).parents[1]
        / "validation"
        / "results"
        / "statistical_batteries_m2sfrk_pilot"
    )
    expected_failed_rows = {
        "f746_float32_dec512": 1,
        "f746_fixed_q14_q30_dec512": 5,
        "h755_float32_dec1024": 2,
        "h755_fixed_q14_q30_dec512": 1,
    }

    for stream_id, expected_failures in expected_failed_rows.items():
        result_path = evidence_root / stream_id / "result.json"
        assert result_path.is_file()
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        result = payload["batteries"]["nist_sp800_22"]["result"]
        assert result["p_value_count"] == result["final_report_row_count"]
        assert result["p_values_below_alpha_count"] == expected_failures
        assert result["final_report_failed_rows_count"] == expected_failures
        assert result["inapplicable_tests"] == [
            "RandomExcursions",
            "RandomExcursionsVariant",
        ]
        assert result["results_final_report_reconciled"] is True


def test_verify_stream_rejects_hash_mismatch(tmp_path: Path) -> None:
    stream = tmp_path / "stream.bin"
    stream.write_bytes(b"\xaa")
    analysis = tmp_path / "analysis.json"
    analysis.write_text(
        json.dumps(
            {
                "outputs": {
                    "bit_count": 8,
                    "bitstream_sha256": digest(b"\xaa"),
                },
                "transport": {"transport_complete": True},
                "solver_status": {"all_samples_ok": True},
                "statistical_eligibility": {
                    "eligible_for_configured_screening": True
                },
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "id": "test_stream",
        "board": "test",
        "representation": "test",
        "analysis": "analysis.json",
        "bitstream": "stream.bin",
        "expected_bits": 8,
        "expected_sha256": "0" * 64,
    }

    with pytest.raises(ValueError, match="manifest_sha256"):
        MODULE.load_and_verify_stream(tmp_path, entry)


def test_basic_only_report_keeps_claim_classes_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    validation = root / "validation"
    validation.mkdir(parents=True)
    stream = validation / "stream.bin"
    payload = b"\xaa" * 125_000
    stream.write_bytes(payload)
    analysis = validation / "analysis.json"
    analysis.write_text(
        json.dumps(
            {
                "outputs": {
                    "bit_count": len(payload) * 8,
                    "bitstream_sha256": digest(payload),
                },
                "transport": {"transport_complete": True},
                "solver_status": {"all_samples_ok": True},
                "statistical_eligibility": {
                    "eligible_for_configured_screening": True
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = validation / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "scope": "test",
                "alpha": 0.01,
                "tool_provenance": {},
                "streams": [
                    {
                        "id": "test_stream",
                        "board": "test",
                        "representation": "test",
                        "analysis": "validation/analysis.json",
                        "bitstream": "validation/stream.bin",
                        "expected_bits": len(payload) * 8,
                        "expected_sha256": digest(payload),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    monkeypatch.setattr(MODULE, "repository_root", lambda *_: root)
    args = MODULE.argparse.Namespace(
        manifest=manifest,
        output_dir=output,
        nist_assess=None,
        nist_root=None,
        practrand=None,
        testu01=None,
        basic_only=True,
    )

    report = MODULE.run(args)

    assert report["execution_summary"]["formal_battery_pass_claims"] == 0
    item = report["streams"][0]
    assert set(item) >= {"diagnostics", "eligibility", "batteries"}
    assert item["diagnostics"]["is_statistical_battery_result"] is False
    assert item["eligibility"]["configured_capture_screening"][
        "is_battery_result"
    ] is False
    assert item["batteries"]["nist_sp800_22"]["result"] is None
    assert item["batteries"]["practrand"]["result"] is None
    assert (output / "summary.md").is_file()
