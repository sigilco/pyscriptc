"""print, input, open and NamedTemporaryFile lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class, _const_int

_OPEN = {"r": 1, "w": 2, "rb": 3, "wb": 4, "a": 5, "ab": 6}


# NamedTemporaryFile adds the read/write `+` modes; `open` keeps _OPEN only.
_TEMP_OPEN = {**_OPEN, "w+": 7, "w+b": 8}


_PRINT_KINDS = {"str", "int", "float", "bool", "bytes"}


def _printable(lo: Any, typ: dict[str, Any]) -> bool:
    """printable: a scalar or a tuple class whose fields are all printable."""

    if typ["kind"] in _PRINT_KINDS:
        return True
    if typ["kind"] == "class" and lo.classes.get(typ["name"], {}).get("tuple"):
        return all(_printable(lo, field["type"]) for field in lo.classes[typ["name"]]["fields"])
    return False


def _print(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`print` lowers to the builtin of the same name; the emitter writes stdout.

    Subset: positional values of the compiler's scalar set (str, int, float,
    bool, bytes), `sep=`/`end=` str keywords, and `file=` naming sys.stdout
    or sys.stderr; `flush=` stays fenced. The keyword exprs ride on the call
    dict so the emitter can splice them; `file` rides as a stream tag.
    """
    extras: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(kw, "PS2002", "**kwargs are not lowered")
        if kw.arg == "file":
            # `sys.stdout` (the default) or `sys.stderr` as the stream tag.
            resolved = lo._resolve_module(kw.value)
            if resolved is not None and resolved[1] == "sys" and resolved[2] in {"stdout", "stderr"}:
                extras["file"] = resolved[2]
                continue
            lo.fail(kw, "PS2002", "print file= takes sys.stdout or sys.stderr")
        if kw.arg not in {"sep", "end"}:
            lo.fail(
                kw,
                "PS2003",
                f"print keyword argument {kw.arg} is not lowered; print supports sep= and end= only",
            )
        value = lo._expr(kw.value)
        if value["type"]["kind"] != "str":
            lo.fail(kw, "PS1003", f"print {kw.arg}= takes a str")
        extras[kw.arg] = value
    if any(isinstance(arg, ast.Starred) for arg in node.args):
        lo.fail(node, "PS2002", "starred calls are not lowered")
    args = [lo._expr(arg) for arg in node.args]
    for index, arg in enumerate(args):
        if arg["type"]["kind"] == "class" and lo.classes.get(arg["type"]["name"], {}).get("namedtuple"):
            # A namedtuple prints `P(x=1, y=2)` in CPython; the tuple-class
            # print here would show `(1, 2)`. Fence instead of diverging.
            lo.fail(node, "PS2003", "print of a namedtuple is not lowered; print its fields")
        if not _printable(lo, arg["type"]):
            if arg["type"]["kind"] == "class":
                called = lo._dunder_call(node, arg, "__str__", [], arg["span"]) or lo._dunder_call(
                    node, arg, "__repr__", [], arg["span"]
                )
                if called is not None:
                    args[index] = called
                    continue
            lo.fail(
                node,
                "PS2003",
                f"print is lowered for str, int, float, bool, and bytes values, not {arg['type']['kind']};"
                " wrap it in str(...)",
            )
    expr = {
        "kind": "call",
        "callee": {"kind": "builtin", "name": "print"},
        "args": args,
        "type": {"kind": "none"},
        "span": span,
    }
    expr.update(extras)
    return expr


def _input(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`input()` reads one stdin line without the trailing newline.

    A prompt argument stays fenced; CPython writes it to stdout without a
    newline, which the simple stdin read here does not model. EOF raises
    RuntimeError in place of EOFError, which is not in the exception table.
    """
    if node.keywords:
        lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "input expects 0 argument(s)")
    return {
        "kind": "call",
        "callee": {"kind": "module", "module": "builtins", "name": "input"},
        "args": [],
        "symbol": "ps_input_line",
        "type": {"kind": "str"},
        "span": span,
    }


def _named_temp(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """tempfile.NamedTemporaryFile(mode, delete) maps onto the open handle
    machinery: it returns a TextIO/BytesIO class whose ps_io_close unlinks
    the file when `delete` is true. `name`, `dir`, and the other CPython
    keywords stay fenced.
    """
    if len(node.args) > 1:
        lo.fail(node, "PS2003", "tempfile.NamedTemporaryFile expects 0 or 1 argument(s)")
    if node.args and any(kw.arg == "mode" for kw in node.keywords):
        lo.fail(node, "PS2003", "NamedTemporaryFile mode is given twice")
    for kw in node.keywords:
        if kw.arg not in {"mode", "delete"}:
            lo.fail(node, "PS2003", "tempfile.NamedTemporaryFile lowers mode and delete keyword arguments")
    keep = 0
    for kw in node.keywords:
        if kw.arg == "delete":
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, bool):
                lo.fail(kw.value, "PS2002", "NamedTemporaryFile delete is a bool literal")
            keep = 0 if kw.value.value else 1
    mode = "w+b"
    positional = [*node.args, *[kw.value for kw in node.keywords if kw.arg == "mode"]]
    if positional:
        arg = positional[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) or arg.value not in _TEMP_OPEN:
            lo.fail(arg, "PS2002", "NamedTemporaryFile mode is one of r, w, rb, wb, a, ab, w+, w+b")
        mode = arg.value
    code = _TEMP_OPEN[mode]
    cls = "BytesIO" if "b" in mode else "TextIO"
    args = [
        {"kind": "const", "value": code, "type": {"kind": "int"}, "span": span},
        {"kind": "const", "value": keep, "type": {"kind": "int"}, "span": span},
    ]
    return lo._abi_call("tempfile", "NamedTemporaryFile", args, _class(cls), "ps_temp_named", span)


def _open(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    slots = lo._place_nodes(node, ["file", "mode"])
    if slots[0] is None:
        lo.fail(node, "PS2003", "open expects a path and an optional mode literal")
    path = lo._expr(slots[0])
    if path["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "open path is str")
    mode = "r"
    if slots[1] is not None:
        mode_node = slots[1]
        if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
            lo.fail(node, "PS2001", "open mode must be a string literal")
        mode = mode_node.value
    if mode not in _OPEN:
        lo.fail(node, "PS2001", "open mode is not lowered")
    cls = "BytesIO" if "b" in mode else "TextIO"
    code = _const_int(_OPEN[mode], span)
    return lo._abi_call("io", "open", [path, code], _class(cls), "ps_io_open", span)
