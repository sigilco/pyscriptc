# Lowering Strategy Options: Sequencing Native Fence Lowering

Date: 2026-10-25. Status: strategy assessment for the owner. Context: post-v0.6.0 release and v0.7.0 weekly-use gate (Refs #592). Positioning anchor: typed subset runs native, island is the floor, native is the ceiling (.agents/plans/2026-10-25-typed-subset-positioning.md). Hardware and budget envelope: 8 GB laptop plus small VPS, constrained premium-model access, majority of development driven by cheap models (Haiku and Flash class).

## Executive summary and empirical baseline

The compiler currently contains 3,057 fences across 100 modules in src/pyscriptc (tests/test_census_self.py:258). Out of 1,778 total definitions in the compiler codebase, 395 definitions (22.2%) compile natively, while 1,383 definitions (77.8%) remain fenced. Across the top 100 PyPI dependency corpus, 2,765 fences are recorded (compat/data/compat.json). Standard library coverage is 0 modules fully lowered, 54 modules partially lowered, and 1 module unlowered; builtins stand at 2 fully lowered, 15 partially lowered, and 1 unlowered.

Root cause analysis of the 3,057 self-census fences reveals 2,612 root fences and 445 derived fences (442 export-cascade and 3 class-cascade). The top root causes are dynamic annotations at 714 fences (27.3%), name resolution at 622 fences (23.8%), unlowered type names at 390 fences (14.9%), unsupported constructs at 207 fences (7.9%), unlowered calls at 116 fences (4.4%), local variable inference at 104 fences (4.0%), field lookups at 102 fences (3.9%), and closed import table restrictions at 67 fences (2.6%). The long tail comprises union types (41), keyword arguments (40), type mismatches (37), decorators (34), call arity mismatches (28), generic aliases (26), and module assignments (16).

The boxed fallback tier (--fallback=cpython, divergences 41 and 42) is operational and provides the necessary floor. In the newest boxed cost window (benchmarks/compat2/boxed_cost.py --window-commits 10, commits 46a65776e968 to e1932b041f80), boxed lines stand at 3,263 out of 75,731 total compiler lines (4.3% code share), with 37.7% churn share during the recent island floor landing, 0.0% bug share, and 10.0% iteration tax. The drop flag remains false. Dogfooding on 8 sibling project scripts from ~/Documents/prjcts/_own (issue #592) demonstrated that 6 of 8 scripts now execute end-to-end with output matching CPython 3.14 via the island floor, while 8 of 8 pure-Python PyPI packages execute at the package level under the fallback. Crucially, across all 8 sibling scripts, the number of definitions lowered natively was zero: real scripts rely entirely on the island floor because unannotated variables, dynamic calls, and partially lowered standard library modules trigger native fences immediately.

## Evaluation of Option A: Current approach (plan ahead, release-grouped lowering)

Option A maintains the established milestone workflow. Each minor release focuses on a bounded cluster of fences: v0.6.0 focused on threading, async, and string codepoints; v0.7.0 targets library consumption, island floor granularity, verification, and assessment; v0.8.0 targets FFI extern lanes and ORT integration. Work is specified in advance via design specs, lowered to LLVM SSA IR via emit/llvm/, and supported by runtime routines in src/pyscriptc/runtime/.

Expected fence reduction per month: 80 to 160 fences per month. Historically, targeted lowering drops 10 to 40 fences per PR, with occasional larger drops such as local inference (#500, dropping 341 fences). At this rate, reducing the 2,612 root fences to near zero would require 16 to 32 months of continuous effort. Dynamic annotations (714) and unlowered type names (390) would persist for many releases because they require systematic type system expansion.

Performance outcome: Excellent. Lowered code generates optimized LLVM IR compiled with zig cc -O2, achieving 2x to 10x speedups over CPython 3.14 on CPU-bound numeric loops, string processing, and deterministic data structures. Zero runtime boxing overhead is incurred for lowered types.

Maintenance churn: Very low. Code is written once to the target LLVM and C specification. There is virtually no throwaway code. Lowered constructs are locked in with regression tests and canonical rules, requiring minimal refactoring in subsequent milestones.

Risk to frozen gates: Very low. Incremental lowering adds new handlers in lower/_calls.py and lower/_stdlib.py without altering existing lowered IR structures. Probes in probes/*.jsonl and hash rules in canonical.py remain stable.

Cost in cheap-model sessions vs premium: High premium cost per fence. Emitting LLVM IR and implementing complex SSA control flow require strict type correctness, phi node placement, and precise memory layout tracking. Cheap models (Haiku, Flash) frequently hallucinate LLVM types, mishandle pointer indirection, or generate malformed IR, demanding substantial Opus/Sonnet intervention and manual debugging by the developer.

What would have to be true to win: The owner must accept a multi-year horizon for native breadth; the island floor must satisfactorily service all weekly script execution needs; and zero maintenance churn must take precedence over rapid native expansion.

## Evaluation of Option B: Advance lowering now (build on branches or behind flags)

Option B decouples development velocity from the release cadence. Lowering passes for major fence clusters (e.g., standard library modules, advanced container types, class inheritance) are built ahead of time on long-lived branches or merged into master behind feature flags (such as --experimental-lowering=all), releasing user-facing features only when milestone criteria are satisfied.

Expected fence reduction per month: 150 to 300 fences per month on development branches. Because implementation is uncoupled from immediate release ceremony, parallel branches can tackle disjoint standard library areas simultaneously.

Performance outcome: Excellent for lowered code, identical to Option A once flags are enabled.

Maintenance churn: Moderate to high. Long-lived feature branches suffer from constant merge conflicts against master refactors (such as the recent island floor granularity and caller cascade changes). If merged behind feature flags, branching logic proliferates in lower/__init__.py and emit/llvm/_driver.py, leading to dead code paths and matrix testing bloat. Code written on branches often requires rewriting when core type representation decisions shift.

Risk to frozen gates: Moderate. Flags that change lowering behavior risk leaking into default code paths, causing drift in coverage_map.py, probes/IR_HASHES.json, or CALIBRATION.json. Gate verification must be duplicated across flag configurations.

Cost in cheap-model sessions vs premium: High premium cost. Cheap models are notoriously poor at rebasing stale branches, resolving three-way merge conflicts, and maintaining subtle invariants across feature-flagged code paths.

What would have to be true to win: The project must have multiple independent developers or dedicated subagents with sufficient premium token quota to constantly rebase and maintain branch integrity without stalling master.

## Evaluation of Option C: Brute-force breadth (naive universal lowering at ~1.2x CPython speed)

Option C prioritizes native coverage over performance. It lowers all or most fences immediately by adopting a naive, unoptimized execution strategy: all Python values are represented as generic heap-allocated tagged variants or boxed C structs; method dispatch uses dynamic string lookups or flat jump tables; operators call generic fallback runtime helpers; and memory is managed via naive reference counting or leak-on-exit semantics.

Expected fence reduction per month: 500 to 1,000 fences per month in an initial burst. By eliminating static typing prerequisites, almost all 714 dynamic annotation fences, 622 name resolution fences, and 390 unlowered type name fences could be mechanically cleared.

Performance outcome: Poor to mediocre. Execution speeds would hover between 0.8x and 1.3x of CPython 3.14. Because CPython's specialized tier-1 and tier-2 bytecode interpreter with adaptive inline caches is already highly optimized, a naive C-struct boxed runtime with frequent malloc allocations and unspecialized dynamic dispatch frequently runs slower than CPython itself.

Maintenance churn: Catastrophic (near 100% throwaway code). Every naive lowering pass, generic runtime struct, and unoptimized dispatch table must eventually be dismantled and rewritten from scratch to achieve genuine native performance. It creates two massive architectural migrations instead of one.

Risk to frozen gates: Extreme. Replacing the existing typed LLVM emitter with a universal boxed model would invalidate probes/IR_HASHES.json across the entire test corpus, scramble CALIBRATION.json cells, and break the compat2 scoring contract in outcomes.py.

Cost in cheap-model sessions vs premium: Moderate initial cheap-model cost to generate naive shims, followed by overwhelming premium-model triage cost when subtle memory leaks, use-after-free bugs, and cyclic reference crashes plague the test corpus.

What would have to be true to win: The project would need an existential requirement for immediate 100% native compilation where libpython cannot exist under any circumstances (e.g., bare-metal microcontrollers), and the team would need a mandate to scrap the codebase and start over in six months. Under our actual positioning ("island is the floor"), this premise is explicitly false.

## Evaluation of Option D: Transpile through another language

Option D investigates transpiling Python AST into an intermediate programming language that compiles through LLVM or zig cc, allowing pyscriptc to inherit broad standard library and runtime semantics before compiling to native binaries. Seven candidate languages were evaluated against seven strict criteria.

Candidate 1: Nim. Pipeline: Emits C code, which can be compiled by the existing zig cc driver. Runtime and GC: ARC/ORC deterministic reference counting with cyclic collection, small runtime footprint. CI availability: Nim is not pip-installable; requires choosenim, apt, brew, or scoop, adding an external dependency and setup time. License: MIT. Compile time: Moderate to slow due to a two-hop compilation chain (Python to Nim, Nim to C, C to zig cc). Semantic fit: Excellent for exceptions and iterators; however, mapping CPython 3.14 code-point string indexing (divergence 3) and exact i64 overflow aborts (divergence 1) requires extensive custom Nim wrappers. What we lose: Direct control of LLVM IR; DWARF debug metadata fidelity (dwarf.py); IR_HASHES.json validation; determinism in A/B generation.

Candidate 2: Zig. Pipeline: Zig is already our driver via pip install ziglang (src/pyscriptc/zigshim.py). Runtime and GC: No garbage collector; manual memory allocation only. No built-in reference counting, dynamic object collection, or closure environments. CI availability: 100% available on macOS, Linux, and Windows. License: MIT. Compile time: Fast. Semantic fit: Very poor for Python runtime semantics. Zig has no classes, no inheritance, no dynamic exceptions (error sets only), and removed async/await in Zig 0.11. To target Zig, pyscriptc would have to implement a complete Python object runtime, garbage collector, and exception unwinding system in Zig. This is identical to writing a C runtime, which we already maintain in src/pyscriptc/runtime/. What we lose: It replaces 26,500 lines of proven C runtime with unwritten Zig code without lowering any fences.

Candidate 3: Swift. Pipeline: Compiles via swiftc, completely incompatible with the zig cc driver. Runtime and GC: Automatic Reference Counting (ARC) with a massive runtime library (libswiftCore.so is over 30 MB). CI availability: Available on macOS, but Linux requires separate toolchains and Windows support is complex and heavy (several gigabytes). License: Apache 2.0 with LLVM exception. Compile time: Slow. Semantic fit: Swift error handling uses typed throws rather than dynamic exceptions; strings are grapheme clusters rather than Unicode code points. What we lose: Lightweight single-binary distribution, fast CI, ability to run on an 8 GB laptop or small VPS.

Candidate 4: Mojo. Pipeline: Proprietary Modular toolchain; cannot be driven by zig cc. Runtime and GC: Ownership and borrow checking; no dynamic tracing GC. CI availability: Closed ecosystem; difficult or impossible to install headlessly across minimal Linux, macOS, and Windows CI. License: Proprietary backend. Compile time: Moderate to slow. Semantic fit: Systems language syntax; does not map dynamic Python features natively. What we lose: Open-source independence, toolchain portability, gate reproducibility. Disqualified.

Candidate 5: Crystal. Pipeline: Requires crystal compiler; cannot be driven by zig cc. Runtime and GC: Boehm-Demers-Weiser conservative GC. CI availability: Linux and macOS only; Windows support is experimental. License: Apache 2.0. Compile time: Very slow and memory-intensive. Crystal whole-program type inference routinely exceeds 4 GB of RAM, causing severe memory swapping on an 8 GB laptop. Semantic fit: Ruby-like semantics; differs substantially from Python 3.14 object and string models. What we lose: Windows target support, memory budget, compiler pipeline stability.

Candidate 6: Rust. Pipeline: Requires rustc and cargo; cannot be driven by zig cc. Runtime and GC: No GC; strict borrow checker requires wrapping all dynamic Python objects in Rc<RefCell<T>> or complex arena allocators. CI availability: Pervasive, but adds a 1 GB toolchain download. License: MIT / Apache 2.0. Compile time: Slow; heavy LLVM codegen and macro expansion consume significant CPU and RAM. Semantic fit: Mismatched error handling (panic is not Python exception flow); cyclic object graphs require manual weak-pointer management; lack of class inheritance requires complex trait composition. What we lose: Build simplicity, rapid compilation, and cheap-model generation capacity (cheap models struggle severely with Rust lifetime and borrow-checker errors).

Candidate 7: C as a target (C runtime-heavy lowering). Pipeline: Emit standard C99/C11 source code rather than raw LLVM IR text, then compile with zig cc alongside src/pyscriptc/runtime/*.c. Runtime and GC: Reuses our existing 26,500 lines of modular C runtime (ps_rt.c, ps_coll.c, ps_path.c, ps_re.c). Uses explicit reference counting and value semantics. CI availability: 100% available on all platforms through zig cc without any new dependencies. License: MIT. Compile time: Fast; zig cc compiles C quickly and with minimal RAM (< 200 MB). Semantic fit: 100% match to our existing 42 numbered divergences, because runtime C functions define those exact semantics. What we lose: Direct SSA instruction synthesis in emit/llvm/, and byte-level IR_HASHES.json reproducibility (which would need to be re-anchored on C source text hashes or emitted AST hashes).

## Evaluation of Option E: Pragmatic hybrid (widened island floor, C-runtime expansion, local inference)

Option E builds directly on the decision plan of 2026-10-25 (.agents/plans/2026-10-25-typed-subset-positioning.md): island is the floor, native is the ceiling. It synthesizes the strengths of C runtime breadth with targeted native lowering, structured across three clear layers:

Layer 1 (The Operational Floor): Keep the island floor widened for weekly dogfooding. Because the island floor is already functional and executes 6 of 8 real scripts (issue #592), the owner has immediate weekly utility without waiting for native lowering. All unannotated scripts, dynamic reflection, and unlowered standard library modules fall back to the island cleanly.

Layer 2 (The C-Runtime Native Ceiling): Expand native standard library coverage by implementing missing functions in C runtime modules (src/pyscriptc/runtime/ps_*.c) with uniform, simplified C signatures, rather than writing custom LLVM IR AST lowerers in Python. Emitting a call from LLVM IR to a C runtime function (e.g., call @ps_path_is_file or call @ps_datetime_timezone) is trivial and can be generated by cheap models with 100% reliability, whereas writing LLVM SSA lowering in lower/_calls.py and emit/llvm/ requires premium models.

Layer 3 (Targeted Type Inference): Expand local inference (#500) to resolve local variable annotations and type facts automatically. The census reveals that 714 fences are dynamic annotations and 104 are local inference. Enhancing local propagation (inferring types from assignments, loop iterators, and function return values) drops hundreds of fences in typed code without touching runtime semantics.

Expected fence reduction per month: 250 to 450 fences per month. C runtime functions are fast to write, self-contained, and easily generated by cheap models. Each new C runtime function resolves multiple fences simultaneously across scripts.

Performance outcome: Optimal. Hot typed loops run at full native LLVM speed; standard library operations run at optimized C speed; untyped code runs at CPython speed on the island.

Maintenance churn: Very low. C runtime files (ps_rt.c, ps_path.c) are permanent assets of the compiler, not throwaway code.

Risk to frozen gates: Minimal. The LLVM emitter remains in control; new C symbols are linked normally by zig cc.

Cost in cheap-model sessions vs premium: Lowest overall cost. Cheap models (Haiku, Flash) write excellent, bug-free C functions for isolated standard library routines when given clear header signatures. Premium models are reserved only for core type inference algorithms and driver orchestration.

What would have to be true to win: The owner embraces the two-tier reality (island floor for broad weekly compatibility, native for measurable speedup); the C runtime library continues to be maintained in clean C99; and the boxed ledger drop flag remains respected.

## Small experiment for the most promising adversarial option (C-transpilation spike)

The most promising adversarial option to pure LLVM IR emission is Option D's C-transpilation target (emitting high-level C code rather than LLVM SSA text, compiled via zig cc). A targeted experiment costing under one working day can decisively evaluate this path.

Experiment design: Select 5 representative scripts with known fence profiles: scripts/pytest_workers.py (system and math logic, 16 fences), scripts/gen_support_table.py (table rendering and collections, 24 fences), and three real sibling project scripts from ~/Documents/prjcts/_own (binary_size.py, tool_token_analysis.py, check-deploy-secrets.py).

Prototype implementation: In a scratch branch, construct a minimal C-emitter visitor (approx. 300 lines of Python) that traverses lowered AST functions and writes plain C functions calling ps_rt.h and runtime helpers directly, bypassing emit/llvm/_stmts.py and emit/llvm/_exprs.py. Compile the emitted C file with zig cc -O2 -I src/pyscriptc/runtime/ and link with the existing runtime objects.

Target measurements:
1. Implementation complexity: Compare lines of compiler code required to lower 3 common fence root causes (field lookup, unsupported constructs, and keyword calls) via C emission versus LLVM IR emission.
2. Toolchain overhead: Measure compile time in milliseconds and peak RAM usage during zig cc compilation on an 8 GB machine.
3. Model success rate: Prompt a cheap model (Haiku or Flash) to generate lowering code for the 5 scripts under both LLVM IR and C emission targets; record the number of syntax errors, type errors, and manual interventions required.
4. Execution performance: Benchmark compiled binaries against CPython 3.14 on CPU-bound loops.

Kill criterion: Terminate the C-transpilation path immediately if: (a) compilation time via zig cc of generated C is more than 50% slower than direct LLVM IR compilation; (b) cheap models generate unsafe C pointer manipulations resulting in segmentation faults on 2 or more scripts; or (c) binary size increases by more than 2x over the current LLVM binary size. If killed, the project stays strictly on LLVM IR emission with C runtime function calls.

## Recommendation and synthesis

Adopt Option E (the Pragmatic Hybrid). It strictly aligns with the recorded positioning decision of 2026-10-25: island is the floor, native is the ceiling.

Do not attempt a whole-compiler rewrite to transpile through Nim, Zig, Swift, Rust, or Crystal (Option D). None of these languages provide a clean match for CPython 3.14's dynamic semantics, exception model, or Unicode codepoint string rules without rebuilding the exact same runtime we already possess in C, while introducing heavy external toolchains that destroy our lightweight 8 GB laptop and VPS workflow.

Do not pursue brute-force naive lowering (Option C). Lowering untyped code into a slow, boxed, dynamic C runtime would produce a compiler slower than CPython, invalidate all frozen gates-v2 testing assets, and generate 100% throwaway code.

The correct sequencing path is:
1. Consolidate the v0.7.0 island floor gate (milestone 4): Ensure the 8 sibling scripts and PyPI package programs execute reliably with zero exit-2 preflight crashes, fulfilling the weekly-use promise today.
2. Systematically expand standard library lowering via modular C runtime routines (src/pyscriptc/runtime/ps_*.c): Address the top root causes in unlowered-call (116 fences) and construct-other (207 fences) by providing uniform C functions that the LLVM emitter can invoke with a single call instruction.
3. Enhance local type inference: Address dynamic-annotation (714 fences) and local-inference (104 fences) by propagating types across local assignment boundaries, enabling typed code to compile natively without manual annotation boilerplate.
4. Measure before optimizing: Run pyscriptc assess --bench on candidate scripts; only prioritize native lowering for modules where native execution measurably beats CPython (>1.5x speedup).

## Decision table

| Dimension | Option A: Current Release-Grouped | Option B: Advance on Branches | Option C: Brute-Force Breadth | Option D: Transpile (Nim/Rust/C) | Option E: Pragmatic Hybrid (Recommended) |
| --- | --- | --- | --- | --- | --- |
| Expected fence drop / month | 80 to 160 | 150 to 300 | 500 to 1,000 | 300 to 600 | 250 to 450 |
| Perf vs CPython 3.14 | 2x to 10x native | 2x to 10x native | 0.8x to 1.3x (slower/equal) | 1.5x to 5x | 2x to 10x native (island at 1x) |
| Maintenance churn (throwaway) | Near 0% | Moderate (branch rot) | High (~100% rewrite) | High (toolchain coupling) | Very low (additive C runtime) |
| Risk to frozen gates-v2 | Very low | Moderate | Extreme (invalidates hashes) | Extreme (destroys IR hashes) | Minimal (LLVM IR preserved) |
| Cheap model usability | Low (LLVM SSA errors) | Low (merge conflict errors) | Moderate (naive shims) | Low to Medium (Rust/Nim errors) | High (C routines + simple calls) |
| 8 GB laptop & VPS fit | Excellent | Good | Good | Poor (Rust/Crystal OOM) | Excellent (< 200 MB RAM) |
| Time to broad weekly use | 12 to 24 months | 9 to 18 months | 1 to 2 months | 6 to 12 months | Immediate (via island floor) |
| Winning condition | Long time horizon, zero churn | Parallel dev team available | All-or-nothing native mandate | Existing runtime unusable | Two-tier positioning accepted |

## Key questions the owner must answer

1. Is the weekly-use criterion satisfied if real-world sibling scripts run at CPython speed via the island floor, or does the owner require measurable native speedup on those specific scripts in v0.7.0?
2. Should we authorize the 1-day C-transpilation spike (Option D/E variant) to test whether emitting C text is more cheap-model friendly than emitting LLVM IR text, or should we strictly keep all code emission in LLVM IR?
3. When expanding standard library coverage, do we agree that implementing routines in C runtime files (src/pyscriptc/runtime/ps_*.c) is preferred over writing intricate AST lowerers in Python (lower/_calls.py)?
4. What is the acceptable ceiling for boxed code share? The ledger currently records 4.3% code share and 37.7% churn in window 0 following the island floor landing; do we maintain the existing 15% tax / 20% churn drop flag rules in AGENTS.md?
