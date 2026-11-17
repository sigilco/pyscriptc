# SPDX-License-Identifier: Apache-2.0
"""`dataclasses.field` (issue #1279).

`@dataclass` is the closed dataclass form; `field` is its one lowered
companion. `default_factory=list|dict|set` names the per-instance default:
every construction that omits the field gets a fresh container instead of
one shared object (CPython raises ValueError for a plain mutable default,
so the factory spelling is the only mutable one supported source can
carry). A callable factory (`default_factory=make` or a lambda) lowers to
the callable value and is called per construction.

The handler returns the `factory-default` marker record: it is not an IR
node, the construction site that omits the argument replays the factory,
the same rule the tuple-default marker follows.
"""

from __future__ import annotations

import ast
from typing import Any

#: The container factories the marker names directly: the construction
#: site builds the empty container of the field's type, so no callable
#: value is lowered.
CONTAINER_FACTORIES = frozenset({"list", "dict", "set"})


def _dataclass_field(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """The `field(default_factory=...)` marker record.

    `expect` is the annotation's field type when the assignment carries
    one. The factory must leave a fresh value per construction, so the
    marker stores the factory and the field type.

    A container factory (`list`, `dict`, `set`) rides by name: the
    construction site builds the empty container of the field's type. A
    callable factory rides as its AST node: the class-body collect pass
    runs before the module's function signatures are known, so the
    callable value is lowered in the class body pass, where the module
    functions are in scope.
    """
    if node.args:
        lo.fail(node, "PS2002", "field takes keyword arguments only")
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(kw, "PS2002", "**kwargs are not lowered")
        if kw.arg != "default_factory":
            lo.fail(kw, "PS2002", f"field({kw.arg}=...) is not lowered; use default_factory")
    factory_kw = next((kw for kw in node.keywords if kw.arg == "default_factory"), None)
    if factory_kw is None:
        lo.fail(node, "PS2002", "field lowers default_factory only; a plain default is spelled without field")
    factory = factory_kw.value
    if isinstance(factory, ast.Name) and factory.id in CONTAINER_FACTORIES:
        # The container factories ride by name: the construction site
        # builds the empty container of the field's type.
        return {"kind": "factory-default", "factory": None, "name": factory.id, "type": expect or {}}
    return {"kind": "factory-default", "factory": None, "name": None, "factory_ast": factory, "type": expect or {}}


def container_factory(marker: dict[str, Any]) -> str | None:
    """The container factory name (`list`, `dict`, `set`), or None when the
    marker holds a callable factory."""

    if marker.get("factory") is not None:
        return None
    name = marker.get("name")
    if not isinstance(name, str):
        return None
    return name


def factory_value(marker: dict[str, Any]) -> dict[str, Any] | None:
    """The lowered callable value the marker holds, or None when the
    marker names a container factory or still carries the AST node."""

    value = marker.get("factory")
    if value is None:
        return None
    assert isinstance(value, dict)
    return value


def factory_ast(marker: dict[str, Any]) -> ast.expr | None:
    """The factory AST node the marker carries, or None when the marker
    names a container factory."""

    node = marker.get("factory_ast")
    if node is None:
        return None
    assert isinstance(node, ast.expr)
    return node


def marker_type(marker: dict[str, Any]) -> dict[str, Any]:
    """The field type the marker carries."""

    typ: dict[str, Any] = marker["type"]
    return typ
