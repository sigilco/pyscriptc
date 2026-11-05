"""The generator seam adapter: the dict IR the generator pass reads.

`coro.py` stays on the HIR dict path: the frame pass walks the lowered
function and class records as plain dicts, and the typed-node list excludes
it. The lowering seam is free to build typed nodes, so this module is the
one place where the two paths meet for resumables: it converts the lowered
records into the dict IR on entry, with `node_to_dict`, the reconstruction
every other seam already uses. The conversion is lossless, so the generator
output stays byte identical.

The adapter lives outside `coro.py` on purpose: the generator logic is
untouched, and the conversion happens at the seam that feeds it.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.tirserde import node_to_dict


def coro_module(module: dict[str, Any]) -> dict[str, Any]:
    """The module the generator pass reads: the dict IR of the records.

    The frame pass walks the module's functions and classes (the records
    that carry the bodies), so those are the ones the adapter converts. The
    rest of the module (the fences, the globals, the island plane) is not
    read by the pass and rides through untouched. A record that is already
    the dict IR maps to itself, key for key; a typed node reconstructs the
    dict it was built from.
    """
    out: dict[str, Any] = {}
    for key in module:
        out[key] = module[key]
    functions: list[dict[str, Any]] = []
    for fn in module["functions"]:
        functions.append(node_to_dict(fn))
    classes: list[dict[str, Any]] = []
    for cls in module["classes"]:
        classes.append(node_to_dict(cls))
    out["functions"] = functions
    out["classes"] = classes
    return out
