# Lowering strategy options: how to sequence fence work

Date: 2026-10-25. Status: strategy assessment for the owner, docs only. Question: how should fence-lowering work be sequenced now that the island floor gives weekly use and the typed subset is the scope? Positioning anchor: `.agents/plans/2026-10-25-typed-subset-positioning.md` (decision plan, pending on branch `work/docsplit`, not yet on master): the typed subset runs native, the island is the floor, the native lane is the ceiling, and there is no CPython byte-for-byte parity goal.

Constraints taken as given: an 8 GB laptop plus a small VPS, limited premium-model capacity, most implementation done by cheap models (Haiku and Flash class), weekly use of the compiler by the owner, gains measured against CPython 3.14, low maintenance churn, and the frozen compat2 gates.

## What the measurements actually say

There is no single fence number, and the three counts that are easy to quote answer different questions. Using one as the target for "reduce fences per month" leads the work somewhere the owner does not need.

| Measurement | Command or source | Reading today | What it answers |
| --- | --- | --- | --- |
| Self-census | `benchmarks/compat2/census_self.py`, asserted in `tests/test_census_self.py` | 3,057 fences in 100 modules, 48,462 lines; 395 of 1,778 definitions native (22.2%), 1,383 fenced (77.8%) | how much of the compiler's own Python the compiler can compile |
| R0 real scripts | `uv run python -m benchmarks.real_scripts.measure` | 39 scripts, 316 fences, 2 scripts with zero fences | user-facing scripts, the closest proxy for the owner's weekly use |
| PyPI corpus | `compat/data/compat.json` (generated 2026-10-25 at `b0e8246`) | 18 packages, 2,765 fences: 2 full, 6 partial, 10 none; builtins 2 full, 15 partial, 1 none; stdlib 0 full, 54 partial, 1 none | dependency surface |
| Boxed ledger | `uv run python benchmarks/compat2/boxed_cost.py --window-commits 10` | newest window `270592ae..d706ecf6`: code 4.3% (3,263 of 75,731 lines), churn 34.2%, bugs 0.0%, tax 10.0%, drop_flag false | what the island floor costs |

The self-census is a moving target and today it moves up, not down: the assertion went from 3,030 to 3,057 in the last ten commits, and it was 1,434 in the Stage U era before the emitter, adapters and CLI grew. New compiler features add fences faster than lowering removes them, so a monthly "fences removed" target on this census mostly measures how much dynamic argparse, subprocess and JSON-RPC plumbing was written that month. It is concentrated, too: `cli` alone holds 511 fences with all 47 of its definitions fenced, and the top 12 modules hold 1,578 fences, 52% of the total. Lowering the compiler's own CLI glue is real work, but it is not work the owner feels.

The R0 corpus is the better scoreboard because it is fixed, vendored, and third party (`benchmarks/real_scripts/scripts`, pinned commits in the manifest). The committed `benchmarks/real_scripts/REPORT.md` records 386 fences and 0 of 39 scripts compiling on 2026-10-24; the same tool today reports 316 fences and 2 of 39 (`check_file`, `snake_case_renamer`). That is 70 fences and 2 scripts in one day, which shows what a burst aimed at measured blockers can do, and it also means the committed report and `docs/reference/what-will-fail.md` are one generation stale.

The island floor is working and that changes the question. Per the Dogfood3 comment on issue #592, 6 of 8 sibling scripts run with output matching CPython 3.14 under `--fallback=cpython`, and 8 of 8 pure-Python PyPI packages run at package level, with three new blockers filed (#626, #627, #628). Milestone 4 (v0.7.0) already carries those receipts plus "a census delta receipt" as exit criteria, so fence-lowering is not optional in this release, but it is also not the thing that makes weekly use possible.

## The fence accounting that should drive the sequence

The R0 corpus groups cleanly by root cause, and the groups are small enough to reason about exactly:

| Root cause | Families | Fences | Scripts touched | Effort in the blocker model |
| --- | --- | --- | --- | --- |
| inference | name-resolution 98, param-inference 23, local-inference 23, unlowered-type-name 10, return-inference 4, generic-alias 2, dynamic-annotation 1, forward-reference 1, annotation-other 1 | 163 | 30 | S per family except name-resolution (L) |
| call-table | unlowered-call 29, call-arity 27, keyword-args 2 | 58 | 28 | S to L |
| stdlib-surface | subprocess 7, time 5, os 5, json 3, pathlib 1, itertools 1, dataclasses 1, random 1 | 25 | 13 | M each |
| classes | field-lookup 21 | 21 | 6 | S |
| statements | construct-other 18, module-assign 2 | 20 | 10 | S to L |
| checker | type-mismatch 20 | 20 | 10 | M |
| imports | import-table 6 | 6 | 6 | M |
| operators and other | operator 2, reflection 1 | 3 | 3 | M to L |

Fences cascade, so a script compiles only when every family it carries is fixed. Simulating the actual per-script family sets from the measurement JSON gives the compounding table below, and it is the most useful table in this document. Fixing inference alone releases 3 scripts; fixing call-table alone releases 4; fixing both releases 13, and adding classes reaches 14, adding stdlib-surface reaches 18. Two workstreams get a third of the corpus (13 of 39), and no single workstream releases more than 4 scripts on its own.

| Fix set | Scripts compiling (of 39) | Fences left |
| --- | --- | --- |
| inference only | 3 | 153 |
| call-table only | 4 | 258 |
| stdlib-surface only | 3 | 291 |
| inference + call-table | 13 | 95 |
| inference + call-table + classes | 14 | 74 |
| inference + call-table + classes + stdlib-surface | 18 | 49 |
| all families | 39 | 0 |

The remaining 49 fences after those four workstreams are the genuine long tail: checker 20, imports 6, statements 20, operators 2, reflection 1, spread over 21 scripts. They are not one project, they are twenty small ones, and several (reflection, the checker's type-mismatch) are structural: the checker rejecting a real type mismatch is the compiler working, not starving.

The self-census long tail is different and mostly out of scope by decision. `dynamic-annotation` (714 fences, 80 modules) is dominated by `Any` on adapters that talk JSON-RPC and subprocess, which the typed subset refuses by design; `unlowered-type-name` (390) is types with no IR value representation; `name-resolution` (622) is unannotated locals whose fix is inference, not a new lowering. That is why a strategy measured on "fences removed from the self-census" would spend months on the compiler's own plumbing and move the R0 number by very little.

## Option A: current approach, release-grouped lowering

Work is planned per milestone and lands in the release that needs it. v0.6.0 shipped the threading primitives and the string-model phases; v0.7.0 (milestone 4) is library consumption, island floor granularity, verification and assessment; v0.8.0 (milestone 5) is the compute two-plane with FFI and ORT.

Expected reduction: the historical unit is a targeted PR dropping 10 to 40 fences (the census comments in `tests/test_census_self.py` record a 20-fence `list.append` flip and a 19-fence dogfood drop), with one outlier (local inference #500, 341 fences). At 2 to 4 targeted PRs a month that is 40 to 90 R0 fences and 3 to 6 scripts a month while the corpus lasts, which reaches the 13-to-18-script rows over two to three release cycles rather than one. Self-census gross reduction of 100 to 200 a month is plausible but net is flat because features add fences.

Perf: native lowered code runs the `benchmarks/baseline` suite at a geomean 4.5x CPython 3.14 on this host (json-roundtrip 4.0x, struct-pack 4.3x, deque-ring 5.2x, recorded in `.agents/plans/2026-10-24-apps-and-package-strategy.md`). Unlowered code keeps CPython speed on the island.

Churn: near zero. Code is written once against the LLVM/C target and kept by regression tests.

Gates: low. New lowerings add handlers in `src/pyscriptc/lower/` and, when the IR changes for a construct that already has a probe, they touch `probes/*.jsonl` and `probes/IR_HASHES.json`, which needs the recorded A/B discipline and cannot be regenerated on macOS. Fences by definition emit nothing, so most additions do not move existing hashes; inference changes can, because they can change how already-lowered code is typed.

Cost: mixed. Call-table and stdlib additions are mostly "emit a call to a C helper" and are cheap-model work; inference, IR shape and control flow are premium work. A family costs roughly 3 to 8 cheap sessions and 1 to 2 premium ones.

What must be true to win: the owner accepts a multi-release horizon, and the R0 corpus stays the scoreboard so the release themes stay aimed at measured blockers.

## Option B: advance lowering now, release later

Same lowering work, decoupled from the release train: long-lived branches or a single flag, merged when the release opens.

Expected reduction: 1.3 to 1.5x option A for the first weeks, then decay. The decay is measurable: the compiler churned 2,445 lines in the newest 10 commits, so a two-week branch rebases against a few thousand changed lines, and the island work lands in `src/pyscriptc/lower/__init__.py` and `_stdlib.py`, which a lowering branch edits as well.

Perf: identical to A once merged.

Churn: moderate to high. The code is not thrown away, but the branch work is. Feature flags also multiply the test matrix: the same program has to be gated in both configurations, and compat2's verdict and calibration files assume one configuration.

Gates: moderate. Any flag that changes default lowering can drift `coverage_map.py` or the probes, and the gate reruns must be done per configuration.

Cost: high premium. Cheap models are weakest exactly here: rebasing stale branches and holding invariants across flag configurations. This is the worst fit for the actual model budget.

What must be true to win: several parallel developers, or a premium quota large enough to keep branches continuously rebased. Neither is true today.

## Option C: brute-force breadth, naive universal lowering

Lower everything now with boxed values, dynamic dispatch, and generic runtime helpers, accepting about 1.2x CPython, then optimize.

Expected reduction: an initial burst that could clear 163 inference fences and 58 call-table fences in one or two months and reach 39 of 39 scripts. But the burst is not free: it needs a second value model (boxed dynamic values) next to the typed one, method dispatch by name, and generic operators, which is the machinery the island already provides through CPython. Effort is 6 to 10 weeks of mostly premium work, not the 1 month the option implies.

Perf: poor to mediocre, and the honest range is wide: 0.5x to 1.3x CPython on the data path, because CPython 3.14's interpreter has inline caches and a boxed C runtime that mallocs per operation often loses. The typed fast path survives only where both models meet, and mixing them costs a type check at every boundary.

Churn: near total for the emitter. `src/pyscriptc/emit/` is 4,993 lines and the typed runtime is 26,516 lines of C across 42 files; a boxed lane duplicates the value model, and the eventual optimization pass rewrites the code that produced the boxed values. This is the two-migration outcome the positioning decision rejected.

Gates: extreme. `probes/IR_HASHES.json` is anchored on emitted LLVM text; a new value model changes every hash. `CALIBRATION.json` and `coverage_map.py` assume one lowering shape. The frozen tag `gates-v2` would need a recorded decision and a full re-baseline before any of this could merge.

Cost: moderate cheap cost up front (boxed helpers are easy to generate) followed by a large premium triage bill for memory, cycles and crash bugs on the corpus.

What must be true to win: a requirement to compile untyped Python with no libpython anywhere. The positioning decision explicitly removes that requirement, and the island answers it today at CPython speed.

## Option D: transpile through another language

The decisive fact first: fences are produced by the lowerer, `src/pyscriptc/lower/` at 14,674 lines, not by the emitter, `src/pyscriptc/emit/` at 4,993 lines. Changing the emit target cannot lower a construct the lowerer refuses, unless the target supplies dynamic values, which is Option C wearing a different name. For a typed target the fence effect is exactly zero; the only thing that changes is how the already-lowered subset is emitted.

| Candidate | Emit path and toolchain | Runtime and GC | CI on macOS, Linux, Windows | License | Compile time | Semantic fit against our 42 divergences | What we lose | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Nim | emits C, so the existing `zig cc` step still consumes it, but adds a Nim compiler (choosenim, brew, apt, scoop; not pip installable) | ARC/ORC, deterministic RC with cycle collection, small runtime | Linux and macOS easy, Windows via scoop, all new to CI | MIT | two-hop, moderate | exceptions and iterators map well; i64 overflow abort (divergence 1), codepoint `str` (3), `re`/`json` (4, 5) need wrappers | direct IR control, our DWARF emitter, IR hash anchoring, one more toolchain | reject |
| Zig | already present as the driver (`src/pyscriptc/zigshim.py`); no new dependency | no GC, no classes, no exceptions, no async; we would write the Python runtime in Zig | good, all three | MIT | fast | poor: errors are sets, not tracebacks; strings are bytes | replaces 26,516 lines of working C with unwritten Zig and lowers no fence | reject |
| Swift | `swiftc` only, not `zig cc` | ARC plus a large runtime library (order of tens of MB, estimate) | macOS easy, Linux needs a toolchain, Windows heavy | Apache 2.0 with LLVM exception | slow | typed throws, grapheme strings, not code points | single-binary size, CI time, the 8 GB budget | reject |
| Mojo | proprietary Modular toolchain, cannot be driven by `zig cc` | ownership, no dynamic GC | headless install across three CI OSes is not a supported story | proprietary | unknown | systems semantics, not dynamic Python | reproducibility and openness | disqualified |
| Crystal | `crystal` compiler, not `zig cc` | Boehm conservative GC | Linux and macOS; Windows experimental | Apache 2.0 | very slow, memory hungry (whole-program inference can exceed the 8 GB budget, estimate) | Ruby-like object model, not Python's | Windows, memory budget, compile feedback loop | reject |
| Rust | `rustc` and `cargo`, not `zig cc`; about 1 GB toolchain | no GC; `Rc<RefCell<..>>` for dynamic graphs, cycles need weak refs | good, all three | MIT or Apache 2.0 | slow, LLVM codegen and macros | panic is not exception flow; no class inheritance | cheap-model productivity (borrow checker), build time | reject |
| C as the emit target | plain C11 through the existing `zig cc`, no new dependency | reuses `src/pyscriptc/runtime/*.c`, 26,516 lines, and its semantics define all 42 divergences | good, all three | MIT | fast, low memory | exact: the runtime is the divergence contract | SSA-level control, IR hash anchoring on LLVM text (needs re-anchoring on C text or AST hashes) | keep as an experiment only |

Expected reduction: zero fences for Nim, Zig, Swift, Mojo, Crystal and Rust as typed targets; also zero for C. The only version that lowers anything is "C with a boxed value model", which is Option C and carries its gates and churn.

Perf: within 1.5x to 5x CPython for a good target language on typed code, but nothing beats the current path, because the current path already emits optimized LLVM.

Churn: for the C target, the throwaway is a new emitter (order of 5,000 lines) while the lowerer and runtime survive; for the others it is a new runtime plus an emitter, which is a rewrite of the part that works.

Gates: extreme for every variant that changes emitted bytes. The C target needs a recorded decision to re-anchor `probes/IR_HASHES.json` on something other than LLVM text, and `irscore.py` A/B identity would have to be redefined.

Cost: 3 to 6 months to reach parity on the 42 divergences, mostly premium, and no fence reduction to show for it.

What must be true to win: that cheap models cannot produce correct LLVM SSA but can produce correct C for the same lowering. That is a testable claim, and it is the one thing worth measuring (see the experiment).

## Option E: the hybrid I would choose

E keeps the positioning decision and sequences the work in four passes, by measured return rather than census count:

1. Inference breadth first. One workstream (param, return, local inference, then the name-resolution cascade it causes) covers 163 of 316 R0 fences and 30 of 39 scripts, and it is the cheapest per script because name-resolution fences disappear without their own lowering. It is premium work, so it sets the pace.
2. Call-table breadth second, built as C runtime helpers with a single IR call site rather than elaborate AST lowerers. `unlowered-call`, `call-arity` and `keyword-args` cover 58 fences and 28 scripts, and the same helpers serve the PyPI corpus where stdlib calls dominate (2,765 fences over 18 packages). Cheap models do this well because the pattern is "write a C function with a fixed signature, emit one call".
3. Island floor protected, not grown. The floor already delivers 6 of 8 scripts; its job is to keep weekly use alive while 1 and 2 land. The boxed ledger stays the brake: two consecutive windows above 20% churn, or a tax window above 15%, raises the flag and stops island feature work.
4. Classes and stdlib-surface third, to cross 18 of 39. Only then the long tail (checker, imports, statements, operators), ordered by scripts released rather than fences.
5. Optimize only where measured. `pyscriptc assess --bench` before any perf work; the baseline suite is already 4.5x, so the native lane does not need speculative optimization.

Expected reduction: 40 to 90 R0 fences and 3 to 6 scripts a month, with the first two workstreams reaching 13 of 39 and four workstreams reaching 18 of 39. Self-census native definition share should rise a few points a month; the self-census fence total may still rise, and that is not a failure signal under this framing.

Perf: no change to the ceiling (the typed subset keeps the 4.5x baseline), and the floor stays at CPython speed.

Churn: very low. All three workstreams are additive; nothing here is designed to be deleted, and the island keeps its marked regions (`boxed:begin` and `boxed:end`) so it stays deletable.

Gates: low to moderate. C helpers and new call table entries do not move existing probes; inference can, so it lands behind the A/B hash discipline and is the one workstream that needs the macOS regeneration rule respected (say in the PR that the IR changed, and regenerate on Linux).

Cost: the cheapest mix available. Roughly 70% of the work (call-table helpers, stdlib functions, tests, corpus runs) fits cheap sessions; the 30% premium goes to inference and any IR shape change.

What must be true to win: the owner accepts scripts-released and native-definition-share as the scoreboard, and accepts that the island is the floor rather than a temporary scaffold.

## The numbers side by side

These projections are estimates. The method: R0 family counts from today's measurement, effort labels from the blocker model in `benchmarks/real_scripts/measure.py`, the historical 10 to 40 fences per targeted PR from the census comments, and the machine and model budget stated at the top. They are not a promise, and the uncertainty section says what would move them.

| Dimension | A: release-grouped (current) | B: advance ahead of release | C: brute-force breadth | D: transpile | E: hybrid |
| --- | --- | --- | --- | --- | --- |
| R0 fences per month | 40 to 90 | 55 to 130, decaying | 220+ once, then stalls | 0 | 40 to 90, front-loaded on inference |
| R0 scripts compiling per month | 3 to 6 | 4 to 8, decaying | up to 39 in 2 months | 0 | 3 to 6, then 13 of 39 from two workstreams |
| Self-census effect | native share up 1 to 2 points; total flat or rising | same | total drops sharply, native share collapses | none | same as A |
| Perf outcome | 4.5x geomean native, island at 1x | same | 0.5x to 1.3x CPython | 1.5x to 5x | 4.5x native, island at 1x |
| Maintenance churn | near zero | branch work lost, code kept | near-total emitter rewrite | new emitter, 5,000+ lines, for C only | near zero, additive |
| Risk to gates-v2 | low; inference may move hashes | moderate, per-config reruns | extreme, every hash | extreme, re-anchor required | low to moderate |
| Cheap model share | 60% | 30% | 50% up front | 20% | 70% |
| Premium capacity | 1 to 2 sessions per family | 2 to 4 sessions per family, plus rebases | 6 to 10 weeks of premium work | 3 to 6 months of premium work | 1 to 2 sessions per family, inference-heavy |
| Time to weekly use | already available via the island | same | 1 to 2 months | 6 to 12 months | already available via the island |
| Fits 8 GB laptop and VPS | yes | yes | yes | C only; Rust and Crystal are memory risks | yes |
| Winning condition | long horizon, corpus scoreboard | parallel developers or large quota | no-libpython mandate | cheap models cannot write LLVM | island accepted as the floor |

## The experiment worth running (under one day)

The most promising adversarial option is not "transpile to Rust"; it is the narrow C-text emission question inside Option D: can a cheap model land call-table lowerings faster when the new call is emitted as C text than when it is emitted as LLVM SSA? If yes, the C path is a productivity tool for one workstream. If no, the whole D family is closed for good.

Design. Use five vendored R0 scripts as sources of representative call sites: `work_connect` (stdlib:subprocess 4), `json_to_csv` (stdlib:json and one unlowered call), `organise` (2 unlowered calls), `cat` (1) and `batch_file_rename` (1). Implement the three unlowered calls that appear across them as C runtime helpers, each a fixed signature plus one IR call site, which is the normal Option E pattern. Then generate each call site's enclosing function twice from the same lowered form: once through `src/pyscriptc/emit/llvm/` as today, once through a throwaway C text writer of at most 300 lines that calls the same runtime. Compile a small driver per site against both artifacts, run both plus CPython 3.14 on the same input, and compare stdout byte for byte.

Measure, per script and per target: number of cheap-model attempts needed to reach a green build and matching output, wall time per attempt on the 8 GB laptop, peak compiler memory, compile time of the emitted artifact, binary size, and the count of new runtime lines the target forced. Also record whether the C target needed a boxed value type anywhere (that is the tell that it has become Option C).

Kill criteria, all decided before the run: stop if the C target needs a boxed or dynamic value type for any of the five scripts, if any emitted binary exceeds 1.5x the LLVM binary size, if compile time exceeds 2x, or if the C path does not cut cheap-model attempts by at least 40% across the five. A win changes only the per-PR cost of the call-table workstream, whose unlowered-call part is 29 R0 fences across 16 scripts and which releases no script on its own, so it cannot change the sequence in this document, only the speed of step 2.

## Recommendation

Adopt Option E and say so in the milestone. Concretely: v0.7.0 keeps its island-floor gate and adds an inference workstream plus a call-table workstream, both aimed at the R0 blockers rather than the self-census; v0.8.0 keeps compute two-plane and absorbs the classes and stdlib-surface work; the long tail is ordered by scripts released and never by raw fence count.

Do not adopt B as a standing mode. The branch rot is not hypothetical: 2,445 compiler lines changed in the last ten commits, and the island work rewrites the same lowerer files a lowering branch would touch. A B-shaped burst is acceptable only as a branch of a week or less, behind one flag, in a release already planned.

Do not adopt C. It buys 39 of 39 scripts at CPython-ish speed, which is the island's job, and pays for it with the typed emitter, every frozen hash, and two migrations. The positioning decision already refused it.

Do not adopt D as the sequencing strategy. For typed targets it lowers no fence at all, because the lowerer, not the emitter, decides what compiles. Keep exactly one piece of it, the C-text experiment above, because it tests a real claim about cheap models cheaply.

The measurable commitments this implies: report `scripts compiling` and `native definition share`, not fences removed; regenerate `benchmarks/real_scripts/REPORT.md` and `docs/reference/what-will-fail.md` as the v0.7.0 census delta receipt (they are one generation stale: 386 versus 316); and keep the boxed window current in AGENTS.md, which this assessment already updates from the 2026-10-23 reading of 0.5% code and 0% churn to 4.3% and 34.2%, tax 10.0%, flag still false.

## Questions the owner must answer

1. Is the success metric scripts compiling (2 of 39 today) and native definition share (22.2%), or fences removed from the self-census (3,057 and rising)? The two imply different work.
2. For v0.7.0, is CPython-speed island output acceptable as "weekly use" for the 6 of 8 scripts that work, or must a named subset (which ones) run native in that release?
3. How many premium sessions per week are actually available? That number decides whether inference (premium, 163 fences) leads or call-table (cheap, 58 fences) leads.
4. Do we authorize the one-day C-text experiment, or is `emit/` LLVM-only by decision and closed to further targets?
5. Do we regenerate the R0 report and the what-will-fail page now, as the v0.7.0 census delta receipt, or defer both to the milestone close?
6. Which three to five scripts define "the owner's weekly use"? Pinning them makes the scoreboard concrete and stops the census from drifting toward compiler plumbing.
7. If the boxed ledger's churn stays above 20% for a second window, does island work stop in favor of lowering, as the AGENTS.md drop rules say, or does the v0.7.0 gate take precedence?

## Uncertainty

The inference estimates are the softest. The blocker model labels param, return and local inference S, but the R0 families are symptoms of one another, and the 98 name-resolution fences may not all vanish when annotation inference improves; nobody has landed that work at this scale, and the 341-fence local inference drop (#500) was a different corpus.

The cheap-model share per workstream is an assumption, not a measurement. It is testable in a week: have a cheap model write three C runtime helpers and three inference rules, and count revisions.

The perf ranges for C and the other targets are judgments from the shape of the work, not measurements on this codebase. The one measured perf fact here is the baseline suite at a 4.5x geomean.

The boxed window is volatile: 4.3% code and 34.2% churn in the newest window against 2.1% and 18.5% in the one before, because the island work is concentrated. One window is not a trend, and the drop flag needs two consecutive ones.

The committed R0 report is stale relative to the tool, so every number quoted from `REPORT.md` here was re-measured with the tool on 2026-10-25. If the difference is a measurement change rather than real lowering, the 70-fence day is smaller than it looks; the 2-of-39 versus 0-of-39 script count is the part to trust, because it comes from the same tool version.
