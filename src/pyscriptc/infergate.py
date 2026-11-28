# SPDX-License-Identifier: Apache-2.0
"""The inference gate: when the pin engine may run (issue #1305 follow-up).

The engine is the expensive half of a compile: it starts a checker
server, walks the tree and proves the call sites. A tree whose source
already carries every annotation needs none of it, and a tree whose
fences the engine cannot touch needs none of it either: the engine only
proves *types*, so it can only clear the fences that a missing type
caused.

So the compile lowers the tree once without a bridge (the plain
compile), reads the fences that pass produced, and asks this module
whether the engine could rescue any of them. Only then does the engine
run, and the compile lowers the tree a second time with the bridge
wired.

The rescuable fences are the missing-type families:

* `PS1001` in the `param-inference`, `local-inference` and
  `return-inference` families: a parameter, a local or a return that
  carries no type. The checker proves the type of every argument at
  every call site, and the sites that agree pin the parameter; the
  checker's own inference answers a local and a return.
* `PS1107`: an element read out of an `Any` container that nothing
  proves. The checker's expression facts prove the element type.

Everything else (an unsupported construct, a name that does not
resolve, a class that does not lower) is a fence the engine never
clears, so a tree that carries one pays nothing for the engine.
"""

from __future__ import annotations

from typing import Any

# The fence families the engine's proofs can clear. The names are the
# coverage report's own families (`pyscriptc.coverage.family_of`), so
# the gate and the report classify a fence the same way.
RESCUABLE_FAMILIES: frozenset[str] = frozenset(
    {
        "param-inference",
        "local-inference",
        "return-inference",
    }
)

# The fence codes the engine's proofs can clear, by code. `PS1107` is
# the unresolved Any-container read: the checker's expression facts
# prove the element type the compiler could not.
RESCUABLE_CODES: frozenset[str] = frozenset({"PS1107"})

# The cascade families a missing type causes one hop out. A definition
# that fences on its own type does not export (`export-cascade`), and
# the calls that reach it then read as unlowered names (`unlowered-call`
# for a plain `call to NAME is not lowered`). The engine clearing the
# definition's fence clears the cascade with it.
CASCADE_FAMILIES: frozenset[str] = frozenset({"export-cascade", "unlowered-call"})

# The island-to-compiled seam (issue #620): a definition the island owns
# calls a native one. The plain pass plans the caller onto the island
# because the callee's unannotated signature fenced it, so the seam is a
# consequence of a missing type and the engine's proof of that signature
# moves the caller back to the native lane.
_SEAM_MARKER = "the island-to-compiled seam is the `--emit binding` surface"


def family_of(code: str, message: str) -> str:
    """The coverage report's family for one fence.

    The report's own classifier, imported lazily: the gate runs on
    every compile and the coverage module pulls the whole report
    machinery in.
    """

    from pyscriptc.coverage import family_of as _family_of

    return _family_of(code, message)


def is_rescuable(fence: dict[str, Any]) -> bool:
    """Whether the engine's proofs could clear this fence."""

    code = str(fence.get("code", ""))
    message = str(fence.get("message", ""))
    if code in RESCUABLE_CODES:
        return True
    if code == "PS1001":
        if family_of(code, message) not in RESCUABLE_FAMILIES:
            return False
        # The island fences ask for a destination annotation (the type the
        # caller puts the value in), not for a proof of the value's own
        # type: no checker fact answers them.
        if "island" in message:
            return False
        return True
    if code == "PS2002":
        if _SEAM_MARKER in message:
            return True
        return family_of(code, message) in CASCADE_FAMILIES
    return False


def needs_inference(module: dict[str, Any] | None) -> bool:
    """Whether the plain compile's fences ask for the engine.

    `module` is the plain (bridge-less) lowering result. A module with
    no fences needs no proof at all; a module whose fences the engine
    cannot clear needs none either. Only a module carrying a
    missing-type fence runs the engine.
    """

    if module is None:
        return False
    fences = module.get("fences")
    if not fences:
        return False
    for fence in fences:
        if not isinstance(fence, dict):
            continue
        if is_rescuable(fence):
            return True
    return False
