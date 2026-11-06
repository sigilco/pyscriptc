"""The shared IR-reading seam for every emitter.

An emitter turns the typed IR into target text. The IR facts an emitter needs
before it can write a single instruction live here, so a second backend reads
the IR the same way the LLVM one does:

- the IR type to target-type mapping (`llvm_type`, `elem_size`),
- the symbol naming rules (`fn_symbol`, `safe`),
- the literal rules (`ir_const_value`, `prepare_arg`, `llvm_double`).

`llvm_type`, `llvm_double` and `llvm_bytes` still spell LLVM (`i64`, the
IEEE-754 bit pattern, the `c"..."` escapes). A backend with a different word
size or literal syntax supplies its own; the IR reading around them does not
change. That is the seam: the IR walk is shared, the target spelling is not.

Nothing here imports LLVM-specific machinery, so a C backend can consume this
module without pulling in the LLVM emitter.
"""

from __future__ import annotations

import struct
from typing import Any

from pyscriptc.errors import FitError, IRError
from pyscriptc.ir import fits_i64


def _kind(typ: dict[str, Any]) -> str:
    """The kind tag of a type record: int, float, bool, str, bytes, list,
    dict, class, exc, func, any or none. Every emitter type dispatch reads
    the record through this accessor instead of the raw dict key."""

    kind: str = typ["kind"]
    return kind


def _expr_kind(expr: dict[str, Any]) -> str:
    """The kind tag of an expression record: const, name, unary, binary,
    compare, call, list, dict, subscript, attr, cast, contains or closure.
    The expression dispatch reads the record through this accessor instead of
    the raw dict key."""

    kind: str = expr["kind"]
    return kind


def _kind_or_none(typ: dict[str, Any] | None) -> str | None:
    """The kind tag of a type record that may be absent, as with an
    unannotated `want` on an island call. Every emitter type dispatch reads
    the record through this accessor instead of the raw dict key."""

    if typ is None:
        return None
    kind: str | None = typ.get("kind", None)
    return kind


def _node_kind(node: dict[str, Any]) -> str:
    """The kind tag of a typed IR node (assign, expr, raise, ...). Statement
    and expression dispatch reads the node through this accessor instead of
    the raw dict key."""

    kind: str = node["kind"]
    return kind


def _node_type_kind(node: dict[str, Any]) -> str:
    """The kind tag of the type record a typed IR node carries. A dispatch
    on the node's own type reads it through this accessor instead of the
    raw nested dict key."""

    typ: dict[str, Any] = node["type"]
    return _kind(typ)


def _maybe_node_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of a typed IR node, or None when the node carries no
    kind (a handler record, an outline env entry). Optional-shape dispatch
    reads the node through this accessor instead of the raw dict key."""

    if "kind" not in node:
        return None
    kind: str = node["kind"]
    return kind


def _maybe_node_type_kind(node: dict[str, Any]) -> str | None:
    """The kind tag of the type record a typed IR node carries, or None
    when the node has no type record. Optional-shape dispatch reads the
    node's type through this accessor instead of the raw nested dict key."""

    if "type" not in node:
        return None
    typ: dict[str, Any] = node["type"]
    return _kind(typ)


def _type_name(typ: dict[str, Any]) -> str:
    """The class name of a class-typed type record."""
    name: str = typ["name"]
    return name


def _node_expr(node: dict[str, Any]) -> dict[str, Any]:
    """The child expression a node-shaped record carries (assign value, expr
    statement, return value). The child walk reads it through this accessor
    instead of the raw dict key."""

    child: dict[str, Any] = node["expr"]
    return child


def _node_target_node(node: dict[str, Any]) -> dict[str, Any]:
    """The callee target of a value-callee record: the callable expression
    an indirect call dispatches through."""

    target: dict[str, Any] = node["target"]
    return target


def _node_args(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The argument records of a call node, or [] when the node omits them."""

    if "args" not in node:
        return []
    args: list[dict[str, Any]] = node["args"]
    return args


def _node_operand(node: dict[str, Any]) -> dict[str, Any]:
    """The operand expression a unary node carries."""

    operand: dict[str, Any] = node["operand"]
    return operand


def _node_left(node: dict[str, Any]) -> dict[str, Any]:
    """The left child expression a binary or compare node carries."""

    left: dict[str, Any] = node["left"]
    return left


def _node_right(node: dict[str, Any]) -> dict[str, Any]:
    """The right child expression a binary or compare node carries."""

    right: dict[str, Any] = node["right"]
    return right


def _node_elems(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The element expressions of a list node, or [] when it omits them."""

    if "elems" not in node:
        return []
    elems: list[dict[str, Any]] = node["elems"]
    return elems


def _node_pairs(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The key/value pair records of a dict node, or [] when it omits them."""

    if "pairs" not in node:
        return []
    pairs: list[dict[str, Any]] = node["pairs"]
    return pairs


def _pair_key(pair: dict[str, Any]) -> dict[str, Any]:
    """The key expression of one dict-literal pair."""

    key: dict[str, Any] = pair["key"]
    return key


def _pair_value(pair: dict[str, Any]) -> dict[str, Any]:
    """The value expression of one dict-literal pair."""

    value: dict[str, Any] = pair["value"]
    return value


def _node_index(node: dict[str, Any]) -> dict[str, Any]:
    """The index expression a subscript node carries."""

    index: dict[str, Any] = node["index"]
    return index


def _node_hay(node: dict[str, Any]) -> dict[str, Any]:
    """The container expression a contains node carries."""

    hay: dict[str, Any] = node["hay"]
    return hay


def _node_needle(node: dict[str, Any]) -> dict[str, Any]:
    """The tested expression a contains node carries."""

    needle: dict[str, Any] = node["needle"]
    return needle


def _node_negated(node: dict[str, Any]) -> bool:
    """Whether a contains node spells the negated (`not in`) form."""

    if "negated" not in node:
        return False
    negated: bool = node["negated"]
    return negated


def _node_value_node(node: dict[str, Any]) -> dict[str, Any]:
    """The indexed expression a subscript node carries (the container)."""

    value: dict[str, Any] = node["value"]
    return value


def _node_callee(node: dict[str, Any]) -> dict[str, Any]:
    """The callee record a call node carries (builtin, method, value, ...)."""

    callee: dict[str, Any] = node["callee"]
    return callee


def _node_stub(node: dict[str, Any]) -> dict[str, Any]:
    """The partial-shim spec a closure node carries."""

    stub: dict[str, Any] = node["stub"]
    return stub


def _node_thread_stub(node: dict[str, Any]) -> dict[str, Any]:
    """The thread-shim spec a closure node carries."""

    thread_stub: dict[str, Any] = node["thread_stub"]
    return thread_stub


def _node_env(node: dict[str, Any]) -> list[dict[str, Any]]:
    """The env entries of a closure node, or [] when it omits them."""

    if "env" not in node:
        return []
    env: list[dict[str, Any]] = node["env"]
    return env


def _node_op(node: dict[str, Any]) -> str:
    """The operator spelling an operator node carries (`+`, `not in`, ...)."""

    op: str = node["op"]
    return op


def _node_name(node: dict[str, Any]) -> str:
    """The name payload of a node that carries one (attr, name, closure)."""

    name: str = node["name"]
    return name


def _node_id(node: dict[str, Any]) -> str:
    """The bound name a name node carries."""

    id_: str = node["id"]
    return id_


def _env_item_init(item: dict[str, Any]) -> dict[str, Any] | None:
    """The boxed initializer of a closure env entry, or None when the entry
    names a local instead (`name` carries it)."""

    if "init" not in item:
        return None
    init: dict[str, Any] = item["init"]
    return init


def _env_item_name(item: dict[str, Any]) -> str:
    """The local name a closure env entry packs."""

    name: str = item["name"]
    return name


def _field_name(field: dict[str, Any]) -> str:
    """The source name of one class field."""
    name: str = field["name"]
    return name


def _field_type(field: dict[str, Any]) -> dict[str, Any]:
    """The type record one class field carries."""
    typ: dict[str, Any] = field["type"]
    return typ


def _elem_kind(typ: dict[str, Any]) -> str:
    """The kind tag of the element type a list or dict type record carries."""
    elem: dict[str, Any] = typ["elem"]
    kind: str = elem["kind"]
    return kind


def _is_optional(typ: dict[str, Any]) -> bool:
    """Whether a type record is the optional wrapper (`T | None`)."""
    if "optional" not in typ:
        return False
    optional: bool = typ["optional"]
    return optional


def _opt_str_or_none(expr: dict[str, Any], key: str) -> str | None:
    """An optional string key of an expression record, or None when absent."""
    if key not in expr:
        return None
    value: str = expr[key]
    return value


def _opt_node_sep(node: dict[str, Any]) -> dict[str, Any] | None:
    """The `sep=` expression a print call carries, or None when absent."""

    if "sep" not in node:
        return None
    sep: dict[str, Any] = node["sep"]
    return sep


def _opt_node_end(node: dict[str, Any]) -> dict[str, Any] | None:
    """The `end=` expression a print call carries, or None when absent."""

    if "end" not in node:
        return None
    end: dict[str, Any] = node["end"]
    return end


def standalone_entry(module: dict[str, Any]) -> dict[str, Any] | None:
    """The module's runnable entry (a script body or `__main__` guard), if `--run` without `--call` has one."""
    entry = module.get("entry")
    fn = next((f for f in module["functions"] if f["name"] == entry), None)
    if fn is not None and not fn["params"]:
        name = fn["name"]
        # A script's entry is `main`/`__entry__`; a program's is the
        # qualified `__main__.main`/`__main__.__entry__`.
        if name in ("main", "__entry__") or name.startswith("__main__."):
            return fn
    return None


def _type_of(expr: dict[str, Any]) -> dict[str, Any]:
    """The type record an expression, local, param or env item carries."""
    typ: dict[str, Any] = expr["type"]
    return typ


def _return_type(fn: dict[str, Any]) -> dict[str, Any]:
    """The return type record of a function record."""
    typ: dict[str, Any] = fn["returnType"]
    return typ


def _const_text(node: dict[str, Any]) -> str:
    """The literal payload of a const node, as text (the int/float/str spelling)."""
    payload: str = node["value"]
    return payload


def _const_bool(node: dict[str, Any]) -> bool:
    """The literal payload of a bool const node."""
    payload: bool = node["value"]
    return payload


def _span_of(record: dict[str, Any]) -> dict[str, Any]:
    """The span of a record that always carries one (a function record)."""
    span: dict[str, Any] = record["span"]
    return span


def _none_type() -> dict[str, Any]:
    """The `none` type record: the return type of a function that returns nothing."""
    typ: dict[str, Any] = {}
    typ["kind"] = "none"
    return typ


def _opt_span(record: dict[str, Any]) -> dict[str, Any] | None:
    """The span of a statement or expression, or None when it carries none."""
    if "span" not in record:
        return None
    span: dict[str, Any] = record["span"]
    return span


def _opt_default(record: dict[str, Any]) -> dict[str, Any] | None:
    """The default-value expression of a parameter, or None."""
    if "default" not in record:
        return None
    default: dict[str, Any] = record["default"]
    return default


def _opt_int(record: dict[str, Any], key: str, default: int) -> int:
    """An int field of a record, or `default` when the record omits it."""
    if key not in record:
        return default
    count: int = record[key]
    return count


def _opt_str(record: dict[str, Any], key: str) -> str:
    """A str field of a record, or "" when the record omits it."""
    if key not in record:
        return ""
    name: str = record[key]
    return name


def _flag_of(record: dict[str, Any], key: str) -> bool:
    """A boolean marker field (`island`, `handle`, `exception`), or False when absent."""
    if key not in record:
        return False
    flag: bool = record[key]
    return flag


def _has(record: dict[str, Any], key: str) -> bool:
    """Whether a record carries a key at all (`env`, `stub`, `name`, ...)."""
    if key in record:
        return True
    return False


def _items(record: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """A list of record fields (`params`, `locals`, `env`, `globals`, `classes`), or [] when absent."""
    if key not in record:
        return []
    items: list[dict[str, Any]] = record[key]
    return items


def _name_list(record: dict[str, Any], key: str) -> list[str]:
    """A list-of-str field (`boxed`, `fences` names), or [] when absent."""
    if key not in record:
        return []
    names: list[str] = record[key]
    return names


def _str_map(record: dict[str, Any], key: str) -> dict[str, str]:
    """A str-to-str map field (`islandGlobals`), or {} when absent."""
    if key not in record:
        return {}
    entries: dict[str, str] = record[key]
    return entries


def _entry_span(module: dict[str, Any]) -> dict[str, Any]:
    """The synthetic span of a process entry point: the module source at line 1."""
    source: str = module["sourceFile"]
    span: dict[str, Any] = {}
    span["file"] = source
    span["line"] = 1
    return span


def _fn_symbol(name: str) -> str:
    """The process entry point is ``@main``. A user function of that name gets another symbol."""
    if name == "main":
        return "ps.user.main"
    return name


def _ir_const_value(typ: dict[str, Any], value: Any) -> Any:
    kind = typ["kind"]
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    return value


def _prepare_arg(param: dict[str, Any], value: Any) -> Any:
    kind = param["type"]["kind"]
    name = param["name"]
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise FitError(f"argument {name} must be an int")
        if not fits_i64(value):
            raise FitError(f"argument {name} does not fit in i64; refusing to truncate")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, float):
            raise FitError(f"argument {name} must be a float")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise FitError(f"argument {name} must be a bool")
        return value
    if kind == "str":
        if not isinstance(value, str):
            raise FitError(f"argument {name} must be a str")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise FitError(f"argument {name} carries a lone surrogate and is not lowered")
        return value
    raise FitError(f"argument {name} has type {kind}, which main cannot pass")


def _llvm_double(value: float) -> str:
    """IEEE-754 bits. Clang 18 rejects the 0x1.0p-1 spelling in call arguments."""

    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    return f"0x{bits:016X}"


def _llvm_type(typ: dict[str, Any]) -> str:
    kind = typ["kind"]
    if kind == "int":
        return "i64"
    if kind == "bool":
        return "i1"
    if kind == "float":
        return "double"
    if kind in {"str", "bytes"}:
        return "%ps.str"
    if kind in {"list", "dict", "class", "exc", "func"}:
        return "ptr"
    if kind == "any":
        # The unresolved slot of an Any container (issue #781): at the ABI a
        # value the read site proved rides the pointer-shaped cell, like the
        # heap kinds; the read's proven type decides how it is loaded.
        return "ptr"
    if kind == "none":
        return "void"
    raise IRError(f"no LLVM type for {kind}")


def _memory_type(typ: dict[str, Any]) -> str:
    """The LLVM type of a str or bytes value at rest in memory the runtime
    reads or writes: class fields, seq and map cells, the %ps.arg slot. The
    runtime's PsBuf is 16 bytes ({ i64, ptr }), so a str cell carries only
    that prefix; the count and flags live in the SSA %ps.str value and the
    emitter widens on load (zero-filled) and narrows on store. `bytes` has
    no payload fields, so its memory type is the same 16-byte shape."""

    if typ["kind"] in {"str", "bytes"}:
        return "%ps.buf"
    return _llvm_type(typ)


def _llvm_field_type(typ: dict[str, Any]) -> str:
    """The LLVM type of a class field. None is not a runtime value, but a
    field of None still occupies the struct (the coroutine frame's `.ps_result`
    on a `-> None` coroutine), so it takes one placeholder byte. Str fields
    use the 16-byte memory type the runtime writes."""

    if typ["kind"] == "none":
        return "i8"
    return _memory_type(typ)


def _elem_size(typ: dict[str, Any]) -> int:
    """Bytes copied into a list or dict slot. Matches the LLVM layout on this ABI."""

    kind = typ["kind"]
    if kind in {"int", "float"}:
        return 8
    if kind in {"str", "bytes"}:
        return 16
    if kind in {"list", "dict", "class", "func", "exc"}:
        return 8
    if kind == "any":
        # The unresolved cell is the str-sized %ps.buf (issue #781): a str
        # value is stashed as the 16-byte prefix the runtime reads, and a
        # scalar rides the first 8 bytes of the same cell.
        return 16
    if kind == "bool":
        return 1
    raise IRError(f"no element size for {kind}")


def _safe(name: str) -> str:
    return name.replace(".", "_")


def _llvm_bytes(data: bytes) -> str:
    """LLVM ``c"..."`` escapes. A quote is ``\\22``; ``\\"`` ends the literal early."""

    parts: list[str] = []
    for byte in data:
        if byte == ord("\\"):
            parts.append("\\\\")
        elif 32 <= byte < 127 and byte != ord('"'):
            parts.append(chr(byte))
        else:
            parts.append(f"\\{byte:02X}")
    parts.append("\\00")
    return "".join(parts)
