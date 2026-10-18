"""Checker-facts seam.

Lowering never consumes a checker's internal type objects: neither mypy nor
pyright exposes a stable compiler API, so pyscriptc owns a narrow facts IR
(`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`). A
`FactsProvider` reports what the checker decided — optional/union members,
callable signatures, overload choices, generic arguments — and tier-2
lowering queries it. `MypyFactsAdapter` is backed by the same mypy pass
`check.py` runs (same subset plugin); a future `PyrightFactsAdapter` feeds
the external JSON oracle into the same dataclasses.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mypy import build as mypy_build
from mypy.modulefinder import BuildSource
from mypy.nodes import (
    ArgKind,
    BytesExpr,
    ClassDef,
    ComplexExpr,
    Decorator,
    Expression,
    FloatExpr,
    FuncDef,
    IntExpr,
    NameExpr,
    Node,
    OverloadedFuncDef,
    Statement,
    StrExpr,
    UnaryExpr,
)
from mypy.options import Options
from mypy.types import (
    CallableType,
    Instance,
    LiteralType,
    NoneType,
    Overloaded,
    TupleType,
    Type,
    TypeAliasType,
    TypeType,
    TypeVarType,
    UnionType,
    get_proper_type,
)

from pyscriptc.check import CheckFailed

# A default value a checker could prove constant at compile time.
StaticValue = int | float | bool | str | bytes | None

_MISSING = object()

_POS_KINDS = (ArgKind.ARG_POS, ArgKind.ARG_OPT)
_PARAM_KIND = {
    ArgKind.ARG_POS: "positional",
    ArgKind.ARG_OPT: "positional_optional",
    ArgKind.ARG_STAR: "varargs",
    ArgKind.ARG_STAR2: "kwargs",
    ArgKind.ARG_NAMED: "keyword_only",
    ArgKind.ARG_NAMED_OPT: "keyword_optional",
}
_TRUE_FALSE_NONE = {
    "builtins.True": True,
    "builtins.False": False,
    "builtins.None": None,
    "builtins.Ellipsis": ...,
}


@dataclass(frozen=True)
class TypeFacts:
    """One type as the checker resolved it, in checker-agnostic terms.

    `kind` is a short classifier: a builtin or user class short name for
    instances ("list", "Box"), or "none", "any", "union", "tuple",
    "callable", "overloaded", "literal", "typevar", "type", "unknown".
    `optional` means the value may be None. `args` carries type arguments —
    union members, generic arguments, tuple items, `type[...]` operands.
    `callable` is set when kind is "callable". `protocols` lists protocol
    fullnames the instance itself declares. `runtime_repr` is the checker's
    own display form (for debugging), never a lowering contract.
    """

    kind: str
    optional: bool = False
    args: tuple[TypeFacts, ...] = ()
    callable: CallableFacts | None = None
    protocols: frozenset[str] = frozenset()
    runtime_repr: str | None = None


@dataclass(frozen=True)
class ParameterFacts:
    """One formal parameter of a callable signature.

    `kind` is "positional", "positional_optional", "keyword_only",
    "keyword_optional", "varargs", or "kwargs".
    """

    name: str
    kind: str
    type: TypeFacts
    has_default: bool = False


@dataclass(frozen=True)
class CallableFacts:
    """One resolved call signature.

    `positional_only` counts leading positional-only parameters.
    `keyword_only` names parameters bound after `*`. `defaults` maps
    parameter names to compile-time-constant default values; a parameter
    with an unrepresentable default is marked `has_default` but absent here.
    """

    parameters: tuple[ParameterFacts, ...]
    return_type: TypeFacts
    positional_only: int = 0
    keyword_only: frozenset[str] = frozenset()
    defaults: Mapping[str, StaticValue] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleFacts:
    """Facts for one compiled module, keyed for lowering queries.

    `expressions` maps "line:col" (1-based line, 0-based column, mypy's
    coordinate space) to the resolved type of the expression starting
    there. `callables` maps a function's qualified name to its resolved
    signature; `overloads` maps an overloaded name to every declared
    variant.
    """

    path: Path
    provider: str
    expressions: dict[str, TypeFacts]
    callables: dict[str, CallableFacts]
    overloads: dict[str, tuple[CallableFacts, ...]]

    def expression_at(self, line: int, col: int) -> TypeFacts | None:
        return self.expressions.get(f"{line}:{col}")

    def signature(self, name: str) -> CallableFacts | None:
        return self.callables.get(name)

    def overload_choices(self, name: str) -> tuple[CallableFacts, ...]:
        return self.overloads.get(name, ())


@runtime_checkable
class FactsProvider(Protocol):
    """Anything that can answer checker questions for a module."""

    @property
    def name(self) -> str: ...

    def facts(self, path: Path) -> ModuleFacts: ...


def facts_provider(checker: str) -> FactsProvider:
    """Pick the provider behind a `run_checker` checker name."""
    if checker == "mypy":
        return MypyFactsAdapter()
    raise CheckFailed(f"checker {checker!r} is not wired; the spike preflight uses mypy")


def _static_value(expr: Expression | None) -> Any:
    """Best-effort compile-time constant for a parameter default."""
    if expr is None:
        return _MISSING
    if isinstance(expr, (IntExpr, FloatExpr, StrExpr, BytesExpr, ComplexExpr)):
        return expr.value
    if isinstance(expr, NameExpr) and expr.fullname in _TRUE_FALSE_NONE:
        return _TRUE_FALSE_NONE[expr.fullname]
    if isinstance(expr, UnaryExpr) and expr.op == "-":
        inner = _static_value(expr.expr)
        return -inner if isinstance(inner, (int, float)) else _MISSING
    return _MISSING


def _param_kind(kind: ArgKind) -> str:
    return _PARAM_KIND.get(kind, "positional")


def _callable_facts(call: CallableType) -> CallableFacts:
    definition = call.definition
    positional_only = 0
    defaults: dict[str, StaticValue] = {}
    if isinstance(definition, FuncDef):
        positional_only = sum(1 for arg in definition.arguments if arg.pos_only)
        for arg in definition.arguments:
            value = _static_value(arg.initializer)
            if value is not _MISSING:
                defaults[arg.variable.name] = value
    else:
        # Stub/serialized callables encode positional-only parameters as a
        # leading run of nameless or `__`-prefixed names.
        for index, kind in enumerate(call.arg_kinds):
            if kind not in _POS_KINDS:
                break
            name = call.arg_names[index] if index < len(call.arg_names) else None
            if name is None or name.startswith("__"):
                positional_only += 1
    parameters: list[ParameterFacts] = []
    keyword_only: set[str] = set()
    for index, kind in enumerate(call.arg_kinds):
        name = call.arg_names[index] if index < len(call.arg_names) else None
        if (
            isinstance(definition, FuncDef)
            and index < len(definition.arguments)
            and definition.arguments[index].variable.name
        ):
            # CallableType drops positional-only names; the def keeps them.
            name = definition.arguments[index].variable.name
        if name is None:
            name = f"arg{index}"
        if kind in (ArgKind.ARG_NAMED, ArgKind.ARG_NAMED_OPT):
            keyword_only.add(name)
        parameters.append(
            ParameterFacts(
                name=name,
                kind=_param_kind(kind),
                type=_type_facts(call.arg_types[index]),
                has_default=kind in (ArgKind.ARG_OPT, ArgKind.ARG_NAMED_OPT),
            )
        )
    return CallableFacts(
        parameters=tuple(parameters),
        return_type=_type_facts(call.ret_type),
        positional_only=positional_only,
        keyword_only=frozenset(keyword_only),
        defaults=defaults,
    )


def _type_facts(typ: Type) -> TypeFacts:
    proper = get_proper_type(typ)
    if isinstance(proper, Instance):
        protocols = frozenset([proper.type.fullname]) if proper.type.is_protocol else frozenset()
        return TypeFacts(
            kind=proper.type.name,
            args=tuple(_type_facts(arg) for arg in proper.args),
            protocols=protocols,
            runtime_repr=str(proper),
        )
    if isinstance(proper, UnionType):
        items = proper.relevant_items()
        members = tuple(_type_facts(item) for item in items)
        return TypeFacts(
            kind="union",
            optional=any(
                isinstance(get_proper_type(item), NoneType) or member.optional
                for item, member in zip(items, members, strict=True)
            ),
            args=members,
            runtime_repr=str(proper),
        )
    if isinstance(proper, NoneType):
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    if isinstance(proper, CallableType):
        return TypeFacts(
            kind="callable",
            callable=_callable_facts(proper),
            runtime_repr=str(proper),
        )
    if isinstance(proper, Overloaded):
        return TypeFacts(
            kind="overloaded",
            args=tuple(_type_facts(item) for item in proper.items),
            runtime_repr=str(proper),
        )
    if isinstance(proper, TupleType):
        return TypeFacts(
            kind="tuple",
            args=tuple(_type_facts(item) for item in proper.items),
            runtime_repr=str(proper),
        )
    if isinstance(proper, LiteralType):
        return TypeFacts(
            kind="literal",
            args=(_type_facts(proper.fallback),),
            runtime_repr=str(proper),
        )
    if isinstance(proper, TypeVarType):
        return TypeFacts(kind="typevar", runtime_repr=proper.name)
    if isinstance(proper, TypeType):
        return TypeFacts(kind="type", args=(_type_facts(proper.item),), runtime_repr=str(proper))
    if isinstance(proper, TypeAliasType):
        return _type_facts(get_proper_type(proper))
    return TypeFacts(
        kind="any" if type(proper).__name__ == "AnyType" else "unknown",
        runtime_repr=str(proper),
    )


def _iter_nodes(root: Node):
    """Yield every AST node under `root`.

    mypyc-compiled mypy classes can't be subclassed, so instead of a
    `TraverserVisitor` we walk the tree generically: every node's
    `__mypyc_attrs__` lists its own fields, and Node or list-valued fields
    hold children.
    """
    stack = [root]
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


def _iter_defs(statements: list[Statement]):
    for stmt in statements:
        yield stmt
        if isinstance(stmt, ClassDef):
            yield from _iter_defs(stmt.defs.body)


def _statement_fullname(stmt: FuncDef | Decorator | OverloadedFuncDef) -> str | None:
    if isinstance(stmt, FuncDef):
        return stmt.fullname or stmt.name
    if isinstance(stmt, Decorator):
        return stmt.func.fullname or stmt.func.name
    if stmt.is_property and stmt.items:
        first = stmt.items[0]
        return _statement_fullname(first)
    return getattr(stmt, "fullname", None)


def _callable_of(stmt: FuncDef | Decorator) -> CallableType | None:
    func = stmt.func if isinstance(stmt, Decorator) else stmt
    typ = get_proper_type(func.type) if func.type is not None else None
    return typ if isinstance(typ, CallableType) else None


class MypyFactsAdapter:
    """Facts backed by the same mypy run `run_checker` drives.

    Options mirror the check invocation: the `pyscriptc.check` plugin,
    `--follow-imports=silent`, no incremental cache. `preserve_asts` and
    `export_types` hold back the trees and expression TypeMap this adapter
    reads; they change what mypy keeps in memory, not what it reports.
    """

    name = "mypy"

    def facts(self, path: Path) -> ModuleFacts:
        path = path.resolve()
        with tempfile.TemporaryDirectory(prefix="pyscriptc-mypy-") as cache:
            options = Options()
            options.incremental = False
            options.follow_imports = "silent"
            options.cache_dir = cache
            options.preserve_asts = True
            options.export_types = True
            options.plugins = ["pyscriptc.check"]
            result = mypy_build.build(
                sources=[BuildSource(str(path), None, None)],
                options=options,
            )
        if result.errors:
            raise CheckFailed("\n".join(result.errors))
        module = next(
            (tree for tree in result.files.values() if Path(tree.path) == path),
            None,
        )
        if module is None:
            raise CheckFailed(f"mypy did not produce a module for {path}")
        # Nodes are yielded parents-first, so a position maps to the
        # outermost expression starting there (a CallExpr over its callee).
        expressions: dict[str, TypeFacts] = {}
        for expr in _iter_nodes(module):
            if not isinstance(expr, Expression) or expr not in result.types:
                continue
            expressions.setdefault(f"{expr.line}:{expr.column}", _type_facts(result.types[expr]))
        callables: dict[str, CallableFacts] = {}
        overloads: dict[str, tuple[CallableFacts, ...]] = {}
        for stmt in _iter_defs(list(module.defs)):
            if isinstance(stmt, (FuncDef, Decorator)):
                call = _callable_of(stmt)
                name = _statement_fullname(stmt)
                if call is not None and name:
                    callables[name] = _callable_facts(call)
            elif isinstance(stmt, OverloadedFuncDef):
                name = _statement_fullname(stmt)
                variants = tuple(
                    _callable_facts(call)
                    for item in stmt.items
                    if isinstance(item, (FuncDef, Decorator))
                    for call in [_callable_of(item)]
                    if call is not None
                )
                if name and variants:
                    overloads[name] = variants
                    impl = stmt.impl
                    impl_call = _callable_of(impl) if isinstance(impl, (FuncDef, Decorator)) else None
                    if impl_call is not None:
                        callables[name] = _callable_facts(impl_call)
        return ModuleFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            callables=callables,
            overloads=overloads,
        )
