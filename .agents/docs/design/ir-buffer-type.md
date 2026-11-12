# IR buffer type: design for a first-class contiguous numeric array

Issue #372 (Future milestone). Consumer: pyscriptc-ndarray #496 (M2 substrate).
Status: design draft plus the stage A type-plumbing slice (landed in the
same PR). The stage A slice touches no frozen gate and moves no digest:
the frozen baselines stay byte-identical, verified with the A/B
`cmp` ritual on the visible corpus. **Stage B still needs the recorded
decision** of §7 before any code lands.

## 1. Summary

A new IR type kind, `buffer`, parameterized by a numeric element type:

```
{"kind": "buffer", "elem": {"kind": "float"}}
```

Element kinds at first landing: `int` and `float` only (mapping to the
`#496` dtypes int64/float64; int32/float32 need narrowing ops and are a
follow-up). `bool` element (the comparison-result array of #496) is the
third candidate but rides later — it is a `buffer` with `elem: bool` and
needs no new machinery, just tests.

The value is a runtime pointer to a flat, contiguous, length-prefixed cell
array — the same shape `list` already has (`PsSeq` in `runtime/ps_rt.h`)
minus the per-element box indirection for scalars, which `PsSeq` already
avoids (`ps_seq_new(elem_size, n)` stores raw doubles by value). The real
difference is at the IR/emit layer: elementwise arithmetic lowers to IR
operations over the buffer (vectorizable), not to `ps_seq_*` calls per
element.

## 2. IR shape

- `TYPE_KINDS` gains `"buffer"` (`src/pyscriptc/ir.py:24`).
- `validate_type` gains an elif: `buffer` requires `elem`, and `elem` is
  restricted to `{"int", "float"}` (later `bool`). A nested `buffer`,
  `list`, `str`, ... element is an `IRError` — buffers are flat by
  definition; multi-dimensional arrays are `#496` surface (reshape) over a
  flat buffer, not nested IR types.
- `type_key` gains a branch: `buffer[<elem key>]` — without it
  `types_equal` would conflate `buffer[float]` with `buffer[int]`
  (`ir.py:125-151`).
- `FORBIDDEN_KINDS` is untouched. Note `ir.py` already rejects `"array"`
  as a JS kind; the name `buffer` avoids that collision on purpose.

Typed records (`src/pyscriptc/_irnode.py`): no dataclass change. `T`
already carries `elem`; the `docstring` even names `buf` as a leaf kind.
Add a `t_buffer(elem: T) -> T` factory beside `t_list`. The existing
`sigregistry.py` `_BUFFER_ALIASES` → `{"kind": "buf"}` leaf (used for
`bytes`-accepting stdlib rows) stays a separate thing; either it retires
into `buffer` later or keeps its binding-layer role — decided in the
implementation PR, not here.

Expression/statement vocabulary: **closed, no new expr kinds in the first
slice.** A buffer is created, read and written through the existing
`call`/`subscript`/`setitem` nodes with a buffer-typed value; elementwise
binary ops are lowered as explicit loops over `subscript`/`setitem` in the
first slice, and the vectorized fast path (see §4) is where new MIR ops
would come in, behind the parallel-PR fresh-id-block rule.

## 3. Interaction with the closed container vocabulary

The vocabulary (int, float, bool, str, bytes, list, dict, handle) stays
closed for *values that cross the FFI manifest*; `buffer` is an IR-internal
numeric substrate, like `class` is an IR-internal composite. Concretely:

- FFI manifests do not gain `buffer` in the first slice.
  `ffi_manifest.type_ir` (`ffi_manifest.py:937-988`) and
  `ALLOWED_TYPE_NAMES` (`:90`) are untouched; an adapter that wants to pass
  a buffer across the manifest boundary is a separate decision. This keeps
  the closed FFI vocabulary contract intact and the acceptance criteria of
  #372 satisfiable without it.
- Conversions at the edges, spelled as ordinary calls:
  - `bytes` ↔ `buffer` via the `#496` surface (`tobytes()`, `frombytes`)
    — bytes stay the interchange type, exactly the M1 backing.
  - `list[float]` ↔ `buffer` via `tolist()` / `array(list)` — same.
- `handle` borrow/consume discipline (PS4010, `lower/__init__.py:245-255`,
  fence at `lower/_expr.py:307-315`): buffers are **owned values, not
  handles**. A buffer assigned to a local is freed at scope exit like any
  container; `buffer` values are copy-on-assign at the IR level only in the
  sense that the lowerer inserts no aliasing — mutation through one name is
  observable through aliases exactly like a `list` today. The #372
  acceptance criterion "ownership rule that matches the existing handle
  borrow/consume discipline" is read as: a buffer *passed across an FFI
  boundary as a borrowed argument* (future slice) rides the same
  `borrowed_handles` machinery, and use-after-consume fences with PS4010.
  Nothing in the first slice crosses that boundary, so no PS4010 change is
  needed to land the type.

## 4. Lowering and emission

Two stages, deliberately split:

**Stage A (type plumbing, the first compilable slice).** `buffer` is a
valid IR type that rides existing pointer machinery everywhere:

- `emit/_ir.py:_llvm_type` → `ptr` (one branch); `_memory_type`,
  `_elem_size` → the `PsSeq` header treatment (same as list: header 8-ish
  + cell array; exact sizes reuse the list path).
- MIR: `mirlower._param_tag` (`mirlower.py:1507`) and its mirror
  `miremit.py:1971` both map `buffer` → the pointer tag `MIR_P`. Per the
  parallel-PR rules, **no new MIR op family** in stage A; buffer values
  only appear in slots/params, never in fast-slice arithmetic
  (`mirlower.supported_type` returns `False` for `buffer`, so the MIR fast
  slice excludes it — safe default).
- The lowerer's per-kind dispatch sites (`lower/_core.py:_show/_zero/
  _subst_type`, `_infer.py:_pin_annotation`, for-loop binding in
  `_stmt_b.py`, subscript in `_expr.py:676-697`) get explicit `buffer`
  arms where a value of the type can flow; everywhere else the existing
  unknown-kind fences (`PS2002`/`PS2004`) fail closed. The stage-A test
  surface is: a buffer can be declared, created by a stub runtime call,
  passed to and returned from a function, and `len()`'d — with everything
  else fencing.
- Runtime: a minimal `ps_buf_*` helper set in `src/pyscriptc/runtime/`
  (`new(elem_size, n)`, `get`, `set`, `len`) — separate symbols from
  `ps_seq_*` so the list path stays byte-stable and the IR_HASHES regen
  stays a type-table change, not a runtime-behavior change. Differential
  probe under `probes/` per rule #524 lands with the first user-visible
  lowering (stage B); stage A alone adds no construct a program can
  spell yet.

**Stage B (the elementwise fast path).** `buffer[float] + buffer[float]`
and friends lower to loop IR the MIR fast slice can admit (supported_type
gains `buffer`), and the backend may vectorize. **Float semantics stay
bit-exact**: no fast-math flags, no reassociation, no FMA contraction; the
differential probe byte-compares against the pinned CPython oracle on the
same inputs, including the `tolist()` float formatting. This stage is
where the #496 M2 receipt comes from, and it is a separate PR with its own
canonicity review (it changes emitted IR for programs that opt in — see
§7 for why that still does not move the frozen hashes).

## 5. Marshalling cost vs `list[float]`

Why the type exists (the #372 pull trigger): a `list[float]` crossing an
FFI boundary marshals per element through `ps_seq_get` + the ABI
`_store_ps_arg` walk (`emit/llvm/_abi.py:67-127`); the elementwise ops go
through runtime calls (`ps_seq_get`/`ps_seq_set` per op per element), so a
1M-element `a*b` is 2M calls plus box traffic. A `buffer` makes the data
layout itself an IR-visible fact: the loop body is `getelementptr` +
`load`/`store` (or, in stage B, vector ops), and the boundary crossing is
one pointer. For GPU/device work the transfer dominates either way — which
is exactly why #372 says the pull trigger is a measured per-element
marshalling cost, and why this stays in Future until #496's M1 profile
shows it.

## 6. Canonicity and hashing

What changes in the canonical bytes when `buffer` lands:

- A new `kind` string and a new `elem` key on type dicts. Existing
  modules never contain `buffer`, so **no existing digest moves**: the
  frozen `probes/IR_HASHES.json` and `TIR_HASHES.json` baselines stay
  valid without regeneration, *provided* no other canonicalization rule
  changes in the same PR.
- Therefore **no `IR_VERSION` bump is required by the type alone**
  (`IR_VERSION` stamps the compiler-to-runtime ABI — the `%ps.str` payload
  precedent — and `validate_module` only compares the int). The bump
  question attaches to stage B: when buffer arithmetic changes what the
  runtime must understand (new `ps_buf_*` helpers are linked in, but old
  modules never reference them), the precedent of the str-model bump
  (1→2) suggests bumping when a *payload shape* changes, not when a new
  optional symbol appears. Decision for stage A: no bump, keep the
  validator refusing nothing new for old modules. Record the stage-B
  decision in the implementation PR with the same reasoning laid out.
- The hashing surface that must be updated **in the same PR** as the type,
  so both digest paths treat a buffer identically (byte-identity is
  enforced by `tirscore --verify-bytes`):
  - `tirserde._type_dict` (`tirserde.py:355-379`): emit
    `{"kind": "buffer", "elem": ...}` — the current unknown-kind fallback
    would silently drop `elem`.
  - `tirserde.dict_to_type` (`tirserde.py:662-678`): reconstruct
    `t_buffer(elem)` — the current `t_leaf(kind)` fallback drops `elem`
    the other direction.
  - `tirhash._write_type` (`tirhash.py:761-788`): the typed walker writes
    the kind then, in fixed schema field order, `elem` — the field order
    must match `tirserde._type_dict`'s key emission order so the two byte
    streams stay identical.
  - `sigrec.type_to_dict` (`sigrec.py`), the closed-table mirror.
- **Interaction with #830 retirement (branch `work/tir-hash-retire`)**:
  the retirement moves canonicalization ownership from `canonical.py`
  (dict path) to `tirhash.py` (typed walker, schema field order). The
  buffer type as designed here lands cleanly either way because every
  rule it touches is duplicated in both paths and the two are kept
  byte-identical by the verify gate:
  - If the buffer lands *before* retirement: the PR adds the four branches
    above plus the same case to `canonical.py`'s doc-level rule list
    (no rule change — paths scrub and strips are kind-agnostic); the
    retirement branch then carries both branches forward when it rebases.
  - If it lands *after*: only `tirhash.py`, `tirserde.dict_to_type`, and
    `sigrec` need branches (the dict `_type_dict` path is gone), and the
    field order is recorded once in the walker.
  - Either way the buffer PR must not be the PR that also changes
    `CANONICAL_SCHEMA_VERSION` or any strip/sort rule: the whole point of
    §6 is that the frozen baselines do not move. If a regen is ever
    needed, the A/B `cmp` ritual applies (`python -m
    benchmarks.compat2.irscore` twice, `cmp`), generated on Linux only.
- Frozen-gate bookkeeping for stage A: `ir.py` (TYPE_KINDS/validate_type/
  type_key) is not in the frozen-file list; `tirserde.py`/`tirhash.py` are
  frozen-gate files whose change here is the *minimum mechanical*
  accompaniment to a new type kind and moves no digest. Per AGENTS.md,
  record this as part of the canonicity decision that gates the first
  code edit — which is exactly what this document requests sign-off on.

## 7. What needs a recorded decision before code

1. Add `"buffer"` to `TYPE_KINDS` restricted to numeric elements (the
   closed-vocabulary exception of §3: IR-internal, FFI manifests stay
   closed).
2. No `IR_VERSION` bump for stage A; stage B revisits with the payload-
   shape reasoning.
3. The four hashing-surface branches land with the type in one PR, moving
   no digest; frozen baselines untouched.
4. Buffer ownership: container-like (scope-exit free), borrowed only at a
   future FFI boundary via the existing PS4010 machinery.

## 8. Migration path from #496 M1 (bytes backing)

M1 arrays are flat `bytes` with a fixed element width, arithmetic in a C
runtime loop. The migration is surface-shaped, not representation-shaped:

- `pyscriptc_ndarray` keeps its API; only the backing of its array object
  changes from a bytes-carrying handle to a `buffer` value. The M1 `tobytes()`
  / `frombytes` surface stays as the interchange and is re-expressed as a
  view/copy over the buffer — the M1 tests (the conformance suite against
  real numpy) run unchanged and become the differential probe corpus for
  stage B.
- The `ps_ndarray.c` arithmetic loop retires into stage-B IR emission per
  op; the C loop remains as the fallback for the shapes the fast slice
  does not admit (bool masks, axis reductions initially).
- The perf receipt (compiled array arithmetic beating CPython numpy on
  ≥1M elements) is re-measured at stage B and recorded in the v0.11.0
  milestone description; the M1 receipt (beating the pure-Python list
  loop) is unaffected.

## 9. PR slicing

1. This design doc plus the stage A type-plumbing slice (this PR;
   milestone Future, `Part of #372`): `buffer` in `TYPE_KINDS` with a
   flat numeric element validation, `type_key` branch, `t_buffer`
   factories, the four hashing-surface branches, the MIR pointer tags, and
   unit tests. The type is unreachable from source code until a
   constructor lowering exists, which *is* the flag-by-absence. Frozen
   baselines untouched (A/B `cmp` verified); `tirscore --verify-bytes`
   green.
2. Stage B: constructor + elementwise lowering + differential probe +
   divergence-ledger entry if any construct diverges. Separate decision
   record for the fast-slice admission.
