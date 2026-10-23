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

## The follow-up families, measured (issue #258)

The four inference families after the bridge landed, measured on mypy 2.4.0
and pyrefly 1.4.x and on the compat2 sources (the bridged census,
`benchmarks/compat2/census_bridged.py`, run over the 18 top100 packages):

| family | plain | bridged | retired |
| --- | --- | --- | --- |
| local-inference | 245 | 62 | 183 |
| return-inference | 57 | 54 | 3 |
| param-inference | 187 | 187 | 0 |
| uninitialized-field | 2 | 2 | 0 |

`name-resolution` moves the other way (508 to 389): a local the checker
proved binds, and the name-resolution fence that followed it retires with
it. No package's total fence count rose under the bridge; the families that
gained fences are shifts, where a site that used to fence as an inference
fence now fences under another family (a walrus target the checker proved
but lowering does not lower, for example).

On the scripts corpus (the adoption ladder's R1 rung, 45 single-file
scripts) the bridge retires one local-inference fence and no package
regresses, so the scripts corpus is not inference-bound.

- `local-inference` and `return-inference`: retired by the bridge wherever
  the checker proves a type the subset can express. This is the win the
  bridge exists for.
- `param-inference`: stays checker-bounded, permanently. Measured on both
  lanes: mypy types an unannotated parameter `Any` and never revises it
  from the body, a default, or a call site; the callee type at a call site
  is `(a: Any, b: Any) -> Any` for an unannotated callee, and a
  protocol-typed context reports the argument as incompatible rather than
  back-propagating a type into the def. The stub-driven overload result the
  design hoped for exists (e.g. `sorted`'s `key` parameter resolves through
  the typevar), but it answers the stub's parameter, not the user def's, and
  a def is only provable when mypy checks its caller with the def as the
  argument, which mypy does not do for unannotated functions. The census
  confirms it on the corpus: 0 of 187 param-inference fences retire. Verdict:
  the family is not retire-able by either lane without inventing types; the
  fix is annotations, not inference.
- `uninitialized-field`: stays a lowering-side rule. The expression TypeMap
  does not carry flow/init analysis, and mypy's init map is not exposed
  through `BuildResult` (`narrowed_type` is per-reference, not per-field, and
  requires re-walking every read). Pricing the extraction: a second
  module-level walk of mypy's symbol table per file, to answer a question
  lowering already answers in one pass over `__init__`'s assigned names. The
  bridge would add cost and a second source of truth for no retired fence
  (0 of 2 on the corpus). Verdict: the family stays lowering-side.

Both verdicts keep the acceptance bar: no default-path change, no invented
types, the frozen v2 gates untouched.

The census also settles the default-on question the design doc left open: a
default-on flip would make the IR depend on the presence of an installed
extra, which breaks the "same source, same IR" property the scorer and the
binary cache rely on. The census shows the bridge is sound on the corpus
(no package regressed), so the flip is a packaging decision, not a soundness
one, and it stays off.

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

The fallback lane landed as `--bridge pyrefly` (issue #258): a
`PyreflyBridge` in `factsbridge.py` that feeds pyrefly's TSP facts (the
`pyrefly/typeFacts` bulk request for expressions, the per-def
`typeServer/getComputedType` signature for parameters and returns) through
the same `CheckerBridge` surface. Measured on pyrefly 1.4.x, the two lanes
answer the same questions with the same limits: pyrefly resolves an
unannotated parameter to `Any` exactly like mypy, and computes `Any` for an
unannotated def's return even when every return statement agrees, so the
bridge recovers it from the return expressions' own facts and answers only
when they all agree. The lanes differ in scope, not in answers: the mypy
lane holds the full TypeMap for one in-process build, the pyrefly lane
reuses the long-lived facts-oracle subprocess. The bridge version recorded
by `irscore --bridge` is the pyrefly wheel version for this lane and the
pinned mypy version for the other.
- Extend lowering's own inference instead. Rejected: it duplicates the
  checker and drifts from it; the checker is the oracle by design
  (`.agents/plans/2026-10-18-tiered-lowering-and-typefacts.md`).
