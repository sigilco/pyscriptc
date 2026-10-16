# Self-host baseline

Baseline for the S1/S2/S3 self-compile track, measured on the post-E0-move-1 tree (master e5437b6, 2026-10-16). Two instruments, both regenerable:

- `python -m benchmarks.compat2.census_self --json benchmarks/compat2/census-self.json` — parse-only fence census (`pyscriptc coverage` on `src/pyscriptc`). The `ranking` key orders fence families by unblocked-modules payoff and is the iteration plan.
- `python -m benchmarks.compat2.selfhost_matrix --json benchmarks/compat2/selfhost-matrix.json` — deepest stage per module, single-file `pyscriptc compile` at `--emit ir`, then `llvm`, then `bin --call main` where a `def main` exists. Records the first PS code or stop detail. This is also the S2 substrate: when a built backend exists, the same per-module ladder drives the byte-identical-IR comparison (`canonical.py`/`irscore.py` are the comparator).

## Current numbers

Census (post-E0 layout, 18 modules): ~642 fences, 497 definitions, zero clean modules except the package root. The committed JSON is now the baseline; `lower.py` shows as `lower/` (`__init__` 260, `_stdlib` 27, `_stdlib_shared` 5, `_core` 6).

Emit matrix (single-file): 1 module reaches `ir` (the empty `pyscriptc` root, which then reports PS0001 no-functions), 12 fence on the first stage, 5 stop at the type checker (`llvm`, `lower/__init__`, `_stdlib`, `restlower`, `stdmore` — cross-module attr/misc errors in single-file mode, e.g. `StdlibDispatchMixin has no attribute filename`, `expr has no attribute id`). Nothing reaches `llvm` or `bin`.

## Fence families, feature vs rewrite

Each family gets one lane: implement the feature where the subset genuinely lacks it, or move the compiler's own source toward the subset where the idiom is optional. Verdicts from the census plus emit probes:

| family | fences | lane | verdict |
| --- | --- | --- | --- |
| dynamic-annotation | 235 | mixed | In `lower/`/`restlower`/`stdmore` mostly `Any`-typed signatures (PS1104) and un-inferable locals. `Any` is a feature gap (no `object`/top type in the IR), not annotation hygiene; inferable locals are the only rewrite lane, thin. |
| unlowered-type-name | 181 | feature | Almost all in `lower/`: annotations naming `ast.*`, `Path`, `argparse.Namespace`, own cross-module types. Needs real type surface (E3 structural types, importable own modules), not rewrites. |
| construct-other | 57 | feature | Constructor/lower forms (dataclass construction shapes). E3 territory. |
| name-resolution | 32 | feature | Module globals, comprehension scoping, generator locals (e.g. `ir.py` `all(part.isidentifier() ...)`). Scoping features, no rewrite lane. |
| union-types | 25 | feature | E3c tagged unions; already on the epic schedule. |
| local-inference | 19 | feature | Inference improvements inside lowering; also the family most likely to shrink free when E3 lands. |
| import-table | 12 | feature | `ast`, `copy`, `argparse`, `collections.Counter`/`defaultdict`, `pyscriptc.*` self-imports. Wave C stdlib families + the self-import seam; explicitly on schedule after E0+F. |
| decorators | 3 | feature | E5 known decorators. |
| parameter-forms | 3 | feature | E5 kw-only/` *args`/defaults. |
| unlowered-call | 5 | feature | Constructor-style calls (e.g. `Diagnostic(...)`). |
| field-lookup | 3 | feature | Attribute access on own exception types (E2/E3b). |
| remaining tail | <20 | feature | with-as, generic-alias, module-assign/value, operator, type-mismatch, stdlib:collections/sys. |

The expected "rewrite the compiler toward the subset" lane is thin: almost every fence is a real feature gap, so self-compile progress tracks the epic schedule, not source hygiene. The one hygiene lever left is keeping *new* compiler code inside the subset as it is written (the census diff is the check), which costs nothing and keeps the baseline from drifting.

## Sequencing against the plan

- Now (E0 window): this baseline only — no `src/` edits. `lower/` is E0's write track and E0's "next 3 features" median-diff sample must stay clean.
- After E0 lands: E1 (module-level statements, `print`, `sys.argv`) and Wave F seams attack the second wall the census cannot see — `cli.py`/`package.py` need argparse, fs, json, subprocess to ever *run*, not just parse. `subprocess.check_output` island is already proven (C11).
- S1 (after E3b): leaf modules `errors.py`/`ir.py`-class join the corpus as a `self` area, one probe per definition. E3b (`super`, single inheritance) and E3c (unions) are the dominant unblockers in the emit matrix today; E2 (exception hierarchy, `str(e)`, args) covers `errors.py`/`check.py`.
- S2 (after E6): frontend/backend split; the emit matrix plus `irscore --baseline` becomes the byte-identical gate.
- S3: full self host, research.

Watch item: the 5 checker-level stops in the matrix are single-file-mode artifacts (mixin attrs resolve only on the composed `Lowerer`, cross-module types unresolvable without the package). If S1 wants single-file semantics, the `self` area needs either package-mode probes or a census-style resolver; record that decision when the area is added.
