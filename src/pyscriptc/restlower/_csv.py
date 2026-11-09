"""csv call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _const_str
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import (
    _class,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _t_list,
    _type_kind,
    _type_name,
)


def _csv_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """csv.reader is eager: it returns list[list[str]] from a file or a
    list[str] of physical lines. csv.writer wraps the TextIO handle itself as
    the CsvWriter value and writes excel-dialect rows."""
    if attr == "reader":
        delim = _const_str(",", span)
        for kw in node.keywords:
            if kw.arg is None:
                lo.fail(node, "PS2002", kwargs_call_note())
            if kw.arg == "delimiter":
                delim = lo._expr(kw.value)
                if _node_type_kind(delim) != "str":
                    lo.fail(kw.value, "PS2003", "csv.reader delimiter must be a str")
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if len(kw.value.value) != 1:
                        lo.fail(kw.value, "PS2003", '"delimiter" must be a 1-character string')
            else:
                lo.fail(node, "PS2003", f"csv.reader keyword {kw.arg} is not lowered")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.reader expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = _node_type(arg)
        rows: dict[str, Any] = _t_list(_t_list(_t_leaf("str")))
        if _type_kind(typ) == "list" and _type_kind(typ["elem"]) == "str":
            return lo._abi_call("csv", "reader", [arg, delim], rows, "ps_csv_read_lines", span)
        if _type_kind(typ) == "class" and _type_name(typ) == "TextIO":
            return lo._abi_call("csv", "reader", [arg, delim], rows, "ps_csv_read_io", span)
        lo.fail(node, "PS1003", "csv.reader takes a list[str] or a text file")
    lo._reject_keywords(node)
    if attr == "writer":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.writer expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = _node_type(arg)
        if _type_kind(typ) == "class" and _type_name(typ) == "TextIO":
            return lo._abi_call("csv", "writer", [arg], _class("CsvWriter"), "ps_csv_writer", span)
        lo.fail(node, "PS1003", "csv.writer takes a text file")
    lo.fail(node, "PS2001", f"csv.{attr} is not in the lowered subset of csv")
    raise AssertionError("unreachable")
