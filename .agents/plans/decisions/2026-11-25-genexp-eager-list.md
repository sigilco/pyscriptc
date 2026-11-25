# Recorded decision: generator expressions keep the eager-list lowering (G4d, #449)

Status: Proposed 2026-11-25

Refs: #449 (G4d, this decision), plan `.agents/plans/2026-10-23-generalized-generator-lowering.md` phase G4, #442 (G3b, the generator frame machinery the lazy form would reuse), #433 (G1), #434/#436 (the D1-D4 typing/attribute decisions), `docs/reference/divergences.md` entry 109, `probes/divergences/d109_genexp_eager_list.py`.

## Decision

**Keep the eager-list lowering of generator expressions.** A genexp materializes its elements into a list at the point of evaluation, the way a list comprehension does, and the observable differences from CPython's lazy generator are recorded as divergence 109. The lazy form (a synthesized anonymous generator function per plan phase G4) is a separate implementation ticket; it is not scheduled by this decision, and the divergence stands until it lands.

## Why

The eager model is the R24 iterator model, and it is the one the whole iterator surface already uses: `iter`/`next`, `map`/`filter`/`reversed`, `range`/`enumerate`/`zip` as stored values, and the `itertools` subset all materialize into a list or a cursor over one (`tests/test_r24_iterators.py`, `src/pyscriptc/restlower/_iterators.py`). A genexp that lowered lazily while `map` and `filter` stayed eager would give the same source two different iteration semantics depending on which spelling the author used, and the corpus's iterator-heavy packages (`pyparsing`, `click`, `tomlkit`, `attr`) use both spellings interchangeably.

The lazy form is a real IR change, not a lowering tweak. It needs a synthesized anonymous generator function (a new frame class per genexp site, the G3b frame machinery applied to a construct that has no `def`), a new value type for the genexp result (the frame, not a list), and a re-typing of every consumer that today reads the result as a list (`sum`, `list`, `for`, `all`/`any`, the `itertools` adapters). The plan sizes that as effort M and gates it on a recorded divergence decision precisely because genexp output is in the compat2 corpus; this is that record.

The blast radius of the divergence is small and measured. Of the 147 visible compat2 probes, 4 carry a genexp (`lang.GeneratorExp.arg/assign/cond/loop`), and all four are the read-only shape (`sum(x for x in xs)`) that the eager model answers byte-identically. Of the 20 corpus scripts, none uses a genexp. Of the 292 vendored package sources, 71 files carry 263 genexp sites, but those are source files the census measures, not scored probes, and the census already counts their fences under the `generators` family (filelock 5, iniconfig 1). The divergence only bites when a program mutates the source iterable between the genexp's creation and its iteration, or drains the genexp twice, or relies on the element expression's side effects being deferred; none of the scored corpus does any of these.

## The seams the divergence records (entry 109)

1. **Creation-time side effects.** `(f(i) for i in range(3))` calls `f` three times before the first `next`; CPython calls it once per step. A program that prints from the element expression, or that depends on the expression not running when the genexp is never drained, sees different stdout.
2. **The captured iterable.** CPython captures the outermost iterable by reference; the eager list captures its elements by value. A mutation of the source list between creation and iteration flows through in CPython and not in the compiled program.
3. **Single-use.** CPython's generator is exhausted after one drain; the eager list drains again. A second `for` over the same genexp yields the elements again where CPython yields nothing.

The read-only shapes stay byte-identical and are not part of the divergence: `sum(x for x in xs)`, `list(x for x in xs)`, a `for` over a genexp, and a genexp passed to `all`/`any` all drain the source once with no mutation in between, and the compiled program answers the same bytes as CPython (`tests/test_r24_iterators.py::test_genexpr_materializes`).

## Rejected alternatives

- **Land the lazy form now.** The IR change is real (a new frame class per site, a new value type, a re-typing of every consumer) and the corpus does not need it: the scored probes and scripts are all read-only. The plan sequences G4 after G1-G3 for a reason, and G3b (#442) only just landed the frame machinery the lazy form would reuse. Doing it now would be an unforced IR change with no corpus receipt.
- **Fence genexps entirely.** The read-only shapes are common and byte-identical; fencing them would remove working, correct code to avoid a divergence that only bites on mutation or double-drain. The fence would also be a regression against the R24 model the rest of the iterator surface already ships.
- **Record nothing.** The parity contract is load-bearing: anything not listed is claimed identical, so a difference that is not listed is a bug. The eager model is a real, observable difference on programs that compile today, so it must be listed.

## Consequences

- Divergence 109 is added to `docs/reference/divergences.md` and the ledger (`docs/reference/divergence-ledger.json`, row D109) with the probe `probes/divergences/d109_genexp_eager_list.py` pinning the three seams on both sides.
- The lazy form stays on the plan as phase G4's implementation ticket, unscheduled by this decision. When it lands, entry 109 is rewritten to describe the remaining seams (the ones the lazy form still does not close) and the probe is regenerated.
- No frozen gate is touched: `outcomes.py`, `score.py`, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl` and `TIR_HASHES.json` are unchanged. The ledger and `divergences.md` are regenerated artifacts, not frozen gates.
- The `itertools.count`/`cycle` T0 refusal is unaffected: an unbounded source stays a hard fence regardless of the genexp model, because the eager list of an unbounded source is a hang and the lazy form of one is still unbounded.
