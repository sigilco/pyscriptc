"""The restlower typed-node seam (P2-0): `_shared` builders plus `_time`.

`restlower/` used to spell dict-IR nodes as `{"kind": ...}` literals and
read `node["type"]` / `typ["kind"]` at the call site. The converted
modules build typed `_irnode` records (reconstructed to dicts at the
restlower boundary) and read through accessors. These tests pin that
shape so later P2 modules can copy it:

- builders reconstruct the same dict IR the old literals spelled,
- accessors are the only place a converted module subscripts a node key,
- the converted restlower modules listed in `CONVERTED` have no raw node-key
  reads and no raw `{"kind": ...}` node/type literals.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"
RESTLOWER = SRC / "restlower"

CONVERTED = (
    RESTLOWER / "_shared.py",
    RESTLOWER / "_time.py",
    RESTLOWER / "_argparse.py",
    RESTLOWER / "_threading.py",
    RESTLOWER / "_minmax.py",
    RESTLOWER / "_iterators.py",
    RESTLOWER / "_builtins.py",
    RESTLOWER / "_queue.py",
    RESTLOWER / "_collections.py",
    RESTLOWER / "_io.py",
    RESTLOWER / "_itertools.py",
)

# The dict-IR keys a node or type record carries. A converted module
# reads them only inside the named accessors.
NODE_KEYS = frozenset(
    {
        "kind",
        "type",
        "value",
        "target",
        "span",
        "id",
        "op",
        "left",
        "right",
        "name",
        "object",
        "index",
        "callee",
        "args",
        "elems",
    }
)

ACCESSORS = frozenset(
    {
        "_type_kind",
        "_node_type",
        "_node_type_kind",
        "_node_kind",
        "_node_id",
        "_node_elems",
        "_node_value",
        "_node_span",
        "_type_value",
    }
)


def _function_hits(func: ast.FunctionDef | ast.AsyncFunctionDef, path: Path) -> list[str]:
    hits: list[str] = []
    allow_keys = func.name in ACCESSORS
    for sub in ast.walk(func):
        if isinstance(sub, ast.Subscript):
            sl = sub.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and sl.value in NODE_KEYS:
                if allow_keys:
                    continue
                hits.append(f"{path.name}:{sub.lineno}:{func.name}[{sl.value!r}]")
        if isinstance(sub, ast.Dict):
            for key in sub.keys:
                if isinstance(key, ast.Constant) and key.value == "kind":
                    hits.append(f"{path.name}:{sub.lineno}:{func.name}:dict-kind")
    return hits


def _module_hits(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            hits.extend(_function_hits(stmt, path))
            continue
        if isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    hits.extend(_function_hits(item, path))
            continue
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Subscript):
                sl = sub.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and sl.value in NODE_KEYS:
                    hits.append(f"{path.name}:{sub.lineno}:module[{sl.value!r}]")
            if isinstance(sub, ast.Dict):
                for key in sub.keys:
                    if isinstance(key, ast.Constant) and key.value == "kind":
                        hits.append(f"{path.name}:{sub.lineno}:module:dict-kind")
    return hits


def test_the_converted_modules_have_no_raw_node_key_reads() -> None:
    hits: list[str] = []
    for path in CONVERTED:
        hits.extend(_module_hits(path))
    assert hits == [], hits


def _shared_mod() -> ModuleType:
    """Load `_shared` after `lower`, so restlower's package init can finish.

    Importing `pyscriptc.restlower` first cycles through `lower._stmt_b`.
    The compiler's real path loads `lower` first; the seam tests do the same.
    """

    import pyscriptc.lower  # noqa: F401
    import pyscriptc.restlower._shared as _shared

    return _shared


def test_the_seam_builds_leaf_types_and_class_types() -> None:
    _shared = _shared_mod()

    assert _shared._t_leaf("int") == {"kind": "int"}
    assert _shared._t_leaf("none") == {"kind": "none"}
    assert _shared._t_leaf("float") == {"kind": "float"}
    assert _shared._t_leaf("str") == {"kind": "str"}
    assert _shared._class("StructTime") == {"kind": "class", "name": "StructTime"}


def test_the_seam_reads_the_type_kind_through_accessors() -> None:
    _shared = _shared_mod()

    typ = {"kind": "float"}
    node = {
        "kind": "name",
        "id": "x",
        "type": typ,
        "span": {"file": "a.py", "line": 1, "col": 0, "endLine": 1, "endCol": 1},
    }
    assert _shared._type_kind(typ) == "float"
    assert _shared._node_type(node) == typ
    assert _shared._node_type_kind(node) == "float"


def test_the_seam_builds_the_shared_nodes() -> None:
    _shared = _shared_mod()

    span = {"file": "a.py", "line": 1, "col": 0, "endLine": 1, "endCol": 1}
    typ = {"kind": "int"}
    left = _shared._int(1, span)
    assert left == {"kind": "const", "value": "1", "type": typ, "span": span}
    name = _shared._nm("x", typ, span)
    assert name == {"kind": "name", "id": "x", "type": typ, "span": span}
    assigned = _shared._asg("x", left, span)
    assert assigned == {"kind": "assign", "target": "x", "value": left, "span": span}
    cmp_node = _shared._cmp("==", name, left, span)
    assert cmp_node["kind"] == "compare"
    assert cmp_node["op"] == "=="
    assert cmp_node["left"] == name
    assert cmp_node["right"] == left
    assert cmp_node["type"] == {"kind": "bool"}
    attr = _shared._field(name, "idx", typ, span)
    assert attr["kind"] == "attr"
    assert attr["name"] == "idx"
    assert attr["value"] == name
    sub = _shared._sub(name, left, typ, span)
    assert sub["kind"] == "subscript"
    assert sub["value"] == name
    assert sub["index"] == left
    length = _shared._len_of(name, span)
    assert length["kind"] == "call"
    assert length["callee"] == {"kind": "builtin", "name": "len"}
    assert length["args"] == [name]
    assert length["type"] == {"kind": "int"}
    store = _shared._set_field(name, "idx", left, span)
    assert store["kind"] == "setattr"
    assert store["object"] == name
    assert store["name"] == "idx"
    assert store["value"] == left
    incr = _shared._incr(name, 1, span)
    assert incr["kind"] == "binary"
    assert incr["op"] == "+"
    text = _shared._const_text("hi", "str", span)
    assert text == {"kind": "const", "value": "hi", "type": {"kind": "str"}, "span": span}
    raw_int = {"kind": "const", "value": "2", "type": {"kind": "int"}, "span": span}
    cast = _shared._as_float(_Fail(), object(), raw_int, span, "time.sleep")
    assert cast["kind"] == "cast"
    assert cast["op"] == "int_to_float"
    assert cast["type"] == {"kind": "float"}


class _Fail:
    def fail(self, node: object, code: str, message: str) -> None:
        raise AssertionError(f"{code}: {message}")
