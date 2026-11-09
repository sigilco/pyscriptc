# V4.1: typed coroutine port -- design and slice plan

Date: 2026-11-07. Epic: #817. Milestone: v0.9.0. Vertical V4 (dict-IR drop
prerequisites). Measured on `bd3e7577` (master at branch time): `coro.py` is
2,239 lines, 82 `def`s, 124 raw keyed reads of IR records, 128 `"kind":`
dict literals.

This note turns the R-2 sizing
(`.agents/notes/2026-11-06-typed-coro-generator-lowering.md`, #978) into an
ordered slice list with per-slice acceptance and file ownership, so each
slice lands as its own small PR. The same PR carries slice 1. Semantics are
not re-decided here: divergences 19–21, 24 and 25 in
`docs/reference/divergences.md` are the contract, generators stay on the
typed-HIR lane until re-verified (backend-only decision 2026-11-02), and
#438 (`GeneratorExit` on throw/close) is a later semantics PR, never folded
into a typing slice. No frozen gate file moves in any slice
(`IR_HASHES.json`, `TIR_HASHES.json`, `canonical.py` rules, probes,
`outcomes.py`); a slice that moves a probe payload is rejected, not repinned.

## Entry points into coro.py

Everything the rest of the tree reaches, grouped by caller.

### Module-level gates (AST only, no IR -- no typing work)

| Entry | Callers | Shape |
| --- | --- | --- |
| `enabled()` | `lower/_stdlib`, `lower/_classes`, `lower/_classes_more`, `restlower/_asyncio`, `restlower/_asyncio_sync` | `PYSC_CORO` env check |
| `is_generator(fn)` | `CoroMixin` internals | `ast` walk |
| `is_contextmanager(fn)` | `CoroMixin` internals | decorator names |
| `_own_nodes`, `_shape_error` | `CoroMixin` internals | AST fences, no IR |
| Marker constants (`YIELD/AWAIT/AWAIT_TASK/WAIT_FOR/PRIM/THREAD/SLEEP/RETURN_MARKER`, `STATE/RESULT/STOP/TASK/SELF/RESUME/SEND/GEN/VALUE/EXC`, `DONE..CANCELLED`, `TASK_CLASS`, `_await_result_name`) | `lower/_call_attr`, `restlower/_asyncio*`, tests | plain strings/ints; unchanged surface |

### `CoroMixin` -- the lower-time entry (mixed into `Lowerer`)

Called from `lower/_module_driver`, `lower/_classes`, `lower/_boxed_resolve`,
`lower/_stmt*`, `lower/_expr`. Emits marker calls and `FramePlan` records;
reads and returns dict-IR statement/expression records.

`_coro_plans`, `_coro_signature`, `_coro_result`, `_coro_elem`,
`_coro_types`, `_coro_body_sig`, `_coro_method_signature`,
`_coro_method_body_sig`, `_coro_current`, `_stmt_core`, `_coro_recv_temp`,
`_coro_yield`, `_coro_yield_from`, `_coro_await_type`,
`_coro_plan_for_frame`, `_coro_plan_from_exports`, `_expr_any`,
`_coro_await`, `_coro_finish`.

`FramePlan` and `CoroError` are already typed records; `FramePlan.elem` /
`send` / `ret` / `params` carry type dicts and become `T` when the mixin
types.

### The IR-to-IR pass entry

`lower_resumables(module, plans)` -- the pass front door, reached only
through `CoroMixin._coro_finish`. Its private walk:
`_lower_method_resumables` (same walk over class method records) and
`_plan_by_ir_name` (package-unit name lookup; already dict-free -- it reads
`FramePlan` fields only).

### Synthesis and transform (pass internals, reached from `lower_resumables`)

| Group | Members | Role |
| --- | --- | --- |
| Manager | `_manager_class` | `cm.<name>` class for `@contextmanager` (~145 lines of dict IR) |
| Kinds | `_Kind`, `_GeneratorKind`, `_CoroutineKind` (`methods`, `suspend`, `finish`) | `__next__`/`send`/`close` vs `resume`; suspend vs finish bodies |
| Frame | `_Frame` (`build`, `_forwarder`, `_wrapper`, `_block`, `_suspending`, `_try`, `_guard_part`, `_save_exc`, `_restore_exc`, `_reraise`, `_exc_slot`, `_await`, `_await_result_type`, `_await_task`, `_wait_for`, `_task_park`, `_await_prim`, `_await_thread`, `_sleep`, `_plain`, `self_name`, `rs`, `fresh`, `field_get`, `field_set`, `temp`, `copy_out`, `copy_in`) | the guard transform, copy-in/out, try re-entry, await expansions (~1,000 lines) |
| Classifiers | `_suspends`, `_is_marker`, `_is_return_marker`, `_contains_kind` | read-only statement classification feeding `_block`/`_suspending`/`_plain`/`_try` |
| Builders | `_name`, `_const`, `_const_str`, `_assign`, `_compare`, `_attr_expr`, `_attr_set`, `_if` | private dict-IR constructors used by every group above |

### The adapter seam (outside coro.py)

`lower/_coro_adapter.coro_module` converts the lowered module's `functions`
and `classes` records through `node_to_dict` before `_coro_finish` runs
(`lower/_module_driver`). It dies when the pass walks typed nodes and emit
reads typed nodes; until then the convert-in direction stays (R-2 note:
never `dict_to_node` a module the pass still indexes by `["kind"]`).

## Typed HIR the port needs

All of it exists in `_irnode.py`/`tirserde.py` today; the only open question
is the extra keys.

- **Node kinds the pass builds** (from the file, counted): `call` (28),
  `return` (14), `expr` (11), `setattr` (6), `raise` (6), `builtin`,
  `function` and `module` callees (21 combined), `try` (3), `const`, `attr`,
  `while`, `if`, `compare`, `assign`, `name`, `closure`, plus `excmake`
  shapes inside `_reraise`. Every one has an `_ir` factory and a
  `_NODE_KEYS` spelling, so a helper that builds `IrNode` and converts at
  the boundary reproduces the exact dict.
- **Type records**: leaves `int`/`bool`/`none`/`float`/`str`/`exc`/`buf`;
  `class` types for `gen.<f>`, `coro.<f>`, `cm.<f>`, `Task`, and the
  `opt_str`/`opt_exc` tagged optionals (`label`/`elem`/`optional`, all on
  `T`); `func` for `_RESUME_FUNC` (`params`/`return` → `T.params`/`T.ret`).
  `dict_to_type` covers all of these, including the `typevar` placeholder.
- **`Span`**: every dict-IR `span` the file carries round-trips through
  `dict_to_span`/`_span_dict`.
- **Function, class, method and module records stay dicts** (R-2 option a).
  The pass wraps typed bodies in dict records; typing the records
  themselves is the R-3 module-record inventory, not this vertical.
- **Extra keys are the schema hole** (R-2 decision 1, still the
  recommendation): `recv`/`recvType` on the yield marker, `suspend` on
  `return`, `wait_for`, `prim_op`/`prim_elem`/`thread_elem` on restlower
  markers. `dict_to_node` already keeps any call with keys outside
  `_CALL_KEYS` as a whole dict, losslessly. Carry the recv/prim/thread
  payloads on `FramePlan` (or a side table) when the walk types; keep
  `suspend` a dict-only flag the convert-out shim stamps for emit
  (`emit/llvm/_funcs.py` reads it today). No `_irnode` flag bits, no serde
  change, no TIR-hash exposure.

## Slice order, smallest first

Each slice is one PR. Every slice: `ruff check`, `ruff format --check`,
`uv run pytest tests/test_coro_generators.py tests/test_coro_async.py`
green, `tests/test_census_self.py` green (repin via
`scripts/repin_census.py` only when a shard's measured count moved; the
delta is recorded in the PR body), no raw dict reads in code the slice
touches, no frozen-file edits.

| # | Slice | Acceptance |
| --- | --- | --- |
| 1 | **Front-door walk on typed accessors** (this PR): `lower_resumables`, `_lower_method_resumables` read module/function/class/method records through named accessors instead of `record["key"]` / `{**rec, "k": v}` splats. | The two touched functions and the new accessors carry zero raw keyed reads; module output is the same dicts as before (accessors return identical values); coro tests green. |
| 2 | **Statement classifiers on typed accessors**: `_suspends`, `_is_marker`, `_is_return_marker`, `_contains_kind` read `kind`/`then`/`else`/`body`/`orelse`/`finalbody`/`handlers`/`expr`/`callee`/`name` through accessors. | Zero raw keyed reads in the four functions; identical classification; coro tests green. |
| 3 | **Idle skip**: `_module_driver` runs `coro_module` only when plans exist. One line, but it is V3's file area -- land it in a V4 PR that declares `lower/_module_driver.py` in the body, or hand to V3. | A module without generators returns the unconverted module (same content; object identity may change); `irscore`/`tirscore` byte-identical on no-generator probes. |
| 4 | **Typed constructors for the private builders**: `_name`, `_const`, `_const_str`, `_assign`, `_compare`, `_attr_expr`, `_attr_set`, `_if` build `IrNode` via `_ir` factories (`dict_to_type`/`dict_to_span`/`dict_to_node` in, `node_to_dict` out at the helper boundary). | The dict each helper returns is key-for-key identical; coro tests green; census repin recorded. |
| 5 | **Mixin markers through typed builders**: `_coro_yield`, `_coro_yield_from`, `_coro_await` and the return/sleep/task/prim/thread markers. Recv and prim/thread payloads stay dict keys (markers still dicts until the walk types). | Marker dicts byte-identical; `test_coro_*` green. |
| 6 | **Synthesis groups**: `_manager_class`, then `_GeneratorKind.methods`/`_CoroutineKind.methods`, then `_Frame._wrapper`/`_forwarder`/`copy_in`/`copy_out`/`field_*` -- bodies built typed, converted at the record boundary. One group per PR if a group does not fit. | Each emitted method/class record is the same dict; coro tests green. |
| 7 | **Typed walk**: `_block`/`_suspending`/`_plain`/`_try` dispatch on `IrNode.kind` after `dict_to_node` of each planned body at `_Frame` entry; `{**stmt}` splats become factory rebuilds. Highest-risk slice: marker calls come back as kept dicts (extra keys), so the walk accepts `IrNode | dict` at the marker seam only. | Same output dicts on every `test_coro_*` program; no TIR movement (markers never serialize). |
| 8 | **Typed expansions**: `_await`, `_await_task`, `_wait_for`, `_task_park`, `_await_prim`, `_await_thread`, `_sleep`, `_guard_part`, `_save_exc`/`_restore_exc`/`_reraise`. One family per PR. Extra keys move onto `FramePlan` or stay on kept-dict markers per the decision above -- coordinate with restlower owners for `prim_op`/`prim_elem`/`thread_elem`. | Same output dicts; coro async tests green. |
| 9 | **Convert-out shim**: `lower_resumables` returns `node_to_dict` of the rewritten records only; `coro_module` no longer converts the whole module on the way in (idle skip from slice 3 already landed). | The pass output dict is unchanged; `_coro_adapter` shrinks to convert-out only. |
| 10 | **Receipt**: `compiles_itself`, `no_import_cycle`, `compiler_progress.py` shows `coro.py` `dict_build`/`dict_read` at record-wrapper only, remaining dict sites documented. | Report lands in `.agents/notes/`; R-2 epic comment updated. |

Not in this vertical: deleting `_coro_adapter.py` (needs the emit seam,
#829/V4.2), typing function/class records (R-3), #438 `GeneratorExit`, #181
async generators, MIR re-verify of divergences 19–21/24/25 (R5), the
optional `coro/` package split (module-leanness, after the walk types).

## File ownership

- **This PR owns**: `src/pyscriptc/coro.py`,
  `.agents/notes/2026-11-07-typed-coro-port.md`. Nothing else.
- **Later slices may touch**: `lower/_coro_adapter.py` (V4-owned, dies with
  the port), `lower/_module_driver.py` line 415 (V3 area -- declare in the PR
  body or hand to V3), `restlower/_asyncio*.py` (marker extra keys, only if
  slice 8 needs plan-carried payloads; coordinate, never shared edits in one
  PR), `_irnode.py`/`tirserde.py` (only if the extra-keys decision flips to
  schema bits -- owner sign-off required first, it is not flipped here).
- **Never**: frozen gates (`outcomes.py`, `IR_HASHES.json`,
  `TIR_HASHES.json`, `canonical.py` rules, probes, calibration), evaluation
  material, `FramePlan` semantics, divergence ledger entries.

## Open decisions inherited from R-2 (unchanged)

1. Extra keys: omitted from the typed node, carried on `FramePlan` or kept
   on whole-dict markers; `suspend` stamped by the convert-out shim.
   (Recommended and assumed above; flip only with owner sign-off.)
2. Throw stays the `_call_attr` special case until #438.
3. Convert-out before emit types; `_coro_adapter.py` deletion waits on the
   emit seam.
4. #438 lands after the typed walk, as its own semantics PR.
