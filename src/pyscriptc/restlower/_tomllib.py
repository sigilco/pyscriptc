"""tomllib call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _show
from pyscriptc.lower._stdlib_shared import _mod_call


def _tomllib_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`tomllib.loads(text)` is `json.loads(ps_toml_to_json(text))`: the TOML
    document is re-emitted as JSON in C and read into the annotated type, so
    the shape rules are json.loads' own. `tomllib.load` takes a binary file
    object and stays fenced."""
    if attr != "loads":
        lo.fail(node, "PS2001", f"tomllib.{attr} is not in the lowered subset of tomllib")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "tomllib.loads expects 1 argument(s)")
    if expect is None:
        lo.fail(node, "PS1001", "tomllib.loads needs the result annotation")
    lo._json_type(node, expect, "tomllib.loads")
    text = lo._expr(node.args[0])
    if text["type"]["kind"] != "str":
        lo.fail(node, "PS1003", "tomllib.loads argument is " + _show(text["type"]) + ", expected str")
    doc = _mod_call("tomllib", "loads", [text], {"kind": "str"}, span)
    doc["symbol"] = "ps_toml_to_json"
    return _mod_call("json", "loads", [doc], expect, span)
