"""Checker-facts seam.

Lowering never consumes a checker's internal type objects: neither mypy nor
pyright exposes a stable compiler API, so pyscriptc owns a narrow facts IR
(`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`). A
`FactsProvider` reports what the checker decided — optional/union members,
callable signatures, overload choices, generic arguments — and tier-2
lowering queries it. `MypyFactsAdapter` is backed by the same mypy pass
`check.py` runs (same subset plugin); `PyrightFactsAdapter` feeds the
external pyright oracle (`--facts` JSON or stdio LSP hover) into the same
dataclasses; `PyreflyFactsAdapter` reads `pyrefly check --report-pysa`
per-module JSON; `TSPFactsAdapter` drives `pyright-typeserver` over its
Type Server Protocol for structured per-node types.
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
    if checker == "pyrefly":
        return PyreflyFactsAdapter()
    if checker == "tsp":
        return TSPFactsAdapter()
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
# The second provider has two lanes. The preferred lane runs the pyright
# CLI with `--facts` (the sigilco/pyright fork): one subprocess emits
# structural type JSON — resolved types per expression node plus function,
# overload and class signatures — which maps into the same
# TypeFacts/CallableFacts dataclasses without any string parsing. The
# fallback lane keeps the original path for stock upstream pyright: a
# long-lived pyright-langserver subprocess over stdio (initialize ->
# didOpen -> hover/documentSymbol) whose rendered hover strings like
# "(function) def f(a: int) -> str" are parsed back. Hover answers on
# symbol positions only, so expression coverage is sparser than the
# `--facts` lane's full-node walk — nodes the oracle declines to describe
# degrade to "no fact". The CLI binary comes from PYRIGHT_BIN (shlex-split,
# default "pyright" on PATH) and is probed for `--facts` support via
# `--help`; the langserver binary comes from PYRIGHT_LANGSERVER_BIN
# (shlex-split, default "pyright-langserver" on PATH); "--stdio" is
# appended when missing.


_PYRIGHT_STUBS = Path(__file__).with_name("_pyright_stubs")

# Named tuples because ruff (target-version py314) strips except-parens
# into `except A, B:` — valid only on 3.14+, broken on the 3.13 dev venv.
_PIPE_ERRORS = (OSError, CheckFailed)
_SHUTDOWN_ERRORS = _PIPE_ERRORS + (subprocess.TimeoutExpired,)
_CLI_ERRORS = (OSError, subprocess.TimeoutExpired)
_READ_ERRORS = (OSError, ValueError, json.JSONDecodeError)
_EVAL_ERRORS = (ValueError, SyntaxError)


class _LspConnection:
    """Minimal synchronous JSON-RPC client for one stdio langserver.

    Requests are sent on the main thread; a daemon reader thread frames
    `Content-Length` messages off stdout and routes responses by id. This
    keeps the protocol plumbing out of the fact-mapping code.
    """

    def __init__(self, argv: Sequence[str], server_name: str = "pyright langserver") -> None:
        self._argv = list(argv)
        self._server_name = server_name
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
                f"{self._server_name} is not installed (command {self._argv[0]!r}); "
                "set PYRIGHT_LANGSERVER_BIN or put the server on PATH"
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
                raise CheckFailed(f"{self._server_name} timed out answering {method!r}") from None
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
        except _PIPE_ERRORS:
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
        except _SHUTDOWN_ERRORS:
            pass
        if proc.poll() is None:
            proc.kill()
        self._dead = True

    def _ensure_live(self) -> None:
        self.start()
        assert self._proc is not None
        if self._dead or self._proc.poll() is not None:
            raise CheckFailed(f"{self._server_name} exited unexpectedly")

    def _send(self, message: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        body = json.dumps(message).encode("utf-8")
        try:
            self._proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._dead = True
            raise CheckFailed(f"{self._server_name} pipe broke: {exc}") from exc

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
            except _READ_ERRORS:
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


def _byte_col(line_text: str, utf16_col: int) -> int:
    """Inverse of `_utf16_col`: UTF-16 units to a UTF-8 byte offset."""
    units = 0
    byte_col = 0
    for char in line_text:
        if units >= utf16_col:
            break
        units += 2 if ord(char) > 0xFFFF else 1
        byte_col += len(char.encode("utf-8"))
    return byte_col


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
    except _EVAL_ERRORS:
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
    except _EVAL_ERRORS:
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


def _facts_type(fact: Mapping[str, Any]) -> TypeFacts:
    """Map one serialized `--facts` type object onto TypeFacts."""
    kind = fact.get("kind")
    if kind == "union":
        members = tuple(_facts_type(option) for option in fact.get("options", ()) if isinstance(option, Mapping))
        return TypeFacts(
            kind="union",
            optional=any(member.kind == "none" or member.optional for member in members),
            args=members,
            runtime_repr="union",
        )
    if kind == "class":
        name = str(fact.get("name") or "unknown")
        if name == "NoneType":
            return TypeFacts(kind="none", optional=True, runtime_repr="None")
        if fact.get("literalValue") is not None:
            return TypeFacts(kind="literal", args=(TypeFacts(kind=name),), runtime_repr=repr(fact["literalValue"]))
        args = tuple(_facts_type(arg) for arg in fact.get("typeArgs", ()) if isinstance(arg, Mapping))
        if name == "tuple":
            items = fact.get("tupleArgs") or fact.get("typeArgs") or ()
            return TypeFacts(
                kind="tuple",
                args=tuple(_facts_type(item) for item in items if isinstance(item, Mapping)),
                runtime_repr="tuple",
            )
        return TypeFacts(kind=name, args=args, runtime_repr=str(fact.get("fullName") or name))
    if kind == "type":
        inner_args = tuple(_facts_type(arg) for arg in fact.get("typeArgs", ()) if isinstance(arg, Mapping))
        if fact.get("name") == "type":
            # builtins.type[T]: args carries the denoted instance type.
            item = inner_args[0] if len(inner_args) == 1 else TypeFacts(kind="unknown")
            return TypeFacts(kind="type", args=(item,), runtime_repr="type")
        # Any other instantiable class (a class object): args carries its
        # instance form, keeping typeArgs so `list[int]` survives unwrap.
        return TypeFacts(
            kind="type",
            args=(TypeFacts(kind=str(fact.get("name") or "unknown"), args=inner_args),),
            runtime_repr="type",
        )
    if kind == "callable":
        signature = fact.get("signature")
        return TypeFacts(
            kind="callable",
            callable=_facts_signature(signature) if isinstance(signature, Mapping) else None,
            runtime_repr="callable",
        )
    if kind == "overloaded":
        variants = fact.get("overloads", ())
        return TypeFacts(
            kind="overloaded",
            args=tuple(
                TypeFacts(kind="callable", callable=_facts_signature(variant))
                for variant in variants
                if isinstance(variant, Mapping)
            ),
            runtime_repr="overloaded",
        )
    if kind == "typevar":
        return TypeFacts(kind="typevar", runtime_repr=str(fact.get("name") or ""))
    if kind == "module":
        return TypeFacts(kind="module", runtime_repr=str(fact.get("name") or ""))
    if kind in ("any", "unknown"):
        return TypeFacts(kind="any", runtime_repr=str(kind))
    return TypeFacts(kind="unknown", runtime_repr=str(fact.get("printed") or kind or ""))


def _facts_denoted(facts: TypeFacts) -> TypeFacts:
    """Unwrap the type-object layer an annotation expression evaluates to.

    Pyright resolves `int | None` in annotation position to
    `type[int] | type[None]`; the declared type a target binds is the
    inner `int | None`. Single `type[X]` facts unwrap to `X`.
    """
    if facts.kind == "type" and facts.args:
        inner = facts.args[0]
        if inner.kind == "NoneType":
            return TypeFacts(kind="none", optional=True, runtime_repr="None")
        return inner
    if facts.kind == "union":
        members = tuple(_facts_denoted(member) for member in facts.args)
        return TypeFacts(
            kind="union",
            optional=any(member.kind == "none" or member.optional for member in members),
            args=members,
            runtime_repr="union",
        )
    return facts


def _facts_default_value(default_type: Any) -> Any:
    """Compile-time constant for a `--facts` param defaultType, or _MISSING."""
    if not isinstance(default_type, Mapping):
        return _MISSING
    if default_type.get("literalValue") is not None:
        return default_type["literalValue"]
    if str(default_type.get("fullName") or "").endswith("NoneType"):
        return None
    return _MISSING


def _facts_signature(signature: Mapping[str, Any]) -> CallableFacts:
    """Map a `--facts` signature onto CallableFacts.

    Pyright keeps the `/` and bare `*` separators in its parameter list as
    unnamed params: an unnamed `simple` param marks the positional-only
    boundary and an unnamed `args` param the keyword-only boundary.
    """
    parameters: list[ParameterFacts] = []
    positional_only = 0
    keyword_only: set[str] = set()
    defaults: dict[str, Any] = {}
    after_star = False
    for param in signature.get("params", ()):
        if not isinstance(param, Mapping):
            continue
        name = param.get("name")
        category = param.get("category")
        if name is None:
            if category == "simple":
                positional_only = len(parameters)
            elif category == "args":
                after_star = True
            continue
        param_type = param.get("type")
        param_facts = _facts_type(param_type) if isinstance(param_type, Mapping) else TypeFacts(kind="unknown")
        has_default = bool(param.get("hasDefault"))
        if category == "args":
            kind = "varargs"
            after_star = True
        elif category == "kwargs":
            kind = "kwargs"
        elif after_star:
            kind = "keyword_optional" if has_default else "keyword_only"
            keyword_only.add(str(name))
        else:
            kind = "positional_optional" if has_default else "positional"
        if has_default:
            value = _facts_default_value(param.get("defaultType"))
            if value is not _MISSING:
                defaults[str(name)] = value
        parameters.append(ParameterFacts(name=str(name), kind=kind, type=param_facts, has_default=has_default))
    return_type = signature.get("returnType")
    return CallableFacts(
        parameters=tuple(parameters),
        return_type=_facts_type(return_type) if isinstance(return_type, Mapping) else TypeFacts(kind="unknown"),
        positional_only=positional_only,
        keyword_only=frozenset(keyword_only),
        defaults=defaults,
    )


def _facts_qualnames(tree: ast.Module) -> dict[str, str]:
    """Suffix map from pyright's flat qualnames to pyscriptc qualnames.

    Pyright names a nested function `mod.outer.inner`; pyscriptc keeps the
    mypy convention `outer.<locals>.inner`. Both drop the module segment,
    so the mapping is keyed on the remaining dotted suffix.
    """
    mapping: dict[str, str] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                visit(stmt.body, f"{prefix}{stmt.name}.")
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                flat = prefix.replace("<locals>.", "")
                mapping[f"{flat}{stmt.name}"] = f"{prefix}{stmt.name}"
                visit(stmt.body, f"{prefix}{stmt.name}.<locals>.")

    visit(tree.body, "")
    return mapping


def _facts_qualname(pyright_name: str, mapping: Mapping[str, str]) -> str:
    """`mod.outer.inner` -> `__main__.outer.<locals>.inner`."""
    _, _, suffix = pyright_name.partition(".")
    return f"__main__.{mapping.get(suffix, suffix)}"


class PyrightFactsAdapter:
    """Facts backed by `pyright --facts`, with an LSP-hover fallback.

    Preferred lane: spawn the pyright CLI with `--facts` once per file and
    map its structural JSON — no string parsing anywhere. The CLI comes
    from `cli`/`PYRIGHT_BIN` (default `pyright` on PATH) and is probed for
    `--facts` support via `--help`; a stock upstream pyright lacks the
    verb and falls through to the fallback lane below.

    Fallback lane (stock pyright compat): one long-lived pyright-langserver
    subprocess is spawned lazily and reused across `facts` calls; each
    target file is opened once via `textDocument/didOpen`. Function/method
    signatures come from hovering the `def` name; expression facts come
    from hovering symbol positions (call sites take the callee's return
    type), so coverage is sparser than the `--facts` lane. Annotated
    assignment targets get their declared type from the annotation's own
    source text because hover reports the *narrowed* type. Anything the
    oracle declines to describe — literals, operators, unsymbolized spans —
    degrades to "no fact" rather than an error.

    `command` overrides the langserver spawn line (default:
    `PYRIGHT_LANGSERVER_BIN` env or `pyright-langserver` on PATH);
    `--stdio` is appended when absent. `cli` overrides the `--facts` CLI
    spawn line (default: `PYRIGHT_BIN` env or `pyright` on PATH); pass
    `cli=[]` to force the LSP lane. `close()` shuts the server down;
    leaving it to GC also works.
    """

    name = "pyright"

    def __init__(
        self,
        command: str | Sequence[str] | None = None,
        cli: str | Sequence[str] | None = None,
    ) -> None:
        raw = command if command is not None else os.environ.get("PYRIGHT_LANGSERVER_BIN")
        argv = shlex.split(raw) if isinstance(raw, str) else list(raw or ["pyright-langserver"])
        if "--stdio" not in argv:
            argv = [*argv, "--stdio"]
        self._argv = argv
        cli_raw = cli if cli is not None else os.environ.get("PYRIGHT_BIN", "pyright")
        self._cli_argv = shlex.split(cli_raw) if isinstance(cli_raw, str) else list(cli_raw)
        self._facts_supported: bool | None = None
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
        cli = self._facts_cli()
        if cli is not None:
            return self._cli_facts(cli, path, tree, source)
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

    def _facts_cli(self) -> list[str] | None:
        """The `--facts`-capable CLI argv, or None to use the LSP lane."""
        if not self._cli_argv:
            return None
        if self._facts_supported is None:
            try:
                proc = subprocess.run(
                    [*self._cli_argv, "--help"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except _CLI_ERRORS:
                proc = None
            self._facts_supported = bool(proc and "--facts" in (proc.stdout or ""))
        return self._cli_argv if self._facts_supported else None

    def _cli_facts(self, cli: list[str], path: Path, tree: ast.Module, source: str) -> ModuleFacts:
        """Fast lane: one `pyright --facts` subprocess, structural JSON in."""
        with tempfile.TemporaryDirectory(prefix="pyscriptc-pyright-facts-") as tmp:
            config = Path(tmp) / "pyrightconfig.json"
            config.write_text(
                json.dumps(
                    {
                        "stubPath": str(_PYRIGHT_STUBS),
                        "executionEnvironments": [{"root": str(path.parent)}],
                    }
                ),
                encoding="utf-8",
            )
            try:
                proc = subprocess.run(
                    [*cli, "--facts", "--pythonversion", "3.13", "--project", tmp, str(path)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except _CLI_ERRORS as exc:
                raise CheckFailed(f"pyright --facts failed for {path}: {exc}") from exc
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            output = (proc.stderr or proc.stdout).strip() or f"pyright exited {proc.returncode}"
            raise CheckFailed(output) from None
        summary = report.get("summary") or {}
        if summary.get("errorCount", 0):
            raise CheckFailed(f"pyright reported {summary['errorCount']} error(s) in {path}")
        facts_doc = (report.get("facts") or {}).get(str(path))
        if facts_doc is None:
            candidates = list((report.get("facts") or {}).values())
            if len(candidates) != 1:
                raise CheckFailed(f"pyright emitted no facts for {path}")
            facts_doc = candidates[0]
        rename = _facts_qualnames(tree)
        callables = {
            _facts_qualname(name, rename): _facts_signature(sig)
            for name, sig in (facts_doc.get("callables") or {}).items()
            if isinstance(sig, Mapping)
        }
        overloads = {
            _facts_qualname(name, rename): tuple(_facts_signature(sig) for sig in sigs if isinstance(sig, Mapping))
            for name, sigs in (facts_doc.get("overloads") or {}).items()
            if isinstance(sigs, list)
        }
        expressions: dict[str, TypeFacts] = {}
        lines = source.splitlines()
        for expr in facts_doc.get("expressions") or ():
            if not isinstance(expr, Mapping):
                continue
            line = expr.get("line")
            col = expr.get("col")
            if not isinstance(line, int) or not isinstance(col, int) or line < 0 or line >= len(lines):
                continue
            if not isinstance(expr.get("type"), Mapping):
                continue
            expressions.setdefault(f"{line + 1}:{_byte_col(lines[line], col)}", _facts_type(expr["type"]))
        # AnnAssign targets take the annotation's denoted type, not the
        # narrowed type pyright reports for the name at its assignment
        # site (e.g. `x: int | None = 1` records the union, not `int`).
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or node.annotation is None:
                continue
            target_key = f"{node.target.lineno}:{node.target.col_offset}"
            annotation_key = f"{node.annotation.lineno}:{node.annotation.col_offset}"
            declared = expressions.get(annotation_key)
            if declared is not None:
                expressions[target_key] = _facts_denoted(declared)
        return ModuleFacts(
            path=path,
            provider=self.name,
            expressions=expressions,
            callables=callables,
            overloads=overloads,
        )

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
                if isinstance(stmt, ast.ClassDef):
                    visit(stmt.body, f"{prefix}{stmt.name}.")
                    continue
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


# ---------------------------------------------------------------------------
# Pyrefly Pysa-report oracle
#
# `pyrefly check --report-pysa <outdir> --report-pysa-format json <target>`
# (hidden flag, pyrefly >= 1.4) writes one `type_of_expressions/<mod>:<id>.json`
# and one `definitions/<mod>:<id>.json` per analyzed module. The expressions
# file carries a Pysa `functions` map: scope id ("MTL" for module level,
# "F:<n>" per function) -> `locations` ("line:col-line:col" spans, 1-based,
# end-exclusive) indexing a `type_table` of rendered types. The definitions
# file resolves each `F:<n>` to a name, location, parent and its
# `undecorated_signatures` (one entry per @overload variant plus the
# implementation), which is how the adapter fills CallableFacts/overloads
# without parsing signature strings.

_PYSA_SIGNATURE = re.compile(r"^\((?P<params>.*)\)\s*->\s*(?P<ret>.+)$", re.S)
_PYSA_FORMAT_VERSION = 1
# Pysa scope keys for function bodies; "MTL" is the module top level.
_PYSA_SCOPE_FUNCTION = re.compile(r"^F:\d+$")
# Param variants inside a pysa signature's parameters.List.
_PYSA_PARAM_KIND = {
    "PosOnly": "positional",
    "Pos": "positional",
    "VarArg": "varargs",
    "KwOnly": "keyword_only",
    "Kwargs": "kwargs",
}


def _pysa_location_key(span: str) -> str | None:
    """`"14:13-14:20"` (1-based, end-exclusive) -> `"14:12"` (mypy space)."""
    start = span.split("-", 1)[0]
    line, _, col = start.partition(":")
    if not line.isdigit() or not col.isdigit() or int(col) < 1:
        return None
    return f"{int(line)}:{int(col) - 1}"


def _pysa_type(entry: Mapping[str, Any]) -> TypeFacts:
    """Map one pysa `type_table` entry onto TypeFacts.

    The `string` field is pyrefly's rendered type — parsed by the shared
    type-text grammar, with a signature form `(a: int, b = 2) -> int` that
    pysa uses for callables. `class_names` is the fallback classifier when
    the rendered form is outside the shared grammar.
    """
    text = str(entry.get("string") or "")
    signature = _PYSA_SIGNATURE.match(text)
    if signature is not None:
        parameters, positional_only, keyword_only, _defaults = _parse_signature_params(signature.group("params"))
        return_type = _parse_type_text(signature.group("ret").strip()) or TypeFacts(
            kind="unknown", runtime_repr=signature.group("ret").strip() or None
        )
        return TypeFacts(
            kind="callable",
            callable=CallableFacts(
                parameters=parameters,
                return_type=return_type,
                positional_only=positional_only,
                keyword_only=keyword_only,
            ),
            runtime_repr=text,
        )
    parsed = _parse_type_text(text)
    if parsed is not None:
        return parsed
    names = entry.get("class_names")
    if isinstance(names, Mapping):
        classes = names.get("classes") or ()
        first = classes[0] if classes else None
        if isinstance(first, Mapping):
            cls = first.get("class")
            if isinstance(cls, Mapping) and cls.get("class_name"):
                return TypeFacts(kind=str(cls["class_name"]), runtime_repr=text or None)
    return TypeFacts(kind="unknown", runtime_repr=text or None)


def _pysa_signature(signature: Mapping[str, Any], defaults: Mapping[str, Any]) -> CallableFacts:
    """Map one `undecorated_signatures` entry onto CallableFacts.

    Parameter kinds come from the pysa wrapper tags (PosOnly/Pos/VarArg/
    KwOnly/Kwargs); `required` absent marks a defaulted parameter, whose
    compile-time value is recovered from the module's own AST defaults map.
    """
    parameters: list[ParameterFacts] = []
    positional_only = 0
    keyword_only: set[str] = set()
    static_defaults: dict[str, StaticValue] = {}
    for wrapper in signature.get("parameters", {}).get("List", ()):
        if not isinstance(wrapper, Mapping):
            continue
        tag, param = next(iter(wrapper.items()), (None, None))
        if not isinstance(param, Mapping):
            continue
        name = str(param.get("name") or f"arg{len(parameters)}")
        required = param.get("required")
        has_default = name in defaults or (required is False and tag in ("Pos", "PosOnly", "KwOnly"))
        annotation = param.get("annotation")
        param_type = _pysa_type(annotation) if isinstance(annotation, Mapping) else TypeFacts(kind="unknown")
        kind = _PYSA_PARAM_KIND.get(str(tag), "positional")
        if tag == "PosOnly":
            positional_only += 1
        elif tag == "KwOnly":
            keyword_only.add(name)
            if has_default:
                kind = "keyword_optional"
        elif has_default:
            kind = "positional_optional"
        if has_default:
            value = defaults.get(name, _MISSING)
            if value is not _MISSING:
                static_defaults[name] = value
        parameters.append(ParameterFacts(name=name, kind=kind, type=param_type, has_default=has_default))
    annotation = signature.get("return_annotation")
    return_type = _pysa_type(annotation) if isinstance(annotation, Mapping) else TypeFacts(kind="unknown")
    return CallableFacts(
        parameters=tuple(parameters),
        return_type=return_type,
        positional_only=positional_only,
        keyword_only=frozenset(keyword_only),
        defaults=static_defaults,
    )


def _def_name_positions(tree: ast.Module) -> dict[tuple[int, int], str]:
    """(1-based line, 0-based col) of each `def` name -> pyscriptc qualname."""
    mapping: dict[tuple[int, int], str] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                visit(stmt.body, f"{prefix}{stmt.name}.")
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                marker = "async def " if isinstance(stmt, ast.AsyncFunctionDef) else "def "
                mapping[(stmt.lineno, stmt.col_offset + len(marker))] = f"{prefix}{stmt.name}"
                visit(stmt.body, f"{prefix}{stmt.name}.<locals>.")

    visit(tree.body, "__main__.")
    return mapping


def _static_defaults(tree: ast.Module) -> dict[str, dict[str, Any]]:
    """Qualname -> parameter name -> default value (or _MISSING when non-constant)."""
    defaults: dict[str, dict[str, StaticValue]] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                visit(stmt.body, f"{prefix}{stmt.name}.")
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{stmt.name}"
                positional = [*stmt.args.posonlyargs, *stmt.args.args]
                bound = list(
                    zip(positional[len(positional) - len(stmt.args.defaults) :], stmt.args.defaults, strict=True)
                ) + list(zip(stmt.args.kwonlyargs, stmt.args.kw_defaults, strict=True))
                slots: dict[str, Any] = {}
                for arg, expr in bound:
                    if expr is None:
                        continue
                    try:
                        value = ast.literal_eval(expr)
                    except _EVAL_ERRORS:
                        value = _MISSING
                    if not (isinstance(value, (int, float, bool, str, bytes)) or value is None):
                        value = _MISSING
                    slots[arg.arg] = value
                defaults[qualname] = slots
                visit(stmt.body, f"{prefix}{stmt.name}.<locals>.")

    visit(tree.body, "__main__.")
    return defaults


def _pysa_module_facts(
    expressions_doc: Mapping[str, Any],
    definitions_doc: Mapping[str, Any],
    path: Path,
    tree: ast.Module,
) -> ModuleFacts:
    """Pure mapping: pysa JSON documents -> ModuleFacts (no subprocess)."""
    for label, doc in (("type_of_expressions", expressions_doc), ("definitions", definitions_doc)):
        version = doc.get("format_version")
        if version != _PYSA_FORMAT_VERSION:
            raise CheckFailed(
                f"pyrefly pysa {label} format_version is {version!r}, expected "
                f"{_PYSA_FORMAT_VERSION} — the report format changed; update PyreflyFactsAdapter"
            )
    expressions: dict[str, TypeFacts] = {}
    functions = expressions_doc.get("functions") or {}
    for scope in functions.values():
        if not isinstance(scope, Mapping):
            continue
        type_table = scope.get("type_table") or ()
        for span, index in (scope.get("locations") or {}).items():
            key = _pysa_location_key(str(span))
            if key is None or not isinstance(index, int) or index >= len(type_table):
                continue
            entry = type_table[index]
            if isinstance(entry, Mapping):
                expressions.setdefault(key, _pysa_type(entry))
    class_names = {
        str(cid): str(cls.get("name") or "")
        for cid, cls in (definitions_doc.get("class_definitions") or {}).items()
        if isinstance(cls, Mapping)
    }
    name_map = _def_name_positions(tree)
    defaults = _static_defaults(tree)
    callables: dict[str, CallableFacts] = {}
    overloads: dict[str, tuple[CallableFacts, ...]] = {}
    for fdef in (definitions_doc.get("function_definitions") or {}).values():
        if not isinstance(fdef, Mapping):
            continue
        name_location = _pysa_location_key(str(fdef.get("name_location") or ""))
        qualname = name_map.get(tuple(int(part) for part in name_location.split(":"))) if name_location else None
        if qualname is None:
            name = str(fdef.get("name") or "")
            parent = fdef.get("parent")
            if isinstance(parent, Mapping) and "Class" in parent:
                cls = parent["Class"]
                owner = class_names.get(str(cls.get("class_id") if isinstance(cls, Mapping) else cls), "")
                qualname = f"__main__.{owner}.{name}" if owner else f"__main__.{name}"
            else:
                qualname = f"__main__.{name}"
        signatures = [
            _pysa_signature(sig, defaults.get(qualname, {}))
            for sig in (fdef.get("undecorated_signatures") or ())
            if isinstance(sig, Mapping)
        ]
        if not signatures:
            continue
        if len(signatures) > 1:
            overloads[qualname] = tuple(signatures)
        callables[qualname] = signatures[-1]
    return ModuleFacts(
        path=path,
        provider="pyrefly",
        expressions=expressions,
        callables=callables,
        overloads=overloads,
    )


class PyreflyFactsAdapter:
    """Facts backed by `pyrefly check --report-pysa` per-module JSON.

    One subprocess per `facts` call emits the Pysa report for the module;
    `type_of_expressions` fills `expressions` and `definitions` fills
    `callables`/`overloads`. Coverage is Pysa-selective: the report only
    records expressions pyrefly kept for taint analysis, so sub-expressions
    (and nested `def`s, which get no `function_definitions` entry) are often
    absent and degrade to "no fact"; `Unknown` annotations map to `any`.
    `format_version` is pinned at 1 — any drift raises CheckFailed instead
    of silently mis-mapping.

    `command` overrides the spawn line (default: `PYREFLY_BIN` env or
    `pyrefly` on PATH).
    """

    name = "pyrefly"

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        raw = command if command is not None else os.environ.get("PYREFLY_BIN")
        self._argv = shlex.split(raw) if isinstance(raw, str) else list(raw or ["pyrefly"])

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
        with tempfile.TemporaryDirectory(prefix="pyscriptc-pyrefly-") as tmp:
            try:
                proc = subprocess.run(
                    [
                        *self._argv,
                        "check",
                        "--report-pysa",
                        tmp,
                        "--report-pysa-format",
                        "json",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except FileNotFoundError as exc:
                raise CheckFailed(
                    f"pyrefly is not installed (command {self._argv[0]!r}); "
                    "pip install pyrefly, set PYREFLY_BIN, or put pyrefly on PATH"
                ) from exc
            except _CLI_ERRORS as exc:
                raise CheckFailed(f"pyrefly check failed for {path}: {exc}") from exc
            expressions_doc = self._read_report(tmp, "type_of_expressions", path)
            definitions_doc = self._read_report(tmp, "definitions", path)
        if proc.returncode != 0:
            output = (proc.stderr or proc.stdout).strip() or f"pyrefly exited {proc.returncode}"
            raise CheckFailed(output)
        return _pysa_module_facts(expressions_doc, definitions_doc, path, tree)

    def _read_report(self, outdir: str, kind: str, path: Path) -> Mapping[str, Any]:
        """The `kind/<module>:<id>.json` whose source_path is `path`."""
        report_dir = Path(outdir) / kind
        stem = path.stem
        candidates = sorted(report_dir.glob(f"{stem}:*.json")) + sorted(
            report_dir.glob(f"{stem.replace('.', '_')}:*.json")
        )
        if not candidates:
            raise CheckFailed(f"pyrefly emitted no {kind} report for {path}")
        for candidate in candidates:
            try:
                doc = json.loads(candidate.read_text(encoding="utf-8"))
            except _READ_ERRORS:
                continue
            source_path = doc.get("source_path")
            recorded = None
            if isinstance(source_path, Mapping):
                recorded = source_path.get("FileSystem")
            if recorded is None or Path(str(recorded)) == path:
                return doc
        raise CheckFailed(f"pyrefly {kind} report for {path} was not readable JSON")


# ---------------------------------------------------------------------------
# Pyright typeserver (TSP) oracle
#
# The third provider talks to `pyright-typeserver` (the sigilco/pyright
# fork's `work/scriptc-native` build, or the stock package) over stdio
# JSON-RPC: LSP `initialize` -> `textDocument/didOpen` ->
# `typeServer/getSnapshot`, then `typeServer/getComputedType` /
# `getDeclaredType` / `getExpectedType` per node range -> structured Type
# JSON (kind/flags/declaration/subTypes/overloads/specializedTypes). Unlike
# hover, TSP is per-node rather than bulk, so the adapter walks every
# `ast.expr` in the file — the same coverage shape mypy's TypeMap gives —
# and maps each response into TypeFacts/CallableFacts without any string
# parsing. Nodes the evaluator declines (e.g. whole Call nodes — the
# adapter falls back to the callee's return type) degrade to "no fact".

# TypeServerProtocol.TypeKind (const enum order in typeServerProtocol.ts).
_TSP_KIND_BUILTIN = 0
_TSP_KIND_FUNCTION = 2
_TSP_KIND_CLASS = 3
_TSP_KIND_UNION = 4
_TSP_KIND_MODULE = 5
_TSP_KIND_TYPEVAR = 6
_TSP_KIND_OVERLOADED = 7
_TSP_KIND_SYNTHESIZED = 8
_TSP_KIND_TYPEREF = 9
_TSP_KIND_OVERLOAD_RESULT = 10

# TypeFlags bits.
_TSP_FLAG_INSTANTIABLE = 1 << 0
_TSP_FLAG_LITERAL = 1 << 3
_TSP_FLAG_INTERFACE = 1 << 4

_TSP_STUB_DEF = re.compile(r"def\s+\w+\s*\((?P<params>.*)\)\s*->\s*(?P<ret>[^\n]+)", re.S)
_TSP_STUB_CLASS = re.compile(r"class\s+(?P<name>\w+)")


def _tsp_decl_name(decl: Any) -> str | None:
    if isinstance(decl, Mapping):
        name = decl.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def _tsp_type(typ: Any, seen: dict[int, TypeFacts]) -> TypeFacts:
    """Map one serialized TSP Type object onto TypeFacts.

    `seen` is the per-response id -> TypeFacts cache TypeReference nodes
    (kind 9) resolve through; ids are registered as each type is mapped so
    later references deref, never recurse.
    """
    if not isinstance(typ, Mapping):
        return TypeFacts(kind="unknown")
    kind = typ.get("kind")
    type_id = typ.get("id")
    if kind == _TSP_KIND_TYPEREF:
        ref = typ.get("typeReferenceId")
        return seen.get(ref, TypeFacts(kind="unknown", runtime_repr=f"tsp-ref:{ref}"))
    facts: TypeFacts
    if kind == _TSP_KIND_BUILTIN:
        name = str(typ.get("name") or "unknown")
        facts = TypeFacts(
            kind="any" if name in ("any", "ellipsis") else "unknown",
            runtime_repr=name,
        )
    elif kind == _TSP_KIND_CLASS:
        facts = _tsp_class_type(typ, seen)
    elif kind == _TSP_KIND_UNION:
        members = tuple(_tsp_type(sub, seen) for sub in typ.get("subTypes", ()))
        facts = TypeFacts(
            kind="union",
            optional=any(member.kind == "none" or member.optional for member in members),
            args=members,
            runtime_repr="union",
        )
    elif kind == _TSP_KIND_FUNCTION:
        facts = TypeFacts(
            kind="callable",
            callable=_tsp_function_signature(typ, seen),
            runtime_repr="function",
        )
    elif kind == _TSP_KIND_OVERLOADED:
        variants = tuple(
            TypeFacts(kind="callable", callable=_tsp_function_signature(variant, seen))
            for variant in typ.get("overloads", ())
        )
        facts = TypeFacts(kind="overloaded", args=variants, runtime_repr="overloaded")
    elif kind == _TSP_KIND_MODULE:
        facts = TypeFacts(kind="module", runtime_repr=str(typ.get("moduleName") or ""))
    elif kind == _TSP_KIND_TYPEVAR:
        facts = TypeFacts(kind="typevar", runtime_repr=_tsp_decl_name(typ.get("declaration")) or "")
    elif kind == _TSP_KIND_SYNTHESIZED:
        facts = _tsp_synthesized_type(typ, seen)
    elif kind == _TSP_KIND_OVERLOAD_RESULT:
        facts = _tsp_type(typ.get("baselineType"), seen)
    else:
        facts = TypeFacts(kind="unknown", runtime_repr=str(kind))
    if isinstance(type_id, int):
        seen[type_id] = facts
    return facts


def _tsp_class_type(typ: Mapping[str, Any], seen: dict[int, TypeFacts]) -> TypeFacts:
    """ClassType (kind 3): literal/instantiable/instance forms."""
    decl = typ.get("declaration")
    name = _tsp_decl_name(decl)
    flags = int(typ.get("flags") or 0)
    if name is None:
        stub = typ.get("stubContent")
        match = _TSP_STUB_CLASS.search(str(stub or ""))
        name = match.group("name") if match else "unknown"
    if name == "NoneType":
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    args = tuple(_tsp_type(arg, seen) for arg in typ.get("typeArgs", ()))
    literal = typ.get("literalValue")
    if flags & _TSP_FLAG_LITERAL and literal is not None:
        return TypeFacts(kind="literal", args=(TypeFacts(kind=name),), runtime_repr=repr(literal))
    if name == "tuple" and args:
        return TypeFacts(kind="tuple", args=args, runtime_repr="tuple")
    instance = TypeFacts(
        kind=name,
        args=args,
        protocols=frozenset({f"builtins.{name}"}) if flags & _TSP_FLAG_INTERFACE else frozenset(),
        runtime_repr=name,
    )
    if flags & _TSP_FLAG_INSTANTIABLE:
        return TypeFacts(kind="type", args=(instance,), runtime_repr=f"type[{name}]")
    return instance


def _tsp_param_types(fn: Mapping[str, Any], seen: dict[int, TypeFacts]) -> tuple[list[TypeFacts], dict[str, Any]]:
    """`specializedTypes` parameter/default types when present."""
    specialized = fn.get("specializedTypes")
    if not isinstance(specialized, Mapping):
        return [], {}
    param_types = [_tsp_type(item, seen) for item in specialized.get("parameterTypes", ()) if item is not None]
    return param_types, specialized


def _tsp_function_signature(fn: Mapping[str, Any], seen: dict[int, TypeFacts]) -> CallableFacts:
    """FunctionType -> CallableFacts (params only when specializedTypes lists them)."""
    param_types, _specialized = _tsp_param_types(fn, seen)
    parameters = tuple(
        ParameterFacts(name=f"arg{index}", kind="positional", type=param_type)
        for index, param_type in enumerate(param_types)
    )
    return_type = _tsp_type(fn.get("returnType"), seen)
    return CallableFacts(parameters=parameters, return_type=return_type)


def _tsp_synthesized_type(typ: Mapping[str, Any], seen: dict[int, TypeFacts]) -> TypeFacts:
    """SynthesizedType: recover a def/class from its generated stub."""
    stub = str(typ.get("stubContent") or "")
    metadata = typ.get("metadata")
    offset = metadata.get("primaryDefinitionOffset", 0) if isinstance(metadata, Mapping) else 0
    body = stub[offset:] if isinstance(offset, int) and 0 <= offset < len(stub) else stub
    signature = _TSP_STUB_DEF.search(body)
    if signature is not None:
        parameters, positional_only, keyword_only, defaults = _parse_signature_params(signature.group("params"))
        return_type = _parse_type_text(signature.group("ret").strip()) or TypeFacts(
            kind="unknown", runtime_repr=signature.group("ret").strip() or None
        )
        return TypeFacts(
            kind="callable",
            callable=CallableFacts(
                parameters=parameters,
                return_type=return_type,
                positional_only=positional_only,
                keyword_only=keyword_only,
                defaults=defaults,
            ),
            runtime_repr="synthesized-def",
        )
    cls = _TSP_STUB_CLASS.search(body)
    if cls is not None:
        return TypeFacts(kind=cls.group("name"), runtime_repr="synthesized-class")
    return TypeFacts(kind="unknown", runtime_repr="synthesized")


def _tsp_signature_from_type(typ: Any, seen: dict[int, TypeFacts]) -> TypeFacts | None:
    if typ is None:
        return None
    facts = _tsp_type(typ, seen)
    return facts


class TSPFactsAdapter:
    """Facts backed by `pyright-typeserver` (Type Server Protocol over stdio).

    One long-lived subprocess is spawned lazily and reused across `facts`
    calls; each file is opened via `textDocument/didOpen`, then the adapter
    snapshots the type cache once (`typeServer/getSnapshot`) and queries
    `getComputedType` for every `ast.expr` range and `getDeclaredType` for
    AnnAssign targets and `ast.arg` parameters — structured Type JSON all
    the way, richer than hover strings (unions, literals, overloads,
    specialized generics come through as structured fields). Call nodes get
    no direct answer from the evaluator, so a call expression records its
    callee's return type, mirroring the hover lane. When a response comes
    back stale the adapter re-snapshots and retries the request once.

    `command` overrides the spawn line (default: `PYRIGHT_TYPESERVER_BIN`
    env or `pyright-typeserver` on PATH); `--stdio` is appended when
    absent. `close()` shuts the server down; leaving it to GC also works.
    """

    name = "tsp"

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        raw = command if command is not None else os.environ.get("PYRIGHT_TYPESERVER_BIN")
        argv = shlex.split(raw) if isinstance(raw, str) else list(raw or ["pyright-typeserver"])
        if "--stdio" not in argv:
            argv = [*argv, "--stdio"]
        self._argv = argv
        self._connection: _LspConnection | None = None
        self._opened: set[str] = set()
        self._snapshot: int | None = None

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
        self._open_document(uri, path, source)
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
            self._snapshot = None

    def __enter__(self) -> TSPFactsAdapter:
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
            connection = _LspConnection(self._argv, server_name="pyright typeserver")
            connection.start()
            result = connection.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": None,
                    "capabilities": {"textDocument": {"publishDiagnostics": {}}},
                    "workspaceFolders": None,
                },
                timeout=60,
            )
            if result is None:
                connection.shutdown()
                raise CheckFailed("pyright typeserver refused initialize")
            connection.notify("initialized", {})
            self._connection = connection
        return self._connection

    def _open_document(self, uri: str, path: Path, source: str) -> None:
        connection = self._connect()
        if uri in self._opened:
            return
        self._opened.add(uri)
        connection.notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": path.parent.as_uri(), "name": "pyscriptc"}],
                    "removed": [],
                }
            },
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
        self._snapshot = None

    def _snapshot_now(self, connection: _LspConnection) -> int:
        if self._snapshot is None:
            result = connection.request("typeServer/getSnapshot", None)
            if not isinstance(result, int):
                raise CheckFailed("pyright typeserver refused getSnapshot")
            self._snapshot = result
        return self._snapshot

    def _get_type(
        self,
        connection: _LspConnection,
        method: str,
        arg: Mapping[str, Any],
    ) -> Any:
        """One typeServer request with a single re-snapshot retry."""
        snapshot = self._snapshot_now(connection)
        result = connection.request(f"typeServer/{method}", {"arg": arg, "snapshot": snapshot}, timeout=30)
        if result is None:
            self._snapshot = None
            snapshot = self._snapshot_now(connection)
            result = connection.request(f"typeServer/{method}", {"arg": arg, "snapshot": snapshot}, timeout=30)
        return result

    @staticmethod
    def _node_arg(uri: str, node: ast.AST, lines: list[str]) -> dict[str, Any] | None:
        """TSP Node {uri, range} for an ast node (0-based, UTF-16 cols)."""
        if node.lineno - 1 >= len(lines) or node.end_lineno is None or node.end_col_offset is None:
            return None
        if node.end_lineno - 1 >= len(lines):
            return None
        start_col = _utf16_col(lines[node.lineno - 1], node.col_offset)
        end_col = _utf16_col(lines[node.end_lineno - 1], node.end_col_offset)
        return {
            "uri": uri,
            "range": {
                "start": {"line": node.lineno - 1, "character": start_col},
                "end": {"line": node.end_lineno - 1, "character": end_col},
            },
        }

    def _expression_facts(
        self, connection: _LspConnection, uri: str, tree: ast.Module, source: str
    ) -> dict[str, TypeFacts]:
        lines = source.splitlines()
        facts: dict[str, TypeFacts] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr):
                continue
            key = f"{node.lineno}:{node.col_offset}"
            if key in facts:
                continue
            arg = self._node_arg(uri, node, lines)
            if arg is None:
                continue
            result = self._get_type(connection, "getComputedType", arg)
            facts_type = _tsp_signature_from_type(result, {})
            if facts_type is None and isinstance(node, ast.Call):
                # Call nodes get no direct answer: the callee's FunctionType
                # carries the return type.
                func_arg = self._node_arg(uri, node.func, lines)
                if func_arg is not None:
                    callee = _tsp_signature_from_type(self._get_type(connection, "getComputedType", func_arg), {})
                    if callee is not None and callee.callable is not None:
                        facts_type = callee.callable.return_type
            if facts_type is not None:
                facts[key] = facts_type
        # Declared types for annotated targets: getDeclaredType answers the
        # annotation itself, not the narrowed type a computed query returns.
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or node.annotation is None:
                continue
            arg = self._node_arg(uri, node.target, lines)
            if arg is None:
                continue
            declared = _tsp_signature_from_type(self._get_type(connection, "getDeclaredType", arg), {})
            if declared is not None:
                facts[f"{node.target.lineno}:{node.target.col_offset}"] = declared
        return facts

    def _callable_facts(
        self, connection: _LspConnection, uri: str, tree: ast.Module, source: str
    ) -> tuple[dict[str, CallableFacts], dict[str, tuple[CallableFacts, ...]]]:
        callables: dict[str, CallableFacts] = {}
        overloads: dict[str, tuple[CallableFacts, ...]] = {}

        def visit(body: list[ast.stmt], prefix: str) -> None:
            for stmt in body:
                if isinstance(stmt, ast.ClassDef):
                    visit(stmt.body, f"{prefix}{stmt.name}.")
                    continue
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = f"{prefix}{stmt.name}"
                    signature = self._function_facts(connection, uri, stmt, source.splitlines())
                    if signature is not None:
                        if any(_is_overload_decorator(dec) for dec in stmt.decorator_list):
                            overloads.setdefault(qualname, ())
                            overloads[qualname] += (signature,)
                        else:
                            callables[qualname] = signature
                    visit(stmt.body, f"{prefix}{stmt.name}.<locals>.")

        visit(tree.body, "__main__.")
        return callables, overloads

    def _function_facts(
        self,
        connection: _LspConnection,
        uri: str,
        stmt: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
    ) -> CallableFacts | None:
        """CallableFacts from the def-name FunctionType plus per-arg declared types."""
        marker = "async def " if isinstance(stmt, ast.AsyncFunctionDef) else "def "
        text = lines[stmt.lineno - 1][stmt.col_offset :] if stmt.lineno - 1 < len(lines) else ""
        offset = text.find(marker)
        if offset < 0:
            return None
        name_col = stmt.col_offset + offset + len(marker)
        span = {
            "uri": uri,
            "range": {
                "start": {
                    "line": stmt.lineno - 1,
                    "character": _utf16_col(lines[stmt.lineno - 1], name_col),
                },
                "end": {
                    "line": stmt.lineno - 1,
                    "character": _utf16_col(lines[stmt.lineno - 1], name_col + len(stmt.name.encode("utf-8"))),
                },
            },
        }
        result = self._get_type(connection, "getComputedType", span)
        seen: dict[int, TypeFacts] = {}
        fn = _tsp_signature_from_type(result, seen)
        if fn is None or fn.callable is None:
            return None
        return_type = fn.callable.return_type
        parameters: list[ParameterFacts] = []
        positional_only = len(stmt.args.posonlyargs)
        keyword_only: set[str] = set()
        defaults: dict[str, StaticValue] = {}
        slots: list[tuple[ast.arg, str]] = []
        positional = [*stmt.args.posonlyargs, *stmt.args.args]
        default_exprs = [None] * (len(positional) - len(stmt.args.defaults)) + list(stmt.args.defaults)
        for index, arg in enumerate(positional):
            kind = "positional"
            if default_exprs[index] is not None:
                kind = "positional_optional"
            slots.append((arg, kind))
            if default_exprs[index] is not None:
                try:
                    defaults[arg.arg] = ast.literal_eval(default_exprs[index])
                except _EVAL_ERRORS:
                    pass
        if stmt.args.vararg is not None:
            slots.append((stmt.args.vararg, "varargs"))
        for index, arg in enumerate(stmt.args.kwonlyargs):
            expr = stmt.args.kw_defaults[index]
            kind = "keyword_optional" if expr is not None else "keyword_only"
            keyword_only.add(arg.arg)
            slots.append((arg, kind))
            if expr is not None:
                try:
                    defaults[arg.arg] = ast.literal_eval(expr)
                except _EVAL_ERRORS:
                    pass
        if stmt.args.kwarg is not None:
            slots.append((stmt.args.kwarg, "kwargs"))
        for arg, kind in slots:
            # Declared types answer on the parameter NAME node only; the
            # `arg` node span covers the annotation too and resolves to it.
            if arg.lineno - 1 < len(lines) and arg.end_lineno == arg.lineno:
                name_end = arg.col_offset + len(arg.arg.encode("utf-8"))
                arg_span = {
                    "uri": uri,
                    "range": {
                        "start": {
                            "line": arg.lineno - 1,
                            "character": _utf16_col(lines[arg.lineno - 1], arg.col_offset),
                        },
                        "end": {
                            "line": arg.lineno - 1,
                            "character": _utf16_col(lines[arg.lineno - 1], name_end),
                        },
                    },
                }
            else:
                arg_span = None
            param_type = TypeFacts(kind="unknown")
            if arg_span is not None:
                declared = _tsp_signature_from_type(self._get_type(connection, "getDeclaredType", arg_span), {})
                if declared is not None:
                    param_type = declared
            parameters.append(
                ParameterFacts(
                    name=arg.arg,
                    kind=kind,
                    type=param_type,
                    has_default=arg.arg in defaults or kind in ("positional_optional", "keyword_optional"),
                )
            )
        defaults = {
            name: value
            for name, value in defaults.items()
            if isinstance(value, (int, float, bool, str, bytes)) or value is None
        }
        return CallableFacts(
            parameters=tuple(parameters),
            return_type=return_type,
            positional_only=positional_only,
            keyword_only=frozenset(keyword_only),
            defaults=defaults,
        )
