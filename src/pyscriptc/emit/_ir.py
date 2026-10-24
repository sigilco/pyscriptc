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
