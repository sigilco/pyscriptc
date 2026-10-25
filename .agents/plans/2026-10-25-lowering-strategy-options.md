# Lowering strategy options: how to sequence fence work

Date: 2026-10-25. Status: strategy assessment for the owner, docs only, audited the same day (every number below was re-run in the `work/strategy` worktree unless it says "recorded" or "estimate"). Question: how should fence-lowering work be sequenced now that the island floor gives weekly use and the typed subset is the scope? Positioning anchor: `.agents/plans/2026-10-25-typed-subset-positioning.md` (on master): the typed subset runs native, the island is the floor, the native lane is the ceiling, there is no CPython byte-for-byte parity goal, and the island tier may not grow features while the boxed ledger says the compiler is starving.

Constraints taken as given: an 8 GB laptop plus a small VPS, limited premium-model capacity, most implementation done by cheap models, weekly use of the compiler by the owner, gains measured against CPython 3.14, low maintenance churn, and the frozen compat2 gates (tag `gates-v2`).

Evidence labels used below: "measured" means a command was run on 2026-10-25 and the output read; "recorded" means quoted from a repo file and not re-run; "estimate" means a judgment with the method stated; "upstream knowledge" means general knowledge of an external project that this repo cannot confirm and that must be re-checked at experiment time.

## What the measurements actually say

There is no single fence number, and the counts that are easy to quote answer different questions. Using one as the target for "reduce fences per month" leads the work somewhere the owner does not need.

| Measurement | Command or source | Reading (measured 2026-10-25) | What it answers |
| --- | --- | --- | --- |
| Self-census | `uv run python -m benchmarks.compat2.census_self` (asserted in `tests/test_census_self.py`) | 3,057 fences in 100 modules, 48,462 lines; 395 of 1,778 definitions native (22.2%), 1,383 fenced | how much of the compiler's own Python the compiler can compile |
| R0 real scripts | `uv run python -m benchmarks.real_scripts.measure` | 39 scripts, 316 fences, 2 scripts with zero fences (`check_file`, `snake_case_renamer`) | user-facing scripts, the closest proxy for the owner's weekly use |
| PyPI corpus | `compat/data/compat.json` (recorded, generated 2026-10-25 at `b0e8246`) | 18 packages, 2,765 fences: 2 full, 6 partial, 10 none; builtins 2 full, 15 partial, 1 none; stdlib 0 full, 54 partial, 1 none | dependency surface |
| Boxed ledger | `uv run python benchmarks/compat2/boxed_cost.py --window-commits 10` | newest window `af4f9de7410f..d97ff120fc07`: code 4.3% (3,263 of 75,731 lines), churn 33.3%, bugs 0.0%, tax 10.0%; previous window churn 24.8%, bugs 25.0%; drop_flag TRUE | what the island floor costs |

The boxed ledger result needs the owner's attention before anything else in this document. The drop-flag rule in `AGENTS.md` raises the flag when churn is above 20% in each of two consecutive windows, and the tool now reports exactly that (33.3% then 24.8%). An earlier run the same day, two docs-only commits older, read 34.2% then 18.5% and no flag, so the flag flipped on window alignment alone. Window 0 holds roughly seven code commits and three docs commits, and its boxed churn is 545 lines of the 1,637 compiler lines changed, which is the island floor stages being built (`boxed.py` was touched in four of those commits). Milestone 4 (v0.7.0) lists "drop flag not raised, owner informed of the raised share" in its exit criteria, so as measured today that criterion is not met and the rule in `AGENTS.md` says to stop adding boxed features and ask the owner. The `AGENTS.md` ledger line on this branch has been corrected to the new reading.

The self-census is a moving target and does not trend cleanly. The recorded assertions in `tests/test_census_self.py` read 3,049, 3,030, 3,035, 3,054, 3,062, 3,065, 3,046, 3,047, 3,057 over the last nine edits: a net rise of 8 with a 19-fence drop inside it. It was 1,434 at the v0.4.1 alpha package (`b07a48c0`), before the emitter, adapters and CLI grew. New compiler features add fences at about the rate targeted lowering removes them, so a monthly "fences removed" target on this census mostly measures how much dynamic argparse, subprocess and JSON-RPC plumbing was written that month. It is concentrated: `cli` alone holds 511 fences with all 47 of its definitions fenced (284 of them name-resolution), and the top 12 modules hold 1,578 fences, 52% of the total. The three biggest self-census families are dynamic-annotation (714, 80 modules), name-resolution (622, 38 modules) and unlowered-type-name (390, 31 modules). Lowering the compiler's own CLI glue is real work, but it is not work the owner feels.

The R0 corpus is the better scoreboard because it is fixed, vendored, and third party (`benchmarks/real_scripts/scripts`, pinned commits in the manifest). The committed `benchmarks/real_scripts/REPORT.md` records 386 fences and 0 of 39 scripts on 2026-10-24; the same tool today reports 316 fences and 2 of 39. I did not find which commits account for the 70-fence difference, so it may be real lowering or a measurement change. The committed report and `docs/reference/what-will-fail.md` are one generation stale either way.

The island floor is working and that changes the question. Per the Dogfood3 comment on issue #592, 6 of 8 sibling scripts run with output matching CPython 3.14 under `--fallback=cpython`, and 8 of 8 pure-Python PyPI packages run at package level. That comment filed #626, #627 and #628; #627 and #628 are now closed and #626 (a compiler crash on callable decorator expressions) is open. Milestone 4 (v0.7.0, 17 open issues) carries those receipts plus a census delta receipt as exit criteria, so fence-lowering is not optional in this release, but it is also not the thing that makes weekly use possible.

## The fence accounting that should drive the sequence

The R0 corpus groups by root cause using the families and root causes assigned by `benchmarks/real_scripts/measure.py`, and the groups are small enough to reason about exactly (measured from its JSON output):

| Root cause | Families | Fences | Scripts touched |
| --- | --- | --- | --- |
| inference | name-resolution 98, param-inference 23, local-inference 23, unlowered-type-name 10, return-inference 4, generic-alias 2, dynamic-annotation 1, forward-reference 1, annotation-other 1 | 163 | 30 |
| call-table | unlowered-call 29, call-arity 27, keyword-args 2 | 58 | 28 |
| stdlib-surface | subprocess 7, time 5, os 5, json 3, pathlib 2, itertools 1, dataclasses 1, random 1 | 25 | 13 |
| classes | field-lookup 21 | 21 | 6 |
| statements | construct-other 18, module-assign 2 | 20 | 10 |
| checker | type-mismatch 20 | 20 | 10 |
| imports | import-table 6 | 6 | 6 |
| operators and other | operator 2, reflection 1 | 3 | 3 |

Fences cascade, so a script compiles only when every family it carries is fixed. Simulating the per-script family sets from the measurement JSON gives the compounding table below, and it is the most useful table in this document. Two workstreams get a third of the corpus (13 of 39), and no single workstream releases more than 4 scripts on its own.

| Fix set | Scripts compiling (of 39) | Fences left |
| --- | --- | --- |
| nothing (today) | 2 | 316 |
| inference only | 3 | 153 |
| call-table only | 4 | 258 |
| stdlib-surface only | 3 | 291 |
| inference + call-table | 13 | 95 |
| inference + call-table + classes | 14 | 74 |
| inference + call-table + classes + stdlib-surface | 18 | 49 |
| all families | 39 | 0 |

The remaining 49 fences after those four workstreams are the genuine long tail: checker 20, statements 20, imports 6, operators 2, reflection 1, spread over 21 scripts. They are not one project, they are twenty small ones, and some (reflection, the checker's type-mismatch) are structural: the checker rejecting a real type mismatch is the compiler working, not starving. The effort labels in the blocker model (S for most inference families, L for name-resolution) are the tool's own guesses and are not validated.

The self-census long tail is different and mostly out of scope by decision. `dynamic-annotation` and `unlowered-type-name` cover types the typed subset refuses or has no value representation for, and `name-resolution` is unannotated locals whose fix is inference, not a new lowering. I have not verified the earlier claim that the dynamic-annotation fences are dominated by JSON-RPC adapters, so it is dropped. A strategy measured on "fences removed from the self-census" would spend months on the compiler's own plumbing and move the R0 number very little.

## Option A: current approach, release-grouped lowering

Work is planned per milestone and lands in the release that needs it. Milestone 4 (v0.7.0) is the weekly-use gate: island floor, library consumption, verification and assessment. Milestone 5 (v0.8.0) is the compute two-plane with FFI and ORT, per its milestone description.

Cost. The historical unit is a targeted PR dropping 10 to 40 fences (recorded in the `tests/test_census_self.py` comments: a 20-fence `list.append` flip, a 19-fence dogfood drop, and one outlier, local inference #500, 341 fences). At 2 to 4 targeted PRs a month that is 40 to 90 R0 fences and 3 to 6 scripts a month (estimate), so the 13-to-18-script rows take two to three release cycles. A family costs roughly 3 to 8 cheap sessions and 1 to 2 premium ones (estimate, no session accounting exists in the repo).

Perf. Native lowered code is several times faster than CPython on typed kernels. Recorded in `.agents/plans/2026-10-24-apps-and-package-strategy.md`: geomean 4.5x on the `benchmarks/baseline` data path. Re-measured today with `uv run python -m benchmarks.runtime.run --runs=3` on five workloads: sum-squares 10.2x, horner 8.1x, json-roundtrip 7.4x, struct-pack 5.2x, deque-ring 7.1x, geomean 7.4x. Three runs on a laptop is noisy, so treat the claim as "5x to 10x on typed kernels", not as a precise factor. Unlowered code keeps CPython speed on the island.

Maintenance churn. Near zero. Code is written once against the LLVM and C target and kept by regression tests.

Risk to frozen gates. Low. Fences emit nothing, so most additions do not change `probes/IR_HASHES.json`. Those hashes are sha256 over the canonical lowerer IR (`canonical-schema/2`, `.agents/docs/spec/ir-canonicity.md`), so only changes that alter the IR of an already-lowered probe move them, and inference changes can. They need the recorded A/B discipline and cannot be regenerated on macOS.

Kill criterion. Measured on R0 at each release close: at least 8 of 39 scripts compiling at v0.7.0 close and at least 13 at v0.8.0 close. If a release closes below its bar, stop release-grouping and reorder to the workstream with the best scripts released per session (this is the move to Option E's ordering).

First step. Pin the scoreboard: regenerate `benchmarks/real_scripts/REPORT.md` and `docs/reference/what-will-fail.md` once, and put the R0 script count and native definition share in the milestone 4 description.

What must be true to win: the owner accepts a multi-release horizon, and the R0 corpus stays the scoreboard so release themes stay aimed at measured blockers.

## Option B: advance lowering now, release later

Same lowering work, decoupled from the release train: long-lived branches or a single flag, merged when the release opens.

Cost. 1.3 to 1.5x the early fence rate of A for the first weeks, then decay (estimate). The decay is measurable: the compiler had 1,637 changed lines in the newest 10-commit window and 2,618 in the one before (boxed_cost.py compiler churn), and island work keeps editing `src/pyscriptc/lower/__init__.py` (three of the ten commits), `_stdlib.py` and `_calls.py`, which a lowering branch edits as well. Cheap models are weakest at rebasing stale branches and holding invariants across flag configurations, so this is the worst fit for the model budget (estimate, 2 to 4 premium sessions per family including rebases).

Perf. Identical to A once merged.

Maintenance churn. Moderate to high. The code survives, the branch effort does not. A flag that changes default lowering doubles the test matrix, and compat2's verdict and calibration files assume one configuration.

Risk to frozen gates. Moderate. A flag that changes default lowering can drift `coverage_map.py` or the probes, and the gate reruns must be done per configuration.

Kill criterion. Any B branch is closed if it lives longer than 7 days or needs more than two conflict resolutions in `lower/__init__.py`, whichever comes first.

First step. None recommended as a standing mode. If a burst is wanted, pick one workstream, branch from master the same day, cap it at a week, and merge in a release already planned.

What must be true to win: several parallel developers, or a premium quota large enough to keep branches continuously rebased. Neither holds today.

## Option C: brute-force breadth, naive universal lowering

Lower everything now with boxed values, dynamic dispatch and generic runtime helpers, accepting about 1.2x CPython, then optimize.

Cost. An initial burst could clear the 163 inference and 58 call-table R0 fences and, if the boxed model really handles every construct, reach 39 of 39. But it needs a second value model (boxed dynamic values) next to the typed one, method dispatch by name, and generic operators, which is the machinery the island already provides through CPython. My estimate is 6 to 10 weeks of mostly premium work, not the one month the option implies. The sizes involved are measured: `src/pyscriptc/lower/` is 14,674 lines, `src/pyscriptc/emit/` is 4,993 lines, and the C runtime is 26,516 lines in 42 files under `src/pyscriptc/runtime/`.

Perf. The honest range is wide, 0.5x to 1.3x CPython on dynamic code (estimate; there is no boxed prototype to measure). CPython 3.14 has inline caches and a mature allocator, so a naive boxed C runtime that allocates per operation can lose. The typed fast path survives only where both models meet, with a type check at every boundary.

Maintenance churn. Near total for the emitter and the lowerer paths that choose representation, because the optimization pass later rewrites the code that produced the boxed values. This is the two-migration outcome the positioning decision rejected.

Risk to frozen gates. High, not automatic. `IR_HASHES.json` hashes the canonical lowerer IR, so it moves only for probes whose lowered IR changes; a boxed model that leaves existing typed probes alone would leave them alone. But the boxed lane needs new IR node kinds, which means a change to `canonical.py` rules or `outcomes.py` consumers, both frozen and requiring a recorded decision, and `CALIBRATION.json` and `coverage_map.py` assume one lowering shape. Whether existing hashes move is knowable only by prototyping.

Kill criterion. Prototype the boxed lane for 3 R0 scripts. Stop if geomean on the `benchmarks.runtime.run` kernel workloads falls below 0.8x CPython on any boxed-path workload, or if any existing entry in `IR_HASHES.json` changes, or if the prototype takes more than 5 premium sessions.

First step. None recommended. If the owner wants it tested, run the prototype above and nothing else.

What must be true to win: a requirement to compile untyped Python with no libpython anywhere. The positioning decision removes that requirement, and the island answers it today at CPython speed.

## Option D: transpile through another language

The decisive fact first: fences are produced by the lowerer, not the emitter. The emitter only sees code the lowerer already accepted. For a typed target, changing the emit target cannot lower a construct the lowerer refuses, unless the target supplies dynamic values, which is Option C wearing a different name. So the fence effect of D is zero by construction, and D can win only on the cost of emission or on runtime reuse.

A second fact corrects an earlier draft of this plan: `probes/IR_HASHES.json` is a hash of the canonical lowerer IR, not of emitted LLVM text. A new emit target leaves those hashes alone. What a new target does put at risk is everything downstream of emission: the compat2 scorer assumptions that a compiled binary exists and its stdout is compared, `--debug-info`, `--target` cross-compilation, the runtime cache, and the island embedding.

Toolchain constraints the pipeline imposes today (measured from the repo): the driver links `zig cc -O2 prog.ll <runtime>/*.c [-target T] -lm -o out` (`src/pyscriptc/emit/llvm/_driver.py`, `packaging/TOOLCHAIN.md`), so the program is one native binary with the C runtime compiled in; `zig` is found on PATH or through the `ziglang` PyPI wheel shim (`src/pyscriptc/zigshim.py`); `--target` requires the zig driver because it bundles the per-target sysroot; macOS and Linux are the supported hosts, Windows is experimental cross-compilation with no CI that runs emitted binaries; DWARF comes from `src/pyscriptc/dwarf.py` (242 lines) which attaches `!dbg` locations from Python IR spans, so `lldb` and `gdb` show the Python file and line. The positioning decision also rules out a tracing GC entering the product, because values cross the island seam once per direction.

Any candidate therefore has to meet four requirements: (1) consumable by `zig cc` as C or as a linkable object for the target triple, so `--target` keeps working; (2) the result is still one statically linkable binary with no second runtime installed on the user's machine; (3) Python line numbers survive into DWARF, which for a C-emitting language means `#line` style directives and for an LLVM-IR-emitting one means our own metadata; (4) the extra compiler is installable in CI and on a small VPS without a multi-GB toolchain.

| Candidate | Joins the pipeline as | Single binary and runtime | zig cross-compile fit | Debug info | GC | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Nim | emits C (upstream knowledge), then `zig cc` can compile it | static, small ORC runtime, no separate install | good in principle; Nim has a cross-compile mode that accepts a custom C compiler, to be verified | `--lineDir` style `#line` output maps to Nim source, so Python lines need our own mapping | ARC/ORC, deterministic | only candidate worth a spike, still no fence gain |
| Zig (as source language) | already the driver; Zig source instead of C and LLVM text | static | native | native DWARF, our spans would need to be emitted as Zig source locations | none | reject: would replace 26,516 lines of working C with unwritten Zig; the strings-as-bytes and error-set model is far from Python exceptions |
| C text (the existing runtime language) | `zig cc` directly, no new dependency | same single binary | identical to today | `#line` directives give Python lines cheaply | none | the only version cheap enough to test; see the experiment |
| Rust | `rustc` or `cargo`; IR emission uses a pinned LLVM that may not match zig's | static std, about 1 GB toolchain (upstream knowledge) | `cargo-zigbuild` exists, extra moving part | DWARF through rustc, Python lines need `#line`-like mapping | none, `Rc` for graphs | reject: build time and borrow-checker friction for cheap models |
| Swift | `swiftc` | runtime libraries are not guaranteed static on Linux (upstream knowledge) | not through `zig cc` | via swiftc | ARC | reject: breaks single binary and the 8 GB budget |
| Crystal | `crystal` compiler | links libgc (Boehm) conservative GC (upstream knowledge) | cross-compile possible by object emit, libgc must be cross-built | partial | Boehm tracing GC | reject: tracing GC contradicts the seam design, memory-hungry whole-program inference |
| Mojo | proprietary Modular toolchain | not driveable by `zig cc` | no | no | n/a | disqualified: closed toolchain, no reproducible headless story |

Upstream-knowledge cells are flagged because this repo cannot confirm them; they must be rechecked before any spike is run.

Cost. 3 to 6 months to reach the 42 numbered divergences in `docs/reference/divergences.md` through another language runtime (estimate), mostly premium, with no fence reduction to show. The cheap-C variant is a throwaway emitter of at most a few hundred lines for a spike, order of 5,000 lines if it ever replaced `emit/`.

Perf. No gain over the current path: the current path already emits optimized LLVM through `zig cc -O2`. Any claim of a better factor from another language is not supported by anything in the repo.

Maintenance churn. High for every non-C candidate (new runtime and emitter, the part that works today). For C text, the lowerer and runtime survive and only the emitter is duplicated.

Risk to frozen gates. Lower than earlier drafts claimed for IR_HASHES (unchanged), but real for the scorer: compat2 runs binaries and `CALIBRATION.json` cells were produced with the LLVM emitter, so any target switch needs a recorded decision and a calibration rerun.

Kill criterion. See the experiment: the C path is closed if it needs a boxed value type, if its binary is more than 1.5x the LLVM binary, if compile time is more than 2x, or if it does not cut cheap-model attempts by at least 40%.

First step. The one-day C-text experiment below, only if the owner answers yes to question 4; otherwise nothing.

What must be true to win: that cheap models cannot produce correct LLVM SSA but can produce correct C for the same lowering. That is testable.

## Option E: hybrid, the one I would choose

E keeps the positioning decision and sequences the work in passes, by measured return rather than census count.

1. Inference breadth first. One workstream (param, return, local inference, then the name-resolution cascade it causes) covers 163 of 316 R0 fences and 30 of 39 scripts. Name-resolution fences should disappear without their own lowering, which is a hypothesis, not a measurement. It is premium work and sets the pace.
2. Call-table breadth second, built as C runtime helpers with a single IR call site rather than elaborate AST lowerers. `unlowered-call`, `call-arity` and `keyword-args` cover 58 fences and 28 scripts. Cheap models should do this well because the pattern is a C function with a fixed signature and one emitted call.
3. Island floor held at bug-fix scope while the boxed flag is raised. This follows the rule in `AGENTS.md` and the framing guard in the positioning plan: island-first must not become island-instead-of-lowering. The floor already delivers 6 of 8 scripts; its job is to keep weekly use alive while 1 and 2 land.
4. Classes and stdlib-surface third, to cross 18 of 39. Only then the long tail (checker, imports, statements, operators), ordered by scripts released rather than fences.
5. Optimize only where measured. The native lane is already several times faster on typed kernels, so no speculative optimization.

Cost. Roughly 70% of the work (call-table helpers, stdlib functions, tests, corpus runs) fits cheap sessions and 30% premium for inference and any IR shape change (estimate, to be tested by the one-week trial in the uncertainty section). Expected result: 40 to 90 R0 fences and 3 to 6 scripts a month, with the first two workstreams reaching 13 of 39 and four reaching 18 of 39.

Perf. Unchanged ceiling; floor stays at CPython speed.

Maintenance churn. Very low. All workstreams are additive; nothing is designed to be deleted, and the island keeps its `boxed:begin` and `boxed:end` regions so it stays deletable.

Risk to frozen gates. Low to moderate. C helpers and call table entries do not move existing probes; inference can, so it lands behind the A/B hash discipline, and the PR must say the IR changed so the hashes are regenerated on Linux, not macOS.

Kill criterion. After inference and call-table both land, R0 must show at least 9 of 39 compiling (the simulation predicts 13; 9 leaves room for cascades the simulation cannot see). If it does not, the cascade hypothesis is wrong and the sequence is re-planned from a fresh blocker ranking.

First step. Pick the single most common inference family, `param-inference` (23 fences, S effort in the tool), land it with R0 re-measured before and after, and record scripts released per session. That produces the first real cheap versus premium split and tests the cascade hypothesis within a week.

What must be true to win: the owner accepts scripts-released and native-definition-share as the scoreboard, and accepts that the island is the floor rather than a scaffold.

## The numbers side by side

These projections are estimates. The method: R0 family counts from today's measurement, effort labels from the blocker model in `benchmarks/real_scripts/measure.py`, the historical 10 to 40 fences per targeted PR from the census comments, and the machine and model budget stated at the top. They are not a promise.

| Dimension | A: release-grouped (current) | B: advance ahead of release | C: brute-force breadth | D: transpile | E: hybrid |
| --- | --- | --- | --- | --- | --- |
| R0 fences per month | 40 to 90 | 55 to 130, decaying | up to 220 once, then stalls | 0 | 40 to 90, front-loaded on inference |
| R0 scripts compiling per month | 3 to 6 | 4 to 8, decaying | up to 39 in 2 to 3 months if the boxed model works | 0 | 3 to 6, then 13 of 39 from two workstreams |
| Perf outcome | native 5x to 10x on typed kernels, island 1x | same | 0.5x to 1.3x on boxed paths (estimate) | no gain over today | native unchanged, island 1x |
| Maintenance churn | near zero | branch work lost, code kept | near-total emitter and representation rewrite | new emitter and runtime | near zero, additive |
| Risk to gates-v2 | low; inference may move hashes | moderate, per-config reruns | high, needs frozen-file decisions | scorer and calibration rerun, hashes unchanged | low to moderate |
| Cheap model share (estimate) | 60% | 30% | 50% up front | 20% | 70% |
| Premium effort (estimate) | 1 to 2 sessions per family | 2 to 4 per family plus rebases | 6 to 10 weeks | 3 to 6 months | 1 to 2 per family, inference-heavy |
| Time to weekly use | already available via the island | same | 1 to 3 months | 6 to 12 months | already available via the island |
| Fits 8 GB laptop and VPS | yes | yes | yes | C and Nim yes; Rust, Swift, Crystal are risks | yes |
| Measurable kill criterion | under 8 scripts at v0.7.0 close | branch older than 7 days | boxed kernel below 0.8x CPython | C path misses a threshold in the experiment | under 9 scripts after inference plus call-table |
| Winning condition | long horizon, corpus scoreboard | parallel developers or large quota | no-libpython mandate | cheap models cannot write LLVM | island accepted as the floor |

## The experiment worth running (under one day)

The most promising adversarial option is not "transpile to Rust"; it is the narrow C-text emission question inside Option D: can a cheap model land call-table lowerings faster when the new call is emitted as C text than when it is emitted as LLVM SSA? If yes, the C path is a productivity tool for one workstream. If no, the whole D family is closed.

Design. Use four vendored R0 scripts as sources of representative call sites: `batch_file_rename` (1 unlowered-call), `json_to_csv` (1 unlowered-call, 1 call-arity, 1 stdlib:json), `organise` (2 unlowered-call, 1 call-arity) and `cat` (2 unlowered-call, 1 call-arity). That is 6 unlowered-call fences and 3 call-arity fences measured from the R0 JSON. Implement those calls as C runtime helpers, each a fixed signature plus one IR call site, which is the normal Option E pattern. Then generate each call site's enclosing function twice from the same lowered form: once through `src/pyscriptc/emit/llvm/` as today, once through a throwaway C text writer of at most 300 lines that calls the same runtime and emits `#line` directives. Compile a small driver per site against both artifacts, run both plus CPython 3.14 on the same input, and compare stdout byte for byte. Use a worktree and `timeout 60` on every compiled run.

Measure, per script and per target: number of cheap-model attempts needed to reach a green build and matching output, wall time per attempt on the 8 GB laptop, peak compiler memory, compile time of the emitted artifact, binary size, whether `lldb` shows the Python file and line (the DWARF constraint above), and the count of new runtime lines the target forced. Also record whether the C target needed a boxed value type anywhere, which is the tell that it has become Option C.

Kill criteria, all decided before the run: stop if the C target needs a boxed or dynamic value type for any of the four scripts, if any emitted binary exceeds 1.5x the LLVM binary size, if compile time exceeds 2x, if Python line numbers cannot be recovered in a debugger, or if the C path does not cut cheap-model attempts by at least 40% across the four. A win changes only the per-PR cost of the call-table workstream, whose unlowered-call part is 29 R0 fences across 16 scripts and releases no script on its own, so it cannot change the sequence in this document, only the speed of step 2.

## Recommendation

Adopt Option E and say so in the milestone. Concretely: v0.7.0 keeps its island-floor gate and adds an inference workstream plus a call-table workstream aimed at the R0 blockers rather than the self-census; v0.8.0 keeps compute two-plane and absorbs the classes and stdlib-surface work; the long tail is ordered by scripts released and never by raw fence count.

Do not adopt B as a standing mode. The branch rot is measurable: 1,637 and 2,618 compiler lines changed in the last two 10-commit windows, and the island work edits the same lowerer files a lowering branch would touch. A B-shaped burst is acceptable only as a branch of a week or less, behind one flag, in a release already planned.

Do not adopt C. It buys up to 39 of 39 scripts at CPython-ish speed, which is the island's job, and pays for it with a second value model, frozen-file decisions and two migrations. The positioning decision already refused it.

Do not adopt D as the sequencing strategy. For typed targets it lowers no fence, because the lowerer, not the emitter, decides what compiles. Keep exactly one piece, the C-text experiment, because it tests a real claim about cheap models cheaply.

The measurable commitments this implies: report `scripts compiling` and `native definition share`, not fences removed; regenerate `benchmarks/real_scripts/REPORT.md` and `docs/reference/what-will-fail.md` as the v0.7.0 census delta receipt; and resolve the raised boxed drop flag before the milestone 4 gate, since its exit criterion requires it not raised.

## Questions the owner must answer

1. The boxed drop flag reads TRUE at the head of this branch (churn 33.3% then 24.8%, above 20% in two consecutive windows) and false two docs commits earlier. Do you treat it as raised, which per `AGENTS.md` stops island feature work and asks you whether to drop the tier, or do you rule that the island floor stages are the expected churn of a planned build and re-baseline the rule?
2. Is the success metric scripts compiling (2 of 39 today) and native definition share (22.2%), or fences removed from the self-census (3,057, roughly flat)? The two imply different work.
3. For v0.7.0, is CPython-speed island output acceptable as weekly use for the 6 of 8 scripts that work, or must a named subset run native in that release?
4. How many premium sessions per week are actually available? That decides whether inference (premium, 163 fences) leads or call-table (cheap, 58 fences) leads.
5. Do we authorize the one-day C-text experiment, or is `emit/` LLVM-only by decision and closed to further targets?
6. Do we regenerate the R0 report and the what-will-fail page now as the v0.7.0 census delta receipt, or defer both to the milestone close?
7. Which three to five scripts define the owner's weekly use? Pinning them makes the scoreboard concrete and stops the census from drifting toward compiler plumbing.

## Uncertainty

The inference estimates are the softest. The blocker model labels param, return and local inference S, but the R0 families are symptoms of one another, and the 98 name-resolution fences may not all vanish when annotation inference improves; nobody has landed that work at this scale, and the 341-fence local inference drop (#500) was on a different corpus.

The cheap-model share per workstream is an assumption, not a measurement. It is testable in a week: have a cheap model write three C runtime helpers and three inference rules, and count revisions. Session counts are not tracked anywhere in the repo, so every cost figure in sessions is an estimate.

The perf figures for Options C and D are judgments from the shape of the work, not measurements on this codebase. The native-lane speedup figures come from three-run laptop measurements.

The boxed ledger is volatile and alignment sensitive, as shown above. One reading is not a trend, and a rule that flips on two docs commits needs either a code-commit-only window or a minimum code-commit count per window. That change would touch `benchmarks/compat2/boxed_cost.py`, which is not in the frozen gates list, but it is a policy change and belongs to the owner.

The committed R0 report is stale relative to the tool, so every R0 number quoted here was re-measured with the tool on 2026-10-25. The 2-of-39 versus 0-of-39 script count is the part to trust, because it comes from the same tool version.

External-language facts marked upstream knowledge (Nim emitting C, Crystal's Boehm GC, Swift runtime linking, Rust toolchain size, `cargo-zigbuild`) were not checked against current documentation in this audit and must be rechecked before any spike.
