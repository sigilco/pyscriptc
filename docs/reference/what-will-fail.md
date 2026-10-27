---
title: What will fail
description: Known gaps and compiler fences measured on real-world scripts.
---

pyscriptc compiles the typed subset of Python 3.14.
When a script uses an unlowered standard library call, an unsupported language construct, or an unannotated variable, compilation halts at a named fence (`PSxxxx`).

This page documents the gap categories measured on real-world CLI scripts.
Features that are supported are not listed here: the support state lives on the [compat site]({{PYSC_COMPAT_URL}}), which measures it.
The raw, nightly-measured gap breakdown is published in the public data file [`data/compat/what_will_fail.json`]({{PYSC_COMPAT_URL}}/data/what_will_fail.json).
Each fence code links to its `pyscriptc explain` description.

## Gap categories

The measured failure modes and compiler fences fall into the following root causes:

### Standard library surface

Calls to standard library modules in the closed import table whose specific function or method is not yet lowered to native IR.
For example, functions like `os.walk` or `json.load`.
Unlowered calls halt with `PS2001`, or route to the in-process CPython island floor when running under `--fallback=cpython`.

### Closed import table

Standard library and external modules that are not yet in the closed import table.
Importing them halts compilation with `PS2002`, or routes to the island floor.

### Call signatures and keyword arguments

Calls that pass keyword arguments, unsupported argument counts, or non-scalar arguments halt with `PS2003`.
Where a typed positional form exists, passing arguments positionally clears the fence.

### Type annotations and inference

pyscriptc requires type annotations on function parameters, return types, and local variable declarations that cannot be inferred from literals or lowered calls.
Unannotated variables fence with `PS1001` at assignment, and downstream reads fence with `PS2002`.

### Statements and language constructs

Statement forms, unpack patterns, and language constructs that have not yet been lowered natively.

### Classes and object model

Class attributes, method declarations, and class decorators outside the lowered object model.

### Operators

Operators applied to unlowered operand type combinations halt with an operator fence.

### Type checking

The type-checker preflight (pyrefly) rejects type mismatches between values and declared annotations before lowering begins (`exit 2`).

### Dynamic reflection

Dynamic argument unpacking, runtime `__annotations__` reads, and `inspect.signature` calls cannot be lowered to static native structures.
These route to the in-process CPython island floor (`libpython`).

## Live measurements

Per-fence diagnostics and counts are updated nightly by the measurement runner (`uv run python scripts/gen_what_will_fail.py`) into the public data file [`data/compat/what_will_fail.json`]({{PYSC_COMPAT_URL}}/data/what_will_fail.json) and rendered on the [compat site]({{PYSC_COMPAT_URL}}#fences).
