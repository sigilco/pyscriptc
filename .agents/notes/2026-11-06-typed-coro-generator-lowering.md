# R-2 (P3): what a typed `coro.py` and generator lowering would take

Date: 2026-11-06. Epic: #817. Milestone: v0.9.0. Docs only; no code in this note.

Handoff 5, group R, task R-2. Drop package P3 is `coro.py` and generators still on the dict IR through `lower/_coro_adapter.py`. This note sizes a typed coroutine lowering (frame synthesis, send/throw/close) and whether the adapter can shrink to a thin typed shim before the drop. It does not implement that lowering, does not migrate tests, and does not touch frozen hash files.

Read first: `src/pyscriptc/coro.py` (2222 lines, 97,659 bytes), `lower/_coro_adapter.py`, `lower/_module_driver.py` (`_coro_finish(coro_module(module))`), `_irnode.py`, `tirserde.py`, `.agents/docs/design/coroutine-lowering.md` (issue #161, the stackless guard transform), `.agents/notes/decisions-and-platform.md` (generator `with` semantics) (issue #433), `.agents/plans/decisions/2026-11-02-mir-backend-only-contract.md`, `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md`, and the draft in PR #937 (MIR fallback share through v0.9.0). Related issues: #826 (typed HIR for `coro.py`, closed as blocked on the generator-stays-HIR decision), #438 (throw/close with `GeneratorExit`), #181 (async generators, out of this drop).

## What exists today

Resumable functions are already a post-lower IR-to-IR pass, decided 2026-10-21: stackless state machines in existing IR kinds, no `llvm.coro.*`, no fibers. The pass runs after `lower/` and before emit. Output is a synthesized frame class plus a wrapper that allocates it. Re-entry is a guard transform over structured `if`/`while`/`try` because the IR has no goto. Consumption is `__iter__`/`__next__` through `_for_dunder`. Coroutines are a second `_Kind` sharing the transform: `resume(self) -> int`, poll-loop `await`.

Two halves live in one file:

| Region | Lines (approx.) | Role |
| --- | --- | --- |
| Preamble, `FramePlan`, `_shape_error` | 1-262 | Markers, field names, plan record, AST-shape fences |
| `CoroMixin` | 264-781 | Lower-time signatures, yield/await/return markers, `@contextmanager` outward types |
| `lower_resumables`, method rewrite, `_manager_class` | 787-1011 | Module walk, frame/wrapper append, `cm.<name>` class |
| `_Kind` / `_GeneratorKind` / `_CoroutineKind` | 1014-1121 | `__next__` vs `resume`, suspend vs finish |
| `_Frame` | 1124-2131 | Guard transform, copy-in/copy-out, try re-entry, await expansions |
| Helpers | 2134-2222 | Marker tests, dict IR constructors |

`rg -c '"kind":' src/pyscriptc/coro.py` is 128. `stmt["kind"]` / `kind ==` dispatch is 27. Census comments pin 45 self-host fences on dict-shaped IR annotations in this file.

The seam: `_module_driver` always calls `coro_module` before `_coro_finish`. The adapter walks `module["functions"]` and `module["classes"]` through `node_to_dict`. Typed statement nodes from the R3 strangler become dicts; records that are already dicts map key for key. Fences, globals, and the island plane ride through. `lower_resumables` then walks dicts. Emit still reads dict IR (`suspend` on `return` is `_flag_of(stmt, 'suspend')` in `emit/llvm/_funcs.py`, flow code 2).

`throw` is not a frame method. `_call_attr._gen_throw` marks `.ps_state = DONE` and raises the exception class at the call site. `close` is a synthesized method that only stores `DONE`. `send` is synthesized only when `Generator[T, S, R]` names a non-None S: store into `.ps_send`, then `__next__`. Divergences 19-21, 24, 25 in `docs/reference/divergences.md` are the current contract, not bugs to fix in a typing pass.

## IR-shape versus semantics

The 95 KB is not one blob. About a third is semantics that must survive a typed rewrite unchanged. The rest is dict construction and `kind` dispatch that a typed pass replaces.

Semantics (do not re-decide while typing):

- `FramePlan` and `_shape_error`: which bodies lower, which fence (`PS2002` nested def, yield-as-expression forms other than `x = yield v`, generic resumables, `return` inside `try`/`with`).
- Outward types: `Iterator[T]` / `Iterable[T]` / `Generator[T, S, R]`; coroutine return annotation is the result type; `@contextmanager` returns `cm.<name>` not the frame.
- Guard transform: preorder suspend ids, `rs == 0` for forward, skip when `rs` is past a statement's range, `while` re-enters without re-testing until the body, `if` latches the taken arm.
- Copy-in at resume, copy-out at every suspension. Locals live in the heap frame.
- `try` re-entry: re-run `ps_try_call`, skip `finally` on flow 2 (issue #164 / 2026-10-24 `with` decision). A handler that suspends keeps `.ps_exc` and restores it (`ps_exc_current` / `ps_exc_restore`).
- Yield-from as `for t in it: yield t` (no send delegation).
- Await expansions: child poll (`ps_await_poll`), task park, `wait_for` deadline, primitive park, `to_thread` park, sleep arm. These are HIR rewrites to existing call/if/return kinds.
- Kind finish: generator raises `StopIteration` (code 8); coroutine returns status 1 and writes `.ps_result`.
- Layout rules that keep unused fields off the frame (`.ps_stop`, `.ps_send`, `.ps_exc` only when used) so programs without those surfaces stay byte-identical on the frozen gates.

IR-shape (must change for typed HIR):

- Mixin and pass both emit dict literals: `{"kind": "call"|"return"|"if"|"try"|...}`. The helpers `_name`, `_const`, `_assign`, `_compare`, `_attr_expr`, `_attr_set`, `_if` are a private dict IR builder.
- Function and class records (`params`, `locals`, `body`, `fields`, `methods`) are dicts, not `_irnode` types. There is still no typed function/class record; R3 typed the statement/expression nodes.
- Extra keys the typed node and `tirserde` do not round-trip today: `recv` / `recvType` on the yield-marker call, `suspend: True` on `return`, `wait_for` on an await expression, `prim_op` / `prim_elem` / `thread_elem` on marker calls (written in `restlower/_asyncio*.py`, read in `_Frame`).
- `FramePlan.elem` / `send` / `ret` are type dicts; class table entries use `"kind": "class"` type dicts and a `coro` / `coro_elem` info blob for exports.
- `_plain` reconstructs `if`/`while` with `{**stmt, "then": ...}`. That copy is dict-shaped. A typed walk has to rebuild `IrNode` children, not splat keys.

The extra keys are the schema hole. `IrNode.flags` has `F_NEGATED`, `F_SET`, `F_IDENTITY`, `F_RERAISE`, `F_BARE`, `F_FROZEN`. There is no `F_SUSPEND`. `call()` has `symbol` / `thread` / identity, not `recv`. `return_stmt()` has no suspend bit. A lossless typed pass either grows those fields (flag bit plus optional `id`/`T` for recv, same pattern as R3c marker bits) or desugars the keys away before the pass so the typed node never carries them.

## What typed coroutine lowering needs

Keep the 2026-10-21 mechanism. Type the data it walks and builds. Do not move generator semantics into MIR (backend-only contract: generators stay HIR until re-verified; PR #937's fallback share through v0.9.0 includes that tail).

### Frame synthesis

`_Frame.build` today: number suspend points while rewriting the body, emit a resume method whose prologue loads `.ps_state` into `.ps_rs`, finishes if `DONE`, stores `DONE` (optimistic, overwritten at the next suspend), copies fields into locals, then runs the guarded body. Fields: `.ps_state`, for coroutines `.ps_task` (resume cell at fixed offset for `ps_loop.c`) then `.ps_result`, optional `.ps_stop` / `.ps_send` / `.ps_exc`, then every local.

Typed version: the same algorithm over `list[IrNode]`. Wrapper, forwarder (`coro.<name>.resume` as a module-level function so the loop holds a `func` cell), and frame class methods are still records the module lists, so either (a) those records stay dicts wrapping typed bodies, or (b) a later slice types the function/class record itself. (a) is enough to drop the statement-level dict IR inside this pass. (b) waits on whoever types module records globally (R-3 inventory).

Builder swap: replace `_name` / `_const` / `_if` with `_ir.assign` / `_ir.const_node` / `_ir.if_stmt` and `t_class` / `t_leaf`. The R3 files do this behind `node_to_dict` at each site. Doing that inside `_Frame` without first typing the walk still requires the adapter on the way in. Type the walk, then build typed, then convert once on the way out.

### Send

Lowering (`CoroMixin._stmt_core`): `x = yield v` becomes a yield marker whose call carries `recv` (a `.ps_recv_N` local) and `recvType` (S). The pass, at the resume arm of that suspend, assigns `recv` from `.ps_send`. `send(v)` on the frame stores `v` then calls `__next__`. Divergence 19: a send to a never-started generator ignores `v` and runs from the top.

Typed need: the marker call must carry the recv name across the mixin-to-pass boundary. Options: `F_RECV` plus `IrNode.id` / a type field (call already has `type` for the call result, which is `none`; `recvType` is a second type), or allocate the recv local in the mixin and pass it out of band on `FramePlan` (a list of recv slots in source order, matched by the pass as it numbers yield points). Out of band avoids a schema change and keeps `tirserde` off the critical path for a key that never reaches the canonical payload (the pass rewrites the marker away). Recommended: out-of-band on the plan for recv, because the marker is an internal seam, not an emit-facing node. `send` itself is ordinary setattr plus call; no extra keys.

### Throw

Today: not a method, a special case in `_call_attr` when the receiver class has `coro == "generator"`. Semantics: mark done, raise E at the caller. Divergence 20: the generator never sees E, so `except` inside the body cannot catch a throw, and `GeneratorExit` does not run.

Typed need for the current contract: `_gen_throw` already builds typed setattr/raise through `node_to_dict`. It does not go through `coro.py`. A typed `coro.py` can leave throw where it is. #438 (throw into the frame, `GeneratorExit` on close) is a semantics change: it needs a `throw` method that stores a pending exception and resumes at the yield, plus close that raises `GeneratorExit` so `finally` runs. That is not required to type the pass, and mixing it into the typing work would re-open divergences 20 and 25 in the same PR. Recommended: type the existing mark-done throw; land #438 as its own HIR semantics PR after the typed pass, still before any MIR re-verify.

### Close

Synthesized `close` stores `DONE`. Next `__next__` takes the prologue's `rs == DONE` arm and raises `StopIteration`. Pending `finally` does not run (divergence 25). Typing this is a three-statement method body. No extra keys. #438 would replace the body; not this drop.

### Context manager

`_manager_class` is ~150 lines of dict IR: `__enter__` drives `__next__` once, `__exit__` either marks done (in-flight exception, divergence 24) or drains to `StopIteration`. Same builder swap as frame methods. Semantics stay.

## Can the adapter shrink before the drop?

Yes. Three shapes, in order:

1. Skip when idle (thin today). `_coro_finish` already returns the module unchanged when `plans` is empty, but the driver still ran `coro_module` first, so every module pays `node_to_dict` on every function and class. Moving `coro_module` inside `_coro_finish` (convert only when there is a plan) is a one-hour, behavior-preserving shrink. Non-resumable modules never enter the dict reconstruction. Frozen hashes of programs without generators stay identical because those programs already skipped `lower_resumables`; they would also skip the convert.

2. Convert out, not in (thin typed shim). Mixin already emits some typed nodes (R3). The pass still wants dicts, so convert in. Once `_Frame` walks `IrNode`, convert the rewritten functions and classes on the way out for emit. The adapter becomes `node_to_dict` over the pass output only, plus `dict_to_node` of the input bodies if the module list is still mixed. That is the last dict seam this package owns. It can live in `_coro_adapter.py` so `coro.py` never imports `tirserde`.

3. Delete the adapter (the drop). Emit, or the emit compatibility accessor, reads typed nodes. Then `_coro_finish` returns the typed module and `_coro_adapter.py` goes. That is gated on the emit seam (in flight elsewhere: #942, #947 to #950) and is not a `coro.py` task. Until emit is typed, keep the convert-out shim.

Do not invert the adapter to `dict_to_node` on the way in while the pass still indexes `stmt["kind"]`: that forces a full typed walk in the same change. Convert-in stays until the walk is typed.

## Size in one-hour tasks

Measured from the file, not from memory. Stop at the first hour that does not finish. No frozen gate edits. Census repin only when a count moves.

1. **Idle skip.** Call `coro_module` only when `plans` is nonempty. Unit: a module without generators produces the same module object identity or the same dict as today. No hash movement.

2. **Schema decision write-up is this note.** Owner picks extra-key handling (decision 1 below) before task 3. If the owner picks "desugar markers in the mixin", skip task 3.

3. **Extra keys in `_irnode` / `tirserde` (only if decision 1 is flag bits).** `F_SUSPEND` on `return`; `recv` as `IrNode.id` on `call` plus a `T` for `recvType` (or a dedicated `recv` str field, already on the dataclass as `id`/`name`); serde emits the keys only when set, same as R3c markers. Round-trip tests in the existing irnode serde file. Does not change `coro.py` yet. A new key that appears in a probe payload would move TIR hashes: do not emit these keys from any node that reaches the gate. They are pass-internal; the serializer should omit them unless a later emit reads `suspend` from a typed node (today emit reads the dict flag). If emit still sees dicts, the convert-out shim can set `suspend` on the dict and the typed node never serializes it. Prefer that: no schema change, no TIR risk.

4. **Typed constructors for the private helpers.** `_name`, `_const`, `_assign`, `_compare`, `_attr_*`, `_if` build `IrNode` and `node_to_dict` at the helper boundary (R3 pattern). The pass still walks dicts. Census will move (dict-shaped annotations become helper signatures); repin. Byte-identical `irscore` on programs with generators: the dict at emit is the same.

5. **Mixin markers through the same helpers.** `_coro_yield`, `_coro_await`, return marker, sleep/task/prim/thread markers. Recv stays a dict key or a plan slot per decision 1.

6. **`_manager_class` and `_GeneratorKind.methods` / `_CoroutineKind.methods` / `_wrapper` / `_forwarder`.** Frame class records still dicts; bodies built typed then converted. Large, one flavour per hour if 6 does not finish: generator methods first, coroutine resume/forwarder second, manager third.

7. **Typed walk of `_block` / `_suspending` / `_plain`.** Dispatch on `stmt.kind` (attribute) after `dict_to_node` of each planned function body at the start of `_Frame`. This is the adapter inversion for planned functions only. Unplanned functions stay dicts and are not walked. Highest risk hour: `_plain`'s `{**stmt}` and `_try`'s handler copies must become factory calls.

8. **Typed `_try`, `_await`, `_await_task`, `_wait_for`, `_await_prim`, `_await_thread`, `_sleep`.** One expansion family per hour. Prim/thread extra keys (`prim_op`, `prim_elem`, `thread_elem`) live on the marker call from restlower; if those files still emit dicts, `dict_to_node` drops them unless task 3 landed or restlower stores them on the plan. Coordinate with restlower owners: either keep those markers as dicts until this hour, or add the same out-of-band slots on `FramePlan`.

9. **Convert-out shim.** `lower_resumables` returns dicts via `node_to_dict` of rewritten records only. Adapter no longer converts the whole module on the way in. Idle skip from task 1 remains.

10. **Receipt.** `tests/test_coro_generators.py` and `tests/test_coro_async.py` CPython-oracle runs (do not rewrite those tests in this workstream; another agent owns dict-IR test inventory). `compiles_itself`, `no_import_cycle`, census, ruff. `IR_HASHES.json` and `TIR_HASHES.json` untouched. Document remaining dict sites in `coro.py` (should be only function/class record wrappers if 7-9 landed).

11. **Split (optional, module-leanness, not required to type).** Mixin vs pass vs kinds into `coro/_mixin.py`, `coro/_frame.py`, `coro/_kinds.py`. v0.8.0 already excepted `coro.py` from the 1,500-line cap. Do not split in the same PR as the typed walk.

Out of this package, still HIR, not typed-pass work: #438 `GeneratorExit`, #181 async generators / `async for` / `async with`, #450 lazy generator expressions, MIR re-verify of divergences 19-21/24/25 (R5, backend-only contract).

## Open decisions for the owner

1. **Extra keys: omit from the typed node, or extend the schema?** Recommended: omit. Markers are an internal mixin-to-pass protocol. Carry recv names, `prim_op`, `prim_elem`, `thread_elem`, and `wait_for` on `FramePlan` (or a small side table keyed by span). Keep `suspend` as a dict-only flag the convert-out shim stamps on the `return` emit already reads. Avoids TIR/IR hash movement and matches "the pass rewrites to existing kinds". Alternative: R3c-style flag bits, serde omits when unset. Needed only if emit starts reading typed nodes before the shim stamps `suspend`.

2. **Throw: keep `_call_attr` special case, or synthesize a `throw` method while typing?** Recommended: keep the special case until #438. A method that only marks done and whose callers still go through `_gen_throw` is duplicate surface. #438 needs the method; typing does not.

3. **Convert-out versus wait for the emit seam?** Recommended: convert-out (task 9) as the P3 drop of the in-adapter. Deleting `_coro_adapter.py` waits on emit. The idle skip (task 1) can land alone.

4. **#438 before or after the typed walk?** Recommended: after. The walk is a representation change; `GeneratorExit` is a semantic change with divergence edits. Mixing them hides hash and oracle deltas.

5. **MIR fallback share (PR #937).** Generators remain in the direct-emit path until re-verified. The 80 percent MIR flip bar must not count a silent MIR lowering of `yield`. The M-3 try note already skips a `try` whose body yields. Confirm that `supported()` stays false for resumables through v0.9.0.

6. **Does `@contextmanager` stay in `coro.py`?** `_manager_class` is generator-frame driving, so it belongs with the pass. Moving it next to `with` desugar (`lower/_stmt`) would split the only consumer of `plan.manager`. Recommended: keep it in the pass until the typed walk lands; split only as part of the optional module-leanness hour.

## Frozen files, tests, and blockers

This note changes none of: `IR_HASHES.json`, `TIR_HASHES.json`, `canonical.py`, probes, `outcomes.py`, or any test file.

Blockers for implementation (not for this docs PR):

- Owner signature on extra keys (decision 1) and on #438 ordering (decision 4).
- Emit still dict-shaped, so a convert-out shim remains until those PRs land.
- Function/class module records are still dicts; a fully typed module is a later inventory (R-3).
- Restlower marker extra keys (`prim_op`, `thread_elem`) are written outside `coro.py`.
- #826 was closed as blocked on "generators stay on the HIR path until re-verified". That decision still holds. This work types the HIR pass; it does not move generators to MIR.

## What this note is not

Not the #830 retirement of `IR_HASHES.json` (R-1). Not the serializers inventory (R-3). Not a test migration. Not a lowering implementation.
