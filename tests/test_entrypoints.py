"""Tests for benchmarks/compat2/entrypoints.py (E1 entry-point detector)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.compat2 import entrypoints


def scan_source(tmp_path: Path, source: str):
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return entrypoints.scan_file(path, rel=Path("probe.py"))


def test_pure_module_is_not_a_script(tmp_path: Path):
    rec = scan_source(
        tmp_path,
        '"""doc"""\nimport os\nfrom sys import path\n\n\ndef f(a: int) -> int:\n    return a\n',
    )
    assert rec["is_script"] is False
    assert rec["entry_stmt_count"] == 0
    assert rec["statement_kinds"] == ["Docstring", "Import", "ImportFrom", "FunctionDef"]


def test_module_assign_is_entry_relevant(tmp_path: Path):
    # Per the spec's inert set, a bare Assign counts toward entry_stmt_count:
    # it lowers into the entry function when it cannot bind as a constant.
    rec = scan_source(tmp_path, "X = 1\n")
    assert rec["is_script"] is True
    assert rec["entry_stmt_count"] == 1


def test_bare_call_makes_a_script(tmp_path: Path):
    rec = scan_source(tmp_path, "def f() -> int:\n    return 1\n\n\nprint(f())\n")
    assert rec["is_script"] is True
    assert rec["entry_stmt_count"] == 1
    assert rec["print_calls"] == 1


def test_guard_body_inventoried_one_level(tmp_path: Path):
    rec = scan_source(
        tmp_path,
        'def f() -> int:\n    return 1\n\n\nif __name__ == "__main__":\n    print(f())\n    x = 2\n',
    )
    assert rec["has_main_guard"] is True
    assert rec["is_script"] is True
    assert rec["statement_kinds"] == ["FunctionDef", "If", "Expr", "Assign"]
    assert rec["entry_stmt_count"] == 2
    assert rec["print_calls"] == 1
    guard_stmts = [s for s in rec["statements"] if s["where"] == "guard"]
    assert len(guard_stmts) == 2


def test_reversed_comparison_is_not_a_guard(tmp_path: Path):
    rec = scan_source(tmp_path, 'if "__main__" == __name__:\n    print(1)\n')
    assert rec["has_main_guard"] is False
    assert [s["where"] for s in rec["statements"]] == ["top"]
    # The non-guard If is itself the entry statement; its body is not flattened.
    assert rec["statement_kinds"] == ["If"]
    assert rec["entry_stmt_count"] == 1


def test_docstring_only_at_position_zero(tmp_path: Path):
    rec = scan_source(tmp_path, 'x = 1\n"not a docstring"\n')
    assert rec["statement_kinds"] == ["Assign", "Expr"]


def test_syntax_error_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bad = tmp_path / "examples"
    bad.mkdir()
    (bad / "broken.py").write_text("def f(:\n", encoding="utf-8")
    monkeypatch.setattr(entrypoints, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(entrypoints, "SCRIPTS_DIR", Path("empty"))
    records = entrypoints.scan(with_fences=False)
    assert len(records) == 1
    assert records[0]["is_script"] is False
    assert "error" in records[0]


def test_serialization_is_byte_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ex = tmp_path / "examples"
    ex.mkdir()
    (ex / "a.py").write_text("print(1)\n", encoding="utf-8")
    (ex / "b.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(entrypoints, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(entrypoints, "SCRIPTS_DIR", Path("empty"))
    first = entrypoints.serialize(entrypoints.scan(with_fences=False))
    second = entrypoints.serialize(entrypoints.scan(with_fences=False))
    assert first == second
    assert first.endswith("\n")
    json.loads(first)  # parses


def test_counts_match_spec_tables():
    records = entrypoints.scan(with_fences=False)
    examples = [r for r in records if r["path"].startswith("examples/")]
    scripts = [r for r in records if r["path"].startswith("benchmarks/compat2/corpus/scripts/")]
    # The sqlite pilot example (#66) is the 29th example and the fifth
    # script: its `__main__` guard calls `main()`, so the detector
    # classifies it as a script.
    assert len(examples) == 29
    assert sum(1 for r in examples if r["is_script"]) == 5
    assert len(scripts) == 20
    assert sum(1 for r in scripts if r["is_script"]) == 20


def test_validation_marks_entry_fences():
    """All entry statements compile today: fib_recursive and word_freq
    un-fenced in #44 and #45. Other scripts fence inside their own
    definitions first; the three example guard scripts already compile.
    R8/R9: exceptions_flow's definitions now lower (try/finally, except-as);
    its one remaining fence is `print(err)` on an exc at the entry statement,
    which sits one entry fix from flipping — recorded so the next milestone
    decides whether print of exc should lift.
    R12: gcd_lcm's definitions now lower too (int `%`, `//`, `**`); its
    entry fences are unannotated module-level `g`/`total` (PS1001), the
    same "needs an annotation before use" family as before.
    #500: local and parameter inference lifts the definition fences of
    bit_tricks and gcd_lcm, so their first fence now sits on the entry
    statement and the validator marks them; exceptions_flow's entry fences
    are the print-of-list and print-of-exc shapes, untouched here."""
    records = entrypoints.scan(with_fences=True)
    flips = sorted(r["path"] for r in records if r.get("predicted_flip"))
    assert flips == [
        "benchmarks/compat2/corpus/scripts/bit_tricks/main.py",
        "benchmarks/compat2/corpus/scripts/exceptions_flow/main.py",
        "benchmarks/compat2/corpus/scripts/gcd_lcm/main.py",
    ]
    fib = next(r for r in records if r["path"].endswith("fib_recursive/main.py"))
    assert fib["first_fence"] is None
    assert fib["fences_at_entry"] == []
