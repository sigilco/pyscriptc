"""The emitter package: the IR-reading seam and the per-backend emitters.

`_ir` holds the IR knowledge every backend shares: the type mapping, the
symbol naming and the literal rules. `llvm` holds the LLVM emitter, split by
concern so each file owns one area of the emission.
"""

from __future__ import annotations

from pyscriptc.emit._ir import (
    _elem_size,
    _fn_symbol,
    _ir_const_value,
    _llvm_bytes,
    _llvm_double,
    _llvm_field_type,
    _llvm_type,
    _prepare_arg,
    _safe,
    standalone_entry,
)

__all__ = [
    "_elem_size",
    "_fn_symbol",
    "_ir_const_value",
    "_llvm_bytes",
    "_llvm_double",
    "_llvm_field_type",
    "_llvm_type",
    "_prepare_arg",
    "_safe",
    "standalone_entry",
]
