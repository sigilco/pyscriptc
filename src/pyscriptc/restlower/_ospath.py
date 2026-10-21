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
