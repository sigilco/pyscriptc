"""Recursive repr spec for the lowered `print(container)` calls.

A `PsSeq` cell is only a byte size, so the element type has to ride the
`ps_seq_repr`/`ps_map_repr` abi call as text. The spec is a small prefix
string the runtime reprs in `ps_fmt.c` parse:

- `i` int, `f` float, `s` str, `b` bool, `y` bytes: the scalar leaves
- `l` + spec: a list cell (a `PsSeq *`)
- `t` + spec: a set cell (a `PsSeq *`, braces and the `set()` empty
  spelling)
- `d` + key char + spec: a dict cell (a `PsMap *`); the key char is `i`
  or `s`, matching the map's stored `key_kind`
- `u` + count byte + per-field (len byte + spec): a synthetic `tuple_N`
  class cell (a pointer to the `%cls` struct). The field specs ride in
  field order; the runtime derives each field's struct offset from the
  field kind's size and alignment, the same layout LLVM gives the
  struct. Field and arity counts stay under 128 so every length rides
  one byte; a wider shape keeps the caller's PS2003 fence.

`type_spec` returns the spec for one type record, or None when the type
is outside the printable set (the caller keeps its PS2003 fence). The
`classes` table resolves a `tuple_N` class name to its field types; a
class that is not a synthetic tuple has no spec.
"""

from __future__ import annotations

from typing import Any

from pyscriptc._irnode import F_TSET, T
from pyscriptc.tirserde import dict_to_type

_SCALAR = {"int": "i", "float": "f", "str": "s", "bool": "b", "bytes": "y"}

# The largest arity or field-spec length the one-byte `u` framing carries.
_SPEC_BYTE_MAX = 127


def _dict_spec(typ: dict[str, Any], classes: dict[str, Any] | None) -> str | None:
    t = dict_to_type(typ)
    if not isinstance(t, T):
        return None
    return _spec(t, classes)


def _tuple_spec(info: dict[str, Any], classes: dict[str, Any] | None) -> str | None:
    """The `u` spec for one synthetic tuple class, or None when a field
    is outside the printable set."""

    fields = info.get("fields") or []
    if not fields or len(fields) > _SPEC_BYTE_MAX:
        return None
    parts: list[str] = []
    for field in fields:
        sub = _dict_spec(field.get("type"), classes)
        if sub is None or len(sub) > _SPEC_BYTE_MAX:
            return None
        parts.append(sub)
    out = "u" + chr(len(parts))
    for sub in parts:
        out += chr(len(sub)) + sub
    return out


def _spec(t: T, classes: dict[str, Any] | None) -> str | None:
    kind = t.kind
    if kind in _SCALAR:
        return _SCALAR[kind]
    if kind == "list":
        if t.elem is None:
            return None
        inner = _spec(t.elem, classes)
        if inner is None:
            return None
        return ("t" if t.flags & F_TSET else "l") + inner
    if kind == "dict":
        if t.key is None or t.value is None:
            return None
        key: str = t.key.kind
        if key not in ("int", "str"):
            return None
        inner = _spec(t.value, classes)
        if inner is None:
            return None
        return "d" + ("i" if key == "int" else "s") + inner
    if kind == "class" and classes is not None:
        info = classes.get(t.name)
        if info is not None and info.get("tuple"):
            return _tuple_spec(info, classes)
    return None


def type_spec(typ: dict[str, Any], classes: dict[str, Any] | None = None) -> str | None:
    """The repr spec for `typ`, or None when it is not printable."""

    return _dict_spec(typ, classes)
