"""Lower spike IR to textual LLVM IR and a host binary.

`int` stays a Python int in the IR. This backend uses i64 only when the value
fits, and checked arithmetic raises through PsExc on overflow instead of wrapping. A literal
that does not fit is refused: it is not truncated.

The emitter itself moved to `pyscriptc.emit.llvm`, split by concern so a
second backend can share the IR-reading seam in `pyscriptc.emit._ir`. This
module stays as the import path the rest of the compiler uses.
"""

from __future__ import annotations

from pyscriptc.emit.llvm import (
    Emitter,
    compile_unit,
    emit_llvm,
    link_binary,
    sanitize_flags,
    standalone_entry,
)

__all__ = [
    "Emitter",
    "compile_unit",
    "emit_llvm",
    "link_binary",
    "sanitize_flags",
    "standalone_entry",
]
