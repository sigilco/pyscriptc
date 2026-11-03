# R3b _stmt slice receipt - the typed IR node conversion for lower/_stmt.py

Branch `work/r3b`, base `origin/master` at c9967a7d (PR #839, the typed HIR node module and the first lower/_core slice).
One commit: `bb5e7591` refactor(lower): build the _stmt nodes as typed _irnode values.
No push, no PR; the lead opens those.

## 1. What was converted

`src/pyscriptc/lower/_stmt.py` builds its statement nodes as typed `_irnode` values and round-trips them through `node_to_dict`, the same pattern `lower/_core.py` uses since PR #839.
Every dict-IR statement construction site in the file is covered: pass, if, while, return, expr, del, raise, assert, try, the with-cm, with-open and with-io families, for-range, for-iter, for-pairs and the comprehensions.
The helper functions `_if_stmt`, `_while_loop`, `_pass_stmt`, `_expr_wrap`, `_unary_not`, `_raise_node`, `_excmake_node` and `_builtin_call` sit at the top of the file and build the typed values; `dict_to_list` and `_body_nodes` convert a dict-IR statement list into typed nodes.

Two smaller files moved with the slice because the typed records cannot carry the distinction they need:
`src/pyscriptc/_irnode.py` widens `Handler.codes` to `list[int] | None`, and `src/pyscriptc/tirserde.py` writes the `codes` key only when it is not None and reads the handler on the `body` field so both spellings round-trip.

## 2. The two transcription slips the frozen gates caught

Neither slipped past inspection; both were caught by the hash gates and fixed in the same commit.

1. The descending range loop compared the cursor against the step temp instead of the stop temp, so 64 IR hashes drifted (the while condition of every descending range loop read `.ps_step_3` where the baseline read `.ps_stop_2`).
2. The with-family handlers (with-cm, with-open, with-io) spell their handler dict without a `codes` key, but the typed handler always emitted `"codes": []`, so 6 With-probe hashes drifted (`lang.With.function`, `lang.With.method`, `lang.With.multi.function`, `lang.With.multi.method`, `lang.withitem.bare.function`, `lang.withitem.bare.method`).
The fix for the second one is the `codes: list[int] | None` widening: None spells the absent key, `[]` spells the present-and-empty key, and the try statement's own handlers keep `codes` always present.

Lesson for the remaining slices: run BOTH hash gates after each file conversion, before the commit.
The round-trip unit checks and the broad test selection do not cover the dict-literal spellings, only the hash gates do.

## 3. Gate results

Hash gates, run on the committed state:

- `irscore` against IR_HASHES.json: only the 2 known environment-derived diffs (`stdlib.sys.base_exec_prefix`, `stdlib.sys.base_prefix`), which reproduce on unmodified master in the same checkout.
- `tirscore` against TIR_HASHES.json: 516 diffs, and the same 516 on unmodified master in the same checkout, with an identical changed-probe set. The TIR baseline is stale against master itself and is not a signal for this slice.
- A/B byte identity: `irscore` and `tirscore` each run twice, `cmp` equal on both pairs. The final A files also compare equal to the A files generated before the commit, so the digests are stable across the commit.

Hard gate, run on the committed state:

- `uv run pytest -q -k "compiles_itself or no_import_cycle" tests/test_selfhost.py`: 2 passed.
- `uv run pytest -q tests/test_threading_thread.py`: 16 passed.
- `uv run ruff check`: clean. `uv run ruff format --check`: 870 files already formatted.
- `uv run pytest -q tests/test_census_self.py`: 3 passed, after the repin below.

Broad selection (`-k "lower or stmt or try or with or for or raise or assert or return"`, 717 passed, 27 failed): every failure reproduces on unmodified master in the same checkout. They are the island and fallback tier tests, which read a `KeyError: 'island'` out of an IR that this checkout does not produce, plus the `test_stdlib_bench` and `test_r7_r14` cases. The set is identical before and after the slice, so none of them is a regression.

## 4. Census delta (parse only, `census_self --json`)

Master against this branch, same checkout, same interpreter:

- fences 3314 -> 3328 (+14)
- definitions 2130 -> 2141 (+11)
- fencedDefinitions 1548 -> 1559 (+11)
- nativeDefinitions 582 -> 582 (flat)
- modules 114 -> 114 (flat)
- lines 57490 -> 57506 (+16)

Family deltas: everything is flat except `dynamic-annotation` 765 -> 776 (+11).
The `lower/_stmt.py` package row moves 92 -> 106 fences, with its root families `dynamic-annotation` 16 -> 27 and derived family `export-cascade` 24 -> 27.

Attribution: the conversion promotes the inline dict literals into 11 module-level helper defs with fully annotated signatures (`dict[str, Any]`, `list[dict[str, Any]]`, `dict[str, Any] | None` parameters and returns).
Those annotations are exactly the shapes the dynamic-annotation family fences, one or two per new helper.
No fence is cut: the typed slices do not reduce dynamic-annotation, name-resolution, export-cascade or unlowered-type-name fences, because the dict-IR round trip keeps the payload types as `dict[str, Any]`.
Cutting them needs the payload itself to become typed, which is a later slice.

`tests/test_census_self.py` is repinned in the same commit: 3314 -> 3328, with the delta noted in the comment block that carries the count history.

## 5. Base note

The worktree `~/.worktrees/pyscriptc-r3b` already existed on branch `work/r3b`, based on the much older 0caaa1b2.
Its commits (the `_closures`, `_classes` and `_stmt` conversions from the earlier slice, the descending-compare and handler-codes fixes, and the slice 2 receipt) were kept as local tags `keep/closures`, `keep/classes`, `keep/stmt`, `keep/fix`, `keep/receipt` and `keep/tip` before the branch was reset to `origin/master`.
This slice redoes the `_stmt` conversion on the current master; the `_closures` and `_classes` conversions are not redone here and remain on the old tags.
The old slice's two fixes are both re-derived and confirmed on this base: the descending compare is restored, and the handler `codes` spelling is handled by the `codes: list[int] | None` widening rather than by the old `_handler_dict` change.

## 6. Remaining for the roadmap

- The next slices continue with `lower/_expr.py`, `lower/_calls.py` and `lower/__init__.py`, same pattern.
- Expect more dynamic-annotation fence growth on every slice that adds annotated module-level helpers, unless the payload itself goes typed.
- Run both hash gates after each file conversion, before the commit.
