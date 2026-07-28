from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "validation"
    / "collect_resource_usage.py"
)
SPEC = importlib.util.spec_from_file_location(
    "collect_resource_usage",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_size_output_derives_flash_and_static_ram() -> None:
    output = """\
   text    data     bss     dec     hex filename
   9996       8    8688   18692    4904 image.elf
"""
    result = MODULE.parse_size_output(output)

    assert result == {
        "text": 9996,
        "data": 8,
        "bss": 8688,
        "dec": 18692,
        "flash_bytes": 10004,
        "static_ram_bytes": 8696,
    }


def test_parse_size_output_rejects_inconsistent_decimal_total() -> None:
    output = """\
   text    data     bss     dec     hex filename
   10         2       3      99      63 image.elf
"""
    with pytest.raises(MODULE.ResourceError, match="text\\+data\\+bss"):
        MODULE.parse_size_output(output)


def test_expected_matrix_has_36_unique_cells_and_targets() -> None:
    cells = list(MODULE.expected_cells())
    targets = {
        MODULE.target_name(**cell)
        for cell in cells
    }

    assert len(cells) == 36
    assert len(targets) == 36
    assert "f746_lorenz_m2sfrk" in targets
    assert "h755_m7_chen_gl_fixed" in targets
