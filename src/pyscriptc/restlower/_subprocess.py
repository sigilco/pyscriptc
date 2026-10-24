"""subprocess call lowering.

`subprocess.run(cmd)` returns the CompletedProcess record, `subprocess.call`
the exit code, and `subprocess.check_call` the exit code with a non-zero
code raising RuntimeError. The command is a `list[str]`, the shape scripts
use. `capture_output=True` captures stdout and stderr into the record;
`text=True` is accepted and ignored, because the captured bytes are read
as bytes like the island `check_output` spelling does.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.reflection import kwargs_call_note


def _argv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """The command list: `list[str]` with one element type."""
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "subprocess expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "list" or arg["type"].get("set"):
        lo.fail(node, "PS1003", "subprocess takes a list of str")
    if arg["type"]["elem"]["kind"] != "str":
        lo.fail(node, "PS1003", "subprocess takes a list of str")
    return arg


def _capture_output(node: ast.Call) -> bool:
    """`capture_output=True` is the only keyword this subset reads."""
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        if keyword.arg == "capture_output" and isinstance(keyword.value, ast.Constant):
            if keyword.value.value is True:
                return True
    return False


def _run(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.run", "subprocess")
    for keyword in node.keywords:
        if keyword.arg is None:
            lo.fail(node, "PS2002", kwargs_call_note())
        if keyword.arg not in {"capture_output", "text", "encoding", "errors", "check"}:
            lo.fail(node, "PS2003", f"subprocess.run keyword {keyword.arg} is not lowered")
    argv = _argv(lo, node, span)
    capture = 1 if _capture_output(node) else 0
    flag = {"kind": "const", "value": str(capture), "type": {"kind": "int"}, "span": span}
    return lo._abi_call(
        "subprocess",
        "run",
        [argv, flag],
        {"kind": "class", "name": "CompletedProcess"},
        "ps_sub_run",
        span,
    )


def _call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.call", "subprocess")
    lo._reject_keywords(node)
    argv = _argv(lo, node, span)
    return lo._abi_call("subprocess", "call", [argv], {"kind": "int"}, "ps_sub_call", span)


def _check_call(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo.mobile_guard(node, "subprocess.check_call", "subprocess")
    lo._reject_keywords(node)
    argv = _argv(lo, node, span)
    return lo._abi_call("subprocess", "check_call", [argv], {"kind": "int"}, "ps_sub_check_call", span)
