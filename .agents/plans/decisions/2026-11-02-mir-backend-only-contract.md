# Draft recorded decision: MIR is the backend-only contract

Status: DRAFT for owner sign-off, 2026-11-02. On approval this is the decision row that unblocks roadmap R4 (3AC MIR) and frames R5 (backend migration); until then no MIR schema lands. Input: `.agents/plans/2026-11-02-ir-rearchitecture-roadmap.md` and `.agents/notes/2026-11-02-ir-rearchitecture-reassessment.md`.

## Decision (proposed)

The 3AC MIR (`pyscriptc/mir.py`, R4) is the backend-only contract: the only IR the `emit/llvm/*` path reads once R5 completes, owned by lowering, verifiable (`pyscriptc/mirverify.py`), and not a semantic source of truth. The typed HIR (`pyscriptc/_irnode.py`) is the semantic source of truth: everything that must agree with the source language - spans-adjacent diagnostics, fence attribution, generator semantics, the canonicity gate - is defined and checked at the HIR. No backend reads the HIR; no semantic pass reads the MIR.

MIR canonical hashes are recorded in the same new probe file as the HIR hashes or a sibling beside it (`probes/TIR_HASHES.json` and, when R4 lands, a `mir` section or sibling file under the same regen ritual). MIR stability is a backend property, checked the same way: byte-stable across two runs, changed only through the repin ritual with a recorded reason.

## Why

The reassessment's option (b) survives its own adversarial check on exactly this split. A 4-int instruction over parallel lists is the arena at the level where data-orientation pays (pass loops, not tree walks); block arguments replace phi placement; the backend contract shrinks from reading roughly 60 AST kinds to roughly 15 ops; DCE and constant prop become flat loops. But the MIR must not become the thing semantics are defined over: instruction selection detail (QBE-flavored `w/l/s/d` widths, register-level identities) would drag diagnostic and fence decisions into a form the source no longer resembles, which is the failure mode that made the dict IR expensive to change.

Keeping the semantic/source-of-truth role in the HIR also keeps the strangler (R3) and the backend migration (R5) independent: R3 slices convert lowering surfaces against an unchanged emitter through a compatibility accessor, and R5 slices convert emitter mixins against an unchanged HIR, and neither needs the other mid-slice.

## Alternatives rejected

- One IR, MIR everywhere (the flat arena as the only representation): the reassessment's option (a). Rejected as the HIR contract on human cost (index arithmetic over parallel lists as the semantic surface of a fast-moving lowering surface) while retained as exactly the right MIR shape.
- Keep the dict IR as the backend contract and only type the HIR: leaves `["kind"] ==` dispatch (249 sites, concentrated in `lower/_calls.py` 72, `lower/_expr.py` 66, `lower/_stmt.py` 54) untouched, which is the emitter-side half of the maintenance tax.
- LLVM IR or the emitted text as the stable contract: far too low; register allocation, scheduling and target detail would freeze under the gate.

## Consequences

- R5's generator question stays a HIR-level question: generators stay on the HIR path until re-verified (roadmap owner checklist row), because generator semantics are semantic.
- The divergence ledger re-verification (items 19-21/24/25 through MIR, roadmap R5) is a backend conformance claim, evidenced by byte-identical stdout through the MIR path.
- MIR schema changes cost one baseline regeneration under the new gate ritual, the same as HIR changes.

Where: the roadmap decision checklist row "MIR backend-only contract" blocks R4.
