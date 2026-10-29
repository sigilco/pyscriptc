"""Checker-facts seam.

Lowering never consumes a checker's internal type objects, so pyscriptc owns
a narrow facts IR (the tiered-lowering decision; see `.agents/docs/design/checker-bridge.md`).
A `FactsProvider` reports what the checker decided (optional/union members,
callable signatures, overload choices, generic arguments) and tier-2
lowering queries it. `PyreflyTspFactsAdapter` is the default oracle since
v0.3.0: it drives `pyrefly tsp` for per-node types. `PyreflyFactsAdapter`
(the Pysa-report adapter, `facts_provider("pyrefly")`) stays importable as
the fallback: it reads `pyrefly check --report-pysa` per-module JSON, which
misses nested defs and sub-expressions but needs no long-lived server.
The mypy, pyright (LSP / `--facts`) and pyright-typeserver adapters were
removed in v0.3.1; run those tools externally as cross-check oracles
(`.agents/docs/spec/external-oracles.md`).
"""

from __future__ import annotations

import ast
import json
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

from pyscriptc.check import CheckFailed, _resolve_pyrefly_binary

# A default value a checker could prove constant at compile time.
StaticValue = int | float | bool | str | bytes | None

_MISSING = object()


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

    `expressions` maps "line:col" (1-based line, 0-based column) to the resolved type of the expression starting
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


def facts_provider(checker: str = "pyrefly-tsp") -> FactsProvider:
    """Pick the provider behind a `run_checker` checker name; default `pyrefly-tsp`."""
    if checker == "pyrefly-tsp":
        return PyreflyTspFactsAdapter()
    if checker == "pyrefly":
        return PyreflyFactsAdapter()
    raise CheckFailed(f"checker {checker!r} has no facts adapter; use 'pyrefly-tsp' or 'pyrefly'")


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


def _is_overload_decorator(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id == "overload"
    if isinstance(expr, ast.Attribute):
        return expr.attr == "overload"
    return False


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

    `command` overrides the spawn line (default: the running interpreter's
    pyrefly, then `PYREFLY_BIN`, then `pyrefly` on PATH; see
    `check._resolve_pyrefly_binary`).
    """

    name = "pyrefly"

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        if command is None:
            self._argv = _resolve_pyrefly_binary()
        else:
            self._argv = shlex.split(command) if isinstance(command, str) else list(command)

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
        """The `kind/*.json` report whose `source_path.FileSystem` is `path`.

        pyrefly names each report by the dotted module path (or `__unknown__`
        for targets outside every search root), so the file name cannot be
        matched reliably; the report's own `source_path` can.
        """
        report_dir = Path(outdir) / kind
        for candidate in sorted(report_dir.glob("*.json")):
            try:
                doc = json.loads(candidate.read_text(encoding="utf-8"))
            except _READ_ERRORS:
                continue
            if not isinstance(doc, Mapping):
                continue
            source_path = doc.get("source_path")
            if not isinstance(source_path, Mapping):
                continue
            recorded = source_path.get("FileSystem")
            if recorded is not None and Path(str(recorded)).resolve() == path:
                return doc
        raise CheckFailed(f"pyrefly emitted no {kind} report for {path}")


# ---------------------------------------------------------------------------
# pyrefly tsp wire constants and decoders (TypeServerProtocol, shared by the
# per-node adapter below).

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
        members = tuple(_tsp_type(sub, seen) for sub in typ.get("subTypes") or ())
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
            for variant in typ.get("overloads") or ()
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
    args = tuple(_tsp_type(arg, seen) for arg in typ.get("typeArgs") or ())
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
    param_types = [_tsp_type(item, seen) for item in specialized.get("parameterTypes") or () if item is not None]
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


# ---------------------------------------------------------------------------
# Pyrefly TSP oracle (`pyrefly tsp`, 1.4.x)
#
# `pyrefly/typeFacts` is a pyrefly-specific bulk request: one call answers N
# expression ranges with a lossy JSON type (`kind` instance|class|union|
# function|module|any|other), NOT the upstream TSP Type shape. Callable
# signatures come from per-node `typeServer/getComputedType`, which does
# return the full TSP Type (decoded by `_tsp_type`). Opening a document
# advances the snapshot; a request carrying the pre-open snapshot silently
# returns nulls, so the snapshot is re-read after every didOpen/didChange.

_PYREFLY_TSP_VERSION_PREFIX = "0.4."
_TYPEFACTS_CHUNK = 1500


def _tsf_denote(facts: TypeFacts) -> TypeFacts:
    """Annotation position: a `class` fact (type[X]) denotes the instance X."""
    if facts.kind == "type" and facts.args:
        return facts.args[0]
    if facts.kind == "union":
        members = tuple(_tsf_denote(member) for member in facts.args)
        return TypeFacts(
            kind="union",
            optional=any(m.kind == "none" or m.optional for m in members),
            args=members,
            runtime_repr=facts.runtime_repr,
        )
    return facts


def _tsf_instance(qname: str, args: tuple[TypeFacts, ...], display: str | None = None) -> TypeFacts:
    name = _short_name(qname)
    if name == "NoneType":
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    if name == "tuple" and args:
        return TypeFacts(kind="tuple", args=args, runtime_repr=display or "tuple")
    return TypeFacts(kind=name, args=args, runtime_repr=display or name)


def _tsf_type(raw: Any) -> TypeFacts | None:
    """Decode one `pyrefly/typeFacts` entry; None when the oracle gave no answer."""
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    if kind == "instance":
        qname = raw.get("qname")
        if not isinstance(qname, str):
            return None
        args = tuple(
            t if (t := _tsf_type(a)) is not None else TypeFacts(kind="unknown") for a in raw.get("typeArgs") or ()
        )
        return _tsf_instance(qname, args)
    if kind == "class":
        qname = raw.get("qname")
        if not isinstance(qname, str):
            return None
        inner = _tsf_instance(qname, ())
        return TypeFacts(kind="type", args=(inner,), runtime_repr=f"type[{inner.runtime_repr}]")
    if kind == "union":
        members = tuple(
            t if (t := _tsf_type(m)) is not None else TypeFacts(kind="unknown") for m in raw.get("members") or ()
        )
        return TypeFacts(
            kind="union",
            optional=any(m.kind == "none" or m.optional for m in members),
            args=members,
            runtime_repr="union",
        )
    if kind == "function":
        returns = _tsf_type(raw.get("returns")) or TypeFacts(kind="unknown")
        return TypeFacts(
            kind="callable",
            callable=CallableFacts(parameters=(), return_type=returns),
            runtime_repr=str(raw.get("qname") or "function"),
        )
    if kind == "module":
        return TypeFacts(kind="module", runtime_repr=str(raw.get("name") or ""))
    if kind == "any":
        return TypeFacts(kind="any", runtime_repr="Any")
    if kind == "other":
        display = raw.get("display")
        if not isinstance(display, str):
            return TypeFacts(kind="unknown")
        parsed = _parse_type_text(display)
        return parsed if parsed is not None else TypeFacts(kind="unknown", runtime_repr=display)
    return None


def _lsp_range(node: ast.AST, lines: list[str]) -> dict[str, Any] | None:
    """LSP range (0-based line, UTF-16 character) for an ast node."""
    end_lineno = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    lineno = getattr(node, "lineno", None)
    if lineno is None or end_lineno is None or end_col is None:
        return None
    if lineno - 1 >= len(lines) or end_lineno - 1 >= len(lines):
        return None
    return {
        "start": {"line": lineno - 1, "character": _utf16_col(lines[lineno - 1], node.col_offset)},
        "end": {"line": end_lineno - 1, "character": _utf16_col(lines[end_lineno - 1], end_col)},
    }


def _def_name_range(stmt: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str]) -> dict[str, Any] | None:
    if stmt.lineno - 1 >= len(lines):
        return None
    line = lines[stmt.lineno - 1]
    raw = line.encode("utf-8")
    marker = b"async def " if isinstance(stmt, ast.AsyncFunctionDef) else b"def "
    at = raw.find(marker, stmt.col_offset)
    if at < 0:
        return None
    start = at + len(marker)
    end = start + len(stmt.name.encode("utf-8"))
    return {
        "start": {"line": stmt.lineno - 1, "character": _utf16_col(line, start)},
        "end": {"line": stmt.lineno - 1, "character": _utf16_col(line, end)},
    }


def _arg_slots(
    args: ast.arguments,
) -> tuple[list[tuple[ast.arg, str, ast.expr | None]], int, frozenset[str]]:
    """(arg, kind, default expr) in declaration order, positional-only count, keyword-only names."""
    slots: list[tuple[ast.arg, str, ast.expr | None]] = []
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults, strict=True):
        slots.append((arg, "positional" if default is None else "positional_optional", default))
    if args.vararg is not None:
        slots.append((args.vararg, "varargs", None))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        slots.append((arg, "keyword_only" if default is None else "keyword_optional", default))
    if args.kwarg is not None:
        slots.append((args.kwarg, "kwargs", None))
    return slots, len(args.posonlyargs), frozenset(a.arg for a in args.kwonlyargs)


class PyreflyTspFactsAdapter:
    """Facts backed by a long-lived `pyrefly tsp` subprocess.

    Expression types come from the bulk `pyrefly/typeFacts` request (one
    query per `ast.expr` range, outermost expression per position, same
    "line:col" keys as the other adapters). `callables` / `overloads` come
    from three sources: each `def` (parameter types from the annotation
    expressions, since parameter nodes return null; the return type from the
    annotation or the def name's inferred function type), plus per-call-site
    `typeServer/getComputedType` on the callee for the full signature or
    overload set of anything called (keyed by the callee's qualified name,
    with the module's own name rewritten to `__main__`).

    The startup probe `typeServer/getSupportedProtocolVersion` must report
    0.4.x; anything else raises CheckFailed rather than risk silent nulls.
    `command` overrides the spawn line; default is `_resolve_pyrefly_binary()`
    plus `tsp`.
    """

    name = "pyrefly-tsp"

    def __init__(self, command: str | Sequence[str] | None = None) -> None:
        argv = (
            _resolve_pyrefly_binary()
            if command is None
            else (shlex.split(command) if isinstance(command, str) else list(command))
        )
        self._argv = [*argv, "tsp"] if "tsp" not in argv else list(argv)
        self._connection: _LspConnection | None = None
        self._opened: dict[str, tuple[int, str]] = {}

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
        snapshot = self._open_document(connection, uri, source)
        lines = source.splitlines()

        annotation_ids: set[int] = set()
        for node in ast.walk(tree):
            for ann in _annotation_exprs(node):
                annotation_ids.update(id(sub) for sub in ast.walk(ann))

        # One batched query list; `slot` maps a query index back to its purpose.
        queries: list[dict[str, Any]] = []
        expr_slots: dict[str, int] = {}
        node_slots: dict[int, int] = {}

        def add(rng: dict[str, Any] | None) -> int | None:
            if rng is None:
                return None
            queries.append({"range": rng, "expected": False})
            return len(queries) - 1

        for node in ast.walk(tree):
            if not isinstance(node, ast.expr):
                continue
            idx = add(_lsp_range(node, lines))
            if idx is None:
                continue
            node_slots[id(node)] = idx
            key = f"{node.lineno}:{node.col_offset}"
            if id(node) not in annotation_ids and key not in expr_slots:
                expr_slots[key] = idx
        defs: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, int | None]] = []

        def collect(body: list[ast.stmt], prefix: str) -> None:
            for stmt in body:
                if isinstance(stmt, ast.ClassDef):
                    collect(stmt.body, f"{prefix}{stmt.name}.")
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.append((f"{prefix}{stmt.name}", stmt, add(_def_name_range(stmt, lines))))
                    collect(stmt.body, f"{prefix}{stmt.name}.<locals>.")

        collect(tree.body, "__main__.")
        raw = self._type_facts(connection, uri, snapshot, queries)
        decoded = [_tsf_type(item) for item in raw]

        expressions = {key: facts for key, idx in expr_slots.items() if (facts := decoded[idx]) is not None}
        callables, overloads = self._def_facts(defs, decoded, node_slots)
        callees = [
            (node.func, node_slots[id(node.func)])
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and id(node.func) in node_slots
        ]
        self._call_site_facts(connection, uri, snapshot, lines, callees, raw, callables, overloads, path.stem)
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

    def __enter__(self) -> PyreflyTspFactsAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _connect(self) -> _LspConnection:
        if self._connection is not None:
            return self._connection
        connection = _LspConnection(self._argv, server_name="pyrefly tsp")
        connection.start()
        initialized = connection.request(
            "initialize",
            {"processId": None, "rootUri": None, "capabilities": {}, "workspaceFolders": None},
            timeout=60,
        )
        if initialized is None:
            connection.shutdown()
            raise CheckFailed("pyrefly tsp refused initialize")
        connection.notify("initialized", {})
        version = connection.request("typeServer/getSupportedProtocolVersion", None, timeout=30)
        if not isinstance(version, str) or not version.startswith(_PYREFLY_TSP_VERSION_PREFIX):
            connection.shutdown()
            raise CheckFailed(
                f"pyrefly tsp speaks protocol {version!r}, expected {_PYREFLY_TSP_VERSION_PREFIX}x; "
                "install pyrefly>=1.4.0,<1.5"
            )
        self._connection = connection
        return connection

    def _open_document(self, connection: _LspConnection, uri: str, source: str) -> int:
        """Open (or refresh) the document, then re-read the snapshot it advanced."""
        previous = self._opened.get(uri)
        if previous is None:
            connection.notify(
                "textDocument/didOpen",
                {"textDocument": {"uri": uri, "languageId": "python", "version": 1, "text": source}},
            )
            self._opened[uri] = (1, source)
        elif previous[1] != source:
            version = previous[0] + 1
            connection.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": source}],
                },
            )
            self._opened[uri] = (version, source)
        return self._snapshot_now(connection)

    @staticmethod
    def _snapshot_now(connection: _LspConnection) -> int:
        snapshot = connection.request("typeServer/getSnapshot", None, timeout=30)
        if not isinstance(snapshot, int):
            raise CheckFailed("pyrefly tsp refused getSnapshot")
        return snapshot

    def _type_facts(
        self, connection: _LspConnection, uri: str, snapshot: int, queries: list[dict[str, Any]]
    ) -> list[Any]:
        results: list[Any] = []
        for start in range(0, len(queries), _TYPEFACTS_CHUNK):
            chunk = queries[start : start + _TYPEFACTS_CHUNK]
            params = {"uri": uri, "snapshot": snapshot, "queries": chunk}
            answer = connection.request("pyrefly/typeFacts", params, timeout=120)
            if not isinstance(answer, list) or len(answer) != len(chunk):
                raise CheckFailed("pyrefly tsp returned a malformed pyrefly/typeFacts answer")
            if all(item is None for item in answer) and len(chunk) > 1:
                # A stale snapshot answers all-null; re-read once and retry.
                params["snapshot"] = snapshot = self._snapshot_now(connection)
                answer = connection.request("pyrefly/typeFacts", params, timeout=120)
                if not isinstance(answer, list) or len(answer) != len(chunk):
                    raise CheckFailed("pyrefly tsp returned a malformed pyrefly/typeFacts answer")
            results.extend(answer)
        return results

    def _def_facts(
        self,
        defs: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, int | None]],
        decoded: list[TypeFacts | None],
        node_slots: dict[int, int],
    ) -> tuple[dict[str, CallableFacts], dict[str, tuple[CallableFacts, ...]]]:
        callables: dict[str, CallableFacts] = {}
        overloads: dict[str, tuple[CallableFacts, ...]] = {}

        def annotation(node: ast.expr | None) -> TypeFacts | None:
            idx = node_slots.get(id(node)) if node is not None else None
            fact = decoded[idx] if idx is not None else None
            return _tsf_denote(fact) if fact is not None else None

        for qualname, stmt, name_idx in defs:
            slots, positional_only, keyword_only = _arg_slots(stmt.args)
            parameters: list[ParameterFacts] = []
            default_values: dict[str, StaticValue] = {}
            for arg, kind, default in slots:
                parameters.append(
                    ParameterFacts(
                        name=arg.arg,
                        kind=kind,
                        type=annotation(arg.annotation) or TypeFacts(kind="unknown"),
                        has_default=default is not None,
                    )
                )
                if default is not None:
                    try:
                        value = ast.literal_eval(default)
                    except _EVAL_ERRORS:
                        continue
                    if value is None or isinstance(value, (int, float, bool, str, bytes)):
                        default_values[arg.arg] = value
            return_type = annotation(stmt.returns)
            if return_type is None and name_idx is not None:
                named = decoded[name_idx]
                if named is not None and named.callable is not None:
                    return_type = named.callable.return_type
            signature = CallableFacts(
                parameters=tuple(parameters),
                return_type=return_type or TypeFacts(kind="unknown"),
                positional_only=positional_only,
                keyword_only=keyword_only,
                defaults=default_values,
            )
            if any(_is_overload_decorator(dec) for dec in stmt.decorator_list):
                overloads[qualname] = (*overloads.get(qualname, ()), signature)
            else:
                callables[qualname] = signature
        return callables, overloads

    def _call_site_facts(
        self,
        connection: _LspConnection,
        uri: str,
        snapshot: int,
        lines: list[str],
        callees: list[tuple[ast.expr, int]],
        raw: list[Any],
        callables: dict[str, CallableFacts],
        overloads: dict[str, tuple[CallableFacts, ...]],
        stem: str,
    ) -> None:
        """Recover signatures of called things via per-callee `getComputedType`."""
        seen: set[str] = set()
        for func, idx in callees:
            reply = raw[idx]
            if not isinstance(reply, Mapping) or reply.get("kind") != "function":
                continue
            qname = str(reply.get("qname") or "")
            if qname.startswith(f"{stem}."):
                qname = "__main__." + qname[len(stem) + 1 :]
            if not qname or qname in seen or qname in callables or qname in overloads:
                continue
            seen.add(qname)
            rng = _lsp_range(func, lines)
            computed = connection.request(
                "typeServer/getComputedType",
                {"arg": {"uri": uri, "range": rng}, "snapshot": snapshot},
                timeout=30,
            )
            if computed is None:
                continue
            facts = _tsp_type(computed, {})
            if facts.callable is not None:
                callables[qname] = facts.callable
            elif facts.kind == "overloaded":
                variants = tuple(v.callable for v in facts.args if v.callable is not None)
                if variants:
                    overloads[qname] = variants


def _annotation_exprs(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.arg) and node.annotation is not None:
        return [node.annotation]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
        return [node.returns]
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    return []
