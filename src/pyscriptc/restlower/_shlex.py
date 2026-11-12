"""shlex call lowering.

The closed subset is `split`, `quote` and `join`. The runtime
(`ps_shlex.c`) lexes the text the way CPython's POSIX-mode `shlex.split`
does, and quotes the way CPython's `shlex.quote` does.

`split(text)` takes the text and an optional constant `comments` flag; it
returns the list of words. `quote(word)` returns the shell spelling of one
word and `join(words)` the command line that spells a list of words.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _const_int

_LIST_STR = {"kind": "list", "elem": {"kind": "str"}}


def _shlex_call(lo: Any, node: ast.Call, span: dict[str, Any], attr: str) -> dict[str, Any]:
    """`shlex.split(text)`, `shlex.quote(word)` and `shlex.join(words)`."""
    if attr == "split":
        slots = lo._place_nodes(node, ["s", "comments"])
        if slots[0] is None:
            lo.fail(node, "PS2003", "shlex.split expects a text argument")
        assert slots[0] is not None
        text = lo._expr(slots[0])
        if text["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "shlex.split text is str")
        # The comments flag is folded at lowering time because the runtime
        # takes it as an int the way the lock's blocking flag does.
        flag = 0
        if slots[1] is not None:
            arg = lo._expr(slots[1])
            if arg["kind"] == "const" and arg["type"]["kind"] == "bool":
                flag = 1 if arg["value"] else 0
            elif arg["kind"] == "const" and arg["type"]["kind"] == "int":
                flag = 1 if int(arg["value"]) != 0 else 0
            else:
                lo.fail(slots[1], "PS1003", "shlex.split comments is a constant bool")
        return lo._abi_call("shlex", "split", [text, _const_int(flag, span)], _LIST_STR, "ps_shlex_split", span)
    if attr == "quote":
        slots = lo._place_nodes(node, ["s"])
        if slots[0] is None:
            lo.fail(node, "PS2003", "shlex.quote expects a text argument")
        assert slots[0] is not None
        word = lo._expr(slots[0])
        if word["type"]["kind"] != "str":
            lo.fail(node, "PS1003", "shlex.quote argument is str")
        return lo._abi_call("shlex", "quote", [word], {"kind": "str"}, "ps_shlex_quote", span)
    if attr == "join":
        slots = lo._place_nodes(node, ["split_command"])
        if slots[0] is None:
            lo.fail(node, "PS2003", "shlex.join expects a list argument")
        assert slots[0] is not None
        words = lo._expr(slots[0])
        if words["type"]["kind"] != "list":
            lo.fail(node, "PS1003", "shlex.join argument is a list of str")
        elem = words["type"].get("elem") or {}
        if elem.get("kind") != "str":
            lo.fail(node, "PS1003", "shlex.join argument is a list of str")
        return lo._abi_call("shlex", "join", [words], {"kind": "str"}, "ps_shlex_join", span)
    lo.fail(node, "PS2001", f"shlex.{attr} is not in the lowered subset of shlex")
    raise AssertionError("unreachable")
