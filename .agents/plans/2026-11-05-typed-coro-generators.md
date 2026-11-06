# R-2: typed `coro.py` and generator lowering (P3, design only)

Date: 2026-11-05. Epic: #817. Prepares drop package P3. Docs only. Do not implement typed coro or generator lowering in this PR.

`src/pyscriptc/coro.py` is 97,659 bytes / 2,222 lines. The generator and `async def` pass still walks dict IR. The seam that feeds it is `src/pyscriptc/lower/_coro_adapter.py` (43 lines): `coro_module` runs `node_to_dict` over `functions` and `classes` so `coro.py` never sees a typed node.

The MIR backend-only decision keeps generator semantics on the HIR. A typed coro lowering is a typed-HIR rewrite, not a MIR pass.

## What the file is doing today

Three layers:

1. AST hooks (`CoroMixin`, lines 264-786). Detects generators (`Iterator`/`Iterable`/`Generator` annotations, `yield`) and `async def`. Builds the outward signature (frame class `gen.<name>` or `coro.<name>`), plants `__ps_yield__` / `__ps_await__` markers, records a `FramePlan`. This layer talks to the rest of lowering (types, fences, class table). Measured: about 520 lines. Mix of semantics (what a generator is) and dict-shaped class records (`{"kind": "class", "name": frame}`).
2. IR-to-IR rewrite (`lower_resumables`, `_Frame`, `_Kind`, lines 787-2132). Turns each planned function into a wrapper plus a frame class. Guard transform re-enters `if`/`while`/`try` without re-evaluating conditions. Copies locals to frame fields at each suspend. This is the bulk: about 1,350 lines. It constructs dict nodes (`{"kind": "return", ...}`) and reads `stmt["kind"]`.
3. Dict constructors and walkers (lines 2134-2222, plus helpers used above: `_name`, `_const`, `_assign`, `_if`, `_suspends`, `_contains_kind`). Pure IR-shape. About 90 lines at the tail, plus the same spelling inlined through `_Frame`.

Kind-specific semantics live in `_GeneratorKind` / `_CoroutineKind` (about 110 lines): `__iter__`/`__next__`/`close`/`send` versus `resume` returning 0/1, StopIteration versus result field.

The adapter is already the typed/dict boundary. It is lossless (`node_to_dict` of a typed record is the dict the pass always read). It cannot shrink to a no-op until `_Frame.build` consumes `IrNode` lists instead of function dicts.

## Frame synthesis (what typed lowering must still do)

Keep, as semantics, not as dict spelling:

- One frame class per resumable: fields for live locals, `state` int, optional `send` slot, coroutine `result`.
- Wrapper allocates the frame, copies params in, returns the frame (generator) or a task/frame (coroutine).
- Resume method: copy fields to locals, switch on `state`, fall into the guarded body, copy locals back at each suspend, set `state` to the resume label.
- Guard transform: `if`/`while` re-entry without re-running the condition; `try`/`finally` as decided for issue #164 and the with-on-suspend note (`.agents/notes/2026-10-24-generator-with-suspend-decision.md`). `__exit__` does not run at suspend.
- No new IR kinds (existing design: stackless state machines in existing HIR kinds). Typed lowering uses `assign` / `if` / `return` / `raise` / `call` factories from `_irnode.py`, not new dataclasses.

Typed work is replacing every `{"kind": ...}` constructor in `_Frame` with `_irnode` factories, and every `stmt["kind"]` read with `.kind`. Output stays existing HIR so the emitter and TIR gate do not move.

## send / throw / close

Today, on the dict path:

- `close`: synthesized. Sets `state` to done. Next `__next__` raises StopIteration. Matches CPython after close for programs that lower.
- `send`: synthesized only when the annotation's send type is not `none`. Stores into the frame send slot, then calls `__next__`. Documented divergence: `send` before the first yield is ignored; CPython raises TypeError (`coro.py` around the send method).
- `throw`: documented on `_GeneratorKind` ("raises the given exception out of the generator") but no `throw` method is built. Comment: yield-in-try is fenced, so a handler cannot surround a live suspension, and CPython's "the generator catches it" path is unreachable. Propagating out would be the semantics for every program that lowers, but the method is not in the class.
- `GeneratorExit` / GC abandonment: not lowered. Divergence 25 (pending cleanup does not run on abandonment), already recorded. Typed lowering must not silently add it.

Typed coro/generator v1 should keep close and send as they are, keep the send-before-start divergence, and either add `throw` as a one-hour follow-up (raise out of resume, mark done) or keep the gap explicit. That is an owner decision below.

Coroutines: `resume(self) -> int`, await is a poll loop, `asyncio.run` / `sleep` / `gather` already lower. `async with`, `async for`, async generators stay out (same as today).

## Can the adapter become a thin typed shim before the drop?

Yes, in two steps, without deleting `coro.py` in the same week.

1. Thin adapter (one hour): keep `coro_module`, but have it assert that functions/classes are already dicts or typed, and convert only the typed ones. That is today's `node_to_dict` loop. Already done. No further shrink until step 2.
2. Typed `_Frame` (the real work): `_Frame.__init__` takes an `IrNode` body list (or a typed function record once one exists). `lower_resumables` reads `mod["functions"]` until a typed Module exists; per-function bodies go through `dict_to_node` on the way in if the lowerer still emits dicts. The adapter then only converts the function envelope, not every statement. Estimate: the adapter stays 20-40 lines until the module envelope is typed, then it deletes.
3. Drop: `coro.py` imports `_irnode` only, `_coro_adapter.py` deleted, `node_to_dict` not in the resumable path. Blocked on typed function records (not just statement nodes) and on TIR still matching dict bytes, so this is after R3 has converted the rest of lowering, not a P3-first rewrite.

Doing step 2 before the rest of lowering is typed is useful: generators stop being the reason the dict IR cannot die. Doing step 3 first is not: the adapter is 43 lines and not the cost.

## Size in one-hour tasks

Honest split of the 2,222 lines. Hours are tasks, not a calendar.

IR-shape (replace dict constructors/readers; behavior tests must stay green):

1. Inventory every `{"kind":` and `["kind"]` site in `coro.py` (about 8 `["kind"]` reads plus many dict literals). Table in a follow-up note.
2. Replace tail helpers (`_name`, `_const`, `_assign`, `_compare`, `_attr_expr`, `_attr_set`, `_if`) with `_irnode` factories plus `node_to_dict` at the helper boundary so `_Frame` still sees dicts. Adapter-style, one helper family per hour.
3. Same for `_GeneratorKind.methods` / `suspend` / `finish` (the `__iter__`/`__next__`/`close`/`send` records).
4. Same for `_CoroutineKind`.
5. Typed walkers: `_suspends`, `_is_marker`, `_contains_kind` take `IrNode`.
6. `_manager_class` (contextmanager wrapper class dict).
7. `lower_resumables` / `_lower_method_resumables` loop: convert per function through `dict_to_node` on entry, `node_to_dict` on the produced wrapper/frame until the emitter reads typed function records.

Semantics (do not change in the shape tasks):

8. Guard transform (`_Frame` body rewrite) kept as-is, then ported statement-by-statement onto typed children (`then`/`else_`/`body` lists of `IrNode`). Likely 3-4 hours (if/while/try/with already desugared).
9. Local/field copy-in and copy-out at suspend. One hour if helpers from task 2 exist.
10. Await poll loop and task-await markers. One hour. Behavior tests: `tests/test_coro_async.py`, `tests/test_coro_tasks.py`.
11. Send slot plumbing. One hour. Keep the TypeError divergence.
12. Tests off dict IR for coro files (T-1 group 4 in the inventory). One hour, independent, docs in T-0.

Out of scope for P3 (owner must say so before anyone starts):

- `throw` method.
- `GeneratorExit` / abandonment `__exit__`.
- Nested/generic resumables, `async with`, `async for`, async generators, `yield from` expressions.

Total shape+semantics port: about 12-15 one-hour tasks after T-1. Not a rewrite of generator semantics. The 95 KB is mostly `_Frame` dict surgery, not a second language.

## Owner decisions still open

- Whether P3 ports `_Frame` onto `IrNode` before the module envelope is typed (recommended: yes, adapter stays).
- Whether `throw` is in typed v1 or stays a named gap.
- Whether send-before-first-yield keeps the TypeError divergence or fences.
- Whether contextmanager lowering stays inside `coro.py` or moves next to `with` desugar.
- Hash/gate: a typed coro PR must not move `IR_HASHES.json` or `TIR_HASHES.json`. If a change would, stop and write a blocker. Behavior tests (`test_coro_*`) are the oracle until then.
