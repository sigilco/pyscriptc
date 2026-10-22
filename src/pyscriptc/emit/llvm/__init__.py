"""The LLVM emitter, split by concern.

`_driver` composes the areas into `Emitter` and owns the toolchain calls;
`_runtime` holds the `ps_*` declaration table and the checked-arithmetic
helpers; the mixins own one area of emission each.
"""

from __future__ import annotations

from pyscriptc.emit._ir import standalone_entry
from pyscriptc.emit.llvm._driver import (
    Emitter,
    compile_unit,
    emit_llvm,
    link_binary,
    runtime_dir,
    sanitize_flags,
)

__all__ = [
    "Emitter",
    "compile_unit",
    "emit_llvm",
    "link_binary",
    "runtime_dir",
    "sanitize_flags",
    "standalone_entry",
]
