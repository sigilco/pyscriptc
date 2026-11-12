"""The engine conformance corpus and the marshalling micro-benchmark (#670).

The corpus is a set of island programs that must behave identically on
every shipped engine. These tests keep the corpus honest: every program
compiles and matches CPython on the built-in engine, the runner's matrix
walks the engine descriptors, and the benchmark harness measures what it
claims to measure.

The corpus compiles and links island binaries, so these tests are slower
than the unit tests; the PyPy leg of the matrix is not exercised here (no
PyPy SDK in CI yet — #668 owns the first real PyPy run).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.engine_corpus import marshalling, run

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks" / "engine_corpus"

#: The programs the issue names, one per area of the island surface.
EXPECTED_PROGRAMS = {
    "bytes",
    "dataclasses",
    "exceptions",
    "fractions",
    "json",
    "lists",
    "re",
}


def test_the_corpus_covers_every_named_area() -> None:
    """The issue names fractions, json, re, dataclasses, exceptions, bytes and lists."""
    names = {d.name for d in run.program_dirs()}
    assert names == EXPECTED_PROGRAMS


@pytest.mark.parametrize("name", sorted(EXPECTED_PROGRAMS))
def test_every_corpus_program_compiles_and_matches_cpython(name: str, tmp_path: Path) -> None:
    """One program: compile with --island, run, byte-compare with CPython."""
    program = CORPUS / "programs" / name
    entry = f"{name}_entry.py"
    binary = tmp_path / f"{name}.bin"

    compile_proc = run.compile_program(program, entry, binary, None)
    assert compile_proc[0], compile_proc[1]

    got = run.run_binary(binary, program)
    want = run.reference_run(program, entry)
    assert got == want


def test_the_runner_walks_the_engine_descriptors() -> None:
    """The matrix is descriptor-driven: a new descriptor file is a new engine."""
    descriptors = run.engine_descriptors()
    names = {d.stem for d in descriptors}
    assert "cpython" in names


def test_a_descriptor_can_opt_out_of_the_matrix(tmp_path: Path) -> None:
    """`conformance = false` skips an engine that has no island seam yet."""
    descriptor = tmp_path / "stub.toml"
    descriptor.write_text(
        'schema_version = 1\nconformance = false\n\n[engine]\nname = "stub"\nversion = "0"\nkind = "interpreter"\n',
        encoding="utf-8",
    )
    assert run.engine_skipped(descriptor) is True


def test_the_cpythen_descriptor_is_not_skipped() -> None:
    """The built-in engine runs the matrix."""
    assert run.engine_skipped(CORPUS / "engines" / "cpython.toml") is False


def test_the_runner_reports_every_program(tmp_path: Path) -> None:
    """The JSON report carries one row per program, with the CPython reference."""
    report_path = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.engine_corpus.run", "--only", "fractions,json", f"--json={report_path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] == report["total"] == 2
    assert {row["program"] for row in report["programs"]} == {"fractions", "json"}
    assert all(row["ok"] for row in report["programs"])


def test_the_marshalling_harness_measures_the_seam(tmp_path: Path) -> None:
    """The benchmark harness compiles and reports a per-case cost."""
    entry = marshalling.build_harness(tmp_path, calls=10, repeats=1)
    assert entry.is_file()
    source = entry.read_text(encoding="utf-8")
    # Every measured case appears, and the no-payload baseline is there so
    # the marshalling share has something to subtract.
    for label, _ in marshalling.CASES:
        assert f'print("{label}"' in source
    assert "emod.sink()" in source


def test_the_marshalling_summary_has_a_row_per_case(tmp_path: Path) -> None:
    """The summary the CI job writes is one row per measured case."""
    from benchmarks.engine_corpus.summary import marshalling_summary

    rows = {name: {"ns_per_call": 1.0, "marshal_ns": 0.0} for name, _ in marshalling.CASES}
    report = tmp_path / "marshalling.json"
    report.write_text(json.dumps({"calls": 1, "repeats": 1, "cases": rows}), encoding="utf-8")
    text = marshalling_summary(report)
    for name, _ in marshalling.CASES:
        assert f"| {name} |" in text
    assert "baseline" in text


def test_the_conformance_summary_names_the_engines(tmp_path: Path) -> None:
    """The summary the CI job writes reports the engines and the pass count."""
    from benchmarks.engine_corpus.summary import conformance_summary

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "engines": ["cpython"],
                "skipped": [],
                "passed": 1,
                "total": 1,
                "programs": [{"engine": "cpython", "program": "fractions", "ok": True}],
            }
        ),
        encoding="utf-8",
    )
    text = conformance_summary(report)
    assert "cpython" in text
    assert "1/1" in text
    assert "| cpython | fractions | ok |" in text
