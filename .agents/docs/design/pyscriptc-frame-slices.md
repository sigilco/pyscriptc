# pyscriptc-frame: slices (issue #497)

The columnar frame surface, the pandas tier (T3). The surface and its
deliberate exclusions are the issue's; this note only splits the
acceptance criteria into slices a single ~3 hour run can land, in the
ndarray pack's shape (#496): a `restlower/_frame.py` subset, a runtime
`ps_frame.c` over the existing `ps_ndbuffer.h` columns, a pack under
`packaging/adapters/pyscriptc-frame/`, tests, and differential probes.

## The substrate decision (slice 0, already decided)

A frame column is a `pyscriptc_ndarray` buffer for numeric columns; the
frame is a `PsMap` (str -> column) in the runtime plus a row count. The
IR type of a frame carries its schema in the class label
(`DataFrame[a=float64,b=int64]`), the way the ndarray label carries its
dtype: the label is the one part of a class type the dict IR round trip
keeps. Column access and dtype knowledge are therefore compile-time;
a name outside a frame's schema fences, it never guesses.

Restrictions slice 1 states honestly (each lifts in a later slice, and
each fences by name until it lifts):

- `DataFrame(...)` takes a dict **display literal**; the closed IR
  container vocabulary cannot type a dict whose values are heterogeneous
  buffer types (the issue names this gap), so the construction site
  lowers each column from its own display.
- Numeric columns only (`list[int]`, `list[float]`) in slice 1; `str`
  columns arrive with `read_csv` in slice 2.
- `df["col"] = values` replaces an existing column of the same dtype in
  slice 1; adding a column changes the schema label and waits for the
  schema-rebind slice.
- `to_dict()` needs a homogeneous frame (one value kind across the
  columns) while the dict IR type is closed; `orient="records"` rides
  the same restriction in slice 2.

## Slices

### Slice 1 — the frame core (one run)

`DataFrame({...})` from a dict display of numeric columns; `df["col"]`
(an ndarray), `df["col"] = values` (replace, same dtype), `df.columns`,
`df.shape`, `len(df)`, `df.head(n)`, `df.tail(n)`, `df.to_dict()` on a
homogeneous frame. Runtime `ps_frame.c` over `ps_ndbuffer.h`. Pack
skeleton `packaging/adapters/pyscriptc-frame/` (identity block, imports,
`[[ffi.fn]]` rows for the slice's calls, vendored stub
`surface/pyscriptc_ndarray`-style `__init__.pyi`). Tests
`tests/test_stdlib_frame.py` in the `test_stdlib_ndarray.py` shape
(compile-run against the CPython-lists oracle, fence cases naming
`pyscriptc_frame`). Probe `probes/differential/frame_subset.py` spelled
in pandas vocabulary so it runs unchanged under real pandas.

Wiring follows the ndarray precedent: `PACK_MODULES`, the stdmore
module/class/alias/member tables, the restlower dispatch row, the
`_call_attr` method routing, the `_classes` attribute routing, the
subscript and setitem branches, the `len` builtin branch, the emitter's
module row (generic `_emit_abi`; the frame calls need no custom
emission), and `DataFrame` in the emitter's pointer-carrier list.

### Slice 2 — IO and str columns (one run)

`str` columns (a `PsSeq`-backed column kind beside the ndarray one),
`read_csv(path)` over the closed `csv` surface, `to_csv(path)`,
`to_json(path)`, `to_dict(orient="records")` (homogeneous-value
restriction still stated). Conformance suite
`conformance/suite.toml` in the pack-format shape (section 10), the
ndarray suite's twin, with the expected values generated from real
pandas under CPython.

### Slice 3 — order, groups, receipt (one run)

`sort_values("col")` and `ascending=False`, the boolean-mask filter
`df[df["col"] > 0]`, `groupby("key").sum()` (one key, one aggregator,
group order = first appearance, pandas'), `describe()` over the numeric
columns, `df["col"] = values` for a new column (schema rebind). The perf
receipt: compiled groupby vs CPython pandas on >= 100k rows, factor
recorded in the milestone description. `docs/supported.md` and the
census in the same PR; the pack gains `replaces = "pandas"` wiring in
`native_packages.json` if that file is the mapping point by then.

### Fence coverage (continuous, verified per slice)

Every acceptance criterion's last line is a fence, so each slice lands
its fence tests in the same PR: every name outside the current subset
fences with `PS2001` naming `pyscriptc_frame` (`merge`, `join`,
`concat`, `pivot`, `melt`, `apply`, `resample`, `Categorical`, `loc`,
`iloc`, `MultiIndex`, multi-key `groupby`, aggregators other than
`sum`), and every argument outside the subset fences with the
`PS100x`/`PS200x` code the ndarray tests use.

## PR shape

One PR per slice against master, milestone v0.11.0, `Refs #497`
(`Closes #497` only when slice 3's receipt and the docs/census land and
every criterion is met), owned files declared in the body.
