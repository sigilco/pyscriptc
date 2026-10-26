"""os and os.path call lowering.

The `os` and `posixpath` rows in `stdmore.REST_FIXED` cover the fixed-shape
calls. This module holds the ones whose arity or keyword set needs a
handler: `relpath` takes an optional `start`, and the `os.path` spellings
that take a `Path` need the string form.
"""

from __future__ import annotations

import ast
from typing import Any


def _relpath(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`posixpath.relpath(path, start=None)`.

    One argument uses the process cwd, two use the given start. The runtime
    takes both as strings, with an empty start meaning "cwd".
    """
    lo._reject_keywords(node)
    if len(node.args) not in {1, 2}:
        lo.fail(node, "PS2003", "posixpath.relpath expects 1 or 2 argument(s)")
    args = []
    for arg_node in node.args:
        arg = lo._expr(arg_node)
        if arg["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "posixpath.relpath arguments are str")
        args.append(arg)
    if len(args) == 1:
        args.append({"kind": "const", "value": "", "type": {"kind": "str"}, "span": span})
    return lo._abi_call("posixpath", "relpath", args, {"kind": "str"}, "ps_path_relpath", span)


def _os_open(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`os.open(path, flags, mode=0o777)`: mode only matters with O_CREAT."""
    lo.mobile_guard(node, "os.open", "osfd")
    lo._reject_keywords(node)
    if len(node.args) not in {2, 3}:
        lo.fail(node, "PS2003", "os.open expects 2 or 3 argument(s)")
    args = []
    path = lo._expr(node.args[0])
    if path["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "os.open takes a str path")
    args.append(path)
    for arg_node in node.args[1:]:
        arg = lo._expr(arg_node)
        if arg["type"]["kind"] != "int":
            lo.fail(node, "PS1003", "os.open flags and mode are int")
        args.append(arg)
    if len(args) == 2:
        args.append({"kind": "const", "value": "511", "type": {"kind": "int"}, "span": span})
    return lo._abi_call("os", "open", args, {"kind": "int"}, "ps_os_open", span)


def _walk(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`os.walk(top)` as a materialized list of the (dirpath, dirnames,
    filenames) triples (issue #509).

    The runtime pre-computes every row in CPython's topdown pre-order, so a
    loop body that edits `dirnames` does not prune the walk (divergence 47)
    and `topdown`, `onerror` and `followlinks` stay fenced. The result is a
    `list` of the synthetic 3-tuple class, so `for root, dirs, files in
    os.walk(top)` lowers through the same unpack the dict.items loop uses.
    """
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "os.walk expects 1 argument(s)")
    top = lo._expr(node.args[0])
    if top["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "os.walk top is str")
    ls = {"kind": "list", "elem": {"kind": "str"}}
    elem = lo._tuple_type([{"kind": "str"}, ls, ls], node)
    return lo._abi_call("os", "walk", [top], {"kind": "list", "elem": elem}, "ps_os_walk", span)


def _os_read(lo: Any, node: ast.Call, span: dict[str, Any]) -> dict[str, Any]:
    """`os.read(fd, n)`: n bytes into a fresh buffer."""
    lo.mobile_guard(node, "os.read", "osfd")
    lo._reject_keywords(node)
    if len(node.args) != 2:
        lo.fail(node, "PS2003", "os.read expects 2 argument(s)")
    fd = lo._expr(node.args[0])
    size = lo._expr(node.args[1])
    for arg, what in ((fd, "fd"), (size, "size")):
        if arg["type"]["kind"] != "int":
            lo.fail(node, "PS1003", f"os.read {what} is int")
    return lo._abi_call("os", "read", [fd, size], {"kind": "bytes"}, "ps_os_read", span)
