"""subprocess call lowering.

`subprocess.run(cmd)` returns the CompletedProcess record, `subprocess.call`
the exit code, and `subprocess.check_call` the exit code with a non-zero
code raising RuntimeError. `run` takes `capture_output=True`, `text=` and
`check=` as bool literals: check joins the flag to ps_sub_run so a non-zero
code raises the check_call stand-in. `text=True` decodes the captured
streams through the runtime's CPython-exact decoder (the record fields are
str), and rides with `encoding=`/`errors=` string literals naming the codec
and error handler. The command is a `list[str]`, the shape scripts use.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.reflection import kwargs_call_note
from pyscriptc.restlower._shared import (
    _call_builtin,
    _class,
    _const_text,
    _flag,
    _int,
    _node_type,
    _node_type_kind,
    _t_leaf,
    _type_kind,
    _type_name,
)


def _argv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """The command list: `list[str]` with one element type."""
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "subprocess expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    arg_t = _node_type(arg)
    if _type_kind(arg_t) != "list" or _flag(arg_t, "set"):
        lo.fail(node, "PS1003", "subprocess takes a list of str")
    if _type_kind(arg_t["elem"]) != "str":
        lo.fail(node, "PS1003", "subprocess takes a list of str")
    return arg


def _capture_output(node: ast.Call) -> bool:
    """`capture_output=True` is the only capture keyword this subset reads."""
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg == "capture_output" and isinstance(keyword.value, ast.Constant):
            if keyword.value.value is True:
                return True
    return False


# The run keywords whose value is a bool.
_RUN_BOOL = {"capture_output", "check", "text"}

# encoding= names one of the codecs the runtime's text decoder implements;
# anything else fences.
_TEXT_ENCODINGS = {"utf-8", "ascii", "latin-1"}

# errors= names one of the handlers the decoder implements.
_TEXT_ERRORS = {"strict", "replace", "ignore", "backslashreplace"}


def _text_mode(lo: Any, node: ast.Call, span: dict[str, Any]) -> tuple[bool, int, int] | None:
    """The text-mode shape: `text=True` (or an `encoding=` name) turns the
    captured streams into str through the runtime's decoder. Returns the
    encoding and errors codes the runtime ABI carries, or None when the call
    stays in the default bytes mode."""

    text = False
    encoding = "utf-8"
    errors = "strict"
    for keyword in node.keywords:
        if keyword.arg == "text" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
            text = True
        if keyword.arg == "encoding":
            if not (isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)):
                lo.fail(keyword.value, "PS2003", "subprocess.run encoding takes a string literal")
            encoding = keyword.value.value.lower()
            if encoding not in _TEXT_ENCODINGS:
                lo.fail(
                    keyword.value,
                    "PS2003",
                    f"subprocess.run encoding {keyword.value.value!r} is not lowered",
                )
            text = True
        if keyword.arg == "errors":
            if not (isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)):
                lo.fail(keyword.value, "PS2003", "subprocess.run errors takes a string literal")
            errors = keyword.value.value.lower()
            if errors not in _TEXT_ERRORS:
                lo.fail(
                    keyword.value,
                    "PS2003",
                    f"subprocess.run errors {keyword.value.value!r} is not lowered",
                )
    if not text:
        return None
    enc = {"utf-8": 0, "ascii": 1, "latin-1": 2}[encoding]
    err = {"strict": 0, "replace": 1, "ignore": 2, "backslashreplace": 3}[errors]
    return (True, enc, err)


def _raise_flag(node: ast.Call) -> bool:
    """`check=True` raises the check_call stand-in after the run records the exit code."""
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg == "check" and isinstance(keyword.value, ast.Constant):
            if keyword.value.value is True:
                return True
    return False


def _cwd(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`cwd=` parameter as a str value or empty str if omitted/None."""
    for keyword in node.keywords:
        if keyword.arg == "cwd":
            val = lo._expr(keyword.value)
            kind = _node_type_kind(val)
            if kind == "str":
                return val
            if kind == "class" and _type_name(_node_type(val)) == "Path":
                return _call_builtin("str", [val], _t_leaf("str"), span)
            if kind == "none":
                return _const_text("", "str", span)
            lo.fail(keyword.value, "PS2003", "subprocess.run cwd must be a str or Path")
    return _const_text("", "str", span)


def _run(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.run", "subprocess")
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if keyword.arg not in {"capture_output", "text", "encoding", "errors", "check", "cwd"}:
            lo.fail(node, "PS2003", f"subprocess.run keyword {keyword.arg} is not lowered")
        if keyword.arg in _RUN_BOOL and not (
            isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool)
        ):
            lo.fail(keyword.value, "PS2003", f"subprocess.run {keyword.arg} takes a True or False literal")
    argv = _argv(lo, node, span)
    capture = 1 if _capture_output(node) else 0
    check = 2 if _raise_flag(node) else 0
    text = _text_mode(lo, node, span)
    record = "CompletedProcess"
    if text is not None:
        _, enc, err = text
        record = "CompletedProcessText"
        flag_value = capture | check | 4 | (enc << 3) | (err << 5)
    else:
        flag_value = capture | check
    flag = _int(flag_value, span)
    cwd = _cwd(lo, node, span)
    return lo._abi_call(
        "subprocess",
        "run",
        [argv, flag, cwd],
        _class(record),
        "ps_sub_run",
        span,
    )


def _call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.call", "subprocess")
    lo._reject_keywords(node)
    argv = _argv(lo, node, span)
    return lo._abi_call("subprocess", "call", [argv], _t_leaf("int"), "ps_sub_call", span)


def _check_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.check_call", "subprocess")
    lo._reject_keywords(node)
    argv = _argv(lo, node, span)
    return lo._abi_call("subprocess", "check_call", [argv], _t_leaf("int"), "ps_sub_check_call", span)
