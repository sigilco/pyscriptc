"""Typed-node hashing in schema field order (recorded decision #830, P2).

`pyscriptc.tirserde.hash_module_node` used to hash typed nodes by
reconstructing the dict IR (`node_to_dict`) and running it through
`canonicalize` plus the `sort_keys` JSON payload. This module replaces that
role for TIR hashing: the typed records are walked directly, field by field,
in the schema field order of each record shape, and the bytes are streamed
into SHA-256 without ever materializing a dict.

The four canonicity rules `canonicalize` used to own now live here, and the
typed serializer is their owner:

- the incidental keys (`span`, `sourceFile`, `cpython`, `_notes`) are never
  emitted: the typed walker has no place for them, so they are gone;
- `classes` is sorted by `name` (the synthetic-class set leak);
- every try-handler's `codes` list is sorted (the same leak);
- `fences` is totally ordered by `(code, message, function or "", span.line,
  span.col)`, computed before the spans are dropped;
- environment absolute paths in string values become `<PATH>`.

The byte contract is unchanged: the payload is still
`{"schema": CANONICAL_SCHEMA_VERSION, "ir": <module>}` in `json.dumps`
shape, so the digests stay comparable with the frozen TIR baseline. Only
the walk changed: typed records instead of dicts, schema field order instead
of `sort_keys`.
"""

from __future__ import annotations

import hashlib
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyscriptc._irnode import (
    F_BARE,
    F_FROZEN,
    F_IDENTITY,
    F_NEGATED,
    F_RERAISE,
    F_SET,
    F_TMARK_COUNTER,
    F_TMARK_DEFAULTDICT,
    F_TMARK_HANDLE,
    F_TMARK_ISLAND,
    F_TMARK_ORDERED,
    F_TOPT,
    F_TSET,
    P_BOOL,
    P_NONE,
)
from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION

# Schema/2: environment-derived absolute paths reaching IR string constants
# are replaced with this placeholder so the digest cannot depend on the
# checkout or venv location (canonical.py, decision 2026-10-16). The rule
# moves here with the rest of the canonicalization.
_PATH_PLACEHOLDER = "<PATH>"

# The typed module is a dict of module-level sections today (the module
# record is still assembled by the lowerer as a dict of typed records), so
# the typed walker reads the same section names the dict IR carries. The
# sections themselves are walked without a dict reconstruction.
# The module record's schema field order. The incidental keys
# (`cpython`, `sourceFile`) and the notes (`_notes`) are never emitted:
# the typed walker has no place for them.
_MODULE_SECTIONS = ("irVersion", "subset", "entry", "functions", "classes", "fences")

# The function record's schema field order. `boxed` and `env` are the
# optional sections the lowerer adds to a function.
_FUNCTION_SECTIONS = ("name", "params", "returnType", "locals", "body", "boxed", "env")

# The class record's schema field order. The exception class carries the
# exception-class extras.
_CLASS_SECTIONS = ("name", "fields", "methods", "exception", "exc_code", "exc_parent")

# The param/field record's schema field order. The argument extras
# (`kwonly`, `posonly`, `vararg`, `kwarg`) and the default value ride
# along.
_PARAM_SECTIONS = ("name", "type", "default", "kwonly", "posonly", "vararg", "kwarg")

# The local slot record's schema field order.
_LOCAL_SECTIONS = ("name", "type")

# The fence record's schema field order. The span is incidental.
_FENCE_SECTIONS = ("code", "message", "function")


def payload_bytes_node(module: Any) -> bytes:
    """The canonical payload bytes of a typed-node module.

    Byte-identical to `tirserde.payload_bytes` over the equivalent dict IR:
    the typed walker emits the same JSON shape in the same schema field
    order, without building the dict.
    """
    chunks: list[bytes] = []
    _write_module(chunks.append, module)
    return b"".join(chunks)


def hash_module_node(module: Any) -> str:
    """SHA-256 hex of the canonical payload of a typed-node module."""
    digest = hashlib.sha256(b"")
    _write_module(digest.update, module)
    return digest.hexdigest()


# --- the typed walk ------------------------------------------------------


def _write_module(write, module: Any) -> None:
    """The canonical payload object: `{"schema": ..., "ir": <module>}`."""
    write(b"{")
    _write_str(write, "schema")
    write(b":")
    _write_str(write, CANONICAL_SCHEMA_VERSION)
    write(b",")
    _write_str(write, "ir")
    write(b":")
    _write_module_body(write, module)
    write(b"}")


def _write_module_body(write, module: Any) -> None:
    """The module record: the canonical keys in schema field order.

    The module is a dict of sections (the lowerer still assembles it that
    way), so the sections are read by name; every section's contents are
    walked as typed records.
    """
    write(b"{")
    first = True
    for key in _MODULE_SECTIONS:
        if not _module_has(module, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = module[key]
        if key == "functions":
            _write_list(write, value, _write_function)
        elif key == "classes":
            _write_classes(write, value)
        elif key == "fences":
            _write_fences(write, value)
        else:
            _write_scalar(write, value)
    write(b"}")


def _module_has(module: Any, key: str) -> bool:
    if isinstance(module, dict):
        return key in module
    return hasattr(module, key)


def _write_classes(write, classes: Any) -> None:
    """The `classes` section, sorted by name.

    The synthetic classes are emitted in set-iteration order, so the sort
    is the owner of that leak (canonical.py used to sort them).
    """
    items = list(classes or [])
    items.sort(key=lambda cls: _class_name(cls))
    _write_list(write, items, _write_class)


def _class_name(cls: Any) -> str:
    if isinstance(cls, dict):
        name = cls.get("name")
    else:
        name = getattr(cls, "name", "")
    return name or ""


def _write_class(write, cls: Any) -> None:
    """One class record, in schema field order.

    The class record is a dict of sections today; the fields and methods
    are walked as records, the fields in schema field order too.
    """
    write(b"{")
    first = True
    for key in _CLASS_SECTIONS:
        if not _module_has(cls, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = cls[key]
        if key == "fields":
            _write_list(write, value, _write_field)
        elif key == "methods":
            _write_list(write, value, _write_function)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_field(write, field: Any) -> None:
    """One class field: `name`, `type`, and the optional `default`."""
    write(b"{")
    first = True
    for key in _PARAM_SECTIONS:
        if not _module_has(field, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = field[key]
        if key == "type":
            _write_type(write, value)
        elif key == "default":
            _write_node(write, value)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_function(write, func: Any) -> None:
    """One function record, in schema field order.

    The body is the typed part: the statements are typed records, walked
    in the node schema field order. The `boxed` and `env` sections ride
    along as they are.
    """
    write(b"{")
    first = True
    for key in _FUNCTION_SECTIONS:
        if not _module_has(func, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = func[key]
        if key == "params":
            _write_list(write, value, _write_slot)
        elif key == "locals":
            _write_list(write, value, _write_local)
        elif key == "returnType":
            _write_type(write, value)
        elif key == "body":
            _write_list(write, value, _write_node)
        elif key == "boxed":
            # The boxed slot names the captured locals.
            _write_list(write, value, _write_scalar)
        elif key == "env":
            # The closure env: the packed locals and their types.
            _write_list(write, value, _write_env_slot)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_slot(write, slot: Any) -> None:
    """One parameter slot: `name`, `type`, the default, and the argument
    extras."""
    write(b"{")
    first = True
    for key in _PARAM_SECTIONS:
        if not _module_has(slot, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = slot[key]
        if key == "type":
            _write_type(write, value)
        elif key == "default":
            _write_node(write, value)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_env_slot(write, slot: Any) -> None:
    """One closure env slot: `name` and `type`.

    The function-level env is a list of name/type pairs (the closure
    node's own env carries the typed `EnvItem` records instead).
    """
    write(b"{")
    first = True
    for key in ("name", "type"):
        if not _module_has(slot, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = slot[key]
        if key == "type":
            _write_type(write, value)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_local(write, local: Any) -> None:
    """One local slot: `name` and `type`."""
    write(b"{")
    first = True
    for key in _LOCAL_SECTIONS:
        if not _module_has(local, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        value = local[key]
        if key == "type":
            _write_type(write, value)
        else:
            _write_scalar(write, value)
    write(b"}")


def _write_fences(write, fences: Any) -> None:
    """The `fences` section, in the canonical total order.

    The order is `(code, message, function or "", span.line, span.col)`,
    computed before the spans are dropped (a fence's `function` may be
    None, and its position is the tiebreaker). canonical.py owned this
    rule; the typed serializer owns it now.
    """
    items = list(fences or [])
    items.sort(key=_fence_key)
    _write_list(write, items, _write_fence)


def _fence_key(fence: Any) -> tuple:
    """The canonical total-order key of one fence record."""
    code = _fence_get(fence, "code")
    message = _fence_get(fence, "message")
    function = _fence_get(fence, "function")
    span = _fence_get(fence, "span")
    line = 0
    col = 0
    if isinstance(span, dict):
        line = span.get("line") or 0
        col = span.get("col") or 0
    return (code or "", message or "", function or "", line, col)


def _fence_get(fence: Any, key: str) -> Any:
    if isinstance(fence, dict):
        return fence.get(key)
    return getattr(fence, key, None)


def _write_fence(write, fence: Any) -> None:
    """One fence record: `code`, `message`, `function`.

    The span is incidental and never emitted.
    """
    write(b"{")
    first = True
    for key in ("code", "message", "function"):
        if not _module_has(fence, key):
            continue
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        _write_scalar(write, fence[key])
    write(b"}")


# --- the typed node walk -------------------------------------------------
#
# Per node kind the serializer emits the node id first, then the schema
# field order of that kind, then the children. The field order is the
# canonicity mechanism: a reorder is a schema change and moves every
# digest through the repin ritual, never silently.

# The schema field order per node kind, the order the typed records carry
# their fields. Absent fields are omitted, exactly as the dict IR omits
# them.
_NODE_FIELDS: dict[str, tuple[str, ...]] = {
    "assign": ("target", "value"),
    "attr": ("name", "type", "value"),
    "binary": ("op", "left", "right", "type"),
    "builtin": ("name",),
    "call": ("callee", "args", "type", "symbol", "format_style", "format_kwnames", "identity", "thread"),
    "cast": ("op", "value", "type"),
    "class": ("name", "label"),
    "closure": ("name", "env", "type", "stub", "thread_stub"),
    "compare": ("op", "left", "right", "type"),
    "contains": ("hay", "needle", "negated", "type"),
    "const": ("value", "type"),
    "dict": ("pairs", "type"),
    "excmake": ("exc", "code", "message"),
    "expr": ("expr",),
    "function": ("name",),
    "if": ("cond", "then", "else"),
    "list": ("elems", "set", "frozen", "type"),
    "method": ("receiver", "name", "tuple"),
    "module": ("module", "name"),
    "name": ("id", "type"),
    "pass": (),
    "raise": ("exc", "code", "message", "reraise", "excslot", "cause"),
    "return": ("value",),
    "setattr": ("object", "name", "value"),
    "setitem": ("container", "index", "value"),
    "subscript": ("value", "index", "type"),
    "try": ("body", "handlers", "orelse", "brk", "cont", "finalbody"),
    "unary": ("op", "operand", "type"),
    "value": ("target",),
    "while": ("cond", "body"),
}


def _write_node(write, node: Any) -> None:
    """One typed node: the node id, then its schema field order."""
    if node is None:
        write(b"null")
        return
    if isinstance(node, (str, int, float, bool)):
        _write_scalar(write, node)
        return
    if isinstance(node, dict):
        # A leftover dict node (the lossy dict IR shapes `dict_to_node`
        # keeps whole): walk it in schema field order like the dict IR.
        _write_dict_node(write, node)
        return
    kind = getattr(node, "kind", None)
    if kind is None:
        # A record shape (handler / env item / pair / stub / thread) is
        # written by its parent.
        raise TypeError(f"the typed IR carries a {type(node).__name__} with no node id")
    fields = _NODE_FIELDS.get(kind)
    if fields is None:
        raise TypeError(f"the typed IR carries a {kind!r} node with no schema field order")
    write(b"{")
    _write_str(write, "kind")
    write(b":")
    _write_str(write, kind)
    for field in fields:
        _write_node_field(write, node, kind, field)
    write(b"}")


def _write_node_field(write, node: Any, kind: str, field: str) -> None:
    """One schema field of a node, omitted when the record does not carry it."""
    if field == "type":
        typ = node.type
        if typ is None:
            return
        write(b',"type":')
        _write_type(write, typ)
        return
    if field == "value" and kind == "const":
        write(b',"value":')
        _write_const_value(write, node)
        return
    if field == "negated":
        # The dict IR always spells `negated` on contains (ir.py validates
        # it); the bit's absence reads as False.
        write(b',"negated":')
        write(b"true" if node.flags & F_NEGATED else b"false")
        return
    if field == "set":
        if not node.flags & F_SET:
            return
        write(b',"set":true')
        return
    if field == "frozen":
        if not node.flags & F_FROZEN:
            return
        write(b',"frozen":true')
        return
    if field == "identity":
        if not node.flags & F_IDENTITY:
            return
        write(b',"identity":true')
        return
    if field == "reraise":
        if not node.flags & F_RERAISE:
            return
        write(b',"reraise":true')
        return
    if field == "bare":
        if not node.flags & F_BARE:
            return
        write(b',"bare":true')
        return
    if field == "code":
        if node.code == -1:
            return
        write(b',"code":')
        _write_scalar(write, node.code)
        return
    if field == "format_kwnames":
        if node.format_kwnames == -1:
            return
        write(b',"format_kwnames":')
        _write_scalar(write, node.format_kwnames)
        return
    if field == "target" and kind == "value":
        if node.target_node is None:
            return
        write(b',"target":')
        _write_node(write, node.target_node)
        return
    if field == "thread":
        if node.thread is None:
            return
        write(b',"thread":')
        _write_thread(write, node.thread)
        return
    if field == "stub":
        if node.stub is None:
            return
        write(b',"stub":')
        _write_stub(write, node.stub)
        return
    if field == "thread_stub":
        if node.thread_stub is None:
            return
        write(b',"thread_stub":')
        _write_stub(write, node.thread_stub)
        return
    if field == "env":
        if node.env is None:
            return
        write(b',"env":')
        _write_list(write, node.env, _write_env_item)
        return
    if field == "pairs":
        if node.pairs is None:
            return
        write(b',"pairs":')
        _write_list(write, node.pairs, _write_pair)
        return
    if field == "handlers":
        if node.handlers is None:
            return
        write(b',"handlers":')
        _write_handlers(write, node.handlers)
        return
    if field in ("args", "elems", "then", "body", "orelse", "finalbody"):
        children = getattr(node, field)
        if children is None:
            return
        write((',"' + field + '":').encode("ascii"))
        _write_list(write, children, _write_node)
        return
    if field == "else":
        # The typed record spells the else branch `else_`.
        children = node.else_
        if children is None:
            return
        write(b',"else":')
        _write_list(write, children, _write_node)
        return
    if field in ("cond", "expr", "operand", "left", "right"):
        child = getattr(node, field)
        if child is None:
            return
        write((',"' + field + '":').encode("ascii"))
        _write_node(write, child)
        return
    if field in ("hay", "needle"):
        child = getattr(node, field)
        if child is None:
            return
        write((',"' + field + '":').encode("ascii"))
        _write_node(write, child)
        return
    if field in ("value", "object", "callee"):
        child = getattr(node, field)
        if child is None:
            return
        write((',"' + field + '":').encode("ascii"))
        _write_node(write, child)
        return
    if field == "index":
        child = node.index
        if child is None:
            return
        write(b',"index":')
        _write_node(write, child)
        return
    if field == "message":
        # The dict IR always spells `message` on raise and excmake (null
        # when the raise carries no message), so the typed record spells
        # it too: the absent message reads as null, not as a missing key.
        write(b',"message":')
        child = node.message
        if child is None:
            write(b"null")
        else:
            _write_node(write, child)
        return
    if field == "cause":
        child = node.cause
        if child is None:
            return
        write(b',"cause":')
        _write_node(write, child)
        return
    if field == "container":
        # The setitem node spells the container as `object`.
        child = node.object
        if child is None:
            return
        write(b',"container":')
        _write_node(write, child)
        return
    if field == "target":
        text = node.target
        if text == "":
            return
        write(b',"target":')
        _write_scalar(write, text)
        return
    if field in _TEXT_FIELDS:
        # The typed record spells the tuple attribute `tuple_` (the `tuple`
        # name is the builtin).
        text = node.tuple_ if field == "tuple" else getattr(node, field)
        if text == "":
            return
        write((',"' + field + '":').encode("ascii"))
        _write_scalar(write, text)
        return
    raise TypeError(f"the typed IR carries a {field!r} field with no schema writer")


# The string fields a node can carry.
_TEXT_FIELDS = (
    "name",
    "id",
    "op",
    "exc",
    "excslot",
    "symbol",
    "format_style",
    "receiver",
    "tuple",
    "module",
    "label",
    "brk",
    "cont",
)


def _write_dict_node(write, node: dict) -> None:
    """A leftover dict node, walked in schema field order.

    `dict_to_node` keeps a few call dicts whole (the island call's extra
    keys, print's `sep`/`end`/`file`, sys.exit's `exitMessage`, reduce's
    `fn`, `json_flags`), so the typed walker has to read them as dicts. The
    field order is the node id first, then the schema field order of the
    kind, then any extra keys the dict carries, in sorted order.
    """
    kind = node.get("kind")
    fields = _NODE_FIELDS.get(kind, ())
    write(b"{")
    _write_str(write, "kind")
    write(b":")
    _write_str(write, kind)
    extra = sorted(key for key in node if key != "kind" and key not in fields and key != "span")
    for field in fields:
        if field not in node:
            continue
        write((',"' + field + '":').encode("ascii"))
        if field == "handlers":
            # The handler list owns the `codes` sort.
            _write_list(write, node[field], _write_dict_handler)
        else:
            _write_dict_value(write, node[field])
    for key in extra:
        write(b",")
        _write_str(write, key)
        write(b":")
        _write_dict_value(write, node[key])
    write(b"}")


def _write_dict_value(write, value: Any) -> None:
    """A value read out of a leftover dict node."""
    if value is None:
        write(b"null")
    elif isinstance(value, dict) and "kind" in value:
        # A nested node: walk it in schema field order too.
        _write_dict_node(write, value)
    elif value is True:
        write(b"true")
    elif value is False:
        write(b"false")
    elif isinstance(value, str):
        _write_str(write, _scrub_paths(value))
    elif isinstance(value, int):
        write(str(value).encode("ascii"))
    elif isinstance(value, float):
        write(repr(value).encode("ascii"))
    elif isinstance(value, list):
        _write_list(write, value, _write_dict_value)
    elif isinstance(value, dict):
        write(b"{")
        first = True
        for key in sorted(value):
            if not first:
                write(b",")
            first = False
            _write_str(write, key)
            write(b":")
            _write_dict_value(write, value[key])
        write(b"}")
    else:
        raise TypeError(f"the typed IR carries a {type(value).__name__} in a leftover dict node")


# --- the typed type walk -------------------------------------------------


def _write_type(write, typ: Any) -> None:
    """One type record: the kind, then the fields that kind carries."""
    if typ is None:
        write(b"null")
        return
    if isinstance(typ, dict):
        _write_dict_type(write, typ)
        return
    kind = typ.kind
    write(b"{")
    _write_str(write, "kind")
    write(b":")
    _write_str(write, kind)
    if kind == "class":
        _write_type_text(write, "name", typ.name)
        _write_type_text(write, "label", typ.label)
        if typ.elem is not None:
            write(b',"elem":')
            _write_type(write, typ.elem)
        if typ.flags & F_TOPT:
            write(b',"optional":true')
        if typ.flags & F_TMARK_HANDLE:
            write(b',"handle":true')
        if typ.flags & F_TMARK_ISLAND:
            write(b',"island":true')
    elif kind == "list":
        if typ.elem is not None:
            write(b',"elem":')
            _write_type(write, typ.elem)
        if typ.flags & F_TSET:
            write(b',"set":true')
    elif kind == "dict":
        if typ.key is not None:
            write(b',"key":')
            _write_type(write, typ.key)
        if typ.value is not None:
            write(b',"value":')
            _write_type(write, typ.value)
        if typ.flags & F_TMARK_COUNTER:
            write(b',"counter":true')
        if typ.flags & F_TMARK_DEFAULTDICT:
            write(b',"defaultdict":true')
        if typ.flags & F_TMARK_ORDERED:
            write(b',"ordered":true')
    elif kind == "func":
        write(b',"params":')
        _write_list(write, typ.params or [], _write_type)
        if typ.ret is not None:
            write(b',"return":')
            _write_type(write, typ.ret)
    elif kind == "typevar":
        _write_type_text(write, "name", typ.name)
    write(b"}")


def _write_type_text(write, key: str, text: str) -> None:
    """One string field of a type record, omitted when it is empty."""
    if text == "":
        return
    write((',"' + key + '":').encode("ascii"))
    _write_scalar(write, text)


def _write_dict_type(write, typ: dict) -> None:
    """A type record read out of a leftover dict."""
    write(b"{")
    first = True
    for key in sorted(typ):
        if not first:
            write(b",")
        first = False
        _write_str(write, key)
        write(b":")
        _write_dict_value(write, typ[key])
    write(b"}")


# --- the typed record shapes ---------------------------------------------


def _write_handlers(write, handlers: Any) -> None:
    """The handler list of a try node.

    Each handler's `codes` list is sorted: the codes are derived from a
    scan over the class table, whose insertion order carries the same set
    leak as the class list (canonical.py used to sort them).
    """
    write(b"[")
    first = True
    for handler in handlers or []:
        if not first:
            write(b",")
        first = False
        _write_handler(write, handler)
    write(b"]")


def _write_handler(write, handler: Any) -> None:
    """One handler record: `exc`, `body`, `codes`, `bare`, `excslot`, `bind`.

    The typed `Handler` record and the dict handler spell the same
    fields; the codes are sorted either way.
    """
    if isinstance(handler, dict):
        _write_dict_handler(write, handler)
        return
    write(b"{")
    _write_str(write, "exc")
    write(b":")
    _write_list(write, handler.exc, _write_scalar)
    write(b',"body":')
    _write_list(write, handler.body, _write_node)
    if handler.codes is not None:
        write(b',"codes":')
        _write_list(write, sorted(handler.codes), _write_scalar)
    if handler.flags & F_BARE:
        write(b',"bare":true')
    _write_type_text(write, "excslot", handler.excslot)
    _write_type_text(write, "bind", handler.bind)
    write(b"}")


def _write_dict_handler(write, handler: Any) -> None:
    """One handler record read out of a dict node."""
    if not isinstance(handler, dict):
        _write_handler(write, handler)
        return
    write(b"{")
    _write_str(write, "exc")
    write(b":")
    _write_list(write, handler.get("exc") or [], _write_scalar)
    write(b',"body":')
    _write_list(write, handler.get("body") or [], _write_node)
    codes = handler.get("codes")
    if codes is not None:
        write(b',"codes":')
        _write_list(write, sorted(codes), _write_scalar)
    if handler.get("bare"):
        write(b',"bare":true')
    _write_type_text(write, "excslot", handler.get("excslot") or "")
    _write_type_text(write, "bind", handler.get("bind") or "")
    write(b"}")


def _write_pair(write, pair: Any) -> None:
    """One dict-literal entry: `key` and `value`."""
    write(b'{"key":')
    _write_node(write, pair.key)
    write(b',"value":')
    _write_node(write, pair.value)
    write(b"}")


def _write_env_item(write, item: Any) -> None:
    """One closure env entry: a local to pack (`name`) or a value to box."""
    if item.name != "":
        write(b'{"name":')
        _write_scalar(write, item.name)
        write(b',"type":')
        _write_type(write, item.type)
        write(b"}")
        return
    write(b'{"init":')
    _write_node(write, item.init)
    write(b',"type":')
    _write_type(write, item.type)
    write(b"}")


def _write_stub(write, stub: Any) -> None:
    """The partial/thread shim spec: the bound types, the params, the return."""
    write(b'{"bound":')
    _write_list(write, stub.bound, _write_type)
    write(b',"params":')
    _write_list(write, stub.params, _write_type)
    write(b',"return":')
    _write_type(write, stub.ret)
    write(b"}")


def _write_thread(write, meta: Any) -> None:
    """The thread record a Thread/to_thread call rides."""
    write(b"{")
    first = True
    if meta.daemon:
        write(b'"daemon":')
        _write_scalar(write, meta.daemon)
        first = False
    if meta.name != "":
        if not first:
            write(b",")
        first = False
        write(b'"name":')
        _write_scalar(write, meta.name)
    if meta.closure is not None:
        if not first:
            write(b",")
        first = False
        write(b'"closure":')
        _write_node(write, meta.closure)
    if meta.result is not None:
        if not first:
            write(b",")
        first = False
        write(b'"result":')
        _write_type(write, meta.result)
        write(b',"size":')
        _write_scalar(write, meta.size)
    write(b"}")


def _write_const_value(write, node: Any) -> None:
    """The JSON value a const node spells."""
    if node.ptag == P_BOOL:
        write(b"true" if node.svalue == "true" else b"false")
        return
    if node.ptag == P_NONE:
        write(b"null")
        return
    _write_scalar(write, node.svalue)


# --- the scalar writers --------------------------------------------------


def _write_list(write, items: Any, item_writer) -> None:
    """A JSON array of items, each written by `item_writer`."""
    write(b"[")
    first = True
    for item in items or []:
        if not first:
            write(b",")
        first = False
        item_writer(write, item)
    write(b"]")


def _write_scalar(write, value: Any) -> None:
    """One JSON scalar, with the path scrub the canonical form applies."""
    if value is None:
        write(b"null")
    elif value is True:
        write(b"true")
    elif value is False:
        write(b"false")
    elif isinstance(value, str):
        _write_str(write, _scrub_paths(value))
    elif isinstance(value, int):
        write(str(value).encode("ascii"))
    elif isinstance(value, float):
        write(repr(value).encode("ascii"))
    else:
        raise TypeError(f"the canonical IR carries a {type(value).__name__}; json.dumps would refuse it")


# json.dumps short escapes for the C0 controls it does not write as \uXXXX.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
}

_HEX = "0123456789abcdef"


def _write_str(write, text: str) -> None:
    """A JSON string: UTF-8 with json's ensure_ascii=False escaping."""
    write(b'"')
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
                escape = "\\u" + _HEX[code >> 12] + _HEX[(code >> 8) & 0xF] + _HEX[(code >> 4) & 0xF] + _HEX[code & 0xF]
        else:
            continue
        if start < index:
            write(text[start:index].encode("utf-8"))
        write(escape.encode("ascii"))
        start = index + 1
    if start < len(text):
        write(text[start:].encode("utf-8"))
    write(b'"')


# --- the path scrub ------------------------------------------------------
#
# Environment absolute paths reaching IR string constants (the stdlib
# probes read sys.prefix / sys.executable at compile time) are replaced
# with `<PATH>`, longest first so an executable path is consumed before
# its prefix runs. canonical.py owned this rule; the typed serializer owns
# it now.


@lru_cache(maxsize=1)
def _env_paths() -> tuple[str, ...]:
    """Every absolute path this environment could leak into IR strings.

    Realpath and as-given variants both (macOS /tmp is a symlink), longest
    first so a path nested under another is replaced before its container.
    Cached: sys.prefix etc. cannot change inside one process.
    """
    roots: list[str] = []
    for raw in (
        getattr(sys, "executable", ""),
        getattr(sys, "_base_executable", ""),
        getattr(sys, "exec_prefix", ""),
        getattr(sys, "prefix", ""),
        os.getcwd(),
        _checkout_root(),
    ):
        if not raw:
            continue
        roots.append(os.path.normpath(raw))
        real = os.path.realpath(raw)
        if real != roots[-1]:
            roots.append(real)
    # Drop paths contained in a longer candidate: replacing the container
    # first would strand the remainder of the shorter one as plain text.
    return tuple(sorted(set(roots), key=len, reverse=True))


def _checkout_root() -> str:
    """Repository root found by walking up from this file to a pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return str(parent)
    return ""


def _scrub_paths(value: str) -> str:
    """Replace environment path substrings in one string value."""
    for path in _env_paths():
        if path in value:
            value = value.replace(path, _PATH_PLACEHOLDER)
    return value
