# JIT and Codon-style annotations in pyscriptc (issue #1283)

Status: design note, first written 2026-11-17, updated 2026-11-26 after the annotation-surface cluster landed. The decision row at the end of this note is appended to `.agents/decisions.md` in the same PR.

The issue asks whether typed annotations could drive a JIT or a Codon-like specialization path alongside the AOT compiler, and asks for a note comparing Codon, Numba and the annotation-as-lowering-input model pyscriptc already has. This note answers that, plus the question the issue implies: what would a `@codon.jit`-shaped or `@par`-shaped annotation give a pyscriptc user that the compiler does not already give them.

The short answer: pyscriptc already does the part of Codon that matters for a typed subset (compile-time specialization from annotations, monomorphization per call site, a native binary with no interpreter in the loop), and it deliberately refuses the part of Codon that trades CPython semantics away. A JIT would trade away the parity contract that the whole project is built on, and the `@par` annotation would need a memory model and a loop-lower that the project has not built yet. The cheapest real answer is the one that landed: making the specialization the compiler already performs visible, as `@specialize` plus a specialization report.

What changed since the first version of this note: the recommendation has been executed. The shadow annotation module (#1306), the `@island` demotion notes (#1307), the default island routing for imports outside the closed table (#1308) and the `@specialize` marker plus the `--specializations` report (#1309, PR #1409) all landed between 2026-11-18 and 2026-11-26. The note below is updated to say what the compiler does today, what the measurement against a real library target says, and what the next scoped issue would be.

## What Codon gives a user, in two pieces

Codon is an ahead-of-time compiler for a Python-like language with its own type checker (a modified Hindley-Milner) and an LLVM backend. Two features of it are the ones this issue names, and they are independent.

The first is that annotations are optional. Codon infers types and monomorphizes per call site, so a program with no annotations at all still compiles to native code. Codon's own generics documentation states the mechanism: a generic parameter "gets realized based on the argument type", with `foo([20, 42, 12])` realizing `T` as `int` and `foo(["hello", "world"])` realizing it as `str`, and the wrong argument type failing at compile time rather than at run time (https://docs.exaloop.io/language/generics/). Codon's own non-goals list says it is "not a drop-in replacement for CPython", that "some aspects of Python that are not suitable for static compilation" are unsupported, and that larger codebases reach Codon through the JIT decorator or the extension backend (https://github.com/exaloop/codon, the README's non-goals section).

The second is that Codon ships a JIT mode, reachable from Python as `@codon.jit`, which compiles a decorated function for the concrete types of the arguments the caller supplies and caches the native entry point per argument-type shape. The JIT is a thin layer over the AOT compiler: it reuses the same frontend, the same type checker and the same LLVM backend. Codon's compiler documentation says the JIT "is based on LLVM's ORC JIT and implemented with LLVM's `LLJIT`", and that it supports the `@codon.jit` Python decorator (https://docs.exaloop.io/developers/compilation/). The Python-side documentation is explicit about the prices: the first call includes compilation, a call with different argument types may require another specialization, the timed call still includes argument and return-value conversion, and "in general, changes made to objects in a JIT'd function will not be reflected in the host Python application, since objects passed to Codon are converted to Codon-native types" (the one exception being NumPy arrays, whose data pointer is shared rather than copied) (https://docs.exaloop.io/integrations/python/codon-from-python/).

The third feature the issue names, `@par`, is not a JIT feature but a parallelism annotation: it marks a `for` loop for OpenMP parallelization, with `num_threads`, `schedule`, `chunk_size`, `ordered` and `collapse` parameters, and Codon's OpenMP pass infers the shared, private and reduction variables automatically (`total += 1` in a `@par` body becomes an atomic reduction, per the Codon README). Codon's multithreading documentation is candid about what it does not make safe: "operations that change a shared container's structure, such as appending to a list or inserting into a dictionary, need synchronization", and "the compiler does not automatically make arbitrary shared mutations thread-safe" (https://docs.exaloop.io/parallel/multithreading/). It also documents that floating-point reductions "may combine values in a different order from a serial loop, so their last few bits can differ even when the calculation is otherwise equivalent", which is a parity-contract problem for pyscriptc, not just a numerical one.

## What Numba gives a user

Numba is a JIT in the classic sense: `@numba.jit` (or `@njit`) decorates a function, LLVM machine code is produced at the first call, and the argument types are inferred then, at run time, from the values the caller passes. Its `nopython` mode supports a documented subset of Python; `object mode` catches the rest at run time with a Python-object fallback. Numba is aimed squarely at array and numerical code, with deep NumPy integration and CPU and GPU targets. Compilation happens inside a running CPython process; there is no standalone binary.

Numba's own reference documents the lazy model precisely: with no signature given, "each call to the decorated function will try to re-use an existing specialization if it exists", and if none fits, "a new specialization is compiled on-the-fly, stored for later use" (https://numba.readthedocs.io/en/stable/reference/jit-compilation.html). The dispatching document spells out the cost model: it is a multiple dispatch over the argument types, the concrete types are inferred at call time, and the "best match" search ranks candidate specializations by an ordered tuple of conversion kinds, exact matches preferred over promotions (https://numba.readthedocs.io/en/latest/developer/dispatching.html). That document also states the performance constraint the design has to meet: dispatch overhead "should stay under a microsecond".

The comparison between the two is instructive for pyscriptc because they are the same idea with different price tags. Numba's compile-time cost is paid by the running program, on the first call, and the compiled code is per argument-type shape, so a program that calls one function with `int` and then with `float` compiles twice. Codon's JIT pays the same cost but is a separate mode of an AOT compiler, so it can also produce a standalone binary from the same frontend. Both leave CPython semantics behind in the JIT'd region: Codon's docs say so about mutation visibility, and Numba's `nopython` mode is a documented subset with a separate object-mode fallback.

## What pyscriptc already has

The AOT path is the whole compiler. `pyscriptc compile FILE` parses with the host's `ast`, runs the checker bridge for facts, lowers the typed subset to a typed IR, emits LLVM IR, and links the `.ll` plus the C runtime through `zig cc` into a native binary. Annotations are not documentation in this pipeline: they are the lowering input, read by the checker and by the lowerer, and they decide the emitted code. A missing annotation is a fence (`PS1001`), not an inferred `Any`. The types are erased at emit time, so a generic function that is specialized over `int` and over a user class emits two native bodies with no runtime type tags.

The specialization Codon gets from inference, pyscriptc gets from the written annotations. `T = TypeVar("T")` at module level plus `def first(xs: list[T]) -> T` monomorphizes per call site, over scalars and user classes alike, and so do `T | None`, `Callable[[T], U]` and a TypeVar key in `dict[K, V]`. A bound TypeVar erases to its bound. So does the compiler's own inference arm: `pyscriptc infer` drives the checker over a package tree, collects the type every call site agrees on, and pins a parameter that the source left unannotated, so the same per-call-site specialization happens without the user writing the TypeVar.

The island tier is the second thing pyscriptc has that the comparison above usually skips. `pyscriptc compile FILE --fallback=cpython` (alias `--island`) links an embedded CPython 3.14 into the same binary and routes what the subset cannot compile there, at the granularity of the definition: typed functions stay native, untyped ones run under the embedded interpreter, and values cross one way per call (scalars, `str`, `bytes` and lists of scalars by value, `dict` and everything else as an opaque handle).

So the honest framing of the comparison is: pyscriptc is Codon's AOT mode with the annotation requirement kept and the inference added as an opt-in arm, plus a permanent escape hatch that Codon only has through `from python import` (a partial, explicit per-module escape, against pyscriptc's automatic per-definition routing). pyscriptc is not Numba's shape at all: there is no compile-on-first-call and no run-time type inference from values, because the whole parity contract (`docs/reference/divergences.md`) depends on the emitted semantics being fixed before the program ships.

## What the typed-subset scope in AGENTS.md says about this

The scope decision is explicit and it binds this question. The 2026-10-25 decision in `.agents/decisions.md` reads: "Typed-subset positioning: island is the floor, native is the ceiling; guarded speculation permanently dropped", with the why being "Byte-parity of dynamic Python is retired; refusal-by-default is the soundness guarantee" and the rejected alternative being the "CPython-parity bailout architecture". The 2026-10-24 decision next to it fixes the package tiering rule: one tier per package, never per feature, never approximate.

Three consequences for this note.

First, a JIT is the "guarded speculation" the decision drops. A JIT specializes on run-time argument types, which means the emitted code is not known at ship time, which means the parity contract cannot be a property of the binary. Divergence 1 (an `int` is `i64`, overflow aborts) and divergence 36 (the float spelling) are already places where pyscriptc's emitted semantics differ from CPython's in ways it can state precisely because the semantics are fixed at compile time. A JIT would make those into "whatever the shape was", which is exactly the silent divergence the contract forbids.

Second, the parity contract is the product. `docs/reference/divergences.md` says plainly: "The list is load-bearing: anything not listed here is claimed identical, so a difference you find that is not listed is a bug." A JIT that produced a different numeric result for a shape the compiler never saw would be a silent divergence, which is the one thing the contract forbids. Codon's own multithreading documentation already shows how easy that is to get wrong: a floating-point reduction may differ in its last bits from the serial order.

Third, `@par` is not a typing feature and not an AOT-vs-JIT question. It is a parallelism annotation, and it needs two things pyscriptc does not have: a loop lower that can outline a loop body for a worker, and a memory model that says what happens when two workers touch the same container. The threads plan (`.agents/docs/design/threads-plan.md`) chose SC-DRF, sequentially consistent for data-race-free programs, and it says outright that a GIL, or any attempt to make container mutation atomic, is out of scope.

## Three candidate designs

Each candidate below names the DX it buys, what it costs in the compiler, what it risks, and what would have to be true for it to be worth doing.

### Candidate A: a JIT mode (`--jit`), Codon-style

Shape: `pyscriptc compile FILE --jit` produces a binary that compiles the annotated hot functions at first call, for the concrete argument types the caller supplies, caching the native entry point per type shape. Or, closer to Codon's own mode, an in-process `@pyscriptc.jit` decorator that a program compiled with `--fallback=cpython` can use.

DX gain: the user writes one decorator instead of moving a hot loop into a separate module, and pays the compile cost at run time instead of at build time. For a user with a workload whose shapes are known only at run time (a server whose request mix is not known at build time), that is real. It is also the shape Numba users already know.

Cost: this is the largest of the three by a wide margin. It needs an ORC-JIT or an equivalent code-loading path in the compiler (the current backend emits `.ll` text and links it with `zig cc`, which has no run-time load path), a specialization key and a cache, a way to re-enter the lowering pipeline from inside the compiled program (which means the compiler has to be a library the binary can call, or the binary ships the compiler), a marshalling layer for arguments and return values in both directions, and a second parity contract. Codon's own implementation is the reference for the size of that job: an LLVM `LLJIT` front end, a native-pointer cache keyed on the concrete input types, and a generated wrapper per specialization that performs the type conversions in both directions (the `__codon_wrapped__sum_of_squares_0` wrapper in Codon's debug output is exactly that shape). pyscriptc would additionally have to answer the question Codon's docs answer by refusing it: what happens to a value the caller mutated. Codon answers "not visible to the host" and pushes the user to return values instead; pyscriptc's contract is byte-identical stdout, so the answer would have to be "identical", which means a real marshalling layer per type, not a shared pointer.

Risk: high, and the risk is not technical but contractual. A JIT's whole premise is that the emitted code is not fixed at ship time, which is the premise the parity contract denies. A JIT build would have to be a separate product with its own divergence list, and every user of it would be outside the "compiled programs match CPython 3.14 on stdout for everything that compiles" promise. It would also reopen the architecture the 2026-10-25 decision closed, and it would add a second compiler pipeline to maintain (the shipped binary would need the compiler inside it, or the user would need the compiler at run time).

Verdict: not recommended. If a JIT ever lands, it lands as a separate product with a separate contract, not as a flag on this one.

### Candidate B: a `@par`-style parallel-loop annotation

Shape: a `@par` marker on a `for` loop that the compiler lowers to an outlined loop body executed by a worker pool, with the shared, private and reduction variables inferred the way Codon's OpenMP pass infers them. `pyscriptc compile FILE --threads=N` or a `@par(num_threads=4)` parameter sets the width.

DX gain: real for a user with an embarrassingly parallel numeric loop over the `pyscriptc_ndarray` surface, which is the one place in the subset where a data-parallel loop has both a dense memory layout and no aliasing surprises. It is also the annotation a Codon user would try first, so adopting the spelling reduces the porting cost for that audience.

Cost: medium to high. It needs a loop outline in the emitter (the loop body becomes a callable the worker runs, so the body's locals must be captured into a frame the way the coroutine pass already does), a work queue in the runtime (`ps_thread.c` has the pthreads plumbing, but a pool and a barrier are new), a reduction analysis for the loop's induction and accumulator variables, and a rule for what happens when the body touches a container. The compiler would also have to prove the body is free of shared mutation, which is the hard half.

Risk: medium to high, and the risk is silent wrong answers. A parallel loop that races on a shared container produces nondeterministic output, and the project's parity contract is byte-exact stdout. Codon's own docs name the same hazard and refuse to paper over it ("the compiler does not automatically make arbitrary shared mutations thread-safe"), and Codon's float reductions already differ in the last bits from the serial order. The threads plan already scoped the honest subset: `multiprocessing` is out of scope through v0.9.0, `ThreadPoolExecutor` is a separate issue that depends on earlier stages, and a GIL or atomic container mutation is explicitly out of scope. A `@par` that parallelizes a loop whose body writes a list would produce output that differs from CPython's, which is a parity break, not a speedup.

Verdict: not now, and not as a typing feature. The right shape is a later issue on the threads track, scoped to loops over the ndarray surface with a proven no-aliasing body, and it must land with the ownership rules the threads plan says are missing. What it needs first is not a decorator but a memory model that says what a worker may touch.

### Candidate C: make the specialization visible (a `@specialize` / `@inline` hint, or a specialization report)

Shape: a compile-time annotation that tells the compiler something it can already do, plus a report that shows what it already did. Two variants.

The annotation variant: `@specialize` on a generic function means "monomorphize this per call site, do not share one generic body". That is already what happens for `TypeVar` functions (`.agents/docs/python-style.md` rule 7), so the annotation is a no-op on the code and a statement of intent on the page. A `@inline` hint would ask the emitter to inline a callee at the call site; the optimization note (`.agents/notes/2026-11-07-compiler-optimization-research.md`) already measured that LLVM inlining is not the bottleneck, so an inline hint is not worth a surface.

The report variant: `pyscriptc compile FILE --specializations` prints, per generic function, the call-site types it was monomorphized to, the number of copies emitted, and the pin the inference arm used for an unannotated parameter. This is the same shape as the plane map `assess` already prints for the island split, and the same "the compiler tells you what it did" DX that `coverage` and `explain` already provide.

DX gain: this is the one that answers the issue's real question. A user who wonders why their generic helper is slow, or who wants to know whether the compiler specialized on the type they expected, currently has no way to ask. The `TypeVar` monomorphization is the compiler's most surprising behavior for a Python user, because Python has one function and pyscriptc emits N.

Cost: low. The annotation variant is a few lines in the lowering's decorator handling (the erase path already exists, and the closed set of behavior-synthesizing decorators is already enumerated in the shadow module's rule table). The report variant is a coverage-style report over the lowering's own signature data, plus a CLI flag, plus the doc updates the AGENTS.md rule requires for a change in what the compiler reports.

Risk: low. The risks are the ordinary ones of a new surface: an annotation that promises something the compiler does not do (so the annotation must either lower to real behavior or be refused with a named code, never silently ignored), and a report that shows something the compiler does not actually do (so the report must read the lowering's own facts, not a re-derivation).

Verdict: recommended, and shipped. See the next section.

## What landed (2026-11-18 to 2026-11-26)

The recommendation above was executed as four small PRs, one per issue in the annotation-surface cluster that this note and its successor (`.agents/docs/design/island-promotion-and-annotation-surface.md`, candidate A5) opened.

| Issue | What landed | Where |
| --- | --- | --- |
| #1306 | The inert shadow module `pyscriptc._annotations`: importable and a no-op on plain CPython and PyPy, erased like a `typing` import under the compiler, so a decorated definition runs unmodified outside pyscriptc | `src/pyscriptc/_annotations.py` |
| #1307 | `@island`: a deliberate demotion marker. Under `--fallback=cpython` the marked definition routes to the embedded CPython on purpose and the compile and `assess` name the routing instead of fencing | `src/pyscriptc/boxed.py`, `src/pyscriptc/lower/` |
| #1308 | The default island routing: the closed import table stopped being an admission gate, so an import of a module outside it (an unknown name, a third-party package, a stdlib module the table does not carry) routes to the island lane with no flag and no opt-out, and the closed-table refusal became an advisory note. An explicit `[tool.pyscriptc.imports]` lane still wins over the default | `src/pyscriptc/imports.py`, `src/pyscriptc/config.py`, `src/pyscriptc/lower/_stdlib_bind.py` |
| #1309 | `@specialize` plus the specialization report: the marker asserts the per-call-site monomorphization the lowering already performs, and `pyscriptc compile FILE --specializations` prints the emitted copies and their TypeVar substitutions | `src/pyscriptc/lower/_specialize.py`, `src/pyscriptc/cli/_main.py` |

The design rules the shipped code follows, which are the ones this note proposed:

The marker is checked, not silently accepted. A marked definition whose signature carries no TypeVar (whose class declares no TypeVar parameter) fences `PS1207`: the annotation promised something the compiler does not do for it, and a hint the compiler ignores is worse than a refusal naming the mismatch.

The report reads the lowering's own instantiation tables (`inst_fns`, `class_insts`) after the drains, so it never re-derives what the compiler did. One row per marked definition, with the emitted copies and the TypeVar substitution each copy took. A marked definition that specialized nowhere reports zero copies.

The annotation surface stayed at one module with two decorators. The successor note's rule holds: a third decorator requires a fence-mass receipt.

## What this buys against a real library target: the jinja2 measurement

The milestone's library targets are the reason the question matters, so the note measures the answer on one of them rather than asserting it. jinja2 3.1.6 (pure Python, `py.typed`, rank 41 in the top-100 download list) was measured on 2026-11-26 with `pyscriptc coverage jinja2` over the wheel's source tree.

The annotation shape is not the blocker. jinja2 is 98 percent annotated by the measure the compiler cares about (761 of 775 module-level functions have every parameter and the return annotated; 25 source files). Compare the corpus packages that are far below that and still fenced. The measured result is:

```
jinja2: 756 fences (605 root) in 530 of 935 definitions (405 native)
```

The fence breakdown, by code:

| Code | Fences | What it is |
| --- | --- | --- |
| PS2002 | 241 | 94 of them "this class base is not lowered"; the rest are unlowered calls, unexported package names and typing names used as values |
| PS1106 | 166 | the annotation names a type the lowering does not know (a class in another module of the same package, mostly) |
| PS1104 | 129 | `t.Any` is a dynamic or structural type and is not lowered |
| PS2001 | 68 | a field read the class lowering does not have, or a stdlib surface not in the lowered subset |
| PS1105 | 69 | a forward reference that does not resolve |
| PS1001 | 26 | an unannotated parameter |
| PS1103 | 24 | a generic alias form |

Three things follow from that table.

First, the annotation surface is not the wall. PS1104 (`t.Any`) plus PS1106 plus PS1105 is 364 fences, and those are three different problems: a dynamic type the subset refuses on purpose (the `object` narrowing of #1226 and #1287 handles the `dict[str, object]` shape, but `t.Any` as a bare annotation does not lower), a cross-module class name the annotation resolver cannot see, and a forward reference the resolver cannot resolve. None of them is "the user did not write the types".

Second, the class-base wall is the largest single family (94 PS2002), and it is the same wall the successor note's N1 per-class demotion plan targets. It is not a typing problem and no annotation surface would move it.

Third, and this is the answer to the issue's implicit question: none of the three candidate paths moves jinja2 much. A JIT cannot lower a class base that the class lowering refuses (the compiler would have to have a run-time type dispatch, which is the island's job). `@par` is irrelevant to a template engine (the hot loop is character-by-character string work, not a dense numeric kernel, so there is no ndarray-shaped parallel loop to outline). `@specialize` moves nothing either: jinja2 has almost no generics in the `TypeVar` sense, so the report has little to report. The paths that would move jinja2 are the compile-time ones the successor note already identified: the annotation-resolver gaps (PS1106 and PS1105, which are the same-package class table; slices 1 and 2 of the follow-up below) and the dynamic annotation (PS1104), plus the class-base demotion (N1), which is the strategic track. The resolver and the dynamic annotation are typing-surface work in the v0.11.0 theme; the class-base wall is not.

That is the honest summary of what this issue's answer buys for the milestone's targets: the JIT and `@par` paths buy nothing for the library targets, and the path that does buy something for them was never a JIT question. It is the annotation-surface and typing-surface work, and it is compile-time.

## The recommendation

Do candidate C, as one PR, and do candidate A and B not now.

Candidate C is done. The recommended PR was small enough for one reviewer: a `@specialize` decorator that the lowering accepts on a generic function (it asserts the monomorphization the compiler already performs, and fences with a named code if the function is not generic), plus a `--specializations` report that lists the monomorphized copies and the inference pins, plus the doc and probe updates the repo's rules require. It touched the lowering, one CLI surface, `docs/guide/python-subset.md` (the typing table), `.agents/docs/python-style.md` (rule 7), `docs/reference/cli.md`, `docs/reference/diagnostics.md` and a differential probe.

Why this and not the others, in one sentence each.

A JIT is refused because the parity contract (`docs/reference/divergences.md`: "anything not listed here is claimed identical") and the 2026-10-25 typed-subset decision ("guarded speculation permanently dropped") both require the emitted semantics to be fixed before the program ships, and a JIT's premise is that they are not. The jinja2 measurement is the second reason: the fences that block a real library target are compile-time structural fences (class bases, cross-module annotation names, dynamic annotations), and a JIT does not lower a class base any better than the AOT path does.

`@par` is deferred because the honest subset for it needs a memory model the threads plan has not finished (who owns a container a worker touches), a loop outline the emitter does not have, and a reduction analysis the lowering does not have; the ndarray surface and a proven no-aliasing body are the right scope, and that belongs on the threads track as its own issue, not as a typing feature. The float-reduction bit difference Codon's own docs describe is also a parity-contract problem, which makes `@par` a contract change, not only a performance feature.

The visible-specialization work is recommended because it is the part of the JIT and Codon story that a pyscriptc user can actually use today, and because it is the annotation namespace that a later `@par` or JIT-mode marker would need anyway. It is shipped.

One caveat worth stating: the recommended work does not make the compiler faster. It makes the compiler legible. If the issue's real driver is a measured performance gap, the gap belongs in the baseline suite (`benchmarks/baseline/suite.json`, 35 workloads with a recorded Linux reference) and in the optimization note's measured list, not in a JIT.

## What a follow-up implementation issue would scope

The follow-up is not a JIT. It is the typing-surface work the jinja2 measurement points at, and it belongs to the annotation-resolver family rather than to the annotation-surface family this issue opened. One issue, scoped as follows.

Title: lower: the annotation resolver sees the package's own classes (the PS1106 and PS1105 remainder) and the dynamic-annotation shape (PS1104).

What: three slices, in the order the jinja2 measurement ranks them. Slices 1 and 2 are one fix seen from two directions: both are the same-package class name, spelled bare in one and quoted in the other.

1. The cross-module class name in annotation position (PS1106). jinja2's 166 PS1106 fences are dominated by first-party classes in sibling modules of the same package: `nodes.Expr` 27, `nodes.Node` 18, `nodes.Template` 9, `nodes.Macro` 5, and the rest of the `nodes` module's classes. That is the same shape issue #1338 measured across 25 real typed PyPI files (181 fences, dominated by `Version` 27, `Interval` 20, `Tokenizer` 13). The slice: the annotation resolver consults the package's class table (the one the project-mode compile already builds for the class lowering, the table #1242 extended for the qualified `mod.Thing` call path), so a same-package class named in an annotation resolves instead of fencing. The receipt: the jinja2 PS1106 count, and the #1338 census, both before and after.

2. The forward reference that does not resolve (PS1105). jinja2's 69 PS1105 fences are the quoted spellings of the same cross-module names: `Environment` 33, `Context` 12, `EvalContext` 6, `Extension` 2, `te.Self` 6, `te.NoReturn` 6, and a `logging.Logger` and a `t.Optional[Environment]`. The quoted form is not an ordering problem inside one module (the resolver already retries a quoted name after the class pass, so `'Node'` in the same module resolves); it fences because the name lives in a sibling module of the package. The slice: the same package class table, reached from the forward-reference path, so the quoted spelling of a sibling-module class resolves too. The receipt: the PS1105 count on jinja2 and on the corpus packages that carry it.

3. The dynamic annotation (PS1104, `t.Any`). jinja2's 129 PS1104 fences are `t.Any` annotations, which the subset refuses because a bare `Any` carries no layout. The slice: decide whether `t.Any` erases to the same tagged dynamic value as `dict[str, object]` (the #1226 and #1287 work) or stays refused. The decision is the owner's, and the note's recommendation is to erase it to the tagged dynamic value, because a template engine's whole surface is "a value of unknown type" and the tagged dynamic value is the one shape the subset already has for that. The receipt: the PS1104 count on jinja2.

Done when: each slice lowers with a named diagnostic where it cannot, the jinja2 fence counts are recorded before and after in the PR, the differential probe for each new lowering shape is added under `probes/differential/` and byte-compares stdout, stderr and the exit code against the pinned CPython, and the construct table in `docs/guide/python-subset.md` is updated in the same PR.

Explicitly out of scope: the JIT (candidate A), `@par` (candidate B), and any change to the island tier's routing granularity. The class-base wall (94 PS2002 fences on jinja2) stays with the successor note's N1 per-class demotion plan, which is the strategic track and not a typing slice.

## The decisions.md row

The row below is the decision this note recommends. It is appended to `.agents/decisions.md` in the same PR, because the recommendation is clear and the owner ratifies it by merging. If the owner wants a different call, the row is the thing to edit.

| Date | Decision | Why (one line) | Rejected alternatives | Detail |
| --- | --- | --- | --- | --- |
| 2026-11-26 | No JIT mode and no `@par` annotation: the compiler stays ahead-of-time, the annotation surface stays at one module with two decorators (`@island`, `@specialize`), and the next library-depth work is the compile-time typing surface (the PS1106 and PS1105 annotation resolver gaps and the PS1104 dynamic annotation) (#1283) | The parity contract requires the emitted semantics to be fixed at compile time and the 2026-10-25 typed-subset decision dropped guarded speculation, so a run-time specialization tier would be a separate product with its own divergence list; and the jinja2 measurement (756 fences, 405 of 935 definitions native, 98 percent of the functions annotated) shows the blocking fences are compile-time structural ones (94 class bases, 166 cross-module annotation names, 129 dynamic annotations) that no JIT and no `@par` moves | A Codon-style `--jit` mode or in-process `@pyscriptc.jit` decorator (needs an ORC load path, a compiler inside the binary, a two-way marshalling layer and a second divergence list), a Codon-style `@par` annotation (needs a memory model, a loop outline and a reduction analysis, and Codon's own float reductions already differ in the last bits, which breaks the parity contract), a third annotation decorator (needs a fence-mass receipt first) | `.agents/docs/design/jit-and-codon-annotations.md`, `.agents/docs/design/island-promotion-and-annotation-surface.md` |

## Related

- `docs/guide/python-subset.md`: the per-construct typing table (the `TypeVar` row names the monomorphization this note makes visible, and the `@island`, `@specialize` row names the shipped surface).
- `.agents/docs/python-style.md`: the agent-facing idiom rules (rule 7 is the monomorphization rule).
- `.agents/decisions.md`: the 2026-10-25 typed-subset positioning and the 2026-10-24 package tiering rows that bind this question.
- `.agents/docs/design/island-promotion-and-annotation-surface.md`: the successor note (the island-promotion survey, the annotation-surface candidates A1 to A5, and the N1 to N8 plan).
- `.agents/docs/design/threads-plan.md`: the SC-DRF memory model and the out-of-scope list a `@par` would have to work inside.
- `.agents/docs/design/loose-typing-and-default-inference.md`: the inference default question (#1305), the other line this issue's cluster opened.
- `.agents/notes/2026-11-07-compiler-optimization-research.md`: the measured optimization list, including the 24 percent `fastcc` number and the 2.3x the checked arithmetic costs over plain adds on an int kernel.
- `docs/reference/divergences.md`: the parity contract, divergence 14 (the erased ABC and Protocol bases) and divergence 41 (the fallback tier's one-way crossing).
- `docs/guide/comparison.md`: the user-facing comparison table that already names Codon and Numba.
- Sources for the peer facts above: Codon's [generics](https://docs.exaloop.io/language/generics/), [multithreading](https://docs.exaloop.io/parallel/multithreading/), [compiler flow](https://docs.exaloop.io/developers/compilation/) and [Python integration](https://docs.exaloop.io/integrations/python/codon-from-python/) docs, the [Codon README](https://github.com/exaloop/codon), and Numba's [JIT compilation](https://numba.readthedocs.io/en/stable/reference/jit-compilation.html) and [dispatching](https://numba.readthedocs.io/en/latest/developer/dispatching.html) references.
