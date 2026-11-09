"""P6-f: binding, emitlib, bundle and boxed read IR through the emit/_ir seam."""

from __future__ import annotations

import ast
from pathlib import Path

from pyscriptc.binding import analyze_binding
from pyscriptc.boxed import ISLAND, NATIVE, island_names, unit_needs_island
from pyscriptc.emit import _ir

_IR_KEYS = frozenset({"kind", "functions", "classes", "body", "span"})
_SRC = Path(__file__).resolve().parents[1] / "src" / "pyscriptc"
_P6_MODULES = ("binding.py", "emitlib.py", "bundle.py", "boxed.py")


def _named_ir_reads(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in _IR_KEYS:
            count += 1
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _IR_KEYS
        ):
            count += 1
    return count


def test_p6_modules_have_no_raw_dict_ir_reads() -> None:
    for name in _P6_MODULES:
        assert _named_ir_reads(_SRC / name) == 0, name


def test_opt_record_reads_optional_nested_records() -> None:
    module = {"sourceFile": "m.py"}
    assert _ir._opt_record(module, "fallback") is None
    assert _ir._opt_record({"fallback": {"functions": {}}}, "fallback") == {"functions": {}}


def test_island_names_reads_the_functions_map() -> None:
    assert island_names({"functions": {"a": ISLAND, "b": NATIVE}}) == ["a"]
    assert island_names({}) == []


def test_unit_needs_island_reads_fallback_through_the_seam() -> None:
    assert unit_needs_island({"fallback": {"functions": {"f": ISLAND}}}) is True
    assert unit_needs_island({"fallback": {"classes": {"C": ISLAND}}}) is True
    assert unit_needs_island({}) is False


def test_analyze_binding_reads_functions_and_classes_through_the_seam() -> None:
    module = {
        "entry": "main",
        "functions": [
            {"name": "main", "params": [], "returnType": {"kind": "none"}},
            {
                "name": "add",
                "params": [{"name": "a", "type": {"kind": "int"}}],
                "returnType": {"kind": "int"},
            },
            {
                "name": "greet",
                "params": [{"name": "s", "type": {"kind": "str"}}],
                "returnType": {"kind": "none"},
            },
        ],
        "classes": [{"name": "Point"}],
    }
    emitted, skipped = analyze_binding(module)
    assert [_ir._node_name(fn) for fn in emitted] == ["add"]
    skipped_names = [name for name, _reason in skipped]
    assert "greet" in skipped_names
    assert "Point" in skipped_names
    assert "main" not in skipped_names
    assert "main" not in [_ir._node_name(fn) for fn in emitted]
