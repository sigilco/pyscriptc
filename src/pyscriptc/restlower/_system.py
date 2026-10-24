"""sys, os, atexit, importlib.resources and unicodedata lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int, _const_text


def _getenv(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    lo._reject_keywords(node)
    args = [lo._expr(arg) for arg in node.args]
    for arg in args:
        if arg["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "os.getenv takes str arguments")
    if len(args) == 1:
        return lo._opt_env_get(node, args[0], span)
    if len(args) == 2:
        return lo._abi_call("os", "getenv", args, {"kind": "str"}, "ps_os_getenv_or", span)
    lo.fail(node, "PS2003", "os.getenv expects 1 or 2 argument(s)")
    raise AssertionError("unreachable")


def _exit(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "sys.exit expects 1 argument(s)")
    arg = lo._expr(node.args[0])
    if arg["type"]["kind"] != "int":
        lo.fail(node, "PS1003", "sys.exit argument is an int")
    return lo._abi_call("sys", "exit", [arg], {"kind": "none"}, "exit", span)


def _unicode_name(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if not 1 <= len(node.args) <= 2:
        lo.fail(node, "PS2003", "unicodedata.name expects 1 or 2 argument(s)")
    char = lo._expr(node.args[0])
    if char["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "unicodedata.name argument is str")
    if len(node.args) == 2:
        default = lo._expr(node.args[1])
        if default["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "unicodedata.name default is str")
        has = _const_int(1, span)
    else:
        default = _const_text("", "str", span)
        has = _const_int(0, span)
    return lo._abi_call("unicodedata", "name", [char, has, default], {"kind": "str"}, "ps_unicode_name", span)


def _resource_search() -> str:
    import os
    import sys

    from pyscriptc.stdlib_queue import resource_root

    found: list[str] = []
    seen: set[str] = set()
    # The staged data-file root beside the emitted binary (issue #410) goes
    # first, so a dependency package's staged files win over any same-named
    # host package. Skipped when empty: a compile that stages nothing keeps
    # the host-only path exactly as before.
    staged = resource_root()
    if staged:
        found.append(staged)
        seen.add(staged)
    for entry in sys.path:
        if not entry:
            entry = os.getcwd()
        if not os.path.isdir(entry):
            continue
        path = os.path.abspath(entry)
        if path in seen:
            continue
        seen.add(path)
        found.append(path)
    return "\n".join(found)


def _as_file_value(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """as_file of a directory resource is that path. __enter__ returns it; __exit__ is a no-op."""

    if len(node.args) != 1 or node.keywords:
        lo.fail(node, "PS2003", "as_file expects 1 argument(s)")
    opened = lo._expr(node.args[0])
    if opened["type"]["kind"] != "class" or opened["type"].get("name") != "Path":
        lo.fail(node, "PS1003", "as_file takes a resource path")
    return opened


def _resource_files(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "importlib.resources.files expects 1 argument(s)")
    package = lo._expr(node.args[0])
    if package["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "importlib.resources.files package is str")
    search = _const_text(_resource_search(), "str", span)
    return lo._abi_call(
        "importlib.resources",
        "files",
        [package, search],
        {"kind": "class", "name": "Path"},
        "ps_res_files",
        span,
    )


def _atexit_register(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Name):
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    name = node.args[0].id
    sig = lo.sigs.get(name)
    if sig is None:
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    params, _ret = sig
    if params:
        lo.fail(node, "PS2001", "atexit.register takes a no-argument function")
    target = _const_text(lo._ir_name(name), "str", span)
    return lo._abi_call("atexit", "register", [target], {"kind": "none"}, "", span)
