"""Drift gate for the single-source MIR opcode table.

`mir.py` owns the op vocabulary: the `MIR_*` id constants, the `OP_ARITY`
and `OP_RESULT_TYPE` tables, the `TERMINATORS`/`VALUE_OPS` sets, the
`_OP_NAMES` mnemonics, and the `op_arity`/`op_result_type`/
`is_terminator`/`is_value_op`/`op_name` accessors other modules must call
(the module-level tables cannot cross the closed import table; the
functions can). This test fails when a consumer module re-declares a
divergent copy: a same-shaped literal table, a shadow binding of a
canonical table name, or an op-id integer literal compared against an
op-carrying name.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pyscriptc.mir as mir

_CONSUMERS = ("mirlower", "mirverify", "mirprint", "miremit")

# The type tags share the `MIR_` prefix but are not op ids; a collection
# spelled only from them is a tag set, not an op table.
_TAG_NAMES = {"MIR_W", "MIR_D", "MIR_B", "MIR_S", "MIR_P", "MIR_VOID"}

# Canonical table names no consumer may rebind. `mir.py` keeps the
# originals; every other spelling is a divergent copy.
_TABLE_NAMES = {
    "OP_ARITY",
    "OP_RESULT_TYPE",
    "TERMINATORS",
    "VALUE_OPS",
    "OP_NAMES",
    "_OP_ARITY",
    "_OP_RESULT_TYPE",
    "_MIR_TERMINATORS",
    "_TERMINATORS",
    "_EXTRA_TERMINATORS",
    "_TAGLESS_VALUE_OPS",
}

_OP_ID_MAX = 57


def _consumer_path(name: str) -> Path:
    return Path(mir.__file__).parent / f"{name}.py"


def _is_op_name_node(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id.startswith("MIR_") and node.id not in _TAG_NAMES


def _is_op_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _OP_ID_MAX


def _is_op_ish(node: ast.AST) -> bool:
    if _is_op_name_node(node):
        return True
    return isinstance(node, ast.Constant) and _is_op_int(node.value)


def _head_text(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _head_text(node.value)
    return ""


def _op_table_offenses(tree: ast.AST, path: Path) -> list[str]:
    offenses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and len(node.keys) >= 8:
            keys = [k for k in node.keys if k is not None]
            if keys and all(_is_op_ish(k) for k in keys):
                offenses.append(f"{path}:{node.lineno}: op-keyed dict table")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) >= 4:
            if node.elts and all(_is_op_ish(e) for e in node.elts):
                offenses.append(f"{path}:{node.lineno}: op-id collection literal")
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for left, right in zip(operands, operands[1:], strict=False):
                for operand, other in ((left, right), (right, left)):
                    if (
                        isinstance(operand, ast.Constant)
                        and _is_op_int(operand.value)
                        and re.search(r"(^|_)ops?($|_)", _head_text(other))
                    ):
                        head = _head_text(other)
                        offenses.append(f"{path}:{node.lineno}: op-id literal {operand.value} compared to {head}")
    return offenses


def test_accessors_agree_with_the_canonical_tables() -> None:
    for op, arity in mir.OP_ARITY.items():
        assert mir.op_arity(op) == arity
        assert mir.op_result_type(op) == mir.OP_RESULT_TYPE[op]
        assert mir.is_terminator(op) == (op in mir.TERMINATORS)
        assert mir.is_value_op(op) == (op in mir.VALUE_OPS)
        assert mir.op_name(op) != f"op{op}"
    assert mir.op_arity(_OP_ID_MAX + 1) == -1
    assert mir.op_result_type(_OP_ID_MAX + 1) == -1
    assert not mir.is_terminator(_OP_ID_MAX + 1)
    assert not mir.is_value_op(_OP_ID_MAX + 1)
    assert mir.op_name(_OP_ID_MAX + 1) == f"op{_OP_ID_MAX + 1}"


def test_consumers_bind_no_table_names() -> None:
    import importlib

    for name in _CONSUMERS:
        module = importlib.import_module(f"pyscriptc.{name}")
        for table in _TABLE_NAMES:
            assert not hasattr(module, table), f"pyscriptc.{name} rebinds {table}"


def test_consumers_hold_no_op_table_literals() -> None:
    offenses: list[str] = []
    for name in _CONSUMERS:
        path = _consumer_path(name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenses.extend(_op_table_offenses(tree, path))
    assert not offenses, "\n".join(offenses)
