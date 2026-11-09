"""Validate-pass seam: IR schema checks read kinds through accessors."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IR_PY = ROOT / "src" / "pyscriptc" / "ir.py"
_SEAM_ACCESSORS = {"_kind", "_node_kind", "_maybe_node_kind", "_node_type_kind"}


def test_the_validate_seam_reads_kind_tags_through_accessors() -> None:
    from pyscriptc.ir import _kind, _maybe_node_kind, _node_kind, _node_type_kind

    assert _kind({"kind": "int"}) == "int"
    assert _node_kind({"kind": "assign"}) == "assign"
    assert _maybe_node_kind({"kind": "raise"}) == "raise"
    assert _maybe_node_kind({"target": "x"}) is None
    assert _node_type_kind({"kind": "const", "type": {"kind": "str"}}) == "str"


def test_ir_validate_has_no_raw_node_kind_reads() -> None:
    tree = ast.parse(IR_PY.read_text(encoding="utf-8"))
    hits: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.inside = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            entered = node.name in _SEAM_ACCESSORS
            if entered:
                self.inside += 1
            self.generic_visit(node)
            if entered:
                self.inside -= 1

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Subscript(self, node: ast.Subscript) -> None:
            sl = node.slice
            if self.inside == 0 and isinstance(sl, ast.Constant) and sl.value == "kind":
                hits.append(f"L{node.lineno}")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                self.inside == 0
                and isinstance(func, ast.Attribute)
                and func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "kind"
            ):
                hits.append(f"L{node.lineno}")
            self.generic_visit(node)

    Visitor().visit(tree)
    assert hits == [], f"raw kind reads outside accessors: {hits}"
