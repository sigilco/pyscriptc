# JIT and Codon-style annotations in pyscriptc (issue #1283)

Status: design note, 2026-11-17. Not a decision. The decision row proposed at the end of this note is what turns it into one.

The issue asks whether typed annotations could drive a JIT or a Codon-like specialization path alongside the AOT compiler, and asks for a note comparing Codon, Numba and the annotation-as-lowering-input model pyscriptc already has. This note answers that, plus the question the issue implies: what would a `@codon.jit`-shaped or `@par`-shaped annotation give a pyscriptc user that the compiler does not already give them.

The short answer: pyscriptc already does the part of Codon that matters for a typed subset (compile-time specialization from annotations, monomorphization per call site, a native binary with no interpreter in the loop), and it deliberately refuses the part of Codon that trades CPython semantics away. A JIT would trade away the parity contract that the whole project is built on, and the `@par` annotation would need a memory model and a loop-lower that the project has not built yet. The cheapest real DX win is not a JIT at all: it is making the existing specialization visible to the user.

## What Codon gives a user, in two pieces

Codon is an ahead-of-time compiler for a Python-like language with its own type checker (a modified Hindley-Milner) and an LLVM backend. Two features of it are the ones this issue names, and they are independent.

The first is that annotations are optional. Codon infers types and monomorphizes per call site, so a program with no annotations at all still compiles to native code. The second is that Codon ships a JIT mode, reachable from Python as `@codon.jit`, which compiles a decorated function for the concrete types of the arguments the caller supplies and caches the native entry point per argument-type shape. The JIT is a thin layer over the AOT compiler: it reuses the same frontend, the same type checker and the same LLVM backend, with an ORC `LLJIT` in place of the file output. The mode exists mainly so that Codon can be called from a running CPython process, and the docs are explicit about the two prices: the arguments and the return value are converted between Python objects and Codon-native values on every call, and a mutation made inside the JIT'd function is not visible to the host Python (the NumPy array case is the documented exception, because the data pointer is shared rather than copied).

The third feature the issue names is `@par`, which is not a JIT feature but a parallelism annotation: it marks a `for` loop for OpenMP parallelization, with `num_threads`, `schedule`, `chunk_size`, `ordered` and `collapse` parameters, and Codon's OpenMP pass infers the shared, private and reduction variables automatically. Codon lowers `for i in range(a, b, c)` to an imperative loop first, so a `@par` loop is a C-style OpenMP loop; a loop over a generator becomes a task-based parallel loop instead. `@par` also has a `gpu=True` mode.

## What Numba gives a user

Numba is a JIT in the classic sense: `@numba.jit` (or `@njit`) decorates a function, LLVM machine code is produced at the first call, and the argument types are inferred then, at run time, from the values the caller passes. Its `nopython` mode supports a documented subset of Python; `object mode` catches the rest at run time with a Python-object fallback. Numba is aimed squarely at array and numerical code, with deep NumPy integration and CPU and GPU targets. Compilation happens inside a running CPython process; there is no standalone binary.

The comparison between the two is instructive for pyscriptc because they are the same idea with different price tags. Numba's compile-time cost is paid by the running program, on the first call, and the compiled code is per argument-type shape, so a program that calls one function with `int` and then with `float` compiles twice. Codon's JIT pays the same cost but is a separate mode of an AOT compiler, so it can also produce a standalone binary from the same frontend. Both leave CPython semantics intact for what they do not compile, and both keep the CPython object model for the boundary.

## What pyscriptc already has

The AOT path is the whole compiler. `pyscriptc compile FILE` parses with the host's `ast`, runs the checker bridge for facts, lowers the typed subset to a typed IR, emits LLVM IR, and links the `.ll` plus the C runtime through `zig cc` into a native binary. Annotations are not documentation in this pipeline: they are the lowering input, read by the checker and by the lowerer, and they decide the emitted code. A missing annotation is a fence (`PS1001`), not an inferred `Any`. The types are erased at compile time (no type objects, no annotation tables in the binary), but every annotation was read.

The specialization Codon gets from inference, pyscriptc gets from the written annotations. `T = TypeVar("T")` at module level plus `def first(xs: list[T]) -> T` monomorphizes per call site, over scalars and user classes alike, and so do `T | None`, `Callable[[T], U]` and a TypeVar key in `dict[K, V]`. A bound TypeVar erases to its bound. So does the compiler's own inference arm: `pyscriptc infer` drives the checker over a package tree, collects the type every call site agrees on, and pins a parameter that no module annotates (issue #731, the `--bridge own` arm). The soundness rule is the same everywhere: a parameter is pinned only when every call site proves the same concrete type and that type is one the subset can express.

The island tier is the second thing pyscriptc has that the comparison above usually skips. `pyscriptc compile FILE --fallback=cpython` (alias `--island`) links an embedded CPython 3.14 into the same binary and routes what the subset cannot compile there, at the granularity of the definition: typed functions stay native, untyped ones run under the embedded interpreter, and values cross one way per call (scalars, `str`, `bytes` and lists of scalars by value, `dict` and everything else as an opaque handle). `pyscriptc assess` prints the plane map, so the split is visible before the compile.

So the honest framing of the comparison is: pyscriptc is Codon's AOT mode with the annotation requirement kept and the inference added as an opt-in arm, plus a permanent escape hatch that Codon only has through `from python import` (a partial, explicit per-module escape, against pyscriptc's automatic per-definition routing). pyscriptc is not Numba's shape at all: there is no compile-on-first-call and no run-time type inference from values, because the whole parity contract (`docs/reference/divergences.md`) depends on the emitted semantics being fixed before the program ships.

## What the typed-subset scope in AGENTS.md says about this

The scope decision is explicit and it binds this question. The 2026-10-25 decision in `.agents/decisions.md` reads: "Typed-subset positioning: island is the floor, native is the ceiling; guarded speculation permanently dropped", with the why being "Byte-parity of dynamic Python is retired; refusal-by-default is the soundness guarantee" and the rejected alternative being the "CPython-parity bailout architecture". The 2026-10-24 decision next to it fixes the package tiering rule: one tier per package, never per feature, never approximate.

Three consequences for this note.

First, a JIT is the "guarded speculation" the decision drops. A JIT specializes on run-time argument types, which means the emitted code is not known at ship time, which means the parity contract cannot be a property of the binary. Divergence 1 (an `int` is `i64`, overflow aborts) and divergence 36 (the float spelling) are already places where pyscriptc's emitted semantics differ from CPython's in ways it can state precisely because the semantics are fixed at compile time. A JIT would make those per-shape and per-call-site.

Second, the parity contract is the product. `docs/reference/divergences.md` says plainly: "The list is load-bearing: anything not listed here is claimed identical, so a difference you find that is not listed is a bug." A JIT that produced a different numeric result for a shape the compiler never saw would be a silent divergence, which is the one thing the contract forbids.

Third, `@par` is not a typing feature and not an AOT-vs-JIT question. It is a parallelism annotation, and it needs two things pyscriptc does not have: a loop lower that can outline a loop body for a worker, and a memory model that says what happens when two workers touch the same container. The threads plan (`.agents/docs/design/threads-plan.md`) chose SC-DRF, sequentially consistent for data-race-free programs, and it says outright that a GIL, or any attempt to make container mutation atomic, is out of scope. The runtime has no GC and no refcounts (`ps_alloc` is `calloc` with no free), which is what makes SC-DRF workable, but it also means there is no ownership discipline that a parallel loop could lean on. Codon's own docs carry the same warning in a different form: modifying shared lists or dicts inside a parallel section needs a lock or a critical section.

## Three candidate designs

Each candidate below names the DX it buys, what it costs in the compiler, what it risks, and what would have to be true for it to be worth doing.

### Candidate A: a JIT mode (`--jit`), Codon-style

Shape: `pyscriptc compile FILE --jit` produces a binary that compiles the annotated hot functions at first call, for the concrete argument types the caller supplies, caching the native entry point per type shape. Or, closer to Codon's own mode, an in-process `@pyscriptc.jit` decorator that a program compiled with `--fallback=cpython` can use.

DX gain: the user writes one decorator instead of moving a hot loop into a separate module, and pays the compile cost at run time instead of at build time. For a user with a workload whose shapes are known only at run time (a server whose request mix is not known at build time), that is real. It is also the shape Numba users already know.

Cost: this is the largest of the three by a wide margin. It needs an ORC-JIT or an equivalent code-loading path in the compiler (the current backend emits `.ll` text and links it with `zig cc`, which has no run-time load path), a specialization key and a cache, a way to re-enter the lowering pipeline from inside the compiled program (which means the compiler has to be a library the binary can call, or the binary ships the compiler), a marshalling layer for arguments and return values in both directions, and a story for the exception model, which is currently `setjmp`-based and per-frame. Every one of those touches the IR, the emitter, the runtime and the canonicity gates (`.agents/docs/spec/ir-canonicity.md` is a frozen gate, and `TIR_HASHES.json` is the one canonicity gate per the 2026-11-11 decision).

Risk: high, and the risk is not technical but contractual. A JIT's whole premise is that the emitted code is not fixed at ship time, which is the premise the parity contract denies. A JIT build would have to be a separate product with its own divergence list, and every user of it would be outside the "compiled programs match CPython 3.14 on stdout for everything that compiles" promise. It would also reopen the architecture the 2026-10-25 decision closed, and it would add a second compiler pipeline that has to be maintained beside the AOT one, in a project where the compiler is already large (`coro.py` alone is 113 KB).

Verdict: not recommended. If a JIT ever lands, it lands as a separate product with a separate contract, not as a flag on this one.

### Candidate B: a `@par`-style parallel-loop annotation

Shape: a `@par` marker on a `for` loop that the compiler lowers to an outlined loop body executed by a worker pool, with the shared, private and reduction variables inferred the way Codon's OpenMP pass infers them. `pyscriptc compile FILE --threads=N` or a `@par(num_threads=4)` parameter sets the width.

DX gain: real for a user with an embarrassingly parallel numeric loop over the `pyscriptc_ndarray` surface, which is the one place in the subset where a data-parallel loop has both a dense memory layout and no aliasing surprises. It is also the annotation a Codon user would try first, so adopting the spelling reduces the porting cost for that audience.

Cost: medium to high. It needs a loop outline in the emitter (the loop body becomes a callable the worker runs, so the body's locals must be captured into a frame the way the coroutine pass already does), a work queue in the runtime (`ps_thread.c` has the pthreads plumbing, but a pool and a barrier are new), a reduction analysis for the loop's induction and accumulator variables, and a rule for what happens when the body touches a container. The compiler would also have to prove the body is free of aliasing writes before it can parallelize anything, which is a real analysis, not a syntactic rule.

Risk: medium to high, and the risk is silent wrong answers. A parallel loop that races on a shared container produces nondeterministic output, and the project's parity contract is byte-exact stdout. The threads plan already scoped the honest subset: `multiprocessing` is out of scope through v0.9.0, `ThreadPoolExecutor` is a separate issue that depends on earlier stages, and a GIL or atomic container mutation is explicitly out of scope. A `@par` that parallelizes a loop whose body writes a list is exactly the case that needs the lock the Codon docs also demand, and pyscriptc has no lock discipline to offer there yet.

Verdict: not now, and not as a typing feature. The right shape is a later issue on the threads track, scoped to loops over the ndarray surface with a proven no-aliasing body, and it must land with the ownership rules the threads plan says are missing. What it needs first is not a decorator but a memory model that says what a worker may touch.

### Candidate C: make the specialization visible (a `@specialize` / `@inline` hint, or a specialization report)

Shape: a compile-time annotation that tells the compiler something it can already do, plus a report that shows what it already did. Two variants.

The annotation variant: `@specialize` on a generic function means "monomorphize this per call site, do not share one generic body". That is already what happens for `TypeVar` functions (`.agents/docs/python-style.md` rule 7), so the annotation is a no-op on the code and a statement of intent on the page. A `@inline` hint would ask the emitter to inline a callee at the call site; the optimization note (`.agents/notes/2026-11-07-compiler-optimization-research.md`) already measured that LLVM inlines at `-O2` and that its `fastcc` experiment on one function showed 24 percent, so an emitter-level inline hint is likely small.

The report variant: `pyscriptc compile FILE --specializations` (or an extension of `pyscriptc assess`) prints, per generic function, the call-site types it was monomorphized to, the number of copies emitted, and the pin the inference arm used for an unannotated parameter. This is the same shape as the plane map `assess` already prints for the island split, and the same "the compiler tells you what it did" DX that `coverage` and `explain` already provide.

DX gain: this is the one that answers the issue's real question. A user who wonders why their generic helper is slow, or who wants to know whether the compiler specialized on the type they expected, currently has no way to ask. The `TypeVar` monomorphization is the compiler's most surprising behavior for a Python user, because Python has one function and pyscriptc emits N. A `@specialize` decorator would also give the user a portable spelling that reads the same in Codon and in pyscriptc, and it would be the natural home for a future `@par` and for any future JIT-mode marker, so the annotation namespace exists before the features do.

Cost: low. The annotation variant is a few lines in the lowering's decorator handling (the erase path already exists in `_classes_more.py`, and the closed set of behavior-synthesizing decorators is already enumerated there). The report variant is a coverage-style report over the lowering's own signature data, plus a CLI flag, plus the doc updates the AGENTS.md rule requires for a change in what the compiler reports.

Risk: low. The risks are the ordinary ones of a new surface: an annotation that promises something the compiler does not do (so the annotation must either lower to real behavior or be refused with a named code, never silently ignored), and a report that shows something the compiler does not actually do (so the report must read the lowering's own facts, not a re-derivation).

Verdict: recommended, as the next step. It buys the DX that a JIT or a `@par` would be asked for, at a small fraction of the cost, and it does not touch the parity contract, the frozen gates or the island decision.

## The recommendation

Do candidate C, as one PR, and do candidate A and B not now.

The recommended PR is small enough for one reviewer: a `@specialize` decorator that the lowering accepts on a generic function (it asserts the monomorphization the compiler already performs, and fences with a named code if the function is not generic), plus a `--specializations` report that lists the monomorphized copies and the inference pins, plus the doc and probe updates the repo's rules require. It touches the lowering, one CLI surface, `docs/guide/python-subset.md` (the typing table), `.agents/docs/python-style.md` (the idiom rules) and the diagnostics registry for the new code. It adds no IR kind, so the `TIR_HASHES.json` canonicity gate does not move, and it adds no runtime, so the boxed ledger does not move.

Why this and not the others, in one sentence each.

A JIT is refused because the parity contract (`docs/reference/divergences.md`: "anything not listed here is claimed identical") and the 2026-10-25 typed-subset decision ("guarded speculation permanently dropped") both require the emitted semantics to be fixed before the program ships, and a JIT's premise is that they are not.

`@par` is deferred because the honest subset for it needs a memory model the threads plan has not finished (who owns a container a worker touches), a loop outline the emitter does not have, and a reduction analysis the lowering does not have; the ndarray surface and a proven no-aliasing body are the right scope, and that belongs on the threads track as its own issue, not as a typing feature.

The visible-specialization work is recommended because it is the part of the JIT and Codon story that a pyscriptc user can actually use today, and because it is the annotation namespace that a later `@par` or JIT-mode marker would need anyway.

One caveat worth stating: the recommended PR does not make the compiler faster. It makes the compiler legible. If the issue's real driver is a measured performance gap, the gap belongs in the baseline suite (`benchmarks/baseline/suite.json`, 35 workloads with a recorded Linux reference) and in the optimization note's measured list, not in a JIT.

## A proposed decisions.md row (not appended)

The row below is proposed, not appended. The 2026-10-28 cleanup moved the full prose of each decision into git history and kept the table one row per decision, newest last, so appending it is an owner action.

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-17 | No JIT mode and no `@par` annotation in v0.11.0; the compiler stays ahead-of-time and the visible-specialization work (`@specialize`, the specialization report) is the next step (#1283) | The parity contract requires the emitted semantics to be fixed at compile time, and the 2026-10-25 typed-subset decision dropped guarded speculation, so a run-time specialization tier would be a separate product with its own divergence list, not a flag on this one; `@par` needs a memory model, a loop outline and a reduction analysis the threads plan has not finished | A `--jit` mode (Codon-style `@codon.jit`), an OpenMP-style `@par` loop annotation, a Numba-style in-process decorator | `.agents/docs/design/jit-and-codon-annotations.md` |

## Related

- `docs/guide/python-subset.md`: the per-construct typing table (the `TypeVar` row names the monomorphization this note makes visible).
- `.agents/docs/python-style.md`: the agent-facing idiom rules (rule 7 is the monomorphization rule).
- `.agents/decisions.md`: the 2026-10-25 typed-subset positioning and the 2026-10-24 package tiering rows that bind this question.
- `.agents/docs/design/threads-plan.md`: the SC-DRF memory model and the out-of-scope list a `@par` would have to work inside.
- `.agents/notes/2026-11-07-compiler-optimization-research.md`: the measured optimization list, including the 24 percent `fastcc` number and the 2.3x the checked arithmetic costs over plain adds on an int kernel.
- `docs/reference/divergences.md`: the parity contract, divergence 14 (the erased ABC and Protocol bases) and divergence 41 (the fallback tier's one-way crossing).
- `docs/guide/comparison.md`: the user-facing comparison table that already names Codon and Numba.
