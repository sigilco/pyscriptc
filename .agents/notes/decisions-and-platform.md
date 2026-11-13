# Decisions and platform notes

Dated session notes were deleted in the 2026-10-28 cleanup when git history, the CHANGELOG or the code carries the content. The remaining durable decisions and platform facts are grouped by topic here.

## Windows tier

Decision (2026-10-23, owner, recorded in #285): Windows stays at the explain/coverage tier. A Windows user can validate that a workload can compile (`explain`, `coverage`, cross-compilation to a Windows target via zig from a macOS or Linux host) but cannot compile-and-run a native program on Windows. The tier extends only when the interest gate fires on telemetry: primary gate is a 5 percent Windows share of non-CI `compile.finished` events over a trailing month; leading indicator is Windows `explain` invocations above 10 percent of sessions, which pulls the port forward on its own. Tickets #419 (the `win.explain` counter) and #420 (a named diagnostic instead of MinGW header noise) make the gate measurable. Extending the tier is per-feature cost (the #286 semantics pass, the #284 and #288 smokes, #289 arm64, #290 wheel statement); the demand on the numbers (roughly 1 to 2 percent across the tracked packages) does not justify it yet.

## Generator `with` semantics

Decision (2026-10-24, issue #433, G1): when a generator suspends inside a `with` block, the context manager's `__exit__` does not run at the suspension point. The generator resumes inside the `with` body, and `__exit__` runs once when the `with` body completes or the exception leaves it. This matches CPython 3.14 (a two-yield `with` prints `enter`, `1`, `2`, `exit`), measured against the oracle. Landed in `src/pyscriptc/coro.py`, where the suspension skips the `__exit__` half of the desugared `try`.

## Keyword argument lowering

Decision (2026-10-25, branch `work/kwmech`, refs #592): keywords stay call-site bound (`_fill_args` and `_fill_varargs` in `src/pyscriptc/lower/_calls.py`), so a keyword call compiles to a positional call with defaults filled in, with evaluation order pinned in written order via `_pin_arg_order`. Rejected: a parameter object (aggregate plus presence bitmask, a new calling convention that also breaks the island crossing) and a dict or hash-table pass (the CPython model; heterogeneous dict values have no type in the subset). Per-call-shape specialization wrappers are the fallback only if a port needs a keyword call through a function value. The measured demand was two stdlib keyword calls, already fixed in the compiler.

## Library receipt deferral

Decision (2026-10-23, owner): the library receipt is two minors, not one. The v0.7.0 exit criterion is the weak receipt: a program depending on a pure-Python PyPI package compiles natively and matches CPython, with `certifi` as the target (landed, #561). The strong receipt (#415) waits for the minor that carries the typing surface, because the typing fence family is the largest one in the corpus and deserves its own exit criteria instead of widening v0.7.0. Rule: ship the strongest receipt the release can actually deliver, not the strongest one the theme implies.

## IR_HASHES regeneration platform

Decision (2026-10-22, #211): `benchmarks/compat2/probes/IR_HASHES.json` is generated only on Linux (ubuntu-24.04, CPython 3.14.x) by `.github/workflows/ir-hashes.yml`; PRs touching `src/pyscriptc/**`, the probes, `canonical.py` or `irscore.py` compare against it (not a required check). The old macOS-generated baseline was unusable: canonical IR folds host constants (socket, errno, os, ssl, time, sys, ctypes, pathlib), and on macOS even the baseline's own commit differed by 458 hashes. Never run the regeneration by hand on a macOS checkout; the workflow is the only path.

## Island floor granularity

Decisions (2026-10-25, refs #597, #628, #641, #677; grounded in the typed-subset positioning of the same day):

- Decision A: the fallback unit is the definition, not the statement. Module top level is all-or-nothing. Rejected per-statement bailout because it needs a dynamic value type for locals live across the bail, and `Any` and unions are refused by design (PS1104, PS1101); per-module-only loses the partially-native scripts for no design saving.
- Decision B: typed functions stay native, untyped ones run under CPython island-side. Native-to-island calls follow the one-way seam rules (handle ownership at scope exit, no object identity across the boundary). Island-to-native stays out of scope for v0.7.0: an island function calling a compiled sibling fences with a diagnostic naming both functions and the `--emit binding` seam.
- Decision C: the flag surface is `--fallback=cpython` (the CLI's `--island` stays as an alias); policy is `[tool.pyscriptc.imports]` plus `[tool.pyscriptc.fallback] definitions = true`. The boxed ledger and its drop-flag thresholds keep arbitrating island growth.
- Decision D (user surface): `pyscriptc assess` reports the plane map per definition; `pyscriptc run` prints which definitions went island and why.
- Decision E (semantics): island entry takes the GIL on every call, so native threads serialize there; island functions inherit CPython semantics exactly, adding none. Deterministic output makes `pyscriptc verify` diffs attributable.
