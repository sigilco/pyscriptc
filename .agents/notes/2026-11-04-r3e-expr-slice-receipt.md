# R3e receipt - the typed IR node conversion of lower/_expr.py

Branch `work/r3e`, base `origin/master` at b11cc81e. Issue #824 (partial: _expr only). One commit, `ecdbd77c` (`refactor(lower): build the _expr nodes as typed _irnode values (R3e)`), no push, no PR; the lead opens those. Worktree `~/.worktrees/pyscriptc-r3e`.

## 1. What was converted

Same pattern as #839, #842 and #845: the builders in `lower/_expr.py` construct typed `_ir.*` values and round trip them through `node_to_dict`, so the dict-IR contract stays byte-identical. Every dict-IR expression construction site in the module is covered: the const family (leaf literals, FFI and module constants, enum members, `__name__`), the unary and binary folds including the pow loop and the int-to-float promotions, the compare family (value compares, chain links, the seq/dict/tuple equality loops, the optional compares), membership (contains, the float-list walk, the const-tuple fold), the list and dict display nodes, the subscript family including the Counter/defaultdict missing-key path and the slice call, the optional boxing and unboxing, the boolop/ifexp temp weaving, the f-string and percent format paths, and the function-value closure.

`_irnode.py` needed no new node kinds or flag bits: every node and marker the file builds was already carried by the typed record. The round trip was verified lossless by the hash gates, not just by inspection. One transcription slip was caught before any commit by the self-host gate (the cheap canary, not the hash run): the new `_dict_node` helper first took pairs as tuples, but the callers build pairs as `{"key": ..., "value": ...}` dicts, and `dict_to_node` read `pair["key"]` out of a str. Fixed in the same working tree before the hash gates ran. The r3b lesson held: both hash gates ran after the conversion, before the commit, and the commit carries them.

## 2. Gate results

- A/B byte identity: `irscore` and `tirscore` each run twice after the conversion, `cmp` equal on both pairs.
- Baseline A files were captured on unmodified master in the same checkout before the edit. The B files compare byte-identical to the A files for both `irscore` and `tirscore`, so the frozen baselines are untouched: no dropped key, no drifted hash.
- `irscore` against `IR_HASHES.json`: only the two known environment-derived diffs (`stdlib.sys.base_exec_prefix`, `stdlib.sys.base_prefix`), which reproduce on master.
- `tirscore` against `TIR_HASHES.json`: the same stale diff set as unmodified master (497 changed probes, identical set). The TIR baseline is stale against master itself and is not a signal for this slice.
- `IR_HASHES.json` and `TIR_HASHES.json` are untouched.
- Hard gate `uv run pytest -q -k "compiles_itself or no_import_cycle" tests/test_selfhost.py`: 2 passed (3m17s), run before the commit.
- `uv run pytest -q tests/test_threading_thread.py`: 16 passed.
- `uv run ruff check` clean, `uv run ruff format --check` clean (856 files).
- `uv run pytest -q tests/test_census_self.py`: 3 passed after the repin.
- Ad hoc smoke: two multi-construct files (equality chains, pow, membership, defaultdict reads, optional ifexp, f-string specs, slices) compile with no fences.

## 3. Self-host census delta (census_self --json)

Master against this branch, same checkout, same interpreter:

- fences 3388 -> 3419 (+31)
- definitions 2177 -> 2205 (+28)
- fencedDefinitions 1582 -> 1610 (+28)
- nativeDefinitions 595 -> 595 (flat)
- modules 116 -> 116 (flat)
- lines 58027 -> 57974 (-53)

All the movement is in `lower/_expr`: 82 -> 113 fences, definitions 47 -> 75, root `dynamic-annotation` 8 -> 36 (+28), derived `export-cascade` 32 -> 35 (+3). The attribution matches r3a/r3b/r3c: the conversion promotes the inline dict literals into 31 module-level typed builder helpers with fully annotated signatures, and those annotations are exactly what the dynamic-annotation family fences. No fence is cut; the payload types still ride the dict-IR boundary. `tests/test_census_self.py` is repinned in the same commit (3388 -> 3419) with the delta noted in the count-history comment block.

## 4. Remaining for the roadmap

- The typed-node conversion continues with `lower/_calls.py`, `lower/_call_attr.py` and `lower/__init__.py`, same pattern (`lower/__init__.py` was in scope for #824 and is untouched here).
- Expect the dynamic-annotation fence growth on every remaining slice; the r3d verdict stands: cutting the family needs the emit seam to receive typed nodes natively.
- No new `_irnode` kinds or flags were needed for `_expr`; the remaining slices are unlikely to need them either unless a payload key this corpus never spells appears.
