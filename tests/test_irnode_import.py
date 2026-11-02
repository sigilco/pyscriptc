"""The compiler-owned dataclass import path (R3 slice one).

The recorded decision draft (`.agents/plans/decisions/2026-11-02-import-
table-admits-ir-module.md`) proposed admitting `pyscriptc._irnode` to the
closed import table. The own-package path makes that unnecessary for the
self-host: `src/pyscriptc` compiles as one package, sibling imports route
through `_is_own_package` and the export table (`lower/__init__.py`
`_bind_own_from`), and the closed table is never consulted for a module
inside the package. The real admission gate is the dataclass body itself:
bool fields, `Any` fields, and non-constant defaults all fence. These
tests pin the sanctioned path end to end: a sibling dataclass module
imports cleanly in program mode, its instances construct, its name
annotates, and an unknown module still refuses PS2002 (test_program.py
has the user-facing twin; this file keeps the typed-IR seam covered).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyscriptc.coverage import build_report

DATACLASS_MODULE = """
from dataclasses import dataclass


@dataclass
class Node:
    kind: str
    child: Node | None
    weight: int = 0


@dataclass
class Span:
    file: str
    line: int
"""

ENTRY = """
from irnode import Node, Span


def build(flag: bool) -> Node:
    span = Span(file="m.py", line=1)
    if flag:
        node = Node(kind="name", child=None, weight=1)
    else:
        node = Node(kind="const", child=None, weight=0)
    return node


def describe(node: Node) -> str:
    return node.kind


def main() -> int:
    node = build(True)
    print(describe(node))
    return 0
"""


def _program(tmp_path: Path) -> Path:
    (tmp_path / "irnode.py").write_text(DATACLASS_MODULE)
    (tmp_path / "__main__.py").write_text(ENTRY)
    return tmp_path


def test_a_sibling_dataclass_module_imports_and_annotates(tmp_path: Path) -> None:
    report = build_report(_program(tmp_path))
    codes = [fence["code"] for fence in report["fences"]]
    assert "PS2002" not in codes, report["fences"]
    assert "PS1106" not in codes, report["fences"]
    assert "PS1104" not in codes, report["fences"]
    assert "PS1105" not in codes, report["fences"]


def test_an_unknown_module_still_refuses_ps2002(tmp_path: Path) -> None:
    (tmp_path / "__main__.py").write_text(
        """
import nosuch

print(nosuch.f())
"""
    )
    report = build_report(tmp_path)
    fenced = [fence for fence in report["fences"] if fence["code"] == "PS2002"]
    assert fenced, report["fences"]
    assert "nosuch is not in the closed import table" in fenced[0]["message"]


def test_the_compiler_ir_module_itself_compiles_clean(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    report = build_report(root / "src" / "pyscriptc" / "_irnode.py")
    assert report["fences"] == [], report["fences"]


def test_the_typed_nodes_round_trip_the_canonical_bytes(tmp_path: Path) -> None:
    import json

    from pyscriptc import tirserde

    dict_module: dict[str, Any] = json.loads(
        """
{
  "irVersion": 1,
  "functions": [
    {
      "name": "f",
      "params": [{"name": "x", "type": {"kind": "int"}}],
      "returnType": {"kind": "int"},
      "locals": [],
      "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
      "body": [
        {
          "kind": "return",
          "value": {"kind": "name", "id": "x", "type": {"kind": "int"},
                    "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9}},
          "span": {"file": "m.py", "line": 1, "col": 5, "endLine": 1, "endCol": 13}
        }
      ]
    }
  ],
  "classes": [],
  "fences": [],
  "entry": "f",
  "subset": "compat2",
  "cpython": "3.14",
  "sourceFile": "m.py"
}
"""
    )
    from pyscriptc._irnode import name_expr, return_stmt, t_leaf

    typed = json.loads(json.dumps(dict_module))
    typed["functions"][0]["body"] = [
        return_stmt(
            name_expr("x", t_leaf("int"), Span_of()),
            Span_of("m.py", 1, 5, 1, 13),
        )
    ]
    assert tirserde.hash_module_node(typed) == tirserde.hash_module(dict_module)


def Span_of(file: str = "m.py", line: int = 1, col: int = 1, end_line: int = 1, end_col: int = 9):
    from pyscriptc._irnode import Span

    return Span(file, line, col, end_line, end_col)
