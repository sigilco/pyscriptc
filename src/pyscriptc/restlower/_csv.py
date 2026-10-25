"""csv call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _const_str
from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import _class


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
                if delim["type"]["kind"] != "str":
                    lo.fail(kw.value, "PS2003", "csv.reader delimiter must be a str")
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    if len(kw.value.value) != 1:
                        lo.fail(kw.value, "PS2003", '"delimiter" must be a 1-character string')
            else:
                lo.fail(node, "PS2003", f"csv.reader keyword {kw.arg} is not lowered")
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.reader expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = arg["type"]
        rows: dict[str, Any] = {"kind": "list", "elem": {"kind": "list", "elem": {"kind": "str"}}}
        if typ["kind"] == "list" and typ["elem"]["kind"] == "str":
            return lo._abi_call("csv", "reader", [arg, delim], rows, "ps_csv_read_lines", span)
        if typ["kind"] == "class" and typ.get("name") == "TextIO":
            return lo._abi_call("csv", "reader", [arg, delim], rows, "ps_csv_read_io", span)
        lo.fail(node, "PS1003", "csv.reader takes a list[str] or a text file")
    lo._reject_keywords(node)
    if attr == "writer":
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "csv.writer expects 1 argument(s)")
        arg = lo._expr(node.args[0])
        typ = arg["type"]
        if typ["kind"] == "class" and typ.get("name") == "TextIO":
            return lo._abi_call("csv", "writer", [arg], _class("CsvWriter"), "ps_csv_writer", span)
        lo.fail(node, "PS1003", "csv.writer takes a text file")
    lo.fail(node, "PS2001", f"csv.{attr} is not in the lowered subset of csv")
    raise AssertionError("unreachable")
