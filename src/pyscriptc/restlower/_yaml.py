"""yaml.safe_load call lowering."""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.lower._core import _show
from pyscriptc.lower._stdlib_shared import _mod_call
from pyscriptc.restlower._shared import _node_type, _node_type_kind, _t_leaf


def _yaml_call(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """`yaml.safe_load(text)` is `json.loads(ps_yaml_to_json(text))`: the YAML
    document is re-emitted as JSON in C and read into the annotated type, so
    the shape rules are json.loads' own. The closed subset is documented in
    the header of `runtime/ps_yaml.c`; everything else stays fenced."""
    if attr != "safe_load":
        lo.fail(node, "PS2001", f"yaml.{attr} is not in the lowered subset of yaml")
    lo._reject_keywords(node)
    if len(node.args) != 1:
        lo.fail(node, "PS2003", "yaml.safe_load expects 1 argument(s)")
    if expect is None:
        lo.fail(node, "PS1001", "yaml.safe_load needs the result annotation")
    lo._json_type(node, expect, "yaml.safe_load")
    text = lo._expr(node.args[0])
    if _node_type_kind(text) != "str":
        lo.fail(node, "PS1003", "yaml.safe_load argument is " + _show(_node_type(text)) + ", expected str")
    doc = _mod_call("yaml", "safe_load", [text], _t_leaf("str"), span)
    doc.update({"symbol": "ps_yaml_to_json"})
    return _mod_call("json", "loads", [doc], expect, span)
