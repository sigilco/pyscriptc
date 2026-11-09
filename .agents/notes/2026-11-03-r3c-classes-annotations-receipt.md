# R3c receipt - the typed IR node conversion of lower/_classes.py and lower/_annotations.py

Branch `work/r3c`, base `origin/master` at 8956e231 (PR #841). Issue #822. Two commits, no push, no PR; the lead opens those. The worktree `~/.worktrees/pyscriptc-r3c` existed on a much older base with uncommitted leftovers; its tip was preserved as the tag `keep/r3c-oldtip` and the branch was reset to `origin/master` before work started (same base note as r3b).

## 1. What was converted

Same pattern as r3a (#839) and r3b (#842): the builders construct typed `_ir.*` values and round-trip them through `node_to_dict`, so the dict-IR contract stays byte-identical. `_classes.py` covers the method-call and constructor calls (the `cls.{emit}.{name}` calls, the plain and generic class constructions, the namedtuple materialize-and-setattr path), the generated `__init__` bodies (the dataclass setattr loop, the parent-init expr call), the Date/TimeDelta binary folds, the field attribute reads and the `__getattr__` hook call. `_annotations.py` covers every type dict the `_annotation` family returns through one `_type()` boundary helper (class, list, dict, func, typevar, the leaf kinds, the iterator and optional synthetic records, the deque/task/queue/OrderedDict/Counter/defaultdict shapes) plus the one `attr` node in `_tuple_subscript`.

## 2. The typed T record needed the marker bits

This slice's gate catch is different from r3b's: the type-dict round trip was not lossless. A `T` record carried kind/name/label/elem/optional/set/key/value/params/return and nothing else, so five type-dict keys dropped at the first typed boundary: `handle` and `island` on a class type, `counter`, `defaultdict` and `ordered` on a dict type. None reach the canonical payload of the frozen probe corpus directly (the probes with the markers are in the FFI and collections lanes whose own files this slice does not convert), but the consumer chain does not read them defensively: a `counter` type that loses its mark re-lowers every `c[k]` read as a plain dict with no zero-on-miss, a dropped `island` mark routes a local through the native ABI, and `types_equal` (an exact `type_key` string compare) says the marked and unmarked types are different, so `self._expect` fences at the call site.

Fix: `_irnode.T` grows five flag bits (`F_TMARK_HANDLE`, `F_TMARK_ISLAND`, `F_TMARK_COUNTER`, `F_TMARK_DEFAULTDICT`, `F_TMARK_ORDERED`), `t_class` and `t_dict` take them as keyword booleans, the tirserde writer emits each key only when its bit is set, and `dict_to_type` reads them back. `list` keeps its existing `set` bit spelling and gains no marker; `func`, leaves and `typevar` keep none. The second commit wraps the manifest handle and island class annotation returns in the same `_type()` boundary so the markers round trip at every annotation site, not only the ones the conversion touched.

## 3. Gate results

- `irscore` and `tirscore` each run twice on the committed state, `cmp` equal on both pairs (byte-identical A/B).
- `irscore` against `IR_HASHES.json`: only the two known environment-derived diffs (`stdlib.sys.base_exec_prefix`, `stdlib.sys.base_prefix`), which reproduce on unmodified master in the same checkout.
- `tirscore` against `TIR_HASHES.json`: 516 diffs, identical changed-probe set on unmodified master in the same checkout. The TIR baseline is stale against master itself and is not a signal for this slice (same finding as r3b).
- `IR_HASHES.json` and `TIR_HASHES.json` are untouched by both commits.
- Hard gate `uv run pytest -q -k "compiles_itself or no_import_cycle" tests/test_selfhost.py`: 2 passed, run before each commit.
- `uv run pytest -q tests/test_threading_thread.py`: 16 passed, both runs.
- `uv run ruff check` clean; `uv run ruff format --check` clean (850 files).
- `uv run pytest -q tests/test_census_self.py`: 3 passed after the repin.
- Broad selection (`-k "class or annotation or dataclass or enum or namedtuple or tuple or attr or property"`, 180 passed, 4 failed): all four failures reproduce on unmodified master in the same checkout and the failure sets diff identical (`test_fallback_island_class_plane_map`, `test_fallback_island_class_dataclass_unusual_args`, `test_io_annotation_read_matches_cpython`, `test_ports.test_classify_port`). Not regressions.
- `uv run pytest -q tests/test_irnode_import.py tests/test_irnode_serde.py`: 14 passed (the typed-node unit gates).

## 4. Census delta (census_self --json)

Master against this branch, same checkout, same interpreter:

- fences 3314 -> 3331 (+17)
- definitions 2130 -> 2139 (+9)
- fencedDefinitions 1548 -> 1557 (+9)
- nativeDefinitions 582 -> 582 (flat)
- modules 114 -> 114 (flat)
- lines 57490 -> 57578 (+88)

The only moving family is `dynamic-annotation` 765 -> 774 (+9). Per package: `_classes` 71 -> 83 fences (+12, root `dynamic-annotation` 11 -> 19, derived `export-cascade` 24 -> 28), `_annotations` 33 -> 38 (+5, root `dynamic-annotation` 6 -> 7, derived `export-cascade` 5 -> 9). The growth is the same attribution r3a/r3b recorded: the conversions promote inline dict literals into 8 module-level helper defs with fully annotated signatures, and those annotations are exactly what the dynamic-annotation family fences. No fence is cut: the payload types still ride the dict-IR boundary, so the slice does not reduce any family.

`tests/test_census_self.py` is repinned in the same commit: 3314 -> 3331, with the delta noted in the count-history comment block.

## 5. Remaining for the roadmap

- The next slices continue with `lower/_expr.py`, `lower/__init__.py` and the `coro.py` stretch, same pattern. `lower/_calls.py` is owned by another session.
- The r3d payload-typing verdict stands: cutting the dynamic-annotation family needs the emit seam to receive typed nodes natively, not more per-file conversions.
- When a later slice's payloads go typed end to end, the marker bits here are the template: a bool the dict spells as a key becomes one flag bit, the writer emits the key only when set, the reader reads it back, and `types_equal` keeps its exact string contract untouched.
