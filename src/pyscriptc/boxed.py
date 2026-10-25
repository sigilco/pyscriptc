"""The boxed per-definition fallback planner (issue #612, island floor Stages 2 and 3).

When a function definition hits a fence, this module decides which execution
plane the definition runs on: native (lowered to LLVM as today) or island
(exec'd inside the embedded CPython interpreter, called through
`ps_island_fn_invoke`). The fallback unit is the definition (plan Decision A):
a definition with zero fences lowers native, unchanged. The module top level
is all-or-nothing too (Decision A, Stage 3): when any module-level statement
fences the whole top level runs island-side as the island seed's body, and
when none fences it lowers native as today.

The planner reads the fence list the lowering pass already produced
(`Fence.function` names the enclosing definition, plan Decision A), so it never
lowers anything itself. Everything in this module is boxed-only code (the
ledger counts `src/pyscriptc/boxed.py` whole); branches in shared compiler
files carry `# boxed:begin` / `# boxed:end` markers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# The execution planes of the plane map (plan Decision D).
NATIVE = "native"
ISLAND = "island"

# The entry shim `ps_island_fn_invoke` reads: the dotted name the embedded
# interpreter calls into the module namespace. One island namespace per
# compile, seeded once at init; `fn` is the plain function name inside it.
INVOKE_SYMBOL = "ps_island_fn_invoke"


def plane_map(
    fences: list[dict[str, Any]],
    functions: list[dict[str, Any]],
    entry: str | None,
    top_level_island: bool = False,
) -> dict[str, Any]:
    """The per-definition plane map from the lowered fence list.

    `fences` are the module's `as_dict()` fences; `functions` the lowered
    records (`name`, `span`). A definition with any fence inside it plans
    island; a definition with none plans native. With `top_level_island`
    (issue #613, Decision A) the whole module body runs island-side: the
    embedded interpreter executes the module body as `__main__` instead of
    the native entry lowering it, the entry's own fences dropped with the
    top level, and every definition rides the seed.

    Returns the `fallback` summary `debug.py` embeds and the receipt records:
    `{"moduleTopLevel": "native"|"island", "functions": {name: plane, ...}}`.
    """

    fenced: set[str] = set()
    for fence in fences:
        owner = fence.get("function") or fence.get("definition")
        if owner is not None:
            fenced.add(owner)
    planes = {}
    for record in functions:
        name = record["name"] if isinstance(record, dict) else str(record)
        if name == entry or (name.startswith("__") and name.endswith("__")):
            continue
        planes[name] = ISLAND if name in fenced or top_level_island else NATIVE
    return {
        "moduleTopLevel": ISLAND if top_level_island else NATIVE,
        "functions": planes,
        "topLevelFences": 1 if top_level_island else 0,
    }


def island_names(planes: dict[str, Any]) -> list[str]:
    """The definitions the plane map routes to the island, in map order."""

    return [name for name, plane in planes.get("functions", {}).items() if plane == ISLAND]


# The fallback tier of the running compile, module-level like the island
# lane in `stdlib_queue.py`: the lowerer reads it per definition, the CLI
# installs it around the same lowering pass.
_DEFINITIONS_TIER = False


def definitions_tier() -> bool:
    """True when this compile runs under `--fallback=cpython`."""

    return _DEFINITIONS_TIER


@contextmanager
def boxed_definitions(on: bool) -> Iterator[None]:
    """Install the per-definition fallback tier for one lowering pass."""

    global _DEFINITIONS_TIER
    saved = _DEFINITIONS_TIER
    _DEFINITIONS_TIER = on
    try:
        yield
    finally:
        _DEFINITIONS_TIER = saved
