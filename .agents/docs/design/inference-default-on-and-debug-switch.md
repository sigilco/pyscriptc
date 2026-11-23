# Inference default-on and the debug-only switch question (follow-up to #1283)

Status: design note, 2026-11-18, session rsG. Research note, not a decision. The decisions.md row proposed at the end is what turns it into one.

Owner direction (2026-11-17): typing is a QUALITY lane, not an ADMISSION lane. Inference is on by default, the `--infer` option is dropped and no `--no-infer` flag is added, because the references do not offer one. This note takes the one open question out of that direction: does default-on inference need any escape hatch at all, and if so, is it a documented flag, a debug-only switch, or nothing? It then prices the preprocessing and gating stack that makes default-on affordable (the skip fast path #1325, the fact cache #1327, the bench suite #1328) and names the checks still missing.

Inputs: `.agents/docs/design/loose-typing-and-default-inference.md` (rsA, the default-on decision), `.agents/docs/design/inference-perf-baseline.md` (rsD, the cost baseline and gate design), `.agents/docs/design/typing-quality-lane.md` (rsC, the quality-lane direction), `.agents/docs/pyscriptc-debug.md` and `.agents/docs/debug.md` (the existing debug surface), `src/pyscriptc/inferrunner.py`, `src/pyscriptc/infercache.py`, `src/pyscriptc/engine_gate.py`, `benchmarks/baseline/`, plus the peer sources cited inline.

## Question

Two sub-questions. (1) Every compiler and checker that infers types ships some relation between "inference runs" and "the user can turn it off". What exactly do mypy, pyright, mypyc, Cython, Codon, Numba, tsc, Sorbet, Rust and Go expose, is any of it a documented on/off switch, is any of it debug-only, and what do their bug reports say about people reaching for the off switch? From that: should pyscriptc ship no switch, a debug-only switch (`PYSC_DEBUG` topic or hidden flag, for bisecting compiler bugs), or a documented flag, and how does the answer interact with the already-proposed "no `--no-infer`" decision row? (2) Default-on is affordable only because of the preprocessing stack. What does the stack cover today (skip fast path, per-module fact cache, bench suite, report-only gate), which additional checks (cache validity, determinism A/B, regression budget) are worth adding, and what does each cost?

## Evidence

### What the peers actually expose

The survey's headline: every peer treats inference as inseparable from the pipeline. Not one of the ten surveyed offers a documented user-facing switch that turns inference off. The escape hatches that exist are all either diagnostic-severity controls (turn off the *errors inference feeds*, not inference itself), scoped opt-outs with stated costs (per-file, per-module), or unstable/debug-only switches reserved for the compiler's own developers. That distribution is the evidence the owner's direction rests on.

Mypy. Type checking of unannotated bodies is itself a severity setting (`--check-untyped-defs`, default False, https://mypy.readthedocs.io/en/stable/command_line.html): unannotated functions are simply not checked, which is mypy's answer to "off", but the inference machinery itself has no switch. Notably, mypy's own options file carries debug-only toggles that look exactly like the bisect tools this note is considering: `disable_expression_cache` is commented "Disable expression cache (for debugging)" and `old_type_inference` is "Disable new type inference algorithm" (`mypy/options.py`, https://github.com/python/mypy/blob/master/mypy/options.py). `old_type_inference` is the closest peer precedent for a debug-only inference-behaviour switch: internal, undocumented in the user docs, used to bisect regressions in the inference rewrite. The bug-report trail around it (the 2022-2023 inference-algorithm rework, python/mypy#11508 and the issues it tracks) shows the pattern: when inference changes break real code, users bisect by toggling the internal flag, and the fix is in the inference, not in users living on the off switch.

Pyright. `typeCheckingMode` (`off/basic/standard/strict`) and the per-rule severity table control which errors are *reported*; there is no flag that stops inference, and the config docs are explicit that file comments may only raise severity over config, never lower it (https://github.com/microsoft/pyright/blob/main/docs/configuration.md). When users report surprising inferences, the resolution is always a narrower type or a `# type: ignore`, never an inference switch (the long trail of `explicit-type-argument` and inference-surprise issues lands in the same place). The off-position that exists is turning checking off per file, which is a scoping decision, not a debug tool.

mypyc. Never had an inference flag because mypy's inference is the pipeline (https://mypyc.readthedocs.io/en/stable/introduction.html): the checker's output *is* the lowering input. The closest thing to an off switch is "don't compile this module" (`mypyc.ini` exclusion), which removes the module from the native tier entirely, and that is an admission decision, exactly the category the owner's 2026-11-17 direction removes from pyscriptc.

Cython. The important negative result. Cython has the most switch-shaped surface of any peer and it is instructive in both directions. `infer_types` is a directive with three values (`None`/`True`/`False`), default `None` meaning "safe inference only" (https://docs.cython.org/en/latest/src/userguide/language_basics.html). `annotation_typing=False` turns annotation-driven typing off (the `@cython.annotation_typing(False)` escape, seen in `tests/run/annotation_typing.pyx`). And the maintainer answer to the project's most famous inference bug is literally "you can set `#cython: infer_types=False` as a compiler directive" (https://github.com/cython/cython/issues/3895, the UnboundLocalError-versus-random-int bug). So Cython does ship off switches, and users do reach for them when inference misbehaves. But read what that bought Cython: (a) the switches are per-module compile directives, not global modes, so the off state is a local bandage, not a supported configuration; (b) the bug reports show inference bugs fixed in Cython itself while users silently run on `infer_types=False` and lose the optimization silently (the changelog carries a steady stream of inference-correctness fixes: #8002 "Type inference depended on arbitrary set ordering when circular references are involved", #7991, #7994, #7937, #7922), and the per-module off switch hides them; (c) the Cython docs are explicit that turning inference up or down is a *safety/performance* dial inside one module, not an admission control. The lesson for pyscriptc is not "ship the switch", it is "a switch converts inference bugs into silently degraded binaries and postpones the fixes".

Codon. Inference is the compiler: Hindley-Milner over the whole program, no off switch of any kind, and the FAQ states the model outright ("type inference and specialization replacing much of the work normally performed by the interpreter", https://docs.exaloop.io/start/faq/). The flags Codon ships disable *semantics* (`-disable-exceptions`) or optimization passes (`-disable-opt`), never type checking (https://docs.exaloop.io/start/usage/). When Codon cannot infer, the compile fails; there is no "compile it untyped" fallback. Codon is the extreme of the spectrum pyscriptc sits at the other end of, and it exposes nothing to toggle.

Numba. The inference unit is the decorated function: `@njit` requests compilation and type inference; the off switch is deleting the decorator, which returns the function to pure CPython. There is no per-compilation inference toggle and no global mode; `NUMBA_DISABLE_JIT=1` exists (documented for testing, it makes every jit decorator a no-op) and is the nearest debug-only analogue in the survey, but it disables the entire compiler, not inference within it.

tsc. Inference is not a flag; `noImplicitAny` and the `strict` family control what happens when inference *fails* to find a type (error versus silent `any`, https://www.typescriptlang.org/tsconfig/noImplicitAny.html). There is no documented way to stop tsc from inferring; the TS performance wiki's advice for inference-cost pain is structural (isolate with interfaces, avoid cross-file inference), not a switch (https://github.com/microsoft/TypeScript/wiki/Performance). Users who want "no inference on this file" get `// @ts-nocheck`, which turns checking off entirely and is understood as a local suppress, not a mode.

Sorbet. Sigils control which errors are reported and, below `typed: true`, whether inference runs at all ("Sorbet does not run type inference over `# typed: false` files at all", https://sorbet.org/docs/lsp-typed-level). Two relevant details: the `--typed` CLI flag force-overrides every sigil, documented explicitly "for adoption experiments" (https://sorbet.org/docs/cli-ref), and the DEBUGGING section of the same page carries genuinely debug-only switches (`--stress-incremental-resolver`, "simulate updates which tend to expose namer and resolver bugs") whose stated purpose is exactly bug-finding in the resolver. Sorbet is therefore the peer with the clearest precedent for both halves of this note: a scoped, documented opt-out that is an *adoption* tool, and a hidden, debug-only stress switch that is a *compiler-bug* tool, and the two never blur.

Rust. Inference is the type system; no stable switch exists. What exists instead is the nightly `-Z` surface, which is explicitly the compiler team's own debugging tool (`-Z verbose-internals`, `-Z treat-err-as-bug`, `rustc_dump_*` attributes, https://rustc-dev-guide.rust-lang.org/compiler-debugging.html), and one directly on-point precedent: when the next-generation trait solver (a rewrite of inference/trait solving) rolled out on nightly, the rollback hatch was `-Znext-solver=coherence`, an unstable flag, with the explicit message "please open an issue if you encounter any bugs" (https://blog.rust-lang.org/2026/08/21/enabling-next-solver-on-nightly/). The Rust pattern for a risky inference change is: land it unstable with an internal revert switch, gather bug reports, fix, stabilize, and the internal switch is never a user feature.

Go. Inference is a language rule since 1.18 ("the job of type inference is solely to find the types of omitted type arguments", and what it finds is then fully checked by instantiation and assignability, https://go.dev/blog/type-inference). No compiler or checker flag touches it; the failure mode of a wrong inference is a compile error downstream, by construction. Go's design argues the soundness point: if every inferred value must pass the same checks a written one would, an off switch has nothing to protect the user from.

### The pattern, stated

Three tiers emerge across the survey. Tier 1, diagnostic controls: mypy `--check-untyped-defs`, pyright modes, Sorbet sigils, tsc `strict` family. They change which errors are reported, never whether inference runs, and they are the only switches users are documented to use. Tier 2, scoped opt-outs that double as escape hatches: Cython directives, Sorbet `--typed`, Numba decorator removal, mypyc module exclusion, tsc `@ts-nocheck`. Every bug report where a user escapes an inference bug through one of these shows the same cost: the degraded state is silent, the bug reports slow down, the fixes wait. Tier 3, debug-only switches: mypy `old_type_inference` / `disable_expression_cache`, Sorbet `--stress-incremental-resolver`, rustc `-Z` flags, Numba `NUMBA_DISABLE_JIT`. All unstable, hidden or explicitly testing-only, all maintained *for the compiler's developers to bisect their own bugs*, and none documented as a user surface. No peer ships a documented global inference off switch. The owner's direction ("no `--no-infer`, references do not offer one") matches the survey exactly.

### The existing pyscriptc debug surface

The host for a tier-3 switch already exists. `pyscriptc compile --debug[=topics]` and `PYSC_DEBUG=<topics>` select debug topics (`stages`, `fences`, `cc`, `paths`, `verify`; `.agents/docs/debug.md`), and the `stages` table already prints the `infer` row with its outcome (`infer: skipped ... N.Nms` or `infer: P pinned, C call site(s) N.Nms`) since #1325. The env var takes topic lists, so `PYSC_DEBUG=noinfer` is an additive topic, not a new mechanism. The audit log (`.agents/docs/pyscriptc-debug.md`) is JSONL keyed on events; an inference-disabled compile would be visible there as a missing `infer` event, which keeps bisection evidence auditable. This is the same shape as Sorbet's debugging options and mypy's internal flags: a switch whose user is the pyscriptc developer, never advertised in user docs, documented in `.agents/docs/debug.md` only.

The existing knobs around inference today: `PYSC_NO_INFER_CACHE=1` (cache off, #1327), `PYSC_NO_CHECK_OVERLAP=1` (serial check order, debug.md). Both are already debug-shaped env vars set by the team for the team; `PYSC_DEBUG=noinfer` joins that family rather than starting a new one.

### What default-on costs today, and what the stack already covers

The rsD measurements (`.agents/docs/design/inference-perf-baseline.md`) are the reason the stack exists: a zero-pin tree paid 12.7x compile time for inference before the skip path; the 164-module tree paid 309 s cold. Since then, the three landed pieces cover the cost and the measurement:

- Skip fast path (#1325, `src/pyscriptc/engine_gate.py`): a tree with no unannotated parameters never starts the checker server. This makes the common annotated case free and is the single most important cost property.
- Per-module fact cache (#1327, `src/pyscriptc/infercache.py`): facts keyed on module content hash, compiler digest, checker identity, and in-tree dependency content hashes; warm trees pay no server; writes are atomic; corruption is a miss; `PYSC_NO_INFER_CACHE=1` disables it.
- Bench suite (#1328, `benchmarks/baseline/` inference class): wordcount, the 42-script tree and a synthetic tree, recorded per release via `bench.yml`, report-only for two releases before any ratio is enforced.

What the stack does not yet cover, and what each missing check would cost, is the Options section below.

## Options

### Option 0: no switch at all

What it is: drop `--infer`, add nothing. Inference runs on every compile that has unannotated parameters and there is no way to disable it, not even for the developers.

For it: simplest surface, matches Codon and mypyc exactly, zero documentation and zero test cost. The soundness rule (pins never override written annotations; a wrong pin either fails the tree-agreement check or leaves the PS1001 fence standing) means an inference bug degrades to fences or extra pins, never silently different semantics on annotated code.

Against it: bisecting an inference regression then requires editing source, and the Cython/rustc evidence says the developers' own revert switch is the tool that makes the rollout's bug reports actionable. rsD's own cache design needed `PYSC_NO_INFER_CACHE=1` for exactly this reason.

Verdict: rejected. A debug-only switch is cheap and the peers' debug surfaces show it is standard practice, not scope creep.

### Option 1: debug-only env topic, `PYSC_DEBUG=noinfer` (recommended)

What it is: `noinfer` joins the existing debug topic list. `PYSC_DEBUG=noinfer` (or `--debug=noinfer`) skips the engine entirely: no checker server, no pins, the compile behaves exactly as pre-default-on, and the `stages` table prints `infer: disabled (noinfer)` so the state is never silent. Undocumented in `docs/`, documented in `.agents/docs/debug.md` next to the other topics. No CLI flag.

Why this shape and not a hidden CLI flag: the debug surface is already env-topic based, so this adds no parser surface and cannot graduate into a supported flag by becoming discoverable in `--help`; an env var also survives across a bisect script unchanged. Why debug-only and not documented: the survey's tier 2 shows documented opt-outs become load-bearing crutches (Cython users living on `infer_types=False` while the bugs they bandaged stay unfixed), and the owner's direction is that the references offer no such switch. Why it exists at all: the tier-3 precedents (mypy `old_type_inference`, rustc `-Znext-solver=coherence`, Sorbet's stress options) are the tools that let a team land a risky inference change and respond to bug reports by bisecting instead of reverting the feature.

Interaction with the "no `--no-infer`" decision row: consistent, and the row should say so explicitly. The row's rejected-alternatives cell already lists a documented `--no-infer`; this note adds `PYSC_DEBUG=noinfer` as the named exception and the reason: it is a developer bisect tool (tier 3), not a user escape hatch (tier 2), it prints its state in every debug run it affects, and it is not documented in user docs. If the owner prefers, the row needs no change at all: the debug topic does not contradict "no `--no-infer` option", it is not an option. One guard: a test asserts that `--help` never lists it and that a compile with `PYSC_DEBUG=noinfer` sets the summary's `infer.skipped = "disabled"` (observable, so a user reporting a fence bug with the topic accidentally set is diagnosable from their own paste).

Cost: small. One branch at the engine gate (`engine_gate.py`), one line in the stages table, one doc paragraph in `.agents/docs/debug.md`, three tests (disabled means zero server starts; disabled means pins absent; help text absent). Maintenance: one more boolean the pin tests must cover; the existing `--infer` tests become the with-and-without pair.

### Option 2: documented per-invocation flag

What it is: a real `--no-infer` in `--help`, documented in the typing guide as a troubleshooting tool.

Verdict: rejected. It contradicts the owner direction and the reference survey; it recreates the Cython crutch dynamic; and every bug it would have bisected is bisectable by Option 1. The one thing a documented flag buys that a debug topic does not is user-side escape from a pin-driven regression in a shipped release, and that scenario is what the fence (PS1001 stands) and the divergences ledger are for: the release response is a fix, not a flag.

### Option 3: per-module pragma opt-out only (`# pyscriptc: no-infer`), from the rsA note

What it is: the rsA note's per-module pragma, without any global switch: a module opts itself out, the rest of the tree defaults on.

Verdict: gate on evidence. The pragma is an *adoption governance* tool (the Sorbet ratchet, rsA stage 3), not a debug tool, and it carries the tier-2 crutch risk in per-module form. It should not land with default-on; it lands if the rollout produces evidence that specific modules genuinely need to stay annotation-explicit (measured by the `infer --report` coverage report: pin-driven lowering changes that a team wants to veto). Until then it is unused surface. Keep it out of v0.11.0 and re-read the coverage report after one release on the default.

### The gating stack: what is covered, what to add, what each costs

Covered today: skip fast path (#1325), fact cache with content+digest+dependency+checker keys (#1327), bench suite report-only (#1328), the `infer` timing row in stages and `summary.json`, the coverage report (`infer --report`, #1320), and the existing perf gate's 2x/1.25x ratios over runtime (compile time recorded but not gated).

Additions, each priced:

1. Cache-validity self-check (cheap, do now). A unit-level property test suite over `infercache.py`: a change to any key component (module text, compiler digest, checker identity, one dependency hash) must invalidate the entry, corruption must miss, and a warm tree must perform zero server starts (assert via the engine's skip counter, the same signal the #1325 test uses). Cost: a day of tests, no runtime cost in the compiler. This is the highest-value missing check because the cache is now load-bearing for every repeat compile.
2. Determinism A/B for the pin set (medium, do before default-on flips). The rsA note names non-determinism of pin sets as a named risk (adding a caller can change a callee's pins) and Cython shipped exactly that bug class (#8002, set-ordering-dependent inference producing non-deterministic C). The check: run `build_facts` twice per bench tree in the #1328 suite, hash the reduced pin set both times, fail the run on mismatch, and additionally run one tree with a shuffled module processing order to flush order dependence. Cost: one extra pass per bench tree per release run (report-only today, so zero CI gate risk), one small harness change in the suite. This should become enforced in the same wave the perf ratios graduate.
3. Pin-set snapshot in the compile summary (cheap, do with option 1). rsA requires "the compile should record the pins it applied"; `summary.json` already carries the `infer` dict (skipped, pins, callSites, ms) from #1325, so the remaining work is recording the pin list itself (callee, parameter, type) so a "the binary changed and I did not touch the file" report is diagnosable from the paste. Cost: a few lines serializing what the engine already has in hand.
4. Regression budget on compile time (free now, armed later). The #1328 suite records `compile_ms` and `infer_ms`; the budget lines are rsD's (no-pin trees show inference time 0; warm repeat adds at most 10%; cold wall trended per release). Nothing to build: the suite exists. The only decision is the date the ratios stop being report-only, which per the `bench.yml` discipline is after two releases of data, i.e. after v0.11.0 ships default-on.
5. Warm/cold cache A/B in the suite (small, do before default-on). One bench run per tree with a cold cache dir and one with a warm one, both recorded, so the P1 "repeat compile pays no server" claim is measured per release rather than asserted. Cost: a second suite pass (the warm run dominates wall time on the big tree, but the suite is release-gated, not per-PR).

Rejected additions: interface-hash incremental recheck (rsD P2) and parallelism (rsD P3) stay gated on the post-P1 numbers exactly as rsD left them; this note adds nothing to those. A "pins must be reproducible across pyrefly versions" gate is rejected: the checker identity is already in the cache key (#1327), so a checker bump invalidates instead of compares, which is the correct and cheap behaviour.

## Recommendation

Do now, in one PR with the default flip (or immediately after it, before release): `PYSC_DEBUG=noinfer` as a debug topic (option 1), with the three tests and the `.agents/docs/debug.md` paragraph; the summary records the applied pin list (addition 3); the cache-validity property tests (addition 1); and the determinism A/B plus warm/cold runs added to the #1328 suite (additions 2 and 5, report-only). The decisions.md row for the default flip should state: no `--no-infer` option and no user docs switch; `PYSC_DEBUG=noinfer` exists as a developer bisect tool, invisible to `--help` and documented only in agent docs; per-module pragma deferred pending coverage-report evidence. Gate on evidence: the pragma (option 3, re-read `infer --report` one release after the flip) and rsD's P2/P3. Reject: a documented `--no-infer` (option 2) and no-switch-at-all (option 0).

## Issues to file

- `PYSC_DEBUG=noinfer` debug topic: engine gate branch, `infer: disabled (noinfer)` stages row, `summary.json` `infer.skipped = "disabled"`, zero-server-start test, help-text-absence test, `.agents/docs/debug.md` paragraph. Milestone v0.11.0, effort S.
- Pin list in `summary.json`: serialize applied pins (callee, parameter, type) alongside the existing `infer` dict. Milestone v0.11.0, effort S.
- Cache-validity property tests for `infercache.py` (all four key components invalidate, corruption misses, warm tree zero server starts). Milestone v0.11.0, effort M.
- Determinism A/B in the bench inference suite: run `build_facts` twice per tree, hash the pin set, fail on mismatch; one shuffled-order tree. Report-only now, enforced when the perf ratios graduate. Milestone v0.11.0, effort M.
- Warm/cold cache A/B rows in the bench suite per release. Milestone v0.11.0, effort S.
- Per-module `# pyscriptc: no-infer` pragma: file for Later, entry evidence is one release of `infer --report` coverage data after the default flips. Effort M.

## The proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Default-on inference ships with no `--no-infer` option and no user-facing switch; the only disable path is the debug topic `PYSC_DEBUG=noinfer`, a developer bisect tool documented in agent docs only, shipped with the pin list in `summary.json`, cache-validity tests and a determinism A/B in the bench suite | Every surveyed peer (mypy, pyright, mypyc, Cython, Codon, Numba, tsc, Sorbet, rustc, Go) ships inference as inseparable from the pipeline, offers only diagnostic-severity controls to users, and reserves inference-behaviour toggles for debug surfaces (mypy `old_type_inference`, rustc `-Znext-solver`, Sorbet stress options), while Cython's user-facing off directive shows the crutch cost of documented opt-outs | A documented `--no-infer` (contradicts the reference survey and recreates the Cython crutch), no switch at all (bisecting inference regressions then needs source edits), the per-module pragma now (adoption governance, gate on coverage evidence) | `.agents/docs/design/inference-default-on-and-debug-switch.md` |

## Related

- `.agents/docs/design/loose-typing-and-default-inference.md`: the default-on decision and staged rollout this note arms.
- `.agents/docs/design/inference-perf-baseline.md`: the cost baseline whose stack (skip path, cache, bench suite) this note extends.
- `.agents/docs/design/typing-quality-lane.md`: the quality-lane direction that removes admission control from the typing surface.
- `.agents/docs/debug.md` and `.agents/docs/pyscriptc-debug.md`: the debug topic and audit surfaces `noinfer` joins.
- `src/pyscriptc/inferrunner.py`, `src/pyscriptc/infercache.py`, `src/pyscriptc/engine_gate.py`: the engine, cache and gate the additions touch.
- `benchmarks/baseline/` and `.github/workflows/bench.yml`: the suite and workflow carrying additions 2 and 5.
