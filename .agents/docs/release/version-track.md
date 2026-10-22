# Version track V: `--target-version` design

Wave-plan reference: `.agents/plans/2026-10-14-waves-after-scorer-v2.md`,
"Version track V". Goal: `--target-version {3.10..3.14}` (default 3.12) gates
syntax per version and the stdlib table by typeshed `VERSIONS`, and the
scorer gains one column per version weighted by the market snapshot.

Survey tool: `benchmarks/compat2/version_surface.py` (read-only, stdlib only).
It parses every file in the entrypoint scan set with
`ast.parse(feature_version=...)` for each target and walks the newest-grammar
AST for gated node kinds. Findings from the first survey:

- 46 files scanned (26 examples: 3 scripts / 23 modules; 20 corpus scripts),
  matching the entrypoint spec tables.
- 45/46 files parse at 3.10. The only gate is `examples/gates_probe.py`,
  which uses unparenthesized `except A, B:` (3.14 grammar).
- No file in the scan set uses `type` statements, `except*`, t-strings,
  match statements, or PEP 695 generics. The syntax surface today is flat;
  the version axis pays off through stdlib coverage, not syntax rejection.

## a. Node-gate table per version

`feature_version` rejects grammar-level syntax with a descriptive
`SyntaxError`, but soft keywords (`match`) and post-3.10 grammar additions
still parse at older features or produce generic nodes. The gate therefore
needs two layers, both already proven in `version_surface.py`:

| Version | Grammar gate (`ast.parse` rejects) | Node gate (AST kind → fence below) |
| --- | --- | --- |
| 3.10 | match statement is a soft keyword: always parses; gate on `ast.Match*` nodes | `Match`, `MatchValue`, `MatchSingleton`, `MatchSequence`, `MatchClass`, `MatchStar`, `MatchMapping`, `MatchAs`, `MatchOr` |
| 3.11 | `except*` ("Exception groups are only supported in Python 3.11 and greater") | `TryStar` |
| 3.12 | `type X = ...` ("Type statement is only supported in Python 3.12 and greater"); PEP 701 f-string grammar (nested same-quote reuse, arbitrary nesting) | `TypeAlias`, `TypeVar`, `ParamSpec`, `TypeVarTuple` |
| 3.13 | none (no grammar additions) | none |
| 3.14 | `t"..."` t-strings ("t-strings are only supported in Python 3.14 and greater"); unparenthesized `except A, B:` | `TemplateStr`, `Interpolation` |

Notes and limits:

- `feature_version` floors vary by running interpreter (3.14 accepts
  `(3, 10)` through `(3, 15)`). The compiler only needs 3.10 to 3.14.
- Annotation semantics are not grammar: `X | Y` unions parse everywhere in
  range; the 3.10 boundary for them is a typeshed/typing question, handled
  by the stdlib gate, not the syntax gate.
- Walrus (`NamedExpr`, 3.8), positional-only params (3.8) and f-string `=`
  (3.8) predate the 3.10 floor and need no gate.

## b. Typeshed `VERSIONS` integration plan

Table source: `stdlib/VERSIONS` inside the mypy typeshed checkout, located
via `PYSC_TYPESHED` or the installed `mypy` package
(`benchmarks/compat2/probes/typeshed.py::stdlib_dir`). The reader
(`module_versions`, `available`) already exists on the benchmark side and
is how `probes/generate.py` filters modules for the oracle. Reuse it; do
not fork the parser.

Where the gate lands in the compiler:

- `lower/_stdlib.py::_bind_import` / `_bind_from` currently fences unknown
  modules with PS2002. The version check inserts one step earlier: a module
  that exists in the dispatch table but is absent from `VERSIONS` for the
  target gets a dedicated fence (proposed code PS2-vtag, wording fixed at
  freeze) instead of a generic unsupported-module fence.
- `_stdlib*.py` call dispatch consults the same table for names added
  mid-range (for example `math.nextafter` 3.9, `tomllib` 3.11,
  `annotationlib` 3.14, `string.templatelib` 3.14): name-level spans come
  from `if sys.version_info` branches inside stubs, which
  `probes/typeshed.py` already resolves for `PYSC_ORACLE`. Extend the same
  resolution to `--target-version` so a stub branch newer than the target
  reads as absent.
- Resolution entry point: one helper `stdlib_version_ok(name, target)`
  living next to the dispatch table, cached per target. `PYSC_TYPESHED`
  stays the override; absent a typeshed checkout the gate degrades to the
  module-level `VERSIONS` check only and logs once.

Staleness policy: typeshed tracks released CPython; spans for 3.14 names
can lag a point release. The gate treats `VERSIONS` as advisory-exact for
modules (a missing span means absent) and report-only for name-level spans
inside stubs (a stub branch the reader cannot date falls back to
"exists"). Re-pin the mypy typeshed snapshot at each freeze boundary; the
pin lands in `pyproject.toml` like any other dev dependency, so a bump is
an ordinary PR, not a freeze event.

## c. Scorer column design

`score.py` is frozen: weights change only via the sanctioned freeze
process. The per-version column is therefore additive tooling that sits
beside the scorer until the next freeze absorbs it.

Inputs that already exist:

- `score.py` emits `results["versions"]`: per-axis-version
  `{probes, counts}` tallies from `probe.versions` (C4, #27), and
  `merge_results` already rejects mixed-oracle shards.
- Probe rows carry `versions` (the subset of 3.10 to 3.14 where the name
  exists and the oracle agrees), written by generation.

Column design (new file, e.g. `benchmarks/compat2/version_column.py`):

- Read a scorer results JSON plus `benchmarks/compat2/market/2026-10-14.json`
  (to be committed by the market-snapshot task; schema: version → weight,
  summing to 1 across 3.10 to 3.14, ordered like the plan's version order
  3.12, 3.14, 3.11, 3.13, 3.10).
- For each version v, `weighted_pass(v) = sum(weight[p] for p in probes
  where v in p.versions and row passed)`. The column is a re-weighting of
  rows the scorer already emitted; it never re-runs probes.
- Output appends a `market` block to the results JSON shape:
  `{"market": {"snapshot": "2026-10-14", "columns": {"3.10": x, ...}}}`.
  `merge_results` ignores unknown top-level keys today, so the block is
  safe to merge through existing tooling.

At the next freeze the column moves into `score.py` proper as one column
per version and the additive tool is deleted.

## d. Milestone ladder

| Milestone | Gate |
| --- | --- |
| V0 survey + design (this PR) | `version_surface.py` run is byte-identical across runs; scan counts reconcile with the entrypoint tables; doc lands |
| V1 CLI plumbing | `pyscriptc compile --target-version {3.10..3.14}` accepted, default 3.12, stamped into the IR next to `cpython`; `--target-version 3.11` rejects `type X = int` with a PS code and 3.12 accepts it |
| V2 syntax gate | node-gate table live in the checker; a corpus fixture per gated feature fences below its version and lowers at it |
| V3 stdlib gate | `stdlib_version_ok` wired into `_bind_import`/`_bind_from` and name dispatch; `import tomllib` fences at 3.10, lowers at 3.11 |
| V4 scorer column | additive `market` block emitted for one results JSON; freeze absorbs it into `score.py` |
| V5 sweep | a `--target-version` sweep over `probes/` shows per-version columns in the score JSON (plan exit criterion) |
