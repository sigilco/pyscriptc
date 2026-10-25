# Profiling and preferred idioms: an assessment

Date: 2026-10-25. Commit: `49608e0`. Host: 6-core x86_64 Linux, CPython 3.14.9, clang 19.1.7, load average 1.39, no zig, no `perf`, no valgrind, no macOS. Scope: assessment only, no `src/` changes. Grounding: `.agents/docs/debug.md`, `.agents/docs/pyscriptc-debug.md`, `.agents/notes/2026-10-25-real-script-bench.md`, `.agents/notes/2026-10-25-port-deltas.md`, `compat/data/*.json`, and the measurements below, which are single-machine medians of 3 to 9 samples with process startup included unless stated.

`.agents/plans/2026-10-25-lowering-strategy-options.md` does not exist on `origin/master` (`git ls-tree -r --name-only origin/master | grep lowering-strategy` is empty), so the fence-strategy side is answered from the code and data that do exist rather than from that plan.

## Short answers

Q1a, the compiler: yes, but the existing surface is incomplete rather than missing. `--timings` and `--debug-dir` already produce per-stage and sub-step wall times, and `cProfile`, `py-spy` and `pyinstrument` all profile the compiler with no code change, but the table omits the unconditional `.ir.json` write (the second largest cost on a large module) and the fence rendering that dominates a fenced compile, and a failed preflight loses its own timing entry. Completing the existing table is an S-sized change; a new profiler is not warranted.

Q1b, the user's program: `pyscriptc assess --bench` gives one end-to-end ratio per entry point, and `run --report` gives the native/island plane map, but there is no per-definition time, no island crossing count, no marshalling volume and no fence cost ranking. The binary seam for external sampling profilers already exists and was verified end to end with gdb: the binary exports user functions by name in both builds, `--debug-info` adds `.debug_line` rows that map native PC ranges to Python lines, and an island binary additionally exports `ps_island_call` and `ps_island_fn_invoke` plus links libpython. The right move is to document and finish that seam (debug info plus frame pointers plus the plane map) and let `perf`, `samply` and Instruments do the profiling; a `run --profile` command should be a launcher that joins their output to the plane map, not a profiler.

Q2, idioms: the repo data does not support a ranked style guide at the level TypeScript's handbook operates. Of the 2746 fences in `compat/data/compat.json`, the dominant families are construct and stdlib surface (PS2002 1331, PS1001 300, PS2001 205), not choices between equivalent forms, and 32 of the 39 minimal equivalent-form probes below compile with zero fences. Two preferences are defensible today and both are compromises with current limits rather than permanent guidance: use a struct-like record (dataclass) instead of a dict in a hot field-access loop, and accumulate strings with `join` instead of `+` in a loop. Everything else should be "no preference" until a probe or a measurement says otherwise.

## Q1a: profiling the compiler

### What exists today

`pyscriptc compile FILE --debug=stages` and `--timings` print the table built by `pyscriptc/debug.py:23-140`: `parse`, `lower` (with `parse` excluded from it), `bridge`, `check`, `validate`, `verify`, `emit_llvm`, `llvmverify`, `archive`, `binding`, `link`, `run`, and inner `cc_compile`/`cc_link` entries, each in milliseconds plus a `total` measured from `Debug.__init__`.

`--debug-dir DIR` writes `summary.json` with `stages`, `timings`, `totalMs`, `fences`, `functions`, `capabilities`, `commands`, `outputs`, `status` and `verify` (`src/pyscriptc/debug.py` `write_dir`), and that file is the machine-readable seam a third-party consumer should read today; `docs/guide/debugging.md` documents the user-facing version and `.agents/docs/debug.md` the full contract.

The audit log (`src/pyscriptc/log.py`, `pyscriptc logs`) is structlog-shaped JSONL with `compile.start`, `emit.ir`, `emit.llvm`, `link.binary`, `run.start`, `run.exit` and `cli.invoke`, but no durations and no stage timings, so it is an event trail, not a profile.

The benchmarks under `benchmarks/` time whole commands (`benchmarks/real/run.py`, `benchmarks/baseline/run.py`, `benchmarks/compat2/census.py`) and their own driver code is where a stage breakdown is really needed; none of them consume `summary.json`.

### Measured stage tables

| Input | Size | parse | lower | check | validate | emit_llvm | total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `examples/add.py` | 2 lines, 1 def | 0.1 | 1.0 | skipped | 0.1 | 0.3 | 5.7 |
| `examples/add.py` with preflight | 2 lines, 1 def | 0.1 | 0.6 | 420.7 | 0.1 | 0.2 | 424.5 |
| generated `big3000.py` | 5705 lines, 901 defs | 64.0 | 318.0 | skipped | 67.9 | 183.0 | 866.6 |
| `benchmarks/compat/top100/src/jwt/algorithms.py` | 1121 lines, 61 defs, 41 fences | 13.4 | 24.5 | skipped | 0.3 | none (refused) | 767.5 |
| `pyscriptc coverage` on `benchmarks/compat/pyjwt/src` | 11 modules | n/a | n/a | n/a | n/a | n/a | 866 wall |
| `python -m benchmarks.compat2.census --top 3` (public top100) | 18 packages | n/a | n/a | n/a | n/a | n/a | 10350 wall |

On the 901-definition module the named stages account for 633 of 867 ms; the missing 234 ms is almost entirely the unconditional IR sidecar write. On the fenced jwt file the named stages account for 38 of 767 ms; the missing 729 ms is the fence rendering, and a direct measurement of `render_fences` on the same module is 840 ms, so the two runs bracket it.

### Where the time actually goes

1. Fence rendering is quadratic in fences times module size and is not a stage. `lower_file` on the jwt file takes 52 ms and `render_fences` takes 840 ms for 41 fences and 415 characters of output (`src/pyscriptc/lower/__init__.py:2837`); a cProfile of that call shows 572,241 `ast.walk` node visits, because `_assign_targets`, `_assign_rhs_names` and `_def_at` each walk the whole module once per fence (41 + 41 + 43 walks). Caching a line-to-node index once would take this to milliseconds, and this is the single largest compiler latency a user sees on a fenced file.
2. The IR sidecar JSON is written unconditionally and untimed: `json.dumps(module, indent=2)` plus `write_text` at `src/pyscriptc/cli.py:1657`. On `big3000.py` that is 153 ms of serialization and 72 ms of writing for a 20.34 MB file, 226 ms, or 26 percent of the whole compile, and py-spy attributes 15.5 percent of self samples to `json` `iterencode`.
3. The pyrefly preflight dominates small compiles and loses its own timing on failure. `check` is 420.7 of 424.5 ms on `examples/add.py`, and when the checker finds an error the `dbg.end("check")` at `src/pyscriptc/cli.py:1645` is skipped because `run_checker` is not in a `try/finally`, which is exactly what the jwt run with the preflight on shows: the table has `parse` 11.9 and `lower` 27.4, total 587.9 ms, and no `check` row, while the unaccounted 550 ms is mostly pyrefly's subprocess plus interpreter startup.
4. IR validation runs twice. `validate` is 67.9 ms, then `emit_llvm` calls `assert_same_cpython` (`src/pyscriptc/emit/llvm/_driver.py:92`), which calls `validate_module` again (`src/pyscriptc/ir.py:482`); in pyinstrument terms the second pass is 0.226 s against 0.236 s for the first, so roughly half of `emit_llvm` is duplicate validation.
5. The LLVM module verifier is an external subprocess: `llvmverify` was 69.5 ms on a two-function module in `summary.json`, because `opt -verify` is preferred when `opt` is on PATH.
6. Lowering is spread across whole-AST passes rather than one hot function: on `big3000` pyinstrument shows `_scan_global_slots` 0.225 s and `_coro_signature`/`is_generator` 0.214 s out of 0.99 s of `lower_file`, about 44 percent of lower in repeated full-AST walks, which explains why `lower` costs 318 ms for 901 tiny functions.
7. `parse` dominates batch jobs: in the census run, py-spy attributes 60.2 percent of self samples to `ast.parse` (`compile`), 25.5 percent inclusive to `Lowerer.module`, and 36.5 percent inclusive to `_catalog_once`; the census cost is Python parsing of the corpus first and lowering second.
8. `coverage`/`assess` parse files twice: `build_report` lowers the target and then re-parses every file to collect definitions (`src/pyscriptc/coverage.py:470`), so a directory run pays for parsing twice; `pyscriptc coverage` on the 11-module pyjwt tree took 866 ms.

### Top 10 self-time hot spots

From py-spy 0.4.2, 1022 samples over 10 in-process compiles of `big3000.py` (speedscope, self time): `json.encoder.iterencode` 15.5 percent, `ast.parse` 5.9, `ir.validate_expr` 5.3, `lower.span_of` 3.4, `lower._stmt_core` 3.1, `lower._name` 2.6, a generator expression 2.4, `ast.iter_fields` 2.4, `ir.validate_stmt` 2.3, `lower.module` 2.2.

From cProfile ordered by tottime on the same input, which is useful for call counts rather than wall attribution: `validate_expr` 0.190 s over 67,208 calls, `isinstance` 0.169 s over 791,328 calls, `iterencode` 0.155 s, `ast.iter_child_nodes` 0.122 s over 126,360 calls, `dict.get` 0.096 s over 331,912 calls, `validate_stmt` 0.086 s, `coro._own_nodes` 0.080 s, `validate_type` 0.077 s, `iter_fields` 0.069 s, `_for_range` 0.068 s.

### The profiling tools, as used

`cProfile` works with no setup (`python -m cProfile -s cumtime -m pyscriptc compile ...`), inflates the run by roughly 3x here (2.6 s against 0.87 s) and is the right tool for call counts and for attribution questions like "how many times is this AST walked".

`py-spy` (installed with `uv tool install py-spy`, version 0.4.2) samples without modifying the process and writes speedscope JSON; a single 0.9 s compile yields too few samples to be useful, so the run must be looped (10 compiles gave 1022 samples). It needs ptrace rights, which worked on this host, and it is the best fit for CI because it needs no source change.

`pyinstrument` (installed with `uv tool install pyinstrument`, run through `uv run --with pyinstrument python -m pyinstrument`) gives a readable call tree with wall time and lower overhead than cProfile at this scale (2.4 s against 2.6 s), and its tree is what showed the duplicate validation and the `_scan_global_slots` cost; it must be invoked with options before `-m` or they are passed through to `pyscriptc`.

`perf` is not installed on this host, so no sampling of the compiler with `perf` was possible; on Linux it would be the same information as py-spy with symbols, and on macOS Instruments or samply cover it.

### Could `--profile` be cheap?

Yes. The measurement plumbing already exists in `Debug.entries`, and the missing work is small: wrap `render_fences` (both call sites, `cli.py:1219` and `cli.py:1668`), the `.ir.json` write (`cli.py:1657`) and the `.report.json` write (`cli.py:1691`) in `dbg.begin`/`dbg.end`; move `dbg.end("check")` into a `finally` so a failed preflight still reports its cost; and add a `runtime-cache` entry around the first-use C object builds. That would take the table from 633/867 accounted on the big module to nearly all of it, and from 38/767 on a fenced file to nearly all of it.

The output format should not be a new one: `summary.json` is already the stable artifact, and if a trace view is wanted, the same entries render to Chrome Trace Event JSON, which Perfetto and `chrome://tracing` open with no code in this repo. A `--profile` alias that means "timings plus a debug dir" is defensible for discoverability, but the fix belongs in the existing table, not in a sampling profiler, and there is no reason to write a profile viewer.

## Q1b: profiling the user's program

### What a user can learn today

`pyscriptc assess PATH` reports native, island and blocked shares per file and per definition, the plane map (including `demotedBy` for cascade demotions), per-fence root causes with the nearest typed idiom (`src/pyscriptc/assess.py:68`), and with `--bench` one end-to-end CPython versus native ratio per compilable entry point, measured interleaved with 5 samples (`_run_bench` at `src/pyscriptc/assess.py:185`). On `examples/access_log.py` that reads `27.6x CPython (median native 7.66ms vs cpython 211.63ms, 5 samples)`.

`pyscriptc run --report[=FILE]` prints or writes the executed plane map, and `compile` always writes `<out>.report.json` next to the artifact, which is the join key for any external profile: it names every function as `native` or `island` and lists `demotedBy`.

`pyscriptc verify` proves output parity but reports no time, and `benchmarks/real/run.py` times whole scripts (`/tmp` harness, not user-facing).

The audit log is compiler-only: it carries no per-run data about the user's binary, so it cannot answer "where did my program spend its time".

### The DWARF seam works, verified with external tools

`--debug-info` (`cli.py:289`, env `PYSC_DEBUG_INFO`, documented at `docs/getting-started/install.md:147`) makes the emitter attach `DISubprogram`, `DILocalVariable` and `DILocation` records (`src/pyscriptc/dwarf.py`) and passes `-g` to clang; `readelf -S` shows `.debug_info`, `.debug_line`, `.debug_loc` and friends, and `readelf --debug-dump=decodedline` on the `hot.py` probe lists rows for lines 1, 3, 4, 5 and 8 to 10 of the Python file.

`gdb -batch -ex "info line work" -ex "disassemble /s work"` on the same binary prints `Line 1 of "hot.py" starts at address 0x49c0 <work>` and disassembles with the Python source interleaved, which is the proof that a third-party debugger or sampling profiler can attribute native instruction pointers to Python lines.

User functions are exported symbols: `nm` shows `work` as a global `T` symbol, and `gdb` also shows the generated `ps.user.main` wrapper, so a sampler can name frames without the plane map; nested definitions and name collisions across modules were not tested, and that is a known uncertainty for the join; a stripped artifact would lose the symbol half and fall back to DWARF names.

An island binary additionally exports the trampolines and links libpython: `nm` on a `--fallback=cpython` build lists `ps_island_call` (`src/pyscriptc/runtime/ps_island.c:984`), `ps_island_fn_invoke` (`ps_island.c:1291`), `ps_island_call_value` and 35 more, and `ldd` shows `libpython3.14.so.1.0`. This is what makes `perf probe`/uprobes and dtrace viable for crossing counts with no compiler code.

The cost of the seam is small: a debug build of `hot.py` is 516,280 bytes against 514,712 without (plus 1,568 bytes, 0.3 percent), and compile time grows about 7 percent (1,283 to 1,369 ms on `big3000`, 931 to 992 ms on `hot.py` with the preflight on), because the module text carries a `!dbg` attachment on each instruction.

What is missing for sampling is frame pointers: the cc line is `clang -O2 -g -x ir -c ...`, so clang omits the frame pointer at `-O2` and `perf record` will need `--call-graph dwarf` (slow, large stacks) or a `-fno-omit-frame-pointer` profile build. That flag is the whole "user-code sample point" design: one compiler flag on profile builds, plus DWARF, plus exported symbols.

`py-spy` is not a candidate for the compiled binary: it fails with `Error: Failed to find python version from target process` on a native binary, and on an island binary it started but produced 3 samples in 72 ms, so it cannot be relied on across the seam. `perf` (Linux), samply (Linux and macOS) and Instruments (macOS) are the candidates, and none of them needed a compiler change to work once symbols and line tables are present.

### Measured costs a profiler would have to surface

An island crossing is expensive per call: a native `main` calling an unannotated island `bump(x)` 100,000 times took 72.47 ms, while the same loop with a typed native `bump` took 1.18 ms, so the crossing costs about 0.72 microseconds per call including int marshalling and the return unbox (`ps_island_fn_invoke`), against an inlined native call near zero. The plane map for that case is `main: native`, `bump: island`, `demotedBy: {}`.

Reading a string copies it: with a 1 MB string, `t = len(big)` 500 times took 744 ms, while computing `n: int = len(big)` once and assigning `t = n` in the loop took 9.6 ms and a loop that never mentions `big` took 12.1 ms, so the cost is the read of the local, at roughly 1.5 ns per byte, not the `len`. Passing strings shows the same slope above 100 bytes: 500,000 calls with a 10-byte string 29 ms, 100-byte 187 ms, 1,000-byte 1,526 ms, which is about 3 ns per byte at the call.

These two numbers are exactly the kind of thing a user cannot see today: `assess` would say the program is 100 percent native and `--bench` would compare it only to CPython, so a native program that copies 1 MB per loop iteration looks like a success until it is slower than the interpreter. The same 500 reads of a 1 MB string take 29.25 ms under CPython, so the native pattern is 25 times slower than the interpreter it was meant to replace.

For scale, a tight native arithmetic loop of 1,000,000 iterations runs in 3.1 ms against 123.3 ms for the same work under CPython (including about 1.1 ms and 28 ms of process startup), so the ceiling is real; the cost model is about what code does with values, not the loop.

### Ranked design for a profile command

Rank 1, slice 0, S: no new command. Add `--debug-info` to `run`, `verify` and `assess` (today `run --debug-info` is rejected: `unrecognized arguments`, and only `PYSC_DEBUG_INFO` works), add `-fno-omit-frame-pointer` to the cc line when debug info is on, and write one user page that shows the exact `perf record -g`, `samply record` and `xcrun xctrace record` recipes plus the plane map join. The join is a JSON file plus a symbol table, not software.

Rank 2, M: `pyscriptc run --profile [--profiler=auto|perf|samply|xctrace]` as a subprocess launcher only: compile with debug info into the module's normal artifact, exec the external profiler on it with the user's argv, then print the plane map and the one command that opens the profile. It must not parse or render profiles; the moment it does, it is a profiler. Kill criterion: if the launcher is used only by maintainers, delete it and keep the Rank 1 page.

Rank 3, S: crossing counts with no code, documented in the same page: `perf probe -x BIN ps_island_fn_invoke` and `perf probe -x BIN ps_island_call`, or the dtrace equivalent, count total seam crossings. Per-definition crossing counts are not a one-liner because both trampolines take the callee name as a `(length, pointer)` pair, so a per-function ranking is the one metric that may later justify a counter, and it should wait until someone asks for it.

Rank 4, L, not recommended now: an optional `PS_COUNTERS` build with counters in `ps_island_call` and the native call sites, off by default and compiled out when off. It is the only way to get marshal bytes per call, but allocation profilers (valgrind massif or DHAT, heaptrack) can approximate the copy volume from call stacks with no compiler code, so this tier should stay closed until there is a production single-binary requirement that those tools cannot serve.

Fence cost ranking falls out of Rank 1 plus Rank 3: the plane map says which definitions are island, and crossing counts or per-symbol samples say how often; a static "fence cost" score without call counts would be guessing.

## The extension seam: what to expose, not what to build

The rule for every extra in this note is that the compiler exposes data and stops; the analysis, rendering and sampling belong to a third-party tool with its own maintenance. Concretely, the seam has five parts, and the new code is one small event sink plus one cc flag; the other three parts already exist.

Compile events: `Debug.entries` already brackets every stage, so add one optional sink that writes `{schema, ts, pid, event, stage, parent, ms, counters}` as JSONL or as a Chrome Trace array, selected by one flag or env var, with zero formatting when off. The counters that make it useful are already in hand at the call sites: definitions, fences, AST nodes, IR bytes, LLVM bytes, binary bytes, cache hit.

Artifacts: `summary.json` (timings, fences, capabilities, commands, outputs, verify) and `<out>.report.json` (plane map) already exist and are the machine format; they need a schema version and a compatibility promise, not a new format.

Fence reports: `coverage --json` and `compile --print diagnostics` already emit a stable JSON shape. If an editor or GitHub integration is wanted, emit SARIF 2.1.0 from the same records rather than writing a viewer; GitHub code scanning and most editors consume SARIF, nothing in this repo would.

Debug info: DWARF plus exported symbols plus frame pointers, as above. This is the only part that touches emitted code, it is opt-in, and it costs 0.3 percent of binary size and about 7 percent of compile time.

Island seam: the exported trampolines are the seam. Do not add counters, hooks or callbacks to `ps_island.c` for profiling; uprobes can read the existing symbols, and any new instrumentation would be permanent cost in the shipped binary for a dev-time question.

Typechecking and validation reuse existing systems and should keep doing so: pyrefly is a 34 MB external binary behind a subprocess (`src/pyscriptc/check.py`), pyright and mypy are deprecated lanes kept for diffing (`benchmarks/factsdiff.py`), ruff is the Python lint, and the LLVM verifier (`opt -verify`) is the IR check. No custom typechecker, no custom lint, no custom IR verifier beyond the small structural pass that already exists.

## Reuse matrix

| Extra | Tier | Reuse candidate | Maintenance | Fit | Recommendation | Kill criterion |
| --- | --- | --- | --- | --- | --- | --- |
| Compiler stage timings | dev | `Debug` plus Chrome Trace JSON (Perfetto) or OTLP spans | small | high | complete the existing table and add one sink | no trace consumer in two releases |
| Compiler sampling | dev | cProfile, py-spy, pyinstrument | none | high | keep as documented recipes, no wrapper | not applicable |
| Fence diagnostics | both | existing JSON, plus SARIF 2.1.0 for editors and GitHub | small | medium | JSON now, SARIF only on demand | no consumer after one release |
| Typecheck frontend | both | pyrefly (current); ty, pyright, mypy as evaluation lanes | none beyond the pin | high | keep pyrefly and re-measure yearly | pyrefly drops the 3.14 window or its preflight cannot be configured below 1 s |
| Lint and IR validation | both | ruff, pyrefly, `opt -verify` | none | high | keep, add nothing | not applicable |
| User CPU, native code | dev | perf, samply, Instruments | none | high | DWARF plus frame pointers plus docs | no user usage reported |
| User CPU, native code | prod single binary | none; opt-in DWARF only | none | high | no sampling in the shipped binary, debug info on request | not applicable |
| Allocation and copy volume | dev | valgrind massif or DHAT, heaptrack | none | high | document, since it explains the string-copy cost | not applicable |
| User CPU, deterministic | dev | valgrind callgrind | none | medium | last resort when sampling is ambiguous | too slow for routine use |
| Profile format and viewer | any | pprof | none | low | reject, no producer for a clang or LLVM binary | not applicable |
| Event logging library | both | structlog | none | low | reject, the zero-dependency rule wins and the audit layer is already shaped like it | not applicable |
| Island crossing counts | both | uprobes or dtrace on exported `ps_island_*` | none | high | document the probes | symbols get internalized or the target OS has no probe facility |
| Per-function island counts and marshal bytes | prod | `PS_COUNTERS` `#ifdef` counters | high | low | last resort, closed for now | delete if the probe path covers the need |
| Product observability | both | PostHog (exists), JSONL audit (exists), OTel only if a collector appears | small | medium | keep the two that exist, no new UI | no dashboard or alert consumes it |
| Idiom preference data | docs and compat | generated JSON plus one docs page | small | medium | adopt the probe plus page, no lint | no row where the preference changes a fence or a measured 2x |

Two candidates from the brief are rejected outright: pprof is a Go ecosystem format and toolchain with no producer for a clang or LLVM binary, and a structlog dependency conflicts with the zero-dependency rule even though the audit layer is already structlog-shaped, so the existing JSONL remains the seam. Callgrind is a dev-only candidate rather than a prod one because it is deterministic and instruction-level but typically 10 times or more slower, which makes it a good last resort for the string-copy question and a poor fit for a CI gate. OTel is worth adopting only if a collector and a dashboard already exist; otherwise the JSONL and `summary.json` are the same data with none of the operational surface.

The single-binary constraint rules out sampling instrumentation in production and rules in DWARF only when the user asks for it, because `-g` is already off by default and costs 0.3 percent when on; with the flag off the emitted text carries no metadata and the binary no debug sections, so the production cost is exactly zero, and any counter tier would have to have the same property by construction. The same constraint is why crossing counts should come from uprobes instead of a counter in `ps_island_call`.

## Q2: preferred idioms or unranked equivalents

### What the repo data says

The public fence census (`compat/data/compat.json`, generated 2026-10-25) counts 2746 fences, led by PS2002 construct not lowered 1331, PS1001 annotation missing 300, PS2001 stdlib name not lowered 205, PS1106 unlowered annotation 198, PS1103 generic alias 129, PS1204 class body statement 125, PS1101 union 92 and PS2003 call arguments 80. Those are surface gaps, and they do not distinguish between two spellings of the same construct.

The real-script gap data (`compat/data/what_will_fail.json`, 39 scripts, 53 fences) is more specific: stdlib surface 12, call signatures and keywords 11, statements 10, inference 9, checker 5, imports 3, classes 1, operators 1. The call-table entry names `argparse` option and default keywords, and the checker entry names `open` path as str and `os.listdir` on a Path, which are exactly the families tested below.

`assess` already emits a "nearest typed idiom" per fence (`src/pyscriptc/assess.py:68`), but the strings are fence fixes, not style preferences: "Annotate with the concrete type. object, Any, Protocol, TypeVar and Callable do not lower", "Narrow the annotation to one arm", "Replace the alias or imported name with a lowered type". On the pyjwt package the 122 fences map to 24 distinct hints, all of this shape.

The port deltas note (`.agents/notes/2026-10-25-port-deltas.md`) lists 56 fence sites, 45 mechanical and 11 semantic, and it is already partly stale: it says to fold `collections.abc.Sequence` and to annotate unannotated module constants, but the minimal probes below show zero fences for both at this commit, likely because `fb4fe8e feat(lower): foldable unannotated module constants` (#631) landed after the note was written. It also says to pass arguments positionally because keywords fence, which no longer reproduces for user functions.

### Equivalent-form probes, measured at this commit

Each probe was compiled with the type-checker preflight on (`--emit bin --call main`), and the output was compared with CPython on the first line; `--call` appends the returned value, so the native stdout has one extra line, which was ignored. Native timings are medians of 5 or 7 samples and include about 1.1 ms of process startup; CPython figures include about 22 to 28 ms.

| Family | Variants | Native today | Fences today | Native median | Recommendation |
| --- | --- | --- | --- | --- | --- |
| Records | `@dataclass` | class and field access lower, but `for it in items` over `list[Item]` does not | PS2002 on iteration, 1 root, caller cascades | 1M field reads 10.15 ms | dataclass plus index loop when hot |
| Records | plain class with `__init__` | same as dataclass | PS2002 on iteration | not timed | equivalent to dataclass |
| Records | `TypedDict` | no: class base not lowered, and entries lose their one-value type | PS2002 class base, PS1003 dict entries | n/a | avoid today |
| Records | `NamedTuple` | no: class base not lowered | PS2002 class base and iteration | n/a | avoid today |
| Records | `dict[str, int]` | yes | none | 1M lookups 38.05 ms | fine for map-shaped data, 4x a dataclass field |
| Records | `dict[str, object]` | no | PS1104 object, then cascade | n/a | replace with a dataclass |
| Calls | positional vs keyword on a user function | both yes, including defaults and reordered keywords | none | 1M calls 9.36 vs 9.50 ms | no preference |
| Calls | stdlib keywords | per-call whitelist: `print(sep=, end=)` and `open(encoding=)` yes, `open(errors=)` no | PS2003 with the keyword named | n/a | use the keywords the call table lists |
| Calls | `argparse` `type=str` vs `type=Path` | str yes, Path no | PS2003 option type, PS2001 `Namespace.name` | n/a | str plus explicit `Path` conversion |
| Calls | `argparse` `nargs="+"` | no | PS2003 | n/a | avoid nargs today |
| Strings | `s = s + str(i)` in a loop | yes, but each concat copies | none | 1k 4.81 ms, 2k 17.05, 4k 74.95, 8k 311.67 | do not accumulate with `+` |
| Strings | `parts.append(...)`, then `"".join(parts)` | yes | none | 8k 3.18 ms | preferred for accumulation |
| Strings | `f"{i}"`, `"{}".format(i)`, `"%d" % i` | all yes | none | 8k: f-string 2.86 ms, format 3.72, percent 3.28 | no preference for one value |
| Iteration | comprehension, loop with append, `list(map(f, ...))` | all yes | none | 200k: 4.63, 4.59, 5.94 ms | no preference, map is slightly slower |
| Containers | `list[int]`, `Sequence[int]`, `Iterable[int]` | all yes in this minimal form | none | startup bound | no preference, prefer `list` for clarity |
| Paths | `os.path.join` plus `open` vs `Path.read_text` | both yes | none | 1.18 vs 1.24 ms | no preference, pathlib is fine |
| Paths | `Path.open("rb")` and stream reads | no: `Path.open` is not a method in the call table | PS2001 `Path.open is not a method` | n/a | use `read_bytes` and `read_text` |
| Optional | `if v is not None:` guard | yes | none | startup bound | preferred for narrowing |
| Optional | `assert v is not None` then use | no: assert does not narrow | PS1003 return is str or None | n/a | do not use assert for narrowing |
| Optional | `v or "default"` | yes | none | startup bound | only when falsy values should also be replaced |
| Sorting | `sorted(xs)`, `sorted(xs, reverse=True)`, `sorted(xs, key=lambda ...)` | all yes | none | 4-element sort 1.32 ms, startup bound | sorted is fine, the port note's lambda fence is stale |
| Modules | annotated vs unannotated literal module constant | both yes after #631, in this minimal form | none | 1.23 vs 1.36 ms, startup bound | annotate for readers, not for the compiler |
| Errors | `try/except ValueError` vs an `if` check | both yes | none | startup bound | no preference |
| Dictionary loops | `for k in d` plus `d[k]` vs `for k, v in d.items()` | both yes | none | startup bound | no preference, items is clearer |

Two rows are the only ones with a measured performance reason: record access and string accumulation. The record row is a compromise, not a recommendation in the abstract, because the fast form (`it.size` in an index loop) is only reachable today because direct iteration over a typed list of dataclasses fences; if iteration lowers, the index-loop half of the advice should disappear and only "use a dataclass rather than a dict when the value is a record" should remain. The string row is also about current lowering: native `+` in a loop copies on every step and is quadratic, 4.8 ms at 1k and 312 ms at 8k, while CPython stays flat near 25 ms; `join` is flat at 3.2 ms at 8k. That is a runtime optimization gap as much as a style question, and the preference should be phrased as "accumulate with join" (good Python anyway) rather than "native + is broken".

Everything else in the table is a "no preference" in the strong sense: the compiler accepts both, the measured difference is inside startup noise, and inventing a ranking would be a DX opinion disguised as a compiler fact. A DX preference that is purely about readability (join, items, pathlib) belongs in a general Python style guide; this project should carry it only when it is also the faster or the only compiling form, which is exactly the admission rule below.

### Mechanism: what to add, in order

First choice is a docs page plus generated status data, and nothing else. The stable half is a short page (`docs/guide/idioms.md`) with the families, the preferred form and the reason, and no claim that "this works"; the volatile half is a JSON file generated from the probes, in the cadence class the docs policy already defines for nightly data (`compat/data/*.json`, `.agents/docs/doc-cadence.md`), carrying `generated`, `commit`, `command`, and one record per family and variant with `fences`, `planeMap` and the measured ratio. The page links to the data for the current status, so a lowering change updates the status without hand-editing markdown.

Second choice is `assess` hints, and only for rows where the preferred form clears a fence. The existing hint mechanism already does this for fences, so extending a hint to name the preferred form ("iterate with `range(len(items))` today; direct iteration over a typed list fences") is cheap, but it should not become general style advice: a diagnostic that says "prefer join" on code that compiles and is not hot would be noise.

Third choice is a `pyscriptc check --idioms` lint, and it is not worth building now. Ruff cannot host a plugin, so it would be a new analysis pass with its own tests and false-positive budget, duplicating the checker, for advice that is mostly "no preference"; the same information is a docs lookup plus the generated data.

The smallest first slice is therefore ten to twelve families under `benchmarks/idioms/`, one probe file per variant, with one generator that compiles each variant and writes `compat/data/idioms.json`, plus a short page that renders the preference column and links the data. No CLI surface, no lint, no new dependency, and the generator reuses `pyscriptc coverage --json` and `assess --bench` rather than adding a measurement path.

### Overfitting and versioning

A preference table is a snapshot of the compiler's current limits, so each row needs a reason and an expiry condition. The rule should be: a preference is admitted only when at least one form fences today (with the fence code in the row) or when the measured gap is at least 2x with a date and a host; otherwise the row says "no preference". That keeps TypeScript-style taste out and makes the table's claims falsifiable.

Rows must be keyed by family id, not by prose, and the generated JSON is the status of record; the docs page must never embed counts or dates, exactly as the documentation policy already requires for support claims. Any PR that changes lowering for a family should regenerate the data, which the nightly job can do for all families at once; the release gate already has a doc cadence check to extend if needed.

Concrete staleness already present in one day of notes is the argument: the port deltas note says unannotated module constants need PS1001 annotations and that keywords and `Sequence` need rewrites, and all three probes compile clean at this commit. The dataclass index-loop advice is the opposite case, a workaround that must be deleted the day `list[Item]` iteration lowers, and it should carry that condition in the row rather than reading as style.

## Uncertainties and limits

The measurements are from one shared 6-core Linux host at load 1.39, with 3 to 9 samples, so the ratios are directional and the millisecond figures should not be quoted as benchmarks; stage tables are single representative runs and varied by 10 to 20 percent between runs.

`perf`, valgrind, heaptrack, samply and Instruments were not available, so the user-code sampling path was verified structurally (DWARF line table via `readelf`, source interleaving and symbol lookup via `gdb`, exported island symbols via `nm` and `ldd`) but not end to end with a sampler. The `-fno-omit-frame-pointer` recommendation follows from the observed `-O2 -g` cc line and was not measured.

The string-copy finding is measured, not explained at the IR level: I verified it is the read of the local (`t = len(big)` in the loop is slow, a precomputed `n` is fast, not mentioning `big` is fast) but not which lowering rule inserts the copy, and it should be reproduced against the IR before anyone files it as a bug.

Per-definition island counts have no free external path, and marshal bytes can only be approximated by an allocation profiler, so the suggestion to wait for a counter is a judgement, not a measurement. Nested definitions and cross-module name collisions in the symbol table were not tested, so the plane-map join may need a mangling rule.

The idiom probes are minimal by construction and a real program can hit a different fence through the same construct (for example a dataclass field with a default or a `field(default_factory=...)`), so the table's "native today" column is about the smallest form that shows the choice, not a proof for every use.

The `TypedDict` and `NamedTuple` results are at this commit only; both are likely to lower later, and the row should be regenerated rather than trusted.

## Follow-ups this assessment implies

1. Fix the fence renderer: index the AST once per file instead of walking it three times per fence (`src/pyscriptc/lower/__init__.py:2879-2942`), which is a measured 840 ms to a few milliseconds on a 41-fence file.
2. Account for the hidden stages in `--timings`: `render_fences`, the IR and report sidecar writes, and a `check` entry that survives a failing preflight.
3. Add `--debug-info` to `run`, `verify` and `assess`, and pass `-fno-omit-frame-pointer` on debug-info builds.
4. Write the user page with the `perf`/samply/Instruments recipes and the plane-map join; document `ps_island_call` and `ps_island_fn_invoke` as uprobe targets.
5. Build the idiom probe suite and the generator, and add the docs page only after the generated data exists.
