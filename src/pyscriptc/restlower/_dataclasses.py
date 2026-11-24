# SPDX-License-Identifier: Apache-2.0
"""`dataclasses.field` (issue #1279, #444).

`@dataclass` is the closed dataclass form; `field` is its one lowered
companion. `default_factory=list|dict|set` names the per-instance default:
every construction that omits the field gets a fresh container instead of
one shared object (CPython raises ValueError for a plain mutable default,
so the factory spelling is the only mutable one supported source can
carry). A callable factory (`default_factory=make` or a lambda) lowers to
the callable value and is called per construction.

`default=<constant>` (issue #444) is the spelled-out form of the bare
assignment default: the construction site that omits the argument plays
the constant, exactly the rule the equivalent `x: T = const` spelling
follows. `default=None` on an `Optional` field rides the same marker and
the construction site boxes the None. Other keyword names (`repr=`,
`compare=`, `kw_only=`, `init=`, `metadata=`, `hash=`) keep the fence.

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

#: The keyword that names the field in the dataclass metadata and not in
#: the class's own value semantics (issue #444). CPython stores the
#: mapping for the serializer libraries to read; the instance, the repr,
#: the compare and the constructor never see it, so the lowered subset
#: accepts and drops it.
IGNORED_KEYWORDS = frozenset({"metadata"})


def _dataclass_field(
    lo: Any, node: ast.Call, span: dict[str, Any], expect: dict[str, Any] | None, attr: str
) -> dict[str, Any]:
    """The `field(default_factory=...)` / `field(default=...)` marker record.

    `expect` is the annotation's field type when the assignment carries
    one. The factory must leave a fresh value per construction, so the
    marker stores the factory and the field type.

    A container factory (`list`, `dict`, `set`) rides by name: the
    construction site builds the empty container of the field's type. A
    callable factory rides as its AST node: the class-body collect pass
    runs before the module's function signatures are known, so the
    callable value is lowered in the class body pass, where the module
    functions are in scope.

    A `default=<constant>` (issue #444) rides the same marker with the
    constant AST stashed: the class body pass folds it against the
    annotation, the same rule the equivalent `x: T = const` spelling
    follows.
    """
    if node.args:
        lo.fail(node, "PS2002", "field takes keyword arguments only")
    for kw in node.keywords:
        if kw.arg is None:
            lo.fail(kw, "PS2002", "**kwargs are not lowered")
        if kw.arg in IGNORED_KEYWORDS:
            # `metadata=` names the field in the dataclass metadata, not
            # in the class's own value semantics: CPython stores the
            # mapping for the serializer libraries to read, and the
            # instance, the repr, the compare and the constructor never
            # see it. The lowered subset has no metadata surface, so the
            # keyword is accepted and dropped.
            continue
        if kw.arg not in {"default_factory", "default"}:
            lo.fail(kw, "PS2002", f"field({kw.arg}=...) is not lowered; use default or default_factory")
    default_kw = next((kw for kw in node.keywords if kw.arg == "default"), None)
    factory_kw = next((kw for kw in node.keywords if kw.arg == "default_factory"), None)
    if default_kw is not None and factory_kw is not None:
        lo.fail(node, "PS2002", "field takes default or default_factory, not both")
    if factory_kw is None and default_kw is None:
        lo.fail(node, "PS2002", "field lowers default or default_factory")
    if factory_kw is None:
        # `field(default=...)`: the constant form of the bare assignment
        # default (issue #444). The marker defers to the class body pass,
        # which folds the AST against the field's annotation.
        assert default_kw is not None
        return {"kind": "field-const-default", "default_ast": default_kw.value, "type": expect or {}}
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
