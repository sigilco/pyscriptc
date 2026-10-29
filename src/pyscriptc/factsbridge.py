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
(design decision `.agents/docs/design/checker-bridge.md`, E3a).
"""

from __future__ import annotations

import ast
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyscriptc.check import CheckFailed
from pyscriptc.typefacts import TypeFacts

_MYPY_MIN = (2, 4)
_MYPY_MAX = (2, 5)
# How deep a type argument tree is reduced before it bottoms out as
# `unknown`. A recursive type (a protocol whose member mentions the
# protocol) would otherwise recurse forever.
_TYPE_DEPTH_CAP = 12


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
    except AttributeError, ValueError:
        return None
    if not _MYPY_MIN <= parsed < _MYPY_MAX:
        return None
    return mypy_build, BuildSource, Options, mypy_version.__version__


def mypy_available() -> bool:
    """True when a pinned mypy (>=2.4,<2.5) is importable in this interpreter."""
    return _mypy_api() is not None


def _warn_bridge_errors(path: Path, errors: list[str]) -> None:
    """Report the checker's diagnostics without failing the bridge.

    The bridge answers lowering's inference questions; it is not a type
    checker. A file that does not type-check still carries the types the
    bridge needs, so the diagnostics go to stderr and the bridge keeps
    going. A file the checker could not read at all (a syntax error) has no
    facts either, and the bridge answers nothing for it.
    """
    import sys

    print(f"bridge: {path}: {len(errors)} checker diagnostics", file=sys.stderr)
    for error in errors[:10]:
        print(f"bridge: {error}", file=sys.stderr)


def _module_identity(path: Path) -> tuple[str, Path]:
    """The module name mypy should give this file, and its search root.

    A package file (`pkg/__init__.py`, `pkg/mod.py`) is named `pkg` or
    `pkg.mod` with the parent of `pkg` as the search root, so a relative
    import resolves the way it does at runtime. A loose file (no
    `__init__.py` above it) is named after its stem.
    """
    path = path.resolve()
    parent = path.parent
    if parent.name == "__init__":
        return parent.name, parent.parent
    parts = [] if path.stem == "__init__" else [path.stem]
    root = parent
    while (root / "__init__.py").is_file():
        parts.insert(0, root.name)
        root = root.parent
    if not parts:
        parts = [root.name]
    return ".".join(parts), root


def mypy_version() -> str | None:
    """The pinned mypy version string, or None when no bridge lane is possible.

    Abridged to `major.minor.patch`; a dev build reports None rather than a
    version the record cannot reproduce.
    """
    api = _mypy_api()
    if api is None:
        return None
    version = api[3]
    if not all(part.isdigit() for part in version.split(".")[:3]):
        return None
    return ".".join(version.split(".")[:3])


def provider_version(provider: str) -> str | None:
    """The version of the named checker-bridge provider, or None when absent."""
    if provider == "mypy":
        return mypy_version()
    if provider == "pyrefly":
        return pyrefly_version()
    return None


def pyrefly_version() -> str | None:
    """The installed pyrefly version string, or None when pyrefly is absent.

    Read from the `pyrefly` package metadata, not from the binary: the
    bridge lane runs pyrefly as a subprocess, and the record must name the
    version the answers came from.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - 3.8 and older only
        return None
    try:
        found = version("pyrefly")
    except PackageNotFoundError:
        return None
    return found if found else None


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


def _mypy_type_facts(typ: Any, _depth: int = 0) -> TypeFacts:
    """Reduce one mypy `Type` to the checker-agnostic `TypeFacts` IR.

    A recursive type (a class whose own type appears in its bases, common
    in protocol-heavy stubs) bottoms out at `_TYPE_DEPTH_CAP`: deeper than
    that the fact is `unknown`, which keeps the fence instead of hanging.
    """
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

    if _depth > _TYPE_DEPTH_CAP:
        return TypeFacts(kind="unknown", runtime_repr="...")

    proper = get_proper_type(typ)
    if isinstance(proper, Instance):
        return TypeFacts(
            kind=proper.type.name,
            args=tuple(_mypy_type_facts(arg, _depth + 1) for arg in proper.args),
            protocols=frozenset([proper.type.fullname]) if proper.type.is_protocol else frozenset(),
            runtime_repr=str(proper),
        )
    if isinstance(proper, UnionType):
        items = proper.relevant_items()
        members = tuple(_mypy_type_facts(item, _depth + 1) for item in items)
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
            args=tuple(_mypy_type_facts(item, _depth + 1) for item in proper.items),
            runtime_repr=str(proper),
        )
    if isinstance(proper, LiteralType):
        return TypeFacts(
            kind="literal", args=(_mypy_type_facts(proper.fallback, _depth + 1),), runtime_repr=str(proper)
        )
    if isinstance(proper, TypeVarType):
        return TypeFacts(kind="typevar", runtime_repr=proper.name)
    if isinstance(proper, TypeType):
        return TypeFacts(kind="type", args=(_mypy_type_facts(proper.item, _depth + 1),), runtime_repr=str(proper))
    if isinstance(proper, TypeAliasType):
        return _mypy_type_facts(get_proper_type(proper), _depth + 1)
    if isinstance(proper, AnyType):
        return TypeFacts(kind="any", runtime_repr=str(proper))
    return TypeFacts(kind="unknown", runtime_repr=str(proper))


@dataclass(frozen=True)
class DefFacts:
    """What the checker proved about one `def`, keyed by position (1-based line, 0-based col)."""

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
    """`FactsProvider`-shaped adapter that feeds mypy's inference to lowering.

    `strict` (the default) reports the checker's diagnostics as a
    `CheckFailed`: a compile that asked for the bridge wants the checker's
    word. The census runs the bridge in lenient mode, where the diagnostics
    go to stderr and the bridge keeps the types it did read, because the
    census asks "what does the bridge retire on this corpus", not "does this
    corpus type-check".
    """

    name = "mypy"

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    def facts(self, path: Path) -> BridgeFacts:
        api = _mypy_api()
        if api is None:
            raise CheckFailed(
                "the mypy bridge needs mypy>=2.4,<2.5; install the extra: pip install 'pyscriptc[oracle-mypy]'"
            )
        mypy_build, build_source, options_cls, _version = api
        from mypy.nodes import Expression, FuncDef

        path = path.resolve()
        module_name, search_root = _module_identity(path)
        with tempfile.TemporaryDirectory(prefix="pyscriptc-bridge-") as cache:
            options = options_cls()
            options.incremental = False
            options.follow_imports = "silent"
            options.cache_dir = cache
            options.preserve_asts = True
            options.export_types = True
            options.plugins = ["pyscriptc.check"]
            # A relative import (`from .core import ...`) resolves against the
            # module name, so the file needs its package name and the search
            # root that holds the package.
            options.mypy_path = [str(search_root)]
            # The bridge reads types, not diagnostics: a file that does not
            # type-check still carries the types the bridge needs, so the
            # errors are reported but do not stop the bridge.
            result = mypy_build(sources=[build_source(str(path), module_name, None)], options=options)
        errors = list(result.errors)
        if errors:
            if self.strict:
                raise CheckFailed("\n".join(errors))
            _warn_bridge_errors(path, errors)
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
            defs[f"{node.line}:{node.column}"] = DefFacts(name=node.name, params=params, return_type=ret_facts)
        return BridgeFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            defs=defs,
        )


class PyreflyBridge:
    """`FactsProvider`-shaped adapter that feeds pyrefly's TSP facts to lowering.

    The fallback lane the checker-bridge design doc names: the same
    `CheckerBridge` surface as the mypy lane, but the facts come from
    `pyrefly tsp` (the `pyrefly/typeFacts` bulk request plus the per-def
    `typeServer/getComputedType` signature), not from mypy's TypeMap.

    `facts(path)` runs TSP with `check-unannotated-defs` and
    `infer-return-types = "checked"` pinned on the command line (the
    tsp server has no config file on its argv, so pyrefly.toml defaults
    would otherwise apply): an unannotated `def` then reports its
    inferred return type instead of `Any`, even for a fully unannotated
    def. Parameter types are unchanged: pyrefly resolves an unannotated
    parameter to `Any` regardless (checked on 1.4.x, the same limit the
    mypy lane has).

    What pyrefly proves and what it does not, measured on pyrefly 1.4.x
    (issue #258):

    - Expressions: the bulk request answers the same `line:col` keys as the
      mypy lane, with the same value shapes (`int`, `list[int]`,
      `dict[str, int]`, `Literal[...]`).
    - Parameters: pyrefly resolves an annotated parameter to its type, and
      resolves an unannotated one to `Any` exactly like mypy does. It does
      not infer an unannotated parameter from its uses or its callers
      (`pyrefly infer` pins the same `a: int` only from literal same-file
      call sites; cross-module or non-literal callers do not pin).
    - Returns: with the two flags pinned here, an unannotated `def`
      carries its inferred return type (literal widened: `Literal['x']`
      reads as `str`, `LiteralString` as `str`). Without them (the raw
      tsp default) an unannotated `def` gets `Any`, and the bridge
      recovers it from the return expressions' own facts, only when they
      all agree.

    So the two lanes answer the same questions with the same limits: the
    bridge is a second opinion on the checker, not a second inference
    engine.
    """

    name = "pyrefly"

    def _config_args(self) -> list[str]:
        """`tsp` spawn flags that pin the inference configuration.

        `pyrefly tsp` takes none of the check-mode inference flags on its
        command line, so the bridge writes a synthetic `pyrefly.toml`
        (`check-unannotated-defs = true`, `infer-return-types = "checked"`)
        into a scratch directory and points the server at it with
        `--config`. An unannotated `def` then reports its inferred return
        type instead of `Any` (checked on 1.4.x). The directory outlives
        the adapter's lifetime: the caller passes it back with
        `_config_dir_done` after `facts()` returns.
        """
        directory = tempfile.TemporaryDirectory(prefix="pyscriptc-pyrefly-tsp-")
        self._config_dir = directory
        Path(directory.name, "pyrefly.toml").write_text(
            'check-unannotated-defs = true\ninfer-return-types = "checked"\n',
            encoding="utf-8",
        )
        return ["--config", str(Path(directory.name, "pyrefly.toml"))]

    def _config_dir_done(self) -> None:
        """Release the scratch config directory after `facts()` returned."""
        if self._config_dir is not None:
            self._config_dir.cleanup()
            self._config_dir = None

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        self._command = command
        self._config_dir: tempfile.TemporaryDirectory[str] | None = None

    def facts(self, path: Path) -> BridgeFacts:
        from pyscriptc.typefacts import PyreflyTspFactsAdapter

        path = path.resolve()
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckFailed(f"cannot read {path}: {exc}") from exc
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise CheckFailed(f"cannot parse {path}: {exc}") from exc
        lines = source.splitlines()
        with PyreflyTspFactsAdapter(self._command) as adapter:
            adapter.with_config_args(self._config_args())
            module_facts = adapter.facts(path)
            self._config_dir_done()
            expressions = dict(module_facts.expressions)
            defs = self._def_facts(adapter, path, tree, lines, expressions)
        return BridgeFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            defs=defs,
        )

    def _def_facts(
        self,
        adapter: Any,
        path: Path,
        tree: ast.Module,
        lines: list[str],
        expressions: dict[str, TypeFacts],
    ) -> dict[str, DefFacts]:
        """Per-def facts: the resolved parameters and the agreed return type."""

        connection = adapter._connection
        if connection is None:
            return {}
        uri = path.as_uri()
        snapshot = adapter._snapshot_now(connection)
        defs: dict[str, DefFacts] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params: dict[str, TypeFacts] = {}
            signature = self._computed_signature(adapter, connection, uri, snapshot, node, lines)
            if signature is not None:
                for param, arg in zip(signature.parameters, node.args.args, strict=False):
                    if param.type.kind in {"any", "unknown"}:
                        continue
                    params[arg.arg] = param.type
            ret_facts = self._return_facts(node, expressions, signature)
            defs[f"{node.lineno}:{node.col_offset}"] = DefFacts(
                name=node.name,
                params=params,
                return_type=ret_facts,
            )
        return defs

    def _computed_signature(
        self,
        adapter: Any,
        connection: Any,
        uri: str,
        snapshot: int,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> Any:
        """The computed signature of one `def`, or None when pyrefly gives none."""
        from pyscriptc.typefacts import _def_name_range, _tsp_type

        rng = _def_name_range(node, lines)
        if rng is None:
            return None
        computed = connection.request(
            "typeServer/getComputedType",
            {"arg": {"uri": uri, "range": rng}, "snapshot": snapshot},
            timeout=30,
        )
        if computed is None:
            return None
        facts = _tsp_type(computed, {})
        return facts.callable

    def _return_facts(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        expressions: dict[str, TypeFacts],
        signature: Any,
    ) -> TypeFacts | None:
        """The return type pyrefly proved, or None when it proved none.

        An annotated `def` carries its own return type. An unannotated one
        gets `Any` from pyrefly, so the bridge recovers it from the return
        expressions' own facts and answers only when they all agree.
        """
        if signature is not None and signature.return_type.kind not in {"any", "unknown"}:
            return signature.return_type
        returns = _collect_ast_returns(node)
        if not returns:
            return None
        resolved = [expressions.get(f"{expr.lineno}:{expr.col_offset}") for expr in returns]
        if not all(facts is not None for facts in resolved):
            return None
        widened = [_widen_literal_facts(facts) for facts in resolved if facts is not None]
        if not all(_same_shape(one, widened[0]) for one in widened):
            return None
        if widened[0].kind in {"any", "unknown"}:
            return None
        return widened[0]


def _widen_literal_facts(facts: TypeFacts) -> TypeFacts:
    """A literal fact widens to its base type (`Literal[1]` to `int`)."""
    if facts.kind == "literal" and facts.args:
        return facts.args[0]
    return facts


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
    """Every `return expr` expression node in a mypy function body."""
    from mypy.nodes import Block, ReturnStmt

    found: list[Any] = []

    def walk(block: Any) -> None:
        body = getattr(block, "body", None)
        if isinstance(body, Block):
            body = body.body
        if not isinstance(body, (list, tuple)):
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


def _collect_ast_returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """Every `return expr` expression node in an ast function body.

    The ast twin of `_collect_returns`: it descends into the nested blocks
    an ast body can hold (if, for, while, with, try), so a return inside a
    branch is found too.
    """
    found: list[ast.expr] = []

    def walk(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                found.append(stmt.value)
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list):
                    walk(nested)
            for handler in getattr(stmt, "handlers", []) or []:
                walk(handler.body)

    walk(fn.body)
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
        return {
            "kind": "class",
            "name": f"__opt_{_slug(base)}",
            "label": _label(base) + " | None",
            "optional": True,
            "elem": base,
        }
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


class BridgeProvider:
    """A provider name that builds one bridge per file of a package tree.

    A checker covers one module at a time, so a directory compile needs a
    bridge per file. This handle carries the lane name and builds the
    per-file bridges on demand; `lower_directory` asks for them through
    `for_file`.
    """

    def __init__(self, provider: str, strict: bool = True) -> None:
        self.provider = provider
        self.strict = strict
        # The inference records the lowerer accepted across every file the
        # provider served, gathered by `pyscriptc infer` and `compile
        # --debug`. Each per-file `CheckerBridge` points here.
        self.records: list[dict[str, Any]] = []

    def for_file(self, path: Path) -> CheckerBridge:
        return CheckerBridge.for_path(path, self.provider, strict=self.strict, records=self.records)


class CheckerBridge:
    """Optional lowering-side consumer of checker facts (issue #87).

    Holds the translated fact map for one file and answers the two lowering
    questions the bridge covers: the inferred type of an assignment target,
    and the checker-proved parameter and return types of a `def`. It never
    invents a type: `None` means lowering keeps its existing fence.
    """

    def __init__(self, facts: BridgeFacts, records: list[dict[str, Any]] | None = None) -> None:
        self._facts = facts
        # The accepted-inference records this file's lowering appended
        # (`pyscriptc infer`, `compile --debug`). None means the caller
        # does not collect records.
        self.infer_records = records if records is not None else []
        # The package-wide pin map (`pyscriptc infer`): the lowerer reads
        # it through the same handle as the per-module facts.
        self.call_site_facts: Any = None

    @property
    def provider(self) -> str:
        return self._facts.provider

    @classmethod
    def for_path(
        cls,
        path: Path,
        provider: str = "mypy",
        strict: bool = True,
        records: list[dict[str, Any]] | None = None,
    ) -> CheckerBridge:
        if provider == "mypy":
            return cls(MypyBridge(strict=strict).facts(path), records)
        if provider == "pyrefly":
            return cls(PyreflyBridge().facts(path), records)
        raise CheckFailed(f"checker bridge {provider!r} is not wired; use 'mypy' or 'pyrefly'")

    def for_file(self, path: Path) -> CheckerBridge:
        """A bridge for one file of a package tree, built by the same provider.

        A checker covers one module at a time, so a package tree needs one
        bridge per file. The provider is remembered so the per-file bridge
        is the same lane as the one the compile asked for.
        """
        return BridgeProvider(self.provider).for_file(path)

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
