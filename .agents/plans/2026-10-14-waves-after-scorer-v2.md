# pyscriptc: next waves toward the base goal

On approval, this file is copied to `pyscriptc/.agents/plans/2026-10-14-waves-after-scorer-v2.md`, and `pyscriptc/AGENTS.md` "Active plan" points to it. The original plan (`2026-10-14-scorer-v2-and-strategy.md`) stays as history. Its phases 7 to 11 are superseded by this file.

## Context

The base goal is a Python to native compiler that supports the builtins and the stdlib as the Python Language Reference specifies them, with CPython as the reference implementation. There are two subgoals: support the most used CPython versions, and compile a share of the top 100 libraries. The scorer rebuild (compat2) is about two thirds done. The compiler has gained nothing yet. A check against the repo confirms how narrow the subset is (`docs/supported.md`):

- No module level statements, so 0 of 20 scripts compile.
- No `print`, `break`, `continue`, `finally` or `except as`.
- Only six exception names exist in the runtime.
- Strings are Latin-1 only.
- No inheritance, no `Optional`, no lambdas.

These gaps sit in the core value model and the statement set. Every probe area and every package hits them before any stdlib module work can count. That is why the census found 8 to 30 root families per package: they are mostly these shared core families, repeated.

### User decisions (2026-10-14)

1. **Primary oracle moves to CPython 3.12.** The version order is 3.12, then 3.14 (latest), then 3.11, 3.13 and 3.10. pyscriptc compiles typed source to a typed IR, so version compatibility is mostly a syntax and stdlib surface question. Native libraries are reached through a static FFI, the way `vercel-labs/scriptc` does it (`scriptc build --ffi <manifest>`: signature only declarations bound to C ABI symbols and linked statically, with no runtime lookup).
2. **Core first, top100 secondary.** Language, builtins and stdlib callables carry the primary weight, scripts sit in the middle, and top100 packages are a lagging secondary that also sets the tie-break order.
3. **Phases 9 and 10 stay.** A step that would benefit from a core epic is scheduled after that epic.

## 1. Verdict on the open risks

**Blockers for a trustworthy keep/drop signal** (all fixed in Wave A, before any compiler epic):

| Risk | Why it blocks | Fix |
| --- | --- | --- |
| Oracle is 3.14, decision says 3.12 | Every value and every denominator would change after the freeze | A1 retarget, before the freeze |
| 189 v1 lowered rows fence under v2, none triaged | Some v2 probes may be wrong. A wrong probe turns a correct compiler into a fail | A2 reconciliation |
| 1,591 `raises TypeError` probes where every candidate argument failed under CPython | They measure a failure of argument synthesis, not behaviour. A typed compiler rejects those calls statically, so they can never pass | A3 drop |
| 1,212 mention rows plus about 4.5k weak folds in the denominator | They inflate the denominator with rows that are unreachable or carry no evidence | A3 reweight |
| Verdict never ran end to end on a real compiler diff | The gates are untested on real input | A5 replay |
| Calibration covers 2,688 of 14,285 probes | Categories outside functions and constants are miscalibrated (classes 1/15, methods 2/10) | A2 full run, sharded |

**Acceptable debt** (recorded, not fixed now):

- The perf baseline is aarch64 in the container. A5 records a container baseline and a native macOS baseline. The x86_64 numbers stay historical and are not compared.
- `test_baseline_run_records_time_and_rss` is flaky. Mark it `xfail(strict=False)` when RSS reads 0, and fix it inside A4 if that's cheap.
- No GitHub Project, by user decision. Phases stay tracked in `.agents/plans/`.
- The holdout's limits are documented. Encryption at rest happens only if the user asks.
- Scripts at 0/20 and 47 thin corpus functions are signal, not debt. The `spec.py` closed rule widens epic by epic, as each epic lowers new types.
- The census coarse buckets (`construct other`, `unlowered call`) get split as each epic starts, not all up front.

## 2. Waves

Effort: S under 1 day, M 1 to 3 days, L about a week, XL more than a week. All runs go through `.agents/docker/run.sh`, and heavy runs use `HEAVY=1`, one at a time. Worktrees come from `.agents/bin/agent-checkout <name>`.

### Wave A. Make the scorer trustworthy, then freeze it (`gates-v2`)

| Id | Work | Effort | Exit criteria | Files |
| --- | --- | --- | --- | --- |
| A0 | Fix the macOS crash (`errno.EADV` in `stdmore.py`: guard platform only errno names with `getattr`) | S | `pyscriptc compile examples/hello.py` runs on macOS arm64. The full suite still passes in the container | `src/pyscriptc/stdmore.py` |
| A1 | Retarget the oracle to 3.12 and add the version axis | L | The generator takes `--oracle 3.12`. The typeshed surface is filtered by `stdlib/VERSIONS` for the target. The language profile uses 3.12 `ast` nodes, and 3.13/3.14 only nodes are tagged `min_version`. `cpython_tests/` is re-pinned from the latest v3.12 tag. Every row carries `versions` (the subset of 3.10 to 3.14 where the name exists and the oracle value agrees). Regeneration is byte for byte reproducible, and the 3.14 set is kept as a secondary column | `benchmarks/compat2/probes/{generate,typeshed,surface,language,cpython_adapter,fold}.py`, `.agents/docker/` (add uv interpreters 3.10 to 3.14) |
| A2 | Calibration reconciliation | M | Run calibration on the full set in shards that merge automatically. Each of the 189 v1 lowered fence rows lands in a triage table, `probes/TRIAGE.md`, as one of: probe wrong (fixed in the generator), stricter than v1 (kept and explained), or compiler gap (kept). Zero unexplained rows. Per category calibration has at least 10 sampled rows in every cell | `probes/calibrate.py`, generator fixes |
| A3 | Weak probe classes (decision in section 4) | S | The manifest shows the new weights. Headroom reports 0 capped rows among weighted rows | `probes/fold.py`, `ledger.py`, `headroom.py`, `score.py` weights |
| A4 | Tier 0 and caching (section 4) | M | Measured in the container and recorded in `benchmarks/compat2/TIMINGS.md`: Tier 0 under 30 s, Tier 1 under 3 min with a warm cache | `score.py`, `engine.py`, new `coverage_map.py`, `cache.py` |
| A5 | Verdict end to end, plus perf baselines | S | Replay three historical iterations (one kept, one dropped, the `optional-tagged-class` attempt) through the verdict, and the outcomes match the original plan's Verification section. The injected bug drop still works. `benchmarks/baseline` has a container baseline and a macOS baseline, with the perf gate exercised once | `verdict.py`, `benchmarks/baseline/` |
| A6 | Rewrite `loop.md`: epics, budgets, the primary metric, verdict driven keep/drop. Retire the v1 floors | S | An agent can run one iteration from `loop.md` alone | `.agents/docs/loop.md` |
| A7 | Freeze: tag `gates-v2`, and list the frozen v2 files in AGENTS.md next to the frozen v1 files | S | Tag exists. AGENTS.md is updated | `AGENTS.md` |

Order: A0 and A4 run in parallel with A1, since their files don't overlap. A1, then A2, then A3 run in sequence because they share the generator. A5 needs A3 and A4. A6 and A7 come last.

Stop conditions: if A2 can't reach zero unexplained rows within 3 days of work, stop and ask the user whether to exclude the remaining rows. Wave A adds no new scorer features beyond this table.

### Wave B. Phase 7: core runtime epics (the compiler finally moves)

Starts only after `gates-v2`. E0 is strictly serial. After E0, epics that touch different files of the new `lower/` package can run in parallel, and E-epics that share a file run in sequence.

| Epic | Scope | Effort | Unlocks (to be confirmed by census at epic start) |
| --- | --- | --- | --- |
| E0 Structural refactor | Split `lower.py` (250 KB) into a `lower/` package: `expr`, `stmt`, `classes`, `calls`, `intrinsics` (one table), `stdlib/` dispatch per module family. Then add a desugar pass. Start with pure move commits so history stays bisectable | L to XL | Parallel work in every later wave, and a cheaper cost per feature |
| E1 Statements and scripts | Module level statements lowered into an entry function, `print`, `break`, `continue`, `assert`, `del`, `finally`, slice step, mixed `and`/`or` | L | 20 scripts, language rows, more adapted CPython tests (they need loops and asserts) |
| E2 Exception model | All 70 builtin exception classes as a real hierarchy in the runtime, user exception classes, `except ... as`, `raise ... from`, `args` and `str(e)`, uncaught tracebacks that print the right name | L | 70 builtin and 172 stdlib exception rows, valid `raises` probes, PS1205 |
| E3 Object model | E3a `None` and `Optional`. E3b single inheritance, `super()`, vtables. E3c native tagged unions. E3d structural no op bases (ABC, Generic, Protocol) | XL (4 sub epics, each with its own budget) | PS1101 to PS1104, PS1203, most top100 class families, receiver construction for method probes |
| E4 Unicode `str` | Full code point strings on top of `ps_unicode.c`, `encode`/`decode` with an encoding argument | L | The str method surface, the non-Latin-1 language row, `json` with non-ASCII text |
| E5 Callables | Lambdas, closures, known decorators (`staticmethod`, `classmethod`, `property`, `overload`, `functools.wraps`), kw-only and positional-only parameters, `*args` and `**kwargs` | L | PS1201, PS1202, corpus functions |
| E6 Containers and iteration | Real `tuple`, generic `set`/`frozenset`, dict views, `iter`/`next`, generators, full comprehensions | XL | `map`, `filter`, `reversed`, `itertools`, and a large share of the builtin method rows |
| F Static FFI | scriptc style `--ffi <manifest>`: `.pyi` signature declarations bound to C ABI symbols, linked statically. Used first inside the compiler to back stdlib modules with C libraries (libm, zlib, expat, mpdecimal, sqlite3, later mbedTLS) without touching the lowering core | L | A stdlib module costs a stub plus a C shim instead of `lower.py` special cases |

Order: E0, then E1. After that, E2 and F run in parallel (exceptions live in `stmt`/`classes` plus the runtime, FFI lives in `calls`/`stdlib` plus the link driver). Then E3a to E3d, then E4 and E5 in parallel, then E6. The order inside each step gets rechecked with the census set cover at every epic boundary, ranked by the primary metric (section 3).

**Wave C. Stdlib families** (parallel tracks after E0 and F, one module family per track, each in its own `lower/stdlib/<family>.py` plus `runtime/ps_<family>.c`). Start order comes from the core ledger backlog, ranked by weight times the version axis `market_coverage`. The likely first batch: `itertools` (rank 11 in the census, unblocks `jmespath`), `functools`, `collections`, `operator`, `string`/`textwrap`, full `math`, `copy`, `enum`, `dataclasses`, `re` completeness, `json` floats and null, `argparse` (scripts need it), `os`/`os.path`, `csv`, `fractions`, then FFI backed `decimal` (mpdecimal), `zlib` and `datetime`. Each family is an epic with budget 3 and exits when its ledger rows pass, or when it stops paying off.

**Version track V** (parallel with Wave B, after A1). `--target-version {3.10..3.14}`, default 3.12. It gates syntax per version (`ast.parse(feature_version=...)` plus an explicit node table, for example `type` statements on 3.12+, `except*` on 3.11+, and t-strings on 3.14), and gates the stdlib table by typeshed `VERSIONS`. The scorer reports one column per version, built from the A1 `versions` field, weighted by the market snapshot in `benchmarks/compat2/market/2026-10-14.json`. Effort M. The implementation axis from the old phase 11 (PyPy, MicroPython, compiler peers) stays report only, runs at epic boundaries, and produces `docs/breadth-review/<date>.md`.

### Phases 9 and 10 (kept, scheduled after the epics they depend on)

| Step | Depends on | When | Effort |
| --- | --- | --- | --- |
| 9 S0: Nuitka onefile build per OS and arch, toolchain contract (system clang), release smoke test | A0 (macOS) | Now, parallel with Wave A (new `packaging/` dir, no compiler files) | M |
| 9 S1: compiler leaf modules (`ir.py`, `errors.py`) join the corpus as a `self` area | E2 and E3 (classes with inheritance, unions, `except as`) | After E3b | S to start, then the census drives it |
| 9 S2: frontend and backend split. The pyscriptc built backend must emit byte identical IR | E5 and E6 | After E6 | XL |
| 9 S3: full self host | n/a | Unscheduled research | n/a |
| 10 H1: HTTP/1.1 `urlopen` in the C runtime | E2 (`URLError`, `HTTPError`, `TimeoutError`, `OSError`), E4 (headers as `str`) | After E4 | L |
| 10 H2: HTTPS through mbedTLS | H1 and F (mbedTLS bound through the FFI manifest, not hand lowered) | After H1 | XL |
| 10 H3: `http.client` closed subset, `requests` shim only if a pinned package needs it | H2 | Later | M |

## 3. Phase 7 metric and how an epic is judged when packages don't flip

**Primary metric (climbed).** Weighted pass under the 3.12 oracle, made of these weighted components:

- core probes: language 1.0, builtins and stdlib functions, classes, methods and exceptions 1.0, weak folds 0.25, constants 0.1
- scripts: 3.0 per program
- top100: 2.0 per corpus function and 5.0 per whole package. Low enough that core work dominates, high enough to break ties.

These weights become `score.py` weights in A3 and are frozen with `gates-v2`.

**Secondary metric (leading indicator).** Definitions unblocked: across the corpus and the probe sources, the number of definitions whose `coverage` fence set becomes empty, plus fences removed weighted by how many definitions they block. Unlike "packages flipped", this moves on every real step.

**Epic rules.**

1. At epic start, the epic file (`.agents/plans/<date>-epic-<name>.md`) declares its family, its budget N (E0 6, others 4 to 8, each E3 sub epic 4), and a **predicted flip list**: the probe and corpus ids that census and coverage say should pass once the family is lowered.
2. Mid epic iterations are kept when all gates hold (no new soundness failures, no pass flips, perf, compile budget) and the epic's family fence count strictly drops.
3. At the epic boundary the primary metric must have risen, and at least 50% of the predicted flip list must pass. Otherwise the epic is reverted as a whole and recorded in a v2 failed log (`benchmarks/compat2/failed.jsonl`, with `epic` and `outcome` fields).
4. The holdout is scored at every epic boundary by the evaluator. A drop while the visible score rises stops the loop and goes to the user.
5. E0 is judged differently: zero pass flips across Tier 1, no rise in compile time, and a measurable cost drop (lines in the largest lowering file down by at least 60%, and the median diff size of the next 3 features recorded as the baseline).

**First epic: E0**, then E1. Why E1 comes before unions: it is the cheapest family that turns whole programs green (20 scripts at weight 3.0). It also unblocks most adapted CPython test cases, and every later probe class needs `print`, loops and asserts.

## 4. Weak probe classes, Tier 0 and caching

| Class | Count | Decision |
| --- | --- | --- |
| `raises` where every candidate argument failed under CPython (TypeError) | about 1,591 | **Drop** from the denominator. They become `mention` with reason `no valid argument list` and land in the backlog for hand written argument factories |
| Other `raises` (a valid call raising a specific exception) | about 374 | **Keep at weight 0** until E2 lands, then weight 1.0. Raising them earlier would only reward the RuntimeError catch all |
| `mention` (1,212: receiver can't be built, no oracle checked attribute) | 1,212 | **Weight 0**, excluded from headroom and listed as backlog. A receiver factory table (one factory per class, ordered by backlog weight) turns them into real probes over time, and each factory is an S task inside the epic that needs it |
| Weak folds (`handle`, `recv-handle`, `exec`, `none`, `shape`) | about 4.5k | **Keep at weight 0.25**. The generator promotes a weak fold to a value fold wherever there is a deterministic observable (type name, `len`, a scalar attribute) |
| Constants | 4,994 | Weight 0.1, unchanged |

**Minimal Tier 0 and caching (A4):**

1. Cache key = (probe source hash, oracle version, hash of the compiler files on the probe's coverage map path). Entries are written atomically, as they already are.
2. `coverage_map.py`: one instrumented Tier 1 run records which compiler modules and runtime C files each probe touches (module level `sys.setprofile` filter, compiled once and cached). Tier 0 then selects the probes whose map intersects the diff, capped at 400. When a shared file changes (the `ir.py`, `llvm.py`, `ps_rt.c` core), it uses a fixed seed stratified sample of 300 across every area and category instead. The headroom check runs first.
3. Bundles: compile up to 50 probes of the same module as one translation unit and bisect only when the bundle fences or fails (the v1 `_bundle` helper). This cuts clang and link overhead, which dominates per probe time.
4. Shards merge automatically into one result file, so there are no hand merges.

## Verification

- Wave A: `compat2 --headroom` reports 0 capped weighted rows. `probes/TRIAGE.md` has zero unexplained rows. Regenerating twice gives byte identical JSONL. The A5 replay matches the expected keep/drop. Tier timings are recorded in `TIMINGS.md` and meet the targets. The full suite, `ruff check`, `ruff format --check` and `benchmarks.baseline.run --check` stay green against the cached baseline log (rule 9c: a new baseline under `/tmp/pyscriptc-master-baseline-<ts>.log` before the first run).
- Each epic: the epic file holds the predicted flip list. The boundary report shows the primary and secondary metrics, the flip list hit rate, the holdout aggregate (from the evaluator) and the `boxed_cost.py` ledger.
- Version track: `--target-version 3.11` rejects `type X = int` (3.12+) with a PS code, and `--target-version 3.12` accepts it, and the per version columns appear in the score JSON.
- Phase 9 S0: unpack the release binary on macOS arm64 and linux aarch64, compile `examples/hello.py`, run it, and compare the output with CPython 3.12.

## Process guards

- No new phases or addenda until Wave B has produced one kept epic.
- `lower.py` (later the `lower/` core files) is touched by one track at a time. Parallel tracks must not share files.
- Heavy runs go one at a time through `HEAVY=1`. If a worktree pane shows no change for about 5 minutes, ask the user to check it for a trust prompt.
- Follow the `_own` capture behaviour at the end of each wave: a decisions row plus the content capture question.
