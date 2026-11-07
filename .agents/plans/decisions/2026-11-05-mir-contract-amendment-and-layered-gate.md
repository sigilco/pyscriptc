# Draft recorded decision: MIR contract amendment and the layered gate

Status: Draft 2026-11-05, awaiting owner signature (tasks D4 and D5 of the v0.9.0 compiler plan)

## Decision (proposed)

Amendment to `2026-11-02-mir-backend-only-contract.md`. MIR is the backend contract for every function the MIR slice covers. The direct-emit path (typed HIR remains the middle stage for every function) is a bounded, measured exception: its share is tracked as slice coverage percent and reaches zero as the tail (str, list and dict values, try handlers, closures) lands. The clause "no backend reads the HIR" holds again at the v0.10.0 drop.

Flip bar for v0.9.0: at least 80 percent of non-fenced corpus functions lower to MIR, with the typed-HIR adapter carrying the rest. A strict 85 percent bar needs the str value type (A8) on the critical path and costs about one week; the default taken is 80 percent with A8 in the v0.10.0 tail.

Layered gate. Per-function byte identity (`test_mir_leaf`) stays through v0.9.0. A runtime A/B differential (compile both lanes, run both, diff stdout, stderr and return code) is added as the release gate. Whole-module byte equality is retired at the flip, because module-scope text was never semantic: the runtime header, ABI declarations, helper bodies and entry point are shared code in both lanes, and module declaration order was PYTHONHASHSEED dependent until D1.

## Why

Full MIR coverage needs value types and a try CFG (two XL items). Waiting for them delays the migration by months. A bounded exception with a measured share keeps the architecture direction and lets the default flip on a safe, escape-hatched path.

## Alternatives rejected

- Strict 85 percent before the flip: slips v0.9.0 by about a week for four functions.
- Keep whole-module byte equality as the flip gate: it guards incidental shape and breaks for reasons unrelated to semantics.
- No adapter, MIR for everything: not reachable before the str and container value types exist.

## Consequences

- The seam tracker gains a slice-coverage metric (D6) and the nightly publishes it.
- The escape hatch (env) restores the old path until the v0.10.0 drop.
- Kill criteria: if the A/B harness is not reliable within two sessions, or slice coverage is under 60 percent at the scope check, the default flip moves to v0.10.0.
