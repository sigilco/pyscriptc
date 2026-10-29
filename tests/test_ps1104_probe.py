"""The PS1104 probe (benchmarks/ps1104_probe.py) measures, and stays honest.

The projection the selfnative report made (clearing PS1104 moves native
definitions 23% to 59%) is pinned here so the probe's classification
logic cannot drift silently: the fence-shape table, the blocked-only
counting, and the must-fence gate that keeps bare `Any` fenced all have
a receipt. Measurement only, no lowering changes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "benchmarks" / "ps1104_probe.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(PROBE), *args], cwd=ROOT, capture_output=True, text=True, timeout=900)


def test_ps1104_probe_classifies_the_shapes(tmp_path: Path) -> None:
    """The shape table splits dict[..., Any] from bare Any and object."""

    from benchmarks.ps1104_probe import classify

    assert classify("def f(d: dict[str, Any]) -> int: ...") == "dict[..., Any]"
    assert classify("def f(xs: list[Any]) -> int: ...") == "list[..., Any]"
    assert classify("def f(x: Any) -> int: ...") == "bare Any"
    assert classify("def f(x: object) -> int: ...") == "object"
    assert classify("def f(x: int) -> int: ...") == "other"


def test_ps1104_probe_on_a_small_file_counts_the_only_fences(tmp_path: Path) -> None:
    """A bare-`Any` definition still counts; a dict[str, Any] one no longer
    fences PS1104 at all (the slice accepted the annotation, issue #814)."""

    case = tmp_path / "case.py"
    case.write_text(
        "from typing import Any\n\n\ndef count(fence: Any) -> int:\n    return 1\n",
        encoding="utf-8",
    )
    got = _run(str(case), "--json")
    assert got.returncode == 0, got.stderr
    result = json.loads(got.stdout)
    assert result["ps1104Fences"] == 1
    assert result["byShape"] == {"bare Any": 1}
    assert result["definitionsBlockedOnlyByPS1104"] == 1
    # The definition moves into the projected pool: native + 1.
    assert result["projectedNativeIfCleared"] == result["native"] + 1

    container = tmp_path / "container.py"
    container.write_text(
        "from typing import Any\n\n\ndef count(fence: dict[str, Any]) -> int:\n    return len(fence)\n",
        encoding="utf-8",
    )
    got = _run(str(container), "--json")
    assert got.returncode == 0, got.stderr
    result = json.loads(got.stdout)
    # The annotation is accepted; the unresolved read is PS1107, not PS1104.
    assert result["ps1104Fences"] == 0


def test_ps1104_probe_a_definition_with_a_second_root_stays_out(tmp_path: Path) -> None:
    """A definition whose fences carry a second code stays out of the
    projected pool: clearing PS1104 alone would not lower it.

    The compiler tree's PS1104 population is the bare-`Any` and `object`
    remainder since the dict[K, Any]/list[Any] slice (issue #814): most
    definitions blocked only by PS1104 are bare `Any` sites. The probe's
    counting reads every fence index of a definition, not just its root
    cause, so the count is the definition-level one.
    """

    from pyscriptc.coverage import build_report

    report = build_report(ROOT / "src" / "pyscriptc")
    fences = report.get("fences", [])
    by_index = {i: f for i, f in enumerate(fences)}
    double = 0
    only = 0
    for definition in report.get("definitions", []):
        codes = {by_index[i]["code"] for i in definition.get("fences", []) if i in by_index}
        if "PS1104" in codes:
            if codes <= {"PS1104"}:
                only += 1
            else:
                double += 1
    assert only > 200, only
    assert double >= 1, double


def test_bare_any_still_fences_ps1104(tmp_path: Path) -> None:
    """The must-fence gate: bare `Any` stays a fence (the frozen probe
    code.PS1104 needs a recorded decision before this can move)."""

    from pyscriptc.coverage import build_report

    case = tmp_path / "bare.py"
    case.write_text(
        "from typing import Any\n\n\ndef f(x: Any) -> int:\n    return 1\n",
        encoding="utf-8",
    )
    report = build_report(case)
    codes = {fence["code"] for fence in report.get("fences", [])}
    assert "PS1104" in codes
