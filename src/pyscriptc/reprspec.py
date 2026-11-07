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

`type_spec` returns the spec for one type record, or None when the type
is outside the printable set (the caller keeps its PS2003 fence).
"""

from __future__ import annotations

from typing import Any

from pyscriptc._irnode import F_TSET, T
from pyscriptc.tirserde import dict_to_type

_SCALAR = {"int": "i", "float": "f", "str": "s", "bool": "b", "bytes": "y"}


def _spec(t: T) -> str | None:
    kind = t.kind
    if kind in _SCALAR:
        return _SCALAR[kind]
    if kind == "list":
        if t.elem is None:
            return None
        inner = _spec(t.elem)
        if inner is None:
            return None
        return ("t" if t.flags & F_TSET else "l") + inner
    if kind == "dict":
        if t.key is None or t.value is None:
            return None
        key: str = t.key.kind
        if key not in ("int", "str"):
            return None
        inner = _spec(t.value)
        if inner is None:
            return None
        return "d" + ("i" if key == "int" else "s") + inner
    return None


def type_spec(typ: dict[str, Any]) -> str | None:
    """The repr spec for `typ`, or None when it is not printable."""

    t = dict_to_type(typ)
    if not isinstance(t, T):
        return None
    return _spec(t)
