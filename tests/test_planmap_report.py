"""Tests for issue #614: per-definition plane map, run --report, and assess agreement.

Receipt: assess and run --report agree on the same script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import assess

needs_clang = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

MIXED_SCRIPT = """import json

def scale(x: int) -> int:
    return x * 42

def loader(text):
    return json.loads(text)

def main() -> None:
    rows: list[dict[str, int]] = loader('[{"a": 1}]')
    print(rows[0]["a"])

if __name__ == "__main__":
    main()
"""


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )


@needs_clang
def test_summary_json_carries_fallback_split(tmp_path: Path) -> None:
    """`compile --debug-dir` writes `fallback` in summary.json (plan Decision D)."""
    src = tmp_path / "mixed.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")
    dbg = tmp_path / "dbg"
    out = tmp_path / "bin"

    proc = _run_cli(
        "compile",
        str(src),
        "--fallback=cpython",
        "--emit",
        "bin",
        "-o",
        str(out),
        "--debug-dir",
        str(dbg),
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    summary_path = dbg / "summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "fallback" in summary
    fb = summary["fallback"]
    assert fb["moduleTopLevel"] == "native"
    assert fb["functions"]["loader"] == "island"
    assert fb["functions"]["scale"] == "native"
    assert fb["functions"]["main"] == "island"


@needs_clang
def test_run_report_silent_by_default(tmp_path: Path) -> None:
    """Without `--report`, stderr bytes are unchanged (silent by default on cached run)."""
    src = tmp_path / "silent.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")

    # Run once to populate cache
    proc_first = _run_cli("run", str(src), "--fallback=cpython", cwd=tmp_path)
    assert proc_first.returncode == 0, proc_first.stderr
    assert proc_first.stdout == "1\n"
    assert "planeMap" not in proc_first.stderr

    # Warm run (cached binary): silent by default
    proc_warm = _run_cli("run", str(src), "--fallback=cpython", cwd=tmp_path)
    assert proc_warm.returncode == 0, proc_warm.stderr
    assert proc_warm.stdout == "1\n"
    assert proc_warm.stderr == ""


@needs_clang
def test_run_report_file_and_stderr(tmp_path: Path) -> None:
    """`--report[=FILE.json]` prints to stderr or writes to FILE.json."""
    src = tmp_path / "rep.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")

    # 1. Write to file
    out_json = tmp_path / "out_report.json"
    proc_file = _run_cli("run", str(src), "--fallback=cpython", f"--report={out_json}", cwd=tmp_path)
    assert proc_file.returncode == 0, proc_file.stderr
    assert proc_file.stdout == "1\n"
    assert "planeMap" not in proc_file.stderr
    assert out_json.is_file()
    file_data = json.loads(out_json.read_text(encoding="utf-8"))
    assert file_data["planeMap"]["functions"]["loader"] == "island"
    assert file_data["planeMap"]["functions"]["scale"] == "native"
    assert file_data["planeMap"]["functions"]["main"] == "island"
    assert file_data["requiresLibpython"] is True

    # 2. Print to stderr
    proc_stderr = _run_cli("run", str(src), "--fallback=cpython", "--report", cwd=tmp_path)
    assert proc_stderr.returncode == 0, proc_stderr.stderr
    assert proc_stderr.stdout == "1\n"
    assert "planeMap" in proc_stderr.stderr
    stderr_lines = [line for line in proc_stderr.stderr.splitlines() if not line.startswith("/")]
    stderr_data = json.loads("\n".join(stderr_lines))
    assert stderr_data["planeMap"] == file_data["planeMap"]


@needs_clang
def test_receipt_assess_and_run_report_agree(tmp_path: Path) -> None:
    """Receipt: assess and run --report agree on the same script."""
    src = tmp_path / "script.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")

    # Assess
    assess_report = assess.assess(src)
    assert "planeMap" in assess_report
    assert assess_report["planeMap"]["functions"]["loader"] == "island"
    assert assess_report["planeMap"]["functions"]["scale"] == "native"
    assert assess_report["planeMap"]["functions"]["main"] == "island"
    assert assess_report["planeMap"]["moduleTopLevel"] == "native"

    # Policy lines predict the run
    policy_lines = assess_report["islandRouting"]["policyLines"]
    assert "[tool.pyscriptc.fallback]" in policy_lines
    assert "definitions = true" in policy_lines

    # Run with --report
    rep_file = tmp_path / "run_report.json"
    proc = _run_cli("run", str(src), "--fallback=cpython", f"--report={rep_file}", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    run_report = json.loads(rep_file.read_text(encoding="utf-8"))

    # Agreement
    assert assess_report["planeMap"] == run_report["planeMap"]
    assert assess_report["summary"]["requiresLibpython"] == run_report["requiresLibpython"]


@needs_clang
def test_router_honors_policy_line(tmp_path: Path) -> None:
    """The router honors [tool.pyscriptc.fallback] definitions = true without CLI flags."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
name = "demo"
version = "0.1.0"

[tool.pyscriptc.fallback]
definitions = true
""",
        encoding="utf-8",
    )
    src = tmp_path / "app.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")

    # Run WITHOUT --fallback=cpython CLI flag
    proc = _run_cli("run", str(src), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "1\n"


@needs_clang
def test_run_report_arg_order_issue622(tmp_path: Path) -> None:
    """`run --report` does not swallow source positional (issue #622)."""
    src = tmp_path / "order.py"
    src.write_text(MIXED_SCRIPT, encoding="utf-8")

    # 1. Natural CLI order: --fallback=cpython --report script.py
    proc_bare = _run_cli("run", "--fallback=cpython", "--report", str(src), cwd=tmp_path)
    assert proc_bare.returncode == 0, proc_bare.stderr
    assert proc_bare.stdout == "1\n"
    assert "planeMap" in proc_bare.stderr

    # 2. Argument order with report file: --fallback=cpython --report rep.json script.py
    rep_file = tmp_path / "custom_rep.json"
    proc_file = _run_cli("run", "--fallback=cpython", "--report", str(rep_file), str(src), cwd=tmp_path)
    assert proc_file.returncode == 0, proc_file.stderr
    assert proc_file.stdout == "1\n"
    assert rep_file.is_file()
    rep_data = json.loads(rep_file.read_text(encoding="utf-8"))
    assert "planeMap" in rep_data

    # 3. Bare --report script.py without fallback flag
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
name = "demo"
version = "0.1.0"

[tool.pyscriptc.fallback]
definitions = true
""",
        encoding="utf-8",
    )
    proc_bare2 = _run_cli("run", "--report", str(src), cwd=tmp_path)
    assert proc_bare2.returncode == 0, proc_bare2.stderr
    assert proc_bare2.stdout == "1\n"
    assert "planeMap" in proc_bare2.stderr
