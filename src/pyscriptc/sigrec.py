"""Named records for the closed-table signature rows (slice 1 typing hygiene).

The stdlib call tables in `stdlib_queue` and `stdmore` described each row as
a plain dict (`{"params": [...], "return": ..., "min": ...}`). The `Any`
annotations those dicts forced are the rank-1 self-host fence family
(PS1104). This module names the two shapes instead:

- `T` is one IR type row: the `kind` tag plus the fields the kind carries.
  It mirrors the dict IR's type rows node for node (`elem`, `key`, `value`,
  `params`/`ret`, `name`, and a flag word for the `set` bit, which the
  subset cannot carry as a bool field per the irtype probes).
- `Sig` is one fixed-signature row: the parameter types, the return type,
  the ABI symbol when the call lowers onto one, and the optional-parameter
  tail (`min`/`defaults`/`names`) the wave rows carry.

The shape is the dict the lowerer reads today: `sig_to_dict`/`type_to_dict`
reproduce the exact dict, key for key, so every consumer of the tables sees
the same canonical payload and no IR or emitter contract moves (contract:
`.agents/docs/spec/ir-canonicity.md`).

Two rules this module follows, from the irtype probes:

- A dataclass default must be a constant, so every child reference is
  declared first without a default and the defaulted tail is str/int
  scalars and the flag word only. Construction goes through the `t_*` and
  `sig`/`fixed` helpers so call sites never see the prefix.
- A missing optional tail is the `-1` sentinel on `min`, not None: the
  sentinel is a constant, `list[str] | None = None` is not.

This module stays pure data and compiles 0 fences under pyscriptc.
"""

from __future__ import annotations

from dataclasses import dataclass

# T flag bits. The dict IR carries one bool per row (`set`); the record
# carries them as one int word because the lowered subset refuses bool
# fields.
T_SET = 1


@dataclass(frozen=True)
class T:
    """One IR type row: the `kind` tag plus the shape the kind carries.

    Leaf kinds (`int`, `float`, `bool`, `str`, `bytes`, `none`, `buf`,
    `exc`, `typevar`, `any`) carry nothing; `class` carries `name`; `list`
    carries `elem` and the set bit; `dict` carries `key`/`value`; `func`
    carries `params`/`ret`. Build through the `t_*` helpers.
    """

    kind: str
    elem: T | None
    key: T | None
    value: T | None
    params: list[T]
    ret: T | None
    name: str = ""
    flags: int = 0


def t_leaf(kind: str) -> T:
    """A leaf type row: `int`, `float`, `bool`, `str`, `bytes`, `none`, `buf`, `exc`."""
    return T(kind, None, None, None, [], None)


def t_class(name: str) -> T:
    """A class-name type row."""
    return T("class", None, None, None, [], None, name=name)


def t_list(elem: T, set_: bool = False) -> T:
    """A list row, or a set row when `set_` is set (the flag word carries it)."""
    return T("list", elem, None, None, [], None, flags=T_SET if set_ else 0)


def t_dict(key: T, value: T) -> T:
    """A dict row with its key and value rows."""
    return T("dict", None, key, value, [], None)


def t_func(params: list[T], ret: T) -> T:
    """A func row with its parameter and return rows."""
    return T("func", None, None, None, list(params), ret)


@dataclass(frozen=True)
class Sig:
    """One fixed-signature row of a closed-table call.

    `params` are the positional parameter types; `ret` is the return type.
    `symbol` is the C symbol an ABI call lowers onto, "" when the call is a
    plain module call. The optional tail names the keyword spellings:
    `min` is the count of required parameters (-1 when every parameter is
    required, the sentinel for "no optional tail"), `defaults` are the
    repr strings of the optional defaults in order, `names` are the
    parameter names in order.
    """

    params: list[T]
    ret: T
    defaults: list[str] | None
    names: list[str] | None
    min: int
    # None means "no symbol key in the dict"; "" means the key exists with
    # the empty value, the spelling the isnan family carries.
    symbol: str | None


def sig(params: list[T], ret: T, symbol: str = "") -> Sig:
    """A fully fixed row with a symbol key (empty when the call is inline)."""
    return Sig(list(params), ret, None, None, -1, symbol or "")


def optional(
    params: list[T],
    ret: T,
    defaults: list[str],
    names: list[str] | None = None,
    symbol: str = "",
) -> Sig:
    """A row with an optional-parameter tail."""
    return Sig(list(params), ret, list(defaults), names, len(params) - len(defaults), symbol)


def type_to_dict(typ: T) -> dict[str, object]:
    """The dict IR's type row for `typ`, key for key.

    `list` emits `elem` plus `set: True` only when the flag is set, `dict`
    emits `key`/`value`, `func` emits `params`/`return`, `class` emits
    `name`; every other kind emits only `kind`. The order matches the dict
    literals the tables wrote before this module existed.
    """
    if typ.kind == "class":
        return {"kind": "class", "name": typ.name}
    if typ.kind == "list":
        row: dict[str, object] = {"kind": "list", "elem": type_to_dict(typ.elem)}
        if typ.flags & T_SET:
            row["set"] = True
        return row
    if typ.kind == "dict":
        return {"kind": "dict", "key": type_to_dict(typ.key), "value": type_to_dict(typ.value)}
    if typ.kind == "func":
        return {
            "kind": "func",
            "params": [type_to_dict(param) for param in typ.params],
            "return": type_to_dict(typ.ret),
        }
    return {"kind": typ.kind}


def sig_to_dict(row: Sig) -> dict[str, object]:
    """The dict the lowerer reads for `row`, key for key.

    `params`/`return` always; `symbol` and `llvm` only when the row names
    an ABI symbol (the `llvm` spelling is derived from the return kind
    exactly as `stdlib_queue._sig` wrote it); `min`/`defaults` only when
    the row has an optional tail; `names` only when the row names its
    parameters.
    """
    out: dict[str, object] = {
        "params": [type_to_dict(param) for param in row.params],
        "return": type_to_dict(row.ret),
    }
    if row.symbol is not None:
        out["symbol"] = row.symbol
        out["llvm"] = "double" if row.ret.kind == "float" else "i64"
    if row.min >= 0:
        out["min"] = row.min
        out["defaults"] = list(row.defaults)
    if row.names is not None:
        out["names"] = list(row.names)
    return out


def sig_for(row: Sig) -> dict[str, object]:
    """The dict row of `sig_to_dict`, cached on first use.

    The lowering re-reads the same row many times per compile; one dict per
    row keeps the read cost where it was before the records existed.
    """
    cached = row.__dict__.get("_dict")
    if cached is None:
        cached = sig_to_dict(row)
        object.__setattr__(row, "_dict", cached)
    return cached


__all__ = [
    "Sig",
    "T",
    "T_SET",
    "optional",
    "sig",
    "sig_for",
    "sig_to_dict",
    "t_class",
    "t_dict",
    "t_func",
    "t_leaf",
    "t_list",
    "type_to_dict",
]
