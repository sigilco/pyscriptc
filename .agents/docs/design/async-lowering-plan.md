# Async lowering plan: `async def` and `await` on the coroutine frame

Status: exploration, written 2026-10-23 for issue #178. Owner: the v0.6.0 async lane. Decision record for the lowering mechanism itself is [`coroutine-lowering.md`](coroutine-lowering.md); the wider async and threads design is `.agents/plans/2026-10-21-async-threads-design.md` (P3).

## Where the work stands on origin/master

The mechanism is already there and it works. `src/pyscriptc/coro.py` lowers a module-level `async def` into a coroutine frame class (`coro.<name>`) with a `resume(self) -> int` method (0 pending, 1 done) and a `.ps_result` field, and it rewrites `await <coroutine call>` into a child poll: the parent stores the child frame in a local, calls `child.resume()`, and suspends with status 0 while the child is pending. `src/pyscriptc/runtime/ps_loop.c` is the run-to-completion loop behind `asyncio.run`, `asyncio.sleep` and `asyncio.gather`, and `src/pyscriptc/restlower/_asyncio.py` lowers those three calls.

What is merged:

- #165 (merged as #324): generator frames lower in package units and as methods. The method path (`_coro_method_signature`, `_lower_method_resumables`) is the template every later stage copies.
- #193 (merged): minimal async and await on coroutine frames, module level only.
- #166 (open as #333, in flight in the `feat/generators` worktree): the generator protocol surface (`send`, `throw`, `close`, `next` with a default). It touches `coro.py`, so it conflicts with any async change to the same file.

What does not lower yet, measured on origin/master with the pinned corpus:

| Shape | Current fence | Corpus weight |
|---|---|---|
| `async def` as a class method | `PS1204 AsyncFunctionDef in a class body is not lowered` | 29 definitions in filelock, 1 package |
| `async def` in a package unit, awaited from another unit | `PS2002 await takes a compiled coroutine call` | every cross-module coroutine |
| `await` inside `try` or `with` | `PS2002 await inside try or with is not lowered yet` | filelock `_async.py:291` |
| `async def` without a return annotation | `PS1001 <name> needs a return annotation` | all of them |
| `async with`, `async for`, async generators | `PS1204 AsyncFunctionDef in a class body is not lowered` plus `__aenter__`/`__aexit__` | filelock `_async_read_write.py`, `_soft_rw/_async.py` |

The P3 exit criterion on the issue is "filelock `async` fences go from 4 to 0, and its async defs lower except the island-bound ones". The 4 root fences in the package census are the four modules that hold `async def`; the per-definition count is 51 `async def` in filelock and 0 anywhere else in the corpus, so filelock is the whole visible async corpus.

## The shape of the work

`async def` is a generator with a different completion protocol, so almost everything the generator path already does applies to coroutines: a frame class, a resume method, the guard transform, the copy-in/copy-out of locals. The difference is the method set (`resume` instead of `__iter__`/`__next__`), the result field, and the fact that a coroutine frame is awaited rather than iterated.

The stages below are ordered by how much they unblock and by how much they depend on other lanes.

## Staged PRs

### Stage A: coroutine methods (this lane, PR 1)

`async def` as a class method lowers like a generator method: the class gets a frame class `coro.<Class>.<method>`, the method's outward type is that frame, and `await obj.method(...)` polls it.

- `coro.py`: `_coro_method_signature` accepts `ast.AsyncFunctionDef` and builds a coroutine frame class with a `resume` method and a `.ps_result` field; the plan is keyed `Class.method` like the generator plan.
- `lower/_classes.py`: the class body accepts an `async def` (the `PS1204` fence currently rejects it), and `_emit_class_method` routes the body through the coro method hooks.
- `coro.py`: `_lower_method_resumables` rewrites a planned coroutine method into the frame-allocating wrapper plus the frame class, the same way it does for generators.
- The emitter needs the class method's frame to be reachable as `cls.<frame>.resume`; the generator path already proves that shape works.

Exit: a coroutine method awaits a module-level coroutine and returns a value; CPython stdout matches byte for byte. The IR is plain classes, `validate_module` passes, and `PYSC_CORO=0` restores the old fences.

This stage does not need anything from #165 or #166 beyond what is already merged: it copies the generator method machinery that #165 landed.

### Stage B: package-unit coroutines (PR 2, depends on Stage A)

A coroutine defined in one package unit and awaited in another needs the frame class to travel with the export, the same way a generator's frame class travels after #165.

- `coro.py`: the plan table is per-`Lowerer`, so a consumer module has no plan for a foreign frame. The await needs the result type, which the export table already carries (the function's outward return type is the frame class, and the frame class's `info.methods.resume` gives the shape). The cleanest fix is to let the export table carry the coroutine result type alongside the class info, so the consumer can build a plan from the export instead of from its own table.
- `lower/__init__.py`: `_install_return_class` already installs the frame class next to the function export; the coroutine case needs the same plus the result type.

Exit: a package with `mod.py` defining an `async def` and `main.py` awaiting it compiles and runs; the cross-module await matches CPython.

### Stage C: await inside `try` and `with` (PR 3, needs the exception work)

An `await` inside a `try` is the shape real async code uses (the design doc says so explicitly). The generator side of this is issue #164 ("yield inside try/except/finally"), which is not merged.

- `coro.py`: the guard transform has to re-enter a `try` block. The design decision is that re-entering a structured `try` re-runs `ps_try_call` and re-establishes the setjmp frame, which is what a suspended frame needs, but the `finally` body must be guarded with `if not self.susp` so a suspension does not run it.
- The exception machinery needs `ps_exc_restore`: Python keeps one handled exception per generator frame, so the pass saves `ps_current` into the frame on suspend inside an `except` body and restores it on resume.

This stage is blocked on #164 for the generator half. The async half can be built on the same primitives but should land after it so the two do not fight over the guard transform.

Exit: a coroutine that awaits inside a `try` and a `finally` matches CPython, including the exception path.

### Stage D: `asyncio` surface beyond run/sleep/gather (PR 4)

`asyncio.create_task`, `wait_for`, `timeout`, `Lock`, `Event`, `Queue`, `Semaphore`, `to_thread` are issue #179 and #180, in the `Future` milestone. They need the loop to grow a poller (kqueue, epoll, poll fallback) and a real task lifetime, which `ps_loop.c` does not have yet: today the loop is run-to-completion over a fixed set of frames.

### Stage E: async generators, `async for`, `async with` (PR 5)

Issue #181, in the `Future` milestone. `async for` lowers to the `_for_dunder` shape with `await it.__anext__()` and `StopAsyncIteration`; `async with` lowers like a sync `with` with awaited `__aenter__`/`__aexit__`. Both need `StopAsyncIteration` and the reserved exception codes from #162 (merged, `ps_rt.h` carries the reserved codes).

## What is needed from the generator lanes

The async work is a thin layer over the generator work, so three things matter.

1. The method machinery from #165 must be generic over the kind, not generator-only. Stage A extends `_coro_method_signature` rather than duplicating it, so a #166 conflict in `coro.py` has to be resolved by rebase, not by rewrite.
2. #166 (open as #333) changes `coro.py` (the `send` type on `FramePlan`, the `close` method, `x = yield v`). Any async change to `coro.py` should land on top of #166 or be rebased onto it, so the plan table shape settles before the async stages add fields to it.
3. The try-suspension work (#164) is the shared blocker for Stage C on both the generator and the coroutine side. The generator side should own it, because the guard transform and the `ps_exc_restore` runtime call are shared.

## Blockers and risks

- **#333 conflicts with any `coro.py` change.** The `feat/generators` branch and this lane both rewrite `coro.py`. Merge order matters: land #333 first (it is the older, broader change), then rebase the async branch on `origin/master`.
- **The census pin conflicts.** `tests/test_census_self.py` pins the self-host fence count. Any new module or fence in the async lane changes it. The pin has to be re-measured from the actual output after a rebase, not hand-edited.
- **The corpus receipt is thin.** Async appears in exactly one visible package (filelock, 51 `async def`). The exit criterion "filelock async fences 4 to 0" is therefore about one package, and it is not a breadth signal. The P3 receipt should also be a CPython-oracle result set in `tests/test_coro_async.py`, not only a fence count.
- **The frame layout is a runtime ABI.** `ps_loop.c` reads field 0 (`.ps_state`) and field 1 (`.ps_task`) at fixed offsets, and the IR pass builds the fields in that order. A coroutine method frame must have the same layout, or the loop cannot drive it. This is the constraint that makes the method frame harder than it looks.
- **Exceptions across an await.** A suspension never crosses a live setjmp frame, so the exception state travels in the frame. Until Stage C lands, an `await` inside an `except` body is fenced, and that is the honest behaviour.
- **No island interop.** A CPython coroutine cannot be driven by the native loop. `await` on an island object stays fenced with a clear message.

## Divergences from CPython that this lane accepts

- `asyncio.gather` lowers only for a homogeneous list of coroutines of one result type, and returns `list[T]` in argument order. The tuple form for a fixed-arity heterogeneous gather is not lowered.
- The loop is run-to-completion. There is no fairness guarantee between tasks beyond the CPython-like deadline ordering of the timer queue, and `gather` does not yet support a coroutine that awaits a foreign future.
- `asyncio.sleep` is the only timer. There is no `wait_for`, no `timeout`, no cancellation.
