#!/usr/bin/env python3
"""Comprueba que las seis tablas versionadas coincidan con el generador."""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))

import generate_tables  # noqa: E402


JOBS = (
    ("lorenz", "efork", "fc_lorenz_efork3.c"),
    ("lorenz", "gl", "fc_lorenz_gl.c"),
    ("rossler", "efork", "fc_rossler_efork3.c"),
    ("rossler", "gl", "fc_rossler_gl.c"),
    ("chen", "efork", "fc_chen_efork3.c"),
    ("chen", "gl", "fc_chen_gl.c"),
)


def main() -> int:
    failures: list[str] = []
    for system, method, filename in JOBS:
        capture = io.StringIO(newline="\n")
        with contextlib.redirect_stdout(capture):
            generate_tables.generate(
                generate_tables.MANIFESTS[system],
                method,
            )

        expected = capture.getvalue()
        path = ROOT / "generated" / filename
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(str(path))

    if failures:
        print("Las tablas no coinciden con el generador:", file=sys.stderr)
        for path in failures:
            print(f"  {path}", file=sys.stderr)
        return 1

    print("Se verifican las seis tablas float32 generadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
