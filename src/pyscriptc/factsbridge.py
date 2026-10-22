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
        module_prefix = f"{module.fullname}."
        for node in _iter_mypy_nodes(module):
            if not isinstance(node, FuncDef):
                continue
            fullname = node.fullname or node.name
            if fullname and not fullname.startswith(module_prefix) and fullname != module.fullname:
                # Decorated/property accessors from imported stubs can leak
                # through `module.defs`; keep this module's defs only.
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
                    # Compare the widened facts: `return 1` / `return 2` are
                    # `Literal[1]` / `Literal[2]`, but the def returns `int`.
                    widened = [_mypy_type_facts(_widen_literal(typ)) for typ in resolved]
                    if all(_same_shape(one, widened[0]) for one in widened):
                        ret_facts = widened[0]
                    if ret_facts is not None and ret_facts.kind in {"any", "unknown"}:
                        # mypy could not prove a concrete return (an
                        # unannotated parameter flows out); the fence stands.
                        ret_facts = None
            defs[f"{node.line}:{node.column}"] = DefFacts(
                name=node.name, params=params, return_type=ret_facts
            )
        return BridgeFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            defs=defs,
        )


def _widen_literal(typ: Any) -> Any:
    """A literal type widens to its base type (`Literal[2]` to `int`).

    mypy 2.4 keeps a literal as an `Instance` whose `last_known_value` holds
    the constant, so the base type is already the instance's own type; older
    shapes use `LiteralType.fallback`.
    """
    from mypy.types import LiteralType, get_proper_type

    proper = get_proper_type(typ)
    if isinstance(proper, LiteralType):
        return proper.fallback
    return proper


def _same_shape(one: TypeFacts, other: TypeFacts) -> bool:
    """Whether two facts name the same type, ignoring the literal value.

    `Literal[1]` and `Literal[2]` are the same shape (`int`); `int` and `str`
    are not.
    """
    if one.kind != other.kind:
        return False
    if one.optional != other.optional:
        return False
    if len(one.args) != len(other.args):
        return False
    return all(_same_shape(a, b) for a, b in zip(one.args, other.args, strict=True))


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


# The IR type kinds a bridged fact may translate into. Anything else (a
# protocol, a typevar, a generic the subset cannot express) stays fenced.
_BRIDGED_SCALARS = {"int", "float", "bool", "str", "bytes", "none"}


def facts_to_ir(facts: TypeFacts, classes: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Translate one `TypeFacts` into a lowering IR type, or None.

    `None` means the fact has no representation in the lowered subset, so
    lowering keeps its fence. The translation is deliberately narrow: scalars,
    homogeneous lists and sets, str/int-keyed dicts, tuples, user classes and
    the `T | None` optional widening. A union with more than one non-None arm
    is not in the IR (PS1102), so it stays None.
    """
    if facts.kind in _BRIDGED_SCALARS:
        return {"kind": facts.kind}
    if facts.kind == "literal" and facts.args:
        return facts_to_ir(facts.args[0], classes)
    if facts.kind == "list" and len(facts.args) == 1:
        elem = facts_to_ir(facts.args[0], classes)
        if elem is None:
            return None
        return {"kind": "list", "elem": elem}
    if facts.kind == "set" and len(facts.args) == 1:
        elem = facts_to_ir(facts.args[0], classes)
        if elem is None or elem["kind"] not in {"int", "str"}:
            return None
        return {"kind": "list", "elem": elem, "set": True}
    if facts.kind == "dict" and len(facts.args) == 2:
        key = facts_to_ir(facts.args[0], classes)
        value = facts_to_ir(facts.args[1], classes)
        if key is None or value is None:
            return None
        if key["kind"] not in {"int", "str"}:
            return None
        return {"kind": "dict", "key": key, "value": value}
    if facts.kind == "union":
        arms = [arm for arm in facts.args if arm.kind != "none"]
        optional = any(arm.kind == "none" for arm in facts.args)
        if len(arms) != 1:
            return None
        base = facts_to_ir(arms[0], classes)
        if base is None:
            return None
        if not optional:
            return base
        return {"kind": "class", "name": f"__opt_{_slug(base)}", "label": _label(base) + " | None",
                "optional": True, "elem": base}
    if facts.kind == "class" and classes is not None and facts.args:
        # A generic user class: the subset erases the type argument, so the
        # plain class name is the honest translation.
        name = facts.runtime_repr.split("[", 1)[0].rsplit(".", 1)[-1]
        if name in classes:
            return {"kind": "class", "name": name}
        return None
    if facts.kind == "class" and not facts.args:
        name = facts.runtime_repr.split("[", 1)[0].rsplit(".", 1)[-1]
        if classes is not None and name not in classes:
            return None
        return {"kind": "class", "name": name}
    return None


def _label(typ: dict[str, Any]) -> str:
    kind = typ["kind"]
    if kind == "list":
        return f"list[{_label(typ['elem'])}]"
    if kind == "dict":
        return f"dict[{_label(typ['key'])}, {_label(typ['value'])}]"
    if kind == "class":
        return str(typ.get("label") or typ.get("name"))
    return kind


def _slug(typ: dict[str, Any]) -> str:
    kind = typ["kind"]
    if kind == "list":
        return f"list_{_slug(typ['elem'])}"
    if kind == "dict":
        return f"dict_{_slug(typ['key'])}_{_slug(typ['value'])}"
    if kind == "class":
        return str(typ.get("name"))
    return kind


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

    def inferred_type(
        self,
        node: ast.expr,
        classes: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The IR type mypy proved for an expression, or None."""
        facts = self._facts.expression_at(node.lineno, node.col_offset)
        if facts is None:
            return None
        return facts_to_ir(facts, classes)

    def param_type(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        name: str,
        classes: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The IR type mypy proved for a parameter, or None."""
        facts = self._facts.def_at(fn.lineno, fn.col_offset)
        if facts is None or name not in facts.params:
            return None
        return facts_to_ir(facts.params[name], classes)

    def return_type(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        classes: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The IR type mypy inferred for a `def`'s return, or None."""
        facts = self._facts.def_at(fn.lineno, fn.col_offset)
        if facts is None or facts.return_type is None:
            return None
        return facts_to_ir(facts.return_type, classes)

    def def_facts(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> DefFacts | None:
        """Facts mypy proved about one `def`, or None when it has none."""
        return self._facts.def_at(fn.lineno, fn.col_offset)
