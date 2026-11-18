# Inference perf baseline before default-on ships (follow-up to #1283)

Status: design note, 2026-11-18, session rsD. Research note, not a decision. The decision row proposed at the end is what turns it into one.

Inputs: `.agents/docs/design/loose-typing-and-default-inference.md` (#1283, default-on decision), `.agents/docs/design/island-promotion-and-annotation-surface.md` (rsB), `benchmarks/baseline/README.md` and `benchmarks/baseline/run.py`, `src/pyscriptc/inferrunner.py`, `src/pyscriptc/cli/_main.py`, `.github/workflows/bench.yml`, plus the peer sources cited inline. Measurements were taken on this host (Linux x86_64 VPS, other agent sessions active, so absolute numbers are load-noisy but the ratios are stable) against `origin/master` at `0606b392`, CPython 3.14 via `uv run`.

## Question

Inference runs by default after the staged rollout of the #1283 note. What does that cost today, what budget and gate keep it bounded, and which perf machinery (fact cache, incremental recheck, parallelism) must land before the default flips rather than after?

## Current behaviour, measured

The pin engine (`pyscriptc infer`, `src/pyscriptc/inferrunner.py:211` `build_facts`) walks the tree, opens every module in one pyrefly `tsp` server session, collects call-site facts, then re-runs `pyrefly infer` over a mirrored pinned copy up to four passes (`_PYREFLY_INFER_PASSES`, `inferrunner.py:47`) to a fixpoint. There is no cache anywhere in that path: every invocation pays the full checker cost again. The only content-hash cache pyscriptc has is the `pyscriptc run` binary cache (`cli/_main.py:956`), and the dependency source cache under `deps.py:667`; neither caches inference facts or lowering artifacts per module.

Measured costs on in-repo trees (wall time, peak RSS of the `pyscriptc` process tree):

| Tree | Command | Wall | Peak RSS | Notes |
| --- | --- | --- | --- | --- |
| examples/wordcount (2 defs) | compile | 1.4 s | 64 MB | no pins needed |
| examples/wordcount | compile --infer | 17.7 s | 64 MB | 12.7x slower for zero pins |
| examples/wordcount | infer (report only) | 17.1 s | 64 MB | checker 5.3 s + pinned mirror 5.4 s, 0 call sites |
| benchmarks/real_scripts/scripts (42 files) | infer --json | 27 s | 63 MB | checker 7.5 s, 128 call sites, 29 pinned params |
| src/pyscriptc (164 modules) | infer --json | 309 s | 63 MB | checker 80 s, pinned mirror 88 s, 9,833 call sites, 750 pins |
| src/pyscriptc | compile --infer | 435 s | n/a | fences on PS2002 mass; inference ran before the fences |
| small kernels (baseline suite) | compile per workload | 0.97-2.8 s | n/a | `compile_ms` rows in a `--json` run |

Two facts stand out. First, the fixed cost of the inference engine dominates on small trees: a two-definition package pays 12.7x its compile time for a pin set of zero. Second, the cost scales with tree size roughly linearly in checker passes (80 s + 88 s of checker time inside 309 s wall on 164 modules; the rest is per-module AST walking and pin reduction in Python), and the wall number on a loaded host shows the reduction loop itself is a real share. Peak RSS stays near 63 MB in all runs: the engine is time-bound, not memory-bound, because each module's facts are discarded after reduction.

The existing perf gate (`benchmarks/baseline/run.py --check`, `mise run bench:baseline`) times runtime binaries against `reference.json` with thresholds of 2x time ratio and 1.25x RSS ratio, and records `compile_ms` per workload but does not gate it (`benchmarks/baseline/README.md`: "the report also keeps compile time ... those are not part of the ratio gate"). Nothing in any gate times inference. The `bench.yml` workflow runs on release tags only and is explicitly report-only ("nothing here fails on a regression: the threshold becomes a gate after two releases of data").

## What peers do, and what transfers

Vercel scriptc is the structural role model for the pipeline and it made its frontend skippable as a unit. "When none of a build's inputs has changed since an earlier build, the frontend's results come from the build cache and this stage is skipped" (https://scriptc.dev/docs/how-it-works, Frontend). Its build cache keys include the compiler itself, so a changed compiler invalidates everything; its `--emit-lowered` / `--from-lowered` pair lets backend phases (IR validation, LLVM emission, link) rerun without ever re-running the type checker or lowering. Its v0.4.2 release notes (https://github.com/vercel-labs/scriptc/releases/tag/v0.4.2) show the iteration pattern: batch checker queries ("answers contextual types and resolved signatures in batches ... instead of one per node", 7-12% off a whole build), run the memory-heavy frontend as a child process so the native generator starts after its memory is released (peak 2.21 GB instead of 3.30 GB), and partition large programs so a rebuild recompiles only changed partitions and their importers (https://scriptc.dev/docs/how-it-works, Rebuilds). tsc itself ships the same ideas as product surface: `--incremental` + `.tsbuildinfo` saves per-file state to re-check only the stale set (https://www.typescriptlang.org/docs/handbook/release-notes/typescript-3-4.html), project references + `tsc --build` give up-to-date checks per sub-project with `.d.ts` interfaces as the boundary (https://www.typescriptlang.org/docs/handbook/project-references), and the TypeScript performance wiki prescribes interface-based isolation and avoiding cross-file inference as the primary levers (https://github.com/microsoft/TypeScript/wiki/Performance).

mypy: coarse module-level incremental caching in `.mypy_cache` by default, and `dmypy` for in-memory daemon state with fine-grained dependencies between definitions, processing only triggered targets to a fixed point (https://github.com/python/mypy/wiki/Mypy-Daemon). The cache is per-module metadata keyed on mtime/size/hash plus dependency info; deserialization cost is a known tax (python/mypy#3456).

Pyrefly (pyscriptc's checker): module-granularity incrementality, parallel checking across modules in Rust, and the "type it and see" trick: a module is re-checked optimistically and downstream is invalidated only if its exported interface actually changed, not marked dirty transitively (https://github.com/facebook/pyrefly/blob/main/ARCHITECTURE.md, and the Meta interview at https://www.metacareers.com/podcast/open-sourcing-pyrefly-a-faster-python-type-checker-written-in-rust/). Its diagnostic-latency work refined dependency tracking from "any export changed rechecks all importers" to per-lookup type dependencies, 18x on their benchmark (https://pyrefly.org/blog/2026/02/06/performance-improvements/). On the cross-checker benchmark (https://microsoft.github.io/pyright/typecheck-benchmark/), pyrefly checks torch in 7.2 s / 673 MB where pyright takes 87 s / 4.9 GB.

ty: query-based memoized incrementality (Salsa) down to individual bindings, 4.7 ms re-diagnosis after editing a load-bearing file in PyTorch (https://astral.sh/blog/ty, https://docs.astral.sh/ty/features/language-server/).

pyright: `--threads` runs coarse file-level parallel workers, "3x or more" on large projects, with directory locality in queue assignment (https://github.com/microsoft/pyright/issues/8765, https://github.com/microsoft/pyright/blob/main/docs/command-line.md); the benchmark page shows the threads mode trades time for modest RSS.

The transferable lessons for pyscriptc, in order of fit: (1) key a cache on inputs including the compiler version, exactly as scriptc does, so correctness never depends on trusting stale facts; (2) treat "the exported interface did not change" as the invalidation boundary, as pyrefly does, which is cheap to check and usually true; (3) skip the whole engine when nothing needs it, as tsc skips stages whose inputs are unchanged; (4) parallelize at module granularity last, since the engine is a single Python process driving one checker server and the checker itself is already fast.

## Options

### Option P0: skip-fast-path and pin-driven compile budget in the gate

What it is: before any default flip, `compile` skips the engine entirely when the tree has no unannotated parameters (one parse pass over the entry tree, no checker server), and `benchmarks/baseline` gains an inference budget row that the `--check` gate actually enforces: compile time ratio and an inference wall-clock ceiling per host-normalized unit.

Cost: small. The parse pass is cheap; the budget row extends `run.py` where compile time is already recorded (`run.py:217`).

Why first: the wordcount measurement (1.4 s to 17.7 s) says the worst default-on regression is not the big trees, it is the small annotated ones that need no inference at all. The #1283 note already names the skip path as mitigation; this option makes it measurable and gated rather than hoped for.

Verdict: do now. It is stage 0 of any rollout: nothing can ship default-on while a zero-pin tree pays 12.7x.

### Option P1: per-module inference-fact cache

What it is: persist `InferFacts` per module under `~/.cache/pyscriptc/infer/`, each entry keyed by (module source sha256, compiler version digest, sorted dependency module content hashes that affect the module's facts, checker version). Reuse the existing `compiler_digest.json` pattern (`~/.cache/pyscriptc/compiler_digest.json`) for the compiler half of the key. Facts load lazily per module the way mypy's cache does; a module whose facts are fresh contributes its pins without a checker round trip. The package-wide pin reduction still runs over the merged per-module call sets, because the pins are a tree-wide fact by design (`inferrunner.py`, module docstring).

What peers do: mypy's `.mypy_cache` is exactly this shape (module metadata keyed on content plus dependency map, python/mypy#3456 documents the deserialization cost lesson: keep the serialized form small or lazy). scriptc keys its build cache on the compiler, options, and every input.

Correctness rule: a cached entry is valid only while the module text, the compiler digest, and the dependency hashes are unchanged. The dependency set for pin purposes is the modules the module imports from the tree (`_imported_names`, `inferrunner.py`), not the transitive closure: the pin reduction re-derives tree-wide agreement from the per-module call lists, so staleness is local. A checker (pyrefly) version change invalidates everything, the same way a compiler change does for scriptc.

Cost: medium. Serialization of call-site facts is a new format (facts are plain data: qualified callee names, argument type strings, positions, so JSON suffices initially). The risk is subtle staleness; the mitigation is that a wrong cache hit produces a pin that fails the tree-agreement check or fences, never a wrong lowering that annotations would have produced differently (the #1283 soundness rule: pins never override written annotations).

Verdict: do before default-on, after P0. It is what makes the 309 s tree number irrelevant for repeat compiles, which is the case default-on actually creates (every compile pays inference, so every compile is a warm-cache compile most of the time).

Landed (issue #1318, PR #1326): `src/pyscriptc/infercache.py` holds the per-module entries under the pyscriptc cache root (`infer/`), keyed on the module's content hash, the compiler digest (`runcache.compiler_digest`), the checker identity (resolved argv plus its reported version) and the content hashes of the in-tree modules the module imports from. `inferrunner.build_facts` loads the entries first and starts the checker server only for the modules that missed, so a warm tree pays no server at all; the pin reduction still runs over the merged per-module lists, so the pins are unchanged. The facts are plain JSON, the write is atomic (temp file plus rename), a corrupt entry is a miss rather than a failed compile, and `PYSC_NO_INFER_CACHE=1` turns the cache off.

### Option P2: interface-hash incremental recheck

What it is: stop re-running the four-pass pyrefly mirror when nothing that feeds it changed. Hash the pinned-signature surface of each module (the annotations the lowerer will apply, i.e. written annotations plus accepted pins) and skip a module's re-check when its own hash and the hashes of its in-tree imports' signature surfaces are unchanged from the cached pass. This is pyrefly's "type it and see" trick applied at the pyscriptc layer: optimistically assume fresh, verify the interface hash, and only invalidate importers when a signature surface actually changed.

Cost: medium-large. It needs the cached pass to record signature surfaces per module, and the mirror pass restructured per module rather than as one tree-wide `pyrefly infer` run. The current engine opens one server and processes the tree as a unit (`build_facts`), so this is a restructuring, not a bolt-on.

Verdict: gate on evidence. P1 removes most of the repeat-compile cost already; P2 matters only for the cold-path and for trees that change constantly during development. Measure the post-P1 cold number on the 164-module tree first: if cold inference stays under the budget, P2 waits.

### Option P3: parallelism

What it is: parallel module checking in the engine, pyright-style worker processes or splitting the tree across server sessions.

Cost: large. The engine is a single Python process coordinating one `tsp` server; parallelism means either N servers (N checker starts, the cost the engine was built to avoid, per the module docstring "the cost is one server start plus one document open per module, not one server per module") or a redesigned reduction that merges concurrent shard results. The checker itself is already fast (pyrefly checks torch in 7.2 s); pyscriptc's overhead is the Python reduction loop, which parallelizes poorly per module but could shard per subtree.

Verdict: reject for v0.11.0. The RSS profile (63 MB flat) and the pyrefly benchmark say the raw checker is not the wall. Revisit only if P1 plus P2 leave cold large-tree inference above budget on the gate.

## The budget and the gate

Proposed budget lines for default-on, measured by the extended baseline gate on the pinned bench runner (ubuntu-24.04, `bench.yml`):

- No-pin trees never pay inference: the skip path must show inference time 0 in the compile summary for a tree with zero unannotated parameters. This is a correctness property of the skip fast path, tested directly, not a threshold.
- Warm-cache repeat compile of a tree with pins: inference adds at most 10% over the no-inference compile of the same tree (P1's target).
- Cold-cache inference wall time: recorded as a trend per release with a 2x-over-reference failure like the existing time gate, first as report-only for two releases (matching the `bench.yml` discipline) then enforced.
- Peak RSS: inference must not raise the compile-time peak RSS beyond the existing 1.25x RSS ratio; today it does not (63 MB flat), so the gate just pins that fact.

The gate extension is a new workload class in `benchmarks/baseline` (an inference suite: the wordcount tree, the 42-script tree, and a synthetic tree of about 150 modules so the suite stays in-repo and hermetic), with `compile_ms` and `infer_ms` promoted from recorded-only to ratio-gated once two releases of data exist.

## Recommendation, staged

Stage 0 (do now, PR-sized): skip fast path plus inference timing surfaced in the compile summary and `--debug` stages table. No gate change yet.
Stage 1 (do now, parallel with stage 0): the in-repo inference benchmark tree set and the baseline-gate extension, report-only.
Stage 2 (do before default-on): the per-module fact cache (P1) keyed on content + compiler digest + dependency hashes. This is the load-bearing piece for default-on: it converts the per-compile cost into a first-compile cost.
Stage 3 (default-on itself, from the #1283 staging): flip the default with the budget from stage 1 enforced.
Stage 4 (gate on evidence): interface-hash incremental recheck (P2) if cold numbers on the gate exceed budget; parallelism (P3) rejected unless everything above lands and the gate still fails.

The ordering argument in one line: default-on without P0+P1 would multiply every compile's wall time on annotated trees by up to 12.7x measured, and the peers that ship type-driven lowering (scriptc, tsc) all made the frontend skippable and cached before they made it unconditional.

## Issues to file

- P0: skip fast path for inference (no unannotated parameters means no engine run) with a test asserting zero checker server starts on a fully annotated tree, and inference timing in the compile summary and `--debug` stages output. Milestone v0.11.0, effort M.
- P1: per-module inference-fact cache under the pyscriptc cache root keyed on module sha256 + compiler digest + in-tree dependency hashes + checker version, lazy load, invalidation tests including a checker-version bump. Milestone v0.11.0, effort L.
- Gate: inference suite in `benchmarks/baseline` (wordcount, the 42-script tree, a synthetic ~150-module tree), `infer_ms` recorded per release via `bench.yml`, ratio gate armed after two releases of data. Milestone v0.11.0, effort M.
- P2 (gated): interface-hash incremental recheck of the pinned mirror pass, per module, pyrefly-style optimistic validity. File for Later with the post-P1 measurement as the entry ticket's evidence requirement.
- Cross-check: the `pyscriptc infer` coverage report (issue #1304) should also print the engine's wall-clock split (checker seconds, mirror seconds, reduction seconds) so the budget lines above are observable per tree, not only on the bench runner. Milestone v0.11.0, effort S.

## The proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Ship default-on inference only after the skip fast path and the per-module fact cache land, with an inference suite in the baseline gate (report-only for two releases, then enforced); interface-hash recheck and parallelism gated on post-cache measurements | A measured zero-pin tree pays 12.7x compile time today and there is no fact cache at all, so the default must not flip before the cost is bounded and cached | Interface-hash recheck now (wait for post-P1 numbers), parallel checking (checker RSS is 63 MB flat and pyrefly itself is fast, the wall is the Python reduction) | `.agents/docs/design/inference-perf-baseline.md` |

## Related

- `.agents/docs/design/loose-typing-and-default-inference.md`: the default-on decision this note prices, and the staged rollout whose stage ordering this note amends.
- `benchmarks/baseline/README.md` and `benchmarks/baseline/run.py`: the gate this note extends.
- `src/pyscriptc/inferrunner.py`: the engine whose cost profile (one server, four mirror passes, no cache) is the subject.
- `.github/workflows/bench.yml`: the release-tag benchmark run whose report-only discipline the new gate follows.
