# Decision: the library receipt is two minors, not one

Date: 2026-10-23. Owner: agreed 2026-10-23. Scope: the v0.7.0 and v0.9.0 milestones.

## The decision

The v0.7.0 exit criterion is the **weak receipt**: a program that depends on a pure-Python PyPI package compiles natively and matches CPython, with `certifi` as the target.

The **strong receipt** named in #137's exit criteria ("a pure dependency, for example `colorama` or `shellingham`, compiles from source into a binary whose output matches CPython") is deferred to v0.9.0, tracked as #415.

## Why the split

The strong receipt's candidates are all blocked by something that is not in v0.7.0's scope. Measured on the vendored trees:

| Target | Root fences | What blocks it |
|---|---|---|
| `shellingham` | 42 | module-value 19, param-inference 15 |
| `colorama` | 147 | too big |
| `tomli` | 63 | generic aliases 22, unlowered type names 29 (typing surface, v0.9.0) |
| `iniconfig` | 43 | `TypeVar` (typing surface, v0.9.0) |
| `certifi` | 1 | the fence is `tests/` only; the data file is #410 |

`certifi` is the only candidate that both compiles inside v0.7.0's scope and has a real user story with a verifiable output (`where()` returns the path to the staged `cacert.pem`, which CPython computes the same way).

The alternative was to pull the typing surface into v0.7.0. That would make the minor carry a compiler epic, and it would push the pack lane out. The typing surface is 51% of `rich`'s fences and the largest fence family in the corpus; it deserves its own minor with its own exit criteria, not a slot inside a release that is already carrying two lanes.

## What this decision costs

#137's exit criteria are not met by v0.7.0. The criteria stay open until #415 closes. The deferral is recorded in the v0.7.0 milestone description so the milestone does not silently claim a criterion it does not meet.

## What this decision buys

v0.7.0 ships a capability that works, with a receipt that is reproducible in CI, and it keeps the riskiest change (reach-based pruning, #414) for a minor whose exit criteria can absorb it.

## The rule this follows

A receipt should be the strongest one the release can actually deliver, not the strongest one the theme implies. The theme is "libraries are consumable"; the weak receipt proves consumability, and #415 proves depth in the minor that has the typing surface to make it possible.

## Related

- `.agents/plans/2026-10-23-v070-milestone.md`
- `.agents/plans/2026-10-23-library-multi-root.md`
- `.agents/plans/2026-10-23-library-seams.md`
- #137 (closed, resolver half; the lowering half is #398), #415 (strong receipt)
