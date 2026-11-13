# Compiler optimization research: what the survey claims, what we measured, and what to do

Date: 2026-11-07. Branch: `work/optres` at `b2f28bfe`. Epic: #817. Milestone: v0.9.0. Docs only: no source file, no test file and no frozen gate file was touched. The scorer holdout was never read.

This note fact-checks the input survey at `~/Downloads/compiler-optimizations.md` (an LLM-written survey of how LLVM compilers handle dynamic typing) against primary sources and against our own code and measurements, then adds the optimizations and adversarial techniques the survey does not mention but that our measured bottlenecks actually call for. The survey's framing is wrong for us in one important way, and the measurements below show why: we are not a dynamically typed compiler that needs a value representation, we are a typed-subset compiler whose measured bottlenecks are overflow-checked arithmetic, runtime C helper calls, and the compile-time floor.

## 0. What we measured before judging anything

Everything in this note rests on these numbers. They were produced on this worktree (macOS arm64, Apple clang 21.0.0, CPython 3.14.9) and are reproducible with the commands given.

### 0.1 Where we are today

`uv run python scripts/mir_perf_ratio.py --runs 3` gives geomean MIR/direct ratio 1.0130 (kernel 0.9843, stdlib 1.0206, scripting 0.9431). Every row is "inconclusive" because the kernels finish in under a millisecond, exactly as `benchmarks/baseline/README.md` warns. The flip is performance-neutral, which is the right result and also tells us the MIR lane is not currently the place where the wins are.

`benchmarks/baseline/reference.json` (the recorded Linux reference) gives the real picture. Kernel geomean is 4.18 percent of CPython time (about 24x), stdlib 6.92 percent (14.4x), scripting 4.50 percent (22.2x), island 84.9 percent (1.2x). The weakest rows are `pathlib-read` 4.1x, `os-files` 5.6x, `path-join` 7.6x, `struct-pack` 8.2x, `url-parse` 8.8x, `builtins-loop` 9.9x, `stringio-loop` 11.5x, `reduce-sum` 11.7x, `re-scan` 11.8x. Every one of those is a runtime-helper-bound workload, not a codegen-bound one. The best rows are `ssl-const` 37.5x, `ctypes-sizeof` 30.7x, `warn-loop` 30.1x and `sum-squares` 33.1x. The island group is a different animal (1.0x to 1.3x): that is the boxed CPython fallback, and no compiler optimization touches it.

Compile time: median 792 ms per program (`.agents/notes/2026-11-07-compile-time-receipt.md`). The stage table says `check` (pyrefly preflight) 300 ms, `cc_link` 105 ms, link residual 125 ms, `cc_compile` 75 ms, and `lower` plus `emit_llvm` under 5 ms combined. The MIR lane itself is not the compile-time problem; the checker and the link are.

### 0.2 clang optimization level: already at the right setting

I compiled the emitted `.ll` for nine workloads at `-O0`, `-O1`, `-O2`, `-O3`. `-O1`, `-O2` and `-O3` were indistinguishable on every workload; `-O0` was up to 4.7x slower on the amplified kernel. To get out of the sub-millisecond noise I built amplified kernels (the kernel function called 30 times, about 10 ms of real work):

| kernel | -O0 | -O1 | -O2 | -O3 |
|---|---:|---:|---:|---:|
| sum_squares x30 | 47.73 ms | 10.0 ms | 9.9 ms | 9.8 ms |
| horner x30 | 51.15 ms | 26.5 ms | 26.3 ms | 26.3 ms |
| floor_sum x30 | 49.74 ms | 11.5 ms | 11.7 ms | 11.4 ms |

Conclusion: `-O2` is the right level, `-O3` buys nothing measurable, and the LLVM middle end is already doing the work. **Do not spend a release on an `-O` flag change.** The single exception worth knowing about: LLVM's frontend performance guide explicitly says "avoid using arithmetic intrinsics unless you are required to emit a particular code sequence... This item explicitly includes the use of the overflow intrinsics", because LLVM reasons about general control flow and arithmetic better than about the intrinsics. We emit exactly those intrinsics, and it costs us (next section).

### 0.3 The overflow-checked arithmetic is the single biggest codegen cost

The emitted IR routes every integer add and multiply through `ps.iadd` / `ps.imul`, which call `llvm.sadd.with.overflow.i64` and branch to a trap on overflow. I hand-wrote the same program three ways and timed them, interleaved with the attribute variants so drift hits every row equally:

| variant | median | versus emitted |
|---|---:|---:|
| emitted IR (checked add + checked mul) | 9.69 ms | 1.00x |
| checked add, unchecked mul | 8.42 ms | 1.16x |
| plain `add` / `mul`, no checks | 4.24 ms | **2.29x** |

This is the largest single lever I found in the whole compiler, and it is a semantic question, not a codegen one: divergence 1 in `docs/reference/divergences.md` says "`int` is `i64`. Overflow aborts the process instead of growing a big int." The checked helpers are how we honour that. Any change has to keep the abort behaviour for programs that can actually overflow, which is a range-analysis problem, not a peephole problem.

### 0.4 Float paths are not weak because of codegen

The emitted `horner` loop compiles at `-O2` into a fully register-allocated `fmul`/`fadd` pair with no spill and no memory traffic in the loop. The float path being "weakest" in the survey's framing is a misreading: float is 18.1x CPython, not the worst row. The worst rows are all runtime-helper-bound.

### 0.5 The dict and str paths live entirely in the runtime C

I wrote a pure-C version of the `dict-keys` workload (the same `ps_map_new` / `ps_map_put` / `ps_dict_keys` / `ps_seq_get` calls) and compared:

| program | median |
|---|---:|
| emitted `dict-keys` binary | 5.07 ms |
| pure-C equivalent, same runtime calls | 5.17 ms |

The MIR and LLVM layers contribute nothing measurable to that workload; 100 percent of its time is the runtime C. The same is true of `repeat-len`, and this one is worth spelling out because the fix is not where it first looks. The runtime cell for a `str` local stores only the `%ps.buf` prefix (byte length plus data pointer). Every read of such a cell widens it into a `%ps.str` aggregate and calls `ps_str_counts` to refill the code-point count and the ASCII flag, which the cell does not carry. The `repeat-len` loop reads `text` and `piece` once each per iteration, so it does two full byte scans of the growing string per iteration, and the string grows linearly, so the total work is quadratic. I measured it by compiling `repeat-len` to IR, replacing the `ps_str_counts` call with an ASCII stub (the workload is ASCII, so the stub returns the correct answer for it), and linking both variants against the same runtime:

| `repeat_len(n)` | emitted | with the counts scan stubbed | ratio |
|---|---:|---:|---:|
| n = 400 (the corpus argument) | 4.71 ms | 4.34 ms | 1.09x |
| n = 40000 | 704.98 ms | 335.52 ms | **2.10x** |

The corpus row (n = 400) barely moves because the string is only 800 bytes, which is why the baseline shows `repeat-len` at 19.1x CPython and not as a weak row. At a realistic string length the scan is more than half the runtime. A separate microbenchmark puts one `ps_str_counts` over 800 bytes at about 0.27 microseconds (8.68 ms over 20000 iterations against a 3.19 ms empty-loop baseline), roughly 0.34 ns per byte.

### 0.6 Attribute bisect on the emitted IR

I rebuilt the emitted kernel with single attribute changes, each applied to the definitions and all call sites, and measured the variants interleaved (25 rounds, so drift hits every row equally):

| variant | median | versus emitted |
|---|---:|---:|
| emitted | 9.69 ms | 1.00x |
| `fastcc` on the arithmetic helpers | 9.60 ms | 0.99x (noise) |
| `nounwind` on the arithmetic helpers | 9.73 ms | 1.00x (noise) |
| `fastcc` on the user function `sum_squares` | 7.36 ms | **0.76x (24 percent faster)** |
| checked add only, unchecked mul | 8.42 ms | 0.87x |
| plain `add` / `mul`, no checks | 4.24 ms | **0.44x (2.3x faster)** |

`fastcc` on the user function is a real and repeatable 24 percent on this kernel, larger than the session's first estimate (17 percent, which had only rewritten the definition and one call site) once the definition and every call site carry the attribute. `nounwind` on the helpers is noise on its own, and `fastcc` on the small helpers does nothing: the win comes from the user function's own calls, which LLVM then inlines and registers-allocates more aggressively. It changes the calling convention of a symbol in the emitted-module ABI, so it needs the same gate as any ABI change (internal-only functions, every call site rewritten). Neither attribute is where the big money is; the checked arithmetic still doubles the runtime.

## 1. Per-suggestion fact-check

The input file is one LLM conversation about dynamic typing. I split it into the distinct technical claims it makes and check each.

### 1.1 "LLVM IR is strictly statically typed, so every dynamic language compiler maps dynamic types to static LLVM primitives using specific architectural strategies"

Verdict: **correct as stated, and irrelevant to us.** LLVM IR is indeed statically typed; there is no `any` or variant type in LLVM IR (no union type exists in the language either, only identified struct types). But our architecture never has to make that mapping: the typed subset means the type is known before the MIR exists. The survey's whole premise (choose a representation for a dynamically typed value) does not apply to a compiler that refuses to lower dynamic code.

### 1.2 "Tagged pointer: lower 3 bits of an aligned pointer to store a type discriminant"

Verdict: **technically correct, practically a non-starter for us.**

Primary source: glibc's `sysdeps/generic/malloc-alignment.h` defines `MALLOC_ALIGNMENT` as `2 * SIZE_SZ` (16 bytes on x86-64), and `request2size` rounds every chunk to that, so 3 or 4 low bits of a `malloc`ed pointer are free. That is exactly why V8, SpiderMonkey and LuaJIT can tag pointers.

Against us, three independent reasons. First, our `MIR_P` values are `ptr` at the ABI and the runtime C owns the allocations; tagging would mean the runtime must mask and untag on every load, and LLVM's own guide says "Use ptrtoint/inttoptr sparingly (they interfere with pointer aliasing analysis)". Second, LLVM's `ptr` type is opaque and its bit representation is not a frontend's business; the LangRef documents non-integral pointer types as an experimental feature with target-specific semantics. Third, we have no dynamically typed value to tag. Our types are `int` (i64), `float` (double), `bool` (i1), `str` (the `%ps.str` aggregate), and `ptr` for list/dict/class. There is no `Any` in the typed subset.

### 1.3 "NaN-boxing: an f64 where the top 13 bits are all ones leaves 51 bits of payload"

Verdict: **the bit count is wrong, and the technique does not apply to us.**

Primary source: the LuaJIT source and its `lj_obj.h` (as documented) use a 13-bit NaN marker (bits 63..51), a 4-bit type tag (bits 50..47) and a **47-bit** payload (bits 46..0) in GC64 mode. The frequently quoted 51-bit figure is the canonical IEEE-754 double NaN payload space (52 mantissa bits minus one), which is what Lua 5.1 and older designs used. Either way, 47 or 51 bits cannot hold a 64-bit pointer. LuaJIT itself limits its memory allocation to 1 GB for exactly this reason (documented in the LuaJIT NaN-tagging retrospective, issue #924).

Against us: NaN-boxing only pays when you must store heterogeneous values in one machine word. We do not. It also breaks on NaN payloads, and our `float` path is a plain `double` with no boxing.

### 1.4 "Tagged unions / value structs: `%Value = type { i32, [8 x i8] }`"

Verdict: **correct description, correct pros and cons, wrong for us.** The survey's own stated cons are right: "larger memory footprint (12 to 16 bytes per value due to padding), and passing structs by value can generate more stack spill overhead in LLVM." LLVM has no union type; the standard way to build a variant is a struct with a tag plus a payload, or the "niche" trick where a payload field reserves an impossible value.

The niche trick is the interesting part and the survey does not mention it: it is how Rust represents `Option<&T>` as a nullable pointer with no extra tag at all. For `Optional[int]` the niche does not exist (every `i64` is a valid `int`), so a tag is required. Our `None` is currently a distinct compile-time type at the HIR level, not a runtime value, so the question does not arise in the typed subset. If the project ever needs a runtime `T | None`, the niche is the answer for pointer-shaped payloads and a one-bit tag for the rest, and the survey's `{i32, i64}` struct is the worst of the options.

### 1.5 "Monomorphization via type specialization (the Julia approach)"

Verdict: **correct description of Julia, and already what we do, but the framing is wrong.**

Primary source: Julia's codegen specializes on concrete argument types at JIT time, with `jl_apply_generic` as the generic fallback when inference fails. Our compiler is the opposite shape: it specializes at *compile* time from annotations and refuses what it cannot type. The survey's suggestion of "inspects function arguments at runtime" is the JIT part, which we do not want.

What the survey misses: Julia's key trick is **union splitting** (a union-typed variable is compiled into separate SSA values per concrete type, with a type check at the union points, not a boxed `jl_value_t*` per value). That is the technique that would be relevant if we ever admitted a union type, and it is not the same as a tagged union value. The survey's "boxed `jl_value_t*` fallback" description is a simplification; Julia's real fallback is per-argument specialization with a generic dispatch entry point.

### 1.6 "The MIR should have explicit `CheckTypeTag` and `Unbox` / `NarrowCast` instructions"

Verdict: **the design is sound in the abstract, and it is the wrong shape for our architecture.**

The proposed MIR shape (a `CheckTypeTag` producing a bool that branches into a narrowed block, then `Unbox` inside it) is exactly what a compiler that admits `Any` needs, and it is a good design. We do not have `Any` in the typed subset: `mirlower.supported_expr` rejects any expression whose type kind is not statically known, and the fence list (the `PS2002` family, "a statement, expression, import, or definition has no compiled form") is where dynamic code currently stops. Introducing a `CheckTypeTag` op family now would mean adding a value representation to MIR that nothing produces, breaking the byte-identity contract for every existing function, and buying nothing: the typed code that does lower is already unboxed.

Where the idea does pay, later and in a different place: the **island tier**. `--fallback=cpython` routes a function to the embedded interpreter. If we ever want to *unfence* a fence by compiling the common case natively and falling back per call, that is a `CheckTypeTag` shape, but the tag lives in the CPython object header, so it is a runtime call, not a MIR tag check. That belongs in `boxed.py` and `runtime/ps_island.c`, not in `mir.py`.

### 1.7 "Flow typing and narrowing: `isinstance(x, T)` as a Type Guard, narrowing on the true branch"

Verdict: **correct and standard, and we already have the machinery in a different form.** TypeScript's CFA does exactly this. Our type facts (`src/pyscriptc/typefacts.py`, `factsbridge.py`, `inferfacts.py`) carry flow-sensitive facts through the HIR, and `mirlower` uses them. The survey's "Exclude[object, T]" on the false branch is the part that is over-engineered for an open type; for a closed union it is exactly the right formulation and is what a union type would need.

### 1.8 "Optional / Nullable types: `T | None` as a pointer where `None = null`"

Verdict: **partly correct, and the caveat is important.** The null-pointer niche works for pointer-shaped payloads (Rust's `Option<&T>` is a nullable pointer). It does not work for `Optional[int]`, because `0` is a perfectly valid `int` and there is no spare bit pattern. The survey presents this as if it were universal.

For us: `None` is a compile-time type in the HIR, so `x: int | None` is currently a fence or a compile error rather than a runtime union. When the typing surface lands (`v0.11.0` per the milestone description: "unions/generics/forward references erased at lower time"), the representation question becomes real, and the answer is: null-pointer niche for `ptr`-shaped payloads, a one-bit tag or a dedicated `MIR_OPT` type for scalars, and a tag for `str` (which is a fat aggregate with no spare bit).

### 1.9 "Variable arguments: desugared into a stack-allocated or heap-allocated tuple/dict wrapper before function entry"

Verdict: **correct as a technique, already done by us.** `mirlower` lowers `*args` / `**kwargs` through the runtime helper channel; see `.agents/notes/decisions-and-platform.md` (keyword argument lowering). Nothing to do.

### 1.10 "Dynamic attribute access: `GetAttrDynamic(%x, "foo")`, split into a fast-path tag check and a slow-path C call if inline caching is added later"

Verdict: **correct as a description of the current state, and inline caching is the wrong tool for us.** Our `MIR_GETATTR` reads a field at a fixed offset in the `%cls.<name>` struct, because a class's fields are statically known. There is no dynamic attribute lookup to cache. The survey's later "hidden classes / shape trees" section is the same idea in more detail, and it is likewise aimed at languages where objects get attributes dynamically.

### 1.11 "Heterogeneous lists modelled as `Array<Any>`"

Verdict: **correct as a description of the dynamic case, and it is precisely what we refuse.** `mirlower` requires the element type of a `list[T]` to be known and supported (the M-2 note, `.agents/notes/2026-11-06-mir-list-dict-value-types.md`). A heterogeneous list is a fence.

### 1.12 "Profile-guided type inference from the test suite (MonkeyType, PyAnnotate) via `sys.monitoring` (PEP 669)"

Verdict: **the tool descriptions are broadly accurate, and the soundness caveat the survey itself raises is the whole story.** Primary sources: PEP 669 is the `sys.monitoring` API (the survey's "do not use `sys.settrace`, which adds 10-20x overhead" claim about relative overhead is a widely repeated figure that I could **not** verify to a primary source: mark it UNVERIFIED). MonkeyType and PyAnnotate are real tools that harvest runtime types from test runs into annotations.

Against us: this is a *type facts* strategy, not an optimization strategy, and we already have a type-facts subsystem (`typefacts.py`) with a different, sounder source: the annotations themselves. Test-harvested types are witnessed, not allowed, which the survey says itself ("Test-harvested types must never be assumed to be mathematically exhaustive unless the developer explicitly locks them down"). Introducing them into the *lowering* path would break the parity contract in a way the current fence model does not: today an unsupported construct is a loud `PSxxxx` diagnostic, and with test-harvested types it would become a silently wrong binary for any input the tests did not cover.

The one place it is genuinely valuable, and where it is already planned, is the **unannotated-code** vertical (V5.2 in the post-flip plan, issues #731 and #784). There the harvest is a *hint* for the type-facts engine, not a lowering input, and every hint is still checked against the runtime value. That is the sound design.

The survey's second point about branch reordering by profile weight is a real technique (LLVM's `!prof` branch weights and the "cold" calling convention do this at the LLVM level), but it is an AOT profile question, not a type question, and it is only useful with real profile data.

### 1.13 "Union domain bounding: if `value: object` only ever takes `str | int | float` in tests, treat it as a closed 3-variant union"

Verdict: **correct technique, unsound source, wrong tool.** Same objection as 1.12: witnessed types are not allowed types. The sound version of this idea is *static* analysis over the HIR (which we have) or an explicit user annotation.

### 1.14 "Property-based testing (Hypothesis) or an in-process fuzzer (Atheris) hooked into the type harvester"

Verdict: **correct about the tools, and the adversarial instinct is right even though the target is wrong.** Hypothesis is a property-based testing library; Atheris is a libFuzzer-based Python fuzzer. The survey's argument ("it immediately surfaces latent polymorphism that tests mask") is correct as an argument about *type discovery*.

Against us: this is a **test-quality** argument, not a compiler-optimization argument, and it belongs to the test strategy, not the lowering. But it is a strong argument for the *verifier* (see section 2.7): fuzzing the MIR verifier and the MIR passes with Hypothesis is cheap and valuable, and it is not done today.

### 1.15 "Abstract interpretation over a CFG lattice with three domains: shape/layout, container element invariant, nullability and value-range intervals"

Verdict: **the technique is real and well documented, and the value-range domain is the one we want.** Primary source: Frama-C's Value and Shape Analysis (the `Wp` weak-pointer analysis for dynamic memory, and the value domain with integer intervals) is the canonical industrial implementation of exactly this shape. Cranelift's alias analysis (below) is the same idea at a mid-level IR.

The survey's payoff claim ("your MIR can skip tag checks, omit overflow branches, and emit raw LLVM vector instructions without speculative guards") is correct in principle and **directly relevant to our biggest measured cost**, the overflow checks in section 0.3. A range analysis over the MIR would let us prove that a loop counter stays in `[0, n)` and that the accumulated value cannot overflow, and drop the checked helpers. That is the highest-value transfer from the survey's section 2 into our compiler.

The shape domain (guaranteed attribute sets) does not apply: our class fields are statically known.

### 1.16 "Hidden classes / shape trees for `__dict__` lowering, adapted from V8/Self"

Verdict: **correct description of V8, wrong target.** Primary source: V8's "Fast properties" blog and `docs/heap/objects-and-maps.md` document the map (hidden class) with a transition tree, in-object versus out-of-object properties, and the fast/slow (dictionary) property modes. The survey's diagram of Shape 0 -> Shape 1 -> Shape 2 is an accurate simplification.

Against us: Python classes in the typed subset have declared fields, lowered to a fixed `%cls.<name>` struct with `MIR_GETATTR` / `MIR_SETATTR` at static offsets. There is no `__dict__` to lower. V8's shape machinery exists because JavaScript objects grow attributes at runtime; ours do not. Adding it would be adding a dynamic-object model we have deliberately refused.

### 1.17 "Aggressive escape analysis and SROA for dynamic objects: no escape -> stack or SSA registers; partial escape -> sink allocation on cold error paths"

Verdict: **the technique is real and correct; the target exists but is not where the survey thinks.**

LLVM already does SROA and escape analysis for us where it can: the LangRef's own introduction says "through pointer analysis, it can be proven that a C automatic variable is never accessed outside of the current function, allowing it to be promoted to a simple SSA value instead of a memory location". The blocker is that our emitter *deliberately* materializes every MIR value as an `alloca` plus `store` / `load` pairs, to reproduce the direct path's shape byte for byte (`miremit.py`'s module docstring says so explicitly). The MIR itself is SSA; the emitter un-SSA's it.

That is the interesting finding here: **our biggest self-inflicted codegen cost is the byte-identity contract itself, not the representation.** The emitted `sum_squares` has five `alloca`s for three live integers. LLVM's SROA cleans that up at `-O2` (the disassembly shows all three in registers), so the alloca cost is already paid for. The value of escape analysis on our side would be small.

But the same idea *does* apply to the runtime C: `ps_seq_new` and `ps_map_new` heap-allocate, and a `list` that never escapes its function could live on the stack. That is a real win in the runtime, not the compiler, and it interacts with the `ps_owned_push` registry in `ps_rt.c` (which deliberately keeps every sequence and map reachable so LeakSanitizer stays quiet; the comment at `ps_rt.c` line 749 explains this).

### 1.18 "Fat pointers / interface vtables for duck typing"

Verdict: **correct technique, wrong target, and the survey's description is garbled.** Primary source: Go's `iface` is two words, an `*itab` (the interface type, the concrete type, a hash, and a sorted method-address table) plus a data pointer; the method index is static and the address is dynamic. Rust's trait objects are the same shape (a vtable pointer plus data). The survey's "VTableVTableVTable" is a formatting artifact of the source text, not a concept.

Against us: duck typing is explicitly out of scope. The typed subset requires a declared type; `Protocol` and `ABC` are listed as divergence 14 ("compile as plain classes and methods with no CPython runtime semantics"). We already have the vtable-ish thing for classes: `MIR_INITCALL` and the direct calls to `cls.<name>.__init__` are static.

### 1.19 "Speculative guards with deoptimization bailouts (the JIT trick in AOT): compile fast paths under an invariant guard, and on violation materialise the SSA state into a CPython interpreter frame"

Verdict: **the technique is real and named correctly, but the survey's mechanism is not implementable as described.** WebKit's FTL and Java's HotSpot do deoptimize, but they do it from *compiled* code into a *running* interpreter, and the materialisation machinery is the JIT's own frame layout. An AOT compiler that bails into CPython would have to reconstruct a CPython frame from LLVM IR at run time, which means shipping a frame builder in the runtime C. That is a large project with a large surface, and the survey gives no mechanism for it.

There is a much cheaper version of the same idea that fits our island tier: compile the common case natively and call the embedded interpreter for the rest, guarded by a cheap runtime check. That is exactly what `--fallback=cpython` already does at *function* granularity (`boxed.py`), so the design exists; making it finer-grained is a v0.10.0+ idea, not a v0.9.0 one.

### 1.20 "The recommended reference repositories: JuliaLang/julia, 47Cid/lox-llvm, willemt/lox-llvm"

Verdict: **the Julia pointer is sound. The Lox pointers are low value.** The Lox projects are teaching implementations of a small dynamically typed language; they demonstrate the object model, not a production optimization strategy. For a typed-subset compiler the useful references are different: Cranelift (a mid-level IR with a real alias analysis), Swift SIL (a mid-level IR with mandatory optimization passes), Wasm GC (a typed reference model with runtime type information and cast refinement), and CPython's own specializing adaptive interpreter (the closest thing to a "typed subset" compiler in the Python ecosystem).

### 1.21 Summary verdict on the survey

Of the nineteen technical claims, **two are outright wrong** (the 51-bit NaN payload figure; `Optional[int]` as a null pointer), **three are correct but target a compiler we are not building** (tagged pointers, NaN-boxing, hidden classes), **four are things we already do** (monomorphization by compile-time specialization, kwargs desugaring, flow-sensitive type narrowing, explicit type facts), **three are correct techniques aimed at the wrong layer** (tagged unions for a runtime `Any`, fat pointers for duck typing, deopt bailouts into CPython frames), and **five are genuinely worth having** (value-range abstract interpretation, escape analysis in the runtime, property-based fuzzing, profile-guided branch weighting, and the general "make the type facts sound" discipline).

The one idea with the largest measured payoff for us is buried in the survey's abstract-interpretation section, and it happens to line up exactly with our biggest measured cost.

## 2. Beyond the input: what our measurements say to do

### 2.1 MIR passes (owner: a new `miropt` package; the op-spec single-source work in the flip plan Stage 1 is the prerequisite)

Today `mir.py` is shape plus op spec plus serializer plus hash. There is no pass infrastructure: no IR-to-IR transform runs between `mirlower` and `miremit`. Every pass below is a new file plus one registry line, following the family-module split design in `.agents/plans/2026-11-06-v090-user-centered-flip.md` section 4 Stage B.

| pass | what it does | expected gain | cost | risk | stage owner |
|---|---|---|---|---|---|
| Const-fold on `MIR_ADDW`/`SUBW`/`MULW`/`ADDD`/`SUBD`/`MULD` with constant operands | folds `x + 0`, `x * 1`, `x - 0` and constant-on-constant | near zero on the current corpus (no constant folding opportunities in typed Python loops) but cheap and it feeds later passes | S | low | MIR pass |
| DCE of `MIR_COPY` into an unused slot | removes dead name materialisation | small; LLVM already does this | S | low | MIR pass |
| Copy-prop / CSE on pure MIR values | `MIR_COPY` of a pure value op folds to the source | small; LLVM does it after we lower | S | medium (the emitter's slot-shaped contract makes value identity observable) | MIR pass |
| GVN across blocks | redundant arithmetic hoisted | small at the MIR level; LLVM's GVN runs late on our LLVM IR | M | medium | MIR pass |
| **Range analysis + overflow-check elision** | proves a value cannot overflow and drops the checked helper in favour of plain `add` | **measured 2.3x on an int kernel** | L | **high** (semantics: divergence 1) | MIR pass + emitter |
| Loop-invariant hoisting of `ps_seq_len` / `ps_map_len` | hoists a length load out of a loop | medium on container loops | M | medium (the runtime call may not be pure) | MIR pass + emitter |
| Strength reduction (`x // 2` to `x >> 1` for non-negative `x`) | replaces a runtime division call with a shift | medium on integer loops | M | high (Python floor division is not truncation; the shift is only valid for non-negative operands) | MIR pass + emitter |
| Inlining of small functions at the MIR level | avoids the call overhead and lets LLVM specialize | medium; note LLVM already inlines at `-O2` and our `fastcc` experiment showed 24 percent for one function | M | medium (Python call semantics, recursion, keyword arguments) | MIR pass |
| Devirtualization for closed class hierarchies | replaces a virtual method call with a direct call when the receiver's class is statically known | small today (our method calls are already mostly direct), large if we add protocol support | M | medium | MIR pass |
| Bounds-check elimination for `list[i]` | drops the `ps_index` range check when the index is provably in range | medium on list-heavy loops | M | medium | MIR pass |
| SROA-like for small records | explodes a small struct into SSA values | small; LLVM does this on the `alloca`s we already produce | M | medium | MIR pass |

The right order is range analysis first (it has the measured payoff), then the cheap dead-code passes, then everything else only if a receipt justifies it.

**A caution about all of these.** The MIR lane currently exists to produce byte-identical LLVM IR to the direct path (`miremit.py`'s docstring; `tests/test_mir_differential.py` pins byte identity per `define`). Any MIR pass that changes the emitted IR breaks that gate for the functions it touches. The flip plan's Stage 5 says the whole-module equality gate retires at v0.9.0 and per-function identity is the cheap guard until then, but as of this writing the per-function byte-identity test is still in `tests/test_mir_leaf.py` and `tests/test_mir_differential.py`. So **every MIR pass in section 2.1 must be gated**: land it behind an env flag (like `MIR_ENV` itself), keep the byte-identity lane green by default, and flip the default only with a measured receipt and a runtime A/B run. That is the safest shape for a v0.9.0 that just landed.

### 2.2 Type-guided unboxing and representation selection

Verdict for our architecture: **mostly already done, with two gaps.**

What we have: `int` is `i64` unboxed, `float` is `double` unboxed, `bool` is `i1`, `str` is the `%ps.str` aggregate `{i64 nbytes, ptr data, i64 nchars, i32 flags}` carried by value, `list`/`dict`/class are `ptr`.

Gaps worth naming:

1. **`str` is a 24-byte aggregate carried by value, and the runtime cell does not carry the derived fields.** Every `str` local is an `alloca` plus `store`/`load` of 24 bytes, and the runtime cell stores only the `%ps.buf` prefix, so every read of a `str` slot re-runs `ps_str_counts` to refill the count and flags fields (section 0.5). Three fixes, in increasing order of work: (a) carry the count and the ASCII flag in the runtime cell (the cell grows from 16 to 32 bytes) so the read does not need to rescan; (b) keep the count and flags in the `%ps.str` aggregate all the way through the loop, which needs the aggregate to be stored where the cell is today; (c) carry `str` as a pointer to an immutable header instead of by value, which changes the ABI and is a bigger job. Option (a) is a runtime-C plus ABI change with a measurable receipt (2.10x on the amplified `repeat-len`).

2. **No interning of string literals.** Every literal site materialises its own buffer. Interning identical literals would save memory and time in string-heavy programs, but it changes pointer identity semantics: CPython interns `str` literals, so `a is b` for two identical literals is `True` in CPython. Our divergence 13 says "`x is None` compiles as `==` against `None`", and other `is` on scalars is refused. Interning would move us *toward* CPython, not away, but it is an observable change and needs its own divergence entry and probe.

3. **No `Optional` representation.** Deferred to the typing surface (v0.11.0). When it lands, the niche rule above applies.

4. **Unboxed int inside containers.** A `list[int]` stores 8-byte cells through the `ps_seq_get` / `ps_seq_set` runtime ABI, so each element read is a `memcpy` through an alloca and each write is a `memcpy`. A typed container layout (element size and type known statically) would let the emitter emit direct `load`/`store` of `i64` at `base + index * 8` instead of a runtime call. This is a genuine win on list-heavy code and it does **not** need a new value representation: the element type is already statically known in the typed subset. This is the highest-value item in this subsection.

### 2.3 Refcount elision, borrow inference, escape analysis

Verdict: **not applicable to our value model, and the survey does not know that.**

We have no reference counting. The runtime C never frees a sequence or map (`ps_rt.c` line 749: "Nothing is freed here on purpose: a free would orphan the heap cells a sequence's slots point at... and could race a detached thread still reading the object"). So there is no refcount to elide and no borrow checker to infer. The island tier does have CPython reference counts, but that is CPython's runtime, not ours.

The related real win is **allocation avoidance**: a `list` that never escapes its function could be a stack buffer. That is a runtime C change (a `ps_seq_new_stack` variant with a caller-provided buffer), gated by an escape analysis over the MIR, and it interacts with the `ps_owned_push` registry that keeps LSan quiet. It is real but it is a v0.10.0+ project.

### 2.4 LLVM-side levers

| lever | verdict | gain | cost | where |
|---|---|---|---|---|
| `-O` level | already `-O2`; `-O3` measured as no different, `-O1` no better | none | S | `emit/llvm/_driver.py` |
| `nounwind` on the arithmetic helpers | sound (the helpers only branch to a `noreturn` trap) | small (about 1 percent measured, within noise) | S | `miremit.py` helper emission |
| `fastcc` on small hot user functions | sound if the function is internal to the module and every call site carries the attribute; 24 percent measured on one kernel (definition plus all call sites) | medium, narrow | S | `miremit.py` |
| `!nonnull` / `!dereferenceable` on runtime helper pointer args | sound only where the runtime guarantees it; `ps_map_get`'s `out` pointer is dereferenceable, the map pointer is not null by contract | small, mostly enables DSE | S | `miremit.py` |
| `!range` metadata on integer loads | LangRef: "may be attached only to load, call and invoke of integer or vector of integer types"; a value outside the range is poison. Would let LLVM drop masks. Needs a range analysis first (2.1). Note the metadata is poison, not undefined behaviour, on violation, so a wrong range is a silent miscompile | small until range analysis exists | M | `miremit.py` |
| `!tbaa` | LangRef and the TBAA source document the struct-path form. Would let LLVM disambiguate the runtime cells from the string payloads. Our accesses are already through distinct typed pointers, so the win is small | small | M | `miremit.py` |
| `noalias` / `alias.scope` on runtime cell pointers | the runtime cell ABI passes a fresh alloca per call, so `noalias` on it is sound and would let LLVM forward stores | small | S | `miremit.py` |
| Fast-math flags | **must not be applied.** Our float semantics are IEEE-754; `nan-in` and `float-inf` are corpus workloads and CPython distinguishes `nan != nan`, inf arithmetic and signed zero. Fast-math would break parity | none (we must not) | none | never |
| LTO | the whole program is one module plus the runtime C, compiled separately. `-flto` would need the runtime objects as bitcode; the gain would be cross-module inlining of the runtime helpers into user code. Worth a measurement, not an assumption | unknown, likely small to medium | M | `emit/llvm/_driver.py` |
| PGO | instrumentation PGO needs a two-pass build; `sample-profile` needs a profile. Feasible for a project that compiles the same program repeatedly, not for a general user | unknown | L | new tooling |
| BOLT | post-link optimizer, needs a profile, Linux-only in practice | unknown | L | not now |
| Vectorization | the LLVM vectorizers are on by default. Our int loops are already vectorizable in principle, but the checked helpers call `llvm.sadd.with.overflow` which is not vectorizable, and the loop is a `while` with a `br`, not a canonical `for`. Nothing in the survey mentions that vectorization needs a *canonical loop shape*; that is the real blocker, not the vectorizer | medium on int loops, if the loop shape is canonicalised | L | MIR + emitter |
| Allocator choice | glibc `malloc` is already 16-byte aligned with tcache. A bump allocator would help programs that allocate many short-lived objects, but our runtime deliberately never frees, so the allocator's reuse behaviour is irrelevant. **Verdict: not worth it.** | none | none | never |

### 2.5 Runtime wins (owner: `src/pyscriptc/runtime/`)

Ordered by measured or clearly-derivable payoff.

1. **Typed container element access.** As in 2.2 item 4: emit direct typed loads and stores for `list[int]`, `list[float]`, `dict[int, int]` instead of `ps_seq_get` / `ps_seq_set` / `ps_map_get` through a runtime cell. Measured context: the dict workload is 100 percent runtime C (section 0.5), so this is where the time is. Owner: `miremit.py` plus `ps_rt.h` plus the `MIR_SEQGET` / `MIR_MAP_GET` op semantics.
2. **Stop recomputing `ps_str_counts` on every `str` slot read.** Measured: 0.27 us per call over 800 bytes; the emitted loop calls it twice per iteration over a string that grows, so the loop is quadratic. The `%ps.str` aggregate carries `nchars` and `flags`, but the runtime cell does not, so the values are lost on every write and rescanned on every read. Owner: `miremit.py` `_widen_str_cell` plus the direct path's `_widen_cell` plus `ps_rt.h`.
3. **The `str` runtime cell should carry the derived fields.** The cell is a `%ps.buf` (byte length plus data pointer), so the count and the ASCII flag are lost on every write and recomputed by a full byte scan on every read. Growing the cell to carry them removes the scan entirely. This is the concrete form of item 2 above and the reason `repeat-len` is quadratic. Owner: `ps_rt.h`, `ps_rt.c`, `miremit.py` `_widen_str_cell`, and the direct path's `_widen_cell` in `emit/llvm/_core.py`. Note that both lanes must move together: the MIR lane's `out.ll` is byte-identical to the direct lane's today, and a change to one side alone breaks that gate.
4. **Small-int cache.** CPython caches small ints (`_PyLong_SMALL_INTS`, documented in CPython's `longobject.c`; the range is CPython-version-specific and I could **not** verify the exact 3.14 range from a primary source in this session: mark the range UNVERIFIED, but the mechanism is confirmed). We have no `PyLong` at all: our `int` is an unboxed `i64`, so there is nothing to cache. **Verdict: not applicable.** The equivalent for us would be caching *boxed* values, and we do not box ints.
5. **Arena / bump allocation.** Our runtime never frees, so `malloc` per object is pure overhead: a bump allocator over a few large `mmap`ed blocks would cut allocation cost and RSS. But the `ps_owned_push` registry exists to keep LSan quiet, and a bump allocator changes the leak story. Medium payoff, medium risk. Owner: `ps_rt.c`.
6. **Dict and str hot paths.** `ps_map_get` already does open addressing with a hash table and a `slot_mask`; `ps_dict_keys` materialises a sequence. The measured dict workload is already close to the cost of its runtime calls, so the win is in the runtime ABI (item 1), not in the hash table.

### 2.6 Compile-speed levers (owner: V6 vertical; the compile-time receipt already ranks these)

The compile-time receipt already ranked five cheap wins (typeshed cache about 160 ms, cached `cc --version` about 30 to 130 ms, overlapping `check` with `emit_llvm` about 140 ms, writing `ir.json` only when asked, and auditing the about 170 ms CLI startup). Two more belong on that list from this research:

6. **Cache the MIR module hash and skip re-lowering.** `mir.py` already computes a SHA-256 of the serialized module (`module_hash`). A content-addressed cache keyed on the source digest plus the compiler version would skip `lower` and `emit_llvm` for unchanged inputs. Measured potential is small (MIR phases are under 5 ms of a 792 ms compile) **unless** the module is large, where the JSON serialization of the IR does cost real time (the receipt's item 4).
7. **Skip the clang step for pure-MIR functions?** No: the .ll must be assembled. But `cc_compile` at 75 ms could drop by emitting object code through a persistent clang process or by using `llc` directly on a bitcode module. The LLVM IR we emit is simple enough that `llc` would work; the risk is the zig driver path.

Parallel codegen: `rtcache.run_jobs(jobs)` already compiles the 43 runtime units in parallel. The remaining serial part is the user module.

### 2.7 Adversarial and verification techniques

This is the area where the survey is weakest and where we have the least coverage today.

**Translation validation of MIR passes.** Alive2 (the LLVM IR refinement checker, `alive-tv`; it "does not support inter-procedural transformations" and produces counterexamples rather than proofs) validates LLVM IR transforms, not MIR transforms. For MIR we need our own checker, and the right shape is a **differential** one, which we already have: `scripts/runtime_ab.py` compiles each program through both lanes and diffs stdout, stderr and the exit code; `scripts/strict_corpus_ab.py` does the same against CPython with untrimmed bytes. The rule for landing a MIR pass should be: (a) the pass is behind a flag; (b) `runtime_ab.py` reports 0 FAIL and 0 ERROR over the MIR leaf fixtures plus the visible corpus with the flag on; (c) `strict_corpus_ab.py` reports no new difference; (d) the MIR hash gate (`mir.py`'s serializer hash) is regenerated and pinned. That is a translation-validation substitute that fits the existing tooling, and it is the receipt every MIR pass should carry.

**Alive2-style checking for peephole rules.** Alive2 works on LLVM IR, so it cannot check a MIR peephole directly. But it *can* check the LLVM-level rewrites we would implement as LLVM metadata or attributes. Concretely: if we add `!range` metadata, the corresponding LLVM peephole (mask removal) is LLVM's, and Alive2 is the right oracle for "is this range metadata sound", via the `alive-tv` web tool at `alive2.llvm.org/ce`. Note that Alive2 is a research-grade tool and building it needs an LLVM with RTTI and exceptions; a first use would be a manual spot-check, not CI.

**Fuzzing the verifier (MVS) and the passes with Hypothesis.** `tests/test_mirverify_negative.py` pins one case per reachable verifier rule (MVS01 type, MVS02 use-def, MVS03 control). That is a hand-written negative suite, which means it only covers the rules someone thought of. A Hypothesis property test would generate random `MirFunction` shapes and assert that `verify_function` never crashes (it must return violations or raise `MirError`, never an uncaught exception), and that every *accepted* function survives the emitter. That is a cheap, high-value addition and it needs no new infrastructure beyond `hypothesis`, which is not currently a dependency. This is the single best-value item in this subsection: it is the "fuzz the verifier" idea from the survey's adversarial section, aimed at the surface we actually own.

**Metamorphic testing (opt vs no-opt outputs must match).** This is exactly the `runtime_ab.py` shape, and it is how every MIR pass must be gated. Extend it: the pass-on and pass-off binaries must agree byte for byte on stdout, stderr and exit code, over the MIR leaf fixtures and the visible corpus. The harness exists; the work is wiring the flag.

**CSmith / YARPGen-style generators for typed Python.** These generators exist for C and Rust, not Python. The Python equivalents in our own toolkit are `benchmarks/compat2/generators/` and the corpus generators. A typed-Python generator that emits a random typed module (random annotated functions, random int/float/str/list/dict mixes, random loop nesting, random class definitions) and then runs the same program through the direct lane, the MIR lane and CPython, comparing all three, is the natural extension of `scripts/runtime_ab.py` and `scripts/strict_corpus_ab.py`. The corpus scorer already exists and is frozen; a *new* generator does not touch it.

**Sanitizer builds of emitted code.** The CLI already has `--sanitize LIST`. The runtime C comment at `ps_rt.c` line 749 shows the project already reasons about LeakSanitizer being silent. ASan and UBSan on the emitted binaries, over the visible corpus, is a cheap way to find emitter bugs (wrong alloca size, wrong alignment, an out-of-bounds cell write). MSan is less useful because our runtime is not memory-initialized by default, but `ps_seq_new` uses `calloc`, which MSan would treat as uninitialized.

**Mutation testing of the verifier.** A small mutation-testing harness over `mirverify.py` (flip a comparison, drop a rule, change an op id) and assert that the negative suite catches each mutant. This is the standard way to prove a negative suite is not vacuous. It is a few dozen lines of script.

**Adversarial inputs for the type facts.** Three concrete families:
- **Wrong annotations.** The user writes `x: int = "hello"`. The compiler must reject it, not silently produce a wrong binary. The type checker (`check.py`, `typefacts.py`) owns this, and the pyrefly preflight at 300 ms per compile is what catches it today. The adversarial test is a corpus of deliberately wrong annotations that must all be rejected with a diagnostic.
- **Aliasing through `+=`.** `a = [1,2]; b = a; b += [3]` must mutate `a` in place (Python list semantics), while `b = a + [3]` must not. Our `MIR_COPY` of a `MIR_P` value copies the pointer, so the aliasing is right by construction; the adversarial test is a probe that asserts the mutation is visible through the other name.
- **Integer overflow semantics versus CPython bigints.** This is the big one and it is a **known divergence** (divergence 1: `int` is `i64`, overflow aborts the process). CPython would grow a big int; we abort. Any range-analysis work in 2.1 that removes an overflow check must preserve the abort for the programs that can overflow, or it must move the divergence. The adversarial test set is: programs whose values provably stay in range (fast path), programs that provably overflow (must abort exactly as now), and programs where the analysis is uncertain (must keep the check). The third category is where a wrong range analysis silently changes behaviour, and it is the reason the range analysis must be conservative by default.

## 3. Ranked shortlist

Owner-sized slices: S is under an hour, M is a session, L is several sessions.

### 3.1 v0.9.0 (must be safe, small, byte-identical or runtime_ab-gated)

The flip just landed, so nothing risky goes here. These are all measurement, documentation or runtime-only changes.

**Rank 1. Carry the derived `str` fields in the runtime cell (runtime ABI plus both lanes, S to M).**
Files: `src/pyscriptc/runtime/ps_rt.h`, `src/pyscriptc/runtime/ps_rt.c`, `src/pyscriptc/miremit.py` (`_widen_str_cell`), `src/pyscriptc/emit/llvm/_core.py` (`_widen_cell`), `src/pyscriptc/emit/llvm/_runtime.py` (`ps.strconcat`).
Why first: it is the largest measured win that touches no language semantics. The `str` cell stores only the byte length and the pointer, so every read rescans the bytes to recover the code-point count and the ASCII flag. Measured 2.10x on the amplified `repeat-len` (n = 40000), 1.09x at the corpus argument. The cost is that it widens the cell ABI, which both lanes must change together because the MIR lane's output is byte-identical to the direct lane's.
Receipt: `benchmarks/baseline.run --check` on the `repeat-len`, `script-slice` and `fstring-const` rows; `scripts/runtime_ab.py` for the byte-level guarantee; and the non-ASCII probes (`ad12_str_utf8`, the `unicode-category` workload) to prove the count is still code points and not bytes.
Risk: medium. The str model is mid-migration (divergence 3), so this touches a moving target; the cell ABI is shared with the direct lane, so the two must land in one PR. The failure mode to guard is a header whose count was computed as bytes instead of code points, which `repeat-len` (pure ASCII) would not catch.

**Rank 2. Measure the overflow-check elision opportunity and write it down (docs plus a spike, S).**
Files: `.agents/notes/<date>-overflow-check-elision.md` plus a spike outside `src/`.
Why: this is the 2.3x measured win, and it is a semantics decision, so it must not land in v0.9.0. The note should state the range-analysis design, the divergence-1 interaction, and the three-category adversarial test set from section 2.7.
Receipt: the note itself, plus the spike's timing table.

**Rank 3. Add a `nounwind` (and optionally `fastcc`) attribute to the emitted arithmetic helpers and internal user functions (S).**
Files: `src/pyscriptc/miremit.py`.
Why: cheap, sound, and the user-function `fastcc` measured 24 percent on an int kernel; `nounwind` is sound because the helpers only branch to a `noreturn` trap.
Receipt: `benchmarks/baseline.run --check`, `scripts/runtime_ab.py`.
Risk: low, but the byte-identity lane will move, so it must land with the per-function identity flag already relaxed or in the same PR as that relaxation. `fastcc` additionally requires the definition and every call site to be rewritten together, and only applies to functions not exported from the module.

**Rank 4. Add a Hypothesis fuzz test for the MIR verifier and the MIR passes (M).**
Files: `tests/test_mirverify_fuzz.py`, `pyproject.toml` (test dependency).
Why: it is the highest-value adversarial technique in this note and it covers the surface we own. It never touches the frozen gates.
Receipt: the test running clean over a fixed seed plus a note recording the seed.

**Rank 5. Document the `--emit mir` surface and the MVS codes for users (S).**
Files: `docs/reference/cli.md` (regenerated), a new docs page.
Why: V6.4 of the post-flip plan asks for it, and it is the surface a user needs in order to use the MIR lane at all.

**Rank 6. Write down the LLVM-side negative results so nobody repeats them (S).**
Files: `.agents/notes/<date>-llvm-lever-negative-results.md`.
Contents: the `-O` sweep table, the `fastcc` / `nounwind` bisect, the "dict is 100 percent runtime C" measurement, the glibc alignment fact, the LuaJIT 47-bit correction.
Why: this note is the evidence, but a short note in the notes directory is what a future session will actually find.

### 3.2 v0.10.0

**Rank 7. Typed container element access (L).**
Files: `src/pyscriptc/mir.py` (op semantics or a new op family), `src/pyscriptc/mirlower.py`, `src/pyscriptc/miremit.py`, `src/pyscriptc/runtime/ps_rt.h`, `src/pyscriptc/runtime/ps_rt.c`.
What: emit direct typed loads and stores for `list[int]` and `dict[int, int]` instead of the runtime-cell round trip.
Why: the measured dict workload is entirely runtime C; this is where its time is.
Receipt: `benchmarks/baseline.run --check` on `dict-keys`, `list-comp-if`, `bool-list`, `script-slice`; `scripts/runtime_ab.py`; the MIR/direct ratio from `scripts/mir_perf_ratio.py`.
Risk: medium (the container ABI is shared with the direct lane).

**Rank 8. Range analysis and overflow-check elision, behind a flag (L).**
Files: `src/pyscriptc/mir.py` (a new op or a helper spelling), `src/pyscriptc/miropt/` (new package), `src/pyscriptc/mirlower.py`, `src/pyscriptc/miremit.py`.
What: prove a value cannot overflow and drop the checked helper for plain `add` / `mul`.
Why: measured 2.3x on an int kernel. This is the biggest codegen lever in the compiler.
Receipt: `scripts/runtime_ab.py` with the flag on (0 FAIL, 0 ERROR), `scripts/strict_corpus_ab.py`, `benchmarks/baseline.run --check` on the kernel group, and the MIR hash gate regenerated.
Risk: **high**. The divergence-1 interaction must be handled explicitly: the analysis must be conservative by default, and the adversarial test set (in-range, provably-overflowing, uncertain) must be a gate.

**Rank 9. Mutation testing of the verifier (S).**
Files: `scripts/mirverify_mutants.py` (new), `tests/test_mirverify_mutants.py`.
Receipt: the harness reports 100 percent of the seeded mutants caught.

**Rank 10. A typed-Python generator for differential testing (M).**
Files: `benchmarks/compat2/generators/` (a new generator, leaving the frozen files alone) plus `scripts/runtime_ab.py` (a new corpus source).
Receipt: the generator's programs pass the three-way comparison (direct lane, MIR lane, CPython).

**Rank 11. Strength reduction for integer division by a power of two (M).**
Files: `src/pyscriptc/miropt/` (new), `src/pyscriptc/mirlower.py`, `src/pyscriptc/miremit.py`.
Risk: high, because Python floor division rounds toward negative infinity and a shift does not. Only valid for provably non-negative operands.

### 3.3 v0.11.0

**Rank 12. Canonicalise loop shapes for the vectorizer (L).**
Files: `src/pyscriptc/mirlower.py` (the `while` lowering), `src/pyscriptc/miremit.py`.
What: emit a canonical counted loop (induction variable, preheader, latch, exiting block) so LLVM's loop vectorizer can work on int loops.
Why: the vectorizers are on by default and are not the blocker; the loop shape is. With the checked helpers gone (rank 8), an int loop becomes vectorizable.
Receipt: the kernel group's `benchmarks/baseline` rows, plus a loop-vectorization remark file (`-Rpass-analysis=loop-vectorize`) to show the vectorizer firing.
Risk: medium.

**Rank 13. LTO or a runtime-helper inlining pass (M).**
Files: `src/pyscriptc/emit/llvm/_driver.py`.
What: measure `-flto` with the runtime as bitcode, or bring the small runtime helpers into the module.
Receipt: the same baseline rows; plus the compile-time row, because LTO will cost compile time and the compile-time receipt is a gate for v0.9.0.

**Rank 14. A bump allocator for the never-freeing runtime (M).**
Files: `src/pyscriptc/runtime/ps_rt.c`, `src/pyscriptc/runtime/ps_rt.h`.
Why: the runtime never frees, so `malloc` per object is pure overhead and RSS is a baseline metric.
Receipt: the `native_rss_kb` column in `benchmarks/baseline/reference.json`; LeakSanitizer stays silent.
Risk: medium (the `ps_owned_push` registry exists for a reason).

**Rank 15. `Optional[T]` representation when the typing surface lands (M).**
Files: `src/pyscriptc/mir.py` (a type tag), `src/pyscriptc/mirlower.py`, `src/pyscriptc/miremit.py`.
What: the niche rule: null pointer for pointer-shaped payloads, a one-bit tag for scalars and `str`.
Receipt: `scripts/runtime_ab.py` plus new probes for the union semantics.
Risk: medium, and it depends on the typing-surface decision, which is an owner item.

## 4. What would make us wrong

Per proposed pass, the way it breaks.

**Overflow-check elision (rank 8).** The failure mode is a silent wrong answer or a missing abort. CPython grows a big int; we abort (divergence 1). If the range analysis proves "no overflow" incorrectly, a program that should abort now computes a wrapped value. Worse, the wrapped value can be *used* in a way that produces plausible output, so the A/B lane sees a wrong stdout rather than a crash. Mitigations: conservative by default; the three-category adversarial test set; `!range`-style poison rather than silent wrap where possible; and never elide a check the analysis is not sure about. Also note the LLVM frontend guide's own warning that overflow intrinsics are hard for LLVM to reason about: the same reasoning means *our* analysis has to be right.

**Typed container element access (rank 7).** The failure mode is a wrong element value or a type confusion. `list[int]` and `list[float]` must not share a layout, and the element size is baked into the sequence at construction (`ps_seq_new(elem_size, n)`). If the static type at a read site disagrees with the type at the construction site, we read the wrong bytes. `list` and `dict` also share the `MIR_P` tag, so a mis-lowered subscript reads a map as a sequence. The adversarial input is a list whose element type changes through an alias, and a dict read through a list-typed name.

**`str` header recomputation removal (rank 1).** The failure mode is a wrong `len()`, a wrong slice, or a wrong comparison. The `%ps.str` header carries `nchars` (code points, not bytes) and the ASCII flag, and the str model is mid-migration (divergence 3). If we stop recomputing, a header produced by a path that counts bytes instead of code points is now trusted. The `repeat-len` workload is ASCII, so it will not catch this; the adversarial inputs are the non-ASCII probes (`ad12_str_utf8`, the `unicode-category` workload, the `str` divergence probes).

**`fastcc` and `nounwind` (rank 3).** `nounwind` is only sound if the helper genuinely cannot unwind. `ps.iadd` and `ps.imul` branch to `ps_raise_code`, which is `noreturn`, so they do not unwind; but any change that makes a helper able to unwind invalidates the attribute, and the consequence is an LLVM miscompile rather than a clean error. `fastcc` is only sound for a function that is internal to the module and whose callers all use the same convention; a `fastcc` function that escapes the module (exported, or called through a pointer) breaks the ABI.

**`!range`, `!tbaa`, `noalias` (section 2.4).** The failure mode is a miscompile inside LLVM, not inside our code. `!range` violation yields poison, which propagates silently. `noalias` on a cell pointer that is actually reused across calls lets LLVM reorder a store and a load. The mitigation is that each of these must be justified by the runtime's actual contract, and the runtime contract for the cell ABI is: a fresh alloca per call, never stored, never aliased. If that changes, the attributes must change with it.

**MIR passes in general (section 2.1).** The failure mode is a divergence from the direct lane that only shows on an input the corpus does not have. The byte-identity lane currently protects the MIR lane, and every pass must either keep that lane green (by being a no-op on the covered functions) or come with a recorded relaxation of the gate. This is why every pass in section 2.1 is behind a flag.

**Fast-math.** The failure mode is a wrong float answer. Our float semantics are IEEE-754 and the corpus has `nan-in` and `float-inf` workloads. Fast-math enables reassociation and assumes no NaN, which breaks both the NaN comparison semantics and any signed-zero-sensitive result. Never apply it.

**Bump allocator (rank 14).** The failure mode is a use-after-free-like bug in a runtime that currently never frees. Today, "never free" is a safety property that makes a whole class of bugs impossible. A bump allocator that reuses memory would reintroduce them. It must keep the never-free property and only change the allocation strategy.

**The `str` interning idea (section 2.2 item 2).** The failure mode is an observable identity change. CPython interns string literals, so `a is b` is `True` for two equal literals; our divergence 13 refuses `is` on scalars. Interning would change behaviour in a way that needs its own divergence entry, its own probe, and probably its own release-note line.

**The type-harvesting idea (section 1.12).** The failure mode is a silently wrong binary for an input the tests did not cover. The current fence model makes unsupported constructs loud (`PSxxxx`); harvested types make them quiet. If the project ever does this, the harvest must be a hint that the type-facts engine checks at run time, never a lowering input.

**A new `Any` or tagged-union value representation.** The failure mode is a change to the whole ABI, to the runtime cell layout, to the island boundary, and to every divergence entry that mentions value representation. It would also break the byte-identity lane wholesale. This is a v0.11.0+ decision at the earliest, and it should not happen at all unless the typing surface forces it.

**The measurement noise itself.** The baseline README warns that the kernels finish in under a millisecond and that the time band is wide. My own first `-O` sweep produced nonsense (a 2.14x "regression" for `horner` at `-O3` that vanished under amplification). Any receipt built on the unamplified kernels is noise. Use the amplified form or a larger argument, and report the amplification.

## 5. Unverified claims

Marked explicitly, per the task instruction.

- The "10 to 20x runtime overhead" figure for `sys.settrace` versus `sys.monitoring` in the input survey: **UNVERIFIED** (PEP 669 confirms the API exists and is the modern replacement; the overhead ratio was not confirmed against a primary source).
- The exact CPython 3.14 small-int cache range: **UNVERIFIED** (the mechanism, `_PyLong_SMALL_INTS`, is confirmed in CPython's `longobject.c` history; the exact range for 3.14 was not read from the source in this session). Not load-bearing, because we have no boxed ints.
- "Julia's `src/codegen.cpp` is the industry standard": an opinion, not a fact. Julia's union-splitting and specialisation are confirmed; the superlative is not.
- The Wasm GC "41 percent devirtualization speedup" figure (from the wasm GC meeting notes quoted in the WebAssembly/gc issue tracker): reported by the toolchain authors, not independently verified here. Marked **UNVERIFIED**.
- The claim that LLVM's `-O3` adds meaningful passes over `-O2` for our IR: measured as **no difference** on our own workloads, which is the relevant fact.
- Whether `ps_seq_get` / `ps_map_get` would be faster with a typed element layout: **inferred, not measured** in this session. The measurement that supports it is the observation that the pure-C version of the dict workload costs the same as the emitted one, so the cost is in the runtime ABI. The specific speedup of a typed layout is an estimate.

## 6. Artifacts

All measurements are reproducible from this worktree.

- `uv run python scripts/mir_perf_ratio.py --runs 3` (MIR/direct ratio, 1.0130 geomean).
- Amplified kernels and the `-O` sweep: `/tmp/optres/` (`k1.ll`, `k2.ll`, `k3.ll`, `kernbench.py`, `olevel.py`).
- The overflow-check bisect: `/tmp/optres/k1n.ll` (unchecked), `/tmp/optres/k1n2.ll` (mul only), `/tmp/optres/kernbuild/k1-O2.bin` (emitted).
- The dict-is-runtime-C proof: `/tmp/optres/dictbench.c` (pure C) against `/tmp/optres/dictkeys_emitted.bin`.
- The `ps_str_counts` cost: `/tmp/optres/strcount2.c` (800 bytes x 20000) against `/tmp/optres/empty2.c`; 8.68 ms against 3.19 ms, so about 0.27 us per call and 0.34 ns per byte.
- The `repeat-len` str-scan measurement: `/tmp/optres/rl_dbg/out.ll` (the emitted MIR lane IR) against `/tmp/optres/rl_fast.ll` (the same IR with `ps_str_counts` replaced by an ASCII stub), linked against `/tmp/optres/rlmain.c` and the runtime objects in `/tmp/optres/kernbuild/robjs/`. The direct lane's IR for the same workload is in `/tmp/optres/rl_direct/out.ll`; `diff` against the MIR lane's is empty, which is the byte-identity gate in its raw form.
- The `fastcc` / `nounwind` bisect: `/tmp/optres/vA_fastcc_user.ll` (fastcc on the user function), `/tmp/optres/vB_fastcc_helpers.ll` (fastcc on the two helpers), `/tmp/optres/vC_nounwind_helpers.ll` (nounwind on the two helpers), each applied to the definitions and every call site.
- Sources read: `src/pyscriptc/mir.py`, `mirlower.py`, `miremit.py`, `mirverify.py`, `mirprint.py`, `emit/llvm/_driver.py`, `emit/llvm/_core.py`, `emit/llvm/_exprs.py`, `emit/llvm/_runtime.py`, `runtime/ps_rt.c`, `runtime/ps_rt.h`, `benchmarks/baseline/reference.json`, `.agents/notes/2026-11-07-compile-time-receipt.md`, `.agents/plans/2026-11-07-post-flip-vertical-plan.md`, `.agents/plans/2026-11-06-v090-user-centered-flip.md`, `docs/reference/divergences.md`.
- Primary sources consulted: the LLVM Language Reference Manual, the LLVM frontend performance guide, `llvm.org/docs/NewPassManager.html`, the LLVM `opt` source, the `TypeBasedAliasAnalysis.cpp` source, the Alive2 repository and PLDI 2021 paper, the Cranelift alias-analysis source and the Wasmtime issue 4131, the LuaJIT source and issue 924, the V8 "Fast properties" blog and `docs/heap/objects-and-maps.md`, PEP 659, the CPython `vm-state.md`, the WebAssembly GC MVP proposal, the Go interface sources, glibc's `malloc-alignment.h`, and the "Compact Representations for Arrays in Lua" paper.

## 7. Board note

This token cannot add the issue or the PR to project board 6. The board entry for this research, and the board entries for every slice in section 3, must be added by a session with board access.

---

# Addendum (2026-11-09): Linux re-measurement and v0.10.0 status re-check

This addendum re-runs the headline measurements on a different host (Debian 13, Linux 6.12, x86-64, gcc 14.2.0 and clang (Debian) as the C toolchain, CPython 3.14.9 via mise) against `origin/master` at `f8571c8a`, and re-checks every claim the note makes against the tree two days later. Conclusion up front: every load-bearing number replicates, two of them move in the direction that strengthens the recommendation, and the tree state is unchanged for all five shortlist items. The v0.10.0 shortlist in section 3.2 stands as written.

## A.1 Host difference

The original measurements were taken on macOS arm64 with Apple clang. This host is Linux x86-64 with gcc-built runtime objects and clang as the LLVM driver. That is actually the more representative configuration for the receipt work, because `benchmarks/baseline` is recorded on Linux.

## A.2 The MIR/direct ratio on this host

`uv run python scripts/mir_perf_ratio.py --runs 3` gives geomean ratio **0.9519** (kernel 0.8799, stdlib 0.9508, scripting 1.3404), with 3 of the visible rows marked "faster" and the rest inconclusive, all under the noise band the baseline README documents. The MIR lane is performance-neutral on this host too, exactly as section 0.1 concluded; the scripting group being 1.34 here is a single-row group (`script-slice`) and inside the noise for a kernel of that size. No change to the recommendation: the MIR lane is not where the user-visible wins are.

## A.3 The `-O` sweep replicates and is tighter

Same amplified-kernel method as section 0.2 (`sum_squares` over `n = 400000` invoked 30 times in an LLVM-level driver loop), interleaved timing, 9 rounds:

| build | median |
|---|---:|
| `-O0` | 99.86 ms |
| `-O1` | 13.79 ms |
| `-O2` | 12.75 ms |
| `-O3` | 12.92 ms |

`-O1` through `-O3` are within 8 percent of each other and `-O3` is not faster than `-O2`, reproducing the macOS result. `-O0` is 7.8x slower, so the emitted `.ll` depends on the optimizer for its performance shape. Section 0.2's conclusion is confirmed on a second toolchain.

## A.4 The overflow-check cost is larger on Linux

Same bisect as section 0.3, replacing the helper calls with raw `add` / `mul` in the emitted IR and keeping everything else byte-identical:

| variant | median | versus emitted |
|---|---:|---:|
| emitted (`ps.iadd` + `ps.imul`, both checked) | 14.41 ms | 1.00x |
| unchecked `mul` only | 14.66 ms | 1.02x |
| unchecked `add` and `mul` | 1.71 ms | **0.12x (about 8.4x faster)** |

The interesting difference from macOS is where the cost sits. On Apple clang, removing the multiply check alone was worth 16 percent; here removing the multiply check alone is worth nothing, and removing both is worth everything. Inspecting the emitted IR at `-O2` explains it: the multiply is loop-invariant-shaped to LLVM only after the add check is also gone, because the checked `ps.iadd` of the accumulator is what keeps the loop body from being simplified into a form where the multiply result feeds a strength-reducible induction chain. In other words the two checks interact, and the full win only appears when the whole loop body is unchecked. That makes rank 8 (range analysis plus overflow-check elision) **more** valuable than the note claimed, and it also means a partial elision (drop one check, keep the other) may buy nothing: the receipt for rank 8 must measure the combined variant, not per-check deltas. The 8.4x figure is specific to this kernel and this amplification shape; treat it as an upper bound, not an expectation for real workloads.

One nuance for whoever picks up rank 8: the emitted helpers use `llvm.sadd.with.overflow` plus a branch to `ps_raise_code` (a `noreturn` trap), not the `nsw` flag. An `nsw`-based rewrite would change overflow from a defined abort into poison, which is exactly the silent-wrong-answer failure mode section 4 warns about. Any elision must keep the explicit trap on the paths the analysis cannot prove, or move divergence 1 deliberately with an owner decision.

## A.5 `fastcc` replicates

| variant | median | versus emitted |
|---|---:|---:|
| `fastcc` on the user function (definition plus call site) | 12.75 ms | 0.88x |
| `fastcc` on the two internal helpers | 13.71 ms | 0.95x |

The user-function `fastcc` win is 12 percent here (was 24 percent on Apple clang): same direction, smaller magnitude, still the largest zero-semantics-change lever measured. The helper-side effect is also visible here, unlike macOS, but is within run-to-run drift. Rank 3 stands; the receipt should report both hosts.

## A.6 Tree re-check, 2026-11-09

- `mir.py` (1237 lines), `mirlower.py` (2949), `miremit.py` (2088), `mirverify.py` (717): unchanged in shape since the note; three commits since November 7 touched them and all were selfhost annotation fixes plus a str.rsplit/print fix, no new passes, no new ops.
- No `miropt` package exists yet. There is still no IR-to-IR transform between `mirlower` and `miremit`.
- The MVS verifier still has three rule families (`MVS01` type, `MVS02` use-def, `MVS03` control) with hand-written negative coverage in `tests/test_mirverify_negative.py`.
- The Hypothesis gap named in rank 4 is **partially closed, differently**: `tests/test_differential_hypothesis.py` now exists and property-tests compiled binaries against the CPython 3.14 oracle for int arithmetic, division, bitwise ops and string operations, derandomized for CI. This is differential testing of generated code, not fuzzing of `mirverify` itself: the verifier-structure fuzzer from rank 4 (random `MirFunction` shapes, "accepted module must print, hash and emit; rejected module must raise `MirError`") is still missing. Rank 4 narrows to exactly that.
- The `str` cell still stores only the `%ps.buf` prefix; `miremit.py` `_widen_str_cell` and the direct path `_core.py` `_widen_cell` still call `ps_str_counts` on every read (`ps_rt.h` line 345). Rank 1 is still open.
- `scripts/runtime_ab.py` and `scripts/strict_corpus_ab.py` exist unchanged; `scripts/mirverify_mutants.py` does not exist (rank 9 still open).
- `benchmarks/baseline/reference.json` (recorded 2026-10-15) re-read: overall geomean 4.18 percent of CPython, weakest non-island rows `pathlib-read` 4.1x, `os-files` 5.6x, `path-join` 7.7x, `struct-pack` 8.3x, `url-parse` 9.1x, `builtins-loop` 10x, `stringio-loop` 11.1x, all stdlib/runtime-helper-bound. Section 0.1's reading is unchanged.

## A.7 What this changes in the shortlist

Nothing in ranks 1, 2, 4, 5, 7, 9 and 10 changes. Two adjustments:

- **Rank 8 (range analysis + overflow-check elision)**: evidence upgraded from "2.3x on one kernel on one host" to "replicated on two hosts, 2.3x on Apple clang and up to 8.4x on gcc-built-runtime Linux, with a demonstrated check-interaction effect". The interaction finding adds a receipt requirement: measure the fully-elided variant, and add a probe whose loop is only provably safe when both checks are gone. The v0.10.0 placement stands, behind a flag, gated by `runtime_ab` plus the three-category adversarial set.
- **Rank 3 (`fastcc`)**: expected gain range narrows to "12 to 24 percent on an amplified int kernel depending on host clang", and the receipt must run `benchmarks/baseline` rather than a synthetic kernel, because the synthetic number is clearly kernel-dependent.

## A.8 Updated unverified list

All of section 5 still stands, plus:

- The 8.4x figure in A.4 is a single-kernel, single-amplification measurement on one Linux host: UNVERIFIED as a general number (the direction and the check-interaction are verified by direct IR inspection).
- The claim that gcc-built runtime objects versus clang-built ones do not change the kernel comparisons in A.4/A.5 was not controlled for: all Linux runs used gcc-built runtime objects. UNVERIFIED; not load-bearing, since the emitted `.ll` was constant across each bisect row.

## A.9 Reproduction

All commands run in this worktree at `f8571c8a`:

- `uv run python scripts/mir_perf_ratio.py --runs 3` (A.2).
- Kernel emitted with `uv run pyscriptc compile /tmp/optres2/k1.py --emit llvm --out-dir /tmp/optres2`; runtime objects built with `cc -O2 -c` per file (skipping `ps_island.c`, which needs `Python.h`); linked with `clang -O2 k1.ll <amp driver> objs/*.o -lm` plus a two-line LLVM `main` wrapper calling `@ps.user.main` (the emitted entry point has a dot in its name, so a C shim cannot reference it directly).
- Variants, sweep and interleaved timing: `/tmp/optres2/` (`k1.py`, `emitted.ll`, `unchecked-mul.ll`, `unchecked-both.ll`, `fastccuser.ll`, `fastcchelpers.ll`, `amp.ll`, plus the `_amp.bin` binaries).
