"""Checker-facts seam.

Lowering never consumes a checker's internal type objects: neither mypy nor
pyright exposes a stable compiler API, so pyscriptc owns a narrow facts IR
(`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`). A
`FactsProvider` reports what the checker decided — optional/union members,
callable signatures, overload choices, generic arguments — and tier-2
lowering queries it. `MypyFactsAdapter` is backed by the same mypy pass
`check.py` runs (same subset plugin); `PyrightFactsAdapter` feeds the
external pyright-langserver oracle (stdio LSP hover + documentSymbol) into
the same dataclasses.
"""

from __future__ import annotations

import ast
import json
import os
import queue
import re
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
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
    if checker == "pyright":
        return PyrightFactsAdapter()
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


# ---------------------------------------------------------------------------
# Pyright langserver oracle
#
# The second provider speaks LSP: a long-lived pyright-langserver subprocess
# over stdio (initialize -> didOpen -> hover/documentSymbol). There is no
# `--facts` verb and no vendored analyzer; rendered hover strings like
# "(function) def f(a: int) -> str" are parsed back into the same
# TypeFacts/CallableFacts dataclasses. Hover answers on symbol positions
# only, so expression coverage is sparser than mypy's full TypeMap — nodes
# pyright declines to describe degrade to "no fact". The langserver binary
# comes from PYRIGHT_LANGSERVER_BIN (shlex-split, default
# "pyright-langserver" on PATH); "--stdio" is appended when missing.


_PYRIGHT_STUBS = Path(__file__).with_name("_pyright_stubs")


class _LspConnection:
    """Minimal synchronous JSON-RPC client for one stdio langserver.

    Requests are sent on the main thread; a daemon reader thread frames
    `Content-Length` messages off stdout and routes responses by id. This
    keeps the protocol plumbing out of the fact-mapping code.
    """

    def __init__(self, argv: Sequence[str]) -> None:
        self._argv = list(argv)
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._next_id = 0
        self._notifications: list[dict[str, Any]] = []
        self._notify_cv = threading.Condition()
        self._dead = False

    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise CheckFailed(
                f"pyright langserver is not installed (command {self._argv[0]!r}); "
                "set PYRIGHT_LANGSERVER_BIN or put pyright-langserver on PATH"
            ) from exc
        threading.Thread(target=self._read_loop, daemon=True).start()

    def request(self, method: str, params: Any, timeout: float = 30.0) -> dict[str, Any] | None:
        """Send a request and return its `result`, or None on LSP error."""
        self._ensure_live()
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                response = slot.get(timeout=timeout)
            except queue.Empty:
                raise CheckFailed(f"pyright langserver timed out answering {method!r}") from None
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        error = response.get("error")
        if error:
            return None
        return response.get("result")

    def notify(self, method: str, params: Any) -> None:
        if self._dead:
            return
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except (OSError, CheckFailed):
            self._dead = True

    def wait_notification(
        self,
        method: str,
        match: Any,
        timeout: float,
    ) -> dict[str, Any] | None:
        """Wait for a notification of `method` whose params satisfy `match`."""
        deadline = time.monotonic() + timeout
        with self._notify_cv:
            while True:
                for index, msg in enumerate(self._notifications):
                    if msg.get("method") == method and match(msg.get("params")):
                        return self._notifications.pop(index)
                if self._dead:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._notify_cv.wait(remaining)

    def shutdown(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            self.request("shutdown", None, timeout=10)
            self.notify("exit", None)
            proc.wait(timeout=5)
        except (CheckFailed, subprocess.TimeoutExpired, OSError):
            pass
        if proc.poll() is None:
            proc.kill()
        self._dead = True

    def _ensure_live(self) -> None:
        self.start()
        assert self._proc is not None
        if self._dead or self._proc.poll() is not None:
            raise CheckFailed("pyright langserver exited unexpectedly")

    def _send(self, message: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        body = json.dumps(message).encode("utf-8")
        try:
            self._proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._dead = True
            raise CheckFailed(f"pyright langserver pipe broke: {exc}") from exc

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            try:
                length = -1
                while True:
                    line = stream.readline()
                    if not line:
                        self._mark_dead()
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    head, _, value = line.partition(b":")
                    if head.strip().lower() == b"content-length":
                        try:
                            length = int(value.strip())
                        except ValueError:
                            pass
                if length < 0:
                    continue
                body = stream.read(length)
                if len(body) < length:
                    self._mark_dead()
                    return
                message = json.loads(body)
            except (OSError, ValueError, json.JSONDecodeError):
                self._mark_dead()
                return
            self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            with self._lock:
                slot = self._pending.pop(request_id, None)
            if slot is not None:
                slot.put(message)
            return
        with self._notify_cv:
            self._notifications.append(message)
            self._notify_cv.notify_all()

    def _mark_dead(self) -> None:
        self._dead = True
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for slot in pending:
            slot.put({"error": "server exited"})
        with self._notify_cv:
            self._notify_cv.notify_all()


def _utf16_col(line_text: str, byte_col: int) -> int:
    """ast col_offset is a UTF-8 byte offset; LSP wants UTF-16 units."""
    prefix = line_text.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore")
    return len(prefix.encode("utf-16-le")) // 2


def _split_top(text: str, sep: str) -> list[str]:
    """Split `text` on `sep` occurrences outside brackets and quotes."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == sep and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


def _strip_wrapping_parens(text: str) -> str:
    """Drop one layer of parens when it wraps the whole span `(A | B)`."""
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        inner = text[1:-1]
        depth = 0
        quote = ""
        balanced = True
        for char in inner:
            if quote:
                if char == "\\":
                    continue
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char in "({[":
                depth += 1
            elif char in ")}]":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = inner.strip()
    return text


def _short_name(name: str) -> str:
    """`module.Class` -> `Class`, keeping subscripts and whitespace out."""
    name = name.strip()
    return name.rsplit(".", 1)[-1]


def _parse_type_text(text: str) -> TypeFacts | None:
    """Parse one rendered type string into TypeFacts; None when unintelligible.

    Shared grammar for hover payloads (`list[int] | None`, `def` return
    types) and for annotation source slices. The shapes it knows: unions
    split on top-level `|`, `Optional`/`Union`, `Literal`, `type[X]`,
    `Callable`, `tuple`, generic `Head[args]`, `None`, `Any`/`Unknown`,
    `name@scope` typevars, quoted literals, and bare class names.
    """
    text = _strip_wrapping_parens(text.strip())
    if not text or text in {"...", "Ellipsis"}:
        return None
    members = [piece.strip() for piece in _split_top(text, "|")]
    if len(members) > 1:
        args = tuple(member for member in (_parse_type_text(piece) for piece in members) if member)
        if not args:
            return None
        return TypeFacts(
            kind="union",
            optional=any(member.kind == "none" or member.optional for member in args),
            args=args,
            runtime_repr=text,
        )
    head, bracket = _split_head_bracket(text)
    if bracket is not None:
        kind = _short_name(head)
        inner = bracket
        if kind in {"Optional"}:
            inner_facts = _parse_type_text(inner)
            if inner_facts is None:
                return None
            return TypeFacts(
                kind="union",
                optional=True,
                args=(inner_facts, TypeFacts(kind="none", optional=True, runtime_repr="None")),
                runtime_repr=text,
            )
        if kind == "Union":
            args = tuple(m for m in (_parse_type_text(p) for p in _split_top(inner, ",")) if m)
            return (
                TypeFacts(
                    kind="union",
                    optional=any(m.kind == "none" or m.optional for m in args),
                    args=args,
                    runtime_repr=text,
                )
                if args
                else None
            )
        if kind in {"type", "Type"}:
            operand = _parse_type_text(inner)
            return TypeFacts(kind="type", args=(operand,), runtime_repr=text) if operand else None
        if kind == "Literal":
            literals = tuple(
                fact for fact in (_parse_literal_piece(piece.strip()) for piece in _split_top(inner, ",")) if fact
            )
            if not literals:
                return None
            if len(literals) == 1:
                return TypeFacts(kind="literal", args=literals[0].args, runtime_repr=text)
            return TypeFacts(
                kind="union",
                optional=any(lit.optional for lit in literals),
                args=literals,
                runtime_repr=text,
            )
        if kind == "Callable":
            return _parse_callable_type(inner, text)
        pieces = _split_top(inner, ",")
        if pieces and pieces[-1].strip() == "...":
            pieces = pieces[:-1]  # homogeneous `tuple[X, ...]`
        args = tuple(m for m in (_parse_type_text(p) for p in pieces) if m)
        return TypeFacts(kind=kind, args=args, runtime_repr=text)
    if text.startswith(("*", "**")):
        return _parse_type_text(text.lstrip("*"))
    if "@" in text and not text.startswith(('"', "'")):
        name = text.split("@", 1)[0].strip()
        return TypeFacts(kind="typevar", runtime_repr=name) if name else None
    lowered = text.lower()
    if lowered in {"none", "nonetype"}:
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    if text in {"Any", "Unknown"}:
        return TypeFacts(kind="any", runtime_repr=text)
    if text in {"Never", "NoReturn"}:
        return TypeFacts(kind="unknown", runtime_repr=text)
    literal = _parse_literal_piece(text)
    if literal is not None:
        return literal
    if text.startswith(("'", '"')) and text.endswith(("'", '"')):
        return _parse_type_text(text[1:-1])
    if not all(part.isidentifier() for part in text.split(".")):
        return None
    return TypeFacts(kind=_short_name(text), runtime_repr=text)


def _split_head_bracket(text: str) -> tuple[str, str | None]:
    """`list[int]` -> ("list", "int"); returns (text, None) when bracketless."""
    depth = 0
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == "\\":
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "[":
            if depth == 0:
                if not text.endswith("]"):
                    return text, None
                return text[:index], text[index + 1 : -1]
            depth += 1
        elif char == "]":
            depth -= 1
        elif char in "({":
            depth += 1
        elif char in ")}":
            depth -= 1
    return text, None


def _parse_literal_piece(text: str) -> TypeFacts | None:
    """A `Literal[...]` member or a bare literal: 1, 's', True, None, Color.RED."""
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        value = None
    if isinstance(value, bool):
        return TypeFacts(kind="literal", args=(TypeFacts(kind="bool"),), runtime_repr=text)
    if isinstance(value, int):
        return TypeFacts(kind="literal", args=(TypeFacts(kind="int"),), runtime_repr=text)
    if isinstance(value, float):
        return TypeFacts(kind="literal", args=(TypeFacts(kind="float"),), runtime_repr=text)
    if isinstance(value, str):
        return TypeFacts(kind="literal", args=(TypeFacts(kind="str"),), runtime_repr=text)
    if isinstance(value, bytes):
        return TypeFacts(kind="literal", args=(TypeFacts(kind="bytes"),), runtime_repr=text)
    if text in {"True", "False"}:
        return TypeFacts(kind="literal", args=(TypeFacts(kind="bool"),), runtime_repr=text)
    if text == "None":
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    if "." in text and all(part.isidentifier() for part in text.split(".")):
        # Enum member like `Color.RED`: fallback is the enum class.
        return TypeFacts(kind="literal", args=(TypeFacts(kind=_short_name(text)),), runtime_repr=text)
    return None


def _parse_callable_type(inner: str, text: str) -> TypeFacts | None:
    """`Callable[[a, b], r]` -> TypeFacts(kind="callable", callable=...)."""
    pieces = _split_top(inner, ",")
    if len(pieces) != 2:
        return TypeFacts(kind="callable", runtime_repr=text)
    params_text, ret_text = pieces[0].strip(), pieces[1].strip()
    parameters: list[ParameterFacts] = []
    if params_text.startswith("[") and params_text.endswith("]"):
        for index, piece in enumerate(_split_top(params_text[1:-1], ",")):
            piece = piece.strip()
            if not piece or piece == "...":
                continue
            param_type = _parse_type_text(piece) or TypeFacts(kind="unknown", runtime_repr=piece)
            parameters.append(ParameterFacts(name=f"arg{index}", kind="positional", type=param_type))
    return_type = _parse_type_text(ret_text) or TypeFacts(kind="unknown", runtime_repr=ret_text)
    return TypeFacts(
        kind="callable",
        callable=CallableFacts(parameters=tuple(parameters), return_type=return_type),
        runtime_repr=text,
    )


def _default_static_value(text: str) -> Any:
    """`"x"`/`3`/`True` -> StaticValue; _MISSING when unrepresentable."""
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return _MISSING
    if value is ... or isinstance(value, (tuple, frozenset, complex)):
        return _MISSING
    return value if isinstance(value, (int, float, bool, str, bytes)) or value is None else _MISSING


def _parse_signature_params(
    inner: str,
) -> tuple[tuple[ParameterFacts, ...], int, frozenset[str], dict[str, StaticValue]]:
    """Parse the parameter list of a `def` hover block.

    `/` and bare `*` separators map to positional-only and keyword-only
    markers; `*name`/`**name` become varargs/kwargs; `= value` marks a
    default (unrepresentable values stay out of `defaults`).
    """
    parameters: list[ParameterFacts] = []
    defaults: dict[str, StaticValue] = {}
    positional_only = 0
    keyword_only: set[str] = set()
    seen_star = False
    for raw in _split_top(inner, ","):
        piece = raw.strip()
        if not piece:
            continue
        if piece == "/":
            positional_only = len(parameters)
            continue
        if piece == "*":
            seen_star = True
            continue
        stars = 0
        while piece.startswith("*"):
            stars += 1
            piece = piece[1:].lstrip()
        name = ""
        type_text = ""
        default_text: str | None = None
        colon = _top_level_char(piece, ":")
        equals = _top_level_char(piece, "=")
        if colon >= 0:
            name = piece[:colon].strip()
            rest = piece[colon + 1 :]
            eq = _top_level_char(rest, "=")
            if eq >= 0:
                type_text, default_text = rest[:eq], rest[eq + 1 :]
            else:
                type_text = rest
        elif equals >= 0:
            name = piece[:equals].strip()
            default_text = piece[equals + 1 :]
        else:
            name = piece
        if not name or not all(part.isidentifier() for part in name.split(".")):
            continue
        name = name.strip()
        param_type = _parse_type_text(type_text.strip()) or TypeFacts(
            kind="unknown", runtime_repr=type_text.strip() or None
        )
        has_default = default_text is not None
        if default_text is not None:
            value = _default_static_value(default_text.strip())
            if value is not _MISSING:
                defaults[name] = value
        if stars >= 2:
            kind = "kwargs"
        elif stars == 1:
            kind = "varargs"
            seen_star = True
        elif seen_star:
            kind = "keyword_optional" if has_default else "keyword_only"
            keyword_only.add(name)
        else:
            kind = "positional_optional" if has_default else "positional"
        parameters.append(ParameterFacts(name=name, kind=kind, type=param_type, has_default=has_default))
    return tuple(parameters), positional_only, frozenset(keyword_only), defaults


def _top_level_char(text: str, char: str) -> int:
    """Index of `char` at bracket depth 0 outside quotes, or -1."""
    depth = 0
    quote = ""
    for index, current in enumerate(text):
        if quote:
            if current == "\\":
                continue
            if current == quote:
                quote = ""
        elif current in "\"'":
            quote = current
        elif current in "([{":
            depth += 1
        elif current in ")]}":
            depth -= 1
        elif current == char and depth == 0:
            return index
    return -1


def _parse_def_signature(value: str) -> CallableFacts | None:
    """Parse `(function|method) def name(params) -> ret` hover text."""
    first_block = value.split("\n\n", 1)[0].strip()
    match = _HOVER_DEF.match(first_block)
    if not match:
        return None
    params_text = match.group("params")
    return_text = match.group("ret") or ""
    parameters, positional_only, keyword_only, defaults = _parse_signature_params(params_text)
    return_type = _parse_type_text(return_text.strip()) or TypeFacts(
        kind="unknown", runtime_repr=return_text.strip() or None
    )
    return CallableFacts(
        parameters=parameters,
        return_type=return_type,
        positional_only=positional_only,
        keyword_only=keyword_only,
        defaults=defaults,
    )


_HOVER_TAG = re.compile(r"^\((?P<tag>[\w ]+)\)\s*", re.S)
_HOVER_DEF = re.compile(r"^(?:\([\w ]+\)\s*)?def\s+\w+\s*\((?P<params>.*)\)\s*->\s*(?P<ret>.+)$", re.S)
_HOVER_DECL = re.compile(r"^\((?P<tag>variable|parameter|property)\)\s*\w[\w.]*\s*:\s*(?P<rest>.+)$", re.S)
_HOVER_CLASS = re.compile(r"^(?:\([\w ]+\)\s*)?class\s+(?P<name>\w+)")


def _parse_hover_type(value: str) -> TypeFacts | None:
    """Map a hover `value` string to the TypeFacts it describes."""
    if not value:
        return None
    block = value.split("\n\n", 1)[0].strip()
    # Strip an overload-count tail like "\n(+2 overloads)".
    block = re.split(r"\n\s*\(\+\d+ overload[s]?\)", block, maxsplit=1)[0].strip()
    tag_match = _HOVER_TAG.match(block)
    tag = tag_match.group("tag") if tag_match else ""
    if tag == "module":
        return TypeFacts(kind="module", runtime_repr=block)
    if tag == "type parameter":
        return TypeFacts(kind="typevar", runtime_repr=block)
    signature = _parse_def_signature(block)
    if signature is not None:
        return TypeFacts(kind="callable", callable=signature, runtime_repr=block)
    decl = _HOVER_DECL.match(block)
    if decl:
        rest = re.sub(r"\s*\((readonly|read-only|final)\)\s*$", "", decl.group("rest").strip())
        return _parse_type_text(rest)
    class_match = _HOVER_CLASS.match(block)
    if class_match:
        return TypeFacts(
            kind="type",
            args=(TypeFacts(kind=class_match.group("name")),),
            runtime_repr=block,
        )
    return None


def _is_overload_decorator(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id == "overload"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "overload"
    return False


class PyrightFactsAdapter:
    """Facts backed by the pyright-langserver stdio oracle.

    One long-lived subprocess is spawned lazily and reused across `facts`
    calls; each target file is opened once via `textDocument/didOpen`.
    Function/method signatures come from hovering the `def` name;
    expression facts come from hovering symbol positions (call sites take
    the callee's return type). Annotated assignment targets get their
    declared type from the annotation's own source text because hover
    reports the *narrowed* type. Anything the oracle declines to describe
    — literals, operators, unsymbolized spans — degrades to "no fact"
    rather than an error.

    `command` overrides the spawn line (default: `PYRIGHT_LANGSERVER_BIN`
    env or `pyright-langserver` on PATH); `--stdio` is appended when
    absent. `close()` shuts the server down; leaving it to GC also works.
    """

    name = "pyright"

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        raw = command if command is not None else os.environ.get("PYRIGHT_LANGSERVER_BIN")
        argv = shlex.split(raw) if isinstance(raw, str) else list(raw or ["pyright-langserver"])
        if "--stdio" not in argv:
            argv = [*argv, "--stdio"]
        self._argv = argv
        self._connection: _LspConnection | None = None
        self._opened: set[str] = set()
        self._hover_cache: dict[tuple[str, int, int], str | None] = {}

    def facts(self, path: Path) -> ModuleFacts:
        path = path.resolve()
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckFailed(f"cannot read {path}: {exc}") from exc
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise CheckFailed(f"cannot parse {path}: {exc}") from exc
        connection = self._connect()
        uri = path.as_uri()
        self._open_document(uri, source)
        expressions = self._expression_facts(connection, uri, tree, source)
        callables, overloads = self._callable_facts(connection, uri, tree, source)
        return ModuleFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            callables=callables,
            overloads=overloads,
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.shutdown()
            self._connection = None
            self._opened.clear()
            self._hover_cache.clear()

    def __enter__(self) -> PyrightFactsAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _connect(self) -> _LspConnection:
        if self._connection is None:
            connection = _LspConnection(self._argv)
            connection.start()
            result = connection.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": None,
                    "capabilities": {
                        "textDocument": {
                            "hover": {"contentFormat": ["plaintext"]},
                            "publishDiagnostics": {},
                        },
                        "workspace": {"workspaceFolders": True},
                    },
                    "workspaceFolders": None,
                },
                timeout=60,
            )
            if result is None:
                connection.shutdown()
                raise CheckFailed("pyright langserver refused initialize")
            connection.notify("initialized", {})
            connection.notify(
                "workspace/didChangeConfiguration",
                {
                    "settings": {
                        "python": {
                            "analysis": {
                                "stubPath": str(_PYRIGHT_STUBS),
                                "pythonVersion": "3.13",
                            }
                        }
                    }
                },
            )
            self._connection = connection
        return self._connection

    def _open_document(self, uri: str, source: str) -> None:
        connection = self._connect()
        if uri in self._opened:
            return
        self._opened.add(uri)
        # Point the workspace at the file's directory so sibling imports
        # resolve the same way the pyright checker preflight does.
        connection.notify(
            "workspace/didChangeWorkspaceFolders",
            {"event": {"added": [{"uri": uri.rsplit("/", 1)[0], "name": "pyscriptc"}], "removed": []}},
        )
        connection.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "python",
                    "version": 1,
                    "text": source,
                }
            },
        )
        connection.wait_notification(
            "textDocument/publishDiagnostics",
            lambda params: isinstance(params, dict) and params.get("uri") == uri,
            timeout=30,
        )

    def _hover(self, connection: _LspConnection, uri: str, line: int, char: int) -> str | None:
        key = (uri, line, char)
        if key in self._hover_cache:
            return self._hover_cache[key]
        result = connection.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": line, "character": char}},
        )
        value: str | None = None
        if isinstance(result, dict):
            contents = result.get("contents")
            if isinstance(contents, dict):
                text = contents.get("value")
                if isinstance(text, str):
                    value = text
            elif isinstance(contents, str):
                value = contents
            elif isinstance(contents, list):
                texts = [
                    item["value"] for item in contents if isinstance(item, dict) and isinstance(item.get("value"), str)
                ]
                value = "\n\n".join(texts) if texts else None
        self._hover_cache[key] = value
        return value

    def _expression_facts(
        self, connection: _LspConnection, uri: str, tree: ast.Module, source: str
    ) -> dict[str, TypeFacts]:
        lines = source.splitlines()
        facts: dict[str, TypeFacts] = {}
        # Declared types first: an AnnAssign target's fact is the annotation
        # itself, parsed from source — hover would report the narrowed type.
        declared: dict[tuple[int, int], ast.expr] = {}
        annotations: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and node.annotation is not None:
                annotations.add(id(node.annotation))
                declared[(node.target.lineno, node.target.col_offset)] = node.annotation
            elif isinstance(node, ast.arg) and node.annotation is not None:
                annotations.add(id(node.annotation))
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr):
                continue
            key = f"{node.lineno}:{node.col_offset}"
            if key in facts:
                continue
            if id(node) in annotations:
                parsed = _parse_type_text(ast.get_source_segment(source, node) or "")
                if parsed is not None:
                    facts[key] = TypeFacts(kind="type", args=(parsed,), runtime_repr=parsed.runtime_repr)
                continue
            declared_annotation = declared.get((node.lineno, node.col_offset))
            if declared_annotation is not None:
                parsed = _parse_type_text(ast.get_source_segment(source, declared_annotation) or "")
                if parsed is not None:
                    facts[key] = parsed
                    continue
            position = self._node_hover_position(node, lines)
            if position is None:
                continue
            line, char = position
            value = self._hover(connection, uri, line, char)
            if value is None:
                continue
            if isinstance(node, ast.Call):
                signature = _parse_def_signature(value.split("\n\n", 1)[0].strip())
                if signature is not None:
                    facts[key] = signature.return_type
                continue
            parsed = _parse_hover_type(value)
            if parsed is not None:
                facts[key] = parsed
        return facts

    def _node_hover_position(self, node: ast.expr, lines: list[str]) -> tuple[int, int] | None:
        """LSP (0-based line, UTF-16 col) where hover describes this node."""
        if isinstance(node, ast.Call):
            node = node.func
            if not isinstance(node, (ast.Name, ast.Attribute)):
                return None
        if isinstance(node, ast.Name):
            if node.lineno - 1 >= len(lines):
                return None
            return node.lineno - 1, _utf16_col(lines[node.lineno - 1], node.col_offset)
        if isinstance(node, ast.Attribute):
            # The node's start is the base; the attribute name sits at the end.
            if node.end_lineno != node.lineno or node.end_col_offset is None:
                return None
            if node.lineno - 1 >= len(lines):
                return None
            attr_start = node.end_col_offset - len(node.attr.encode("utf-8"))
            if attr_start < 0:
                return None
            return node.lineno - 1, _utf16_col(lines[node.lineno - 1], attr_start)
        return None

    def _callable_facts(
        self, connection: _LspConnection, uri: str, tree: ast.Module, source: str
    ) -> tuple[dict[str, CallableFacts], dict[str, tuple[CallableFacts, ...]]]:
        lines = source.splitlines()
        callables: dict[str, CallableFacts] = {}
        overloads: dict[str, tuple[CallableFacts, ...]] = {}

        def visit(body: list[ast.stmt], prefix: str) -> None:
            for stmt in body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = f"{prefix}{stmt.name}"
                    position = self._def_hover_position(stmt, lines)
                    signature = (
                        _parse_def_signature(self._hover(connection, uri, *position) or "")
                        if position is not None
                        else None
                    )
                    if signature is None:
                        continue
                    if any(_is_overload_decorator(dec) for dec in stmt.decorator_list):
                        overloads.setdefault(qualname, ())
                        overloads[qualname] += (signature,)
                    else:
                        callables[qualname] = signature
                    visit(stmt.body, f"{prefix}{stmt.name}.<locals>.")

        visit(tree.body, "__main__.")
        return callables, overloads

    def _def_hover_position(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]
    ) -> tuple[int, int] | None:
        line_index = node.lineno - 1
        if line_index >= len(lines):
            return None
        text = lines[line_index][node.col_offset :]
        marker = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        offset = text.find(marker)
        if offset < 0:
            return None
        name_col = node.col_offset + offset + len(marker)
        return line_index, _utf16_col(lines[line_index], name_col)
