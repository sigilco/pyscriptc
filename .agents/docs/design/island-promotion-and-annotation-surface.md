# Island-to-native promotion and an incremental annotation surface (follow-up to #1283)

Status: design note, 2026-11-18, session rsB. Research note, not a decision. The decisions.md row proposed at the end is what turns it into one.

Inputs: `/tmp/compat_matrix.csv`, `/tmp/pyscriptc_native_compat_report.md`, `.agents/docs/design/jit-and-codon-annotations.md` (#1283), `.agents/docs/design/self-contained-install.md`, `docs/reference/divergences.md`, `.agents/decisions.md`.

The question this note answers: given the fence forensics in the compat report (nine trees, 17,997 fence events, PS2002 and the class-base wall dominating), is there an island-to-native promotion technique or an opt-in annotation surface that widens the native share incrementally, while the same source keeps running on plain CPython and PyPy with the decorators as no-ops? The #1283 note rejected a run-time JIT for the parity contract and recommended visible specialization. This note takes the next step: it surveys what peers actually ship for promotion and incremental adoption, maps each option onto the compat matrix rows, and gates every option on ROI.

## The constraint set, restated

Four facts bind everything below.

First, the parity contract (`docs/reference/divergences.md`, divergence list header: "anything not listed here is claimed identical") requires emitted semantics fixed at ship time. The 2026-10-25 typed-subset decision dropped guarded speculation permanently. So a tracing JIT that specializes on runtime types is out, full stop, unless it ships as a separate product with its own divergence list.

Second, the promotion axis that is still open is not within-process JIT but island-to-native: definitions currently routed to the embedded CPython island (divergence 41) that could instead be lowered, or could get a guarded fast path where the native tier lowers a common shape and the island handles the rest. The island is an existing, proven tier (policy-lane receipt, exit 0, about 42 ms startup); promotion happens at compile time, which keeps semantics fixed before the program ships.

Third, the source-compatibility invariant: a user's annotated source must run unmodified on plain CPython and PyPy. Any decorator pyscriptc ships must be importable and a no-op without pyscriptc, exactly as Cython's `cython` module is a shadow no-op (`Cython.Shadow`) and mypyc's modules are plain Python.

Fourth, the matrix says where the mass is. PS2002 plus class-base fences is about 2,071 events in SQLAlchemy alone and the top fence in six of nine trees; the annotation family (PS1001, PS1103, PS1104, PS1105, PS1106) is about 3,089 fences in SQLAlchemy and 1,058 in pydantic; PS1203 class-header forms are 254 in psycopg. Decorator fences (PS2006, PS3099) appear in the webapp receipt. Anything that does not target these rows does not move native_share_pct.

## Part 1: island-to-native promotion techniques, surveyed

### P0. What pyscriptc already has (the baseline)

Per-definition island routing (divergence 41): a function or class the subset cannot compile is exec'd into the embedded interpreter, values cross one way per call. The per-import policy lane (`[tool.pyscriptc.imports]`) is proven. The `--emit binding` seam lets an island program call compiled code. Assess prints the plane map. What does not exist: cross-runtime class interop (report item N1), default-degrade import routing (N2), decorator-aware demotion (N8), and any runtime code-loading path (the backend emits `.ll` and links with `zig cc`; there is no ORC, no dlopen-of-own-artifacts in the native tier today).

### P1. Codon `@codon.jit` (in-process JIT over the AOT compiler)

What it is: a `codon` pip package whose decorator compiles a Python function with the Codon compiler for the concrete argument types at first call, caching native function pointers per type shape, with value conversion at every crossing (https://docs.exaloop.io/integrations/python/codon-from-python/). Documented prices: mutation inside the JIT'd function is not visible to the host (NumPy arrays are the exception via shared data pointer), polymorphism and inheritance are unsupported in JIT mode, and the compile cost is paid inside the running process.

Effect on the matrix: none of the named rows. PS2002 is a compile-time closed-table refusal; a JIT decorator does not lower the classes and inheritance that fence (Codon's own JIT explicitly excludes inheritance).

Cost: an ORC-equivalent load path, the compiler callable from the binary or shipped inside it, a marshalling layer, and a second divergence list. The #1283 note priced this as the largest of its three candidates.

Invariant risk: direct violation. Semantics would not be fixed at ship time.

Verdict: reject (already rejected in #1283; the survey confirms Codon's own JIT excludes exactly the features the matrix fences on, so it would not even move the rows).

First step: none.

### P2. CPython 3.13+ copy-and-patch JIT (tiered interpretation, stencils)

What it is: PEP 659's specializing interpreter collects type profiles; hot bytecode is lowered to micro-ops; the JIT copies precompiled machine-code stencils generated at build time from the same `bytecodes.c` DSL and patches in constants and addresses (https://peps.python.org/pep-0744/, https://github.com/python/cpython/blob/main/InternalDocs/jit.md). About 900 lines of build-time Python and 500 lines of runtime C. The build-time-generation trick is the transferable idea: templates for lowering are generated from one source of truth, so the JIT cannot drift from the interpreter.

What pyscriptc can take: the *discipline*, not the machine. The lowering rules that decide native vs island should be one table consumed by both the compile path and `assess`/`coverage`, so a definition's plane is never computed twice from two rule sets. The code cache pattern already shipped this way (divergence 49: guarded cache keyed on `.pyc` magic and sha256, fallback to recompiling, so behavior never differs).

Effect on the matrix: indirect. Consistent plane computation prevents promotion regressions but does not itself close any fence row.

Cost: low (refactor only). Maintenance: low once landed.

Invariant risk: none.

Verdict: gate on evidence, folded into any N1/N2 work rather than a standalone project. First step: an audit that `assess` plane hints and lowering decisions read the same table (PR-sized).

### P3. PyPy meta-tracing (the anti-pattern to avoid)

What it is: a tracing JIT over the whole interpreter; hot loops traced via a meta-interpreter roughly 900x slower than normal execution during tracing, compilation amortized after thousands of iterations, guards with resume data for deopt (https://cfbolz.de/posts/speed-of-tracing/, https://morepypy.blogspot.com/2015/10/pypy-memory-and-warmup-improvements-2.html). It gets generality by giving up fixed-at-ship-time semantics entirely.

Effect on the matrix: none; PyPy's JIT fences nothing because it refuses nothing.

Cost: a tracing infrastructure the project has neither built nor budgeted, plus warmup characteristics that conflict with the CLI/agent single-shot workloads pyscriptc receipts target (0.55 ms spawn is the advertised win; a tracing warmup is the opposite trade).

Invariant risk: maximal.

Verdict: reject. First step: none. Record this as the reason the 2026-10-25 decision stays.

### P4. Static Hermes (mixed modes in one runtime)

What it is: the closest architectural peer. Hermes ships AOT-to-bytecode, AOT-to-native, an interpreter for dynamic features, and a baseline JIT, all simultaneously in one runtime (https://github.com/facebook/hermes/blob/static_h/doc/blog/2025-11-02-hermes-compilation-runtime-modes.md, https://github.com/meta/hermes/discussions/1685). A compiled Wasm/native binary embeds the interpreter for `eval`. Its own engineering note is candid that AOT of untyped JS is not faster than a high-tier JIT; the win is predictable performance, and types are what make native worth it. Their takeaway: compile parts of the app native with a faster codegen strategy, keep the rest interpreted.

What pyscriptc can take: the pattern is validated. Native for the typed closed subset, embedded interpreter for the rest, in one binary, with types as the promotion criterion. That is exactly the island tier plus the N1/N2 roadmap items; Hermes is evidence the shape is sound at scale, not a new technique to build.

Effect on the matrix: the shape Hermes validates is precisely what closing the class-base wall (N1) and default-degrade routing (N2) implement. Rows affected: PS2002 across all nine trees, class-base fences in SQLAlchemy (1,383), asyncpg (304), pydantic (218), Piccolo (90), FastAPI (76).

Cost: carried by the existing N1/N2 line items (N1 large, N2 small-medium per the report).

Invariant risk: none if promotion stays compile-time, which is also what Hermes does (its modes are fixed at build; the JIT is optional runtime work above a fixed baseline).

Verdict: do now, but as the already-planned N1/N2 work; this note adds no new mechanism, it confirms the peer precedent and the ordering. First step: land N2 (`--fallback-island-imports`) as the small PR-sized promotion; it converts every closed-table PS2002 refusal into an island routing, moving rows like pydantic's 1,256 PS2002 events from fence to island, and it reuses the proven policy lane.

### P5. Profile-guided / cached per-function native tier (compile-time PGO, not runtime)

What it is: run the program under plain CPython (or under the island binary) with a profiler or the coverage instrument, record which functions are hot and which shapes they see, feed the file list and shapes into the next compile so promotion decisions (which definitions to try lowering, which to leave island) come from evidence rather than the import classifier. Numba's `cache=True` is the runtime-side analog: a file-based cache in `__pycache__` keyed to source, so compiled artifacts survive across runs (https://numba.readthedocs.io/en/stable/reference/jit-compilation.html). mypyc's incremental compilation is the compile-side analog: IR generated SCC-at-a-time with cached serialized metadata validated by hash (https://github.com/python/mypy/pull/7887).

What pyscriptc can take: `coverage` already records fence events per tree (the matrix is its output). A `--promotion-hints <coverage.json>` flag that biases which definitions assess marks promotable, and a per-definition cache of lowering results keyed on source hash, are both compile-time-only and parity-safe by construction (the hint changes what is attempted, never what the emitted code means; a wrong hint costs an attempt, not a wrong answer).

Effect on the matrix: indirect but measurable; it makes N1/N2 promotions target the actual hot fences (for example SQLAlchemy's 1,327 class-base events at the exact definitions users hit) instead of import-tree order.

Cost: medium. Maintenance: the cache-invalidation discipline (magic + sha256, like divergence 49) must be kept honest; every change to lowering must move the key.

Invariant risk: low, provided hints only gate attempts and diagnostics remain deterministic.

Verdict: gate on evidence: do the coverage-to-hints file first (a trivial, PR-sized format change), adopt the lowering-result cache only if compile times on real trees become a reported pain. First step: extend `coverage` output with per-definition promotability hints consumable by `compile`.

### P6. Guarded fast paths (compile-time guard, island slow path)

What it is: lower the common shape of a construct natively and emit a compile-time-planned check that routes everything else to the island, rather than refusing the whole definition. The existing precedent inside pyscriptc is divergence 41's growth-shape check: the static analyzer finds unbounded-int growth and plans that function for the island instead of letting native i64 overflow abort. That is a guard, decided at compile time, parity-safe. Numba's object-mode fallback and PyPy's guards are the runtime versions and are precisely the "guarded speculation" the 2026-10-25 decision dropped; a compile-time guard with a planned island fallback is not that, because the dispatch is fixed before ship.

Effect on the matrix: targeted. A guard at the class-base wall would let, for example, a subclass of an island base be *defined* island (per-class demotion, N1's mechanism) while unaffected sibling functions stay native; today the fence cascades through the hierarchy (report section 5).

Cost: medium-large, and it is really N1 (per-class island demotion with a boxed-class ABI) plus N8 (decorator-aware demotion) under one framing. The report already ranks N1 as the strategic commitment.

Invariant risk: low if the guard conditions are static and listed in the divergence ledger; the risk is scope creep toward dynamic guards, which is the dropped architecture. Guard every new guard in decisions.md.

Verdict: do now as the strategic track, reusing the N1 plan; do not introduce any runtime re-dispatch. First step: N1's analysis half exists (assess demotion hints); the first PR-sized step is emitting per-class demotion plans in `assess` output so the ABI work has a stable contract to build against.

## Part 1 verdict summary

The only promotion techniques consistent with the parity contract are compile-time ones: default-degrade import routing (P4/N2), per-class demotion (P6/N1), coverage-driven hints and a cached lowering (P5), and plane-rule consistency (P2). Runtime techniques (P1, P3) are rejected and stay rejected. This matches #1283 and strengthens it with peer evidence: Hermes runs the mixed-mode shape pyscriptc already chose; Codon's JIT excludes inheritance, so it would not move the matrix's dominant rows even if the contract allowed it.

## Part 2: the annotation surface

### A0. Requirements

A decorator or directive is acceptable only if: (a) without pyscriptc installed it is importable and inert (plain `functools`-free module, no side effects, IDE-clean); (b) it never promises behavior the compiler does not deliver (the #1283 rule: lower to real behavior or fence with a named code, never silently ignore); (c) the surface stays minimal, one module; (d) it buys incremental benefit per annotation, not all-or-nothing.

### A1. Cython pure-Python mode (the closest prior art)

What it is: plain Python files sped up when Cython compiles them, via PEP 484 annotations, an augmenting `.pxd`, or the magic `cython` module whose decorators are no-ops under the interpreter (`Cython.Shadow` ships a copyable stand-in) (https://docs.cython.org/en/stable/src/tutorial/pure.html). Directives like `annotation_typing` scope per class and function (https://docs.cython.org/en/stable/src/userguide/migrating_to_cy30.html).

Effect on the matrix rows: none directly for pyscriptc, but the pattern is the template for A3 below. Note Cython's own annotation semantics are stricter than PEP 484 (exact types, not subclasses), which is the same divergence pyscriptc documents; the no-op shadow module is what keeps the source runnable.

Cost of copying the pattern: trivial (one shadow module).

Invariant risk: none if the module is genuinely inert.

Verdict: adopt the pattern. First step: ship `src/pyscriptc/_annotations.py` (shadow module) as the namespace for A3 and A4.

### A2. Numba and Codon decorators (the runtime-JIT spelling)

What they are: `@njit(cache=True)` with lazy per-signature compilation and dispatch (https://numba.readthedocs.io/en/stable/reference/jit-compilation.html, https://numba.readthedocs.io/en/latest/developer/dispatching.html); `@codon.jit` as surveyed above (https://docs.exaloop.io/integrations/python/codon-from-python/).

Effect on the matrix: none (Part 1, P1). Cost: rejected above. Invariant risk: rejected above.

Verdict: reject the decorator *semantics* (runtime specialization). Adopt only the *spelling* precedent: decorators as the opt-in locus, which is what users already know. First step: none beyond A1's namespace.

### A3. mypyc per-module opt-in (the workflow to copy)

What it is: any subset of modules compiled via `mypycify([...])`, the rest stays interpreted; the mypy project itself develops interpreted and runs compiled, "often we forget that we aren't working on a vanilla Python project" (https://mypyc.readthedocs.io/en/stable/getting%5Fstarted.html); incremental SCC-based compilation with hash-validated caches (https://github.com/python/mypy/pull/7887). All code remains valid Python; native classes give up `__dict__`, arbitrary metaclasses, most class decorators, and multiple inheritance except traits (https://mypyc.readthedocs.io/en/latest/using_type_annotations.html).

Effect on the matrix rows: the per-module opt-in is the user-facing surface for N2/N8; a module-level opt-in in pyproject (which the policy lane already is, per import) maps onto PS2002 mass per tree. The mypyc trait/erasure rules also suggest which of pyscriptc's annotation-family fences (PS1001, PS1103, PS1104) could relax under an opt-in flag rather than globally: opt-in modules can accept stricter inference expectations, global behavior stays conservative.

Cost: low; it is configuration surface over existing lanes.

Invariant risk: none (interpretation is the default, compilation is the opt-in, exactly pyscriptc's shape).

Verdict: do now as part of N2. First step: document and test `--fallback-island-imports` together with a per-module promote-list in pyproject (generalizing `[tool.pyscriptc.imports]` from island-lane-only to lane-selection), one PR.

### A4. React Compiler / Next.js incremental adoption (the rollout policy to copy)

What it is: three levers of decreasing blast radius: directory scoping via Babel `overrides`, per-function opt-in via a `"use memo"` directive (`compilationMode: 'annotation'`), and runtime gating flags; `"use no memo"` opts a problematic unit out regardless of mode (https://react.dev/learn/react-compiler/incremental-adoption, https://react.dev/reference/react-compiler/compilationMode, https://nextjs.org/docs/app/api-reference/config/next-config-js/reactCompiler). The ESLint rule tells users which units cannot be optimized and skipping them is safe.

Effect on the matrix: the *out*-directive is the piece pyscriptc lacks and can use immediately: a way to mark a definition "island on purpose" that silences the fence and records the routing. That converts fence noise into deliberate plane choice and shrinks the reported fence mass without touching lowering; rows affected: PS2002/PS2006/PS3099 in the trees where the fence is expected and accepted (the report's top_fence_message column is exactly the residual mass this targets).

Cost: small: one annotation, one diagnostics path (fence becomes note, like PS3099 already does for erased decorators), docs.

Invariant risk: low; the annotation must be advisory-only in the shadow module and a named, checked construct under the compiler.

Verdict: do now. First step: the shadow module's `@island` decorator plus the diagnostics change (PS2002 at an `@island`-marked definition becomes a note naming the deliberate routing), mirroring `"use no memo"` semantics. This is the smallest PR in this note that changes user-visible behavior, and it composes with every N item.

### A5. #1283 candidate C (`@specialize` and the specialization report)

Still recommended, unchanged: the annotation asserts monomorphization the compiler already performs and the report makes it legible. Under the A1 namespace it becomes `pyscriptc._annotations.specialize`, no-op without pyscriptc. This note adds one requirement: the report variant (or the coverage hints of P5) should be the measurement instrument that proves or disproves the promotion bets of Part 1. First step: as #1283 stated, one PR.

## Part 2 verdict summary

The surface is one shadow module with three decorators at most: `@island` (deliberate demotion, the "use no memo" analog), `@specialize` (#1283 candidate C), and nothing else until evidence demands it. Per-module lane selection stays in pyproject (mypyc/A3 shape), never in code, matching the existing policy lane and the package-tiering decision (one tier per package, never approximate). Runtime-JIT decorators (numba/codon spellings) are rejected with the JIT itself.

## Cost, maintenance and ROI gates, one table

| Option | Cost | Maintenance | Matrix rows touched | Invariant risk | Verdict |
| --- | --- | --- | --- | --- | --- |
| P1 Codon-style JIT | Very large | Second pipeline | none of the dominant ones | Violates parity contract | Reject |
| P2 CPython copy-and-patch discipline | Low (refactor) | Low | indirect | none | Fold into N1/N2 |
| P3 PyPy meta-tracing | Very large | Very high | none | Maximal | Reject |
| P4 Hermes-style mixed modes (= island + N2) | Small-medium | Low | PS2002 all nine trees | none (compile-time) | Do now (N2 first) |
| P5 Coverage hints + lowering cache | Medium | Cache keys per lowering change | indirect, targets N1 effort | Low | Gate on evidence |
| P6 Guarded fast paths (= N1/N8) | Large | High until boxed ABI lands | class-base wall, ~2,071 in SQLAlchemy alone | Low if guards static | Do now (strategic) |
| A1 Shadow-module pattern | Trivial | Trivial | enables A4/A5 | none | Do now |
| A2 numba/codon decorator semantics | rejected with P1 | - | none | rejected | Reject |
| A3 mypyc per-module opt-in surface | Low | Low | PS2002 per tree | none | Do now (with N2) |
| A4 `@island` opt-out + note-not-fence | Small | Low | PS2002/PS2006/PS3099 residuals | Low | Do now |
| A5 `@specialize` + report (#1283 C) | Low | Low | none directly | none | Do now |

## Ordering and gates

The do-now set is four small PRs in order: (1) the shadow module (A1, inert); (2) `@island` demotion notes (A4); (3) `--fallback-island-imports` plus the per-module lane table (P4/N2 + A3); (4) `@specialize` and the specialization report (A5). The strategic track stays N1 with the assess demotion-plan step from P6; P5's hints file should land before or with N1 so the boxed ABI work targets measured fences. The gates: any second decorator beyond the three requires a fence-mass receipt (a matrix row moving) or an assessed workload in the baseline suite; a lowering-result cache (P5) requires compile-time complaints on real trees; anything touching runtime dispatch is refused by the 2026-10-25 decision and needs a new decision row to even discuss.

## A proposed decisions.md row (not appended)

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-18 | Promotion stays compile-time: N2 default-degrade import routing and an inert `pyscriptc._annotations` shadow module (`@island`, `@specialize`) are the v0.11.0 surface; N1 per-class demotion is the strategic track; no runtime JIT or tracing tier | The parity contract and the 2026-10-25 typed-subset decision require semantics fixed at ship time, and the fence mass (PS2002 plus the class-base wall) moves only through compile-time routing and demotion, which the Hermes precedent validates as the mixed-mode shape | A Codon-style in-process JIT, PyPy-style meta-tracing, Numba-style runtime specialization decorators, per-feature package tiers | `.agents/docs/design/island-promotion-and-annotation-surface.md`, `.agents/docs/design/jit-and-codon-annotations.md` |

## Related

- `.agents/docs/design/jit-and-codon-annotations.md`: the #1283 note this extends.
- `.agents/docs/design/threads-plan.md`: the memory model a future `@par` still waits on.
- `docs/reference/divergences.md`: divergences 1, 41, 49 (island routing, the guarded code cache).
- `.agents/decisions.md`: 2026-10-24 package tiering, 2026-10-25 typed-subset positioning and island granularity.
- The compat report, chapter 6: the N1 to N8 ranking this note's Part 1 maps onto.
