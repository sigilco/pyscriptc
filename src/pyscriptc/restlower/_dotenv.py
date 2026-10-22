"""dotenv call lowering.

`dotenv_values(path)` is `json.loads(ps_dotenv_values(path))`: the runtime
parses the .env document and re-emits it as JSON, so the shape rules are the
json loads ones and the annotation is required (PS1001). The closed subset
of parsing rules lives in the header of `runtime/ps_dotenv.c` and in
divergence 16 of `docs/reference/divergences.md`.

`load_dotenv(path)` lowers to `ps_dotenv_load(path) -> bool`: the same
parse, then every key not already in the process environment is set, and
the result says whether any binding was seen. python-dotenv's
`override=True`, `stream=`, `verbose=` and `encoding=` arguments are outside
the subset and fenced.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _show
from pyscriptc.lower._stdlib_shared import _mod_call

_SUBSET = ("dotenv_values", "load_dotenv")


def _dotenv_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    if attr not in _SUBSET:
        lo.fail(node, "PS2001", f"dotenv.{attr} is not in the lowered subset of dotenv")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", f"dotenv.{attr} expects 1 argument(s)")
    if attr == "dotenv_values" and expect is None:
        lo.fail(node, "PS1001", "dotenv.dotenv_values needs the result annotation")
    text = lo._expr(node.args[0])
    if text["type"]["kind"] != "str":
        lo.fail(node, "PS1003", f"dotenv.{attr} argument is " + _show(text["type"]) + ", expected str")
    if attr == "load_dotenv":
        if expect is not None and expect["kind"] != "bool":
            lo.fail(node, "PS1003", "load_dotenv returns bool")
        typ = expect or {"kind": "bool"}
        return _mod_call("dotenv", "load_dotenv", [text], typ, span) | {"symbol": "ps_dotenv_load"}
    lo._json_type(node, expect, "dotenv.dotenv_values")
    doc = _mod_call("dotenv", "dotenv_values", [text], {"kind": "str"}, span)
    doc["symbol"] = "ps_dotenv_values"
    return _mod_call("json", "loads", [doc], expect, span)
