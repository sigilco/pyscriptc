"""Closed builtin and method tables.

A name is lowered only when it is listed here. `len` of `str` and `len` of a
list are different operations; both are listed. Anything else stays a fence.
"""

from __future__ import annotations

from typing import Any

# Codes match ps_rt.c. Exception is the wildcard handler and a raise target.
EXCEPTIONS: dict[str, int] = {
    "ValueError": 1,
    "IndexError": 2,
    "KeyError": 3,
    "OverflowError": 4,
    "ZeroDivisionError": 5,
    "RuntimeError": 6,
    "Exception": 7,
}

# User exception classes take codes from here up; matches PS_USER_BASE in
# ps_rt.h. Exception handlers treat every code at or above it as an
# Exception descendant, since all user exceptions inherit a lowered builtin.
USER_EXC_BASE = 8

# params are type kinds. return is a type kind, or "self" for the receiver's type.
STR_METHODS: dict[str, dict[str, Any]] = {
    "strip": {"params": [], "return": "self"},
    "lstrip": {"params": [], "return": "self"},
    "rstrip": {"params": [], "return": "self"},
    "upper": {"params": [], "return": "self"},
    "lower": {"params": [], "return": "self"},
    "startswith": {"params": ["self"], "return": "bool"},
    "endswith": {"params": ["self"], "return": "bool"},
    "find": {"params": ["self"], "return": "int"},
    "replace": {"params": ["self", "self"], "return": "self"},
    "count": {"params": ["self"], "return": "int"},
    "index": {"params": ["self"], "return": "int"},
    "rfind": {"params": ["self"], "return": "int"},
    "isdigit": {"params": [], "return": "bool"},
    "isalpha": {"params": [], "return": "bool"},
    "isalnum": {"params": [], "return": "bool"},
    "isspace": {"params": [], "return": "bool"},
    "isupper": {"params": [], "return": "bool"},
    "islower": {"params": [], "return": "bool"},
    "title": {"params": [], "return": "self"},
    "capitalize": {"params": [], "return": "self"},
    # casefold is str-only in CPython; the "kinds" gate keeps bytes.casefold fenced.
    "casefold": {"params": [], "return": "self", "kinds": {"str"}},
    "swapcase": {"params": [], "return": "self"},
    "zfill": {"params": ["int"], "return": "self"},
    "removeprefix": {"params": ["self"], "return": "self"},
    "removesuffix": {"params": ["self"], "return": "self"},
    "splitlines": {"params": [], "return": "self_list"},
}

LEN_KINDS = {"str", "bytes", "list", "dict"}


def _len_ok(typ: dict[str, Any]) -> bool:
    if typ["kind"] in LEN_KINDS:
        return True
    return typ["kind"] == "class" and typ.get("name") == "Deque"


def builtin_spec(name: str, arg_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the result type of a builtin call, or None when it is not lowered."""

    if name == "len" and len(arg_types) == 1 and _len_ok(arg_types[0]):
        return {"kind": "int"}
    if name == "int" and len(arg_types) == 1 and arg_types[0]["kind"] in {"float", "bytes", "str"}:
        return {"kind": "int"}
    if name == "float" and len(arg_types) == 1 and arg_types[0]["kind"] in {"int", "str"}:
        return {"kind": "float"}
    if (
        name == "str"
        and len(arg_types) == 1
        and arg_types[0]["kind"]
        in {
            "str",
            "int",
            "float",
            "bool",
            "bytes",
            "none",
        }
    ):
        return {"kind": "str"}
    if name == "str" and len(arg_types) == 1 and arg_types[0]["kind"] == "class" and arg_types[0].get("name") == "Path":
        return {"kind": "str"}
    return None


def method_spec(receiver: dict[str, Any], name: str, arg_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    if receiver["kind"] not in {"str", "bytes"}:
        return None
    if name == "encode":
        # One optional str encoding argument; the value is checked in _call_attr.
        if receiver["kind"] != "str" or len(arg_types) > 1:
            return None
        if arg_types and arg_types[0]["kind"] != "str":
            return None
        return {"kind": "bytes"}
    if name == "decode":
        if receiver["kind"] != "bytes" or len(arg_types) > 1:
            return None
        if arg_types and arg_types[0]["kind"] != "str":
            return None
        return {"kind": "str"}
    if name in {"split", "rsplit"}:
        # split([sep [, maxsplit]]); sep None is not a lowered type, so the
        # whitespace form stays the 0-arg call.
        if len(arg_types) > 2:
            return None
        if len(arg_types) >= 1 and arg_types[0]["kind"] != receiver["kind"]:
            return None
        if len(arg_types) == 2 and arg_types[1]["kind"] != "int":
            return None
        return {"kind": "list", "elem": {"kind": receiver["kind"]}}
    if name in {"ljust", "rjust", "center"}:
        if not 1 <= len(arg_types) <= 2:
            return None
        if arg_types[0]["kind"] != "int":
            return None
        if len(arg_types) == 2 and arg_types[1]["kind"] != receiver["kind"]:
            return None
        return {"kind": receiver["kind"]}
    if name == "expandtabs":
        if len(arg_types) > 1 or (arg_types and arg_types[0]["kind"] != "int"):
            return None
        return {"kind": receiver["kind"]}
    if name == "join":
        if len(arg_types) != 1 or arg_types[0]["kind"] != "list":
            return None
        if arg_types[0]["elem"]["kind"] != receiver["kind"]:
            return None
        return {"kind": receiver["kind"]}
    if name not in STR_METHODS:
        return None
    spec = STR_METHODS[name]
    if receiver["kind"] not in spec.get("kinds", {"str", "bytes"}):
        return None
    if len(arg_types) != len(spec["params"]):
        return None
    for got, want in zip(arg_types, spec["params"], strict=True):
        if want == "self":
            if got["kind"] != receiver["kind"]:
                return None
        elif got["kind"] != want:
            return None
    if spec["return"] == "self":
        return {"kind": receiver["kind"]}
    if spec["return"] == "self_list":
        return {"kind": "list", "elem": {"kind": receiver["kind"]}}
    return {"kind": spec["return"]}
