"""The pyscriptc_frame lowering (issue #497, slice 1).

The module name is the surface pandas spells, minus everything outside
the slice-1 subset: `DataFrame` built from a dict display of numeric
columns, the column reads and writes, the metadata (`columns`, `shape`,
`len`), `head`, `tail` and the homogeneous `to_dict`. Everything else
fences with PS2001 naming this module — never a silent approximation.

The frame is a runtime record (`runtime/ps_frame.c`): a str-keyed map of
`ndarray` columns plus the shared row count. The schema rides the IR
type's `label` (`DataFrame[a=float64,b=int64]`), the way the ndarray
label carries its dtype — the label is the one part of a class type the
dict IR round trip keeps, so the compiler checks every column name and
dtype at compile time and the runtime never guesses.

Slice-1 restrictions, each stated at the fence:

- the constructor takes a dict display literal (the closed IR container
  vocabulary cannot type a dict whose values are heterogeneous buffers);
- numeric columns only (`list[int]` / `list[float]`); str columns come
  with `read_csv` in slice 2;
- `df["col"] = values` replaces an existing column of the same dtype
  (adding a column changes the schema label and waits for the
  schema-rebind slice);
- `to_dict()` needs a homogeneous frame (one value kind across the
  columns) while the dict value type is closed.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _assign, _name

# The ndarray dtype codes, the order the runtime's enum spells.
_DTYPES: dict[str, int] = {"int32": 0, "int64": 1, "float32": 2, "float64": 3}


def _frame_type(schema: list[tuple[str, str]]) -> dict[str, Any]:
    """The IR type of a frame value; the schema rides in the label
    (`DataFrame[a=float64,b=int64]`), the one part of a class type the
    dict IR round trip keeps."""
    body = ",".join(f"{name}={dtype}" for name, dtype in schema)
    return {"kind": "class", "name": "DataFrame", "label": f"DataFrame[{body}]"}


def _is_frame(typ: dict[str, Any]) -> bool:
    return typ.get("kind") == "class" and typ.get("name") == "DataFrame"


def _schema_of(typ: dict[str, Any]) -> list[tuple[str, str]]:
    """The schema a frame IR type carries, read back from its label."""
    label = str(typ.get("label", ""))
    if not (label.startswith("DataFrame[") and label.endswith("]")):
        return []
    body = label[len("DataFrame[") : -1]
    schema: list[tuple[str, str]] = []
    for part in body.split(","):
        if "=" not in part:
            continue
        name, dtype = part.split("=", 1)
        schema.append((name.strip(), dtype.strip()))
    return schema


def _dtype_of_col(typ: dict[str, Any]) -> str | None:
    """The dtype a column (an ndarray IR type) carries, or None."""
    label = str(typ.get("label", ""))
    if typ.get("kind") == "class" and typ.get("name") == "ndarray" and label.startswith("ndarray["):
        return label[len("ndarray[") :].split(",", 1)[0].strip()
    return None


def _const_str_node(text: str, span: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "const", "value": text, "type": {"kind": "str"}, "span": span}


def _frame_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """The module-level entry points. Slice 1 lowers `DataFrame` only."""
    if attr == "DataFrame":
        return _constructor(lo, node, span)
    lo.fail(node, "PS2001", f"pyscriptc_frame.{attr} is not in the lowered subset of pyscriptc_frame")
    raise AssertionError("unreachable")


def _column_of(lo: Any, node: ast.expr, name: str, span: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """One column value: a list display of numbers or an ndarray.

    The list display lowers through the generic list path (the elements'
    own types decide the dtype, numpy's rule: a float anywhere floats);
    an ndarray rides its label's dtype. Anything else fences."""
    if isinstance(node, ast.List):
        value = lo._expr(node)
        if value["type"].get("kind") != "list" or value["type"]["elem"]["kind"] not in {"int", "float"}:
            lo.fail(node, "PS1003", f"the DataFrame column {name} is a list of numbers")
        dtype = "float64" if value["type"]["elem"]["kind"] == "float" else "int64"
        col = lo._abi_call(
            "pyscriptc_frame",
            "column",
            [
                {"kind": "const", "value": str(_DTYPES[dtype]), "type": {"kind": "int"}, "span": span},
                value,
                {
                    "kind": "const",
                    "value": "1" if value["type"]["elem"]["kind"] == "int" else "0",
                    "type": {"kind": "int"},
                    "span": span,
                },
            ],
            {"kind": "class", "name": "ndarray", "label": f"ndarray[{dtype},1]"},
            "ps_nd_from_seq",
            span,
        )
        return col, dtype
    value = lo._expr(node)
    dtype = _dtype_of_col(value["type"])
    if dtype is None:
        lo.fail(node, "PS1003", f"the DataFrame column {name} is a list of numbers or an ndarray")
    return value, dtype


def _constructor(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`DataFrame({...})` from a dict display of numeric columns.

    The display literal is the contract: each entry's value lowers on its
    own, the schema collects in display order, and the lowering hands the
    emitter one `with_col` call per column. A non-literal dict has no
    schema the IR type can carry, so it fences rather than approximating.
    """
    for keyword in node.keywords:
        lo.fail(node, "PS2003", f"pyscriptc_frame.DataFrame keyword {keyword.arg} is not lowered")
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Dict):
        lo.fail(node, "PS2003", "pyscriptc_frame.DataFrame takes a dict display of columns")
    display = node.args[0]
    schema: list[tuple[str, str]] = []
    columns: list[tuple[str, dict[str, Any]]] = []
    for key_node, value_node in zip(display.keys, display.values, strict=False):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            lo.fail(key_node if key_node is not None else node, "PS1003", "a DataFrame column name is a string")
        name = key_node.value
        if any(existing == name for existing, _ in schema):
            lo.fail(key_node, "PS1003", f"a DataFrame has one column named {name}")
        col, dtype = _column_of(lo, value_node, name, span)
        columns.append((name, col))
        schema.append((name, dtype))
    if not schema:
        lo.fail(node, "PS2003", "pyscriptc_frame.DataFrame needs at least one column")
    out = _frame_type(schema)
    # `fresh = ps_frame_new()` into a temp, one `with_col` per column, the
    # temp is the result: the prelude the emitter renders in order.
    holder = lo._temp("frame")
    lo.locals[holder] = out
    fresh = lo._abi_call("pyscriptc_frame", "DataFrame", [], out, "ps_frame_new", span)
    lo.prelude.append(_assign(holder, fresh, span))
    frame = {"kind": "name", "id": holder, "type": out, "span": span}
    for name, col in columns:
        call = lo._abi_call(
            "pyscriptc_frame",
            "with_column",
            [frame, _const_str_node(name, span), col],
            out,
            "ps_frame_with_col",
            span,
        )
        lo.prelude.append(_assign(holder, call, span))
    return _name(holder, out, span)


# The method names of the slice-1 subset. A name outside this set fences.
_METHODS = frozenset({"head", "tail", "to_dict"})


def _frame_method(
    lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], attr: str
) -> dict[str, Any] | None:
    """The frame method calls, or None when `attr` is not a method.

    The caller (lower._call_attr) routes the receiver's method call here
    before the class-method table; the return is the same shape
    `_synth_call` produces. `sort_values`, `groupby` and `describe` are
    slice 3: the routing returns None for them and the caller's fence
    names the module."""
    if attr == "head":
        return _rows_slice(lo, node, span, recv, "head", "ps_frame_head")
    if attr == "tail":
        return _rows_slice(lo, node, span, recv, "tail", "ps_frame_tail")
    if attr == "to_dict":
        return _to_dict(lo, node, span, recv)
    return None


def _rows_slice(
    lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any], attr: str, symbol: str
) -> dict[str, Any]:
    """`head(n)` and `tail(n)`: the first or last n rows. The default n=5
    rides as a constant when the call carries no argument (pandas')."""
    for keyword in node.keywords:
        lo.fail(node, "PS2003", f"pyscriptc_frame.{attr} keyword {keyword.arg} is not lowered")
    args_nodes = list(node.args)
    if len(args_nodes) > 1:
        lo.fail(node, "PS2003", f"pyscriptc_frame.{attr} takes at most one argument")
    if args_nodes:
        count = lo._expr(args_nodes[0])
        if count["type"]["kind"] != "int":
            lo.fail(node, "PS1003", f"pyscriptc_frame.{attr} count is an int")
    else:
        count = {"kind": "const", "value": "5", "type": {"kind": "int"}, "span": span}
    return lo._abi_call("pyscriptc_frame", attr, [recv, count], recv["type"], symbol, span)


def _to_dict(lo: Any, node: ast.Call, span: dict[str, Any], recv: dict[str, Any]) -> dict[str, Any]:
    """`to_dict()`: a dict[str, list[number]] over a homogeneous frame.

    The value type reads off the schema: one dtype across the columns is
    the contract while the IR's dict value type is closed, and a mixed
    frame fences. `orient="records"` is slice 2; the keyword fences by
    name until it lands."""
    args_nodes = list(node.args)
    for keyword in node.keywords:
        if keyword.arg == "orient":
            lo.fail(node, "PS2001", "pyscriptc_frame.to_dict(orient='records') is not in the slice-1 subset")
        lo.fail(node, "PS2003", f"pyscriptc_frame.to_dict keyword {keyword.arg} is not lowered")
    if args_nodes:
        lo.fail(node, "PS2003", "pyscriptc_frame.to_dict takes no positional arguments")
    schema = _schema_of(recv["type"])
    dtypes = {dtype for _, dtype in schema}
    if len(dtypes) != 1:
        lo.fail(node, "PS2001", "pyscriptc_frame.to_dict needs a homogeneous frame (one dtype across the columns)")
    dtype = next(iter(dtypes))
    elem: dict[str, Any] = {"kind": "int"} if dtype in {"int32", "int64"} else {"kind": "float"}
    return lo._abi_call(
        "pyscriptc_frame",
        "to_dict",
        [recv],
        {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "list", "elem": elem}},
        "ps_frame_to_dict",
        span,
    )
