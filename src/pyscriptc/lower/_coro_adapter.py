"""The generator seam adapter: the dict IR the pass output emits.

`coro.py` walks typed nodes inside the pass (`_walk_node` converts each
planned statement with `dict_to_node`) and builds dict records for emit,
so the lowering seam needs no conversion on the way in: the module
arrives as the lowerer left it. On the way out the pass output still has
to be the dict IR emit reads, so this module is the one place where the
two paths meet for resumables: `node_to_dict` over the pass output, the
reconstruction every other seam already uses. A record that is already
the dict IR maps to itself, key for key; a typed node reconstructs the
dict it was built from, so the generator output stays byte identical.

The adapter lives outside `coro.py` on purpose: the generator logic
never imports `tirserde` for the module boundary, and the conversion
happens at the seam that feeds emit. It dies when emit reads typed
nodes.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.tirserde import node_to_dict


def coro_module(module: dict[str, Any]) -> dict[str, Any]:
    """The module emit reads: `node_to_dict` over the pass output.

    `lower_resumables` rewrites the planned functions and classes and
    leaves every other record untouched. A record the pass built or
    passed through may still carry a typed node (a kept-dict marker's
    children, a statement kind the typed schema does not cover), so the
    whole output converts once here. The rest of the module (the fences,
    the globals, the island plane) is not IR and rides through
    `node_to_dict` unchanged, key for key.
    """
    return node_to_dict(module)
