# Compiler progress trend (refs #817)

`scripts/compiler_progress.py --markdown FILE` appends a trend table that CI
writes to the job summary (`$GITHUB_STEP_SUMMARY`) on pull requests (the
stage-2 fixpoint job) and on the nightly gates workflow. The step is
report-only: it never fails the job. The table ends with a reserved
`slice_coverage` row (batch 1, A12/D6 owns the metric; this tracker only
holds the slot).

## How to read the table

The first table lists one row per metric (`seam.counter`):

- `lines`: module line count in the seam (`lower/`, `mir/`, `emit/`).
- `dict_build`: hand-built `"kind":` node dicts, the lowering sites still
  dict based. These must trend down as slices land.
- `dict_read`: `["kind"]` dispatch sites, emitters still reading dicts.
  These must trend down too.
- `typed_use`: uses of the typed `_ir` nodes. This one must trend up.

Columns: `current` (the worktree being measured), `baseline` (the comparison
snapshot), `delta` (current minus baseline). Negative deltas on `lines`,
`dict_build`, and `dict_read` are progress; a positive delta on `typed_use`
is progress. Positive deltas on the first three mean the seam grew.

The baseline is the snapshot file passed with `--baseline FILE`, or a
snapshot computed from `git origin/master~20` when no file is given. Write
today's snapshot with `--snapshot FILE` so a future run can diff against it.

The second table splits the self-host fence pins from
`tests/census_pins.json` into seams (`lower`, `emit`, `mir`, `cli`, `other`)
by module name prefix. Fences must trend down; `other` typically dominates
because modules outside the three stage seams still sit on the
not-yet-lowered AST layer.

The third table is MIR leaf-slice coverage from `scripts/mir_coverage.py`:
in-slice over non-fenced IR functions on the visible corpus scripts
(`benchmarks/compat2/corpus/scripts` only). `mir.percent` must trend up as
the slice grows; `mir.in_slice` and `mir.functions` are the raw counts.

## What moves it

- Landing a lowering slice that converts dict nodes to `_ir` typed nodes
  moves `lower.dict_build` down and `lower.typed_use` up.
- Rewriting an emitter dispatch to read typed nodes moves
  `emit.dict_read` down.
- Repinning the census (`uv run python scripts/repin_census.py`) moves the
  fence table; per-module pins are ceilings, so a run at a ceiling shows
  delta 0 until the fence is actually removed.
- Growing the MIR leaf slice (`supported_fn`) moves `mir.in_slice` and
  `mir.percent` up. Measure with `uv run python scripts/mir_coverage.py`;
  do not hardcode the percentage.
