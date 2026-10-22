# Checker bridge: mypy's inferred-type map into lowering (E3a, #87)

Date: 2026-10-22. Decision record for issue #87, implemented in
`src/pyscriptc/factsbridge.py` behind `compile --bridge mypy`.

## Decision

Lowering may consult a checker's inferred-type map through an opt-in bridge,
off by default. The bridge extracts a plain `line/col -> TypeFacts` map plus
per-`def` facts (proved parameter types, the return type when every return
statement infers the same type) at check time, and reduces everything to the
`typefacts` IR before lowering sees it. No mypy object crosses the seam. When
the bridge cannot answer, or answers with a type the lowered subset cannot
express, the existing fence stands: the bridge only answers, it never forces.

`--bridge mypy` wires it for one compile. Without the flag nothing runs:
`Lowerer.bridge` is None, every inference fence fires exactly as before, and
the IR is byte-identical to a no-bridge compile. The mypy lane needs mypy
importable in the running interpreter (`oracle-mypy` extra), pinned to
>=2.4,<2.5; outside that range the bridge reports absent rather than
guessing.

## Why

Lowering re-derives types the checker already proved: `local-inference`
(PS1001 `needs an annotation before use`), `return-inference`
(`needs a return annotation`) and the global-slot type scan. scriptc lowers
TypeScript off `tsc`'s checker; the equivalent here is mypy-as-library, whose
`mypy.build.build()` exposes the expression TypeMap (`BuildResult.types`)
and per-node inferred types. Feeding it across the boundary retires those
fences without inventing a second inference engine inside lowering.

## What mypy can and cannot prove (measured on mypy 2.4.0)

- Expressions: the TypeMap covers assignment targets, call results and
  sub-expressions in the compiled module. `x = 1 + 2` proves `int`.
- Return types: for an annotated `def`, `FuncDef.type` carries the signature.
  For an unannotated `def`, mypy types the body but keeps the signature
  `None`; the bridge infers the return from the return-statement
  expressions, widening literals (`Literal[1]` and `Literal[2]` both widen
  to `int`), and answers only when every return agrees. `return 1` /
  `return "s"` stays fenced. `Any`/`unknown` (an unannotated parameter
  flowing out) stays fenced.
- Parameter types: mypy never infers unannotated parameter types; they stay
  `Any`. The bridge therefore un-fences a parameter only when something
  else proved it (a stub-driven overload result), which today means:
  param-inference fences stay. The family shrinks later, not here.

## Determinism

IR now depends on the pinned mypy version, not just the source. Consequences:

- The bridge is pinned `>=2.4,<2.5` and refuses to run outside the range,
  so a mypy upgrade cannot silently change emitted binaries.
- The A/B `irscore` discipline (run twice, `cmp`) is unchanged; a probe run
  with a bridged compile must record the mypy version next to the oracle
  version (`oracle` field already carries the CPython version).
- The frozen v2 gates are untouched: this PR never regenerates
  `IR_HASHES.json`, and the default (no bridge) compile path is
  IR-identical to master by the A/B comparison.

## PS11xx fences stay

Representational fences are not retired by the bridge. A bridged fact
translates only into IR types the subset already lowers: scalars,
homogeneous lists/sets, str/int-keyed dicts, tuples, user classes, and the
existing `T | None` optional widening. Multi-arm unions, protocols,
typevars, callables and unknown kinds return None and keep their fence.
`facts_to_ir` in `factsbridge.py` is the whole translation surface.

## Alternatives considered

- Make the bridge the default once mypy is installed. Rejected for now: it
  changes IR on installed-extra presence, which breaks the "same source,
  same IR" property the scorer and the binary cache rely on. Revisit after
  the census shows the bridged fences are sound on the corpus.
- Consume pyrefly's TSP map instead (it is already the facts oracle).
  The `pyrefly/typeFacts` bulk request is non-standard and lossy (kinds
  without full parameter maps); mypy's TypeMap is complete for expressions
  today. A `PyreflyBridge` behind the same `CheckerBridge` surface is the
  follow-up if the mypy pin becomes a liability.
- Extend lowering's own inference instead. Rejected: it duplicates the
  checker and drifts from it; the checker is the oracle by design
  (`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`).
