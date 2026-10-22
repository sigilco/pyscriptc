"""Emitter seam: build a small IR module in memory and emit it to text.

The golden corpus (`tests/test_emitter_goldens.py`) and the future C backend
emit one fixture at a time through `emit_ir_text`: no file, no subprocess, no
`cc`. The emitter contract is unchanged; this module only packages what
`llvm.emit_llvm` already does for whole programs into a form a fragment can
use.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.errors import IRError
from pyscriptc.ir import IR_VERSION, SUBSET, assert_same_cpython, cpython_version, validate_module


def make_module(
    functions: list[dict[str, Any]],
    classes: list[dict[str, Any]] | None = None,
    entry: str | None = None,
    source_file: str = "<emitter-seam>",
) -> dict[str, Any]:
    """A minimal valid IR module carrying exactly the given functions and classes.

    The stamp uses the running interpreter, as `lower` would, so the module
    passes the same cross-version check a whole-program compile does.
    """

    module: dict[str, Any] = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": cpython_version(),
        "sourceFile": source_file,
        "entry": entry,
        "functions": functions,
        "classes": classes or [],
        "fences": [],
    }
    validate_module(module)
    return module


def emit_ir_text(
    module: dict[str, Any],
    call: str | None = None,
    args: list[Any] | None = None,
) -> str:
    """Emit one IR module to LLVM text, in memory.

    This is the fragment-level seam: the golden corpus asserts on this text
    byte for byte, and a second backend renders the same modules the same way.
    `call` names the function the `main` entry calls, so a fixture can cover
    the entry point as well as the function bodies.

    The module goes through the same checks as a whole-program compile: the
    schema is validated and the CPython stamp must match the running
    interpreter.
    """

    from pyscriptc.llvm import Emitter

    assert_same_cpython(module)
    if module["fences"]:
        raise IRError("refusing to emit LLVM while fences are present")
    if not module["functions"]:
        raise IRError("module has no functions to emit")
    return Emitter(module).emit(call, [] if args is None else args, standalone=False)
