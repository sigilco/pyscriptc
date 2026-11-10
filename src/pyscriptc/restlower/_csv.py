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
    _t_dict,
    _t_leaf,
    _t_list,
    _type_kind,
    _type_name,
)


# The row of a DictReader: a dict from a fieldname to the field text. The
# text is optional because a short record takes the restval, and CPython's
# reader restval default is None.
def _row_type(lo: Any, node: ast.AST) -> dict[str, Any]:
    opt = lo._opt_type(_t_leaf("str"), node)
    return _t_dict(_t_leaf("str"), opt)


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
    if attr == "DictReader":
        return _dict_reader(lo, node, span)
    if attr == "DictWriter":
        return _dict_writer(lo, node, span)
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


def _dict_reader(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`csv.DictReader(f)`: the header row names the fields of every row.

    The reader is eager: it returns a list of dict[str, str] rows, the same
    shape `csv.reader` returns, so a `for row in csv.DictReader(f)` drains
    the list like it drains the reader rows.

    A field a row does not carry takes the restval, the empty text CPython
    fills a short row with; the restkey and restval keywords are outside the
    subset and fenced by name.
    """
    delim = _const_str(",", span)
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if kw.arg == "delimiter":
            delim = lo._expr(kw.value)
            if _node_type_kind(delim) != "str":
                lo.fail(kw.value, "PS2003", "csv.DictReader delimiter must be a str")
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                if len(kw.value.value) != 1:
                    lo.fail(kw.value, "PS2003", '"delimiter" must be a 1-character string')
        elif kw.arg in {"restkey", "restval"}:
            lo.fail(node, "PS2003", f"csv.DictReader keyword {kw.arg} is not lowered")
        else:
            lo.fail(node, "PS2003", f"csv.DictReader keyword {kw.arg} is not lowered")
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "csv.DictReader expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    typ = _node_type(arg)
    rows: dict[str, Any] = _t_list(_row_type(lo, node))
    if _type_kind(typ) == "list" and _type_kind(typ["elem"]) == "str":
        return lo._abi_call("csv", "DictReader", [arg, delim], rows, "ps_csv_dict_reader_lines", span)
    if _type_kind(typ) == "class" and _type_name(typ) == "TextIO":
        return lo._abi_call("csv", "DictReader", [arg, delim], rows, "ps_csv_dict_reader", span)
    lo.fail(node, "PS1003", "csv.DictReader takes a list[str] or a text file")
    raise AssertionError("unreachable")


def _dict_writer(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`csv.DictWriter(f, fieldnames)`: the writer names the fields once.

    The handle is a CsvDictWriter value: it keeps the file handle and the
    fieldnames, and `writeheader()` and `writerow(row)` write the header row
    and the rows of a dict[str, str]. The extrasaction, restval, dialect and
    quoting keywords are outside the subset and fenced by name.
    """
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        lo.fail(node, "PS2003", f"csv.DictWriter keyword {kw.arg} is not lowered")
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "csv.DictWriter expects 2 argument(s)")
    handle = lo._expr(node.args[0])
    htyp = _node_type(handle)
    if not (_type_kind(htyp) == "class" and _type_name(htyp) == "TextIO"):
        lo.fail(node, "PS1003", "csv.DictWriter takes a text file")
    names = lo._expr(node.args[1])
    ntyp = _node_type(names)
    if not (_type_kind(ntyp) == "list" and _type_kind(ntyp["elem"]) == "str"):
        lo.fail(node, "PS1003", "csv.DictWriter fieldnames are a list[str]")
    return lo._abi_call("csv", "DictWriter", [handle, names], _class("CsvDictWriter"), "ps_csv_dict_writer", span)
