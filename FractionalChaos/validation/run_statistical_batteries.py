#!/usr/bin/env python3
"""Audit and run statistical tools over accepted physical LSB streams.

The output deliberately separates three different claims:

* basic diagnostics, which are descriptive and never a battery pass/fail;
* eligibility, including tool-specific length and replication limitations;
* external battery output, preserved with commands, hashes, and raw logs.

The runner never repeats or cycles a finite input stream.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALPHA_DEFAULT = 0.01
NIST_SUCCESS_RETURN_CODE = 1
NIST_TEST_DIRECTORIES = (
    "Frequency",
    "BlockFrequency",
    "CumulativeSums",
    "Runs",
    "LongestRun",
    "Rank",
    "FFT",
    "NonOverlappingTemplate",
    "OverlappingTemplate",
    "Universal",
    "ApproximateEntropy",
    "RandomExcursions",
    "RandomExcursionsVariant",
    "Serial",
    "LinearComplexity",
)
FLOAT_TOKEN = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$"
)
PRACTRAND_RESULT = re.compile(
    r"^\s{2}(?P<name>.+?)\s{2,}"
    r"R=\s*(?P<raw>.*?)\s{2,}"
    r"p\s*(?P<relation>~?=)\s*(?P<pvalue>.*?)\s{2,}"
    r"(?P<evaluation>\S.*?)\s*$"
)
NIST_FINAL_ROW = re.compile(
    r"^\s*(?:\d+\s+){10}"
    r"(?:----|\d+\.\d+)\s+\*?\s*"
    r"(?P<passed>\d+)/(?P<total>\d+)\s+\*?\s*"
    r"(?P<test>[A-Za-z]+)\s*$"
)
TESTU01_REPORTED_BITS = re.compile(r"Number of bits:\s+(\d+)")
TESTU01_REPORTED_STATISTICS = re.compile(
    r"Number of statistics:\s+(\d+)"
)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repository_root(script_path: Path | None = None) -> Path:
    source = script_path or Path(__file__)
    return source.resolve().parents[1]


def resolve_repo_path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"path escapes repository: {value}") from error
    return candidate


def python_package_version(names: tuple[str, ...]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def discover_on_path(names: tuple[str, ...]) -> dict[str, str | None]:
    return {name: shutil.which(name) for name in names}


def baseline_tool_audit() -> dict[str, Any]:
    """Report tools installed on PATH before explicit local paths are applied."""

    nist_names = ("assess", "assess.exe", "nist_sts", "sts")
    practrand_names = ("RNG_test", "RNG_test.exe")
    testu01_names = (
        "testu01",
        "testu01.exe",
        "bbattery",
        "bbattery.exe",
        "RNG_to_TestU01",
        "RNG_to_TestU01.exe",
    )
    nist = discover_on_path(nist_names)
    practrand = discover_on_path(practrand_names)
    testu01 = discover_on_path(testu01_names)
    packages = python_package_version(
        (
            "nistrng",
            "nist-sts",
            "sp800-22-tests",
            "practrand",
            "testu01",
        )
    )
    return {
        "scope": (
            "PATH executables and selected Python distributions; explicit "
            "locally compiled paths are reported separately"
        ),
        "audit_method": {
            "executables": "Python shutil.which over the names listed below",
            "python_distributions": "importlib.metadata.version",
        },
        "nist_sp800_22": {
            "installed": any(nist.values()),
            "executables_checked": nist,
        },
        "practrand": {
            "installed": any(practrand.values()),
            "executables_checked": practrand,
        },
        "testu01": {
            "installed": any(testu01.values()),
            "executables_checked": testu01,
            "note": "TestU01 is normally a library, not a generic file CLI.",
        },
        "python_distributions_checked": packages,
    }


def executable_record(
    path: Path | None,
    *,
    version_command: list[str] | None = None,
) -> dict[str, Any]:
    if path is None:
        return {"selected": False, "available": False}
    resolved = path.resolve()
    record: dict[str, Any] = {
        "selected": True,
        "available": resolved.is_file(),
        "path": str(resolved),
    }
    if not resolved.is_file():
        return record
    record["sha256"] = sha256_file(resolved)
    record["bytes"] = resolved.stat().st_size
    if version_command is not None:
        completed = subprocess.run(
            [str(resolved), *version_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        record["version_probe"] = {
            "command": [str(resolved), *version_command],
            "return_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    return record


def load_and_verify_stream(
    root: Path,
    entry: dict[str, Any],
) -> dict[str, Any]:
    stream_id = str(entry["id"])
    if not SAFE_ID.fullmatch(stream_id):
        raise ValueError(f"unsafe stream id: {stream_id}")

    analysis_path = resolve_repo_path(root, str(entry["analysis"]))
    bitstream_path = resolve_repo_path(root, str(entry["bitstream"]))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    size = bitstream_path.stat().st_size
    bit_count = size * 8
    digest = sha256_file(bitstream_path)
    expected_bits = int(entry["expected_bits"])
    expected_digest = str(entry["expected_sha256"]).lower()

    checks = {
        "manifest_bit_count": bit_count == expected_bits,
        "manifest_sha256": digest == expected_digest,
        "analysis_bit_count": (
            int(analysis["outputs"]["bit_count"]) == bit_count
        ),
        "analysis_sha256": (
            str(analysis["outputs"]["bitstream_sha256"]).lower() == digest
        ),
        "transport_complete": bool(
            analysis["transport"]["transport_complete"]
        ),
        "all_solver_status_clear": bool(
            analysis["solver_status"]["all_samples_ok"]
        ),
        "configured_screening_eligible": bool(
            analysis["statistical_eligibility"][
                "eligible_for_configured_screening"
            ]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(
            f"{stream_id}: input verification failed: {', '.join(failed)}"
        )
    return {
        "id": stream_id,
        "board": entry["board"],
        "representation": entry["representation"],
        "analysis_path": str(analysis_path),
        "bitstream_path": str(bitstream_path),
        "bytes": size,
        "bits": bit_count,
        "sha256": digest,
        "verification_checks": checks,
        "analysis": analysis,
    }


def basic_diagnostics(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    bit_count = len(payload) * 8
    byte_histogram = Counter(payload)
    ones = sum(value.bit_count() for value in payload)
    zeros = bit_count - ones

    bits = bytearray(bit_count)
    write_index = 0
    for value in payload:
        for shift in range(7, -1, -1):
            bits[write_index] = (value >> shift) & 1
            write_index += 1

    transitions = 0
    longest = [0, 0]
    current_bit = bits[0] if bits else 0
    current_run = 0
    for bit in bits:
        if bit == current_bit:
            current_run += 1
        else:
            longest[current_bit] = max(longest[current_bit], current_run)
            transitions += 1
            current_bit = bit
            current_run = 1
    if bits:
        longest[current_bit] = max(longest[current_bit], current_run)

    entropy = 0.0
    if payload:
        for count in byte_histogram.values():
            probability = count / len(payload)
            entropy -= probability * math.log2(probability)

    most_common = [
        {"byte": value, "count": count, "fraction": count / len(payload)}
        for value, count in byte_histogram.most_common(8)
    ]
    ones_fraction = ones / bit_count if bit_count else None
    return {
        "classification": "descriptive_diagnostic_not_a_battery",
        "is_statistical_battery_result": False,
        "bytes": len(payload),
        "bits": bit_count,
        "zeros": zeros,
        "ones": ones,
        "ones_fraction": ones_fraction,
        "absolute_balance_error": (
            abs(ones_fraction - 0.5) if ones_fraction is not None else None
        ),
        "bit_runs": transitions + 1 if bits else 0,
        "bit_transition_fraction": (
            transitions / (bit_count - 1) if bit_count > 1 else None
        ),
        "longest_zero_run": longest[0],
        "longest_one_run": longest[1],
        "distinct_byte_values": len(byte_histogram),
        "byte_entropy_bits_per_byte": entropy,
        "most_common_bytes": most_common,
        "no_pass_fail_conclusion": True,
    }


def parse_nist_final_report(path: Path) -> dict[str, list[dict[str, int]]]:
    rows: dict[str, list[dict[str, int]]] = {
        name: [] for name in NIST_TEST_DIRECTORIES
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = NIST_FINAL_ROW.fullmatch(line)
        if match is None:
            continue
        name = match.group("test")
        if name not in rows:
            raise ValueError(f"unknown NIST final-report test name: {name}")
        rows[name].append(
            {
                "passed_sequences": int(match.group("passed")),
                "total_sequences": int(match.group("total")),
            }
        )
    return rows


def parse_nist_results(
    algorithm_dir: Path,
    final_report_path: Path,
    alpha: float,
) -> dict[str, Any]:
    by_test: dict[str, list[float]] = {}
    below_alpha: list[dict[str, Any]] = []
    excluded_placeholders: dict[str, list[float]] = {}
    detailed_sections: list[str] = []
    final_rows = parse_nist_final_report(final_report_path)

    for test_name in NIST_TEST_DIRECTORIES:
        test_dir = algorithm_dir / test_name
        results_path = test_dir / "results.txt"
        values: list[float] = []
        if results_path.is_file():
            raw_results = results_path.read_text(
                encoding="utf-8", errors="replace"
            )
            for line in raw_results.splitlines():
                candidate = line.strip()
                if FLOAT_TOKEN.fullmatch(candidate):
                    value = float(candidate)
                    if 0.0 <= value <= 1.0:
                        values.append(value)
            detailed_sections.extend(
                [
                    f"===== {test_name}/results.txt =====",
                    raw_results.rstrip(),
                    "",
                ]
            )
        stats_path = test_dir / "stats.txt"
        if stats_path.is_file():
            detailed_sections.extend(
                [
                    f"===== {test_name}/stats.txt =====",
                    stats_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).rstrip(),
                    "",
                ]
            )
        applicable_count = len(final_rows[test_name])
        if applicable_count > len(values):
            raise ValueError(
                f"NIST {test_name}: final report has {applicable_count} "
                f"rows but results.txt has {len(values)} p-value lines"
            )
        if applicable_count not in (0, len(values)):
            raise ValueError(
                f"NIST {test_name}: ambiguous result count "
                f"({len(values)} values, {applicable_count} final rows)"
            )
        applicable_values = values[:applicable_count]
        if applicable_count == 0 and values:
            excluded_placeholders[test_name] = values
        by_test[test_name] = applicable_values
        below_alpha.extend(
            {
                "test": test_name,
                "index_within_test": index,
                "p_value": value,
            }
            for index, value in enumerate(applicable_values)
            if value < alpha
        )

    failed_final_rows = [
        {
            "test": test_name,
            "index_within_test": index,
            **row,
        }
        for test_name, rows in final_rows.items()
        for index, row in enumerate(rows)
        if row["passed_sequences"] < row["total_sequences"]
    ]
    rounded_threshold_mismatches = []
    for test_name, values in by_test.items():
        for index, (value, row) in enumerate(
            zip(values, final_rows[test_name], strict=True)
        ):
            p_value_failed = value < alpha
            row_failed = (
                row["passed_sequences"] < row["total_sequences"]
            )
            if p_value_failed != row_failed and value != alpha:
                rounded_threshold_mismatches.append(
                    {
                        "test": test_name,
                        "index_within_test": index,
                        "p_value": value,
                        "final_report": row,
                    }
                )
    if rounded_threshold_mismatches:
        raise ValueError(
            "NIST results.txt and finalAnalysisReport.txt disagree away "
            "from the printed alpha boundary"
        )

    return {
        "p_values_by_test": by_test,
        "p_value_count": sum(len(values) for values in by_test.values()),
        "p_values_below_alpha": below_alpha,
        "p_values_below_alpha_count": len(below_alpha),
        "tests_with_p_values_below_alpha": sorted(
            {item["test"] for item in below_alpha}
        ),
        "final_report_rows_by_test": final_rows,
        "final_report_row_count": sum(
            len(rows) for rows in final_rows.values()
        ),
        "final_report_failed_rows": failed_final_rows,
        "final_report_failed_rows_count": len(failed_final_rows),
        "results_placeholders_excluded_as_inapplicable": (
            excluded_placeholders
        ),
        "inapplicable_tests": sorted(excluded_placeholders),
        "results_final_report_reconciled": True,
        "detailed_text": "\n".join(detailed_sections).rstrip() + "\n",
    }


def run_nist(
    *,
    assess: Path,
    nist_root: Path,
    stream: dict[str, Any],
    output_dir: Path,
    alpha: float,
) -> dict[str, Any]:
    bitstream = Path(stream["bitstream_path"])
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fc_nist_") as temporary:
        work = Path(temporary)
        local_exe = work / assess.name
        shutil.copy2(assess, local_exe)
        templates = nist_root / "templates"
        if not templates.is_dir():
            raise ValueError(
                f"NIST templates directory is missing: {templates}"
            )
        shutil.copytree(templates, work / "templates")
        algorithm_dir = work / "experiments" / "AlgorithmTesting"
        for name in NIST_TEST_DIRECTORIES:
            (algorithm_dir / name).mkdir(parents=True, exist_ok=True)

        local_input = work / "input.bin"
        shutil.copyfile(bitstream, local_input)
        padding_bytes = (-local_input.stat().st_size) % 4
        if padding_bytes:
            with local_input.open("ab") as destination:
                destination.write(b"\x00" * padding_bytes)

        answers = "\n".join(
            ("0", str(local_input), "1", "0", "1", "1")
        ) + "\n"
        command = [str(local_exe), str(stream["bits"])]
        completed = subprocess.run(
            command,
            input=answers,
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        elapsed = time.perf_counter() - started
        report_path = algorithm_dir / "finalAnalysisReport.txt"
        completed_marker = "Statistical Testing Complete" in completed.stdout
        successful = (
            completed.returncode == NIST_SUCCESS_RETURN_CODE
            and completed_marker
            and report_path.is_file()
        )
        if not successful:
            raise RuntimeError(
                "NIST STS did not complete: "
                f"return={completed.returncode}, "
                f"marker={completed_marker}, report={report_path.is_file()}"
            )

        parsed = parse_nist_results(algorithm_dir, report_path, alpha)
        stdout_path = output_dir / "nist_sp800_22_stdout.txt"
        stderr_path = output_dir / "nist_sp800_22_stderr.txt"
        final_report_path = (
            output_dir / "nist_sp800_22_final_analysis_report.txt"
        )
        detailed_path = output_dir / "nist_sp800_22_detailed_results.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        final_report_path.write_text(
            report_path.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
        detailed_path.write_text(parsed.pop("detailed_text"), encoding="utf-8")

    return {
        "tool": "NIST SP 800-22 STS",
        "version": "2.1.2",
        "execution": {
            "completed": True,
            "return_code": completed.returncode,
            "known_success_return_code": NIST_SUCCESS_RETURN_CODE,
            "elapsed_seconds": elapsed,
            "command": ["assess.exe", str(stream["bits"])],
            "stdin_protocol": [
                "0",
                "${EXACT_STREAM_WITH_READER_PADDING_IF_NEEDED}",
                "1",
                "0",
                "1",
                "1",
            ],
            "input_bits_requested": stream["bits"],
            "reader_padding_bytes": padding_bytes,
            "reader_padding_tested": False,
            "reader_padding_reason": (
                "official binary reader requires four-byte reads; tp.n stops "
                "conversion after the exact original bit count"
            ),
        },
        "eligibility": {
            "complete_input_consumed": True,
            "individual_test_execution_eligible": True,
            "uniformity_and_pass_proportion_eligible": False,
            "formal_battery_pass_statement_allowed": False,
            "reason": (
                "only one physical sequence exists for this configuration; "
                "the final-report uniformity/proportion columns cannot support "
                "a validation claim"
            ),
            "fft_minimum_recommended_bits": 1_000,
            "fft_recommended_length_met": stream["bits"] >= 1_000,
            "fft_note": (
                "SP 800-22 Rev. 1a section 2.6.7 recommends n >= 1,000 "
                "bits for the spectral test; all four streams meet it"
            ),
        },
        "result": {
            **parsed,
            "alpha": alpha,
            "classification": (
                "single_sequence_external_test_output_not_formal_validation"
            ),
            "battery_pass": None,
        },
        "artifacts": {
            path.name: {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in (
                stdout_path,
                stderr_path,
                final_report_path,
                detailed_path,
            )
        },
    }


def parse_practrand_output(stdout: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for line in stdout.splitlines():
        match = PRACTRAND_RESULT.match(line)
        if match is not None:
            results.append(
                {
                    "test": match.group("name").strip(),
                    "raw_score": match.group("raw").strip(),
                    "p_relation": match.group("relation"),
                    "p_value_text": match.group("pvalue").strip(),
                    "evaluation": match.group("evaluation").strip(),
                }
            )
    return results


def run_practrand(
    *,
    executable: Path,
    stream: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    bitstream = Path(stream["bitstream_path"])
    whole_blocks = stream["bytes"] // 1024
    omitted_tail = stream["bytes"] - whole_blocks * 1024
    if whole_blocks < 1:
        return {
            "tool": "PractRand",
            "version": "0.96",
            "execution": {"completed": False, "reason": "less_than_1KiB"},
            "eligibility": {
                "complete_input_consumed": False,
                "formal_battery_pass_statement_allowed": False,
            },
            "result": None,
        }

    command = [
        str(executable),
        "stdin8",
        "-tlmin",
        f"{whole_blocks}KB",
        "-tlmax",
        f"{whole_blocks}KB",
        "-tlmaxonly",
        "-a",
        "-singlethreaded",
    ]
    started = time.perf_counter()
    with bitstream.open("rb") as source:
        completed = subprocess.run(
            command,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"PractRand failed with return code {completed.returncode}"
        )

    stdout_path = output_dir / "practrand_stdout.txt"
    stderr_path = output_dir / "practrand_stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    parsed = parse_practrand_output(completed.stdout)
    evaluations = Counter(item["evaluation"] for item in parsed)
    prefix_bytes = whole_blocks * 1024
    complete = omitted_tail == 0
    return {
        "tool": "PractRand",
        "version": "0.96",
        "execution": {
            "completed": True,
            "return_code": completed.returncode,
            "elapsed_seconds": elapsed,
            "command": [
                "RNG_test.exe",
                *command[1:],
                "<",
                "${FULL_LSB_BINARY}",
            ],
            "input_interface": "stdin8",
            "test_set": "core",
            "folding": "standard_8_bit",
        },
        "eligibility": {
            "complete_input_consumed": complete,
            "formal_battery_pass_statement_allowed": False,
            "input_bytes": stream["bytes"],
            "tested_prefix_bytes": prefix_bytes,
            "tested_prefix_bits": prefix_bytes * 8,
            "omitted_tail_bytes": omitted_tail,
            "coverage_fraction": prefix_bytes / stream["bytes"],
            "reason": (
                "complete stream is an integer number of PractRand 1KiB blocks"
                if complete
                else (
                    "PractRand accepts whole 1KiB TestBlocks; the result covers "
                    "the largest exact prefix and the unmodified tail is not "
                    "silently padded or repeated"
                )
            ),
            "short_run_note": (
                "approximately 127-136 KiB is only a short diagnostic run, "
                "not evidence of long-stream randomness"
            ),
        },
        "result": {
            "classification": (
                "complete_stream_short_external_test_output"
                if complete
                else "short_prefix_external_test_output"
            ),
            "reported_result_count": len(parsed),
            "evaluation_counts": dict(sorted(evaluations.items())),
            "reported_results": parsed,
            "battery_pass": None,
        },
        "artifacts": {
            path.name: {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in (stdout_path, stderr_path)
        },
    }


def parse_testu01_output(stdout: str, battery: str) -> dict[str, Any]:
    title = battery.capitalize()
    marker = f"========= Summary results of {title} ========="
    if marker not in stdout:
        raise ValueError(f"TestU01 {title} summary marker is missing")
    summary = stdout.rsplit(marker, 1)[1].strip()
    bits_match = TESTU01_REPORTED_BITS.search(summary)
    statistics_match = TESTU01_REPORTED_STATISTICS.search(summary)
    if bits_match is None or statistics_match is None:
        raise ValueError(f"TestU01 {title} summary is incomplete")
    all_passed = "All tests were passed" in summary
    return {
        "battery": title,
        "reported_bits": int(bits_match.group(1)),
        "reported_statistics": int(statistics_match.group(1)),
        "tool_reported_all_tests_passed": all_passed,
        "tool_reported_conclusion": (
            "all_tests_passed"
            if all_passed
            else "at_least_one_result_flagged"
        ),
        "summary_text": marker + "\n\n" + summary + "\n",
    }


def run_testu01(
    *,
    executable: Path,
    stream: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    bitstream = Path(stream["bitstream_path"])
    runs: dict[str, Any] = {}
    total_elapsed = 0.0
    for battery in ("rabbit", "alphabit"):
        command = [
            str(executable),
            battery,
            str(bitstream),
            str(stream["bits"]),
        ]
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        elapsed = time.perf_counter() - started
        total_elapsed += elapsed
        if completed.returncode != 0:
            raise RuntimeError(
                f"TestU01 {battery} failed with return code "
                f"{completed.returncode}"
            )
        parsed = parse_testu01_output(completed.stdout, battery)
        if parsed["reported_bits"] > stream["bits"]:
            raise RuntimeError(
                f"TestU01 {battery} reported more bits than the finite input"
            )
        stdout_path = output_dir / f"testu01_{battery}_stdout.txt"
        stderr_path = output_dir / f"testu01_{battery}_stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        runs[battery] = {
            "execution": {
                "completed": True,
                "return_code": completed.returncode,
                "elapsed_seconds": elapsed,
                "command": [
                    "testu01_file_batteries.exe",
                    battery,
                    "${FULL_LSB_BINARY}",
                    str(stream["bits"]),
                ],
                "requested_bits": stream["bits"],
                "file_rewound_between_distinct_tests": True,
                "input_extended_or_recycled_to_reach_length": False,
            },
            "result": {
                **parsed,
                "omitted_tail_bits": (
                    stream["bits"] - parsed["reported_bits"]
                ),
                "classification": (
                    "external_file_battery_output_not_formal_validation"
                ),
                "battery_pass": None,
            },
            "artifacts": {
                path.name: {
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in (stdout_path, stderr_path)
            },
        }

    reported_bits = {
        name: value["result"]["reported_bits"]
        for name, value in runs.items()
    }
    complete = all(value == stream["bits"] for value in reported_bits.values())
    return {
        "tool": "TestU01",
        "version": "1.2.3",
        "execution": {
            "completed": True,
            "battery_invocations_completed": 2,
            "elapsed_seconds": total_elapsed,
            "finite_file_adapter": "testu01_file_batteries_v1",
        },
        "eligibility": {
            "rabbit_and_alphabit_executable": True,
            "complete_input_consumed": complete,
            "formal_battery_pass_statement_allowed": False,
            "requested_bits": stream["bits"],
            "reported_bits": reported_bits,
            "reason": (
                "both file batteries consumed the complete stream"
                if complete
                else (
                    "TestU01 file batteries consume whole 32-bit words; "
                    "the exact requested nb was passed, and the remaining "
                    "tail was neither padded nor repeated"
                )
            ),
            "same_file_reused_between_tests": True,
            "reuse_scope": (
                "each statistical test starts from the same finite sequence; "
                "this does not extend a test beyond the requested bit count"
            ),
        },
        "result": {
            "classification": (
                "external_file_battery_output_not_formal_validation"
            ),
            "batteries": runs,
            "battery_pass": None,
        },
    }


def battery_eligibility(
    stream: dict[str, Any],
    *,
    nist_selected: bool,
    practrand_selected: bool,
    testu01_selected: bool,
) -> dict[str, Any]:
    remainder = stream["bytes"] % 1024
    return {
        "configured_capture_screening": {
            "eligible": True,
            "minimum_bits": 1_000_000,
            "observed_bits": stream["bits"],
            "is_battery_result": False,
        },
        "nist_sp800_22": {
            "tool_selected": nist_selected,
            "single_sequence_tests_executable": nist_selected,
            "uniformity_and_pass_proportion_eligible": False,
            "formal_battery_validation_eligible": False,
            "reason": "one physical sequence per configuration",
        },
        "practrand": {
            "tool_selected": practrand_selected,
            "whole_1KiB_blocks": stream["bytes"] // 1024,
            "tail_bytes": remainder,
            "complete_stream_eligible": practrand_selected and remainder == 0,
            "prefix_diagnostic_eligible": (
                practrand_selected and stream["bytes"] >= 1024
            ),
        },
        "testu01": {
            "tool_selected": testu01_selected,
            "rabbit_alphabit_execution_eligible": testu01_selected,
            "whole_32_bit_words": stream["bits"] // 32,
            "tail_bits": stream["bits"] % 32,
            "complete_stream_eligible": (
                testu01_selected and stream["bits"] % 32 == 0
            ),
            "formal_battery_validation_eligible": False,
            "reason": (
                "an audited finite-file adapter is selected"
                if testu01_selected
                else "finite-file adapter unavailable"
            ),
        },
    }


def write_markdown_summary(report: dict[str, Any], path: Path) -> None:
    tools = report["selected_toolchain"]["tools"]
    lines = [
        "# Auditoría estadística del piloto físico M2sFRK",
        "",
        (
            "Este documento separa diagnósticos descriptivos, elegibilidad "
            "y salida de baterías externas. No declara que ninguna trama "
            "haya aprobado una batería formal."
        ),
        "",
        "## Herramientas",
        "",
        "| Herramienta | En PATH al inicio | Seleccionada localmente | Versión |",
        "|---|---:|---:|---|",
        (
            "| NIST SP 800-22 STS | no | "
            f"{'sí' if tools['nist_sp800_22']['available'] else 'no'} | "
            "2.1.2 |"
        ),
        (
            "| PractRand | no | "
            f"{'sí' if tools['practrand']['available'] else 'no'} | "
            "0.96 |"
        ),
        (
            "| TestU01 | no | "
            f"{'sí' if tools['testu01']['available'] else 'no'} | "
            "1.2.3 |"
        ),
        "",
        "## Resultados observados",
        "",
        (
            "| Trama | Bits completos | Fracción de unos | Entropía por byte "
            "| NIST p<0.01 | Familias NIST señaladas | NIST no aplicables "
            "| Prefijo PractRand "
            "| Cola no evaluada | Evaluaciones PractRand |"
        ),
        "|---|---:|---:|---:|---:|---|---|---:|---:|---|",
    ]
    for stream in report["streams"]:
        diagnostic = stream["diagnostics"]
        nist = stream["batteries"]["nist_sp800_22"]
        practrand = stream["batteries"]["practrand"]
        if nist["execution"]["completed"]:
            nist_count: str | int = nist["result"][
                "p_values_below_alpha_count"
            ]
            nist_tests = ", ".join(
                nist["result"]["tests_with_p_values_below_alpha"]
            )
            nist_inapplicable = ", ".join(
                nist["result"]["inapplicable_tests"]
            ) or "ninguna"
        else:
            nist_count = "no ejecutada"
            nist_tests = "—"
            nist_inapplicable = "—"
        if practrand["execution"]["completed"]:
            prefix = practrand["eligibility"]["tested_prefix_bytes"]
            tail = practrand["eligibility"]["omitted_tail_bytes"]
            evaluations = ", ".join(
                f"{name}: {count}"
                for name, count in practrand["result"][
                    "evaluation_counts"
                ].items()
            )
        else:
            prefix = "no ejecutada"
            tail = "—"
            evaluations = "—"
        lines.append(
            "| "
            + " | ".join(
                (
                    stream["id"],
                    str(stream["input"]["bits"]),
                    f"{diagnostic['ones_fraction']:.9f}",
                    f"{diagnostic['byte_entropy_bits_per_byte']:.9f}",
                    str(nist_count),
                    nist_tests,
                    nist_inapplicable,
                    str(prefix),
                    str(tail),
                    evaluations,
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## TestU01 Rabbit/Alphabit",
            "",
            (
                "| Trama | Rabbit | Bits Rabbit | Alphabit | Bits Alphabit "
                "| Cola omitida (bits) |"
            ),
            "|---|---|---:|---|---:|---:|",
        ]
    )
    for stream in report["streams"]:
        testu01 = stream["batteries"]["testu01"]
        if testu01["execution"]["completed"]:
            rabbit = testu01["result"]["batteries"]["rabbit"]["result"]
            alphabit = testu01["result"]["batteries"]["alphabit"]["result"]
            requested = stream["input"]["bits"]
            tail = max(
                rabbit["omitted_tail_bits"],
                alphabit["omitted_tail_bits"],
            )
            lines.append(
                "| "
                + " | ".join(
                    (
                        stream["id"],
                        rabbit["tool_reported_conclusion"],
                        f"{rabbit['reported_bits']}/{requested}",
                        alphabit["tool_reported_conclusion"],
                        f"{alphabit['reported_bits']}/{requested}",
                        str(tail),
                    )
                )
                + " |"
            )
        else:
            lines.append(
                f"| {stream['id']} | no ejecutada | — | no ejecutada | — | — |"
            )

    lines.extend(
        [
            "",
            "## Límites de interpretación",
            "",
            (
                "- NIST procesó exactamente todos los bits originales. Su "
                "lector binario exige lecturas de cuatro bytes; el relleno "
                "físico registrado nunca supera `tp.n` y no entra a las "
                "pruebas."
            ),
            (
                "- Solo hay una secuencia física por configuración. Por ello "
                "los valores individuales de NIST son auditables, pero las "
                "proporciones de aprobación y la uniformidad entre secuencias "
                "no sustentan una validación formal."
            ),
            (
                "- Para FFT, NIST SP 800-22 Rev. 1a, sección 2.6.7, recomienda "
                "`n >= 1000`; las cuatro tramas cumplen ese mínimo. Random "
                "Excursions y Random Excursions Variant quedaron no aplicables "
                "cuando no hubo ciclos suficientes; sus ceros de relleno en "
                "`results.txt` se excluyeron al reconciliar con "
                "`finalAnalysisReport.txt`."
            ),
            (
                "- PractRand opera con bloques enteros de 1 KiB. Se probó el "
                "mayor prefijo exacto de cada archivo; la cola indicada no se "
                "rellenó, repitió ni recicló."
            ),
            (
                "- Las corridas PractRand de 127–136 KiB son diagnósticos "
                "cortos, no evidencia de aleatoriedad a gran escala."
            ),
            (
                "- TestU01 no estaba en PATH. Se compiló localmente la versión "
                "oficial 1.2.3 y se ejecutaron `bbattery_RabbitFile` y "
                "`bbattery_AlphabitFile` mediante el adaptador finito auditado."
            ),
            (
                "- El adaptador pasó el número exacto de bits solicitado. "
                "TestU01 consumió únicamente palabras completas de 32 bits; "
                "las colas de 0 a 24 bits indicadas no se rellenaron, "
                "repitieron ni reciclaron."
            ),
            (
                "- Rabbit y Alphabit vuelven a abrir la misma secuencia para "
                "pruebas distintas, según la interfaz oficial. Esto no alarga "
                "ninguna prueba ni convierte la conclusión emitida por la "
                "herramienta en validación formal."
            ),
            "",
            (
                "Los hashes, comandos, p-valores individuales y logs crudos "
                "están en `summary.json` y en los subdirectorios por trama."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = repository_root()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    alpha = float(manifest.get("alpha", ALPHA_DEFAULT))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    assess = args.nist_assess.resolve() if args.nist_assess else None
    nist_root = (
        args.nist_root.resolve()
        if args.nist_root
        else assess.parent
        if assess is not None
        else None
    )
    practrand = args.practrand.resolve() if args.practrand else None
    testu01 = args.testu01.resolve() if args.testu01 else None

    selected_tools = {
        "nist_sp800_22": executable_record(assess),
        "practrand": executable_record(
            practrand, version_command=["--version"]
        ),
        "testu01": executable_record(testu01),
    }
    nist_available = bool(
        selected_tools["nist_sp800_22"]["available"]
        and nist_root is not None
        and (nist_root / "templates").is_dir()
    )
    practrand_available = bool(selected_tools["practrand"]["available"])
    testu01_available = bool(selected_tools["testu01"]["available"])

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "scope": manifest["scope"],
        "claim_boundary": {
            "basic_diagnostics_are_battery_results": False,
            "eligibility_is_a_battery_result": False,
            "single_sequence_nist_is_formal_validation": False,
            "short_practrand_prefix_is_formal_validation": False,
            "testu01_result_available": False,
            "testu01_result_is_formal_validation": False,
        },
        "runner": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__).resolve()),
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "python": os.sys.version,
        },
        "baseline_environment_audit": baseline_tool_audit(),
        "selected_toolchain": {
            "compiler_used_for_local_builds": (
                "MSYS2 UCRT64 GCC/G++ 15.2.0"
            ),
            "tools": selected_tools,
            "source_and_build_provenance": manifest["tool_provenance"],
        },
        "streams": [],
    }

    for entry in manifest["streams"]:
        stream = load_and_verify_stream(root, entry)
        stream_dir = output_dir / stream["id"]
        stream_dir.mkdir(parents=True, exist_ok=True)
        eligibility = battery_eligibility(
            stream,
            nist_selected=nist_available and not args.basic_only,
            practrand_selected=practrand_available and not args.basic_only,
            testu01_selected=testu01_available and not args.basic_only,
        )
        batteries: dict[str, Any] = {}
        if nist_available and not args.basic_only:
            assert assess is not None and nist_root is not None
            batteries["nist_sp800_22"] = run_nist(
                assess=assess,
                nist_root=nist_root,
                stream=stream,
                output_dir=stream_dir,
                alpha=alpha,
            )
        else:
            batteries["nist_sp800_22"] = {
                "execution": {
                    "completed": False,
                    "reason": (
                        "basic_only"
                        if args.basic_only
                        else "no runnable assess executable plus templates"
                    ),
                },
                "result": None,
            }
        if practrand_available and not args.basic_only:
            assert practrand is not None
            batteries["practrand"] = run_practrand(
                executable=practrand,
                stream=stream,
                output_dir=stream_dir,
            )
        else:
            batteries["practrand"] = {
                "execution": {
                    "completed": False,
                    "reason": (
                        "basic_only"
                        if args.basic_only
                        else "RNG_test executable unavailable"
                    ),
                },
                "result": None,
            }
        if testu01_available and not args.basic_only:
            assert testu01 is not None
            batteries["testu01"] = run_testu01(
                executable=testu01,
                stream=stream,
                output_dir=stream_dir,
            )
        else:
            batteries["testu01"] = {
                "execution": {
                    "completed": False,
                    "tool_available": testu01_available,
                    "reason": (
                        "basic_only"
                        if args.basic_only
                        else "audited finite-file adapter unavailable"
                    ),
                },
                "eligibility": eligibility["testu01"],
                "result": None,
            }

        stream_report = {
            "id": stream["id"],
            "board": stream["board"],
            "representation": stream["representation"],
            "input": {
                "path": stream["bitstream_path"],
                "bytes": stream["bytes"],
                "bits": stream["bits"],
                "sha256": stream["sha256"],
                "verification_checks": stream["verification_checks"],
            },
            "diagnostics": basic_diagnostics(
                Path(stream["bitstream_path"])
            ),
            "eligibility": eligibility,
            "batteries": batteries,
        }
        json_write(stream_dir / "result.json", stream_report)
        report["streams"].append(stream_report)

    testu01_completed = sum(
        bool(item["batteries"]["testu01"]["execution"]["completed"])
        for item in report["streams"]
    )
    report["claim_boundary"]["testu01_result_available"] = bool(
        report["streams"] and testu01_completed == len(report["streams"])
    )
    report["execution_summary"] = {
        "stream_count": len(report["streams"]),
        "nist_completed": sum(
            bool(
                item["batteries"]["nist_sp800_22"]["execution"][
                    "completed"
                ]
            )
            for item in report["streams"]
        ),
        "practrand_completed": sum(
            bool(item["batteries"]["practrand"]["execution"]["completed"])
            for item in report["streams"]
        ),
        "testu01_completed": testu01_completed,
        "testu01_battery_invocations_completed": sum(
            int(
                item["batteries"]["testu01"]["execution"].get(
                    "battery_invocations_completed", 0
                )
            )
            for item in report["streams"]
        ),
        "formal_battery_pass_claims": 0,
    }
    markdown_path = output_dir / "summary.md"
    write_markdown_summary(report, markdown_path)
    report["summary_artifacts"] = {
        markdown_path.name: {
            "sha256": sha256_file(markdown_path),
            "bytes": markdown_path.stat().st_size,
        }
    }
    json_write(output_dir / "summary.json", report)
    return report


def main() -> int:
    root = repository_root()
    parser = argparse.ArgumentParser(
        description=(
            "Audit and execute available statistical tools over the accepted "
            "physical LSB streams without recycling finite inputs."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "validation" / "statistical_battery_manifest.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            root
            / "validation"
            / "results"
            / "statistical_batteries_m2sfrk_pilot"
        ),
    )
    parser.add_argument("--nist-assess", type=Path)
    parser.add_argument("--nist-root", type=Path)
    parser.add_argument("--practrand", type=Path)
    parser.add_argument("--testu01", type=Path)
    parser.add_argument(
        "--basic-only",
        action="store_true",
        help="verify streams and write descriptive diagnostics only",
    )
    args = parser.parse_args()
    report = run(args)
    print(args.output_dir.resolve() / "summary.json")
    print(json.dumps(report["execution_summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
