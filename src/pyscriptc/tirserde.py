"""Streaming canonical serializer over the dict JSON IR (TIR gate bootstrap).

Byte-identical to the canonical payload `pyscriptc.canonical.hash_module`
digests today, namely

    json.dumps({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)},
               sort_keys=True, separators=(",", ":"), ensure_ascii=False,
               allow_nan=False).encode("utf-8")

but the serialization walks the canonical structure once and feeds SHA-256
directly, so the full payload string is never materialized. This is the
serializer the TIR_HASHES gate consumes (spec: the "Proposed" section of
.agents/docs/spec/ir-canonicity.md); `canonical.py` itself is frozen and the
old IR_HASHES gate keeps reading it until the recorded retirement decision.

Byte identity is enforced per probe by `benchmarks.compat2.tirscore
--verify-bytes`, which `cmp`s the streaming bytes against the json.dumps
bytes on the whole visible corpus.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from pyscriptc._irnode import (
    F_BARE,
    F_FROZEN,
    F_IDENTITY,
    F_NEGATED,
    F_RERAISE,
    F_SET,
    F_TOPT,
    F_TSET,
    P_BOOL,
    P_NONE,
)
from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION, canonicalize

# json.dumps short escapes for the C0 controls it does not write as \uXXXX.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}

_HEX = "0123456789abcdef"


class Serializer:
    """Feeds one canonical payload into a sink (hashlib or bytearray).

    The output is exactly the json.dumps payload described in the module
    docstring, produced incrementally: object keys are emitted in Python
    string sort order, strings are UTF-8 with json's ensure_ascii=False
    escaping, floats must be finite (allow_nan=False semantics).
    """

    __slots__ = ("_sink",)

    def __init__(self, sink: Any) -> None:
        self._sink = sink

    def _write(self, chunk: bytes) -> None:
        self._sink(chunk)

    def write_any(self, value: Any) -> None:
        if value is None:
            self._write(b"null")
        elif value is True:
            self._write(b"true")
        elif value is False:
            self._write(b"false")
        elif isinstance(value, str):
            self.write_str(value)
        elif isinstance(value, int):
            self._write(str(value).encode("ascii"))
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(
                    "a non-finite float reached the canonical IR; json.dumps(allow_nan=False) would refuse it too"
                )
            self._write(repr(value).encode("ascii"))
        elif isinstance(value, dict):
            self.write_object(value)
        elif isinstance(value, (list, tuple)):
            self.write_array(value)
        else:
            raise TypeError(f"the canonical IR carries a {type(value).__name__}; json.dumps would refuse it")

    def write_object(self, obj: dict[str, Any]) -> None:
        self._write(b"{")
        first = True
        for key in sorted(obj):
            if not first:
                self._write(b",")
            first = False
            self.write_str(key)
            self._write(b":")
            self.write_any(obj[key])
        self._write(b"}")

    def write_array(self, items: Any) -> None:
        self._write(b"[")
        first = True
        for item in items:
            if not first:
                self._write(b",")
            first = False
            self.write_any(item)
        self._write(b"]")

    def write_str(self, text: str) -> None:
        self._write(b'"')
        start = 0
        for index, char in enumerate(text):
            code = ord(char)
            if code == 0x22:
                escape = '\\"'
            elif code == 0x5C:
                escape = "\\\\"
            elif code < 0x20:
                escape = _SHORT_ESCAPES.get(code)
                if escape is None:
                    # json's \\u%04x form, lower case hex digits.
                    escape = (
                        "\\u" + _HEX[code >> 12] + _HEX[(code >> 8) & 0xF] + _HEX[(code >> 4) & 0xF] + _HEX[code & 0xF]
                    )
            else:
                continue
            if start < index:
                self._write(text[start:index].encode("utf-8"))
            self._write(escape.encode("ascii"))
            start = index + 1
        if start < len(text):
            self._write(text[start:].encode("utf-8"))
        self._write(b'"')


def payload_bytes(module: dict[str, Any]) -> bytes:
    """The canonical payload bytes, built incrementally into one buffer.

    Byte-identical to the json.dumps payload hash_module digests; used by
    the byte-identity verifier and by tooling that wants the stream itself.
    """
    chunks: list[bytes] = []
    stream = Serializer(chunks.append)
    stream.write_object({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)})
    return b"".join(chunks)


def hash_module(module: dict[str, Any]) -> str:
    """SHA-256 hex of the canonical payload, streamed; never materializes it.

    Same digest contract as pyscriptc.canonical.hash_module at the same
    CANONICAL_SCHEMA_VERSION; `tirscore --verify-bytes` proves the bytes
    are identical on the corpus.
    """
    digest = hashlib.sha256()
    stream = Serializer(digest.update)
    stream.write_object({"schema": CANONICAL_SCHEMA_VERSION, "ir": canonicalize(module)})
    return digest.hexdigest()


# --- typed-node reconstruction ------------------------------------------
#
# `pyscriptc._irnode` carries the typed HIR (R3 slice one). The dataclass
# module cannot host the dict reconstruction itself: a dict value is
# strictly type-checked (`dict value is X, annotation says Y`), so no
# lowered annotation describes the heterogeneous serializer dict, and
# this module already speaks `Any`. One visitor per record shape; the
# output is the exact dict the lowerer builds today, key for key, so the
# canonical bytes are identical either way (tirscore --verify-bytes).

_ABSENT: Any = object()


def _nd_key(node: Any, key: str) -> Any:
    """One output key of a node, or _ABSENT when the key is not emitted."""
    kind = node.kind
    if key == "kind":
        return kind
    if key == "span":
        return (
            None
            if node.span is None
            else {
                "file": node.span.file,
                "line": node.span.line,
                "col": node.span.col,
                "endLine": node.span.endLine,
                "endCol": node.span.endCol,
            }
        )
    if key == "type":
        return _ABSENT if node.type is None else _type_dict(node.type)
    if key == "value":
        if kind == "const":
            return _const_value(node)
        return _ABSENT if node.value is None else node.value
    if key == "else":
        return _ABSENT if node.else_ is None else node.else_
    if key == "tuple":
        return _ABSENT if node.tuple_ == "" else node.tuple_
    if key == "negated":
        # The dict IR always spells negated on contains (ir.py validates it);
        # the bit's absence reads as False.
        return bool(node.flags & F_NEGATED)
    if key == "set":
        return True if node.flags & F_SET else _ABSENT
    if key == "identity":
        return True if node.flags & F_IDENTITY else _ABSENT
    if key == "reraise":
        return True if node.flags & F_RERAISE else _ABSENT
    if key == "bare":
        return True if node.flags & F_BARE else _ABSENT
    if key == "frozen":
        return True if node.flags & F_FROZEN else _ABSENT
    if key == "message":
        return None if node.message is None else node.message
    if key == "code":
        return _ABSENT if node.code == -1 else node.code
    if key == "format_kwnames":
        return _ABSENT if node.format_kwnames == -1 else node.format_kwnames
    if key == "format_style":
        return _ABSENT if node.format_style == "" else node.format_style
    if key == "target" and kind == "value":
        return _ABSENT if node.target_node is None else node.target_node
    if key == "target":
        return _ABSENT if node.target == "" else node.target
    if key in {"args", "elems", "then", "body", "orelse", "finalbody"}:
        return _ABSENT if getattr(node, key) is None else getattr(node, key)
    if key == "env":
        return _ABSENT if node.env is None else node.env
    if key == "pairs":
        return _ABSENT if node.pairs is None else node.pairs
    if key == "handlers":
        return _ABSENT if node.handlers is None else node.handlers
    if key in {"stub", "thread_stub"}:
        return _ABSENT if getattr(node, key) is None else getattr(node, key)
    if key == "thread":
        return _ABSENT if node.thread is None else node.thread
    if key == "cause":
        return _ABSENT if node.cause is None else node.cause
    if key == "container":
        return _ABSENT if node.object is None else node.object
    # setitem spells the container as node.object too.
    if (
        key == "cond"
        or key == "expr"
        or key == "object"
        or key == "callee"
        or key == "operand"
        or key == "left"
        or key == "right"
        or key == "hay"
        or key == "needle"
        or key == "index"
    ):
        child = getattr(node, key)
        return _ABSENT if child is None else child
    text = getattr(node, key)
    return _ABSENT if text == "" else text


def _const_value(node: Any) -> Any:
    """The JSON value the dict IR stores for this const payload."""
    if node.ptag == P_BOOL:
        return node.svalue == "true"
    if node.ptag == P_NONE:
        return None
    return node.svalue


def _type_dict(typ: Any) -> Any:
    """The type dict a `T` record spells."""
    out: dict[str, Any] = {"kind": typ.kind}
    if typ.kind == "class":
        out["name"] = typ.name
        if typ.label != "":
            out["label"] = typ.label
        if typ.elem is not None:
            out["elem"] = _type_dict(typ.elem)
        if typ.flags & F_TOPT:
            out["optional"] = True
        return out
    if typ.kind == "list":
        if typ.elem is not None:
            out["elem"] = _type_dict(typ.elem)
        if typ.flags & F_TSET:
            out["set"] = True
        return out
    if typ.kind == "dict":
        if typ.key is not None:
            out["key"] = _type_dict(typ.key)
        if typ.value is not None:
            out["value"] = _type_dict(typ.value)
        return out
    if typ.kind == "func":
        out["params"] = [_type_dict(param) for param in typ.params or []]
        if typ.ret is not None:
            out["return"] = _type_dict(typ.ret)
    return out


def _env_dict(item: Any) -> Any:
    if item.name != "":
        return {"name": item.name, "type": _type_dict(item.type)}
    return {"init": node_to_dict(item.init), "type": _type_dict(item.type)}


def _stub_dict(stub: Any) -> Any:
    return {
        "bound": [_type_dict(item) for item in stub.bound],
        "params": [_type_dict(item) for item in stub.params],
        "return": _type_dict(stub.ret),
    }


def _thread_dict(meta: Any) -> Any:
    out: dict[str, Any] = {}
    if meta.daemon:
        out["daemon"] = meta.daemon
    if meta.name != "":
        out["name"] = meta.name
    if meta.closure is not None:
        out["closure"] = node_to_dict(meta.closure)
    if meta.result is not None:
        out["result"] = _type_dict(meta.result)
        out["size"] = meta.size
    return out


def _handler_dict(handler: Any) -> Any:
    out: dict[str, Any] = {
        "exc": handler.exc,
        "codes": handler.codes,
        "body": [node_to_dict(item) for item in handler.body],
    }
    if handler.flags & F_BARE:
        out["bare"] = True
    if handler.excslot != "":
        out["excslot"] = handler.excslot
    if handler.bind != "":
        out["bind"] = handler.bind
    return out


# Output keys per node kind, the exact insertion order the lowerer's dict
# literals use; the serializer sorts keys, so only presence and value are
# the contract.
_NODE_KEYS: dict[str, tuple[str, ...]] = {
    "assign": ("kind", "target", "value", "span"),
    "attr": ("kind", "name", "type", "value", "span"),
    "binary": ("kind", "left", "op", "right", "type", "span"),
    "builtin": ("kind", "name"),
    "call": (
        "kind",
        "callee",
        "args",
        "type",
        "span",
        "symbol",
        "format_style",
        "format_kwnames",
        "identity",
        "thread",
    ),
    "cast": ("kind", "op", "value", "type", "span"),
    "class": ("kind", "name", "label"),
    "closure": ("kind", "name", "env", "type", "stub", "thread_stub", "span"),
    "compare": ("kind", "left", "op", "right", "type", "span"),
    "contains": ("kind", "hay", "needle", "negated", "type", "span"),
    "const": ("kind", "value", "type", "span"),
    "dict": ("kind", "pairs", "type", "span"),
    "excmake": ("kind", "exc", "code", "message", "span"),
    "expr": ("kind", "expr", "span"),
    "function": ("kind", "name"),
    "if": ("kind", "cond", "then", "else", "span"),
    "list": ("kind", "elems", "set", "frozen", "type", "span"),
    "method": ("kind", "receiver", "name", "tuple"),
    "module": ("kind", "module", "name"),
    "name": ("kind", "id", "type", "span"),
    "pass": ("kind", "span"),
    "raise": ("kind", "exc", "code", "message", "reraise", "excslot", "cause", "span"),
    "return": ("kind", "value", "span"),
    "setattr": ("kind", "object", "name", "value", "span"),
    "setitem": ("kind", "container", "index", "value", "span"),
    "subscript": ("kind", "value", "index", "type", "span"),
    "try": ("kind", "body", "handlers", "orelse", "brk", "cont", "finalbody", "span"),
    "unary": ("kind", "operand", "op", "type", "span"),
    "value": ("kind", "target"),
    "while": ("kind", "cond", "body", "span"),
}


def node_to_dict(value: Any) -> Any:
    """The dict IR one typed value spells: nodes, records, lists, dicts.

    A plain dict/list/str/int/bool/None passes through; an `_irnode`
    record reconstructs its dict. Returns the exact dict the lowerer
    builds today, key for key.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: node_to_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [node_to_dict(item) for item in value]
    kind = getattr(value, "kind", None)
    if kind is None and hasattr(value, "bound"):
        # Stub: the partial/thread shim spec (before T: a Stub also has params/ret).
        return _stub_dict(value)
    if (
        hasattr(value, "ret")
        and hasattr(value, "params")
        and not hasattr(value, "bound")
        and not hasattr(value, "span")
    ):
        # T: a type dict passed at the top level.
        return _type_dict(value)
    if kind is None and hasattr(value, "key") and hasattr(value, "value"):
        # Pair: one dict-literal entry.
        return {"key": node_to_dict(value.key), "value": node_to_dict(value.value)}
    if kind is None and hasattr(value, "type") and (hasattr(value, "init") or hasattr(value, "name")):
        # EnvItem: a closure env entry.
        return _env_dict(value)
    if (
        kind is None
        and hasattr(value, "result")
        and hasattr(value, "size")
        or (kind is None and hasattr(value, "daemon"))
    ):
        # ThreadMeta.
        return _thread_dict(value)
    if kind is None:
        raise TypeError(f"the canonical IR carries a {type(value).__name__}; json.dumps would refuse it")
    if kind in {"excmake", "try"} and hasattr(value, "body"):
        if kind == "excmake":
            out: dict[str, Any] = {"kind": kind, "exc": value.exc}
            if value.code != -1:
                out["code"] = value.code
            out["message"] = None if value.message is None else node_to_dict(value.message)
            if value.span is not None:
                out["span"] = _span_dict(value.span)
            return out
        return _try_dict(value)
    if not hasattr(value, "span"):
        # A callee record (function/method/module/class/builtin/value) or
        # a handler/env/stub/thread record handled by its parent.
        return _callee_or_record(value)
    return _node_dict(value)


def _node_dict(node: Any) -> Any:
    out: dict[str, Any] = {}
    for key in _NODE_KEYS.get(node.kind, ()):
        val = _nd_key(node, key)
        if val is _ABSENT:
            continue
        out[key] = node_to_dict(val)
    return out


def _span_dict(span: Any) -> Any:
    return {
        "file": span.file,
        "line": span.line,
        "col": span.col,
        "endLine": span.endLine,
        "endCol": span.endCol,
    }


def _try_dict(node: Any) -> Any:
    out: dict[str, Any] = {
        "kind": "try",
        "body": [node_to_dict(item) for item in node.body or []],
        "handlers": [_handler_dict(handler) for handler in node.handlers or []],
    }
    if node.orelse is not None:
        out["orelse"] = [node_to_dict(item) for item in node.orelse]
        if node.brk != "":
            out["brk"] = node.brk
        if node.cont != "":
            out["cont"] = node.cont
    if node.finalbody is not None:
        out["finalbody"] = [node_to_dict(item) for item in node.finalbody]
    if node.span is not None:
        out["span"] = _span_dict(node.span)
    return out


def _callee_or_record(value: Any) -> Any:
    # Handler: exc list of names.
    if hasattr(value, "codes"):
        return _handler_dict(value)
    # EnvItem: init or name plus type.
    if hasattr(value, "init") or (hasattr(value, "name") and hasattr(value, "type") and not hasattr(value, "kind")):
        if value.name != "":
            return {"name": value.name, "type": _type_dict(value.type)}
        return {"init": node_to_dict(value.init), "type": _type_dict(value.type)}
    # Stub.
    if hasattr(value, "bound"):
        return _stub_dict(value)
    # ThreadMeta.
    if hasattr(value, "daemon") or (hasattr(value, "result") and hasattr(value, "size")):
        return _thread_dict(value)
    # Callee records: builtin / function / class / method / module / value.
    if hasattr(value, "receiver"):
        out: dict[str, Any] = {"kind": "method", "receiver": value.receiver, "name": value.name}
        if value.tuple_ != "":
            out["tuple"] = value.tuple_
        return out
    if hasattr(value, "module"):
        return {"kind": "module", "module": value.module, "name": value.name}
    if hasattr(value, "target_node"):
        return {"kind": "value", "target": node_to_dict(value.target_node)}
    if value.kind == "excmake":
        out = {"kind": "excmake", "exc": value.exc}
        if value.code != -1:
            out["code"] = value.code
        out["message"] = None if value.message is None else node_to_dict(value.message)
        if value.span is not None:
            out["span"] = _span_dict(value.span)
        return out
    out = {"kind": value.kind, "name": value.name}
    if value.label != "":
        out["label"] = value.label
    return out


def payload_bytes_node(module: Any) -> bytes:
    """The canonical payload bytes of a typed-node module.

    Byte-identical to payload_bytes over the equivalent dict IR: the
    typed nodes reconstruct the exact dicts before the stream sees them.
    """
    return payload_bytes(node_to_dict(module))


def hash_module_node(module: Any) -> str:
    """SHA-256 hex of the canonical payload of a typed-node module."""
    return hash_module(node_to_dict(module))
