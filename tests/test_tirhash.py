"""The typed IR hasher (recorded decision #830, P2).

`pyscriptc.tirhash` walks the typed records in schema field order and
owns the four canonicalization rules `canonical.py` used to own: the
incidental-key strip, the class order, the handler `codes` order, the
fence total order and the environment path scrub. These tests pin the
ownership, the field order and the byte contract.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from pyscriptc import tirhash, tirserde
from pyscriptc._irnode import (
    Handler,
    Span,
    assign,
    const_node,
    name_expr,
    return_stmt,
    t_leaf,
)


def _module(**overrides: Any) -> dict[str, Any]:
    """A minimal module record the tests mutate."""
    module: dict[str, Any] = {
        "irVersion": 2,
        "subset": "gates-v1",
        "entry": "f",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    module.update(overrides)
    return module


def _tree(module: Any) -> Any:
    """The canonical tree the typed payload spells."""
    return json.loads(tirhash.payload_bytes_node(module))


def _span(line: int = 1, col: int = 1) -> Span:
    return Span("m.py", line, col, line, col + 1)


def test_the_node_id_is_spelled_first_then_the_schema_field_order() -> None:
    """The node id leads, the schema field order follows."""
    module = _module(
        functions=[
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "int"},
                "locals": [],
                "body": [
                    assign(
                        "x",
                        const_node(0, "1", t_leaf("int"), _span()),
                        _span(),
                    )
                ],
            }
        ]
    )
    body = _tree(module)["ir"]["functions"][0]["body"]
    assert list(body[0].keys()) == ["kind", "target", "value"]
    assert body[0]["kind"] == "assign"
    # The value node is a const: the id first, then the value and the type.
    assert list(body[0]["value"].keys()) == ["kind", "value", "type"]


def test_the_spans_never_reach_the_payload() -> None:
    """The incidental keys are gone: the typed walker has no place for them."""
    module = _module(
        functions=[
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "int"},
                "locals": [],
                "span": {"file": "m.py", "line": 1, "col": 1, "endLine": 1, "endCol": 9},
                "body": [return_stmt(name_expr("x", t_leaf("int"), _span()), _span())],
            }
        ]
    )
    payload = tirhash.payload_bytes_node(module).decode("utf-8")
    assert "span" not in payload
    assert "sourceFile" not in payload
    assert "cpython" not in payload


def test_the_class_order_is_owned_by_the_hasher() -> None:
    """The synthetic-class set leak is closed by the class sort."""
    module = _module(
        classes=[
            {"name": "Zeta", "fields": [], "methods": []},
            {"name": "Alpha", "fields": [], "methods": []},
        ]
    )
    names = [cls["name"] for cls in _tree(module)["ir"]["classes"]]
    assert names == ["Alpha", "Zeta"]


def test_the_handler_codes_order_is_owned_by_the_hasher() -> None:
    """The handler `codes` leak is closed by the codes sort."""
    handler = Handler(["ValueError"], [30, 10, 20], [], "", "", 0)
    module = _module(
        functions=[
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "int"},
                "locals": [],
                "body": [{"kind": "try", "body": [], "handlers": [handler]}],
            }
        ]
    )
    codes = _tree(module)["ir"]["functions"][0]["body"][0]["handlers"][0]["codes"]
    assert codes == [10, 20, 30]


def test_the_fence_total_order_is_owned_by_the_hasher() -> None:
    """The fences are totally ordered by code, message, function, position."""
    module = _module(
        fences=[
            {"code": "PS1001", "message": "m", "function": None, "span": {"file": "a", "line": 5, "col": 1}},
            {"code": "PS1001", "message": "m", "function": None, "span": {"file": "a", "line": 2, "col": 1}},
            {"code": "AA0001", "message": "z", "function": "g", "span": {"file": "a", "line": 9, "col": 9}},
        ]
    )
    fences = _tree(module)["ir"]["fences"]
    assert [fence["code"] for fence in fences] == ["AA0001", "PS1001", "PS1001"]
    # The position tiebreaker is invisible in the payload: the span is gone.
    assert "span" not in fences[1]


def test_the_path_scrub_is_owned_by_the_hasher() -> None:
    """The environment absolute paths become `<PATH>`."""
    scrubbed = tirhash._scrub_paths(f"prefix {os.getcwd()} suffix")
    assert "<PATH>" in scrubbed
    assert os.getcwd() not in scrubbed
    assert tirhash._scrub_paths(sys.executable) == "<PATH>"


def test_the_typed_payload_does_not_go_through_the_dict_path() -> None:
    """The typed hasher never calls `node_to_dict` nor `canonicalize`."""
    calls: list[str] = []
    original = tirserde.node_to_dict

    def spy(value: Any) -> Any:
        calls.append("node_to_dict")
        return original(value)

    tirserde.node_to_dict = spy  # type: ignore[method-assign]
    try:
        module = _module(
            functions=[
                {
                    "name": "f",
                    "params": [],
                    "returnType": {"kind": "int"},
                    "locals": [],
                    "body": [return_stmt(name_expr("x", t_leaf("int"), _span()), _span())],
                }
            ]
        )
        tirhash.hash_module_node(module)
    finally:
        tirserde.node_to_dict = original  # type: ignore[method-assign]
    assert calls == []


def test_the_typed_hash_is_stable_across_calls() -> None:
    """The same typed module hashes to the same digest."""
    module = _module(
        functions=[
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "int"},
                "locals": [],
                "body": [return_stmt(name_expr("x", t_leaf("int"), _span()), _span())],
            }
        ]
    )
    assert tirhash.hash_module_node(module) == tirhash.hash_module_node(module)
    assert len(tirhash.hash_module_node(module)) == 64


def test_tirserde_exposes_the_typed_hasher() -> None:
    """`tirserde.hash_module_node` is the typed hasher now."""
    module = _module()
    assert tirserde.hash_module_node(module) == tirhash.hash_module_node(module)
    assert tirserde.payload_bytes_node(module) == tirhash.payload_bytes_node(module)
