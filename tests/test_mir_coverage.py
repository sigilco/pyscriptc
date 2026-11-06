"""Unit tests for scripts/mir_coverage.py (A12 / D6)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mir_coverage.py"
VISIBLE_SCRIPTS = ROOT / "benchmarks" / "compat2" / "corpus" / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("mir_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visible_corpus_is_twenty_scripts() -> None:
    mod = _load()
    mains = mod.script_mains(VISIBLE_SCRIPTS)
    assert len(mains) == 20
    assert all(path.name == "main.py" for path in mains)
    assert mains == sorted(mains)


def test_fixture_corpus_counts_non_fenced_functions(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    (scripts / "leaf").mkdir(parents=True)
    (scripts / "leaf" / "main.py").write_text(
        "def steps(n: int) -> int:\n    c = 0\n    while n > 1:\n        n = n // 2\n        c += 1\n    return c\n",
        encoding="utf-8",
    )
    (scripts / "fenced").mkdir(parents=True)
    (scripts / "fenced" / "main.py").write_text(
        "def dump(obj):\n    return str(obj)\n",
        encoding="utf-8",
    )
    report = _load().measure_corpus(scripts)
    by_name = {row["script"]: row for row in report["scripts"]}
    assert by_name["leaf"]["in_slice"] == 1
    assert by_name["leaf"]["functions"] >= 1
    assert "steps" in by_name["leaf"]["in_slice_names"]
    assert by_name["fenced"]["in_slice"] == 0
    assert report["in_slice"] == 1
    assert report["script_count"] == 2
    assert 0 <= int(report["percent"]) <= 100


def test_format_report_is_deterministic(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    (scripts / "b").mkdir(parents=True)
    (scripts / "a").mkdir(parents=True)
    (scripts / "b" / "main.py").write_text("def g(n: int) -> int:\n    return n\n", encoding="utf-8")
    (scripts / "a" / "main.py").write_text("def f(n: int) -> int:\n    return n\n", encoding="utf-8")
    mod = _load()
    report = mod.measure_corpus(scripts)
    text = mod.format_report(report)
    assert text == mod.format_report(report)
    assert text.startswith("MIR slice coverage")
    assert text.index("a") < text.index("b")
    assert text.endswith("\n")
    assert "TOTAL" in text


def test_cli_text_is_byte_identical_twice(tmp_path: Path) -> None:
    out_a = tmp_path / "a.txt"
    out_b = tmp_path / "b.txt"
    cmd = [sys.executable, str(SCRIPT)]
    for dest in (out_a, out_b):
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
        dest.write_text(done.stdout, encoding="utf-8")
    assert out_a.read_bytes() == out_b.read_bytes()
    text = out_a.read_text(encoding="utf-8")
    assert "TOTAL" in text
    assert "MIR slice coverage" in text


def test_cli_json_matches_text_totals() -> None:
    text = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    payload = json.loads(
        subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert payload["script_count"] == 20
    assert payload["in_slice"] >= 0
    assert payload["functions"] >= payload["in_slice"]
    assert f"{payload['in_slice']:8d}" in text
    assert f"{payload['percent']}%" in text
    names = [row["script"] for row in payload["scripts"]]
    assert names == sorted(names)


def test_out_of_slice_reason_names_the_innermost_rejected_node(tmp_path: Path) -> None:
    from pyscriptc.lower import lower_file

    mod = _load()
    src = tmp_path / "reasons.py"
    src.write_text(
        "def leaf(a: int) -> int:\n    return a + 1\n\n\n"
        "def calls(a: int) -> int:\n    return leaf(a) + 1\n\n\n"
        "def text(a: int) -> str:\n    return str(a)\n\n\n"
        "def bits(a: int) -> int:\n    if a > 0:\n        return len([a])\n    return a\n",
        encoding="utf-8",
    )
    module, _ = lower_file(str(src))
    by_name = {fn["name"]: fn for fn in module["functions"]}
    assert mod.out_of_slice_reason(by_name["leaf"]) is None
    # A call to a plain module-level user function is in the slice since
    # the call slice (A3): the callee lowers to a `MIR_CALL` and the
    # caller rides the same lane.
    assert mod.out_of_slice_reason(by_name["calls"]) is None
    assert mod.out_of_slice_reason(by_name["text"]) == "return:str"
    assert mod.out_of_slice_reason(by_name["bits"]) == "expr:call"


def test_reasons_cover_every_out_of_slice_function() -> None:
    mod = _load()
    report = mod.measure_corpus(VISIBLE_SCRIPTS)
    reasons = report["reasons"]
    assert sum(reasons.values()) == report["functions"] - report["in_slice"]
    for row in report["scripts"]:
        assert set(row["out_of_slice"]) == set(row["names"]) - set(row["in_slice_names"])
    top = mod.top_reasons(report)
    assert len(top) <= mod.TOP_REASONS
    assert list(top.values()) == sorted(top.values(), reverse=True)
    summary = mod.coverage_summary(report=report)
    assert summary["out_of_slice"] == sum(reasons.values())
    assert summary["reason_kinds"] == len(reasons)
    assert "Out-of-slice reasons (top 10)" in mod.format_report(report)
