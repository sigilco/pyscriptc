"""tarfile and zipfile open lowering.

The archive surface is read-only: `tarfile.open(path, mode)` and
`zipfile.ZipFile(path)` lower onto the runtime openers (`ps_archive.c`) and
the handle rides as the `TarFile`/`ZipFile` pointer class. The read modes
are str constants: `r`, `r:`, `r:gz`, `r:bz2` and `r:xz` for tarfile, `r`
for zipfile. A write or append mode is a named fence, not a runtime
failure, because CPython would create or truncate the archive the binary
never writes.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_text, _node_type_kind, _node_value

# The read modes: the runtime takes the mode text after the colon, so the
# mode rides as written (CPython's `r:gz` and the bare `r` alias).
TAR_READ_MODES = ("r", "r:", "r:gz", "r:bz2", "r:xz")


def _mode_arg(lo: Any, node: ast.Call, mode_node: ast.expr | None, what: str) -> str:
    """The mode: a str constant from the read set."""
    if mode_node is None:
        return "r"
    arg = lo._expr(mode_node)
    if arg["kind"] == "const" and _node_type_kind(arg) == "str":
        mode = str(_node_value(arg))
        if mode in TAR_READ_MODES:
            return mode
        if mode[0] in {"w", "a", "x"}:
            lo.fail(node, "PS2001", f"{what} mode '{mode}' is not lowered: the archive surface is read-only")
        lo.fail(node, "PS2001", f"{what} mode is one of r, r:, r:gz, r:bz2, r:xz")
    lo.fail(node, "PS1003", f"{what} mode is a string literal")
    raise AssertionError("unreachable")


def _tarfile_open(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`tarfile.open(path, mode)`; `tarfile.TarFile(path)` is the same call."""
    if attr not in {"open", "TarFile"}:
        lo.fail(node, "PS2001", f"tarfile.{attr} is not in the lowered subset of tarfile")
    slots = lo._place_nodes(node, ["name", "mode"])
    if slots[1] is None and attr == "TarFile":
        for kw in node.keywords:
            if kw.arg == "mode":
                slots[1] = kw.value
    mode = _mode_arg(lo, node, slots[1], f"tarfile.{attr}")
    if slots[0] is None:
        lo.fail(node, "PS2003", f"tarfile.{attr} expects a path argument")
    assert slots[0] is not None
    path = lo._expr(slots[0])
    if _node_type_kind(path) != "str":
        lo.fail(node, "PS1003", f"tarfile.{attr} path is str")
    mode_text = _const_text(mode, "str", span)
    return lo._abi_call(
        "tarfile", "open", [path, mode_text], {"kind": "class", "name": "TarFile"}, "ps_tarfile_open", span
    )


def _zipfile_open(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`zipfile.ZipFile(path)`: the read handle. The mode keyword is spelled
    only as the read mode; every other value fences."""
    if attr != "ZipFile":
        lo.fail(node, "PS2001", f"zipfile.{attr} is not in the lowered subset of zipfile")
    slots = lo._place_nodes(node, ["file", "mode"])
    if slots[0] is None:
        lo.fail(node, "PS2003", "zipfile.ZipFile expects a path argument")
    assert slots[0] is not None
    path = lo._expr(slots[0])
    if _node_type_kind(path) != "str":
        lo.fail(node, "PS1003", "zipfile.ZipFile path is str")
    if slots[1] is not None:
        arg = slots[1]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) or arg.value != "r":
            lo.fail(
                node,
                "PS2001",
                "zipfile.ZipFile mode 'r' is the only lowered mode: the archive surface is read-only",
            )
    return lo._abi_call("zipfile", "ZipFile", [path], {"kind": "class", "name": "ZipFile"}, "ps_zipfile_open", span)
