---
title: First-class scripts
description: The first-class script bar and beta-readiness checklist measured by the script corpus.
---

pyscriptc targets CLI scripts and automation tools written in the typed subset of Python 3.14.
This page defines the first-class script bar: the concrete criteria a script must meet to be considered first class, the falsifiable corpus metrics that measure progress toward that bar, and the commands used to verify compliance.

In accordance with the documentation policy (decision 2026-10-23), user documentation states what fails or diverges rather than maintaining static feature lists of what works.
Live support status across modules and language features lives on the [compat site]({{PYSC_COMPAT_URL}}).
Specific compiler fences measured on real scripts are documented in [What will fail](what-will-fail.md), and runtime differences from standard CPython are cataloged in [Divergences from CPython](divergences.md).

## Positioning: the typed subset

The compiler's scope is strictly the typed Python subset:

- **Island is the floor**: when a script touches untyped third-party libraries, dynamic reflection, or unlowered modules, execution routes through the CPython island runtime (`libpython`).
- **Native is the ceiling**: when a script uses lowered standard-library modules, explicit annotations, and scalar constructs, it compiles to a standalone native binary with no interpreter runtime.
- **No parity goal for untyped Python**: pyscriptc does not aim for byte-for-byte CPython parity across arbitrary untyped Python programs.
- **No compiler shims for untyped code**: the compiler introduces no runtime shims, dynamic type-coercion layers, or speculative interpreter emulators to make untyped code compile natively. Code outside the typed subset stops cleanly at a named fence.

## The first-class script bar

A Python script is first class when all five of the following criteria hold:

1. **It compiles without workarounds.**
`pyscriptc compile script.py` exits 0 with zero fences. Compilation succeeds without passing `--skip-check`, without disabling type preflights, and without special CLI flags.
2. **It matches CPython on what lowers.**
The compiled binary's stdout, stderr, and exit code match CPython 3.14 on identical invocations and inputs, byte for byte for lowered constructs (subject only to the numbered entries in [Divergences](divergences.md)). In the typed subset, native compilation is the ceiling and island fallback is the floor; a divergence outside documented differences is treated as an actionable defect.
3. **Diagnostics name the exact gap.**
When a script does not compile natively, the diagnostic names the exact construct, module, and source location (for example, `PS2001: os.walk is not in the lowered subset of os`). The compiler never produces generic parse failures or silent miscompilations. Running `pyscriptc explain <CODE>` explains what caused the fence and shows the recommended typed idiom.
4. **The install and build story is one command.**
Installation requires a single command (`pip install pyscriptc`), and compiling a script requires one command (`pyscriptc compile script.py`). A native binary requires no host CPython runtime, no virtual environment, and no external toolchain beyond the bundled wheel components.
5. **Performance is predictable.**
The native binary starts immediately without interpreter initialization overhead and runs at or near CPython's execution speed for the script's shape. Performance claims are verified per workload shape (CLI argument parsing, JSON processing, string transformation, process execution) rather than claimed as a blanket multiple.

## What first-class covers

The first-class bar targets single-file and small multi-file CLI scripts, automation utilities, and system commands that use:

- **Builtin operations**: core arithmetic, comparisons, string operations over Unicode code points, collection literals (`dict`, `list`, `set`, `tuple`), exception handling, and standard file I/O.
- **Standard library CLI modules**: `argparse`, `sys`, `os`, `pathlib`, `shutil`, `subprocess`.
- **Data formats and serialization**: `json`, `csv`.
- **Utilities and algorithms**: `re`, `datetime`, `logging`, `urllib.request`, `dataclasses`, `typing`, `itertools`, `functools`, `collections`.

External graphical frameworks, database drivers (such as `sqlite3`), numerical arrays (`numpy`), and heavy ORMs/frameworks are outside the script bar.

## Verification commands

Three commands in the pyscriptc CLI measure and verify compliance against this bar:

### `pyscriptc assess`

```sh
pyscriptc assess PATH [--json] [--all] [--bench] [--verify]
```

Assesses a script, package, or directory against the compiler ladder without crashing, regardless of fence count. It reports:
- The lowering status (native, island, or blocked) per module and definition.
- Root causes for fences with the nearest typed idiom to clear each fence.
- Island routing recommendations via concrete `[tool.pyscriptc.imports]` policy entries.
- Optional benchmark ratios against CPython 3.14 (`--bench`) and inline differential verification (`--verify`).

### `pyscriptc verify`

```sh
pyscriptc verify FILE [--call FN] [--args '[...]'] [--input FILE] [--json]
```

Executes the compiled binary and the pinned CPython 3.14 oracle side by side under identical argv and stdin, checking stdout bytes, stderr bytes, and exit code. Emits a differential receipt proving whether execution matches or diverges.

### `pyscriptc imports`

```sh
pyscriptc imports SOURCE [--json]
```

Classifies every imported module into a rung on the capability ladder: `native`, `native: fences (PS20xx)`, or `island (requires libpython)`. Reflection culprits (such as `inspect.signature` or runtime `__annotations__`) are identified so dependencies can be cleanly routed to the island floor.

## Corpus measurements

The bar is falsifiable: progress is measured continuously against two corpora of scripts without manual edits to make them pass.

### R0 real-scripts corpus (`benchmarks/real_scripts/`)

The R0 corpus consists of 39 real-world third-party CLI scripts vendored byte-identical from open-source repositories (including psf/black, pypa/pipx, jazzband/pip-tools, and CPython `Tools/scripts`) at pinned commits.
Scripts are tested as written, ensuring fences reflect genuine compiler gaps rather than artificial test harnesses.

Baseline measured on commit `5860541e` (2026-10-24):
- **Pass rate**: 0 of 39 scripts lower with zero fences (0%).
- **Total fences**: 386 fences across the corpus.
- **Primary blockers**:
  - `PS1001` unannotated variable and parameter assignments: 23 scripts.
  - `PS2003` call shapes, keyword arguments, and arity mismatches: 6 scripts.
  - `PS2002` imports outside the closed import table (`platform`, `calendar`, `zipfile`, `sqlite3`): 6 scripts.
  - `PS2001` unlowered standard-library functions (`os.walk`, `json.load`, `time.perf_counter`): 6 scripts.

### R1 script corpus (`benchmarks/scripts_corpus/`)

The R1 corpus consists of 45 single-file CLI scripts written to the lowered typed shape, exercising common automation tasks.

Baseline measured on commit `b42a7379` (2026-10-22):
- **Compile pass rate**: 15 of 45 scripts compile with zero fences (33%).
- **Total fences**: 116 fences across the corpus.
- **Execution verification**: 12 match CPython byte-for-byte, 3 differ due to `sys.exit(main())` double evaluation (issue #183), 0 fail to build.

## What fails and diverges

Rather than listing what works, the table below summarizes the primary failure modes and divergence boundaries encountered by scripts:

| Category | Typical fence / symptom | Reason | Verification / Guidance |
| --- | --- | --- | --- |
| Untyped assignments | `PS1001: needs an annotation` | Variable assigned without type annotation or inferrable literal RHS | Add type annotation (`x: int = ...`) or run `pyscriptc explain PS1001` |
| Unlowered stdlib calls | `PS2001: <call> is not in the lowered subset of <mod>` | Function not yet lowered to native IR (e.g. `os.walk`, `json.load`) | See [What will fail](what-will-fail.md) or route module to island |
| Unadmitted imports | `PS2002: <mod> is not in the closed import table` | Module is outside the native standard library table | Inspect via `pyscriptc imports` or configure `[tool.pyscriptc.imports]` island policy |
| Keyword argument shapes | `PS2003: keyword argument <name> is not lowered` | Native stdlib signature does not accept keyword argument reordering | Pass arguments positionally or check signature in `pyscriptc explain PS2003` |
| Dynamic reflection | `PS2001: inspect.signature` / `getattr` | Introspection cannot be lowered to static native structures | Identified by `pyscriptc imports`; requires island execution floor |
| Numeric overflow | Process abort on `i64` overflow | `int` is represented as native `i64` rather than arbitrary-precision bigints | Documented in [Divergences item 1](divergences.md) |
| Division by zero | `ZeroDivisionError` message variance | Runtime matches 3.14 division-by-zero error semantics | Documented in [Divergences item 2](divergences.md) |
| Unicode code point model | Surrogate handling variance | `str` model uses UTF-8 code-point buffers; lone surrogates fence | Documented in [Divergences item 3](divergences.md) |

## Beta-readiness checklist

Before the beta release opens to general users, the first-class bar requires the following verification checklist:

- [ ] **R0 corpus unlocks**: inference and call-table epics (tickets #500-#502) land, moving R0 pass rate from 0 to at least 23 of 39 scripts.
- [ ] **R1 execution soundness**: issue #183 resolved so 100% of compiled R1 scripts match CPython without output doubling.
- [ ] **Gap documentation complete**: every non-derived fence code emitted by the R0 corpus is indexed in [What will fail](what-will-fail.md) and explained via `pyscriptc explain`.
- [ ] **Automated verification**: `pyscriptc assess` and `pyscriptc verify` run cleanly on CLI scripts without unhandled compiler exceptions.
- [ ] **Self-contained wheels**: standalone binaries compile and link with `zig cc` / `clang` without requiring an active CPython virtual environment.
