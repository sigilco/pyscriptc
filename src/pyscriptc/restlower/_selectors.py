"""selectors: the closed DefaultSelector subset over the #177 poller (issue #485).

Every selector is an opaque pointer to the runtime record (`ps_select.c`);
every key is the `SelectorKey` class record ({ i64 fd, i64 events }) the
runtime allocates. The surface is the int-fd form: CPython's
`register(fileobj, events)` reads the fd off the object, and the typed
`socket` handle is #229, so until it lands the subset takes the bare fd and
the `fileobj` spelling fences (divergence 29 names the fence and the issue).

`select(timeout)` returns `list[SelectorKey]` in registration order, the way
`DefaultSelector.select` returns the ready keys; the `data` attribute and
the `fileobj` attribute of CPython's key stay fenced with the same note.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.restlower._shared import _class

_SELECTOR = _class("DefaultSelector")
_KEY = _class("SelectorKey")
_INT = {"kind": "int"}
_NONE = {"kind": "none"}
_KEY_LIST: dict[str, Any] = {"kind": "list", "elem": _KEY}


def _selectors_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`selectors.DefaultSelector()` and the module constants."""
    if attr != "DefaultSelector":
        lo.fail(node, "PS2001", f"selectors.{attr} is not in the lowered subset of selectors")
        raise AssertionError("unreachable")
    lo._reject_keywords(node)
    if node.args:
        lo.fail(node, "PS2003", "selectors.DefaultSelector expects 0 argument(s)")
    return lo._abi_call("selectors", "DefaultSelector", [], _SELECTOR, "ps_select_new", span)


def _no_args(lo: Any, node: ast.Call, what: str) -> None:
    if node.args:
        lo.fail(node, "PS2003", f"{what} expects 0 argument(s)")


def _selector_method(lo: Any, node: ast.Call, span: dict[str, Any], receiver: dict[str, Any]) -> dict[str, Any]:
    """Method dispatch for DefaultSelector receivers."""
    assert isinstance(node.func, ast.Attribute)
    attr = node.func.attr

    def call(typ: dict[str, Any], symbol: str, args: list[dict[str, Any]], what: str) -> dict[str, Any]:
        return lo._abi_call("selectors", what, [receiver, *args], typ, symbol, span)

    def fd_arg(position: int, what: str) -> dict[str, Any]:
        arg = lo._expr(node.args[position])
        if arg["type"]["kind"] != "int":
            lo.fail(
                node.args[position],
                "PS1003",
                f"DefaultSelector.{what} takes an int fd, not a fileobj: the typed socket handle is #229",
            )
        return arg

    if attr == "register":
        lo._reject_keywords(node)
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "DefaultSelector.register expects 2 argument(s)")
        events = lo._expr(node.args[1])
        if events["type"]["kind"] != "int":
            lo.fail(node.args[1], "PS1003", "DefaultSelector.register events is an int mask")
        return call(_KEY, "ps_select_register", [fd_arg(0, "register"), events], "register")
    if attr == "unregister":
        lo._reject_keywords(node)
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "DefaultSelector.unregister expects 1 argument(s)")
        return call(_KEY, "ps_select_unregister", [fd_arg(0, "unregister")], "unregister")
    if attr == "modify":
        lo._reject_keywords(node)
        if len(node.args) != 2:
            lo.fail(node, "PS2003", "DefaultSelector.modify expects 2 argument(s)")
        events = lo._expr(node.args[1])
        if events["type"]["kind"] != "int":
            lo.fail(node.args[1], "PS1003", "DefaultSelector.modify events is an int mask")
        return call(_KEY, "ps_select_modify", [fd_arg(0, "modify"), events], "modify")
    if attr == "select":
        lo._reject_keywords(node)
        if len(node.args) > 1:
            lo.fail(node, "PS2003", "DefaultSelector.select expects at most 1 argument(s)")
        # None blocks, a number is seconds, nothing is None. The runtime reads
        # a negative double as "block", so None lowers to -1.0.
        timeout: dict[str, Any]
        if node.args:
            arg = lo._expr(node.args[0])
            if arg["type"]["kind"] == "int":
                arg = {"kind": "cast", "op": "int_to_float", "value": arg, "type": {"kind": "float"}, "span": span}
            if arg["type"]["kind"] == "none":
                arg = {"kind": "const", "value": "-1.0", "type": {"kind": "float"}, "span": span}
            if arg["type"]["kind"] != "float":
                lo.fail(node.args[0], "PS1003", "DefaultSelector.select timeout is a number or None")
            timeout = arg
        else:
            timeout = {"kind": "const", "value": "-1.0", "type": {"kind": "float"}, "span": span}
        return call(_KEY_LIST, "ps_select_select", [timeout], "select")
    if attr == "get_key":
        lo._reject_keywords(node)
        if len(node.args) != 1:
            lo.fail(node, "PS2003", "DefaultSelector.get_key expects 1 argument(s)")
        # The tagged `SelectorKey | None`: a bool probe, then the make call
        # fills the cell only when the probe hit, the shape re.search lowers.
        fd = fd_arg(0, "get_key")
        opt_t = lo._opt_type(_KEY, node)
        res = lo._opt_box(None, opt_t, span)
        dest = res["id"]
        hit = lo._abi_call("selectors", "get_key.hit", [receiver, fd], {"kind": "bool"}, "ps_select_get_key_hit", span)
        made = lo._abi_call("selectors", "get_key.make", [receiver, fd], _KEY, "ps_select_get_key", span)
        lo.prelude.append(
            {
                "kind": "if",
                "cond": hit,
                "then": [
                    {
                        "kind": "setattr",
                        "object": {"kind": "name", "id": dest, "type": opt_t, "span": span},
                        "name": "_tag",
                        "value": {"kind": "const", "value": True, "type": {"kind": "bool"}, "span": span},
                        "span": span,
                    },
                    {
                        "kind": "setattr",
                        "object": {"kind": "name", "id": dest, "type": opt_t, "span": span},
                        "name": "_val",
                        "value": made,
                        "span": span,
                    },
                ],
                "else": [],
                "span": span,
            }
        )
        return res
    if attr == "close":
        lo._reject_keywords(node)
        _no_args(lo, node, "DefaultSelector.close")
        return call(_NONE, "ps_select_close", [], "close")
    lo.fail(node, "PS2001", f"DefaultSelector.{attr} is not lowered")
    raise AssertionError("unreachable")
