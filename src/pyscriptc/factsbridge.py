"""Checker bridge: a mypy inferred-type map fed into lowering (issue #87, E3a).

The bridge is opt-in and off by default. `mypy_facts(path)` runs mypy as a
library (the same `pyscriptc.check` plugin, `follow_imports=silent`, no
incremental cache, `preserve_asts` + `export_types` that the removed
`MypyFactsAdapter` of #119 used) and extracts a plain `line/col -> TypeFacts`
map plus per-def inferred data: parameter types mypy proved (annotation or
default), the return type when every return statement agrees, and local
variable types from the assignment-target `NameExpr` in mypy's expression
TypeMap. mypy objects never cross the seam: everything is reduced to the
`typefacts` IR before it leaves this module.

Lowering consumes it through `CheckerBridge.for_path` only when the caller
asked for it (`compile --bridge mypy`, config key `bridge = "mypy"`). When
mypy is not importable the bridge reports absent and lowering proceeds
exactly as before, so the zero-dependency path is unchanged. Anything the
map cannot prove keeps its fence: the bridge only answers, it never forces
(design decision `.agents/docs/checker-bridge.md`, E3a).
"""

from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import TypeFacts

_MYPY_MIN = (2, 4)
_MYPY_MAX = (2, 5)


def _mypy_api():
    """Import mypy lazily; return (build, BuildSource, Options, version) or None."""
    try:
        from mypy import version as mypy_version
        from mypy.build import BuildSource  # noqa: F401 — re-exported for the adapter
        from mypy.build import build as mypy_build
        from mypy.options import Options
    except ImportError:
        return None
    try:
        parsed = tuple(int(part) for part in mypy_version.__version__.split(".")[:2])
    except (AttributeError, ValueError):
        return None
    if not _MYPY_MIN <= parsed < _MYPY_MAX:
        return None
    return mypy_build, BuildSource, Options, mypy_version.__version__


def mypy_available() -> bool:
    """True when a pinned mypy (>=2.4,<2.5) is importable in this interpreter."""
    return _mypy_api() is not None


def _iter_mypy_nodes(root: Any):
    """Yield every mypy AST node under `root`.

    mypyc-compiled mypy classes cannot be subclassed, so there is no
    `TraverserVisitor`; walk the generic `__mypyc_attrs__` fields instead
    (same discipline as the original MypyFactsAdapter and factsdiff).
    """
    from mypy.nodes import Node

    stack: list[Any] = [root]
    visited: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        yield node
        for attr in getattr(type(node), "__mypyc_attrs__", ()):
            value = getattr(node, attr, None)
            if isinstance(value, Node):
                stack.append(value)
            elif isinstance(value, (list, tuple)):
                stack.extend(item for item in value if isinstance(item, Node))


def _mypy_type_facts(typ: Any) -> TypeFacts:
    """Reduce one mypy `Type` to the checker-agnostic `TypeFacts` IR."""
    from mypy.types import (
        AnyType,
        CallableType,
        Instance,
        LiteralType,
        NoneType,
        Overloaded,
        TupleType,
        TypeAliasType,
        TypeType,
        TypeVarType,
        UnionType,
        get_proper_type,
    )

    proper = get_proper_type(typ)
    if isinstance(proper, Instance):
        return TypeFacts(
            kind=proper.type.name,
            args=tuple(_mypy_type_facts(arg) for arg in proper.args),
            protocols=frozenset([proper.type.fullname]) if proper.type.is_protocol else frozenset(),
            runtime_repr=str(proper),
        )
    if isinstance(proper, UnionType):
        items = proper.relevant_items()
        members = tuple(_mypy_type_facts(item) for item in items)
        optional = any(
            isinstance(get_proper_type(item), NoneType) or member.optional
            for item, member in zip(items, members, strict=True)
        )
        return TypeFacts(kind="union", optional=optional, args=members, runtime_repr=str(proper))
    if isinstance(proper, NoneType):
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    if isinstance(proper, CallableType):
        return TypeFacts(kind="callable", runtime_repr=str(proper))
    if isinstance(proper, Overloaded):
        return TypeFacts(kind="overloaded", runtime_repr=str(proper))
    if isinstance(proper, TupleType):
        return TypeFacts(
            kind="tuple",
            args=tuple(_mypy_type_facts(item) for item in proper.items),
            runtime_repr=str(proper),
        )
    if isinstance(proper, LiteralType):
        return TypeFacts(kind="literal", args=(_mypy_type_facts(proper.fallback),), runtime_repr=str(proper))
    if isinstance(proper, TypeVarType):
        return TypeFacts(kind="typevar", runtime_repr=proper.name)
    if isinstance(proper, TypeType):
        return TypeFacts(kind="type", args=(_mypy_type_facts(proper.item),), runtime_repr=str(proper))
    if isinstance(proper, TypeAliasType):
        return _mypy_type_facts(get_proper_type(proper))
    if isinstance(proper, AnyType):
        return TypeFacts(kind="any", runtime_repr=str(proper))
    return TypeFacts(kind="unknown", runtime_repr=str(proper))


@dataclass(frozen=True)
class DefFacts:
    """What mypy proved about one `def`, keyed by position (1-based line, 0-based col)."""

    name: str
    params: dict[str, TypeFacts]
    return_type: TypeFacts | None


@dataclass(frozen=True)
class BridgeFacts:
    """The plain fact map the bridge hands to lowering; no mypy objects inside."""

    path: Path
    provider: str
    expressions: dict[str, TypeFacts]
    defs: dict[str, DefFacts]

    def expression_at(self, line: int, col: int) -> TypeFacts | None:
        return self.expressions.get(f"{line}:{col}")

    def def_at(self, line: int, col: int) -> DefFacts | None:
        return self.defs.get(f"{line}:{col}")


class MypyBridge:
    """`FactsProvider`-shaped adapter that feeds mypy's inference to lowering."""

    name = "mypy"

    def facts(self, path: Path) -> BridgeFacts:
        api = _mypy_api()
        if api is None:
            raise CheckFailed(
                "the mypy bridge needs mypy>=2.4,<2.5; install the extra: "
                "pip install 'pyscriptc[oracle-mypy]'"
            )
        mypy_build, build_source, options_cls, _version = api
        from mypy.nodes import Expression, FuncDef

        path = path.resolve()
        with tempfile.TemporaryDirectory(prefix="pyscriptc-bridge-") as cache:
            options = options_cls()
            options.incremental = False
            options.follow_imports = "silent"
            options.cache_dir = cache
            options.preserve_asts = True
            options.export_types = True
            options.plugins = ["pyscriptc.check"]
            result = mypy_build(sources=[build_source(str(path), None, None)], options=options)
        if result.errors:
            raise CheckFailed("\n".join(result.errors))
        module = next((tree for tree in result.files.values() if Path(tree.path) == path), None)
        if module is None:
            raise CheckFailed(f"mypy did not produce a module for {path}")
        expressions: dict[str, TypeFacts] = {}
        for node in _iter_mypy_nodes(module):
            if not isinstance(node, Expression):
                continue
            try:
                typ = result.types.get(node)
            except TypeError:  # unhashable synthetic node
                continue
            if typ is None:
                continue
            expressions.setdefault(f"{node.line}:{node.column}", _mypy_type_facts(typ))
        defs: dict[str, DefFacts] = {}
        for node in _iter_mypy_nodes(module):
            if not isinstance(node, FuncDef):
                continue
            params: dict[str, TypeFacts] = {}
            for arg in node.arguments:
                if arg.variable.name in {"self", "cls"}:
                    continue
                if arg.variable.type is not None:
                    facts = _mypy_type_facts(arg.variable.type)
                    if facts.kind not in {"any", "unknown"}:
                        params[arg.variable.name] = facts
            ret_facts: TypeFacts | None = None
            returns = _collect_returns(node)
            if returns:
                resolved = [result.types.get(expr) for expr in returns]
                if all(typ is not None for typ in resolved):
                    ret_facts = _mypy_type_facts(resolved[0])
                    for typ in resolved[1:]:
                        if str(typ) != str(resolved[0]):
                            ret_facts = None
                            break
            defs[f"{node.line}:{node.column}"] = DefFacts(
                name=node.name, params=params, return_type=ret_facts
            )
        return BridgeFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            defs=defs,
        )


def _collect_returns(fn: Any) -> list[Any]:
    """Every `return expr` expression node in a function body."""
    from mypy.nodes import ReturnStmt

    found: list[Any] = []

    def walk(block: Any) -> None:
        body = getattr(block, "body", None)
        if body is None:
            return
        for stmt in body:
            if isinstance(stmt, ReturnStmt) and stmt.expr is not None:
                found.append(stmt.expr)
            for attr in getattr(type(stmt), "__mypyc_attrs__", ()):
                value = getattr(stmt, attr, None)
                if hasattr(value, "body"):
                    walk(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if hasattr(item, "body"):
                            walk(item)

    walk(getattr(fn, "body", None))
    return found


class CheckerBridge:
    """Optional lowering-side consumer of checker facts (issue #87).

    Holds the translated fact map for one file and answers the two lowering
    questions the bridge covers: the inferred type of an assignment target,
    and the checker-proved parameter and return types of a `def`. It never
    invents a type: `None` means lowering keeps its existing fence.
    """

    def __init__(self, facts: BridgeFacts) -> None:
        self._facts = facts

    @property
    def provider(self) -> str:
        return self._facts.provider

    @classmethod
    def for_path(cls, path: Path, provider: str = "mypy") -> CheckerBridge:
        if provider == "mypy":
            return cls(MypyBridge().facts(path))
        raise CheckFailed(f"checker bridge {provider!r} is not wired; use 'mypy'")

    def inferred_local(self, line: int, col: int) -> TypeFacts | None:
        """The inferred type of the expression starting at (line, col)."""
        return self._facts.expression_at(line, col)

    def def_facts(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> DefFacts | None:
        """Facts mypy proved about one `def`, or None when it has none."""
        return self._facts.def_at(fn.lineno, fn.col_offset)
