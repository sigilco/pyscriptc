"""B11 adapter tests: the per-function MIR-or-direct records, the debug
surface and the byte-identity guarantee on the mir_leaf fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.debug import Debug
from pyscriptc.emit._mir_in import last_plan, out_of_slice_reason, plan_module
from pyscriptc.emit.llvm import emit_llvm
from pyscriptc.lower import lower_file
from pyscriptc.miremit import emit_llvm_mir

FIXTURES = Path(__file__).parent / "fixtures" / "mir_leaf"
FIXTURE_SAMPLES = sorted(FIXTURES.glob("*.py"))


@pytest.mark.parametrize("path", [str(p) for p in FIXTURE_SAMPLES])
def test_emit_llvm_mir_byte_identical_to_direct(path: str) -> None:
    """Every mir_leaf fixture emits the same bytes through both lanes."""
    module, _ = lower_file(path)
    assert emit_llvm_mir(module) == emit_llvm(module)


def test_fallback_records_carry_function_module_reason(tmp_path: Path) -> None:
    src = tmp_path / "adapter.py"
    src.write_text(
        "def leaf(a: int) -> int:\n    return a + 1\n\n\n"
        'def text(a: int) -> bytes:\n    return b"x"\n\n\n'
        "def bits(a: int) -> int:\n    if a > 0:\n        return a & 3\n    return a\n",
        encoding="utf-8",
    )
    module, _ = lower_file(str(src))
    plan = plan_module(module)
    fallbacks: list[dict] = plan["fallbacks"]
    mir = plan["mir"]
    by_function = {record["function"]: record for record in fallbacks}
    assert set(by_function) == set(mir.skipped)
    for record in fallbacks:
        assert set(record) == {"module", "function", "reason"}
        assert record["module"] == module["sourceFile"]
    assert by_function["text"]["reason"] == "return:bytes"
    assert "leaf" not in by_function
    # `int &` joined the leaf slice in #1016, so `bits` rides the MIR lane.
    assert "bits" not in by_function
    assert "bits" in {fn.name for fn in mir.functions}
    assert [record["function"] for record in fallbacks] == ["text"]


def test_callee_out_of_slice_reason(tmp_path: Path) -> None:
    """A function `supported_fn` accepts but the callee fixpoint rejects
    records `fn:callee out of slice`, matching the coverage report."""
    src = tmp_path / "callee.py"
    src.write_text(
        'def inner(a: int) -> int:\n    s = b"x"\n    return a\n\n\n'
        "def outer(a: int) -> int:\n    return inner(a) + 1\n",
        encoding="utf-8",
    )
    module, _ = lower_file(str(src))
    plan = plan_module(module)
    fallbacks: list[dict] = plan["fallbacks"]
    by_function = {record["function"]: record for record in fallbacks}
    assert by_function["outer"]["reason"] == "fn:callee out of slice"


def test_emit_records_last_plan(tmp_path: Path) -> None:
    src = tmp_path / "emitrec.py"
    src.write_text("def leaf(a: int) -> int:\n    return a + 1\n", encoding="utf-8")
    module, _ = lower_file(str(src))
    emit_llvm_mir(module)
    plan = last_plan()
    assert plan is not None
    assert plan["source_file"] == module["sourceFile"]
    lowered = {fn.name for fn in plan["mir"].functions}
    assert "leaf" in lowered
    for record in plan["fallbacks"]:
        assert record["function"] not in lowered


def test_out_of_slice_reason_matches_supported_fn(tmp_path: Path) -> None:
    src = tmp_path / "reason.py"
    src.write_text(
        "def leaf(a: int) -> int:\n    return a + 1\n\n\n"
        "def closure(a: int) -> int:\n    def inner() -> int:\n        return a\n    return inner()\n",
        encoding="utf-8",
    )
    module, _ = lower_file(str(src))
    by_name = {fn["name"]: fn for fn in module["functions"]}
    assert out_of_slice_reason(by_name["leaf"]) is None
    assert out_of_slice_reason(by_name["closure.inner"]) == "fn:closure env"


def _dbg_with_emit_entry(topics: set[str]) -> Debug:
    dbg = Debug(topics, None)
    dbg.entries.append(("emit_llvm", 1.0, True))
    return dbg


def test_debug_stages_show_mir_summary_only_when_set() -> None:
    records = [{"module": "m.py", "function": "f", "reason": "return:str"}]
    dbg = _dbg_with_emit_entry({"stages"})
    dbg.set_mir(3, records)
    out = dbg.render(None, {})
    assert "[debug] mir  lowered 3  adapter 1" in out
    assert "mir.adapter" not in out

    dbg_off = _dbg_with_emit_entry({"stages", "mir"})
    out_off = dbg_off.render(None, {})
    assert "[debug] mir" not in out_off


def test_debug_mir_topic_lists_adapter_functions_sorted() -> None:
    records = [
        {"module": "b.py", "function": "g", "reason": "stmt:for"},
        {"module": "a.py", "function": "z", "reason": "return:str"},
        {"module": "a.py", "function": "a", "reason": "expr:call"},
    ]
    dbg = _dbg_with_emit_entry({"stages", "mir"})
    dbg.set_mir(0, records)
    out = dbg.render(None, {})
    adapter_lines = [line for line in out.splitlines() if "mir.adapter" in line]
    assert adapter_lines == [
        "[debug] mir.adapter a.py:a  expr:call",
        "[debug] mir.adapter a.py:z  return:str",
        "[debug] mir.adapter b.py:g  stmt:for",
    ]


def test_summary_json_carries_mir_adapter() -> None:
    records = [{"module": "m.py", "function": "f", "reason": "return:str"}]
    dbg = _dbg_with_emit_entry({"stages"})
    dbg.set_mir(3, records)
    summary = dbg.summary(None, {}, "ok")
    assert summary["mirAdapter"] == records
